"""Persistent sector retry queue; run again after circuit-breaker cooldown."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..core.constants import INDICATOR_VERSION
from .quality import latest_completed_trade_day
from .storage import query_sector_db, save_sector_kline, save_sector_indicators
from .storage.paths import get_sector_db
from .storage.schema import _write_conn


def repair_sectors() -> dict:
    from .build.helpers import _sector_kline_full
    from .indicators import indicator_rows_from_df
    import pandas as pd
    expected = latest_completed_trade_day()
    if not expected:
        raise RuntimeError("calendar required for sector repair")
    with _write_conn(get_sector_db()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sector_retry_queue (
            sector_code TEXT PRIMARY KEY,status TEXT,last_error TEXT,next_retry_at TEXT,updated_at TEXT)""")
        queued = {r["sector_code"]: dict(r) for r in conn.execute("SELECT * FROM sector_retry_queue")}
    sectors = query_sector_db("SELECT sector_code,sector_name FROM sector_basic ORDER BY sector_code")
    results = []
    deferred = 0
    now = datetime.now().isoformat()
    for sector in sectors:
        code = sector["sector_code"]
        state = queued.get(code, {})
        if state.get("next_retry_at") and state["next_retry_at"] > now:
            deferred += 1
            continue
        error, retry = None, None
        try:
            rows = _sector_kline_full(code, sector["sector_name"], 5000)
            if not rows:
                raise RuntimeError("Eastmoney unavailable; retry after cooldown")
            save_sector_kline(rows)
            local = query_sector_db("SELECT *,trade_date AS date FROM sector_kline WHERE sector_code=? ORDER BY trade_date", (code,))
            indicators = indicator_rows_from_df(pd.DataFrame(local), index_cols=("sector_code", "trade_date"))
            save_sector_indicators(indicators)
            status = "ready" if len(local) >= 260 and local[-1]["trade_date"] == expected else "partial"
        except Exception as exc:
            status, error = "failed", str(exc)
            retry = (datetime.now() + timedelta(minutes=10)).isoformat()
        with _write_conn(get_sector_db()) as conn:
            conn.execute("INSERT OR REPLACE INTO sector_retry_queue VALUES(?,?,?,?,?)",
                         (code, status, error, retry, datetime.now().isoformat()))
        results.append({"sector_code": code, "status": status, "error": error, "next_retry_at": retry})
        print(f"sector {code} {status}", flush=True)
    return {"indicator_version": INDICATOR_VERSION, "results": results,
            "total": len(sectors), "retry_deferred": deferred,
            "status": "partial" if not sectors or deferred or any(
                r["status"] != "ready" for r in results) else "ok"}
