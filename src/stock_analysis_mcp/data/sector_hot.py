"""THS concept heat ranking and immutable observation snapshots.

THS codes are a separate namespace from Eastmoney BK codes. No implicit join,
trading date inference, capital-flow synthesis or price prediction is performed.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path

from .information import _cached, _request

PLATE_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate"
PAGE_URL = "https://eq.10jqka.com.cn/webpage/ths-hot-list/index.html"
CACHE_KEY = "sector-hot:v1:ths:concept"


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _load():
    payload = _request(PLATE_URL, params={"type": "concept"},
                       extra_headers={"Referer": PAGE_URL})
    if not isinstance(payload, dict) or payload.get("status_code") != 0:
        raise ValueError("THS plate endpoint returned an error")
    data = payload.get("data")
    raw = data.get("plate_list") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("THS plate response is empty or its schema changed")
    records, codes, ranks = [], set(), set()
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("THS plate row is not an object")
        code, name = str(row.get("code", "")), row.get("name")
        rank = _integer(row.get("order"))
        heat = _number(row.get("rate"))
        if (not re.fullmatch(r"\d{6}", code) or not isinstance(name, str)
                or not name.strip() or rank is None or rank < 1
                or heat is None or heat < 0 or code in codes or rank in ranks):
            raise ValueError("THS plate identity/rank/heat is invalid or duplicated")
        codes.add(code)
        ranks.add(rank)
        records.append({
            "sector_code": code, "sector_name": name.strip(), "sector_type": "concept",
            "code_namespace": "ths", "heat_rank": rank, "heat_value": heat,
            "change_pct": _number(row.get("rise_and_fall")),
            "source_rank_change_raw": _integer(row.get("hot_rank_chg")),
            "hot_tag": row.get("hot_tag") if isinstance(row.get("hot_tag"), str) else None,
            "tag": row.get("tag") if isinstance(row.get("tag"), str) else None,
            "source_as_of": None,
        })
    return sorted(records, key=lambda row: row["heat_rank"])


def _observations(records, source):
    """Store only network observations; compare the complete previous list.

    The cache timestamp identifies an observation. Cache reads never create new
    observations, nor do missing top-list members acquire a synthetic zero heat.
    """
    captured = source.get("fetched_at")
    cache_path = source.get("cache_path")
    if not captured or not cache_path:
        return None, []
    path = Path(cache_path).parent / "sector-hot-snapshots.sqlite3"
    writable = source.get("status") == "ok"
    if not writable and not path.exists():
        return None, []
    uri = str(path) if writable else path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=not writable, timeout=10) as connection:
        if writable:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS observations "
                               "(observed_at TEXT PRIMARY KEY, records_json TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO observations VALUES (?, ?)",
                               (captured, json.dumps(records, ensure_ascii=False, allow_nan=False)))
        previous = connection.execute(
            "SELECT observed_at, records_json FROM observations "
            "WHERE observed_at < ? ORDER BY observed_at DESC LIMIT 1", (captured,)
        ).fetchone()
    return (previous[0], json.loads(previous[1])) if previous else (None, [])


def get_sector_hot_rank(limit=20, offline=False, comparison="observations", trade_date=None):
    """Get the provider's bounded concept list, with signed observed deltas."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be an integer from 1 to 20")
    if not isinstance(offline, bool):
        raise ValueError("offline must be boolean")
    if comparison == "daily_1430":
        from .sector_hot_daily import get_sector_hot_1430
        return get_sector_hot_1430(trade_date, limit)
    if comparison != "observations" or trade_date is not None:
        raise ValueError("trade_date requires comparison=daily_1430")
    records, source = _cached(CACHE_KEY, _load, offline=offline, ttl=300)
    source = {**source, "provider": "ths", "url": PLATE_URL, "page_url": PAGE_URL,
              "source_as_of": None, "freshness_verified": False}
    warnings = [
        "来源未提供行情时点；fetched_at仅为获取时间，不能认定为今日/实时榜单。",
        "heat_value为同花顺展示热度，算法未公开；source_rank_change_raw的比较时点与正负口径未核实。",
        "仅来源热门概念榜，非全部板块；同花顺代码不能作为东方财富BK代码使用。",
    ]
    if source.get("stale"):
        warnings.append("使用过期缓存，不能作为当前热度。")
    if source.get("status") == "unavailable":
        warnings.append(source.get("error", "数据源不可用"))
    previous_at, previous = None, []
    try:
        previous_at, previous = _observations(records, source)
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        warnings.append(f"观测快照不可用，差值缺失：{exc}")
    previous_by_code = {row["sector_code"]: row for row in previous}
    output = []
    for row in records[:limit]:
        old = previous_by_code.get(row["sector_code"])
        output.append({**row,
                       "heat_delta": row["heat_value"] - old["heat_value"] if old else None,
                       "rank_improvement": old["heat_rank"] - row["heat_rank"] if old else None,
                       "previous_heat_value": old["heat_value"] if old else None,
                       "comparison_status": "matched" if old else "not_in_previous_list" if previous else "no_previous_observation"})
    return {"status": "unavailable" if source.get("status") == "unavailable" else "partial",
            "records": output, "source": source, "warnings": warnings,
            "coverage": {"scope": "provider_hot_concepts", "available_count": len(records),
                         "returned_count": len(output), "full_market": False},
            "comparison": {"previous_observed_at": previous_at,
                           "current_observed_at": source.get("fetched_at"),
                           "basis": "successive_fetch_observations_not_trading_days",
                           "heat_delta": "current heat - previous heat; positive means increased",
                           "rank_improvement": "previous rank - current rank; positive means improved",
                           "left_list_codes": sorted(set(previous_by_code) - {row["sector_code"] for row in records})}}
