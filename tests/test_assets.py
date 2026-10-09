"""Multi-asset identity, provider contracts, pagination and offline regressions."""
import json
from types import SimpleNamespace

import pytest

from stock_analysis_mcp.data import assets, information


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(information, "get_settings", lambda: SimpleNamespace(data_root=tmp_path))


def test_identity_collision_and_no_stock_prefix_guess():
    assert assets.identity("sh000001", "index")["id"] == "sh:index:000001"
    assert assets.identity("000001", "fund")["id"] == "otc:fund:000001"
    assert assets.identity("sh510300", "etf")["market"] == "sh"
    for code, kind, market in [("000001", "index", None), ("sh510300", "etf", "sz"),
                               ("blah000001", "fund", None), ("sz000001", "fund", None),
                               ("000001", "bond", "csi")]:
        with pytest.raises(ValueError):
            assets.identity(code, kind, market)


def test_catalog_pages_market_identity_missing_values_and_cache(monkeypatch):
    calls = []
    def request(url, **kwargs):
        calls.append(kwargs["params"])
        page = kwargs["params"]["pn"]
        return {"data": {"total": 2, "diff": [{"f12": "000001", "f13": page - 1,
                   "f14": "index", "f2": "-", "f124": 0}]}}
    monkeypatch.setattr(assets, "_request", request)
    first = assets.list_instruments("index", page=1, page_size=1)
    second = assets.list_instruments("index", page=2, page_size=1)
    assert first["has_more"] is True and second["has_more"] is False
    assert first["records"][0]["id"] != second["records"][0]["id"]
    assert first["records"][0]["latest_price"] is None
    assert first["records"][0]["quote_time"] is None
    monkeypatch.setattr(assets, "_request", lambda *a, **k: pytest.fail("network in offline"))
    assert assets.list_instruments("index", page=1, page_size=1, offline=True)["records"] == first["records"]
    assert len(calls) == 2


def test_fund_directory_only_parses_json_never_executes_js(monkeypatch):
    monkeypatch.setattr(assets, "_request", lambda *a, **k: '\ufeffvar r = [["000001","HX","华夏成长","混合型","HUAXIA"]];')
    result = assets.list_instruments("fund", page_size=1)
    assert result["records"][0]["id"] == "otc:fund:000001"
    monkeypatch.setattr(assets, "_request", lambda *a, **k: pytest.fail("should reuse single directory"))
    assert assets.list_instruments("fund", page=2, page_size=1)["records"] == []


def test_convertible_mapping_preserves_percent_and_trigger_semantics(monkeypatch):
    monkeypatch.setattr(assets, "_request", lambda *a, **k: {"data": {"total": 1, "diff": [{
        "f12": "110076", "f13": 1, "f14": "华海转债", "f232": "600521", "f235": 16.5,
        "f236": 103.3333, "f237": 6.96, "f240": 21.45, "f242": 20210101}]}})
    result = assets.get_convertible("sh110076")
    row = result["records"][0]
    assert row["stock_symbol"] == "600521"
    assert row["conversion_premium_pct"] == 6.96
    assert row["conversion_start_date"] == "2021-01-01"
    assert "已公告强赎" in result["warnings"][-1]


def test_nav_pagination_preserves_dates_and_rejects_schema_changes(monkeypatch):
    def request(url, **kwargs):
        assert kwargs["params"]["pageIndex"] == 2
        return {"TotalCount": 5, "Data": {"LSJZList": [
            {"FSRQ": "2026-01-06", "DWJZ": "1.20", "LJJZ": "2.30", "JZZZL": "-1.5"},
            {"FSRQ": "2026-01-05", "DWJZ": "1.22", "LJJZ": "--", "JZZZL": "--"}]}}
    monkeypatch.setattr(assets, "_request", request)
    result = assets.get_nav("000001", "2026-01-01", "2026-01-10", page=2, page_size=2)
    assert result["total"] == 5 and result["has_more"]
    assert result["as_of"] == "2026-01-06"
    assert result["records"][0]["accumulated_nav"] is None
    assert result["records"][1]["daily_change_pct"] == -1.5
    monkeypatch.setattr(assets, "_request", lambda *a, **k: {"Data": {}})
    failed = assets.get_nav("000002", "2026-01-01", "2026-01-10")
    assert failed["source"]["status"] == "unavailable" and failed["records"] == []


def test_kline_market_verification_unadjusted_and_ohlc_order(monkeypatch):
    monkeypatch.setattr(assets, "_find", lambda *a, **k: ({"id": "sh:etf:510300"}, {"status": "ok"}))
    def kline(params, **kwargs):
        assert params["secid"] == "1.510300" and params["fqt"] == 0 and params["klt"] == 102
        return {"data": {"code": "510300", "klines": ["2026-01-09,4,4.1,4.2,3.9,100,410,7.5,2.5,0.1,1"]}}
    monkeypatch.setattr(assets, "fetch_em_kline", kline)
    result = assets.get_kline("sh510300", "etf", start_date="2026-01-01", end_date="2026-01-10", period="weekly")
    assert result["records"][0]["close"] == 4.1 and result["records"][0]["high"] == 4.2
    assert result["adjustment"] == "unadjusted"
    monkeypatch.setattr(assets, "_find", lambda *a, **k: pytest.fail("offline directory query"))
    assert assets.get_kline("sh510300", "etf", start_date="2026-01-01", end_date="2026-01-10", period="weekly", offline=True)["records"] == result["records"]


def test_wrong_type_never_calls_kline(monkeypatch):
    monkeypatch.setattr(assets, "_find", lambda *a, **k: (None, {"status": "unavailable"}))
    monkeypatch.setattr(assets, "fetch_em_kline", lambda *a, **k: pytest.fail("unverified instrument"))
    assert assets.get_kline("sh600000", "etf")["source"]["status"] == "unavailable"
    with pytest.raises(ValueError):
        assets.get_kline("000001", "fund")
    with pytest.raises(ValueError, match="仅支持列表和报价"):
        assets.get_kline("sh010107", "bond")


def test_bond_list_excludes_bj_and_retains_unknown_quote_date(monkeypatch):
    monkeypatch.setattr(assets, "_request", lambda *a, **k: json.dumps([
        {"symbol": "bj810011", "name": "定转"}, {"symbol": "sh010107", "name": "国债", "trade": "100", "ticktime": "09:00:00"}]))
    result = assets.list_instruments("bond", page_size=2)
    assert len(result["records"]) == 1 and result["has_more"]
    assert result["records"][0]["quote_date"] is None


async def test_registered_tools_and_schema_offline(monkeypatch):
    from stock_analysis_mcp import server
    monkeypatch.setattr(assets, "_request", lambda *a, **k: pytest.fail("offline network"))
    for name, args in [
        ("list_market_instruments", {"asset_type": "index"}),
        ("get_market_quote", {"symbol": "sh000001", "asset_type": "index"}),
        ("get_market_kline", {"symbol": "sh510300", "asset_type": "etf"}),
        ("get_fund_nav", {"symbol": "000001"}),
        ("get_convertible_bond_info", {"symbol": "sh110076"}),
    ]:
        result = json.loads((await server.call_tool(name, {**args, "offline": True}))[0].text)
        assert result["error"] is None and result["data"]["status"] == "partial"
        assert result["data"]["records"] == [] and result["warnings"]
    result = json.loads((await server.call_tool("list_market_instruments", {"asset_type": "etf", "page_size": 101}))[0].text)
    assert result["error"]["code"] == "INVALID_PARAMS"


def test_package_registry_stays_in_sync():
    from pathlib import Path
    from stock_analysis_mcp import server
    package = json.loads((Path(__file__).parents[1] / "package.json").read_text(encoding="utf-8"))
    assert set(package["mcp"]["tools"]) == set(server.TOOL_HANDLERS)


def test_stale_cache_is_labeled_and_failed_source_does_not_overwrite(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(assets, "_request", lambda *a, **k: {"data": {"total": 1, "diff": [
        {"f12": "510300", "f13": 1, "f14": "ETF", "f2": 4.2}]}})
    result = assets.list_instruments("etf")
    from pathlib import Path
    path = Path(result["source"]["cache_path"])
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    path.write_text(json.dumps(entry), encoding="utf-8")
    monkeypatch.setattr(assets, "_request", lambda *a, **k: {"data": None})
    stale = assets.list_instruments("etf")
    assert stale["records"][0]["latest_price"] == 4.2
    assert stale["source"]["stale"] is True
    assert any("过期缓存" in warning for warning in stale["warnings"])
    assert json.loads(path.read_text(encoding="utf-8")) == entry


def test_catalog_scan_is_bounded(monkeypatch):
    pages = []
    def page(kind, page_number, size, offline):
        pages.append(page_number)
        return {"rows": [], "total": 100000}, {"status": "ok"}
    monkeypatch.setattr(assets, "_page", page)
    result = assets.get_quote("sh999999", "index")
    assert pages == list(range(1, 51))
    assert result["records"] == [] and result["source"]["pages_checked"] == 50


def test_wrong_kline_response_code_is_not_cached(monkeypatch):
    monkeypatch.setattr(assets, "_find", lambda *a, **k: ({}, {"status": "ok"}))
    monkeypatch.setattr(assets, "fetch_em_kline", lambda *a, **k: {"data": {
        "code": "600000", "klines": ["2026-01-09,4,4.1,4.2,3.9,100,410,7.5,2.5,0.1,1"]}})
    result = assets.get_kline("sh510300", "etf")
    assert result["source"]["status"] == "unavailable"
    assert "代码与请求不一致" in result["source"]["error"]


def test_remote_fund_js_is_not_executed(monkeypatch):
    monkeypatch.setattr(assets, "_request", lambda *a, **k: 'var r=[]; dangerous();')
    result = assets.list_instruments("fund")
    assert result["source"]["status"] == "unavailable"
    assert result["records"] == []


def test_money_fund_income_never_mislabeled_as_nav(monkeypatch):
    monkeypatch.setattr(assets, "_request", lambda *a, **k: {"TotalCount": 1, "Data": {
        "FundType": "005", "SYType": "每万份收益", "LSJZList": [
            {"FSRQ": "2026-10-08", "DWJZ": "0.2252", "LJJZ": "0.8260", "NAVTYPE": "1"}]}})
    result = assets.get_nav("000198")
    assert result["records"] == []
    assert "货币基金" in result["source"]["error"]
