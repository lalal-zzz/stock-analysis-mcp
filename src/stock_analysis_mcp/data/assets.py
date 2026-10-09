"""Bounded multi-asset queries. Never store fund/bond data in stock tables."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .information import _cached, _request
from .network import fetch_em_kline

ASSET_TYPES = ("index", "etf", "lof", "fund", "convertible_bond", "bond")
MARKETS = ("sh", "sz", "csi", "otc")
CLIST = "https://push2delay.eastmoney.com/api/qt/clist/get"
SINA_BONDS = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
FUND_LIST = "https://fund.eastmoney.com/js/fundcode_search.js"
FUND_NAV = "https://api.fund.eastmoney.com/f10/lsjz"
FILTERS = {
    "index": "m:1+t:1,m:0+t:5,m:2",
    "etf": "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
    "lof": "b:MK0404,b:MK0405,b:MK0406,b:MK0407",
    "convertible_bond": "b:MK0354",
}
FIELDS = {
    "f2": "latest_price", "f3": "change_pct", "f4": "change",
    "f5": "volume", "f6": "amount", "f7": "amplitude_pct",
    "f8": "turnover_pct", "f15": "high", "f16": "low",
    "f17": "open", "f18": "previous_close", "f20": "market_cap",
    "f21": "float_market_cap", "f38": "shares", "f402": "discount_pct",
    "f441": "iopv", "f227": "pure_bond_value", "f229": "stock_price",
    "f230": "stock_change_pct", "f235": "conversion_price",
    "f236": "conversion_value", "f237": "conversion_premium_pct",
    "f238": "pure_bond_premium_pct", "f239": "put_trigger_price",
    "f240": "redemption_trigger_price", "f241": "conversion_price_floor",
}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def identity(symbol, asset_type, market=None):
    """Explicit type/market prevent 000001 stock/index/fund collisions."""
    if asset_type not in ASSET_TYPES:
        raise ValueError(f"asset_type must be one of {ASSET_TYPES}")
    match = re.fullmatch(r"(?:(sh|sz|csi|otc))?(\d{6})", str(symbol).strip().lower())
    if not match:
        raise ValueError("代码必须为六位数字，可带 sh/sz/csi/otc 前缀")
    prefix, code = match.groups()
    if market is not None and market not in MARKETS:
        raise ValueError(f"market must be one of {MARKETS}")
    if prefix and market and prefix != market:
        raise ValueError("代码前缀与 market 不一致")
    market = market or prefix
    if asset_type == "fund":
        market = market or "otc"
        if market != "otc":
            raise ValueError("fund 使用 otc；交易所基金请使用 etf/lof")
    elif market is None:
        raise ValueError("交易所标的必须明确 market 或提供市场前缀，不能按股票规则猜测")
    if asset_type != "fund" and market == "otc":
        raise ValueError("此品种不支持 otc 市场")
    if asset_type != "index" and market == "csi":
        raise ValueError("csi 仅用于指数")
    return {"id": f"{market}:{asset_type}:{code}", "symbol": code,
            "market": market, "asset_type": asset_type}


def _result(rows, meta, **extra):
    warnings = list(extra.pop("warnings", []))
    if meta.get("stale"):
        warnings.append("使用过期缓存，不能作为当前报价")
    if meta.get("status") == "unavailable":
        warnings.append(meta.get("error", "数据源不可用"))
    return {"status": "partial" if warnings else "ok", "records": rows,
            "source": meta, "warnings": warnings, **extra}


def _source_url(asset_type):
    return FUND_LIST if asset_type == "fund" else SINA_BONDS if asset_type == "bond" else CLIST


def _date(value):
    if not value or str(value) in {"-", "0"}:
        return None
    text = str(value)
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _em_row(row, asset_type):
    market = {0: "sz", 1: "sh", 2: "csi"}.get(row.get("f13"))
    if not market or not re.fullmatch(r"\d{6}", str(row.get("f12", ""))):
        raise ValueError("行情响应缺少有效代码/市场")
    record = {**identity(row["f12"], asset_type, market), "name": row.get("f14")}
    record.update({name: number(row.get(field)) for field, name in FIELDS.items()
                   if field in row})
    stamp = number(row.get("f124"))
    record["quote_time"] = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp and stamp > 0 else None
    if asset_type == "convertible_bond":
        record.update(stock_symbol=row.get("f232"), stock_name=row.get("f234"),
                      conversion_start_date=_date(row.get("f242")), subscription_date=_date(row.get("f243")))
    return record


def _load_page(asset_type, page, page_size):
    if asset_type in FILTERS:
        payload = _request(CLIST, params={"pn": page, "pz": page_size, "po": 0,
            "np": 1, "fltt": 2, "invt": 2, "fid": "f12", "fs": FILTERS[asset_type],
            "fields": ",".join([*FIELDS, "f12", "f13", "f14", "f124", "f232", "f234", "f242", "f243"])})
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("diff"), (list, dict)):
            raise ValueError("行情列表响应无效，未缓存失败结果")
        records = data["diff"]
        records = list(records.values()) if isinstance(records, dict) else records
        total = int(data["total"])
        if total > (page - 1) * page_size and not records:
            raise ValueError("行情列表返回意外空页")
        return [{"rows": [_em_row(row, asset_type) for row in records], "total": total}]
    if asset_type == "fund":
        text = _request(FUND_LIST, text=True).lstrip("\ufeff")
        match = re.fullmatch(r"\s*var\s+r\s*=\s*(\[.*\])\s*;?\s*", text, re.S)
        if not match:
            raise ValueError("基金列表响应格式已改变")
        items = json.loads(match[1])
        if not isinstance(items, list) or not items:
            raise ValueError("基金列表为空或格式错误")
        # Fund directory includes listed funds as well; classification is disclosed.
        return [{"rows": [{**identity(row[0], "fund"), "name": row[2], "fund_type": row[3]}
                           for row in items], "total": len(items)}]
    text = _request(SINA_BONDS, text=True, params={"page": page, "num": page_size,
        "sort": "symbol", "asc": 1, "node": "hs_z"},
        extra_headers={"Referer": "https://finance.sina.com.cn"})
    items = json.loads(text)
    if not isinstance(items, list):
        raise ValueError("新浪债券列表响应无效")
    rows = []
    for item in items:
        # hs_z may include BJ private convertibles. Only SH/SZ are supported here.
        if not str(item.get("symbol", "")).startswith(("sh", "sz")):
            continue
        record = {**identity(item["symbol"], "bond"), "name": item.get("name"),
                  "quote_time": item.get("ticktime"), "quote_date": None}
        record.update({name: number(item.get(key)) for key, name in {
            "trade": "latest_price", "pricechange": "change", "changepercent": "change_pct",
            "settlement": "previous_close", "open": "open", "high": "high", "low": "low",
            "volume": "volume", "amount": "amount"}.items()})
        rows.append(record)
    return [{"rows": rows, "total": None, "raw_count": len(items)}]


def _page(asset_type, page, page_size, offline=False):
    # Fund JS is a single directory; don't download/cache it separately for every page.
    key = f"assets:v1:list:{asset_type}:" + ("all" if asset_type == "fund" else f"{page}:{page_size}")
    data, meta = _cached(key, lambda: _load_page(asset_type, page, page_size),
                         offline=offline, ttl=86400 if asset_type == "fund" else 300)
    meta.update(provider="sina" if asset_type == "bond" else "eastmoney", url=_source_url(asset_type))
    packet = data[0] if data else {"rows": [], "total": None}
    if asset_type == "fund":
        packet = {**packet, "rows": packet["rows"][(page - 1) * page_size:page * page_size]}
    return packet, meta


def list_instruments(asset_type, page=1, page_size=100, offline=False):
    if asset_type not in ASSET_TYPES:
        raise ValueError("unsupported asset_type")
    if not 1 <= page <= 1000 or not 1 <= page_size <= 100:
        raise ValueError("page/page_size out of range")
    packet, meta = _page(asset_type, page, page_size, offline)
    total = packet["total"]
    warnings = []
    if asset_type in FILTERS:
        warnings.append("东方财富延迟行情；以 quote_time 判断数据时点，缓存时间不是交易时间")
    if asset_type == "bond":
        warnings.append("新浪沪深交易所债券集合可能包含可转债；不覆盖银行间债券，报价仅有时分秒，日期未知")
        if packet.get("raw_count", 0) > len(packet["rows"]):
            warnings.append("此源页包含不支持的市场，已过滤；返回条数可能小于 page_size，仍按原始页判断 has_more")
    if asset_type == "fund":
        warnings.append("基金目录包含场内基金；otc 标识用于净值查询，不代表所有基金都可场外申购")
    return _result(packet["rows"], meta, page=page, page_size=page_size, total=total,
                   has_more=None if meta.get("status") == "unavailable" else
                       (page * page_size < total) if total is not None else packet.get("raw_count", 0) == page_size,
                   coverage="single_page", raw_page_count=packet.get("raw_count", len(packet["rows"])), warnings=warnings)


def _find(instrument, offline=False):
    sources = []
    for page in range(1, 51):
        packet, meta = _page(instrument["asset_type"], page, 100, offline)
        sources.append(meta)
        if meta["status"] == "unavailable":
            break
        for row in packet["rows"]:
            if row["id"] == instrument["id"]:
                return row, meta
        total = packet["total"]
        if total is not None and page * 100 >= total:
            break
        if total is None and packet.get("raw_count", 0) < 100:
            break
    return None, {"status": "unavailable", "source": "asset_directory",
                  "error": "未在最多50页的品种目录中确认标的（可能离线缺页、源不可用或代码/品种错误）",
                  "pages_checked": len(sources)}


def get_quote(symbol, asset_type, market=None, offline=False):
    instrument = identity(symbol, asset_type, market)
    if asset_type == "fund":
        raise ValueError("场外基金请调用 get_fund_nav，不提供交易所报价")
    row, meta = _find(instrument, offline)
    warnings = []
    if asset_type in FILTERS:
        warnings.append("东方财富延迟行情；以 quote_time 判断数据时点，缓存时间不是交易时间")
    if asset_type == "bond":
        warnings.append("交易所债券集合可能包含可转债；价格不是到期收益率，报价日期未知")
    if asset_type in {"etf", "lof"}:
        warnings.append("IOPV 是盘中参考估值；discount_pct 保留数据源折价率口径，不能替代正式净值")
    return _result([row] if row else [], meta, instrument=instrument, warnings=warnings)


def _range(start_date, end_date):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    end_date = end_date or today.isoformat()
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end or end > today:
        raise ValueError("日期区间无效或结束日期晚于今天")
    return start.isoformat(), end.isoformat()


def get_kline(symbol, asset_type, market=None, start_date="2020-01-01", end_date=None,
              period="daily", limit=320, offline=False):
    instrument = identity(symbol, asset_type, market)
    if asset_type == "fund":
        raise ValueError("场外基金没有交易所K线，请调用 get_fund_nav")
    if asset_type == "bond":
        raise ValueError("普通交易所债券目前仅支持列表和报价，尚未接入可靠历史K线源")
    if period not in {"daily", "weekly", "monthly"} or not 1 <= limit <= 2000:
        raise ValueError("period/limit 无效")
    start, end = _range(start_date, end_date)
    directory_source = {}
    def load():
        row, meta = _find(instrument)
        directory_source.update(meta)
        if row is None or meta.get("stale"):
            raise ValueError("无法用新鲜品种目录确认标的")
        params = {"secid": f"{ {'sz': 0, 'sh': 1, 'csi': 2}[instrument['market']]}.{instrument['symbol']}",
                  "klt": {"daily": 101, "weekly": 102, "monthly": 103}[period],
                  "fqt": 0, "beg": start.replace("-", ""), "end": end.replace("-", ""),
                  "lmt": limit, "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"}
        payload = fetch_em_kline(params, timeout=10)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("klines"), list):
            raise ValueError("K线不可用，未缓存失败结果")
        if str(data.get("code")) != instrument["symbol"]:
            raise ValueError("K线返回代码与请求不一致")
        rows = []
        for line in data["klines"]:
            values = line.split(",")
            if len(values) != 11:
                raise ValueError("K线列数已改变")
            day = date.fromisoformat(values[0]).isoformat()
            if start <= day <= end:
                row = dict(zip(["open", "close", "high", "low", "volume", "amount", "amplitude_pct", "change_pct", "change", "turnover_pct"], map(number, values[1:])))
                if any(row[k] is None for k in ("open", "close", "high", "low")):
                    raise ValueError("K线价格缺失")
                rows.append({"date": day, **row})
        if not rows:
            raise ValueError("请求区间没有可用K线")
        return sorted(rows, key=lambda row: row["date"])[-limit:]
    rows, meta = _cached(f"assets:v1:kline:{instrument['id']}:{start}:{end}:{period}:{limit}:unadjusted",
                         load, offline=offline, ttl=3600)
    meta.update(provider="eastmoney", url="https://push2his.eastmoney.com/api/qt/stock/kline/get")
    return _result(rows, meta, instrument=instrument, period=period, adjustment="unadjusted",
                   as_of=rows[-1]["date"] if rows else None, directory_source=directory_source,
                   warnings=["仅返回指定区间最多 limit 根未复权K线；不代表全历史或全市场覆盖。成交量保留数据源单位。"])


def get_nav(symbol, start_date="2020-01-01", end_date=None, page=1, page_size=100, offline=False):
    instrument = identity(symbol, "fund")
    start, end = _range(start_date, end_date)
    if not 1 <= page <= 1000 or not 1 <= page_size <= 100:
        raise ValueError("page/page_size out of range")
    def load():
        payload = _request(FUND_NAV, params={"fundCode": instrument["symbol"], "pageIndex": page,
            "pageSize": page_size, "startDate": start, "endDate": end},
            extra_headers={"Referer": f"https://fundf10.eastmoney.com/jjjz_{instrument['symbol']}.html"})
        data = payload.get("Data")
        if not isinstance(data, dict) or not isinstance(data.get("LSJZList"), list):
            raise ValueError("基金净值响应无效")
        # Money fund DWJZ/LJJZ represent income/yield, not unit/accumulated NAV.
        if str(data.get("FundType")) == "005" or any(
                word in str(data.get("SYType") or "") for word in ("每万份", "年化", "收益")):
            raise ValueError("货币基金返回的是收益指标，不能标记为单位/累计净值；货币基金指标尚未接入")
        total = int(payload["TotalCount"])
        rows = []
        for item in data["LSJZList"]:
            day = date.fromisoformat(item["FSRQ"]).isoformat()
            if not start <= day <= end:
                raise ValueError("净值返回日期超出请求范围")
            rows.append({"date": day, "unit_nav": number(item.get("DWJZ")),
                         "accumulated_nav": number(item.get("LJJZ")), "daily_change_pct": number(item.get("JZZZL")),
                         "subscription_status": item.get("SGZT"), "redemption_status": item.get("SHZT"),
                         "distribution_note": item.get("FHSP"), "nav_type": item.get("NAVTYPE")})
        if total > (page - 1) * page_size and not rows:
            raise ValueError("基金净值返回意外空页")
        return [{"rows": sorted(rows, key=lambda row: row["date"]), "total": total}]
    saved, meta = _cached(f"assets:v1:nav:{instrument['id']}:{start}:{end}:{page}:{page_size}",
                          load, offline=offline, ttl=3600)
    meta.update(provider="eastmoney", url=FUND_NAV)
    packet = saved[0] if saved else {"rows": [], "total": None}
    rows, total = packet["rows"], packet["total"]
    return _result(rows, meta, instrument=instrument, page=page, page_size=page_size, total=total,
                   has_more=page * page_size < total if total is not None else None,
                   as_of=rows[-1]["date"] if rows else None,
                   warnings=["净值按发布日期更新，非盘中报价；目录可能包含场内基金。货币基金收益指标、持仓、费率和基金经理尚未接入。"])


def get_convertible(symbol, market=None, offline=False):
    result = get_quote(symbol, "convertible_bond", market, offline)
    result["warnings"].append("强赎触发价只是条款参考，不代表已公告强赎；未接入强赎公告、评级、付息日与到期收益率。")
    result["status"] = "partial"
    return result
