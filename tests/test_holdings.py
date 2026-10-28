import json
from types import SimpleNamespace

import pytest

from stock_analysis_mcp.data import holdings as h, information


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(information, "get_settings", lambda: SimpleNamespace(data_root=tmp_path))


def record(**changes):
    return {"SECURITY_CODE": "000001", "HOLDER_NAME": "全国社保基金一零一组合",
            "END_DATE": "2026-06-30 00:00:00", "HOLD_NUM": 123456,
            "FREE_HOLDNUM_RATIO": 0.25, "XZCHANGE": -100,
            "UPDATE_DATE": "2026-08-20 00:00:00", **changes}


def test_dates_units_categories_and_unknown():
    row = h.normalize(record(), "float_top10")
    assert row["shares"] == 123456 and row["shares_change"] == -100
    assert row["float_holding_ratio_pct"] == 0.25
    assert row["announcement_date"] is None
    assert row["source_update_date"] == "2026-08-20"
    assert row["category"] == "social_security"
    assert h.classify("基本养老保险基金一零零一组合", "基金") == "pension"
    assert h.classify("张三", "个人") == "other"


def test_paging_filter_cache_and_reverse_lookup(monkeypatch):
    calls = []
    def request(url, **kw):
        calls.append(kw["params"])
        return {"result": {"data": [record(), record(HOLDER_NAME="基本养老保险基金一零零一组合")], "pages": 3, "count": 6}}
    monkeypatch.setattr(information, "_request", request)
    result = h.get_stock_holdings("000001", category="pension", page_size=2)
    assert len(result["rows"]) == 1 and result["source_page_rows"] == 2
    assert result["has_more"] is True
    monkeypatch.setattr(information, "_request", lambda *a, **kw: pytest.fail("offline network"))
    assert h.get_stock_holdings("000001", page_size=2, offline=True)["source_page_rows"] == 2
    assert h.get_stock_holdings("600000", offline=True)["status"] == "error"
    monkeypatch.setattr(information, "_request", lambda *a, **kw: {"result": {"data": [record()], "pages": 1, "count": 1}})
    assert h.search_institution_holdings(holder_name="全国社保基金一零一组合")["rows"][0]["symbol"] == "000001"


def test_validation_failure_and_empty_are_distinct(monkeypatch):
    for kwargs in ({}, {"holder_name": 'a\")foo'}, {"symbol": "sh000001"},
                   {"report_date": "2026-04-01"}, {"symbol": "000001", "page": 101}):
        with pytest.raises(ValueError):
            h.query_holdings(**kwargs)
    monkeypatch.setattr(information, "_request", lambda *a, **kw: {"success": False, "result": None})
    assert h.get_stock_holdings("000001")["status"] == "error"
    monkeypatch.setattr(information, "_request", lambda *a, **kw: {"result": {"data": [], "pages": 0, "count": 0}})
    assert h.get_stock_holdings("000001")["status"] == "ok"
    monkeypatch.setattr(information, "_request", lambda *a, **kw: {"result": {"data": [record(SECURITY_CODE="600000")], "pages": 1}})
    assert h.get_stock_holdings("000001", refresh=True)["cache"]["status"] == "stale"


async def test_registration_and_envelope():
    from stock_analysis_mcp.server import TOOL_HANDLERS, call_tool
    assert {"get_stock_holdings", "search_institution_holdings"} <= TOOL_HANDLERS.keys()
    result = json.loads((await call_tool("get_stock_holdings", {"symbol": "000001", "offline": True}))[0].text)
    assert result["error"]["code"] == "DATA_ERROR"
    bad = json.loads((await call_tool("get_stock_holdings", {"symbol": "000001", "page": 0}))[0].text)
    assert bad["error"]["code"] == "INVALID_PARAMS"
