"""
build/backfill.py — 缺口检测与补齐 (history / rank / combined)
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ...core.parallel import run_parallel
from ..sources import load_trade_dates
from ..storage import (
    build_combined_from_history,
    query_stock_db,
    save_popularity_rank,
    save_stock_kline,
    upsert_combined_spot,
)
from .helpers import _get_symbols, _net, _today


# ── 缺口检测 ──


def _date_symbol_counts(table: str, date_col: str, start: str, end: str) -> dict[str, int]:
    rows = query_stock_db(
        f"SELECT {date_col} AS d, COUNT(DISTINCT symbol) AS cnt FROM {table} "
        f"WHERE {date_col} >= ? AND {date_col} <= ? GROUP BY {date_col}", (start, end))
    return {r["d"]: r["cnt"] for r in rows}


def _reference_count(table: str, date_col: str) -> int:
    rows = query_stock_db(
        f"SELECT MAX(cnt) AS m FROM ("
        f"  SELECT {date_col} AS d, COUNT(DISTINCT symbol) AS cnt FROM {table} "
        f"  WHERE {date_col} >= date('now', '-30 days') GROUP BY {date_col})")
    return (rows[0]["m"] or 5000) if rows else 5000


def get_missing_trade_dates(table: str, date_col: str, start: str, end: str) -> list[str]:
    """[start, end] 内缺失的交易日 (有数据股票数 < 基准90%)"""
    try:
        all_trade_dates = load_trade_dates()
    except Exception as e:
        print(f"警告: 无法获取交易日历 ({e})，将用周一到周五近似", flush=True)
        all_trade_dates = None

    date_counts = _date_symbol_counts(table, date_col, start, end)
    ref_count = _reference_count(table, date_col)

    missing = []
    cur = datetime.strptime(start, "%Y-%m-%d").date()
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    while cur <= end_d:
        cur_str = cur.strftime("%Y-%m-%d")
        if all_trade_dates is not None:
            is_trade = cur_str in all_trade_dates
        else:
            is_trade = cur.weekday() < 5
        if is_trade:
            have = date_counts.get(cur_str, 0)
            if have < ref_count * 0.9:
                missing.append(cur_str)
        cur += timedelta(days=1)

    if missing:
        print(f"基准单日股票数: {ref_count}", flush=True)
        for d in missing:
            print(f"  缺失 {d}: 仅 {date_counts.get(d, 0)}/{ref_count} 只股票有数据", flush=True)
    return missing


def _estimate_lmt(start_date: str) -> int:
    """估算覆盖 start_date 至今需要的K线根数 (交易日数 × 1.5 + 20 缓冲)"""
    try:
        trade_dates = load_trade_dates()
        today = datetime.now().strftime("%Y-%m-%d")
        n = sum(1 for d in trade_dates if start_date <= d <= today)
        return min(max(int(n * 1.5) + 20, 30), 5000)
    except Exception:
        days = (datetime.now() - datetime.strptime(start_date, "%Y-%m-%d")).days
        return min(max(int(days * 1.1) + 20, 30), 5000)


# ── 补齐 ──


def backfill_history(start: str, end: str, symbols: list[str] | None = None,
                     max_workers: int = 8, dry_run: bool = False) -> int:
    """补齐 stock_kline 缺失日期的K线 (INSERT OR REPLACE 幂等)"""
    if dry_run:
        n_sym = len(symbols) if symbols else "all"
        print(f"[DRY-RUN] backfill history: {start} ~ {end}, symbols={n_sym}, "
              f"workers={max_workers}")
        return 0
    if symbols is None:
        symbols = _get_symbols()
    missing_dates = get_missing_trade_dates("stock_kline", "date", start, end)
    if not missing_dates:
        print(f"stock_kline 在 {start} ~ {end} 范围内没有缺失的交易日。")
        return 0
    print(f"发现 {len(missing_dates)} 个缺失交易日: {missing_dates[:10]}...", flush=True)

    lmt = _estimate_lmt(min(missing_dates))
    print(f"每股取最近 {lmt} 根K线 (INSERT OR REPLACE, 已有数据幂等覆盖)", flush=True)

    total_rows = 0
    completed = 0
    success = 0
    failed = 0

    def _fetch(sym: str):
        return _net("fetch_kline_history", sym, "qfq", limit=lmt)

    for symbol, result in run_parallel(symbols, _fetch, workers=max_workers):
        if isinstance(result, Exception):
            failed += 1
            print(f"  {symbol}: 失败 ({result})", flush=True)
        elif result:
            save_stock_kline(result)
            total_rows += len(result)
            success += 1
        completed += 1
        if completed % 200 == 0 or completed == len(symbols):
            print(f"  进度: {completed}/{len(symbols)} 成功={success} 失败={failed} "
                  f"写入行={total_rows}", flush=True)

    print(f"\nstock_kline 补全完成: 写入 {total_rows} 行 ({success} 只股票成功)")
    return total_rows


def backfill_rank(start: str, end: str, max_workers: int = 8,
                  dry_run: bool = False) -> int:
    """补齐 stock_popularity_rank 缺失日期 (股吧年文件, 只覆盖滚动一年)"""
    if dry_run:
        print(f"[DRY-RUN] backfill rank: {start} ~ {end}, workers={max_workers} "
              "(guba yearly files, rolling one year)")
        return 0
    missing_dates = get_missing_trade_dates("stock_popularity_rank", "trade_date", start, end)
    if not missing_dates:
        print(f"stock_popularity_rank 在 {start} ~ {end} 范围内没有缺失的交易日。")
        return 0
    print(f"发现 {len(missing_dates)} 个缺失交易日: {missing_dates[:10]}...", flush=True)

    symbols = _get_symbols()
    missing_date_set = set(missing_dates)

    total_rows = 0
    completed = 0
    failed = 0
    for symbol, result in run_parallel(
            symbols, lambda s: _net("fetch_guba_rank_history", s), workers=max_workers):
        if isinstance(result, Exception):
            failed += 1
            print(f"  {symbol}: 失败 ({result})", flush=True)
        else:
            recs = [r for r in result if r.get("trade_date") in missing_date_set]
            save_popularity_rank(recs, replace=False)
            total_rows += len(recs)
        completed += 1
        if completed % 200 == 0 or completed == len(symbols):
            print(f"  进度: {completed}/{len(symbols)} 失败={failed} 写入行={total_rows}",
                  flush=True)

    print(f"\nstock_popularity_rank 补全完成: 写入 {total_rows} 行, 失败 {failed}")
    return total_rows


def rebuild_combined(dry_run: bool = False) -> int:
    """用 history + rank + spot 全量重建 stock_daily_combined"""
    if dry_run:
        print("[DRY-RUN] 将重建 stock_daily_combined "
              "(stock_kline JOIN stock_popularity_rank + daily_stock_info)。")
        return 0
    kline_rows = build_combined_from_history()
    spot_rows = upsert_combined_spot()
    print(f"stock_daily_combined 重建完成: kline-join={kline_rows}, spot={spot_rows}")
    return kline_rows + spot_rows


def backfill_data(start: str | None = None, end: str | None = None,
                  do_history: bool = True, do_rank: bool = True, do_combined: bool = True,
                  symbols: list[str] | None = None,
                  max_workers: int = 8, dry_run: bool = False) -> int:
    """缺口检测补齐入口"""
    end = end or _today()
    if dry_run:
        print(f"[DRY-RUN] backfill plan: {start or end} ~ {end}, "
              f"history={do_history}, rank={do_rank}, combined={do_combined}")
        return 0
    if (do_history or do_rank) and not start:
        rows = query_stock_db(
            "SELECT date FROM stock_kline WHERE adjust_type='qfq' AND date >= date('now', '-30 days') "
            "GROUP BY date ORDER BY COUNT(DISTINCT symbol) DESC, date DESC LIMIT 1")
        start = rows[0]["date"] if rows else end
    print(f"补数据日期范围: {start} ~ {end}")

    total = 0
    if do_history:
        print("=" * 60)
        print("步骤1: 补全 stock_kline (K线数据)")
        print("=" * 60, flush=True)
        total += backfill_history(start, end, symbols=symbols, max_workers=max_workers,
                                  dry_run=dry_run)
        print()
    if do_rank:
        print("=" * 60)
        print("步骤2: 补全 stock_popularity_rank (股吧年文件)")
        print("=" * 60, flush=True)
        total += backfill_rank(start, end, max_workers=max_workers, dry_run=dry_run)
        print()
    if do_combined:
        print("=" * 60)
        print("步骤3: 重建 stock_daily_combined")
        print("=" * 60, flush=True)
        total += rebuild_combined(dry_run=dry_run)
    return total
