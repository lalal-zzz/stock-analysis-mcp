"""
数据同步模块: 全量初始化 + 增量每日更新

网络层(tools/*)已用 asyncio.to_thread 包装同步请求, 此处的 asyncio.gather
为真并发(默认 16 并发), 大幅加速批量数据获取。
增量更新只拉取变化数据, 不做全量重复下载。
"""

import asyncio
import sys
import time
from datetime import date, timedelta

from .storage import (
    init_all as init_dbs,
    save_stock_basic,
    save_stock_kline,
    save_stock_indicators,
    save_stock_spot,
    save_stock_rank,
    save_sector_basic,
    save_sector_kline,
    save_sector_member,
    set_meta_stock,
    set_meta_sector,
    query_stock_db,
    get_db_paths,
    save_data_coverage,
)

from .indicators import indicator_rows_from_df
from .progress import make_progress
from .util import stocks_from_spot as _stocks_from_spot, today as _today

# 注: data 层不在模块顶层反向 import tools 层 (tools.data_manager 顶层 import 本模块),
# tools 层的网络函数在使用处延迟加载, 与 build.py/search.py 的约定一致。

import pandas as pd

_CONCURRENCY = 16


def _today() -> str:
    """当前日期(每次调用时求值, 避免长驻进程跨天后日期固化)"""
    return date.today().isoformat()


def _format_rank_items(items: list[dict]) -> list[dict]:
    """批量格式化排名数据, 跳过无法识别代码的脏数据, 避免单条异常炸掉整批更新"""
    from ..tools.stock_rank import _format_rank_item
    result = []
    for item in items:
        try:
            result.append(_format_rank_item(item))
        except ValueError:
            continue
    return result


def _stocks_from_spot(spots: list[dict]) -> list[dict]:
    """从全市场行情提取股票基础信息(代码/名称), 避免 akshare 50+ 页串行拉列表"""
    return [
        {"symbol": s["symbol"], "name": s.get("name") or "", "raw_symbol": s.get("raw_code") or s["symbol"]}
        for s in spots if s.get("symbol")
    ]


# ── 并发控制 ──


async def _concurrent_map(items, async_fn, desc="", batch_size=30, concurrency=None):
    """
    信号量流水线: 任意时刻最多 _CONCURRENCY 个任务在飞, 一个完成立即补一个。
    desc 非空且任务数>=20 时自动显示动画进度条(仅 TTY, 写 stderr)。
    batch_size 为历史遗留参数, 已不生效。
    """
    sem = asyncio.Semaphore(max(1, int(concurrency or _CONCURRENCY)))
    show_bar = bool(desc) and len(items) >= 20
    progress = make_progress(total=len(items), desc=desc) if show_bar else None

    async def _task(item):
        async with sem:
            try:
                return await async_fn(item)
            finally:
                if progress is not None:
                    progress.update(1)

    try:
        return await asyncio.gather(*(_task(i) for i in items), return_exceptions=True)
    finally:
        if progress is not None:
            progress.close()


async def _with_spinner(desc: str, coro):
    """给单次异步调用套上不确定模式进度条(动画+耗时)"""
    with make_progress(None, desc):
        return await coro


async def _fetch_kline(item):
    from ..tools.sector_data import get_sector_kline_net
    code, limit, name = item if isinstance(item, tuple) else (item, 300, None)
    try:
        return await get_sector_kline_net(code, limit, sector_name=name)
    except Exception:
        return []


async def _fetch_members(item):
    from ..tools.sector_data import get_sector_members
    if isinstance(item, tuple):
        code, name = item
    else:
        code, name = item, None
    try:
        return code, await get_sector_members(code, sector_name=name)
    except Exception:
        return code, []


# ═══════════════════ 全量初始化 ═══════════════════

async def init_all_data(include_sector_members: bool = True, quick: bool = False,
                        mode: str | None = None, workers: int = 8,
                        resume: bool = True) -> dict:
    """
    一次性全量下载(并发加速)

    quick=True: 快速模式, 仅股票列表+实时行情+人气排名(秒级完成),
    板块数据在首次使用时自动从网络下载并缓存(懒加载)。
    """
    from ..tools.sector_data import get_sector_list
    from ..tools.stock_data import get_latest_indicators
    from ..tools.stock_rank import _fetch_all_rankings
    if mode is None:
        mode = "quick" if quick else "legacy_full"
    if mode not in {"quick", "research", "full", "legacy_full"}:
        raise ValueError("mode must be quick|research|full")
    quick = mode == "quick"
    init_dbs()
    log = []
    today = _today()
    has_stock_history = mode in {"research", "full"}
    total_steps = 3 if quick else ((8 if include_sector_members else 6)
                                   if has_stock_history else (7 if include_sector_members else 5))
    step = 0

    def _step(msg):
        nonlocal step
        step += 1
        log.append(f"[{step}/{total_steps}] {msg}")

    # 1. 全市场实时行情(分页并发)
    _step("下载全市场实时行情...")
    spots = await _with_spinner("下载全市场实时行情...", get_latest_indicators())
    if not spots:
        return {"status": "error", "mode": mode, "error": "全市场行情为空或网络请求失败",
                "paths": get_db_paths()}
    for s in spots:
        s["updated_date"] = today
    save_stock_spot(spots)
    set_meta_stock("spot_updated", today)
    log[-1] += f" {len(spots)} 只"

    # 2. 股票列表(从行情数据提取)
    _step("保存股票列表...")
    stocks = _stocks_from_spot(spots)
    save_stock_basic(stocks)
    set_meta_stock("stock_count", str(len(stocks)))
    log[-1] += f" {len(stocks)} 只"

    # 3. 人气排名(分页并发)
    _step("下载人气排名...")
    raw_rank = await _with_spinner("下载人气排名...", _fetch_all_rankings())
    rank_rows = _format_rank_items(raw_rank)
    if rank_rows:
        for r in rank_rows:
            r["rank_date"] = today
        save_stock_rank(rank_rows)
        set_meta_stock("rank_updated", today)
    else:
        log[-1] += " [警告: 无数据，未更新排名时间]"
    log[-1] += f" {len(rank_rows)} 条"

    if quick:
        log.append("快速模式完成: 板块数据将在首次使用时自动下载")
        return {"status": "partial" if not rank_rows else "ok", "mode": "quick",
                "log": log, "paths": get_db_paths()}

    if mode in {"research", "full"}:
        _step(f"同步全市场股票K线 ({mode})...")
        symbols = [s["symbol"] for s in stocks]
        target = 320 if mode == "research" else 4000
        synced = await sync_stock_kline_universe(symbols=symbols, target_bars=target,
                                                  workers=workers, resume=resume)
        log[-1] += (f" ready={synced['ready']}/{synced['total']} "
                    f"coverage={synced['coverage_ratio']:.1%}")

    # 4. 板块列表
    _step("下载概念板块...")
    concept = await _with_spinner("下载概念板块...", get_sector_list("concept"))
    _step("下载行业板块...")
    industry = await _with_spinner("下载行业板块...", get_sector_list("industry"))
    all_sectors = concept + industry
    for s in all_sectors:
        s["updated_date"] = today
    save_sector_basic(all_sectors)
    if all_sectors:
        set_meta_sector("sector_count", str(len(all_sectors)))
        set_meta_sector("sector_updated", today)
    log[-1] += f" 概念{len(concept)}+行业{len(industry)}={len(all_sectors)}"

    # 5. 板块 K 线(并发下载, 限300条; 形态引擎要求 >=260 根)
    codes = [s.get("sector_code", "") for s in all_sectors if s.get("sector_code")]
    name_by_code = {s.get("sector_code", ""): s.get("sector_name") for s in all_sectors}
    _step(f"并发下载 {len(codes)} 个板块 K 线...")
    tasks = [(code, 300, name_by_code.get(code)) for code in codes]
    t0 = time.perf_counter()
    all_klines = await _concurrent_map(tasks, _fetch_kline, desc="板块K线")
    kline_total = 0
    for kl in all_klines:
        if kl:
            save_sector_kline(kl)
            kline_total += len(kl)
    set_meta_sector("kline_updated", today)
    log[-1] += f" {kline_total} 条(耗时 {time.perf_counter() - t0:.0f}s)"

    # 6-7. 板块成分股(可选, 并发下载)
    if include_sector_members:
        _step(f"并发下载 {len(codes)} 个板块成分股...")
        t0 = time.perf_counter()
        member_results = await _concurrent_map(
            [(code, name_by_code.get(code)) for code in codes],
            _fetch_members, desc="成分股", concurrency=1,
        )
        member_total = 0
        for code, members in member_results:
            if members:
                for m in members:
                    m["updated_date"] = today
                    if "sector_code" not in m:
                        m["sector_code"] = code
                save_sector_member(members)
                member_total += len(members)
        set_meta_sector("member_updated", today)
        set_meta_sector("member_sector_count", str(len(codes)))
        log[-1] += f" {member_total} 条(耗时 {time.perf_counter() - t0:.0f}s)"
    else:
        _step("跳过成分股下载")

    partial = (not all_sectors or not kline_total or
               (mode in {"research", "full"} and synced.get("status") != "ok"))
    return {"status": "partial" if partial else "ok", "mode": mode, "log": log, "paths": get_db_paths()}


# ═══════════════════ 增量每日更新 ═══════════════════

async def update_daily_stocks() -> dict:
    """增量更新股票: 行情+排名+列表(三项并发执行)"""
    from ..tools.stock_data import get_latest_indicators
    from ..tools.stock_rank import _fetch_all_rankings
    log = []
    today = _today()
    jobs = [
        ("实时行情", get_latest_indicators(),
         lambda r: (save_stock_spot([{**s, "updated_date": today} for s in r]),
                    save_stock_basic(_stocks_from_spot(r)),
                    set_meta_stock("spot_updated", today),
                    set_meta_stock("stock_count", str(len(r))),
                    f"{len(r)} 只(含列表)")),
        ("人气排名", _fetch_all_rankings(),
         lambda r: (save_stock_rank([{**x, "rank_date": today} for x in _format_rank_items(r)]),
                    set_meta_stock("rank_updated", today), f"{len(r)} 条")),
    ]
    results = await asyncio.gather(*(coro for _, coro, _ in jobs), return_exceptions=True)
    for (label, _, on_ok), result in zip(jobs, results):
        if isinstance(result, Exception):
            log.append(f"{label}: FAIL {result}")
            continue
        if not result:
            log.append(f"{label}: FAIL 无数据，保留现有缓存")
            continue
        try:
            ret = on_ok(result)  # 副作用只执行一次
            log.append(f"{label}: OK {ret[-1]}")
        except Exception as e:
            log.append(f"{label}: FAIL {e}")
    failed = any(": FAIL" in item for item in log)
    return {"status": "partial" if failed else "ok", "log": log}


async def update_daily_sectors(include_members: bool = True, top_n: int = 50) -> dict:
    """
    增量更新板块 — 智能策略, 避免全量重复下载。

    - 板块列表: 全量更新(几个 API 调用, 很快)
    - K 线: 仅更新涨跌幅前 top_n 板块的最近 5 条(避免 280+ 次 API 调用)
    - 成分股: 仅更新涨跌幅前 top_n 板块(与 K 线一致)
    """
    from ..tools.sector_data import get_sector_list
    log = []
    today = _today()

    # 板块列表(全量, 快)
    all_sectors = []
    for st in ["concept", "industry"]:
        try:
            secs = await get_sector_list(st)
            for s in secs:
                s["updated_date"] = today
            all_sectors.extend(secs)
            log.append(f"板块列表({st}): {len(secs)} 个")
        except Exception as e:
            log.append(f"板块列表({st}): FAIL {e}")
    save_sector_basic(all_sectors)
    if all_sectors:
        set_meta_sector("sector_count", str(len(all_sectors)))
        set_meta_sector("sector_updated", today)

    # 筛选活跃板块(top_n 按涨跌幅绝对值)
    active = sorted(
        [s for s in all_sectors if s.get("change_pct") is not None],
        key=lambda x: abs(x.get("change_pct") or 0), reverse=True,
    )[:top_n]
    active_codes = [s["sector_code"] for s in active]

    # K 线(仅活跃板块, 最近 5 条) → 并发
    try:
        log.append(f"板块K线(Top{len(active_codes)}, 最近5条): 并发...")
        tasks = [
            (s["sector_code"], 5, s.get("sector_name"))
            for s in active if s.get("sector_code")
        ]
        all_klines = await _concurrent_map(tasks, _fetch_kline, desc="板块K线更新")
        kline_total = 0
        for kl in all_klines:
            if kl:
                save_sector_kline(kl)
                kline_total += len(kl)
        set_meta_sector("kline_updated", today)
        log[-1] += f" OK {kline_total} 条"
    except Exception as e:
        log.append(f"板块K线: FAIL {e}")

    # 成分股(仅活跃板块) → 并发
    if include_members and active_codes:
        try:
            log.append(f"成分股(Top{len(active_codes)}): 并发...")
            member_results = await _concurrent_map(
                [(s["sector_code"], s.get("sector_name")) for s in active],
                _fetch_members, desc="成分股更新", concurrency=1,
            )
            member_total = 0
            for code, members in member_results:
                if members:
                    for m in members:
                        m["updated_date"] = today
                        if "sector_code" not in m:
                            m["sector_code"] = code
                    save_sector_member(members)
                    member_total += len(members)
            set_meta_sector("member_updated", today)
            log[-1] += f" OK {member_total} 条"
        except Exception as e:
            log.append(f"成分股: FAIL {e}")

    failed = any(": FAIL" in item for item in log)
    return {"status": "partial" if failed else "ok", "log": log}


async def update_daily_all(include_sector_members: bool = True,
                           stock_kline_mode: str = "tracked",
                           skip_non_trading_day: bool = True) -> dict:
    if stock_kline_mode not in {"none", "tracked", "all"}:
        raise ValueError("stock_kline_mode must be none|tracked|all")
    if skip_non_trading_day:
        try:
            from .sources import is_trade_day
            if not await asyncio.to_thread(is_trade_day, date.today()):
                return {"status": "skipped", "reason": "non_trading_day",
                        "date": _today(), "paths": get_db_paths()}
        except Exception as exc:
            # Calendar failure must not prevent a normal update; surface it in the result.
            calendar_warning = str(exc)
        else:
            calendar_warning = None
    else:
        calendar_warning = None
    stock_result = await update_daily_stocks()
    sector_result = await update_daily_sectors(include_sector_members)
    kline_result = None
    if stock_kline_mode != "none":
        if stock_kline_mode == "tracked":
            rows = query_stock_db(
                "SELECT DISTINCT symbol FROM data_coverage WHERE data_type='stock_kline'")
        else:
            rows = query_stock_db("SELECT symbol FROM stock_basic ORDER BY symbol")
        symbols = [r["symbol"] for r in rows]
        if symbols:
            kline_result = await sync_stock_kline_universe(
                symbols=symbols, target_bars=320, workers=6,
                resume=False, incremental=True,
            )
    overall_status = "ok"
    if stock_result.get("status") != "ok" or sector_result.get("status") != "ok":
        overall_status = "partial"
    if kline_result and kline_result.get("failed", 0):
        overall_status = "partial"
    return {
        "status": overall_status,
        "stock_log": stock_result["log"],
        "sector_log": sector_result["log"],
        "stock_kline": kline_result,
        "warnings": ([f"交易日历不可用: {calendar_warning}"] if calendar_warning else []),
        "paths": get_db_paths(),
    }


# ═══════════════════ 个股 K 线(按需, 并发批量) ═══════════════════

async def _download_one_kline(symbol: str, days: int = 560, adjust: str = "qfq",
                              ready_bars: int = 260,
                              start_date: str | None = None) -> dict:
    from .network import normalize_symbol
    from .quality import latest_completed_trade_day
    symbol = normalize_symbol(symbol)
    expected = latest_completed_trade_day()
    end = date.fromisoformat(expected) if expected else date.today()
    try:
        start = (date.fromisoformat(start_date[:10]) if start_date
                 else end - timedelta(days=days + 30))
        from ..tools.stock_data import get_stock_history
        if ready_bars >= 4000:
            from .sources import fetch_complete_history
            klines = await asyncio.to_thread(fetch_complete_history, symbol, adjust)
        else:
            klines = await get_stock_history(
                symbol, start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"), adjust=adjust,
            )
        if klines:
            for row in klines:
                row.setdefault("adjust_type", adjust)
                row.setdefault("source", "multi-provider")
            from .quality import PriceBasisChanged
            try:
                save_stock_kline(klines)
            except PriceBasisChanged as changed:
                from .sources import fetch_complete_history
                from .storage.writer import replace_stock_history
                complete = await asyncio.to_thread(fetch_complete_history, symbol, adjust)
                if not complete:
                    raise PriceBasisChanged(f"{changed}; full refresh unavailable, original data preserved")
                await asyncio.to_thread(replace_stock_history, symbol, adjust, complete, reason=str(changed))
                klines = complete
            adjust_types = {r.get("adjust_type", adjust) for r in klines}
            coverage_adjust = next(iter(adjust_types)) if len(adjust_types) == 1 else adjust
            from .search import get_stock_kline_local
            local_klines = get_stock_kline_local(
                symbol, limit=100000, adjust=coverage_adjust)
            if not local_klines:
                local_klines = sorted(klines, key=lambda r: str(r.get("date", "")))
            stats = query_stock_db(
                """SELECT MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS row_count
                   FROM (
                       SELECT date FROM stock_kline WHERE symbol=? AND adjust_type=?
                       UNION
                       SELECT date FROM stock_kline_variants WHERE symbol=? AND adjust_type=?
                   )""",
                (symbol, coverage_adjust, symbol, coverage_adjust),
            )[0]
            first_date = stats.get("first_date") or str(local_klines[0].get("date", ""))[:10]
            last_date = stats.get("last_date") or str(local_klines[-1].get("date", ""))[:10]
            total_rows = int(stats.get("row_count") or len(local_klines))
            sources = sorted({r.get("source", "multi-provider") for r in klines})
            source = sources[0] if len(sources) == 1 else "mixed"
            try:
                df = pd.DataFrame(local_klines).sort_values("date", ascending=True)
                ind_rows = indicator_rows_from_df(df, index_cols=("symbol", "date", "adjust_type"))
                if ind_rows:
                    save_stock_indicators(ind_rows)
            except Exception as ind_err:
                # K线已保存; 指标失败不能无感知(下游形态引擎要求指标齐全)
                print(f"[warn] {symbol} 指标计算失败: {ind_err}", file=sys.stderr)
                save_data_coverage("stock_indicators", symbol, adjust_type=coverage_adjust,
                                   first_date=first_date, last_date=last_date,
                                   row_count=len(local_klines), status="failed", last_error=str(ind_err))
            else:
                save_data_coverage("stock_indicators", symbol, adjust_type=coverage_adjust,
                                   first_date=first_date, last_date=last_date,
                                   row_count=len(local_klines), status="ready")
            price_status = ("ready" if expected and last_date == expected and total_rows >= ready_bars else "partial")
            save_data_coverage("stock_kline", symbol, adjust_type=coverage_adjust,
                               first_date=first_date, last_date=last_date,
                               row_count=total_rows, source=source,
                               status=price_status)
            result_status = "ok" if coverage_adjust == adjust else "degraded"
            return {"status": result_status, "symbol": symbol, "count": len(klines),
                    "total_count": total_rows,
                    "first_date": first_date, "last_date": last_date,
                    "adjust_type": coverage_adjust}
        if start_date is None:
            save_data_coverage("stock_kline", symbol, adjust_type=adjust, status="empty")
        return {"status": "empty", "symbol": symbol}
    except Exception as e:
        save_data_coverage("stock_kline", symbol, adjust_type=adjust,
                           status="failed", last_error=str(e))
        return {"status": "error", "symbol": symbol, "error": str(e)}


async def download_stock_kline(symbol: str, days: int = 560, adjust: str = "qfq") -> dict:
    return await _download_one_kline(symbol, days, adjust)


async def download_stocks_kline_batch(symbols: list[str], days: int = 560, adjust: str = "qfq") -> dict:
    """并发批量下载多只股票 K 线"""
    async def _task(sym):
        return await _download_one_kline(sym, days, adjust)

    results = await _concurrent_map(symbols, _task, desc="K线批量")
    total = sum(r.get("count", 0) for r in results if isinstance(r, dict))
    return {"status": "ok", "symbols": len(symbols), "total_klines": total, "detail": results}


async def sync_stock_kline_universe(symbols: list[str] | None = None,
                                    target_bars: int = 320, adjust: str = "qfq",
                                    workers: int = 6, resume: bool = True,
                                    incremental: bool = False) -> dict:
    """Synchronize a stock universe and report honest data coverage."""
    if symbols is None:
        symbols = [r["symbol"] for r in query_stock_db("SELECT symbol FROM stock_basic ORDER BY symbol")]
    symbols = list(dict.fromkeys(symbols))
    coverage_rows = query_stock_db(
        """SELECT symbol,last_date,status,row_count FROM data_coverage
           WHERE data_type='stock_kline' AND period='daily' AND adjust_type=?""",
        (adjust,),
    )
    coverage_by_symbol = {r["symbol"]: r for r in coverage_rows}
    from .quality import latest_completed_trade_day
    expected = latest_completed_trade_day()
    if incremental:
        today = expected or date.today().isoformat()
        pending = [s for s in symbols
                   if not coverage_by_symbol.get(s)
                   or str(coverage_by_symbol[s].get("last_date") or "") < today]
    elif resume:
        ready = {s for s, r in coverage_by_symbol.items()
                 if r.get("status") == "ready" and int(r.get("row_count") or 0) >= target_bars
                 and expected and str(r.get("last_date") or "") >= expected}
        pending = [s for s in symbols if s not in ready]
    else:
        pending = symbols
    sem = asyncio.Semaphore(max(1, min(int(workers), 16)))

    async def _one(sym):
        async with sem:
            incremental_start = None
            if incremental:
                last_date = str((coverage_by_symbol.get(sym) or {}).get("last_date") or "")[:10]
                if last_date:
                    incremental_start = (date.fromisoformat(last_date) - timedelta(days=10)).isoformat()
            return await _download_one_kline(sym, days=max(560, int(target_bars * 1.6)),
                                             adjust=adjust, ready_bars=target_bars,
                                             start_date=incremental_start)

    results = await asyncio.gather(*(_one(s) for s in pending), return_exceptions=True)
    detail = [r if isinstance(r, dict) else {"status": "error", "error": str(r)} for r in results]
    rows = query_stock_db(
        """SELECT symbol,status,row_count,last_date FROM data_coverage
           WHERE data_type='stock_kline' AND period='daily' AND adjust_type=?""", (adjust,))
    requested = set(symbols)
    covered = [r for r in rows if r["symbol"] in requested and r["status"] == "ready"
               and int(r.get("row_count") or 0) >= target_bars
               and expected and str(r.get("last_date") or "") >= expected]
    ready_count = len(covered)
    failed = sum(1 for r in detail if r.get("status") in {"error", "empty", "degraded"})
    status = "ok" if not failed and ready_count == len(symbols) else "partial"
    return {"status": status, "total": len(symbols), "processed": len(pending),
            "ready": ready_count, "covered": len(covered), "failed": failed,
            "coverage_ratio": len(covered) / len(symbols) if symbols else 0.0,
            "expected_trade_date": expected,
            "warnings": [] if expected else ["交易日历未确认，不能声明数据新鲜或全市场完成"],
            "detail": detail}
