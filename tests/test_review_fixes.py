"""回归测试: code-review 修复项的行为锁定。"""

import os
import sys
from pathlib import Path

import pytest

from stock_analysis_mcp.data import network
from stock_analysis_mcp.core import config


# ── 熔断器: 空数据不应计入网络失败 ──

@pytest.fixture(autouse=True)
def _reset_health():
    network.mark_provider_ok(network.EM_KLINE)
    network._provider_fail_count.pop(network.EM_KLINE, None)
    network._provider_cooldown_until.pop(network.EM_KLINE, None)
    yield
    network.mark_provider_ok(network.EM_KLINE)


def test_em_kline_empty_data_does_not_trip_breaker(monkeypatch):
    """host 返回 HTTP 200 但 data 为空(退市股) → 返回 None 且不熔断。"""
    monkeypatch.setattr(network, "http_get",
                        lambda *a, **k: {"code": 0, "data": None})
    for _ in range(5):
        assert network.fetch_em_kline({"secid": "1.600000"}) is None
    assert network.provider_available(network.EM_KLINE)


def test_em_kline_network_failure_trips_breaker(monkeypatch):
    """host 连接失败(http_get 返回 None) → 连续 3 次进入冷却。"""
    monkeypatch.setattr(network, "http_get", lambda *a, **k: None)
    for _ in range(3):
        assert network.fetch_em_kline({"secid": "1.600000"}) is None
    assert not network.provider_available(network.EM_KLINE)
    # 冷却期内不发请求
    monkeypatch.setattr(network, "http_get",
                        lambda *a, **k: {"code": 0, "data": {"klines": ["x"]}})
    assert network.fetch_em_kline({"secid": "1.600000"}) is None


def test_em_kline_success_resets(monkeypatch):
    monkeypatch.setattr(network, "http_get",
                        lambda *a, **k: {"code": 0, "data": {"klines": ["x"]}})
    assert network.fetch_em_kline({"secid": "1.600000"}) == {"code": 0, "data": {"klines": ["x"]}}
    assert network.provider_available(network.EM_KLINE)


# ── config: 空字符串环境变量回退 default ──

def test_empty_env_vars_fall_back(monkeypatch, tmp_path):
    monkeypatch.setenv("EASTMONEY_DATA_DIR", "")
    monkeypatch.setenv("EASTMONEY_STOCK_DATA_DIR", "")
    monkeypatch.setenv("EASTMONEY_SECTOR_DATA_DIR", "")
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "nonexistent.toml")
    s = config.get_settings()
    assert s.data_root != Path("")            # 不能落到当前目录
    assert s.stock_dir == s.data_root / "股票信息"
    assert s.sector_dir == s.data_root / "分析板块"


def test_toml_inline_comment_stripped(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('data_root = "D:/quant/data" # 主数据目录\n', encoding="utf-8")
    monkeypatch.setattr(config, "config_path", lambda: cfg)
    monkeypatch.delenv("EASTMONEY_DATA_DIR", raising=False)
    monkeypatch.delenv("EASTMONEY_STOCK_DATA_DIR", raising=False)
    monkeypatch.delenv("EASTMONEY_SECTOR_DATA_DIR", raising=False)
    s = config.get_settings()
    assert s.data_root == Path("D:/quant/data")   # 不带 " # 主数据目录"


@pytest.mark.asyncio
async def test_spot_paging_returns_rows_without_unpacking(monkeypatch):
    from stock_analysis_mcp.tools import stock_data

    monkeypatch.setattr(
        stock_data, "_spot_page_sync",
        lambda host, page: {"data": {"diff": [{"f12": "000001"}], "total": 1}},
    )
    rows = await stock_data._spot_all_rows("https://example.invalid")
    assert rows == [{"f12": "000001"}]


def test_large_sector_filter_does_not_duplicate_bindings(monkeypatch):
    from stock_analysis_mcp.data import search, storage

    monkeypatch.setattr(
        storage, "query_sector_db",
        lambda *args: [{"stock_code": f"{i:06d}"} for i in range(901)],
    )
    calls = []

    def query(sql, params=()):
        calls.append((sql.count("?"), len(params)))
        return []

    monkeypatch.setattr(search, "query_stock_db", query)
    search.screen_stocks_local(sector_code="BK0001")
    assert calls and all(placeholders == bound for placeholders, bound in calls)


@pytest.mark.asyncio
async def test_incremental_sync_only_processes_stale_symbols(monkeypatch):
    from datetime import date, timedelta
    from stock_analysis_mcp.data import sync

    today = date.today()
    stale_date = today - timedelta(days=3)
    query_count = 0

    def query(sql, params=()):
        nonlocal query_count
        query_count += 1
        if query_count == 1:
            return [
                {"symbol": "current", "last_date": today.isoformat(),
                 "status": "ready", "row_count": 320},
                {"symbol": "stale", "last_date": stale_date.isoformat(),
                 "status": "ready", "row_count": 320},
            ]
        return [
            {"symbol": symbol, "last_date": today.isoformat(),
             "status": "ready", "row_count": 320}
            for symbol in ("current", "stale")
        ]

    captured = []

    async def download(symbol, **kwargs):
        captured.append((symbol, kwargs))
        return {"status": "ok", "symbol": symbol, "count": 5}

    monkeypatch.setattr(sync, "query_stock_db", query)
    monkeypatch.setattr(sync, "_download_one_kline", download)
    monkeypatch.setattr("stock_analysis_mcp.data.quality.latest_completed_trade_day", lambda **k: today.isoformat())
    result = await sync.sync_stock_kline_universe(
        ["current", "stale"], target_bars=320,
        resume=False, incremental=True,
    )
    assert result["processed"] == 1
    assert captured[0][0] == "stale"
    assert captured[0][1]["start_date"] == (stale_date - timedelta(days=10)).isoformat()


@pytest.mark.asyncio
async def test_incremental_empty_fetch_preserves_existing_coverage(monkeypatch):
    from stock_analysis_mcp.data import sync
    from stock_analysis_mcp.tools import stock_data

    async def empty_history(*args, **kwargs):
        return []

    writes = []
    monkeypatch.setattr(stock_data, "get_stock_history", empty_history)
    monkeypatch.setattr(sync, "save_data_coverage", lambda *a, **k: writes.append((a, k)))
    result = await sync._download_one_kline(
        "000001", adjust="qfq", start_date="2026-09-01")
    assert result["status"] == "empty"
    assert writes == []


# ── CLI: pattern-backtest 透传失败返回码 ──

def test_cli_backtest_failure_returns_nonzero(monkeypatch):
    from stock_analysis_mcp import cli

    def boom(argv):
        raise RuntimeError("cache missing")
    monkeypatch.setattr("stock_analysis_mcp.strategies.pattern_backtest.main", boom)
    rc = cli.main(["pattern-backtest", "--cache", "x.csv"])
    assert rc == 1


def test_cli_backtest_help_exit_code(monkeypatch):
    from stock_analysis_mcp import cli

    def _help(argv):
        raise SystemExit(0)
    monkeypatch.setattr("stock_analysis_mcp.strategies.pattern_backtest.main", _help)
    assert cli.main(["pattern-backtest"]) == 0
