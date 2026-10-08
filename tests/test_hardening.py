"""Regressions for data consistency, SQL chunking, execution and SDK protocols."""
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stock_analysis_mcp.data import storage
from stock_analysis_mcp.data.quality import PriceBasisChanged


@pytest.fixture
def databases(tmp_path):
    storage.set_db_paths(str(tmp_path / "stock.db"), str(tmp_path / "sector.db"))
    storage.init_all()
    yield tmp_path
    storage.set_db_paths(None, None)


def bar(day="2026-09-01", close=10, source="tencent", adjust="qfq"):
    return {"symbol":"000001", "date":day, "open":close, "close":close,
            "high":close + 1, "low":close - 1, "volume":100000,
            "source":source, "adjust_type":adjust}


def test_adjusted_provider_does_not_fall_back_to_raw(monkeypatch):
    from stock_analysis_mcp.data.providers import tencent
    monkeypatch.setattr(tencent, "http_get", lambda *a, **k: {
        "data":{"sz000001":{"day":[["2026-09-01","10","10","11","9","100"]]}}})
    assert tencent._daily_like("sz000001", "000001", "day", "qfq", 2, None, None) == []


def test_adjusted_minute_provider_declines_unsupported_request(monkeypatch):
    from stock_analysis_mcp.data.providers import tencent
    monkeypatch.setattr(tencent, "provider_ready", lambda: True)
    monkeypatch.setattr(tencent, "http_get", lambda *a, **k: pytest.fail("raw minute call"))
    assert tencent.fetch_stock_kline("000001", klt="5", adjust="hfq") == []


def test_provider_rows_outside_requested_window_are_dropped(monkeypatch):
    from stock_analysis_mcp.data.providers import tencent
    monkeypatch.setattr(tencent, "http_get", lambda *a, **k: {"data":{"sz000001":{"day":[
        ["2026-09-30","10","10","11","9","100"],
        ["2026-10-08","10","10","11","9","100"]]}}})
    rows = tencent._daily_like("sz000001","000001","day","",10,None,"20260930")
    assert [r["date"] for r in rows] == ["2026-09-30"]


def test_sohu_cannot_satisfy_adjusted_request(monkeypatch):
    from stock_analysis_mcp.tools import stock_data
    from stock_analysis_mcp.data.providers import tencent, sohu
    monkeypatch.setattr(tencent, "fetch_stock_kline", lambda *a, **k: [])
    monkeypatch.setattr(stock_data, "_akshare_history", lambda *a: None)
    monkeypatch.setattr(sohu, "fetch_stock_kline_daily", lambda *a, **k: pytest.fail("raw fallback"))
    assert stock_data._stock_history_sync("000001", "20260101", "20260901", "hfq") == []


@pytest.mark.parametrize("incoming", [bar(close=20), bar(source="sina"), bar(day="2026-09-02")])
def test_incompatible_price_append_keeps_original(databases, incoming):
    storage.save_stock_kline([bar()])
    with pytest.raises(PriceBasisChanged):
        storage.save_stock_kline([incoming])
    assert storage.query_stock_db("SELECT close FROM stock_kline")[0]["close"] == 10


def test_complete_replacement_archives_and_invalidates_indicators(databases):
    import zlib
    from stock_analysis_mcp.data.storage.writer import replace_stock_history
    storage.save_stock_kline([bar()])
    storage.save_stock_indicators([{**bar(), "MA5": 10}])
    batch = replace_stock_history("000001", "qfq", [bar(close=20, source="sina")], reason="test")
    assert storage.query_stock_db("SELECT close FROM stock_kline")[0]["close"] == 20
    assert storage.query_stock_db("SELECT * FROM stock_indicators") == []
    blob = storage.query_stock_db("SELECT history_zlib FROM kline_repair_archive WHERE batch_id=?", (batch,))[0]
    assert json.loads(zlib.decompress(blob["history_zlib"]))[0]["close"] == 10
    from stock_analysis_mcp.data.restore import restore_history
    assert restore_history(batch)["rows"] == 1
    assert storage.query_stock_db("SELECT close FROM stock_kline")[0]["close"] == 10
    assert len(storage.query_stock_db("SELECT * FROM kline_repair_archive")) == 2


def test_truncated_replacement_rejected(databases):
    from stock_analysis_mcp.data.storage.writer import replace_stock_history
    storage.save_stock_kline([bar()])
    with pytest.raises(ValueError, match="truncate"):
        replace_stock_history("000001", "qfq", [bar(day="2026-09-02")], reason="test")
    assert len(storage.query_stock_db("SELECT * FROM stock_kline")) == 1


def test_schema_restart_preserves_failed_coverage(databases):
    storage.save_stock_kline([bar()])
    storage.save_data_coverage("stock_kline", "000001", adjust_type="qfq", status="failed", last_error="basis")
    storage._schema_ready.discard(storage.get_stock_db())
    assert storage.query_stock_db("SELECT status FROM data_coverage")[0]["status"] == "failed"


def test_large_sector_filters_and_parameter_count(databases):
    from stock_analysis_mcp.data.search import screen_stocks_local
    members = [{"sector_code":"BK0001", "stock_code":str(i).zfill(6)} for i in range(901)]
    storage.save_sector_member(members)
    storage.save_stock_spot([{"symbol":"000000", "name":"cheap", "latest_price":1},
                             {"symbol":"000900", "name":"expensive", "latest_price":20}])
    rows = screen_stocks_local({"min_price":10}, sector_code="BK0001", sort_by="latest_price")
    assert [r["symbol"] for r in rows] == ["000900"]


def test_calendar_holiday_and_intraday(monkeypatch):
    from stock_analysis_mcp.data import quality
    monkeypatch.setattr(quality, "cached_trade_dates", lambda: {"2026-09-30","2026-10-09"})
    now = datetime(2026,10,8,14,tzinfo=ZoneInfo("Asia/Shanghai"))
    assert quality.latest_completed_trade_day(now=now) == "2026-09-30"
    now = datetime(2026,10,9,14,tzinfo=now.tzinfo)
    assert quality.latest_completed_trade_day(now=now) == "2026-09-30"
    assert quality.latest_completed_trade_day(now=now.replace(hour=16)) == "2026-10-09"


def test_expired_calendar_cannot_certify_freshness(monkeypatch):
    from stock_analysis_mcp.data import quality
    monkeypatch.setattr(quality, "cached_trade_dates", lambda: {"2025-12-31"})
    assert quality.latest_completed_trade_day(now=datetime(2026,10,8)) is None


@pytest.mark.parametrize("args,schema", [
    ({"extra":1}, {"properties":{}}),
    ({"symbols":[1]}, {"properties":{"symbols":{"type":"array","items":{"type":"string"}}}}),
    ({"conditions":{"misspelled":2}}, {"properties":{"conditions":{"type":"object","properties":{"min_price":{"type":"number"}}}}}),
    ({"score":float("nan")}, {"properties":{"score":{"type":"number"}}}),
    ([], {"properties":{}}),
])
def test_recursive_argument_validation(args, schema):
    from stock_analysis_mcp.server import _validate_arguments
    assert _validate_arguments("test", args, schema)


def test_hold_period_drawdown_visible(monkeypatch):
    from stock_analysis_mcp.strategies.trading_backtest import _portfolio_summary
    from stock_analysis_mcp.data import quality
    monkeypatch.setattr(quality, "cached_trade_dates", lambda: {"2026-09-01","2026-09-02","2026-09-03"})
    trade = {"symbol":"000001", "entry_date":"2026-09-01", "exit_date":"2026-09-03", "net_return":.1,
             "marks":[{"date":"2026-09-01","net_value":1},
                      {"date":"2026-09-02","net_value":.5},
                      {"date":"2026-09-03","net_value":1.1}]}
    result = _portfolio_summary([trade])
    assert result["max_drawdown"] == -.05
    assert len(result["equity_series"]) == 3
    assert result["total_return"] == .01


def test_gap_stop_fills_at_open(monkeypatch):
    from stock_analysis_mcp.strategies import trading_backtest as module
    hist = pd.DataFrame([{"date":"2026-09-01","open":10,"close":10,"high":10.1,"low":9.9}]*14)
    future = pd.DataFrame([{"date":"2026-09-01","open":10,"close":10,"high":10.1,"low":9.9,"volume":100000},
                           {"date":"2026-09-02","open":10,"close":10,"high":10.1,"low":9.9,"volume":100000},
                           {"date":"2026-09-03","open":8,"close":8.5,"high":9,"low":7.5,"volume":100000}])
    monkeypatch.setattr(module, "prepare_df", lambda *a, **k: hist if "tail" in k else future)
    result = module._simulate_trade({"symbol":"000001","date":"2026-09-01","pattern":"w_bottom"}, 0, 0, slippage_bps=0)
    assert result["exit"] == 8
    assert result["exit_reason"] == "stop"


def test_limit_down_defers_time_exit(monkeypatch):
    from stock_analysis_mcp.strategies import trading_backtest as module
    hist = pd.DataFrame([{"date":"2026-09-01","open":10,"close":10,"high":10.1,"low":9.9}]*14)
    future = pd.DataFrame([{"date":"2026-09-01","open":10,"close":10,"high":10.1,"low":9.9,"change_rate":0,"volume":100000},
                           {"date":"2026-09-02","open":10,"close":10,"high":10.1,"low":9.9,"change_rate":0,"volume":100000},
                           {"date":"2026-09-03","open":9,"close":9,"high":9,"low":9,"change_rate":-10,"volume":100000},
                           {"date":"2026-09-04","open":8.9,"close":9,"high":9.1,"low":8.8,"change_rate":0,"volume":100000}])
    monkeypatch.setattr(module, "prepare_df", lambda *a, **k: hist if "tail" in k else future)
    result = module._simulate_trade({"symbol":"000001","date":"2026-09-01","pattern":"w_bottom"}, 0, 0, max_holding_days=2, slippage_bps=0)
    assert result["exit_date"] == "2026-09-04"


def test_atomic_alert_replay_is_idempotent(databases):
    from stock_analysis_mcp.alerts.service import evaluate_and_store
    from stock_analysis_mcp.alerts.models import ZoneRule, PriceUpdate
    rule = ZoneRule("z","000001","101",9,11)
    update = PriceUpdate("2026-09-01T16:00:00",10,11,9,True)
    first = evaluate_and_store(rule, update)
    second = evaluate_and_store(rule, update)
    assert first and not second
    assert len(storage.query_stock_db("SELECT * FROM alert_events")) == len(first)


@pytest.mark.asyncio
async def test_node_stdio_initialization_and_tools(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env = {**os.environ, "STOCK_ANALYSIS_PYTHON":sys.executable,
           "STOCK_ANALYSIS_STOCK_DATA_DIR":str(tmp_path / "stocks"),
           "STOCK_ANALYSIS_SECTOR_DATA_DIR":str(tmp_path / "sectors")}
    root = Path(__file__).resolve().parents[1]
    async def run():
        async with stdio_client(StdioServerParameters(command="node", args=[str(root / "index.js")], env=env)) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                result = await client.list_tools()
                expected = json.loads((root / "package.json").read_text())["mcp"]["tools"]
                assert {t.name for t in result.tools} == set(expected)
                result = await client.call_tool("get_data_status", {"unexpected":1})
                envelope = json.loads(result.content[0].text)
                assert envelope["error"]["code"] == "INVALID_PARAMS"
    await asyncio.wait_for(run(), timeout=25)


def test_rule_proposal_requires_separate_approval(tmp_path, monkeypatch):
    from stock_analysis_mcp.strategies.rule_registry import propose, activate, active_filters
    path = tmp_path / "rules.json"
    filters = {"w_bottom":{"score":[.8,None]}}
    version = propose(path, filters, evidence="report.md")
    monkeypatch.setenv("STOCK_ANALYSIS_RULE_REGISTRY", str(path))
    assert active_filters("2026-09-30") is None
    with pytest.raises(ValueError):
        activate(path, version, approved_by="", effective_date="2026-10-09")
    activate(path, version, approved_by="reviewer", effective_date="2026-10-09")
    assert active_filters("2026-10-08") is None
    assert active_filters("2026-10-09") == filters
