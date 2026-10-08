"""
build/helpers.py — 数据构建包内共享的小工具

- 日期/计数/标的清单等通用查询
- 全量历史K线翻页下载 (_fetch_full_history)
- 板块代码清单与板块K线网络取数
- 网络入口统一经包门面解析 (_net) —— 兼容测试对 build.fetch_* 的重定向
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta

from ..indicators import indicator_rows_from_df as _indicator_rows_from_df
from ..storage import query_sector_db, query_stock_db


def _pkg():
    """包门面模块 (测试通过 monkeypatch build.fetch_* 等重定向网络入口)。"""
    return sys.modules[__package__]


def _net(name: str, *args, **kwargs):
    """经包门面调用网络入口函数 (getattr 在调用时解析, 可被测试替换)。"""
    return getattr(_pkg(), name)(*args, **kwargs)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _table_count(db: str, table: str) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _get_symbols() -> list[str]:
    return [r["symbol"] for r in query_stock_db("SELECT symbol FROM stock_basic ORDER BY symbol")]


def _fetch_full_history(symbol: str, adjust: str = "qfq", limit: int = None) -> list[dict]:
    """全量历史K线: 腾讯单次最多 5000 根, 超过时按日期分段向前翻页"""
    if limit:
        return _net("fetch_kline_history", symbol, adjust, limit)
    from ..sources import fetch_complete_history
    return fetch_complete_history(symbol, adjust)


# ── 板块 ──


def _sector_codes() -> list[tuple[str, str | None]]:
    """板块代码列表 (优先本地 sector_basic, 无数据时网络拉取)"""
    rows = query_sector_db("SELECT sector_code, sector_name FROM sector_basic")
    if rows:
        return [(r["sector_code"], r["sector_name"]) for r in rows]
    import asyncio

    from ...tools.sector_data import get_sector_list

    items = asyncio.run(get_sector_list("concept")) + asyncio.run(get_sector_list("industry"))
    return [(i["sector_code"], i.get("sector_name")) for i in items]


def _sector_kline_full(code: str, name: str | None, limit: int) -> list[dict]:
    from ...tools.sector_data import _sector_kline_net_sync

    return _sector_kline_net_sync(code, limit=limit, klt=101, sector_name=name)
