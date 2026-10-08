"""Independent A-share financial sources, units and period semantics."""
import csv
import json
from types import SimpleNamespace

import pytest

from stock_analysis_mcp.data import financials as fin, information as info


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(info, "get_settings", lambda: SimpleNamespace(data_root=tmp_path, stock_dir=tmp_path, sector_dir=tmp_path))


@pytest.mark.parametrize("bad", ["00700", "usAAPL", "hk00700", "510300", "159915", "900901", "sh000001"])
def test_a_share_download_rejects_other_markets(bad):
    with pytest.raises(ValueError):
        fin.a_symbol(bad)


def test_beijing_symbols_and_units():
    assert fin.a_symbol("bj430047") == ("430047", "bj")
    assert fin.numeric("1,234.50万元") == 12345000
    assert fin.numeric("12.5亿元") == 1250000000
    assert fin.numeric("15.2%") == 15.2
    assert fin.numeric(0) == 0 and fin.numeric("--") is None
    assert fin.report_date("2026中报") == "2026-06-30"
    assert fin.report_date("2025年报") == "2025-12-31"


def test_sohu_skips_yoy_column_and_normalizes_section_units():
    body = '''<table class="reportA"><tr><th>报告期</th><td>2026-06-30</td><td>同比变化</td><td>2026-03-31</td></tr>
      <tr><td colspan="4">财务指标</td></tr><tr><th>每股收益</th><td>35.57</td><td>-1.69%</td><td>21.76</td></tr>
      <tr><td colspan="4">经营业绩(万元)</td></tr><tr><th>主营业务收入</th><td>9070326</td><td>1.47%</td><td>5390925</td></tr></table>'''
    rows = fin.parse_sohu(body, "600519", "indicators", 8)
    assert len(rows) == 2 and rows[0]["eps"] == 35.57
    assert rows[1]["eps"] == 21.76 and rows[0]["operating_revenue"] == 90703260000
    assert rows[0]["operating_revenue_yoy_pct"] == 1.47
    assert "operating_revenue_yoy_pct" not in rows[1]


def test_sohu_statement_keeps_yuan_and_section_duplicates():
    body = '''<table class="tableP"><tr><td>报告期</td><td>2026-06-30</td></tr>
      <tr><td colspan="2">经营活动</td></tr><tr><td>净利润</td><td>10</td></tr>
      <tr><td colspan="2">附注</td></tr><tr><td>净利润</td><td>11</td></tr></table>'''
    row = fin.parse_sohu(body, "600519", "income", 1)[0]
    assert [r["value"] for r in row["raw_items"]] == [10, 11]
    assert [r["section"] for r in row["raw_items"]] == ["经营活动", "附注"]


def tencent_payload():
    return {"code": 0, "data": {"zyzb": {"date": "2026中报", "detail": {
        "mgsy": "35.57元", "yyzsr": "922.78亿元", "jlrzzl": "-1.95%", "zcfzl": "15.19%"}},
        "zysr": [{"date": "2026中报", "detail": [{"type": "product", "detail": [
            {"name": "茅台酒", "unit": "元", "income": "777.24亿", "zb": "85.69"}]}]}]}}


def test_tencent_latest_report_is_not_padded():
    rows = fin.parse_tencent(tencent_payload(), "600519", "indicators", 8)
    assert len(rows) == 1 and rows[0]["revenue"] == 92278000000
    assert rows[0]["profit_yoy_pct"] == -1.95
    assert "parent_net_profit" not in rows[0]
    segment = fin.parse_tencent(tencent_payload(), "600519", "revenue_segments", 8)[0]["segments"][0]
    assert segment["revenue_cny"] == 77724000000 and segment["share_pct"] == 85.69


def test_sina_retains_announcement_and_scope():
    payload = {"result": {"data": {"report_list": {"20260630": {"rCurrency": "CNY", "rType": "合并期末",
        "publish_date": "20260815", "is_audit": "未审计", "data": [
            {"item_title": "货币资金", "item_field": "CURFDS", "item_value": "53518798979.08"}]}}}}}
    row = fin.parse_sina(payload, "600519", "balance", 8)[0]
    assert row["published_date"] == "2026-08-15" and row["period_basis"] == "instant"
    assert row["scope"] == "合并期末" and row["raw_items"][0]["value"] == 53518798979.08


def test_explicit_unsupported_tencent_statement_never_falls_back(monkeypatch):
    monkeypatch.setattr(info, "_request", lambda *a, **k: pytest.fail("unsupported provider must not request other sources"))
    result = fin.download_financials("600519", provider="tencent", report_type="balance")
    assert result["status"] == "partial" and result["datasets"] == []
    assert result["unavailable"][0]["status"] == "unsupported"


def test_offline_download_and_cli_dry_run_make_no_requests(monkeypatch, tmp_path):
    from stock_analysis_mcp.cli import main
    monkeypatch.setattr(info, "_request", lambda *a, **k: pytest.fail("network forbidden"))
    assert fin.download_financials("600519", provider="all", report_type="all", offline=True)["status"] == "partial"
    assert main(["financial-download", "--symbol", "600519", "--provider", "sohu", "--dry-run"]) == 0
    assert not list(tmp_path.iterdir())


def test_download_export_and_source_difference_preserve_independent_values(monkeypatch, tmp_path):
    def fetch(symbol, provider, kind, periods):
        return [{"symbol": symbol, "report_date": "2026-06-30", "source": provider, "report_type": kind,
                 "eps": 10 if provider == "sohu" else 11, "currency": "CNY", "period_basis": "year_to_date"}]
    monkeypatch.setattr(fin, "fetch_provider", fetch)
    result = fin.download_financials("600519", provider="all", periods=1)
    assert len(result["datasets"]) == 3
    difference = result["analysis"]["cross_source_differences"][0]
    assert difference["range"] == 1 and {v["value"] for v in difference["values"]} == {10, 11}
    paths = fin.export_financials(result, tmp_path / "finance.json")
    assert json.loads((tmp_path / "finance.json").read_text(encoding="utf-8"))["export_paths"] == paths
    with (tmp_path / "finance.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {r["provider"] for r in rows} == {"eastmoney", "sohu", "tencent"}


def test_analysis_auto_fails_over_to_sohu(monkeypatch):
    monkeypatch.setattr(info, "fetch_financials", lambda *a: (_ for _ in ()).throw(RuntimeError("Eastmoney down")))
    monkeypatch.setattr(fin, "fetch_provider", lambda *a: [{"report_date": "2026-06-30", "eps": 35.57}])
    result = info.financial_analysis("600519", periods=1)
    assert result["companies"][0]["provider"] == "sohu"
    assert result["companies"][0]["reports"][0]["eps"] == 35.57


def test_analysis_preserves_old_cache_if_all_alternatives_fail(monkeypatch):
    def cached(key, loader, **kwargs):
        if key.startswith("financial:"):
            return [{"report_date": "2026-06-30", "eps": 35.57}], {"status": "stale", "stale": True}
        return [], {"status": "unavailable"}
    monkeypatch.setattr(info, "_cached", cached)
    result = info.financial_analysis("600519", periods=1)
    assert result["status"] == "partial" and result["companies"][0]["provider"] == "eastmoney"
    assert result["companies"][0]["reports"][0]["eps"] == 35.57


@pytest.mark.integration
def test_financial_provider_live():
    # Single-company bounded validation, never a market-wide financial download.
    for kind in ("indicators", "balance", "income", "cashflow"):
        assert fin.fetch_sohu("600519", kind, 1)
    assert fin.fetch_tencent("600519", "indicators", 1)
    assert fin.fetch_sina("600519", "balance", 1)
