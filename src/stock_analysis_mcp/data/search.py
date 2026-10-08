"""
本地 + 网络搜索模块

优先从本地 SQLite 查询，数据不足时回退到网络 API。
"""

from .storage import (
    query_stock_db,
    query_sector_db,
    get_meta_stock,
    get_meta_sector,
    get_db_paths,
)


# ════════════════════════════════════════
# 股票本地搜索
# ════════════════════════════════════════

def search_stock_local(keyword: str, limit: int = 50) -> list[dict]:
    """本地搜索股票(代码+名称模糊匹配)"""
    kw = f"%{keyword.strip()}%"
    rows = query_stock_db(
        """SELECT b.symbol, b.name,
                   s.latest_price, s.change_pct, s.volume, s.amount,
                   s.turnover_rate, s.volume_ratio, s.pe_dynamic, s.pb,
                   s.total_market_cap, s.float_market_cap
            FROM stock_basic b
            LEFT JOIN stock_spot s ON b.symbol = s.symbol
            WHERE b.symbol LIKE ? OR b.name LIKE ?
            LIMIT ?""",
        (kw, kw, limit),
    )
    return rows


def _chunks(values: list, size: int = 900):
    """SQLite 变量上限 999, 大 IN 列表分块查询"""
    for i in range(0, len(values), size):
        yield values[i:i + size]


def get_stock_spot_batch(symbols: list[str]) -> list[dict]:
    """批量获取股票实时行情"""
    if not symbols:
        return []
    rows: list[dict] = []
    for chunk in _chunks(symbols):
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(query_stock_db(
            f"""SELECT * FROM stock_spot WHERE symbol IN ({placeholders})""",
            tuple(chunk),
        ))
    return rows


def get_stock_kline_local(symbol: str, limit: int = 250, adjust: str = "qfq") -> list[dict]:
    """从本地读取股票K线"""
    normalized_adjust = adjust or ""
    if normalized_adjust == "qfq":
        rows = query_stock_db(
            "SELECT * FROM stock_kline WHERE symbol=? AND adjust_type='qfq' ORDER BY date DESC LIMIT ?",
            (symbol, limit))
    else:
        rows = query_stock_db(
            "SELECT * FROM stock_kline_variants WHERE symbol=? AND adjust_type=? ORDER BY date DESC LIMIT ?",
            (symbol, normalized_adjust, limit))
        if not rows:  # fresh v2 databases keep every adjustment in the main table
            rows = query_stock_db(
                "SELECT * FROM stock_kline WHERE symbol=? AND adjust_type=? ORDER BY date DESC LIMIT ?",
                (symbol, normalized_adjust, limit))
    return list(reversed(rows))


def get_stock_rank_history(symbol: str = None, rank_date: str = None, limit: int = 100) -> list[dict]:
    """查询历史人气排名"""
    conditions = []
    params = []
    if symbol:
        conditions.append("symbol=?")
        params.append(symbol)
    if rank_date:
        conditions.append("rank_date=?")
        params.append(rank_date)
    where = " AND ".join(conditions) if conditions else "1=1"
    return query_stock_db(
        f"SELECT * FROM stock_rank WHERE {where} ORDER BY rank_date DESC, popularity_rank ASC LIMIT ?",
        tuple(params + [limit]),
    )


def get_rank_trend(symbol: str, days: int = 30) -> list[dict]:
    """获取单只股票的人气排名趋势"""
    return query_stock_db(
        """SELECT rank_date, popularity_rank, change_pct, latest_price
           FROM stock_rank WHERE symbol=? ORDER BY rank_date DESC LIMIT ?""",
        (symbol, days),
    )


# ════════════════════════════════════════
# 板块本地搜索
# ════════════════════════════════════════

def search_sector_local(keyword: str, limit: int = 50) -> list[dict]:
    """本地搜索板块(代码+名称模糊匹配)"""
    kw = f"%{keyword.strip()}%"
    rows = query_sector_db(
        """SELECT sector_code, sector_name, sector_type,
                   latest_index, change_pct, main_net_inflow, main_net_pct,
                   super_large_net, large_net, medium_net, small_net,
                   lead_stock_name, lead_stock_code
            FROM sector_basic
            WHERE sector_code LIKE ? OR sector_name LIKE ?
            ORDER BY change_pct DESC
            LIMIT ?""",
        (kw, kw, limit),
    )
    return rows


def get_sector_kline_local(sector_code: str, limit: int = 250) -> list[dict]:
    """从本地读取板块K线"""
    rows = query_sector_db(
        """SELECT * FROM sector_kline WHERE sector_code=? ORDER BY trade_date DESC LIMIT ?""",
        (sector_code, limit),
    )
    return list(reversed(rows))


def get_sector_members_local(sector_code: str) -> list[dict]:
    """获取板块成分股(含资金流向)"""
    return query_sector_db(
        """SELECT * FROM sector_member WHERE sector_code=? ORDER BY change_pct DESC""",
        (sector_code,),
    )


def get_sectors_by_stock(stock_code: str) -> list[dict]:
    """查询某只股票属于哪些板块"""
    return query_sector_db(
        """SELECT m.sector_code, b.sector_name, b.sector_type,
                   b.change_pct as sector_change_pct,
                   b.main_net_inflow, b.main_net_pct, b.updated_date,
                   m.latest_price, m.change_pct, m.turnover_rate, m.volume_ratio
                   ,(SELECT COUNT(*) FROM sector_kline k
                     WHERE k.sector_code=m.sector_code) AS kline_bars
                   ,(SELECT MIN(trade_date) FROM sector_kline k
                     WHERE k.sector_code=m.sector_code) AS kline_first_date
                   ,(SELECT MAX(trade_date) FROM sector_kline k
                     WHERE k.sector_code=m.sector_code) AS kline_last_date
                   ,(SELECT COUNT(*) FROM sector_member sm
                     WHERE sm.sector_code=m.sector_code) AS member_count
            FROM sector_member m
            LEFT JOIN sector_basic b ON m.sector_code = b.sector_code
            WHERE m.stock_code=?""",
        (stock_code,),
    )


def get_top_sectors(sort_by: str = "change_pct", sector_type: str = None, limit: int = 20) -> list[dict]:
    """获取涨幅/资金流入最强的板块"""
    valid_cols = {"change_pct", "main_net_inflow", "large_net", "latest_index"}
    col = sort_by if sort_by in valid_cols else "change_pct"
    conditions = []
    params = []
    if sector_type:
        conditions.append("sector_type=?")
        params.append(sector_type)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return query_sector_db(
        f"""SELECT * FROM sector_basic {where} ORDER BY {col} DESC LIMIT ?""",
        tuple(params + [limit]),
    )


# ════════════════════════════════════════
# 板块 → 股票流程
# ════════════════════════════════════════

async def get_sector_to_stocks_flow(
    sector_code: str,
    member_limit: int = 50,
    sort_by: str = "change_pct",
) -> dict:
    """
    从板块出发获取成分股全面信息(行情/资金/排名)

    优先本地 DB, 无数据时回退网络。
    """
    # 板块信息
    sector_info = query_sector_db(
        "SELECT * FROM sector_basic WHERE sector_code=?", (sector_code,)
    )
    sector = sector_info[0] if sector_info else None

    # 成分股
    members = get_sector_members_local(sector_code)

    # 本地无数据则回退网络
    if not members:
        from ..tools.sector_data import get_sector_members as fetch_members
        try:
            members = await fetch_members(sector_code)
        except Exception:
            return {"error": f"无法获取板块 {sector_code} 的成分股数据"}

    # 排序
    valid_sort = {"change_pct", "turnover_rate", "volume_ratio", "latest_price", "volume"}
    sort_key = sort_by if sort_by in valid_sort else "change_pct"
    if members:
        members.sort(key=lambda x: x.get(sort_key) or -9999, reverse=True)
    top_members = members[:member_limit]

    # 获取每只成分股的人气排名(取库内最新一期, 避免假定"今天"有数据)
    stock_codes = [m.get("stock_code", "") for m in top_members if m.get("stock_code")]
    rank_map = {}
    if stock_codes:
        rank_rows = []
        for chunk in _chunks(stock_codes):
            placeholders = ",".join("?" for _ in chunk)
            rank_rows.extend(query_stock_db(
                f"""SELECT symbol, popularity_rank FROM stock_rank
                    WHERE symbol IN ({placeholders})
                      AND rank_date = (SELECT MAX(rank_date) FROM stock_rank)""",
                tuple(chunk),
            ))
        rank_map = {r["symbol"]: r["popularity_rank"] for r in rank_rows}

    enriched = []
    for m in top_members:
        code = m.get("stock_code", "")
        enriched.append({
            "stock_code": code,
            "stock_name": m.get("stock_name"),
            "latest_price": m.get("latest_price"),
            "change_pct": m.get("change_pct"),
            "change_amount": m.get("change_amount"),
            "volume": m.get("volume"),
            "turnover": m.get("turnover"),
            "amplitude": m.get("amplitude"),
            "turnover_rate": m.get("turnover_rate"),
            "volume_ratio": m.get("volume_ratio"),
            "high": m.get("high"),
            "low": m.get("low"),
            "open_today": m.get("open_today"),
            "close_yesterday": m.get("close_yesterday"),
            "pb": m.get("pb"),
            "pe_dynamic": m.get("pe_dynamic"),
            "popularity_rank": rank_map.get(code),
        })

    return {
        "sector_code": sector_code,
        "sector_name": sector["sector_name"] if sector else sector_code,
        "sector_change_pct": sector.get("change_pct") if sector else None,
        "sector_main_net_inflow": sector.get("main_net_inflow") if sector else None,
        "sector_main_net_pct": sector.get("main_net_pct") if sector else None,
        "member_count": len(members),
        "members": enriched,
    }


# ════════════════════════════════════════
# 多条件选股(本地)
# ════════════════════════════════════════

# 支持的筛选字段及其 SQL 列名
_SCREEN_COLUMNS = {
    "min_price": ("s.latest_price >= ?", float),
    "max_price": ("s.latest_price <= ?", float),
    "min_change_pct": ("s.change_pct >= ?", float),
    "max_change_pct": ("s.change_pct <= ?", float),
    "min_volume_ratio": ("s.volume_ratio >= ?", float),
    "min_turnover_rate": ("s.turnover_rate >= ?", float),
    "max_turnover_rate": ("s.turnover_rate <= ?", float),
    "min_pe": ("s.pe_dynamic >= ?", float),
    "max_pe": ("s.pe_dynamic <= ?", float),
    "min_pb": ("s.pb >= ?", float),
    "max_pb": ("s.pb <= ?", float),
    "min_market_cap": ("s.total_market_cap >= ?", float),
    "max_market_cap": ("s.total_market_cap <= ?", float),
    "min_float_market_cap": ("s.float_market_cap >= ?", float),
    "min_sixty_day_change": ("s.sixty_day_change >= ?", float),
    "min_ytd_change": ("s.ytd_change >= ?", float),
    "max_amplitude": ("s.amplitude <= ?", float),
    "min_amplitude": ("s.amplitude >= ?", float),
}

_VALID_SORT_COLS = {
    "change_pct", "volume_ratio", "turnover_rate", "latest_price",
    "volume", "amount", "pe_dynamic", "pb", "total_market_cap",
    "amplitude", "sixty_day_change", "ytd_change", "popularity_rank",
}


def screen_stocks_local(
    conditions: dict = None,
    top_n: int = 50,
    sort_by: str = "change_pct",
    sector_code: str = None,
    name_keyword: str = None,
) -> list[dict]:
    """
    多条件本地选股(纯 stock_spot 表查询 + 可选人气排名 + 可选板块/名称过滤)。

    conditions 支持的字段:
        min_price, max_price          — 价格区间
        min_change_pct, max_change_pct
        min_volume_ratio
        min_turnover_rate, max_turnover_rate
        min_pe, max_pe
        min_pb, max_pb
        min_market_cap, max_market_cap — 总市值区间(亿)
        min_float_market_cap          — 最小流通市值(亿)
        min_sixty_day_change
        min_ytd_change
        min_amplitude, max_amplitude
    sector_code: 限定板块
    name_keyword: 股票名称关键词
    sort_by: 默认 change_pct
    """
    if conditions is None:
        conditions = {}

    clauses = []
    params = []

    for key, val in conditions.items():
        if key not in _SCREEN_COLUMNS:
            continue
        if val is None:
            continue
        sql_frag, _cast = _SCREEN_COLUMNS[key]
        clauses.append(sql_frag)
        params.append(val)

    if name_keyword:
        clauses.append("(s.name LIKE ? OR s.symbol LIKE ?)")
        kw = f"%{name_keyword.strip()}%"
        params.append(kw)
        params.append(kw)

    where_sql = " AND ".join(clauses) if clauses else "1=1"

    # 板块过滤: 先查 sector_db 获取成分股代码, 再拼入 IN 子句
    if sector_code:
        from .storage import query_sector_db
        member_rows = query_sector_db(
            "SELECT stock_code FROM sector_member WHERE sector_code=?",
            (sector_code,),
        )
        if not member_rows:
            return []
        member_codes = [m["stock_code"] for m in member_rows]

    sort_col = sort_by if sort_by in _VALID_SORT_COLS else "change_pct"
    if sort_col == "popularity_rank":
        # NULL(未上榜)置底, 否则 SQLite 升序时 NULL 行会排在最前面
        order_clause = "r.popularity_rank IS NULL, r.popularity_rank ASC"
    else:
        order_clause = f"s.{sort_col} DESC"

    sql = f"""
        SELECT s.symbol, s.name,
               s.latest_price, s.change_pct, s.volume_ratio,
               s.turnover_rate, s.pe_dynamic, s.pb,
               s.total_market_cap, s.float_market_cap,
               s.volume, s.amount, s.amplitude,
               s.sixty_day_change, s.ytd_change,
               r.popularity_rank
        FROM stock_spot s
        LEFT JOIN stock_rank r ON s.symbol = r.symbol
             AND r.rank_date = (SELECT MAX(rank_date) FROM stock_rank)
        WHERE {{where_sql}}
        ORDER BY {order_clause}
        LIMIT ?
    """

    codes = member_codes if sector_code else None
    if codes and len(codes) > 900:
        # 大板块: 分块执行避开 SQLite 999 变量上限, 内存重排还原全局排序
        rows: list[dict] = []
        for chunk in _chunks(codes):
            chunk_in = ",".join("?" for _ in chunk)
            chunk_params = params + chunk + [top_n]
            rows.extend(query_stock_db(
                sql.format(where_sql=f"{where_sql} AND s.symbol IN ({chunk_in})"), tuple(chunk_params)))
        if sort_col == "popularity_rank":
            rows.sort(key=lambda r: (r["popularity_rank"] is None,
                                     r["popularity_rank"] if r["popularity_rank"] is not None else 0))
        else:
            rows.sort(key=lambda r: r[sort_col] if r[sort_col] is not None else float("-inf"),
                      reverse=True)
        return rows[:top_n]

    if codes:
        where_sql += f" AND s.symbol IN ({','.join('?' for _ in codes)})"

    all_params = params + (codes or []) + [top_n]
    return query_stock_db(sql.format(where_sql=where_sql), tuple(all_params))


# ════════════════════════════════════════
# 数据库状态查询
# ════════════════════════════════════════

def get_db_status() -> dict:
    from .quality import latest_completed_trade_day
    from ..core.constants import INDICATOR_VERSION
    expected = latest_completed_trade_day()
    basic = query_stock_db("SELECT COUNT(*) AS n FROM stock_basic")[0]["n"]
    coverage = query_stock_db(
        """SELECT COUNT(DISTINCT CASE WHEN row_count>=260 THEN symbol END) AS symbols,
                  SUM(CASE WHEN status='ready' AND row_count>=260 THEN 1 ELSE 0 END) AS ready,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                  MAX(last_date) AS latest_date
           FROM data_coverage WHERE data_type='stock_kline' AND period='daily' AND adjust_type='qfq'""")
    cov = coverage[0] if coverage else {}
    covered = int(cov.get("symbols") or 0)
    fresh = query_stock_db("""SELECT COUNT(*) AS n FROM data_coverage
        WHERE data_type='stock_kline' AND period='daily' AND adjust_type='qfq'
        AND status='ready' AND row_count>=260 AND last_date=?""", (expected or "9999-12-31",))[0]["n"]
    has_verification = query_stock_db("SELECT 1 FROM sqlite_master WHERE name='history_repair_state'")
    verified = query_stock_db("""SELECT COUNT(*) AS n FROM history_repair_state h
        JOIN data_coverage c ON c.symbol=h.symbol AND c.adjust_type=h.adjust_type
        WHERE h.adjust_type='qfq' AND h.status='repaired' AND h.last_date=?
        AND c.data_type='stock_kline' AND c.status='ready' AND c.row_count>=260""",
        (expected or "9999-12-31",))[0]["n"] if has_verification else 0
    sector_count = query_sector_db("SELECT COUNT(*) AS n FROM sector_basic")[0]["n"]
    sector_ready = query_sector_db("""SELECT COUNT(*) AS n FROM (
        SELECT sector_code FROM sector_kline GROUP BY sector_code
        HAVING COUNT(*)>=260 AND MAX(trade_date)>=?)""", (expected or "9999-12-31",))[0]["n"]
    sector_indicators = query_sector_db("""SELECT COUNT(DISTINCT sector_code) AS n
        FROM sector_indicators WHERE trade_date>=? AND indicator_version=?""",
        (expected or "9999-12-31", INDICATOR_VERSION))[0]["n"]
    from ..alerts.storage import list_alert_events
    return {
        "stock": {
            "count": get_meta_stock("stock_count") or str(basic),
            "spot_updated": get_meta_stock("spot_updated"),
            "rank_updated": get_meta_stock("rank_updated"),
            "kline_coverage": {
                "symbols": covered,
                "ready": int(cov.get("ready") or 0),
                "failed": int(cov.get("failed") or 0),
                "latest_date": cov.get("latest_date"),
                "coverage_ratio": round(covered / basic, 4) if basic else 0.0,
                "fresh_symbols": fresh,
                "fresh_coverage_ratio": round(fresh / basic, 4) if basic else 0.0,
                "expected_trade_date": expected,
                "verified_basis_symbols": verified,
                "full_market_ready": bool(expected and basic and verified / basic >= 0.95),
                "full_history_complete": False,
            },
        },
        "sector": {
            "kline_coverage": {"ready": sector_ready, "total": sector_count,
                "coverage_ratio": sector_ready / sector_count if sector_count else 0.0},
            "indicator_coverage": {"ready": sector_indicators, "version": INDICATOR_VERSION},
            "count": get_meta_sector("sector_count"),
            "sector_updated": get_meta_sector("sector_updated"),
            "kline_updated": get_meta_sector("kline_updated"),
            "member_updated": get_meta_sector("member_updated"),
        },
        "paths": {
            "stock_db": get_db_paths()["stock_db"],
            "sector_db": get_db_paths()["sector_db"],
        },
        "warnings": [] if expected else ["交易日历未确认；历史条数覆盖率不代表最新数据覆盖率"],
        "alerts": {"recent_events": list_alert_events(limit=10)},
    }
