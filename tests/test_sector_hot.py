"""Source contract, signed deltas, bounded-list censoring and offline behavior."""
import json
from types import SimpleNamespace

import pytest

from stock_analysis_mcp.data import information, sector_hot


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(information, "get_settings", lambda: SimpleNamespace(data_root=tmp_path))


def payload(heat=100, rank=2, code="886069"):
    return {"status_code": 0, "data": {"plate_list": [
        {"code": code, "name": "人形机器人", "order": rank, "rate": str(heat),
         "rise_and_fall": -1.5, "hot_rank_chg": -2, "hot_tag": "首次上榜"}]}}


def test_live_cache_and_offline_no_new_snapshot(monkeypatch, tmp_path):
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        return payload()
    monkeypatch.setattr(sector_hot, "_request", request)
    first = sector_hot.get_sector_hot_rank()
    row = first["records"][0]
    assert row["sector_code"] == "886069" and row["code_namespace"] == "ths"
    assert row["change_pct"] == -1.5 and row["source_rank_change_raw"] == -2
    assert row["heat_delta"] is None and row["source_as_of"] is None
    assert first["source"]["freshness_verified"] is False
    assert first["coverage"]["full_market"] is False
    assert calls[0][1]["params"] == {"type": "concept"}
    monkeypatch.setattr(sector_hot, "_request", lambda *a, **k: pytest.fail("offline network"))
    assert sector_hot.get_sector_hot_rank(offline=True)["records"] == first["records"]
    import sqlite3
    with sqlite3.connect(tmp_path / "information-cache" / "sector-hot-snapshots.sqlite3") as c:
        assert c.execute("SELECT count(*) FROM observations").fetchone()[0] == 1


def test_signed_deltas_and_censored_missing_members(monkeypatch, tmp_path):
    source = {"status": "ok", "fetched_at": "2026-10-19T07:00:00+00:00",
              "cache_path": str(tmp_path / "cache.json")}
    first = [{"sector_code": "886069", "heat_value": 100., "heat_rank": 2},
             {"sector_code": "885517", "heat_value": 80., "heat_rank": 3}]
    assert sector_hot._observations(first, source) == (None, [])
    current = [{"sector_code": "886069", "heat_value": 90., "heat_rank": 1},
               {"sector_code": "885700", "heat_value": 70., "heat_rank": 2}]
    source = {**source, "fetched_at": "2026-10-20T07:00:00+00:00"}
    monkeypatch.setattr(sector_hot, "_cached", lambda *a, **k: (current, source))
    result = sector_hot.get_sector_hot_rank(limit=1)
    assert result["records"][0]["heat_delta"] == -10
    assert result["records"][0]["rank_improvement"] == 1
    assert result["comparison"]["left_list_codes"] == ["885517"]
    # Repeating this observation must still compare against the earlier one.
    assert sector_hot.get_sector_hot_rank()["records"][0]["heat_delta"] == -10
    assert sector_hot.get_sector_hot_rank()["records"][1]["heat_delta"] is None
    # Positive heat change and worsening rank are independent signals.
    later = [{"sector_code": "886069", "heat_value": 110., "heat_rank": 4}]
    source = {**source, "fetched_at": "2026-10-21T07:00:00+00:00"}
    monkeypatch.setattr(sector_hot, "_cached", lambda *a, **k: (later, source))
    row = sector_hot.get_sector_hot_rank()["records"][0]
    assert row["heat_delta"] == 20 and row["rank_improvement"] == -3


@pytest.mark.parametrize("bad", [None, {}, {"status_code": 1},
    {"status_code": 0, "data": {"plate_list": []}}, payload("nan"), payload(-1),
    payload(100, 1.2), payload(100, 1, "BK1234")])
def test_reject_provider_failures_without_caching_as_success(monkeypatch, bad):
    monkeypatch.setattr(sector_hot, "_request", lambda *a, **k: bad)
    result = sector_hot.get_sector_hot_rank()
    assert result["status"] == "unavailable" and result["records"] == []


def test_stale_fallback_and_offline_miss(monkeypatch, tmp_path):
    monkeypatch.setattr(sector_hot, "_request", lambda *a, **k: pytest.fail("offline network"))
    assert sector_hot.get_sector_hot_rank(offline=True)["status"] == "unavailable"
    monkeypatch.setattr(sector_hot, "_request", lambda *a, **k: payload())
    result = sector_hot.get_sector_hot_rank()
    path = result["source"]["cache_path"]
    entry = json.loads(open(path, encoding="utf-8").read())
    # Force an expired observation inside the fallback window, deterministically.
    from datetime import datetime, timedelta, timezone
    entry["fetched_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f)
    monkeypatch.setattr(sector_hot, "_request", lambda *a, **k: {"status_code": 1})
    result = sector_hot.get_sector_hot_rank()
    assert result["source"]["stale"] and len(result["records"]) == 1
    assert any("过期" in warning for warning in result["warnings"])


async def test_mcp_registration_validation_and_envelope(monkeypatch):
    from stock_analysis_mcp import server
    monkeypatch.setattr(sector_hot, "_request", lambda *a, **k: payload())
    value = json.loads((await server.call_tool("get_sector_hot_rank", {}))[0].text)
    assert value["data"]["records"][0]["heat_value"] == 100
    assert value["warnings"]
    assert server._validate_arguments("get_sector_hot_rank", {"limit": 21},
                                      server.TOOL_HANDLERS["get_sector_hot_rank"]["schema"])


@pytest.mark.parametrize("limit", [0, 21, True, 1.5])
def test_invalid_limits(limit):
    with pytest.raises(ValueError):
        sector_hot.get_sector_hot_rank(limit=limit)
