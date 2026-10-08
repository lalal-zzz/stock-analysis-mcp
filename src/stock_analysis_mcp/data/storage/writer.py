"""
data/storage/writer.py — 行级写入 (INSERT OR REPLACE) 通用模板与各表 save_* 函数
"""

import json
import zlib
from itertools import groupby
from datetime import datetime

from ...core.constants import INDICATOR_VERSION, PATTERN_ENGINE_VERSION
from .paths import get_sector_db, get_stock_db
from .schema import _get_conn, _write_conn


def _save_rows(db_path_fn, table: str, cols: list[str], rows: list[dict]) -> int:
    """save_* 通用模板: 列补齐 -> INSERT OR REPLACE -> 提交 (锁内, 连接 finally 关闭)"""
    if not rows:
        return 0
    rows_padded = [{c: r.get(c) for c in cols} for r in rows]
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    with _write_conn(db_path_fn()) as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})",
            rows_padded,
        )
    return len(rows)


def _table_has_adjust_pk(table: str) -> bool:
    conn = _get_conn(get_stock_db())
    try:
        pk = [r[1] for r in sorted(conn.execute(f"PRAGMA table_info({table})"), key=lambda r: r[5]) if r[5]]
        return pk == ["symbol", "date", "adjust_type"]
    finally:
        conn.close()


# ── Stock DB 写入 ──

_STOCK_KLINE_COLS = ["symbol", "date", "adjust_type", "source", "fetched_at",
                     "open", "high", "low", "close", "volume", "amount", "amplitude",
                     "change_pct", "change_amount", "turnover_rate"]

_STOCK_INDICATOR_COLS = ["symbol", "date", "adjust_type", "indicator_version",
                         "MA5", "MA10", "MA20", "MA30", "MA60", "MA100", "MA120", "MA200", "MA250",
                         "RSI6", "RSI14", "RSI24",
                         "DIF", "DEA", "MACD",
                         "BOLL_UPPER", "BOLL_MIDDLE", "BOLL_LOWER",
                         "KDJ_K", "KDJ_D", "KDJ_J",
                         "VOL_MA5", "VOL_MA10", "VOL_MA20", "VOL_RATIO5", "VOL_RATIO20",
                         "ATR14", "ATR_PCT", "BIAS20", "BIAS60", "BIAS250",
                         "RETURN_5", "RETURN_10", "RETURN_20", "RETURN_60",
                         "HIGH_20", "HIGH_60", "HIGH_120", "LOW_20", "LOW_60", "LOW_120"]


def save_stock_basic(rows: list[dict]):
    return _save_rows(get_stock_db, "stock_basic", ["symbol", "name", "raw_symbol"], rows)


def save_stock_kline(rows: list[dict]):
    now = datetime.now().isoformat()
    payload = [{**r, "adjust_type": r["adjust_type"] if "adjust_type" in r and r["adjust_type"] is not None else "qfq",
                "source": r.get("source") or "unknown",
                "fetched_at": r.get("fetched_at") or now} for r in rows]
    from ..quality import check_price_basis, validate_prices
    with _write_conn(get_stock_db()) as conn:
        for (symbol, adjust), group in groupby(sorted(payload, key=lambda r: (r["symbol"], r["adjust_type"])),
                                              key=lambda r: (r["symbol"], r["adjust_type"])):
            batch = list(group)
            if all(r["source"] != "unknown" for r in batch):
                validate_prices(batch, adjust)
                check_price_basis(_history(conn, symbol, adjust), batch, adjust)
            _insert_klines(conn, batch)
    return len(payload)


def _history(conn, symbol, adjust):
    # Explicit columns support legacy schemas with a different column order.
    columns = ','.join(_STOCK_KLINE_COLS)
    return [dict(r) for r in conn.execute(f"""SELECT {columns} FROM stock_kline WHERE symbol=? AND adjust_type=?
        UNION ALL SELECT {columns} FROM stock_kline_variants WHERE symbol=? AND adjust_type=? ORDER BY date""",
        (symbol, adjust, symbol, adjust))]


def _insert_klines(conn, rows):
    pk = [r[1] for r in sorted(conn.execute("PRAGMA table_info(stock_kline)"), key=lambda r: r[5]) if r[5]]
    modern = pk == ["symbol", "date", "adjust_type"]
    for table, batch in (("stock_kline", [r for r in rows if modern or r["adjust_type"] == "qfq"]),
                         ("stock_kline_variants", [r for r in rows if not modern and r["adjust_type"] != "qfq"])):
        if batch:
            conn.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(_STOCK_KLINE_COLS)}) "
                f"VALUES ({','.join(':'+c for c in _STOCK_KLINE_COLS)})",
                [{c: r.get(c) for c in _STOCK_KLINE_COLS} for r in batch])


def replace_stock_history(symbol: str, adjust: str, rows: list[dict], *, reason: str) -> str:
    """Archive and atomically replace one complete price history, never the database."""
    from ..quality import validate_prices
    validate_prices(rows, adjust)
    if not rows or any(r.get("symbol") != symbol for r in rows):
        raise ValueError("replacement must contain one nonempty symbol")
    if len({r.get("source") for r in rows}) != 1:
        raise ValueError("complete history must use one provider")
    now = datetime.now().isoformat()
    batch_id = f"{symbol}:{adjust}:{now}"
    payload = [{**r, "adjust_type": adjust, "fetched_at": now} for r in rows]
    with _write_conn(get_stock_db()) as conn:
        existing = _history(conn, symbol, adjust)
        if existing and min(r["date"] for r in rows) > min(r["date"] for r in existing):
            raise ValueError("replacement would truncate existing history; preserved original data")
        conn.execute("""CREATE TABLE IF NOT EXISTS kline_repair_archive (
            batch_id TEXT PRIMARY KEY,symbol TEXT,adjust_type TEXT,reason TEXT,
            created_at TEXT,history_zlib BLOB)""")
        conn.execute("INSERT INTO kline_repair_archive VALUES(?,?,?,?,?,?)", (batch_id, symbol,
            adjust, reason, now, zlib.compress(json.dumps(existing, default=str).encode())))
        for table in ("stock_kline", "stock_kline_variants", "stock_indicators", "stock_indicator_variants"):
            conn.execute(f"DELETE FROM {table} WHERE symbol=? AND adjust_type=?", (symbol, adjust))
        conn.execute("DELETE FROM pattern_signals WHERE universe='stocks' AND symbol=?", (symbol,))
        conn.execute("DELETE FROM structure_snapshots WHERE universe='stocks' AND symbol=?", (symbol,))
        conn.execute("UPDATE data_coverage SET status='partial',last_error='history replaced; requires revalidation' WHERE symbol=? AND adjust_type=?", (symbol, adjust))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_repair_state'").fetchone():
            conn.execute("DELETE FROM history_repair_state WHERE symbol=? AND adjust_type=?", (symbol, adjust))
        _insert_klines(conn, payload)
    return batch_id


def save_stock_spot(rows: list[dict]):
    return _save_rows(get_stock_db, "stock_spot", ["symbol", "name", "latest_price", "change_pct", "change_amount",
        "volume", "amount", "amplitude", "high", "low", "open", "pre_close",
        "volume_ratio", "turnover_rate", "pe_dynamic", "pb",
        "total_market_cap", "float_market_cap", "speed", "sixty_day_change",
        "ytd_change", "updated_date"], rows)


def save_stock_rank(rows: list[dict]):
    return _save_rows(get_stock_db, "stock_rank", ["symbol", "name", "rank_date", "latest_price", "change_pct",
        "volume_ratio", "high", "low", "pre_close",
        "volume", "amount", "turnover_rate", "popularity_rank"], rows)


def save_stock_indicators(rows: list[dict]):
    payload = [{**r, "adjust_type": r["adjust_type"] if "adjust_type" in r and r["adjust_type"] is not None else "qfq",
                "indicator_version": r.get("indicator_version") or INDICATOR_VERSION} for r in rows]
    if _table_has_adjust_pk("stock_indicators"):
        return _save_rows(get_stock_db, "stock_indicators", _STOCK_INDICATOR_COLS, payload)
    primary = [r for r in payload if r["adjust_type"] == "qfq"]
    variants = [r for r in payload if r["adjust_type"] != "qfq"]
    return (_save_rows(get_stock_db, "stock_indicators", _STOCK_INDICATOR_COLS, primary)
            + _save_rows(get_stock_db, "stock_indicator_variants", _STOCK_INDICATOR_COLS, variants))


def save_data_coverage(data_type: str, symbol: str, *, period: str = "daily",
                       adjust_type: str = "", first_date: str | None = None,
                       last_date: str | None = None, row_count: int = 0,
                       source: str | None = None, status: str = "partial",
                       last_error: str | None = None, data_version: str | None = None) -> None:
    _save_rows(get_stock_db, "data_coverage", [
        "data_type", "symbol", "period", "adjust_type", "first_date", "last_date",
        "row_count", "source", "status", "last_error", "data_version", "updated_at",
    ], [{"data_type": data_type, "symbol": symbol, "period": period,
         "adjust_type": adjust_type, "first_date": first_date, "last_date": last_date,
         "row_count": row_count, "source": source, "status": status,
         "last_error": last_error, "data_version": data_version,
         "updated_at": datetime.now().isoformat()}])


def save_pattern_signals(rows: list[dict], universe: str = "stocks") -> int:
    now = datetime.now().isoformat()
    payload = []
    for row in rows:
        payload.append({
            "universe": row.get("universe") or universe, "symbol": row["symbol"],
            "signal_date": row.get("signal_date") or row.get("date"), "pattern": row["pattern"],
            "stage": row.get("stage") or "candidate", "score": row.get("score"),
            "confirmed": int(bool(row.get("confirmed"))), "trend_state": row.get("trend_state"),
            "entry_status": row.get("entry_status"), "support": row.get("support"),
            "resistance": row.get("resistance"), "neckline": row.get("neckline"),
            "invalid_level": row.get("invalid_level"),
            "fib_levels_json": json.dumps(row.get("fib_levels") or {}, ensure_ascii=False),
            "evidence_json": json.dumps(row.get("evidence") or [], ensure_ascii=False),
            "factors_json": json.dumps(row.get("factors") or {}, ensure_ascii=False),
            "engine_version": PATTERN_ENGINE_VERSION, "calculated_at": now,
        })
    return _save_rows(get_stock_db, "pattern_signals", [
        "universe", "symbol", "signal_date", "pattern", "stage", "score", "confirmed",
        "trend_state", "entry_status", "support", "resistance", "neckline", "invalid_level",
        "fib_levels_json", "evidence_json", "factors_json", "engine_version", "calculated_at",
    ], payload)


# ── Sector DB 写入 ──

def save_sector_basic(rows: list[dict]):
    return _save_rows(get_sector_db, "sector_basic", ["sector_code", "sector_name", "sector_type", "latest_index", "change_pct",
        "main_net_inflow", "main_net_pct",
        "super_large_net", "super_large_pct", "large_net", "large_pct",
        "medium_net", "medium_pct", "small_net", "small_pct",
        "lead_stock_name", "lead_stock_code", "updated_date"], rows)


def save_sector_kline(rows: list[dict]):
    now = datetime.now().isoformat()
    payload = [{**r, "source": r.get("source") or "eastmoney",
                "fetched_at": r.get("fetched_at") or now} for r in rows]
    return _save_rows(get_sector_db, "sector_kline", ["sector_code", "trade_date", "open", "close", "high", "low",
        "volume", "turnover", "amplitude", "change_pct", "change_amount", "turnover_rate",
        "source", "fetched_at"], payload)


def save_sector_member(rows: list[dict]):
    return _save_rows(get_sector_db, "sector_member", ["sector_code", "stock_code", "stock_name",
        "latest_price", "change_pct", "change_amount",
        "volume", "turnover", "amplitude", "turnover_rate", "volume_ratio",
        "high", "low", "open_today", "close_yesterday", "pb", "pe_dynamic", "updated_date"], rows)


def save_sector_indicators(rows: list[dict]):
    payload = [{**r, "indicator_version": INDICATOR_VERSION} for r in rows]
    return _save_rows(get_sector_db, "sector_indicators", ["sector_code", "trade_date", "indicator_version",
        "MA120", "MA250", "VOL_MA20", "VOL_RATIO5", "VOL_RATIO20", "ATR_PCT",
        "BIAS20", "BIAS60", "BIAS250", "RETURN_5", "RETURN_10", "RETURN_20", "RETURN_60",
        "HIGH_20", "HIGH_60", "HIGH_120", "LOW_20", "LOW_60", "LOW_120",
        "MA5", "MA10", "MA20", "MA30", "MA60", "MA100", "MA200",
        "RSI6", "RSI14", "RSI24",
        "DIF", "DEA", "MACD",
        "BOLL_UPPER", "BOLL_MIDDLE", "BOLL_LOWER",
        "KDJ_K", "KDJ_D", "KDJ_J",
        "VOL_MA5", "VOL_MA10",
        "ATR14"], payload)
