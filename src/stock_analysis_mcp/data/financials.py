"""Source-separated A-share financial downloads; no market-history side effects."""
from __future__ import annotations

import json
import re
from datetime import datetime

from . import information as info
from .network import normalize_symbol

PROVIDER_CAPABILITIES = {
    "eastmoney": {"indicators": "up to 20 report periods"},
    "sohu": {"indicators": "latest page, normally 5 periods", "balance": "latest page, normally 4 periods",
             "income": "latest page, normally 4 periods", "cashflow": "latest page, normally 4 periods"},
    "tencent": {"indicators": "latest report only", "revenue_segments": "recent product/region/sector composition"},
    "sina": {"balance": "up to 20 report periods", "income": "up to 20 report periods", "cashflow": "up to 20 report periods"},
}
REPORT_TYPES = ("indicators", "balance", "income", "cashflow", "revenue_segments")
SOHU_PAGES = {"indicators": "cwzb", "balance": "zcfz", "income": "lr", "cashflow": "xjll"}
SINA_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
TENCENT_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/stockinfo/jiankuang"


def a_symbol(symbol):
    code = normalize_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code) or not (
            code.startswith(("000", "001", "002", "003", "300", "301", "6", "4", "8", "920"))):
        raise ValueError("仅支持沪深北A股代码，不接受港美股、基金或B股代码")
    market = "sh" if code.startswith("6") else "bj" if code.startswith(("4", "8", "9")) else "sz"
    prefix = re.match(r"^(sh|sz|bj)", str(symbol).lower())
    if prefix and prefix[1] != market:
        raise ValueError("股票代码与沪深北市场前缀不一致")
    return code, market


def numeric(value, *, multiplier=1):
    text = str(value if value is not None else "").strip().replace(",", "").replace("，", "")
    if text in {"", "--", "-", "None", "null", "不适用"}:
        return None
    match = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)(亿元|万元|亿|万|元|%|倍)?", text)
    if not match:
        return None
    unit = match[2] or ""
    scale = 1e8 if unit in {"亿元", "亿"} else 1e4 if unit in {"万元", "万"} else multiplier
    # Percentage values are kept as percentage points, never divided by 100.
    result = info.number(match[1])
    return info.number(result * scale) if result is not None else None


def report_date(value):
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    match = re.fullmatch(r"(\d{4})年?(一季报|中报|半年报|三季报|年报)", text)
    if match:
        text = match[1] + {"一季报": "-03-31", "中报": "-06-30", "半年报": "-06-30",
                            "三季报": "-09-30", "年报": "-12-31"}[match[2]]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"无法确认财务报告期: {value}") from None


SOHU_INDICATORS = {
    "每股收益": "eps", "每股净资产": "book_value_per_share", "每股经营现金流": "operating_cash_flow_per_share",
    "净资产收益率": "roe_reported_pct", "销售毛利率": "gross_margin_pct", "净利润率": "net_margin_pct",
    "资产负债率": "debt_asset_pct", "流动比率": "current_ratio", "速动比率": "quick_ratio",
    "主营业务收入": "operating_revenue", "净利润": "reported_net_profit", "资产总计": "total_assets",
    "负债合计": "total_liabilities", "股东权益": "reported_equity",
}


def parse_sohu(body, symbol, kind, periods):
    table_class = "reportA" if kind == "indicators" else "tableP"
    table = re.search(r'<table\b[^>]*class=["\x27]' + table_class + r'["\x27][^>]*>(.*?)</table>', body, re.S | re.I)
    if not table:
        raise ValueError("搜狐财务表结构变更或当前证券无此报表")
    rows = []
    indexes = []
    section = ""
    money_scale = 1
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table[1], re.S | re.I):
        cells = [info.clean(c, limit=1000) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if not cells:
            continue
        if cells[0] == "报告期":
            indexes = [(i, report_date(c)) for i, c in enumerate(cells) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c)]
            rows = [{"symbol": symbol, "report_date": day, "published_at": None, "source": "sohu_financial",
                     "report_type": kind, "period_basis": "instant" if kind == "balance" else "year_to_date",
                     "currency": "CNY", "amount_unit": "CNY", "raw_items": [],
                     "url": f"https://q.stock.sohu.com/cn/{symbol}/{SOHU_PAGES[kind]}.shtml"} for _, day in indexes]
            continue
        if len(cells) == 1:
            section = cells[0]
            if kind == "indicators":
                money_scale = 10000 if "万元" in section else 1
            continue
        if not rows:
            continue
        label = cells[0]
        for row, (index, _) in zip(rows, indexes):
            raw = cells[index] if index < len(cells) else None
            value = numeric(raw, multiplier=money_scale if kind == "indicators" else 1)
            row["raw_items"].append({"section": section, "label": label, "raw_value": raw, "value": value,
                                     "source_unit": "万元" if money_scale == 10000 else "原表值"})
            if kind == "indicators" and label in SOHU_INDICATORS:
                row[SOHU_INDICATORS[label]] = value
            # Only compare same-labelled provider growth fields, not equity ROE definitions.
            if kind == "indicators" and label == "主营业务收入" and index == indexes[0][0] and len(cells) > 2:
                row["operating_revenue_yoy_pct"] = numeric(cells[2]) if "同比变化" not in cells[2] else None
    if not rows or not any(item.get("value") is not None for r in rows for item in r["raw_items"]):
        raise ValueError("搜狐没有可解析的报告期数据")
    return sorted(rows, key=lambda r: r["report_date"], reverse=True)[:periods]


def fetch_sohu(symbol, kind, periods):
    url = f"https://q.stock.sohu.com/cn/{symbol}/{SOHU_PAGES[kind]}.shtml"
    return parse_sohu(info._request(url, text=True, encoding="gbk", impersonate="edge99"), symbol, kind, periods)


def parse_tencent(payload, symbol, kind, periods):
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise ValueError("腾讯A股财务接口不可用或格式变更")
    data = payload["data"]
    url = f"https://gu.qq.com/{a_symbol(symbol)[1]}{symbol}/gp/cwzb"
    if kind == "indicators":
        summary = data.get("zyzb") or {}
        detail = summary.get("detail") or {}
        date = report_date(detail.get("date") or summary.get("date"))
        mapping = {"mgsy": "eps", "mgjzc": "book_value_per_share", "yyzsr": "revenue",
                   "jlr": "reported_net_profit", "jlrzzl": "profit_yoy_pct", "zsrzzl": "revenue_yoy_pct",
                   "jzcsyl": "roe_reported_pct", "zcfzl": "debt_asset_pct", "jzc": "reported_equity"}
        if not any(numeric(detail.get(key)) is not None for key in mapping):
            raise ValueError("腾讯财务摘要没有可解析指标")
        return [{"symbol": symbol, "report_date": date, "published_at": None, "source": "tencent_financial",
                 "report_type": kind, "period_basis": "year_to_date", "currency": "CNY", "amount_unit": "CNY",
                 "url": url, "field_definitions": {"revenue": "营业总收入", "reported_net_profit": "提供商净利润；归母口径未独立确认",
                    "roe_reported_pct": "提供商ROE；不默认为加权ROE"},
                 "raw_items": [{"label": key, "raw_value": detail.get(key), "value": numeric(detail.get(key))} for key in mapping],
                 **{field: numeric(detail.get(key)) for key, field in mapping.items()}}]
    rows = []
    for group in data.get("zysr") or []:
        segments = []
        for category in group.get("detail") or []:
            for item in category.get("detail") or []:
                segments.append({"dimension": category.get("type"), "name": info.clean(item.get("name")),
                    "revenue_cny": numeric(str(item.get("income") or "") + (item.get("unit") or "")),
                    "share_pct": numeric(item.get("zb")), "raw_income": item.get("income"), "raw_unit": item.get("unit")})
        rows.append({"symbol": symbol, "report_date": report_date(group.get("date")), "published_at": None,
                     "source": "tencent_financial", "report_type": kind, "period_basis": "year_to_date",
                     "currency": "CNY", "amount_unit": "CNY", "url": url, "segments": segments})
    if not rows:
        raise ValueError("腾讯当前证券无主营收入构成数据")
    return sorted(rows, key=lambda r: r["report_date"], reverse=True)[:periods]


def fetch_tencent(symbol, kind, periods):
    payload = info._request(TENCENT_URL, params={"code": a_symbol(symbol)[1] + symbol, "app": "official_website"})
    return parse_tencent(payload, symbol, kind, periods)


def parse_sina(payload, symbol, kind, periods):
    data = (payload.get("result") or {}).get("data") or {}
    reports = data.get("report_list") or {}
    rows = []
    for day, report in reports.items():
        currency = report.get("rCurrency")
        if currency != "CNY":
            raise ValueError(f"新浪币种不是CNY: {currency}")
        published = report.get("publish_date")
        rows.append({"symbol": symbol, "report_date": report_date(day),
            "published_date": report_date(published) if published else None,
            "source": "sina_financial", "report_type": kind, "currency": currency,
            "amount_unit": "CNY", "period_basis": "instant" if kind == "balance" else "year_to_date",
            "scope": report.get("rType"), "audit_status": report.get("is_audit"), "data_source": report.get("data_source"),
            "url": "https://vip.stock.finance.sina.com.cn/corp/go.php/vFD_FinanceSummary/stockid/" + symbol + ".phtml",
            "raw_items": [{"label": item.get("item_title"), "field": item.get("item_field"),
                           "raw_value": item.get("item_value"), "value": numeric(item.get("item_value"))}
                          for item in report.get("data") or []]})
    if not rows or not any(item.get("value") is not None for r in rows for item in r["raw_items"]):
        raise ValueError("新浪没有此A股财务报表或数据格式变更")
    return sorted(rows, key=lambda r: r["report_date"], reverse=True)[:periods]


def fetch_sina(symbol, kind, periods):
    payload = info._request(SINA_URL, params={"paperCode": a_symbol(symbol)[1] + symbol,
        "source": {"balance": "fzb", "income": "lrb", "cashflow": "llb"}[kind],
        "type": "0", "page": "1", "num": str(periods)}, extra_headers={"Referer": "https://finance.sina.com.cn"})
    return parse_sina(payload, symbol, kind, periods)


def fetch_provider(symbol, provider, kind, periods):
    if kind not in PROVIDER_CAPABILITIES[provider]:
        raise ValueError(f"{provider}不支持已验证的A股{kind}接口")
    if provider == "eastmoney":
        return [{**row, "report_type": "indicators", "currency": "CNY", "amount_unit": "CNY",
                 "field_definitions": {"revenue": "营业总收入", "parent_net_profit": "归母净利润", "roe_pct": "加权ROE"}}
                for row in info.fetch_financials(symbol, periods)]
    return {"sohu": fetch_sohu, "tencent": fetch_tencent, "sina": fetch_sina}[provider](symbol, kind, periods)


def summarize(datasets):
    summaries = []
    for dataset in datasets:
        for report in dataset["reports"]:
            metrics = {key: report.get(key) for key in ("eps", "book_value_per_share", "revenue", "operating_revenue",
                       "parent_net_profit", "reported_net_profit", "roe_pct", "roe_reported_pct", "debt_asset_pct")
                       if report.get(key) is not None}
            # Exact normalized labels only; preserve source net-profit ambiguities.
            values = {}
            for item in report.get("raw_items", []):
                label = re.sub(r"^[一二三四五六七八九十\d]+[、．.：:]", "", item.get("label") or "").strip()
                if item.get("value") is not None:
                    values.setdefault(label, item["value"])
            for labels, target in [(("资产总计", "资产总额"), "total_assets"), (("负债合计", "负债总额"), "total_liabilities"),
                    (("经营活动产生的现金流量净额",), "operating_cash_flow"), (("净利润",), "reported_net_profit")]:
                for label in labels:
                    if label in values:
                        metrics[target] = values[label]
                        break
            if metrics.get("total_assets") and "total_liabilities" in metrics:
                metrics["calculated_debt_asset_pct"] = metrics["total_liabilities"] / metrics["total_assets"] * 100
            summaries.append({"provider": dataset["provider"], "report_type": dataset["report_type"],
                              "report_date": report["report_date"], "metrics": metrics})
    # EPS and BPS are the only presently mapped cross-provider comparable fields.
    differences = []
    indexed = {}
    for summary in summaries:
        if summary["report_type"] != "indicators":
            continue
        for metric in ("eps", "book_value_per_share"):
            value = summary["metrics"].get(metric)
            if value is not None:
                indexed.setdefault((summary["report_date"], metric), []).append({"provider": summary["provider"], "value": value})
    for (day, metric), values in indexed.items():
        if len(values) > 1:
            differences.append({"report_date": day, "metric": metric, "values": values,
                "range": max(v["value"] for v in values) - min(v["value"] for v in values)})
    return {"by_source": summaries, "cross_source_differences": differences,
            "rule": "同报告期对照，不平均、不跨源拼接；ROE、主营/总收入和净利润口径不自动视作相同"}


def download_financials(symbol, provider="auto", report_type="indicators", periods=8, offline=False, refresh=False):
    symbol, market = a_symbol(symbol)
    if provider not in {"auto", "all", *PROVIDER_CAPABILITIES} or report_type not in {"all", *REPORT_TYPES} or not 1 <= periods <= 20:
        raise ValueError("invalid provider/report_type or periods outside 1..20")
    candidates = list(PROVIDER_CAPABILITIES) if provider in {"auto", "all"} else [provider]
    kinds = list(REPORT_TYPES) if report_type == "all" else [report_type]
    datasets, unavailable, warnings = [], [], []
    for kind in kinds:
        for source in candidates:
            if kind not in PROVIDER_CAPABILITIES[source]:
                if provider not in {"auto", "all"}:
                    unavailable.append({"provider": source, "report_type": kind, "status": "unsupported"})
                continue
            key = f"a-financial:v1:{source}:{symbol}:{kind}:{periods}"
            rows, state = info._cached(key, lambda s=source, k=kind: fetch_provider(symbol, s, k, periods),
                                       offline=offline, ttl=-1 if refresh and not offline else 86400)
            dataset = {"provider": source, "report_type": kind, "reports": rows, "source_status": state,
                       "requested_periods": periods, "available_periods": len(rows),
                       "capability": PROVIDER_CAPABILITIES[source][kind]}
            datasets.append(dataset)
            if len(rows) < periods:
                warnings.append(f"{source}/{kind}仅返回{len(rows)}期，请求{periods}期；不代表上市以来完整。")
            if provider == "auto" and rows and not state.get("stale"):
                break
    warnings.extend(["仅支持沪深北A股；提供商财务数据不是交易所原始公告。当前修订版不可直接用于历史时点回测。",
        "金额统一人民币元，原字段及单位保留；比率_pct按百分数返回。资产负债表是期末存量，利润/现金流通常是年初累计。",
        "腾讯已验证接口提供最新财务摘要和近期主营构成，不冒充历史三张报表；搜狐仅返回当前页面有限报告期。"])
    partial = bool(unavailable) or not datasets or any(not d["reports"] or d["source_status"].get("stale") or
                  d["source_status"]["status"] == "unavailable" or len(d["reports"]) < periods for d in datasets)
    return {"status": "partial" if partial else "ok", "symbol": symbol, "market": market.upper(),
            "provider": provider, "report_type": report_type, "datasets": datasets, "unavailable": unavailable,
            "capabilities": PROVIDER_CAPABILITIES, "analysis": summarize(datasets), "warnings": warnings}


def export_financials(result, output):
    """Export one explicit request as JSON plus source-labelled long-form CSV."""
    import csv
    from pathlib import Path
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = path.with_suffix(".csv")
    if path == csv_path:
        raise ValueError("output must be a JSON path, not CSV")
    result["export_paths"] = {"json": str(path.resolve()), "csv": str(csv_path.resolve())}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    fields = ["symbol", "provider", "report_type", "report_date", "period_basis", "currency", "label", "value", "raw_value", "source_unit", "url"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for dataset in result["datasets"]:
            for row in dataset["reports"]:
                items = row.get("raw_items") or [{"label": k, "value": v} for k, v in row.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)]
                if row.get("segments"):
                    items = [{"label": f"{s['dimension']}:{s['name']}", "value": s["revenue_cny"],
                              "raw_value": s["raw_income"], "source_unit": s["raw_unit"]} for s in row["segments"]]
                for item in items:
                    writer.writerow({**{key: row.get(key) for key in fields if key not in {"provider", "label", "value", "raw_value", "source_unit"}},
                        "provider": dataset["provider"], **{key: item.get(key) for key in ("label", "value", "raw_value", "source_unit")}})
    return result["export_paths"]
