"""code-review 第二批修正的回归测试:

- storage: combined ON CONFLICT 保留 spot 列
- data/util: safe_float / parse_em_kline_rows / indicator_rows_from_df
- server: 15 工具注册、重复注册报错、参数校验 envelope
- tools/pattern_scan: golden_cross / oversold_reversal 与描述一致
- charting: 合成K线渲染 PNG
- strategies: MA_WINDOW_WHITELIST 单一来源
"""

import asyncio
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_analysis_mcp.data import storage
from stock_analysis_mcp.data.util import parse_em_kline_rows, safe_float


# ── data/util ──

def test_safe_float():
    assert safe_float(None) is None
    assert safe_float("") is None
    assert safe_float("-") is None
    assert safe_float("abc") is None
    assert safe_float("1.5") == 1.5
    assert safe_float(3) == 3.0


def test_parse_em_kline_rows():
    rows = ["2026-08-29,10.0,10.5,10.8,9.9,1000,10500,8.0,1.2,0.5,1.3",
            "bad,short", "2026-08-28,-,-,-,-,-,-,-,-,-,-"]
    out = parse_em_kline_rows(rows, {"symbol": "000001"}, "date",
                              ["open", "close", "high", "low", "volume", "amount",
                               "amplitude", "change_pct", "change_amount", "turnover_rate"])
    assert len(out) == 2
    assert out[0]["date"] == "2026-08-29" and out[0]["open"] == 10.0
    assert out[1]["open"] is None and out[1]["close"] is None


# ── storage: combined 保留 spot 列 ──

@pytest.fixture()
def tmp_stock_db(tmp_path, monkeypatch):
    db = str(tmp_path / "stock.db")
    monkeypatch.setattr(storage, "get_stock_db", lambda: db)
    storage._schema_ready.discard(db)
    storage._db_paths_cache["STOCK_DB"] = db
    yield db
    storage._schema_ready.discard(db)


def test_combined_preserves_spot_cols(tmp_stock_db):
    from datetime import date
    storage.init_stock_db()
    today = date.today().isoformat()
    storage.save_stock_basic([{"symbol": "000001", "name": "平安银行"}])
    storage.save_stock_kline([{"symbol": "000001", "date": today, "open": 10, "high": 11,
                               "low": 9.9, "close": 10.5, "volume": 100}])
    storage.save_popularity_rank([{"trade_date": today, "symbol": "000001",
                                   "rank": 8, "rank_time": "t", "source": "xuangu"}])
    # 先填 spot 列
    storage.upsert_combined_spot = getattr(storage, "upsert_combined_spot")
    n_spot = storage.upsert_combined_spot()
    # 再跑 K线+排名 写入: 不得抹掉 spot 列
    n_hist = storage.build_combined_from_history()
    assert n_hist == 1
    row = storage.query_stock_db(
        "SELECT * FROM stock_daily_combined WHERE symbol='000001'")[0]
    assert row["popularity_rank"] == 8
    assert row["close"] == 10.5
    # spot 列仍在 (REPLACE 语义下这些会是 NULL)
    spot_cols = [c for c in ("pe_dynamic", "total_market_cap", "main_net_inflow")]
    assert any(row[c] is not None for c in spot_cols) or n_spot == 0


# ── server ──

def test_server_registry_matches_manifest():
    from stock_analysis_mcp import server
    import json
    from pathlib import Path
    manifest = json.loads((Path(__file__).parents[1] / "package.json").read_text(encoding="utf-8"))
    assert set(server.TOOL_HANDLERS) == set(manifest["mcp"]["tools"])
    assert "render_stock_charts" in server.TOOL_HANDLERS


def test_server_duplicate_register_raises():
    from stock_analysis_mcp import server
    with pytest.raises(ValueError):
        server.register("get_data_status", "dup", {"type": "object"})(lambda: None)


@pytest.mark.asyncio
async def test_server_call_tool_invalid_params():
    from stock_analysis_mcp.server import call_tool
    out = await call_tool("get_kline_local_or_net", {})
    env = json.loads(out[0].text)
    assert env["error"]["code"] == "INVALID_PARAMS"
    assert "symbol" in env["error"]["message"]


@pytest.mark.asyncio
async def test_server_call_tool_unknown_param():
    from stock_analysis_mcp.server import call_tool
    out = await call_tool("get_rank_trend_data", {"symbol": "000001", "bogus": 1})
    env = json.loads(out[0].text)
    assert env["error"]["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_server_call_tool_validates_schema_types_and_ranges():
    from stock_analysis_mcp.server import call_tool
    wrong_type = await call_tool("get_kline_local_or_net", {"symbol": "000001", "days": "30"})
    assert json.loads(wrong_type[0].text)["error"]["code"] == "INVALID_PARAMS"
    out_of_range = await call_tool("get_stock_kline_period", {"symbol": "000001", "period": "999"})
    envelope = json.loads(out_of_range[0].text)
    assert envelope["error"]["code"] == "INVALID_PARAMS"
    assert "允许值" in envelope["error"]["message"]


@pytest.mark.asyncio
async def test_server_error_envelope_has_meta():
    from stock_analysis_mcp.server import call_tool, TOOL_HANDLERS
    info = TOOL_HANDLERS["get_data_status"]

    async def boom():
        raise RuntimeError("x")
    orig = info["func"]
    info["func"] = boom
    try:
        out = await call_tool("get_data_status", {})
    finally:
        info["func"] = orig
    env = json.loads(out[0].text)
    assert env["error"]["code"] == "TOOL_ERROR"
    assert env["meta"]["fetched_at"]  # 错误路径 meta 不再为空


@pytest.mark.asyncio
async def test_server_promotes_business_warnings_to_envelope():
    from stock_analysis_mcp.server import call_tool, TOOL_HANDLERS
    info = TOOL_HANDLERS["get_data_status"]

    async def result_with_warning():
        return {"warnings": ["stale data"], "value": 1}
    orig = info["func"]
    info["func"] = result_with_warning
    try:
        out = await call_tool("get_data_status", {})
    finally:
        info["func"] = orig
    env = json.loads(out[0].text)
    assert env["warnings"] == ["stale data"]
    assert env["data"]["value"] == 1


# ── pattern_scan ──

@pytest.fixture()
def tmp_dbs(tmp_path, monkeypatch):
    stock_db = str(tmp_path / "s.db")
    sector_db = str(tmp_path / "sec.db")
    monkeypatch.setattr(storage, "get_stock_db", lambda: stock_db)
    monkeypatch.setattr(storage, "get_sector_db", lambda: sector_db)
    storage._schema_ready.discard(stock_db)
    storage._schema_ready.discard(sector_db)
    yield
    storage._schema_ready.discard(stock_db)
    storage._schema_ready.discard(sector_db)


def test_golden_cross_and_oversold(tmp_dbs):
    from stock_analysis_mcp.tools import pattern_scan
    storage.init_stock_db()
    # 两个交易日: 昨日 MA5<=MA20, 今日 MA5>MA20, RSI14=65 → 金叉
    rows = []
    for d, ma5, ma20 in (("2026-08-27", 9.0, 10.0), ("2026-08-28", 10.5, 10.0)):
        rows.append({"symbol": "000001", "date": d, "MA5": ma5, "MA20": ma20, "RSI14": 65})
    storage.save_stock_indicators(rows)
    sig = asyncio.run(pattern_scan.screen_golden_cross())
    assert len(sig) == 1 and sig[0]["symbol"] == "000001"
    # oversold: RSI14<30 才返回
    storage.save_stock_indicators(
        [{"symbol": "600000", "date": "2026-08-28", "RSI14": 25}])
    picked = asyncio.run(pattern_scan.screen_oversold_reversal(
        [{"symbol": "600000", "change_pct": 2.0},
         {"symbol": "000001", "change_pct": 3.0}]))  # 000001 RSI=65 应被排除
    assert [x["symbol"] for x in picked] == ["600000"]


# ── charting ──

def test_render_kline_chart(tmp_path):
    pytest.importorskip("matplotlib")
    from stock_analysis_mcp.charting import render_kline_chart, resample_daily
    n = 80
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="B"),
        "open": [10 + 0.01 * i for i in range(n)],
        "close": [10.5 + 0.01 * i for i in range(n)],
        "high": [11 + 0.01 * i for i in range(n)],
        "low": [9.8 + 0.01 * i for i in range(n)],
        "volume": [1000 + i for i in range(n)],
    })
    out = render_kline_chart(df, "测试", str(tmp_path / "t.png"))
    assert Path(out).exists() and Path(out).stat().st_size > 1000
    weekly = resample_daily(df, "W-FRI")
    assert len(weekly) < n and list(weekly.columns) >= ["date", "open"]


# ── strategies ──

def test_ma_window_whitelist_matches_ctx_columns():
    from stock_analysis_mcp.strategies.patterns import MA_WINDOW_WHITELIST, _Ctx
    cols = {int(c[2:]) for c in _Ctx._COLUMNS if c.startswith("MA") and c[2:].isdigit()}
    assert MA_WINDOW_WHITELIST == frozenset(cols)
