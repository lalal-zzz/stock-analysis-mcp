"""
build/cleanup.py — 数据库清理 (表规模报告 + VACUUM + 重建进度表过期步骤清理)
"""

from __future__ import annotations

import os
import sqlite3

from ..storage import get_sector_db, get_stock_db, query_stock_db


def cleanup_database(dry_run: bool = False, vacuum: bool = True) -> int:
    """清理: 报告表规模 + 可选 VACUUM + 清理重建进度表过期步骤"""
    removed = 0
    for db_path, name in ((get_stock_db(), "stock"), (get_sector_db(), "sector")):
        if not os.path.exists(db_path):
            continue
        size_mb = os.path.getsize(db_path) / 1024 / 1024
        print(f"[{name}] {db_path} ({size_mb:.1f} MB)")
        with sqlite3.connect(db_path) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            for (t,) in tables:
                try:
                    cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    print(f"    {t}: {cnt} rows")
                except Exception:
                    pass
        if vacuum and not dry_run:
            with sqlite3.connect(db_path) as conn:
                conn.execute("VACUUM")
            print(f"[{name}] VACUUM done")

    # 清理重建进度表中除 kline/guba 外的历史步骤 (断点续传只关心这两步)
    if not dry_run:
        for step in ("spot", "combined", "indicators", "sector-kline", "sector-indicators"):
            query_stock_db("DELETE FROM rebuild_progress WHERE step=?", (step,))
        print("rebuild_progress 已清理历史步骤 (仅保留 kline/guba)")
    return removed
