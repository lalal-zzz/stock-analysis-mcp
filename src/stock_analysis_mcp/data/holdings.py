"""Bounded disclosed shareholder holdings; independent of price databases."""
from __future__ import annotations

import json
import re
from datetime import date

from . import information as info
from .financials import a_symbol, numeric

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
KINDS = ("all", "social_security", "pension", "fund", "insurance", "qfii", "broker", "trust", "other")
TABLES = {"top10": "RPT_DMSK_HOLDERS", "float_top10": "RPT_F10_EH_FREEHOLDERS"}


def classify(name, source_type):
    text = name + " " + source_type
    for kind, words in (
        ("pension", ("基本养老", "养老保险")),
        ("social_security", ("全国社保", "社保基金", "社会保障", "社保")),
        ("qfii", ("QFII", "合格境外")),
        ("insurance", ("保险", "人寿")),
        ("fund", ("基金",)),
        ("broker", ("证券", "券商")),
        ("trust", ("信托",)),
    ):
        if any(word in text for word in words):
            return kind
    return "other"


def _day(value):
    if not value:
        return None
    return date.fromisoformat(str(value)[:10]).isoformat()


def normalize(row, scope):
    symbol, _ = a_symbol(row.get("SECURITY_CODE", ""))
    name = info.clean(row.get("HOLDER_NAME"), limit=500)
    if not name or not row.get("END_DATE"):
        raise ValueError("股东明细缺少股东名称或报告期")
    source_type = info.clean(row.get("HOLDER_NEWTYPE") or row.get("HOLDER_TYPE"))
    return {"symbol": symbol, "stock_name": info.clean(row.get("SECURITY_NAME_ABBR")),
            "holder_name": name, "holder_code": row.get("HOLDER_CODE"),
            "source_holder_type": source_type, "category": classify(name, source_type),
            "classification_basis": "股东名称及来源类型关键词；other不等于机构",
            "scope": scope, "report_date": _day(row["END_DATE"]),
            "announcement_date": _day(row.get("NOTICE_DATE")),
            "source_update_date": _day(row.get("UPDATE_DATE")),
            "rank": numeric(row.get("RANK") if scope == "top10" else row.get("HOLDER_RANK")),
            "shares": numeric(row.get("HOLD_NUM")), "shares_unit": "股",
            "holding_ratio_pct": numeric(row.get("HOLD_RATIO")) if scope == "top10" else None,
            "float_holding_ratio_pct": numeric(row.get("FREE_HOLDNUM_RATIO")) if scope == "float_top10" else None,
            "shares_change": numeric(row.get("HOLD_NUM_CHANGE") if scope == "top10" else row.get("XZCHANGE")),
            "shares_change_pct": numeric(row.get("HOLD_RATIO_CHANGE") if scope == "top10" else row.get("CHANGE_RATIO")),
            "change_description": info.clean(row.get("HOLDNUM_CHANGE_NAME")),
            "source": "eastmoney", "source_url": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html"}


def query_holdings(symbol=None, holder_name=None, report_date=None, scope="float_top10",
                   category="all", page=1, page_size=100, offline=False, refresh=False):
    if scope not in TABLES or category not in KINDS:
        raise ValueError("无效的股东范围或类别")
    if type(page) is not int or not 1 <= page <= 100 or type(page_size) is not int or not 1 <= page_size <= 500:
        raise ValueError("page范围1–100，page_size范围1–500")
    filters = []
    if symbol is not None:
        symbol, _ = a_symbol(symbol)
        filters.append(f'(SECURITY_CODE="{symbol}")')
    if holder_name is not None:
        holder_name = holder_name.strip()
        if not re.fullmatch(r"[\w\u3400-\u9fff ·•—－\-（）()]{2,120}", holder_name):
            raise ValueError("股东名称需2–120字，不接受引号或查询表达式")
        filters.append(f'(HOLDER_NAME="{holder_name}")')
    if report_date is not None:
        report_date = date.fromisoformat(report_date).isoformat()
        if report_date[5:] not in {"03-31", "06-30", "09-30", "12-31"}:
            raise ValueError("报告期必须为季度末YYYY-MM-DD")
        filters.append(f"(END_DATE='{report_date}')")
    if symbol is None and holder_name is None and report_date is None:
        raise ValueError("全市场查询必须指定报告期；反查使用股东完整名称")
    params = {"reportName": TABLES[scope], "columns": "ALL", "source": "WEB", "client": "WEB",
              "filter": "".join(filters), "pageNumber": page, "pageSize": page_size,
              "sortColumns": "END_DATE,SECURITY_CODE," + ("RANK" if scope == "top10" else "HOLDER_RANK"),
              "sortTypes": "-1,1,1"}

    def load():
        payload = info._request(URL, params=params)
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("股东数据源不可用或结构变化；不能解释为无持仓")
        if payload.get("success") is False:
            raise ValueError("股东数据源报告失败")
        if not isinstance(result.get("pages"), int) or not isinstance(result.get("count"), int):
            raise ValueError("股东数据源缺少分页总数")
        rows = [normalize(row, scope) for row in result["data"]]
        if any((symbol and r["symbol"] != symbol) or
               (holder_name and r["holder_name"] != holder_name) or
               (report_date and r["report_date"] != report_date) for r in rows):
            raise ValueError("来源返回了不匹配的股票、股东或报告期")
        return [{"rows": rows, "total": result.get("count"), "pages": result.get("pages")}]

    key = "holdings:v1:" + json.dumps(params, sort_keys=True, ensure_ascii=False)
    saved, state = info._cached(key, load, offline=offline, ttl=0 if refresh else 86400)
    warnings = ["仅覆盖已披露十大股东或十大流通股东，不是全部机构持仓；未出现不代表未持有。",
                "报告期持仓不代表实时持仓；不同报告期、两个股东范围不得相加。",
                "类别按名称和来源类型识别；基金不保证为公募，证券公司名下基金产品需核验。",
                "category在当前来源页内过滤，翻页依据来源页数；不据此声称全市场完成。",
                "UPDATE_DATE仅作来源更新时间，公告日缺失保留空值；变化字段按来源口径保留。"]
    if state.get("stale"):
        warnings.append("返回旧缓存，来源刷新失败或缓存已过期，请查看cache状态和获取时间。")
    block = saved[0] if saved else {"rows": [], "total": None, "pages": None}
    rows = [r for r in block["rows"] if category == "all" or r["category"] == category]
    unavailable = not saved
    return {"status": "error" if unavailable else "ok", "error": state.get("error") if unavailable else None,
            "rows": rows, "cache": state, "scope": scope, "category": category,
            "page": page, "page_size": page_size, "source_total": block["total"],
            "source_pages": block["pages"], "source_page_rows": len(block["rows"]),
            "has_more": page < block["pages"] if isinstance(block["pages"], int) else None,
            "warnings": warnings}


def get_stock_holdings(symbol, **kwargs):
    return query_holdings(symbol=symbol, **kwargs)


def search_institution_holdings(**kwargs):
    return query_holdings(**kwargs)
