"""Fixed 14:30 Asia/Shanghai observations, never reconstructed from late data."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..core.config import get_settings
from . import sector_hot
from .quality import cached_trade_dates

TZ = ZoneInfo("Asia/Shanghai")


def _now():
    return datetime.now(TZ)


def _path():
    return get_settings().data_root / "information-cache" / "sector-hot-1430.sqlite3"


def _read(day):
    path = _path()
    if not path.exists():
        return None
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as c:
        row = c.execute("SELECT payload FROM captures WHERE trade_date=?", (day,)).fetchone()
    return json.loads(row[0]) if row else None


def _in_window(now):
    return now.hour == 14 and 30 <= now.minute < 35


def capture_sector_hot_1430():
    """Fetch fresh data only during [14:30,14:35); first successful capture wins."""
    start = _now().astimezone(TZ)
    day = start.date().isoformat()
    if not _in_window(start):
        return {"status": "skipped", "reason": "outside_1430_1435_Asia_Shanghai", "observed_at": start.isoformat()}
    dates = cached_trade_dates()
    if not dates or max(dates) < day:
        from .sources import load_trade_dates
        dates = load_trade_dates()
    if not dates or max(dates) < day:
        return {"status": "unavailable", "reason": "trading_calendar_not_current"}
    if day not in dates:
        return {"status": "skipped", "reason": "non_trading_day"}
    if _read(day):
        return get_sector_hot_1430(day)
    previous = max((d for d in dates if d < day), default=None)
    # Bypass the five-minute cache: a 14:27 cache is not a 14:30 observation.
    records = sector_hot._load()
    finish = _now().astimezone(TZ)
    if finish.date() != start.date() or not _in_window(finish):
        return {"status": "unavailable", "reason": "request_completed_outside_capture_window"}
    payload = {"trade_date": day, "previous_trade_date": previous,
               "request_started_at": start.isoformat(), "observed_at": finish.isoformat(),
               "target_time": "14:30:00+08:00", "allowed_delay_seconds": 300,
               "source_as_of": None, "source_url": sector_hot.PLATE_URL,
               "freshness_verified": False, "records": records}
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS captures (trade_date TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        c.execute("INSERT OR IGNORE INTO captures VALUES (?, ?)",
                  (day, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
    return get_sector_hot_1430(day)


def get_sector_hot_1430(trade_date=None, limit=20):
    """Read exact-day observations; never silently compare against an older day."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be 1..20")
    day = trade_date or _now().astimezone(TZ).date().isoformat()
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("trade_date must be YYYY-MM-DD")
    current = _read(day)
    if current is None:
        return {"status": "unavailable", "trade_date": day, "records": [],
                "warnings": ["该日期没有14:30窗口采集记录，不能用当前榜单回填。"]}
    previous_day = current["previous_trade_date"]
    previous = _read(previous_day) if previous_day else None
    old_rows = {r["sector_code"]: r for r in previous["records"]} if previous else {}
    rows = []
    for r in current["records"][:limit]:
        old = old_rows.get(r["sector_code"])
        rows.append({**r, "previous_heat_value": old["heat_value"] if old else None,
                     "previous_heat_rank": old["heat_rank"] if old else None,
                     "heat_delta": r["heat_value"] - old["heat_value"] if old else None,
                     "heat_change_pct": (r["heat_value"] / old["heat_value"] - 1) * 100 if old and old["heat_value"] > 0 else None,
                     "rank_improvement": old["heat_rank"] - r["heat_rank"] if old else None,
                     "list_status": "retained" if old else "entered_bounded_list" if previous else "previous_capture_missing"})
    warnings = ["仅为14:30至14:35窗口的获取观测，准确时间见observed_at；来源行情时点未知。",
                "新入榜不等于首次市场升温；榜外热度未知，不能将其设为零。",
                "热度与涨幅不保证次日盈利；两日延续假设尚未验证。"]
    if previous is None:
        warnings.append("缺少前一交易日同窗口记录，所有差值为null，不回退到更早日期。")
    return {"status": "partial", "trade_date": day, "records": rows,
            "source": {k: v for k, v in current.items() if k != "records"},
            "comparison": {"basis": "previous_trading_day_1430_capture_window",
                           "previous_trade_date": previous_day,
                           "previous_observed_at": previous["observed_at"] if previous else None,
                           "left_list_codes": sorted(set(old_rows) - {r["sector_code"] for r in current["records"]})},
            "warnings": warnings}
