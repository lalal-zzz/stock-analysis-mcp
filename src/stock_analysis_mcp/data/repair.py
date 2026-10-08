"""Read-only audits and resumable, archived full-history repairs."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..core.config import get_settings
from ..core.constants import INDICATOR_VERSION
from .quality import latest_completed_trade_day, cached_trade_dates


def audit_data(symbols=None) -> dict:
    """No migrations, directory creation or network calls during audit."""
    path = get_settings().stock_dir / "stock_data.db"
    if not path.exists():
        return {"stock_db": str(path), "symbols": 0, "issues": ["database_missing"]}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "stock_basic" not in tables:
            return {"stock_db": str(path), "symbols": 0, "issues": ["stock_basic_missing"],
                    "full_market_complete": False}
        universe = [r[0] for r in conn.execute("SELECT symbol FROM stock_basic ORDER BY symbol")]
        selected = set(symbols or universe)
        histories = []
        for table in ("stock_kline", "stock_kline_variants"):
            if table not in tables:
                continue
            columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            adjust = "adjust_type" if "adjust_type" in columns else "'qfq' AS adjust_type"
            source = "source" if "source" in columns else "'legacy' AS source"
            histories.append(f"SELECT symbol,{adjust},date,{source} FROM {table}")
        history_sql = " UNION ALL ".join(histories)
        stats = [dict(r) for r in conn.execute(f"""SELECT symbol,adjust_type,COUNT(*) AS bars,
            MIN(date) AS first_date,MAX(date) AS last_date,COUNT(DISTINCT source) AS sources
            FROM ({history_sql}) GROUP BY symbol,adjust_type""") if r["symbol"] in selected] if histories else []
    expected = latest_completed_trade_day()
    indexed = {(r["symbol"], r["adjust_type"]): r for r in stats}
    jobs = []
    for symbol in sorted(selected):
        for adjust in ("qfq", "hfq", ""):
            row = indexed.get((symbol, adjust))
            reasons = []
            if not row:
                reasons.append("missing_adjustment")
            else:
                if row["bars"] < 320:
                    reasons.append("short_history_or_new_listing")
                if row["sources"] > 1:
                    reasons.append("mixed_provider")
                if expected and row["last_date"] < expected:
                    reasons.append("stale_or_inactive")
            jobs.append({"symbol": symbol, "adjust_type": adjust, "issues": reasons,
                         "history": row, "history_completeness": "not_verified"})
    return {"stock_db": str(path), "symbols": len(selected), "expected_trade_date": expected,
            "adjustments": {a or "raw": sum(1 for r in stats if r["adjust_type"] == a)
                            for a in ("qfq", "hfq", "")},
            "jobs": jobs, "full_market_complete": False,
            "warnings": ["短历史和交易日缺口须区分新股、停牌、退市；条数不能证明上市以来完整性"]}


def _state_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS history_repair_state (
        symbol TEXT,adjust_type TEXT,status TEXT,last_date TEXT,indicator_version TEXT,
        last_error TEXT,next_retry_at TEXT,updated_at TEXT,
        PRIMARY KEY(symbol,adjust_type))""")


def repair_history(symbol: str, adjust: str) -> dict:
    from .sources import fetch_complete_history
    from .storage.writer import replace_stock_history
    from .storage import save_stock_indicators, save_data_coverage
    from .indicators import indicator_rows_from_df
    import pandas as pd
    rows = fetch_complete_history(symbol, adjust)
    if not rows:
        raise RuntimeError("requested adjustment history unavailable; original retained")
    batch = replace_stock_history(symbol, adjust, rows, reason="full-history consistency repair")
    save_data_coverage("stock_indicators", symbol, adjust_type=adjust, status="rebuilding")
    indicators = indicator_rows_from_df(pd.DataFrame(rows), index_cols=("symbol", "date", "adjust_type"))
    save_stock_indicators(indicators)
    expected = latest_completed_trade_day()
    status = "ready" if expected and rows[-1]["date"] >= expected and len(rows) >= 260 else "partial"
    for kind in ("stock_kline", "stock_indicators"):
        save_data_coverage(kind, symbol, adjust_type=adjust, first_date=rows[0]["date"],
            last_date=rows[-1]["date"], row_count=len(rows), source=rows[0]["source"], status=status)
    calendar = cached_trade_dates()
    dates = {r["date"] for r in rows}
    gaps = sorted(d for d in calendar if rows[0]["date"] <= d <= rows[-1]["date"] and d not in dates)
    return {"symbol": symbol, "adjust_type": adjust, "status": "repaired",
            "bars": len(rows), "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
            "source": rows[0]["source"], "archive_batch": batch,
            "unexplained_trade_dates": gaps, "needs_inactive_review": bool(gaps or status != "ready")}


def repair_data(*, symbols=None, adjustments=("qfq", "hfq", ""), dry_run=False,
                max_jobs: int | None = None, output: str | None = None,
                with_sectors: bool = False) -> dict:
    if dry_run:
        report = audit_data(symbols)
        report["dry_run"] = True
        return report
    from .sources import load_trade_dates
    from .storage.paths import get_stock_db
    from .storage.schema import _write_conn
    load_trade_dates()
    expected = latest_completed_trade_day()
    if not expected:
        raise RuntimeError("trading calendar does not cover today; cannot certify a repair")
    from .storage import query_stock_db
    universe = symbols or [r["symbol"] for r in query_stock_db("SELECT symbol FROM stock_basic ORDER BY symbol")]
    from .network import normalize_symbol
    universe = list(dict.fromkeys(normalize_symbol(s) for s in universe))
    with _write_conn(get_stock_db()) as conn:
        _state_table(conn)
        saved = {(r["symbol"], r["adjust_type"]): dict(r) for r in conn.execute("SELECT * FROM history_repair_state")}
    jobs = []
    now = datetime.now().isoformat()
    resumed = 0
    deferred = 0
    for symbol in universe:
        for adjust in adjustments:
            state = saved.get((symbol, adjust), {})
            if (state.get("status") == "repaired" and state.get("last_date", "") == expected
                    and state.get("indicator_version") == INDICATOR_VERSION):
                resumed += 1
                continue
            if state.get("next_retry_at") and state["next_retry_at"] > now:
                deferred += 1
                continue
            jobs.append((symbol, adjust))
    due = len(jobs)
    if max_jobs is not None:
        if max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        jobs = jobs[:max_jobs]
    report = {"expected_trade_date": expected, "scheduled": len(jobs), "results": [],
              "full_market_complete": False, "status": "running", "resumed": resumed,
              "remaining": due - len(jobs), "retry_deferred": deferred,
              "universe_symbols": len(universe), "adjustments": list(adjustments)}
    path = Path(output) if output else get_settings().stock_dir / "reports" / "data_repair.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    def checkpoint(full=False):
        temporary = path.with_suffix(".tmp")
        snapshot = {**report, "processed": len(report["results"])}
        if not full:
            snapshot["results"] = report["results"][-100:]
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    checkpoint()
    for index, (symbol, adjust) in enumerate(jobs, 1):
        retry = None
        try:
            result = repair_history(symbol, adjust)
        except Exception as exc:
            retry = (datetime.now() + timedelta(minutes=10)).isoformat()
            result = {"symbol": symbol, "adjust_type": adjust, "status": "failed", "error": str(exc)}
        with _write_conn(get_stock_db()) as conn:
            conn.execute("INSERT OR REPLACE INTO history_repair_state VALUES(?,?,?,?,?,?,?,?)",
                (symbol, adjust, result["status"], result.get("last_date"), INDICATOR_VERSION,
                 result.get("error"), retry, datetime.now().isoformat()))
        report["results"].append(result)
        with path.with_suffix(".jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(result, ensure_ascii=False) + "\n")
        if index % 10 == 0 or result["status"] == "failed":
            checkpoint()
        print(f"[{index}/{len(jobs)}] {symbol} {adjust or 'raw'} {result['status']} {result.get('bars', '')}", flush=True)
        time.sleep(0.3)
    if with_sectors:
        from .sector_repair import repair_sectors
        report["sectors"] = repair_sectors()
    report["status"] = "partial" if (report["remaining"] or deferred or
        report.get("sectors", {}).get("status") == "partial" or any(
            r["status"] == "failed" or r.get("needs_inactive_review") for r in report["results"])) else "repaired_selected"
    report["report_path"] = str(path)
    checkpoint(full=True)
    return report
