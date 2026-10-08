"""Explicit rollback of one archived history repair."""
import json
import zlib
from datetime import datetime

from .storage.paths import get_stock_db
from .storage.schema import _write_conn
from .storage.writer import _history, _insert_klines


def restore_history(batch_id: str) -> dict:
    with _write_conn(get_stock_db()) as conn:
        record = conn.execute("SELECT * FROM kline_repair_archive WHERE batch_id=?", (batch_id,)).fetchone()
        if not record:
            raise ValueError("archive batch not found")
        symbol, adjust = record["symbol"], record["adjust_type"]
        original = json.loads(zlib.decompress(record["history_zlib"]))
        current = _history(conn, symbol, adjust)
        now = datetime.now().isoformat()
        undo = f"{symbol}:{adjust}:restore:{now}"
        conn.execute("INSERT INTO kline_repair_archive VALUES(?,?,?,?,?,?)", (undo, symbol, adjust,
            f"rollback of {batch_id}", now, zlib.compress(json.dumps(current, default=str).encode())))
        for table in ("stock_kline", "stock_kline_variants", "stock_indicators", "stock_indicator_variants"):
            conn.execute(f"DELETE FROM {table} WHERE symbol=? AND adjust_type=?", (symbol, adjust))
        _insert_klines(conn, original)
        conn.execute("DELETE FROM pattern_signals WHERE universe='stocks' AND symbol=?", (symbol,))
        conn.execute("DELETE FROM structure_snapshots WHERE universe='stocks' AND symbol=?", (symbol,))
        conn.execute("UPDATE data_coverage SET status='partial',last_error='history restored; indicators require recalculation' WHERE symbol=? AND adjust_type=?", (symbol, adjust))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='history_repair_state'").fetchone():
            conn.execute("DELETE FROM history_repair_state WHERE symbol=? AND adjust_type=?", (symbol, adjust))
    return {"symbol":symbol,"adjust_type":adjust,"rows":len(original),"undo_batch":undo,
            "status":"restored","indicators":"invalidated"}
