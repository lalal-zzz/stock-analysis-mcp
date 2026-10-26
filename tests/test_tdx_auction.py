import struct
from datetime import datetime

import pytest

from stock_analysis_mcp.data import auction
from stock_analysis_mcp.data.providers import tdx_auction as tdx


def payload(*rows):
    return struct.pack("<H", len(rows)) + b"".join(struct.pack("<HfIiBB", *r) for r in rows)


def test_signed_int32_and_lot_units():
    data = payload((555, 11.3, 1075, -598, 0, 0),
                   (560, 11.2, 10000, 109912, 0, 0),
                   (565, 11.15, 27526, -100000, 0, 0),
                   (897, 11.3, 10, 1, 0, 0))
    rows = tdx.decode_series(data, "2026-10-26")
    assert len(rows) == 3
    assert rows[0]["price"] == 11.3
    assert rows[0]["matched_volume"] == 107500
    assert rows[0]["unmatched_sell_volume"] == 59800
    assert rows[1]["unmatched_buy_volume"] == 10991200
    assert rows[1]["unmatched_direction"] == "buy"
    assert rows[2]["unmatched_sell_volume"] == 10000000
    # A 09:25 process snapshot is still virtual, not an execution.
    assert rows[2]["volume_kind"] == "virtual_matched"
    assert rows[2]["amount"] == 30691490


@pytest.mark.parametrize("data", [b"", b"\x02\x00", payload((555, float('nan'), 1, 0, 0, 0)),
    payload((555, 10, 1, 0, 0, 60)), payload((560, 10, 1, 0, 0, 0), (555, 10, 1, 0, 0, 0))])
def test_corrupt_series_rejected(data):
    with pytest.raises(ValueError):
        tdx.decode_series(data, "2026-10-26")


def test_snapshot_precision_not_every_second():
    rows = tdx.decode_series(payload((555, 10, 1, 0, 0, 0), (555, 10, 1, 1, 0, 9),
                                     (555, 10, 2, -1, 0, 27)), "2026-10-26")
    result = auction._precision(rows)
    assert result["min_interval_seconds"] == 9
    assert result["median_interval_seconds"] == 13.5
    assert result["max_interval_seconds"] == 18
    assert result["observed_seconds"] == 3
    assert result["missing_seconds_count"] == 598
    assert not result["every_second_available"]
    assert not result["order_by_order_available"]


def test_provider_rotates_and_explicitly_requests_day(monkeypatch):
    calls = []
    def request(host, market, symbol, day, timeout):
        calls.append((host, market, symbol, day))
        if len(calls) == 1:
            raise TimeoutError("test")
        return payload((555, 9.5, 100, -10, 0, 3))
    monkeypatch.setattr(tdx, "provider_available", lambda _: True)
    monkeypatch.setattr(tdx, "tdx_request", request)
    result = tdx.fetch_auction_series("600000", "2026-10-26")
    assert result["status"] == "ok"
    assert calls[-1][1:] == (1, "600000", 20261026)
    assert result["date_validation"] == "explicit_request_date_not_echoed_by_protocol"


def test_process_archive_keeps_unmatched_and_precision(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_ANALYSIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(auction, "_now", lambda: datetime.fromisoformat("2026-10-26T16:00:00+08:00"))
    rows = tdx.decode_series(payload((555, 11.3, 1075, -598, 0, 0), (564, 11.15, 23660, 42, 0, 57)), "2026-10-26")
    monkeypatch.setattr(auction, "fetch_auction_series", lambda *args: {
        "status": "ok", "records": rows, "source": "tdx_auction", "source_host": "test",
        "source_url": tdx.REFERENCE, "date_validation": "explicit_request_date_not_echoed_by_protocol"})
    monkeypatch.setattr(auction, "http_get", lambda *args, **kwargs: None)
    result = auction.get_stock_auction("000001")
    assert result["source"] == "tdx_auction"
    assert result["coverage"]["unmatched_volume_available"]
    assert result["precision"]["max_interval_seconds"] == 597
    assert result["opening_match"] is None
    monkeypatch.setattr(auction, "fetch_auction_series", lambda *args: pytest.fail("offline"))
    cached = auction.get_stock_auction("000001", offline=True)
    assert cached["origin"] == "archive"
    assert cached["records"] == rows
    assert cached["precision"] == result["precision"]


def test_transport_reassembles_partial_frames(monkeypatch):
    from stock_analysis_mcp.data import network
    messages = []
    raw = payload((555, 10, 1, -3, 0, 0))
    def response(command, request_id, body):
        return bytes.fromhex("b1cb7400") + struct.pack("<BIBHHH", 28, request_id, 0, command, len(body), len(body)) + body
    stream = bytearray(response(13, 1, b"ok") + response(0x056a, 2, raw))
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, timeout): pass
        def sendall(self, frame): messages.append(frame)
        def recv(self, count):
            chunk = bytes(stream[:min(count, 3)])
            del stream[:len(chunk)]
            return chunk
    monkeypatch.setattr(network.socket, "create_connection", lambda *args, **kwargs: Socket())
    assert network.tdx_request("test", 0, "000001", 20261026) == raw
    market, code, day, mode, start, reserved, limit = struct.unpack("<H6sIIIII", messages[1][12:])
    assert (market, code, day, mode, start, limit) == (0, b"000001", 20261026, 3, 0, 500)


@pytest.mark.parametrize("opening_clock", ["09:25:00", "09:25:02"])
def test_0926_can_read_process_and_separate_opening_match(monkeypatch, tmp_path, opening_clock):
    """Local clock simulation only; does not establish a provider's live delivery SLA."""
    now = datetime.fromisoformat("2026-10-26T09:26:00+08:00")
    monkeypatch.setenv("STOCK_ANALYSIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(auction, "_now", lambda: now)
    rows = tdx.decode_series(payload((555, 11.3, 1075, -598, 0, 0),
                                    (564, 11.15, 23660, -351, 0, 57)), "2026-10-26")
    monkeypatch.setattr(auction, "fetch_auction_series", lambda *args: {
        "status": "ok", "records": rows, "source": "tdx_auction", "source_host": "test",
        "source_url": tdx.REFERENCE, "date_validation": "explicit_request_date_not_echoed_by_protocol"})
    def http(url, **kwargs):
        if url == auction.TREND_URL:
            return {"data": {"code": "000001", "market": 0, "preClose": 11.2,
                "trends": ["2026-10-26 09:25,11.15,11.15,11.15,11.12,0,0,11.2"]}}
        if url == auction.DETAIL_URL:
            return {"data": {"code": "000001", "market": 0,
                "details": [f"{opening_clock},11.15,27526,648,1"]}}
        assert url == auction.BOOK_URL
        return {"data": {"f57": "000001", "f86": now.timestamp(), "f43": 11.15}}
    monkeypatch.setattr(auction, "http_get", http)
    result = auction.get_stock_auction("000001")
    assert result["status"] == "ok"
    assert result["fetched_at"] == now.isoformat()
    assert result["records"] == rows
    assert result["latest_virtual_match"]["unmatched_sell_volume"] == 35100
    assert result["opening_match"]["volume"] == 2752600
    assert result["opening_match"]["time"].endswith(f"{opening_clock}+08:00")
    assert result["order_book_observations"] == []
