"""Calendar freshness and price-series validation shared by all ingestion paths."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class PriceBasisChanged(ValueError):
    """Incoming adjusted prices cannot be safely appended to local history."""


def calendar_path() -> Path:
    from ..core.config import config_path
    return config_path().parent / "trade-calendar.json"


def cached_trade_dates() -> set[str]:
    from . import sources
    if sources._trade_dates_cache is not None:
        return set(sources._trade_dates_cache)
    try:
        return set(json.loads(calendar_path().read_text(encoding="utf-8"))["dates"])
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def cache_trade_dates(dates: set[str]) -> None:
    path = calendar_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"dates": sorted(dates),
        "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}), encoding="utf-8")
    temporary.replace(path)


def latest_completed_trade_day(*, allow_network: bool = False, now=None) -> str | None:
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.date().isoformat()
    dates = cached_trade_dates()
    if allow_network and (not dates or max(dates) < today):
        from .sources import load_trade_dates
        try:
            dates = load_trade_dates()
        except Exception:
            return None
    if not dates or max(dates) < today:
        return None
    completed = [d for d in dates if d < today or (d == today and now.hour >= 16)]
    return max(completed) if completed else None


def validate_prices(rows: list[dict], adjust: str) -> None:
    if adjust not in {"", "qfq", "hfq"}:
        raise ValueError("adjust must be qfq|hfq|empty")
    seen = set()
    for row in rows:
        if row.get("adjust_type", adjust) != adjust:
            raise ValueError("provider returned a different adjustment type")
        date = str(row.get("date", ""))[:10]
        datetime.strptime(date, "%Y-%m-%d")
        if date in seen:
            raise ValueError(f"duplicate K-line date: {date}")
        seen.add(date)
        for key in ("open", "high", "low", "close"):
            value = row.get(key)
            if value is None or not math.isfinite(float(value)):
                raise ValueError(f"invalid {key} at {date}")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            raise ValueError(f"invalid OHLC order at {date}")


def check_price_basis(existing: list[dict], incoming: list[dict], adjust: str) -> None:
    if not existing or not adjust:
        return
    old = {str(r["date"])[:10]: r for r in existing}
    overlap = [r for r in incoming if str(r["date"])[:10] in old]
    old_sources = {r.get("source") or "unknown" for r in existing}
    new_sources = {r.get("source") or "unknown" for r in incoming}
    if old_sources != new_sources:
        raise PriceBasisChanged("adjusted provider changed; refresh the complete history")
    if not overlap:
        raise PriceBasisChanged("no overlapping dates to verify adjusted price basis")
    for row in overlap:
        previous = old[str(row["date"])[:10]]
        for key in ("open", "high", "low", "close"):
            a, b = float(previous[key]), float(row[key])
            if abs(a - b) > max(0.011, abs(a) * 0.001):
                raise PriceBasisChanged("historical adjustment changed; refresh the complete history")
