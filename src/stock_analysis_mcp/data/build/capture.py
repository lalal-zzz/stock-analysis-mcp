"""
build/capture.py — 晚间采集

xuangu 排名快照 + 全市场 spot 快照 + 最近K线增量 + 指标缓存; 跳过非交易日。
dry-run 在一切网络调用 (含交易日历) 之前短路。
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from ...core.parallel import run_parallel
from ..sources import is_trade_day
from ..storage import (
    init_all,
    save_daily_stock_info,
    save_popularity_rank,
    save_stock_basic,
    save_stock_kline,
    upsert_combined_rank,
    upsert_combined_spot,
)
from ..sync import _stocks_from_spot
from .helpers import _get_symbols, _net
from .rebuild import compute_all_stock_indicators


def save_rank_snapshot(rows: list[dict], trade_date: str, capture_time: str) -> int:
    """xuangu 快照行 -> stock_popularity_rank (source='xuangu') + combined 排名列"""
    rank_rows = []
    for r in rows:
        match = re.search(r"(\d{6})", str(r.get("SECURITY_CODE") or ""))
        symbol = match.group(1) if match else None
        rank = r.get("POPULARITY_RANK")
        if not symbol or rank is None:
            continue
        try:
            rank_val = int(float(rank))
        except (ValueError, TypeError):
            continue
        rank_rows.append({
            "trade_date": trade_date,
            "symbol": symbol,
            "rank": rank_val,
            "rank_time": capture_time,
            "source": "xuangu",
        })
    written = save_popularity_rank(rank_rows, replace=True)
    if is_trade_day(trade_date):
        upsert_combined_rank(trade_date)
    return written


def save_spot_snapshot(capture_time: str, trade_date: str) -> int:
    """全市场 spot -> daily_stock_info + stock_basic 刷新 + combined"""
    spot_rows = _net("fetch_full_spot", verbose=True)
    if not spot_rows:
        raise RuntimeError("fetch_full_spot returned no rows")
    list_rows = _stocks_from_spot(spot_rows)
    save_stock_basic(list_rows)
    written = save_daily_stock_info(trade_date, capture_time, spot_rows)
    upsert_combined_spot()
    return written


def save_recent_klines(workers: int = 8, limit: int = 5) -> int:
    """全市场最近 N 根日K -> stock_kline (增量, INSERT OR REPLACE)"""
    symbols = _get_symbols()
    start = time.time()
    total_bars = 0
    failed = 0

    def _fetch(sym: str):
        from ..quality import latest_completed_trade_day
        return _net("fetch_kline_history", sym, "qfq", limit=limit,
                    end_date=latest_completed_trade_day())

    for _sym, result in run_parallel(symbols, _fetch, workers=workers):
        if isinstance(result, Exception):
            failed += 1
            continue
        if result:
            try:
                save_stock_kline(result)
            except Exception as exc:
                from ..quality import PriceBasisChanged
                if isinstance(exc, PriceBasisChanged):
                    from ..repair import repair_history
                    try:
                        repaired = repair_history(_sym, "qfq")
                        total_bars += repaired["bars"]
                        continue
                    except Exception:
                        failed += 1
                        continue
                failed += 1
                continue
            total_bars += len(result)
        else:
            failed += 1
    print(f"kline incremental: {total_bars} bars, failed={failed}, "
          f"{int(time.time() - start)}s", flush=True)
    if failed:
        raise RuntimeError(f"K-line capture incomplete: {failed} symbols failed; successful rows retained")
    return total_bars


def daily_capture(skip_non_trading_day: bool = True, save_spot: bool = True,
                  save_kline: bool = True, save_indicators: bool = True,
                  page_size: int = 500, max_pages: int | None = None,
                  workers: int = 8, dry_run: bool = False) -> int:
    """晚间采集: xuangu 排名 + spot 快照 + 最近K线增量 + 指标缓存"""
    now = datetime.now()
    capture_time = now.strftime("%Y-%m-%d %H:%M:%S")
    date_part = now.strftime("%Y-%m-%d")

    # dry-run 必须在一切网络调用(含交易日历)之前短路: 只打印计划, 不联网不写库
    if dry_run:
        print("[DRY-RUN] daily capture plan:")
        print(f"  - xuangu rankings (page_size={page_size}, max_pages={max_pages})")
        if save_spot:
            print("  - full-market spot snapshot -> daily_stock_info + stock_basic")
        if save_kline:
            print("  - recent klines (limit=5) -> stock_kline")
        if save_indicators:
            print("  - compute stock indicators -> stock_indicators")
        if skip_non_trading_day:
            print("  - skip if not a trading day")
        print("[DRY-RUN] Nothing was downloaded or written.")
        return 0

    if not _net("wait_for_internet", timeout_seconds=120):
        raise RuntimeError("Network connection not available after 120 seconds.")

    if skip_non_trading_day and not is_trade_day(now.date()):
        message = f"{capture_time} capture: skipped, {date_part} is not a trading day"
        print(message, flush=True)
        return 0

    rows = _net("fetch_xuangu_rankings", page_size=page_size, max_pages=max_pages,
                verbose=True)
    if not rows:
        raise RuntimeError("no rows returned from Eastmoney xuangu API")

    init_all()  # 幂等建表保险

    rank_written = save_rank_snapshot(rows, date_part, capture_time)
    spot_message = ""
    if save_spot:
        spot_written = save_spot_snapshot(capture_time, date_part)
        spot_message = f", spot={spot_written}"
    kline_message = ""
    if save_kline:
        bars = save_recent_klines(workers=workers)
        kline_message = f", kline_bars={bars}"
    indicators_message = ""
    if save_indicators:
        print("正在计算并缓存全部股票的技术指标...", flush=True)
        try:
            compute_all_stock_indicators(workers=max(workers, 10))
            indicators_message = ", indicators=cached"
        except Exception as e:
            indicators_message = f", indicators=failed({e})"
            print(f"指标缓存失败: {e}", flush=True)

    message = (f"{capture_time} capture: rank={rank_written}"
               f"{spot_message}{kline_message}{indicators_message}")
    print(message, flush=True)
    return rank_written
