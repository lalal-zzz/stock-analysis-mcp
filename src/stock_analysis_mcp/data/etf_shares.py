"""Compare directly reported ETF shares from the Shanghai and Shenzhen exchanges."""

import re
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import BadZipFile

from .network import http_get, http_get_bytes

# Each family lists exact benchmark or exchange-quoted ETF name aliases.
# Avoid substring matches such as 沪深300价值, 创业板人工智能 and 中证500增强.
INDEX_ALIASES = {
    "上证综指": ("上证综指", "上证综合", "上证指数", "上证"),
    "上证50": ("上证50", "50"),
    "上证180": ("上证180", "180"),
    "上证380": ("上证380", "380"),
    "深证成指": ("深证成指", "深证成份", "深成指"),
    "深证50": ("深证50", "深50"),
    "深证100": ("深证100", "深100"),
    "深证200": ("深证200", "深200"),
    "深证300": ("深证300", "深300"),
    "深证综指": ("深证综指", "深证综合", "深证综"),
    "中小100": ("中小100", "中小板", "中小"),
    "沪深300": ("沪深300", "300"),
    "中证A50": ("中证A50", "A50"),
    "中证A100": ("中证A100", "中证100", "A100", "100"),
    "中证A500": ("中证A500", "A500"),
    "中证A股": ("中证A股", "中证全指", "全指", "A股"),
    "中证500": ("中证500", "中证小盘500", "500"),
    "中证800": ("中证800", "800"),
    "中证1000": ("中证1000", "1000"),
    "中证2000": ("中证2000", "2000"),
    "国证1000": ("国证1000",),
    "国证2000": ("国证2000",),
    "国证A指": ("国证A指", "国证A股", "国证A"),
    "创业板指": ("创业板指", "创业板"),
    "创业板50": ("创业板50", "创50"),
    "创业板综": ("创业板综", "创业板综合", "创综"),
    "创业板200": ("创业板200", "创200"),
    "创业板精选88": ("创业板精选88", "创精选88"),
    "科创50": ("上证科创板50", "上证科创50", "科创板50", "科创50"),
    "科创100": ("上证科创板100", "科创板100", "科创100"),
    "科创200": ("上证科创板200", "科创板200", "科创200"),
    "科创综指": ("上证科创板综合价格", "上证科创板综合", "科创综指", "科创综"),
    "科创创业50": ("中证科创创业50", "科创创业50", "科创创业", "双创50", "双创"),
    "MSCI中国A50互联互通": ("MSCI中国A50互联互通人民币", "MSCI中国A50互联互通", "MSCI中国A50", "MSCI互联互通"),
    "MSCI中国A股": ("MSCI中国A股人民币", "MSCI中国A股", "MSCIA股", "MSCI中国"),
    "MSCI中国A股国际": ("MSCI中国A股国际", "MSCI中国A股国际通人民币", "MSCI中国A股国际通", "MSCIA股国际"),
    "富时中国A50": ("富时中国A50", "富时A50"),
}
ALIAS_TO_INDEX = {alias.upper(): index for index, aliases in INDEX_ALIASES.items() for alias in aliases}
SSE_DIRECTORY = "https://etf.sse.com.cn/fundlist/"
SZSE_EXPORT = "https://www.szse.cn/api/report/ShowReport"
SOURCE_URLS = {
    "sh": "https://query.sse.com.cn/commonQuery.do",
    "sz": "https://www.szse.cn/api/report/ShowReport/data",
}
REFERERS = {
    "sh": "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/",
    "sz": "https://fund.szse.cn/marketdata/fundslist/index.html",
}
LOOKBACK_DAYS = 14


def _fetch(market, params):
    return http_get(SOURCE_URLS[market], params, timeout=6, retries=1,
                    extra_headers={"Referer": REFERERS[market]}, use_cookies=False)


def _sse(day):
    payload = _fetch("sh", {
        "isPagination": "true",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
        "pageHelp.pageSize": 2000,
        "pageHelp.pageNo": 1,
        "STAT_DATE": day.isoformat(),
    })
    rows = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or payload.get("pageHelp", {}).get("total", len(rows)) != len(rows):
        raise ValueError("上交所份额报表不可用")
    return rows


def _szse(code, start, end):
    payload = _fetch("sz", {
        "SHOWTYPE": "JSON",
        "CATALOGID": "fund_jjgm",
        "TABKEY": "tab1",
        "PAGENO": 1,
        "txtDm": code,
        "txtStart": start.isoformat(),
        "txtEnd": end.isoformat(),
    })
    for tab in payload if isinstance(payload, list) else []:
        metadata = tab.get("metadata", {})
        if metadata.get("tabkey") == "tab1":
            rows = tab.get("data")
            if isinstance(rows, list) and str(metadata.get("pagecount")) in {"0", "1"}:
                return rows
    raise ValueError("深交所份额报表不可用或数据分页超出预期")


def _sse_directory():
    payload = http_get(SOURCE_URLS["sh"], {
        "sqlId": "COMMON_JJZWZ_JJLB_L", "isPagination": "true",
        "pageHelp.pageSize": 2000, "pageHelp.pageNo": 1,
    }, timeout=8, retries=1, extra_headers={"Referer": SSE_DIRECTORY}, use_cookies=False)
    rows = payload.get("result") if isinstance(payload, dict) else None
    if not rows or payload.get("pageHelp", {}).get("total") != len(rows):
        raise ValueError("上交所标的指数目录不可用或未完整返回")
    return {row["FUND_CODE"]: row for row in rows if "FUND_CODE" in row}


def _szse_daily(day):
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    day_text = day.isoformat()
    payload = http_get_bytes(SZSE_EXPORT, {
        "SHOWTYPE": "xlsx", "CATALOGID": "fund_jjgm", "TABKEY": "tab1",
        "txtStart": day_text, "txtEnd": day_text,
    }, timeout=8, retries=1, extra_headers={"Referer": REFERERS["sz"]}, use_cookies=False)
    if not payload:
        raise ValueError("深交所份额报表不可用")
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Workbook contains no default style", module="openpyxl")
            workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True)
        try:
            sheet = workbook.active
            sheet.reset_dimensions()  # The exchange export declares a one-cell used range.
            table = sheet.values
            columns = {key: i for i, key in enumerate(next(table, ()))}
            required = ("日期", "基金代码", "基金简称", "基金规模(份)")
            if not all(key in columns for key in required):
                raise ValueError("深交所份额报表缺少预期列")
            positions = [columns[key] for key in required]
            rows = []
            for row in table:
                if len(row) <= max(positions):
                    continue
                disclosure_day, code, name, shares = (row[pos] for pos in positions)
                if str(disclosure_day)[:10] == day_text and code is not None:
                    rows.append({"fund_code": str(code), "security_short_name": name,
                                 "size_date": day_text, "shares": shares})
            return rows
        finally:
            workbook.close()
    except (BadZipFile, InvalidFileException) as exc:
        raise ValueError("深交所份额报表无法读取") from exc


def _benchmark_index(name):
    key = re.sub(r"\s+", "", str(name)).upper()
    key = re.sub(r"(?:价格|成份|成分)?指数$", "", key)
    return ALIAS_TO_INDEX.get(key)


def _name_index(name):
    key = re.sub(r"\s+", "", str(name)).upper()
    if "ETF" not in key or any(word in key for word in ("增强", "联接", "低波", "红利", "成长", "价值")):
        return None
    prefix = key.split("ETF", 1)[0]
    return ALIAS_TO_INDEX.get(prefix) or ALIAS_TO_INDEX.get(prefix.removesuffix("指数"))


def _observations(rows, market, code, start, end, *, bulk=False):
    fields = ("SEC_CODE", "STAT_DATE", "TOT_VOL", 10000) if market == "sh" else (
        ("fund_code", "size_date", "shares", 1) if bulk else ("fund_code", "size_date", "current_size", 10000)
    )
    code_key, date_key, shares_key, multiplier = fields
    observations = {}
    for row in rows:
        if not isinstance(row, dict) or row.get(code_key) != code:
            continue
        try:
            day = date.fromisoformat(str(row.get(date_key)))
            shares = Decimal(str(row.get(shares_key)).replace(",", "")) * multiplier
        except (ValueError, InvalidOperation):
            continue
        if start <= day <= end and shares.is_finite() and shares >= 0:
            if day in observations and observations[day] != shares:
                raise ValueError(f"{day} 披露存在冲突的份额记录")
            observations[day] = shares
    return observations


def _fund_result(symbol, observations, start, end, index=None, name=None):
    dates = sorted(observations)[-2:]
    baseline_day, latest_day = (dates if len(dates) == 2 else (None, dates[0] if dates else None))
    if start is not None:
        baseline_day, latest_day = start, end
    baseline = observations.get(baseline_day)
    latest = observations.get(latest_day)
    change = latest - baseline if baseline is not None and latest is not None else None
    return {
        "symbol": symbol,
        "name": name,
        "index": index,
        "baseline_date": baseline_day.isoformat() if baseline_day else None,
        "baseline_shares": float(baseline) if baseline is not None else None,
        "latest_date": latest_day.isoformat() if latest_day else None,
        "latest_shares": float(latest) if latest is not None else None,
        "change_shares": float(change) if change is not None else None,
        "change_pct": float(change / baseline * 100) if change is not None and baseline else None,
        "source_url": REFERERS[symbol[:2]],
    }


def _market_disclosures(market, start, end, earliest):
    fetch = _sse if market == "sh" else _szse_daily
    days = (end, start) if start is not None else (
        end - timedelta(days=offset) for offset in range((end - earliest).days + 1)
    )
    reports, notices = {}, []
    for day in days:
        if start is None and day.weekday() >= 5:
            continue
        try:
            rows = fetch(day)
        except ValueError as exc:
            notices.append(f"{day} {exc}")
            continue
        day_field = "STAT_DATE" if market == "sh" else "size_date"
        same_day = [row for row in rows if row.get(day_field) == day.isoformat()]
        if same_day:
            reports[day] = same_day
        if len(reports) == 2:
            break
    return reports, notices


def _market_results(market, reports, directory, start, end):
    markets = {
        "sh": ("SEC_CODE", "SEC_NAME"),
        "sz": ("fund_code", "security_short_name"),
    }
    code_field, name_field = markets[market]
    records = {}
    for day, rows in sorted(reports.items()):
        for row in rows:
            code = str(row.get(code_field, ""))
            if not re.fullmatch(r"[0-9]{6}", code):
                continue
            records.setdefault(code, {}).setdefault(day, []).append(row)

    results, notices = [], []
    for code, by_day in records.items():
        latest_row = next(reversed(by_day.values()))[-1]
        fund = directory.get(code, {})
        name = fund.get("FUND_EXPANSION_ABBR") or latest_row.get(name_field)
        if name and re.search(r"增强|联接", name):
            continue
        benchmark = fund.get("INDEX_NAME")
        index = _benchmark_index(benchmark) if benchmark and benchmark != "-" else _name_index(name)
        if not index:
            continue

        observations = {}
        for day, rows in by_day.items():
            try:
                observations.update(_observations(rows, market, code, day, day, bulk=True))
            except ValueError as exc:
                notices.append(f"{market}{code} {exc}")
        results.append(_fund_result(market + code, observations, start, end, index, name))
    return results, notices


def _summaries(results):
    by_period, by_index = {}, {}
    for row in results:
        change = row["change_shares"]
        if change is None:
            continue
        period = (row["baseline_date"], row["latest_date"])
        for groups, key in ((by_period, period), (by_index, (*period, row["index"]))):
            group = groups.setdefault(key, {
                "baseline_date": period[0], "latest_date": period[1], "fund_count": 0,
                "increase_count": 0, "decrease_count": 0, "change_shares": 0.0,
            })
            group["fund_count"] += 1
            group["increase_count"] += change > 0
            group["decrease_count"] += change < 0
            group["change_shares"] += change
    return list(by_period.values()), [
        {"index": key[2], **group} for key, group in sorted(by_index.items())
    ]


def _discover(start, end, earliest):
    with ThreadPoolExecutor(max_workers=3) as executor:
        directory_job = executor.submit(_sse_directory)
        jobs = {market: executor.submit(_market_disclosures, market, start, end, earliest)
                for market in ("sh", "sz")}
        warnings = []
        try:
            directory = directory_job.result()
        except ValueError as exc:
            directory = {}
            warnings.append(f"{exc}，沪市仅按报表简称匹配")

        results, coverage = [], {}
        for market, job in jobs.items():
            reports, notices = job.result()
            market_rows, row_notices = _market_results(market, reports, directory if market == "sh" else {}, start, end)
            results.extend(market_rows)
            warnings.extend(notices + row_notices)
            compared = sum(row["change_shares"] is not None for row in market_rows)
            coverage[market] = {
                "disclosure_dates": [day.isoformat() for day in sorted(reports)],
                "disclosure_row_counts": {day.isoformat(): len(rows) for day, rows in sorted(reports.items())},
                "matched_funds": len(market_rows),
                "compared_funds": compared,
            }
            if len(reports) < 2 or compared < len(market_rows) or not market_rows:
                warnings.append(f"{market} 识别 {len(market_rows)} 只，完成比较 {compared} 只，请核对披露覆盖")
    results.sort(key=lambda row: (row["index"], row["symbol"]))
    summaries, index_summaries = _summaries(results)
    return {
        "scope": "discovered_domestic_broad_index_etfs",
        "classification": {
            "index_families": list(INDEX_ALIASES),
            "sh": "交易所标的指数优先，缺失时匹配份额报表简称",
            "sz": "匹配交易所份额报表简称",
            "sse_directory_url": SSE_DIRECTORY,
        },
        "coverage": coverage,
        "summaries": summaries,
        "index_summaries": index_summaries,
        "results": results,
        "warnings": warnings,
    }


def get_broad_etf_share_changes(symbols=None, start_date=None, end_date=None):
    """Compare exact dates, or the two latest observations up to end_date."""
    today = datetime.now(timezone(timedelta(hours=8))).date()
    try:
        end = date.fromisoformat(end_date) if end_date is not None else today
        start = date.fromisoformat(start_date) if start_date is not None else None
    except ValueError as exc:
        raise ValueError("日期应为 YYYY-MM-DD") from exc
    if end > today or (start is not None and start >= end):
        raise ValueError("起始日期须早于结束日期，结束日期须为今天或更早")
    if symbols is not None and (
        not isinstance(symbols, list) or not 1 <= len(symbols) <= 12
        or any(not isinstance(symbol, str) or not re.fullmatch(r"(sh|sz)[0-9]{6}", symbol)
               for symbol in symbols)
    ):
        raise ValueError("symbols 须为 1 至 12 个带 sh/sz 前缀的六位基金代码")
    earliest = start if start is not None else date.fromordinal(max(1, end.toordinal() - LOOKBACK_DAYS + 1))
    context = {
        "comparison": "exact_dates" if start is not None else "latest_two_disclosures",
        "start_date": earliest.isoformat(),
        "end_date": end.isoformat(),
        "unit": "shares",
    }
    if symbols is None:
        return {**context, **_discover(start, end, earliest)}

    warnings = []
    sse_reports = {}
    results = []
    for symbol in dict.fromkeys(symbols):
        market, code = symbol[:2], symbol[2:]
        observations = {}
        name = None
        try:
            if market == "sh":
                days = (end, start) if start is not None else (
                    end - timedelta(days=offset) for offset in range((end - earliest).days + 1)
                )
                for day in days:
                    if start is None and day.weekday() >= 5:
                        continue
                    if day not in sse_reports:
                        try:
                            sse_reports[day] = _sse(day)
                        except ValueError as exc:
                            sse_reports[day] = None
                            warnings.append(f"{day} {exc}")
                    rows = sse_reports[day]
                    if rows is not None:
                        observations.update(_observations(rows, market, code, day, day))
                        name = name or next((row.get("SEC_NAME") for row in rows if row.get("SEC_CODE") == code), None)
                    if len(observations) == 2:
                        break
            else:
                ranges = [(start, start), (end, end)] if start is not None else [(earliest, end)]
                for first, last in ranges:
                    try:
                        rows = _szse(code, first, last)
                        observations.update(_observations(rows, market, code, first, last))
                        name = next((row.get("security_short_name") for row in rows if row.get("fund_code") == code), None) or name
                    except ValueError as exc:
                        warnings.append(f"{symbol} {first} 至 {last} {exc}")
        except ValueError as exc:
            warnings.append(f"{symbol} {exc}")

        result = _fund_result(symbol, observations, start, end, _name_index(name), name)
        results.append(result)
        if result["change_shares"] is None:
            warnings.append(f"{symbol} 缺少两个可比较的披露值，份额变化为空")

    return {
        **context,
        "scope": "requested_symbols",
        "results": results,
        "warnings": warnings,
    }
