"""
数据管理工具: 本地初始化、增量更新、本地搜索、板块↔股票流程
"""

from datetime import date, timedelta

from ..data.sync import (
    init_all_data,
    update_daily_all,
    download_stock_kline as sync_download_stock_kline,
)
from ..data.search import (
    search_stock_local as local_search_stock,
    get_stock_kline_local,
    get_rank_trend,
    get_sectors_by_stock,
    get_sector_members_local,
    get_sector_to_stocks_flow,
    get_db_status as _get_db_status,
    screen_stocks_local,
)
from ..tools.stock_data import search_stock


# ═══════════════════ 数据管理 ═══════════════════

async def init_full_data(include_sector_members: bool = True, quick: bool = False,
                         mode: str | None = None, workers: int = 8,
                         resume: bool = True) -> dict:
    return await init_all_data(include_sector_members=include_sector_members, quick=quick,
                               mode=mode, workers=workers, resume=resume)


async def update_daily_data(include_sector_members: bool = True,
                            stock_kline_mode: str = "tracked",
                            skip_non_trading_day: bool = True) -> dict:
    return await update_daily_all(include_sector_members=include_sector_members,
                                  stock_kline_mode=stock_kline_mode,
                                  skip_non_trading_day=skip_non_trading_day)


async def get_data_status() -> dict:
    return _get_db_status()


# ═══════════════════ 股票查询 ═══════════════════

async def search_stock_full(keyword: str, limit: int = 50) -> list[dict]:
    results = local_search_stock(keyword, limit)
    if results:
        return results
    return await search_stock(keyword)


async def get_kline_local_or_net(symbol: str, days: int = 300, adjust: str = "qfq") -> list[dict]:
    from ..data.network import normalize_symbol
    symbol = normalize_symbol(symbol)
    klines = get_stock_kline_local(symbol, days, adjust)
    from ..data.quality import latest_completed_trade_day
    import asyncio
    expected = await asyncio.to_thread(latest_completed_trade_day, allow_network=True)
    if klines:
        # 数据量达到请求的 8 成且最后一条在 7 天内才算可用, 否则重新下载
        enough = len(klines) >= days
        last_date = str(klines[-1].get("date", ""))[:10]
        fresh = bool(expected and last_date == expected)
        if enough and fresh:
            return klines
    # days 语义为交易日条数, 下载窗口按 1.55 倍换算为日历天数(覆盖节假日)
    result = await sync_download_stock_kline(symbol, days=int(days * 1.8), adjust=adjust)
    rows = get_stock_kline_local(symbol, days, adjust)
    if result.get("status") in {"error", "empty", "degraded"}:
        raise RuntimeError(f"K线更新失败，保留原数据: {result}")
    if not expected:
        raise RuntimeError("交易日历不可用，无法确认K线新鲜度；本地数据已保留")
    if not rows or str(rows[-1].get("date", ""))[:10] < expected:
        raise RuntimeError("K线未达到最新完成交易日；请检查停牌、退市或下载缺口")
    return rows


async def get_rank_trend_data(symbol: str, days: int = 30) -> list[dict]:
    return get_rank_trend(symbol, days)


# ═══════════════════ 板块查询 ═══════════════════

async def get_stock_belong_sectors(stock_code: str) -> list[dict]:
    return get_sectors_by_stock(stock_code)


async def get_sector_members_flow(sector_code: str, member_limit: int = 50, sort_by: str = "change_pct") -> dict:
    return await get_sector_to_stocks_flow(sector_code, member_limit, sort_by)


# ═══════════════════ 选股 ═══════════════════

async def screen_stocks(
    conditions: dict = None,
    top_n: int = 50,
    sort_by: str = "change_pct",
    sector_code: str = None,
    name_keyword: str = None,
) -> list[dict]:
    if sector_code:
        sector_code = await _ensure_sector_members(sector_code)
    return screen_stocks_local(
        conditions=conditions, top_n=top_n, sort_by=sort_by,
        sector_code=sector_code, name_keyword=name_keyword,
    )


async def _ensure_sector_members(sector_code: str) -> str:
    """
    本地无成分股数据时从网络下载并缓存(quick 初始化后的懒加载)。
    返回标准化后的板块代码(BKxxxx), 查询失败时原样返回, 由本地查询兜底。
    """
    from ..data.network import normalize_sector_code
    from ..data.storage import save_sector_member
    from ..tools.sector_data import get_sector_members as fetch_members

    try:
        code = normalize_sector_code(sector_code)
    except ValueError:
        return sector_code
    if get_sector_members_local(code):
        return code
    try:
        members = await fetch_members(code)
    except Exception:
        return code
    if members:
        today = date.today().isoformat()
        for m in members:
            m["updated_date"] = today
            m.setdefault("sector_code", code)
        save_sector_member(members)
    return code
