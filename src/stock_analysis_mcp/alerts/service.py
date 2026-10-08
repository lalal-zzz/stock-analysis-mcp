"""CLI polling and atomic evaluation of explicitly configured raw-price zones."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .evaluator import evaluate_zone
from .models import AlertState, PriceUpdate, ZoneRule
from ..data.storage.schema import _write_conn
from ..data.storage.paths import get_stock_db


def evaluate_and_store(rule: ZoneRule, update: PriceUpdate) -> list[dict]:
    with _write_conn(get_stock_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM alert_state WHERE zone_id=?", (rule.zone_id,)).fetchone()
        state = None
        if row:
            state = AlertState(zone_id=row["zone_id"], episode=row["episode"], relation=row["relation"],
                emitted=set(json.loads(row["emitted_json"])), invalidated=bool(row["invalidated"]),
                last_timestamp=row["last_timestamp"])
            if state.last_timestamp and update.timestamp < state.last_timestamp:
                return []
        state, events = evaluate_zone(rule, state, update)
        now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        conn.execute("INSERT OR REPLACE INTO alert_state VALUES(?,?,?,?,?,?,?,?,?)", (
            state.zone_id, rule.symbol, rule.timeframe, state.episode, state.relation,
            json.dumps(sorted(state.emitted)), int(state.invalidated), state.last_timestamp, now))
        for event in events:
            conn.execute("""INSERT OR IGNORE INTO alert_events
                (idempotency_key,zone_id,symbol,timeframe,episode,event_type,event_timestamp,
                 close,lower,upper,confirmed,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event.idempotency_key,event.zone_id,event.symbol,event.timeframe,event.episode,
                 event.event_type,event.timestamp,event.close,event.lower,event.upper,int(event.confirmed),now))
    return [asdict(event) for event in events]


def watch_zones(path: str, *, poll_seconds: float = 60, max_polls: int = 0) -> int:
    if poll_seconds < 5 or max_polls < 0:
        raise ValueError("poll_seconds >=5 and max_polls >=0 required")
    from ..tools.stock_data import get_stock_kline_period
    rules = [ZoneRule(**item) for item in json.loads(Path(path).read_text(encoding="utf-8"))]
    polls = 0
    while not max_polls or polls < max_polls:
        for rule in rules:
            period = {"daily":"101", "weekly":"102", "monthly":"103"}.get(rule.timeframe, rule.timeframe)
            rows = asyncio.run(get_stock_kline_period(rule.symbol, period=period, limit=2, adjust=""))
            if not rows:
                print(json.dumps({"symbol": rule.symbol, "error": "quote unavailable"}), flush=True)
                continue
            row = rows[-1]
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            stamp = str(row.get("datetime") or row.get("date"))
            parsed = None
            for fmt in ("%Y%m%d%H%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(stamp, fmt).replace(tzinfo=now.tzinfo)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise ValueError(f"unrecognized quote time: {stamp}")
            daily = int(period) >= 101
            from ..data.quality import latest_completed_trade_day, cached_trade_dates
            completed = latest_completed_trade_day()
            closed = bool(completed and parsed.date().isoformat() <= completed) if daily else (
                now - parsed).total_seconds() >= int(period) * 60
            if period in {"102", "103"} and closed:
                following = sorted(d for d in cached_trade_dates() if d > completed)
                if following:
                    next_day = datetime.strptime(following[0], "%Y-%m-%d").date()
                    current = parsed.date()
                    closed = (next_day.isocalendar()[:2] != current.isocalendar()[:2]) if period == "102" else (
                        (next_day.year, next_day.month) != (current.year, current.month))
                else:
                    closed = False
            update = PriceUpdate(timestamp=parsed.isoformat(), close=row["close"],
                                 high=row.get("high"), low=row.get("low"), closed=closed)
            for event in evaluate_and_store(rule, update):
                print(json.dumps(event, ensure_ascii=False), flush=True)
        polls += 1
        if not max_polls or polls < max_polls:
            time.sleep(poll_seconds)
    return 0
