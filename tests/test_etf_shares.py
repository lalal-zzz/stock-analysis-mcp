import json
from datetime import date
from io import BytesIO

from openpyxl import Workbook

from stock_analysis_mcp.data import etf_shares
from stock_analysis_mcp.server import call_tool


async def test_exact_exchange_disclosures_through_mcp(monkeypatch):
    def fetch(market, params):
        if market == "sh":
            day = params["STAT_DATE"]
            return {"result": [{"SEC_CODE": "510300", "STAT_DATE": day,
                                "TOT_VOL": "1,000.00" if day.endswith("12") else "1,001.25"}]}
        day = params["txtStart"]
        return [{"metadata": {"tabkey": "tab1", "pagecount": 1}, "data": [
            {"fund_code": "159919", "size_date": "2025-11-12", "current_size": "200"}
        ] if day.endswith("12") else []}]

    monkeypatch.setattr(etf_shares, "_fetch", fetch)
    response = await call_tool("get_broad_etf_share_changes", {
        "symbols": ["sh510300", "sz159919"], "start_date": "2025-11-12", "end_date": "2025-11-13",
    })
    payload = json.loads(response[0].text)
    assert payload["error"] is None
    sh, sz = payload["data"]["results"]
    assert sh["baseline_shares"] == 10_000_000
    assert sh["change_shares"] == 12_500
    assert sh["change_pct"] == 0.125
    assert sz["baseline_shares"] == 2_000_000
    assert sz["latest_shares"] is None
    assert sz["change_shares"] is None
    assert any("sz159919" in warning for warning in payload["warnings"])


def test_latest_shared_report_skips_unpublished_date(monkeypatch):
    dates = []

    def sse(day):
        dates.append(day)
        return [
            {"SEC_CODE": code, "STAT_DATE": day.isoformat(), "TOT_VOL": str(100 - day.day)}
            for code in ("510300", "510500")
        ] if day.day in {12, 13} else []

    monkeypatch.setattr(etf_shares, "_sse", sse)
    data = etf_shares.get_broad_etf_share_changes(["sh510300", "sh510500"], end_date="2025-11-14")
    assert dates == [date(2025, 11, day) for day in (14, 13, 12)]
    for row in data["results"]:
        assert row["baseline_date"] == "2025-11-12"
        assert row["latest_date"] == "2025-11-13"
        assert row["change_shares"] == -10_000


def test_default_discovers_funds_and_summarizes_matching_dates(monkeypatch):
    monkeypatch.setattr(etf_shares, "_sse_directory", lambda: {
        "510301": {"INDEX_NAME": "沪深300指数", "FUND_EXPANSION_ABBR": "沪深300ETF甲"},
        "510302": {"INDEX_NAME": "沪深300价值指数", "FUND_EXPANSION_ABBR": "沪深300ETF乙"},
    })

    def sse(day):
        return [{"SEC_CODE": code, "STAT_DATE": day.isoformat(), "TOT_VOL": day.day}
                for code in ("510301", "510302")]

    def szse(day):
        return [
            {"fund_code": code, "security_short_name": name, "size_date": day.isoformat(),
             "shares": day.day * 100 + 7}
            for code, name in [
                ("159101", "沪深300ETF丙"), ("159102", "300ETF丁"),
                ("159103", "300ETF增强"), ("159104", "创业板人工智能ETF"),
            ]
            if code != "159102" or day.day == 13
        ]

    monkeypatch.setattr(etf_shares, "_sse", sse)
    monkeypatch.setattr(etf_shares, "_szse_daily", szse)
    result = etf_shares.get_broad_etf_share_changes(end_date="2025-11-13")
    assert [row["symbol"] for row in result["results"]] == ["sh510301", "sz159101", "sz159102"]
    assert result["coverage"]["sz"]["matched_funds"] == 2
    assert result["coverage"]["sz"]["compared_funds"] == 1
    assert result["index_summaries"][0]["index"] == "沪深300"
    assert result["summaries"][0]["fund_count"] == 2
    assert result["summaries"][0]["change_shares"] == 10_100


def test_szse_export_uses_share_units_and_checks_disclosure_date(monkeypatch):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["日期", "基金代码", "基金简称", "基金规模(份)"])
    sheet.append(["2025-11-12", "159919", "沪深300ETF嘉实", "123,456.00"])
    sheet.append(["2025-11-13", "159919", "沪深300ETF嘉实", "999999"])
    buffer = BytesIO()
    workbook.save(buffer)
    monkeypatch.setattr(etf_shares, "http_get_bytes", lambda *args, **kwargs: buffer.getvalue())
    day = date(2025, 11, 12)
    rows = etf_shares._szse_daily(day)
    assert etf_shares._observations(rows, "sz", "159919", day, day, bulk=True) == {day: 123_456}
