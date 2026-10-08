"""
build/rebuild.py — 全量重建

步骤: spot -> kline -> guba -> combined -> 指标缓存 (+ 可选板块全量K线/指标)。
rebuild_full_data 为编排入口: 断点续传 + dry-run 预览。
所有并行循环统一走 core.parallel.run_parallel。
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime

import pandas as pd

from ...core.parallel import run_parallel
from ..sources import is_trade_day, previous_trade_day
from ..storage import (
    build_combined_from_history,
    get_rebuild_done,
    get_sector_db,
    get_stock_db,
    init_all,
    query_sector_db,
    query_stock_db,
    record_rebuild_progress,
    save_daily_stock_info,
    save_popularity_rank,
    save_sector_indicators,
    save_sector_kline,
    save_stock_basic,
    save_stock_indicators,
    save_stock_kline,
    upsert_combined_spot,
)
from ..sync import _stocks_from_spot
from .helpers import (
    _fetch_full_history,
    _get_symbols,
    _indicator_rows_from_df,
    _net,
    _sector_codes,
    _sector_kline_full,
    _table_count,
    _today,
)


# ── 股票步骤 ──


def step_spot(dry_run: bool = False) -> int:
    """全市场 spot -> stock_basic + daily_stock_info (最近交易日快照) """
    if dry_run:
        print("[DRY-RUN] step spot: fetch full-market spot -> stock_basic + daily_stock_info")
        return 0
    if _table_count(get_stock_db(), "stock_basic") > 0:
        print("step spot: stock_basic already populated, skip.")
        return 0

    print("step spot: fetching full-market spot ...", flush=True)
    rows = _net("fetch_full_spot", verbose=True)
    if not rows:
        raise RuntimeError("fetch_full_spot returned no rows")

    capture_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    spot_trade_date = _today() if is_trade_day(_today()) else previous_trade_day(_today())
    print(f"step spot: {len(rows)} rows, snapshot trade_date={spot_trade_date}", flush=True)

    list_rows = _stocks_from_spot(rows)
    save_stock_basic(list_rows)
    written = save_daily_stock_info(spot_trade_date, capture_time, rows)
    print(f"step spot: stock_basic={len(list_rows)}, daily_stock_info={written}", flush=True)
    return written


def step_kline(workers: int = 8, max_passes: int = 3, dry_run: bool = False) -> int:
    """全市场K线 -> stock_kline (按 symbol 断点续传; 空结果视为失败, 多轮重试) """
    if dry_run:
        print("[DRY-RUN] step kline: download full history for all stocks "
              "(tencent fqkline primary, workers={})".format(workers))
        return 0
    symbols = _get_symbols()
    done = get_rebuild_done("kline")
    pending = [s for s in symbols if s not in done]
    print(f"step kline: {len(symbols)} symbols, {len(done)} done, {len(pending)} pending",
          flush=True)
    if not pending:
        return 0
    if dry_run:
        print(f"[DRY-RUN] step kline: download full history for {len(pending)} symbols "
              f"(tencent fqkline primary, workers={workers})")
        return 0

    start = time.time()
    total_bars = 0
    pass_num = 0
    while pending and pass_num < max_passes:
        pass_num += 1
        if pass_num > 1:
            wait_s = 30 * (pass_num - 1)
            print(f"kline pass {pass_num}: retry {len(pending)} failed symbols "
                  f"after {wait_s}s cooldown...", flush=True)
            time.sleep(wait_s)

        processed = 0
        failed = []
        for symbol, result in run_parallel(pending, _fetch_full_history, workers=workers):
            if isinstance(result, Exception):
                failed.append(symbol)
                print(f"  kline {symbol} FAILED: {result}", flush=True)
                continue
            if not result:
                failed.append(symbol)
                continue
            try:
                from ..storage.writer import replace_stock_history
                replace_stock_history(symbol, "qfq", result, reason="complete-history rebuild")
            except Exception as exc:
                failed.append(symbol)
                print(f"  kline {symbol} write rejected: {exc}", flush=True)
                continue
            record_rebuild_progress("kline", symbol, len(result))
            total_bars += len(result)
            processed += 1
            if processed % 200 == 0:
                elapsed = int(time.time() - start)
                print(f"  kline pass {pass_num}: {processed}/{len(pending)}, "
                      f"total {total_bars} bars, {elapsed}s elapsed", flush=True)
        pending = failed

    print(f"step kline: done, +{total_bars} bars in {int(time.time() - start)}s, "
          f"unresolved after {pass_num} passes={len(pending)} {pending[:10]}", flush=True)
    return total_bars


def step_guba_rank(workers: int = 8, dry_run: bool = False) -> int:
    """股吧年文件 -> stock_popularity_rank (按 symbol 断点续传, 不覆盖已有 xuangu 值) """
    if dry_run:
        print("[DRY-RUN] step guba: fetch guba yearly files for all stocks "
              "(rolling one year, INSERT OR IGNORE)")
        return 0
    symbols = _get_symbols()
    done = get_rebuild_done("guba")
    pending = [s for s in symbols if s not in done]
    print(f"step guba: {len(symbols)} symbols, {len(done)} done, {len(pending)} pending",
          flush=True)
    if not pending:
        return 0
    if dry_run:
        print(f"[DRY-RUN] step guba: fetch guba yearly files for {len(pending)} symbols "
              f"(rolling one year, INSERT OR IGNORE)")
        return 0

    start = time.time()
    processed = 0
    total_rows = 0
    failed = []
    for symbol, result in run_parallel(
            pending, lambda s: _net("fetch_guba_rank_history", s), workers=workers):
        if isinstance(result, Exception):
            failed.append(symbol)
            print(f"  guba {symbol} FAILED: {result}", flush=True)
            continue
        recs = result
        save_popularity_rank(recs, replace=False)
        record_rebuild_progress("guba", symbol, len(recs))
        total_rows += len(recs)
        processed += 1
        if processed % 200 == 0:
            elapsed = int(time.time() - start)
            print(f"  guba progress: {processed}/{len(pending)}, "
                  f"{total_rows} records, {elapsed}s elapsed", flush=True)

    print(f"step guba: done, +{total_rows} records in {int(time.time() - start)}s, "
          f"failed={len(failed)} {failed[:10]}", flush=True)
    return total_rows


def step_combined(dry_run: bool = False) -> int:
    """stock_daily_combined <- K线 INNER JOIN 排名 + 已有 spot 快照"""
    if dry_run:
        print("[DRY-RUN] step combined: rebuild stock_daily_combined "
              "(stock_kline JOIN stock_popularity_rank + daily_stock_info)")
        return 0
    start = time.time()
    kline_rows = build_combined_from_history()
    spot_rows = upsert_combined_spot()
    total = _table_count(get_stock_db(), "stock_daily_combined")
    print(f"step combined: kline-join rows={kline_rows}, spot rows={spot_rows}, "
          f"total={total}, {int(time.time() - start)}s", flush=True)
    return kline_rows + spot_rows


# ── 指标缓存 ──


def compute_all_stock_indicators(workers: int = 8, dry_run: bool = False) -> int:
    """遍历全部股票, 从 stock_kline 计算技术指标并写入 stock_indicators"""
    if dry_run:
        print("[DRY-RUN] step indicators: compute indicators for all stocks -> stock_indicators")
        return 0
    symbols = _get_symbols()
    if not symbols:
        print("step indicators: no stocks in stock_basic, skip.")
        return 0
    print(f"step indicators: computing indicators for {len(symbols)} stocks ...", flush=True)
    start = time.time()
    success = 0

    def _calc(sym: str) -> int:
        rows = query_stock_db(
            "SELECT date, open, high, low, close, volume FROM stock_kline "
            "WHERE symbol=? AND adjust_type='qfq' ORDER BY date", (sym,))
        if len(rows) < 30:
            return 0
        df = pd.DataFrame(rows)
        ind_rows = _indicator_rows_from_df(df, index_cols=("date",))
        if not ind_rows:
            return 0
        for r in ind_rows:
            r["symbol"] = sym
        save_stock_indicators(ind_rows)
        return len(ind_rows)

    def _on_progress(done_count: int, total: int) -> None:
        if done_count % 500 == 0 or done_count == total:
            print(f"  indicators: {done_count}/{total} ok={success}, "
                  f"{int(time.time() - start)}s", flush=True)

    for _sym, result in run_parallel(symbols, _calc, workers=workers,
                                     on_progress=_on_progress):
        if not isinstance(result, Exception) and result > 0:
            success += 1
    print(f"step indicators: done, {success} stocks cached in {int(time.time() - start)}s",
          flush=True)
    return success


def update_stock_indicators_since(start_date: str, workers: int = 8,
                                  lookback: int = 320) -> int:
    """Recompute enough history for indicators, but persist only new dates."""
    symbols = _get_symbols()
    success = 0
    written = 0
    started = time.time()

    def _calc(sym: str) -> int:
        rows = query_stock_db(
            "SELECT date,open,high,low,close,volume FROM ("
            " SELECT date,open,high,low,close,volume FROM stock_kline"
            " WHERE symbol=? AND adjust_type='qfq' ORDER BY date DESC LIMIT ?"
            ") ORDER BY date", (sym, lookback))
        if len(rows) < 30:
            return 0
        df = pd.DataFrame(rows)
        values = _indicator_rows_from_df(df, index_cols=("date",))
        values = [r for r in values if r.get("date") and r["date"] >= start_date]
        for row in values:
            row["symbol"] = sym
            row["adjust_type"] = "qfq"
        return save_stock_indicators(values) if values else 0

    def _on_progress(done_count: int, total: int) -> None:
        if done_count % 500 == 0 or done_count == total:
            print(f"  incremental indicators: {done_count}/{total} "
                  f"ok={success} rows={written}, {int(time.time() - started)}s",
                  flush=True)

    for _sym, result in run_parallel(symbols, _calc, workers=workers,
                                     on_progress=_on_progress):
        if isinstance(result, Exception):
            continue
        if result:
            success += 1
            written += result
    return written


# ── 板块步骤 ──


def download_sector_full_history(workers: int = 8, limit: int = 5000,
                                 dry_run: bool = False) -> int:
    """全量板块K线 -> sector_kline (东财单源, 带熔断; ~580 板块) """
    if dry_run:
        print(f"[DRY-RUN] step sector-kline: download full history for all sectors "
              f"(eastmoney single-source, limit={limit}, workers={workers})")
        return 0
    codes = _sector_codes()
    print(f"step sector-kline: {len(codes)} sectors, limit={limit} ...", flush=True)
    start = time.time()
    total = 0
    failed = []

    def _fetch_one(item: tuple[str, str | None]) -> list[dict]:
        c, n = item
        return _sector_kline_full(c, n, limit)

    for code, result in run_parallel(codes, _fetch_one, workers=workers):
        if isinstance(result, Exception):
            failed.append(code)
            print(f"  sector {code} FAILED: {result}", flush=True)
            continue
        if not result:
            failed.append(code)
            continue
        save_sector_kline(result)
        total += len(result)
    print(f"step sector-kline: done, +{total} bars in {int(time.time() - start)}s, "
          f"failed={len(failed)} {failed[:10]}", flush=True)
    return total


def compute_all_sector_indicators(workers: int = 8, dry_run: bool = False) -> int:
    """从 sector_kline 计算板块技术指标 -> sector_indicators"""
    if dry_run:
        print("[DRY-RUN] step sector-indicators: compute indicators for all sectors "
              "-> sector_indicators")
        return 0
    codes = [r["sector_code"] for r in query_sector_db("SELECT sector_code FROM sector_basic")]
    if not codes:
        print("step sector-indicators: no sectors in sector_basic, skip.")
        return 0
    if dry_run:
        print(f"[DRY-RUN] step sector-indicators: compute indicators for {len(codes)} sectors "
              f"-> sector_indicators")
        return 0
    print(f"step sector-indicators: {len(codes)} sectors ...", flush=True)
    start = time.time()
    success = 0

    def _calc(code: str) -> int:
        rows = query_sector_db(
            "SELECT trade_date AS date, open, high, low, close, volume, turnover_rate "
            "FROM sector_kline WHERE sector_code=? ORDER BY trade_date", (code,))
        if len(rows) < 30:
            return 0
        df = pd.DataFrame(rows)
        ind_rows = _indicator_rows_from_df(df, index_cols=("date",))
        if not ind_rows:
            return 0
        for r in ind_rows:
            r["sector_code"] = code
            r["trade_date"] = r.pop("date")
        save_sector_indicators(ind_rows)
        return len(ind_rows)

    def _on_progress(done_count: int, total: int) -> None:
        if done_count % 100 == 0 or done_count == total:
            print(f"  sector-indicators: {done_count}/{total} ok={success}, "
                  f"{int(time.time() - start)}s", flush=True)

    for _code, result in run_parallel(codes, _calc, workers=workers,
                                      on_progress=_on_progress):
        if not isinstance(result, Exception) and result > 0:
            success += 1
    print(f"step sector-indicators: done, {success} sectors cached in "
          f"{int(time.time() - start)}s", flush=True)
    return success


# ── 重建主流程 ──


def rebuild_full_data(force: bool = False, workers: int = 8,
                      skip_spot: bool = False, skip_kline: bool = False,
                      skip_rank: bool = False, skip_combined: bool = False,
                      skip_indicators: bool = False,
                      with_sectors: bool = False, sector_limit: int = 5000,
                      dry_run: bool = False) -> int:
    """步骤化全量重建 (断点续传 + dry-run 预览, 不下载不写库)"""
    if force and not dry_run:
        for db_path in (get_stock_db(), get_sector_db()):
            if os.path.exists(db_path):
                print(f"deleting old database: {db_path}", flush=True)
                os.unlink(db_path)
                for suffix in ("-wal", "-shm"):
                    side = db_path + suffix
                    if os.path.exists(side):
                        os.unlink(side)

    if dry_run:
        print("=" * 60)
        print("[DRY-RUN] rebuild plan (no network / no writes):")
        print("=" * 60)
    else:
        init_all()  # 幂等建表
    steps = []
    if not skip_spot:
        steps.append(("spot", lambda: step_spot(dry_run=dry_run)))
    if not skip_kline:
        steps.append(("kline", lambda: step_kline(workers=workers, dry_run=dry_run)))
    if not skip_rank:
        steps.append(("guba", lambda: step_guba_rank(workers=workers, dry_run=dry_run)))
    if not skip_combined:
        steps.append(("combined", lambda: step_combined(dry_run=dry_run)))
    if not skip_indicators:
        steps.append(("indicators",
                      lambda: compute_all_stock_indicators(workers=workers, dry_run=dry_run)))
    if with_sectors:
        steps.append(("sector-kline",
                      lambda: download_sector_full_history(workers=workers,
                                                           limit=sector_limit, dry_run=dry_run)))
        steps.append(("sector-indicators",
                      lambda: compute_all_sector_indicators(workers=workers, dry_run=dry_run)))

    if not dry_run and not _net("wait_for_internet", timeout_seconds=120):
        print("no internet, abort.", flush=True)
        return 1

    overall_start = time.time()
    for name, fn in steps:
        print("=" * 60)
        print(f"STEP {name}")
        print("=" * 60, flush=True)
        fn()

    if not dry_run:
        with sqlite3.connect(get_stock_db()) as conn:
            for table in ("stock_basic", "stock_kline", "daily_stock_info",
                          "stock_popularity_rank", "stock_daily_combined"):
                print(f"final count {table}: "
                      f"{conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
        print(f"rebuild finished in {int(time.time() - overall_start)}s.", flush=True)
    else:
        print("[DRY-RUN] done. Nothing was downloaded or written.")
    return 0
