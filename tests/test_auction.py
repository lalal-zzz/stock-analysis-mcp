import json
from datetime import datetime

import pytest

from stock_analysis_mcp.data import auction as a


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_ANALYSIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(a, "_now", lambda: datetime.fromisoformat("2026-10-26T09:24:58+08:00"))
    monkeypatch.setattr(a, "http_get", lambda *args, **kwargs: pytest.fail("unexpected network"))
    monkeypatch.setattr(a, "fetch_auction_series", lambda *args: {"status": "unavailable"})


def quote(clock="09:24:57", symbol="000001"):
    return {"data": {"f57": symbol, "f58": "test", "f43": 11.15,
                     "f86": datetime.fromisoformat(f"2026-10-26T{clock}+08:00").timestamp(),
                     "f19": 11.15, "f20": 23660, "f39": 11.15, "f40": 23660}}


def source(url, **kwargs):
    if url == a.BOOK_URL:
        assert "f530" in kwargs["params"]["fields"]
        return quote()
    if url == a.TREND_URL:
        return {"data": {"code": "000001", "market": 0, "preClose": 11.2, "trends": [
            "2026-10-26 09:24,11.17,11.15,11.17,11.15,0,0,11.2",
            "2026-10-26 09:25,11.15,11.15,11.15,11.12,0,0,11.2",
            "2026-10-26 09:26,11.15,11.15,11.15,11.15,27526,30691490,11.15"]}}
    assert url == a.DETAIL_URL
    return {"data": {"code": "000001", "market": 0, "details": [
        "09:24:57,11.15,23660,0,4", "09:25:00,11.15,27526,648,1", "09:30:00,11.15,8265,151,1"]}}


def test_seconds_and_opening_are_separate_not_summed(monkeypatch):
    monkeypatch.setattr(a, "http_get", source)
    result = a.get_stock_auction("sz000001")
    assert len(result["records"]) == 2
    assert result["latest_virtual_match"]["volume"] == 2366000
    assert result["opening_match"]["volume"] == 2752600
    assert result["opening_match"]["amount"] == 30691490
    assert result["opening_match"]["amount_kind"] == "derived_price_times_volume"
    assert result["minute_records"][0]["volume_lots"] == 0
    assert result["records"][0]["unmatched_buy_volume"] is None
    assert not result["coverage"]["minute_grid_complete"]
    assert not result["coverage"]["unmatched_volume_available"]
    # The local read needs no network and preserves the original observation time.
    monkeypatch.setattr(a, "http_get", lambda *args, **kwargs: pytest.fail("offline request"))
    cached = a.get_stock_auction("000001", offline=True)
    assert cached["records"] == result["records"]
    assert cached["origin"] == "archive"


def test_book_units_and_auction_archive(monkeypatch):
    monkeypatch.setattr(a, "http_get", lambda *args, **kwargs: quote())
    result = a.get_stock_order_book("000001")
    assert result["bids"][0]["volume"] == 2366000
    assert result["bids"][1]["volume"] is None
    assert result["archived"]
    assert len(a.get_stock_auction("000001", offline=True)["order_book_observations"]) == 1


@pytest.mark.parametrize("stamp", ["08:59:00", "09:23:00", "09:25:01", "15:00:00"])
def test_stale_future_or_outside_book_not_archived(monkeypatch, stamp):
    monkeypatch.setattr(a, "http_get", lambda *args, **kwargs: quote(stamp))
    assert not a.get_stock_order_book("000001")["archived"]


def test_sse_interpretation_is_explicitly_unverified(monkeypatch):
    payload = quote(symbol="600000")
    payload["data"].update(f17=0, f18=100, f37=0, f38=0)
    monkeypatch.setattr(a, "http_get", lambda *args, **kwargs: payload)
    indication = a.get_stock_order_book("600000")["auction_indication"]
    assert indication["matched_volume"] == 2366000
    assert indication["unmatched_buy_volume"] == 10000
    assert indication["unmatched_direction"] == "buy"
    assert indication["provider_mapping_verified"] is False


def test_mismatched_day_or_identity_never_relabels(monkeypatch):
    def stale(url, **kwargs):
        result = source(url, **kwargs)
        if url == a.BOOK_URL:
            result["data"]["f86"] -= 86400
        return result
    monkeypatch.setattr(a, "http_get", stale)
    assert a.get_stock_auction("000001")["records"] == []
    monkeypatch.setattr(a, "http_get", lambda *args, **kwargs: {"data": {"code": "600000", "market": 1}})
    assert a.get_stock_auction("000001")["records"] == []


def test_historical_missing_never_networks():
    result = a.get_stock_auction("000001", trade_date="2026-10-23")
    assert result["status"] == "unavailable"
    assert result["records"] == []


def test_cache_connections_close_on_windows():
    a._save("test", "000001", "2026-10-26", "latest", {"value": 1})
    assert a._read("test", "000001", "2026-10-26") == [{"value": 1}]
    # Windows refuses this while SQLite still owns an open database handle.
    a._path().unlink()


@pytest.mark.parametrize("value", ["sh000001", "sz600000", "BK1090", "510300", "000001x", "../600000"])
def test_invalid_symbols(value):
    with pytest.raises(ValueError):
        a.get_stock_auction(value)


def test_missing_and_malformed_fields():
    rows, bad = a._details({"details": ["oops", "09:20:00,nan,10,0,4", "09:20:01,10,-1,0,4",
                                        "09:20:02,10,1,0,1", "09:20:03,10,1,0,4"]}, "2026-10-26")
    assert bad == 4
    assert rows[0]["volume"] == 100
    assert a._number("-") is None
    assert a._number("inf") is None


async def test_server_contract(monkeypatch):
    from stock_analysis_mcp import server
    monkeypatch.setattr(a, "http_get", source)
    result = json.loads((await server.call_tool("get_stock_auction", {"symbol": "000001"}))[0].text)
    assert result["error"] is None
    assert result["data"]["opening_match"]["volume"] == 2752600
    assert result["warnings"]
    invalid = json.loads((await server.call_tool("get_stock_auction", {"symbol": "000001", "offline": "true"}))[0].text)
    assert invalid["error"]
