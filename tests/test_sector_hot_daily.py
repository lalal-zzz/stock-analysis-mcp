from datetime import datetime
from types import SimpleNamespace

import pytest

from stock_analysis_mcp.data import sector_hot, sector_hot_daily as daily


@pytest.fixture
def setup(tmp_path, monkeypatch):
    clock = [datetime.fromisoformat("2026-10-19T14:30:00+08:00")]
    monkeypatch.setattr(daily, "get_settings", lambda: SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(daily, "_now", lambda: clock[0])
    monkeypatch.setattr(daily, "cached_trade_dates", lambda: {"2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"})
    monkeypatch.setattr(sector_hot, "_load", lambda: [{"sector_code": "886069", "heat_value": 100., "heat_rank": 3}])
    return clock


def test_exact_previous_trading_day_not_calendar_day(setup, monkeypatch):
    clock = setup
    clock[0] = datetime.fromisoformat("2026-10-16T14:30:00+08:00")
    daily.capture_sector_hot_1430()
    clock[0] = datetime.fromisoformat("2026-10-19T14:30:05+08:00")
    monkeypatch.setattr(sector_hot, "_load", lambda: [{"sector_code": "886069", "heat_value": 120., "heat_rank": 1}])
    result = daily.capture_sector_hot_1430()
    assert result["comparison"]["previous_trade_date"] == "2026-10-16"
    assert result["records"][0]["heat_delta"] == 20
    assert result["records"][0]["rank_improvement"] == 2
    assert result["records"][0]["heat_change_pct"] == pytest.approx(20)
    monkeypatch.setattr(sector_hot, "_load", lambda: pytest.fail("duplicate capture"))
    assert daily.capture_sector_hot_1430()["records"] == result["records"]


def test_missing_previous_does_not_fall_back(setup):
    daily.capture_sector_hot_1430()
    setup[0] = datetime.fromisoformat("2026-10-21T14:30:00+08:00")
    result = daily.capture_sector_hot_1430()
    assert result["comparison"]["previous_trade_date"] == "2026-10-20"
    assert result["records"][0]["heat_delta"] is None
    assert daily.get_sector_hot_1430("2026-10-20")["status"] == "unavailable"


@pytest.mark.parametrize("stamp", ["2026-10-19T14:29:59+08:00", "2026-10-19T14:35:00+08:00", "2026-10-19T22:00:00+08:00", "2026-10-18T14:30:00+08:00"])
def test_outside_window_or_non_trading_day_no_network(setup, monkeypatch, stamp):
    setup[0] = datetime.fromisoformat(stamp)
    monkeypatch.setattr(sector_hot, "_load", lambda: pytest.fail("must not fetch"))
    assert daily.capture_sector_hot_1430()["status"] == "skipped"


def test_slow_request_cannot_fill_slot(setup, monkeypatch):
    def fetch():
        setup[0] = datetime.fromisoformat("2026-10-19T14:35:01+08:00")
        return [{"sector_code": "886069", "heat_value": 100, "heat_rank": 1}]
    monkeypatch.setattr(sector_hot, "_load", fetch)
    assert daily.capture_sector_hot_1430()["status"] == "unavailable"
    assert daily.get_sector_hot_1430("2026-10-19")["records"] == []


def test_mcp_read_mode_never_fetches(setup, monkeypatch):
    monkeypatch.setattr(sector_hot, "_load", lambda: pytest.fail("historical read network"))
    result = sector_hot.get_sector_hot_rank(comparison="daily_1430", trade_date="2026-10-19")
    assert result["status"] == "unavailable"


def test_cli_capture_and_date_are_mutually_exclusive(setup):
    from stock_analysis_mcp.cli import main
    assert main(["sector-hot-1430", "--capture", "--date", "2026-10-19"]) == 2
