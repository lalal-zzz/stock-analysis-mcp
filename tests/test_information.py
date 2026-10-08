"""Traceability, bounded requests, cache fallback and financial comparability."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from stock_analysis_mcp.data import information as info


@pytest.fixture(autouse=True)
def isolated_information(tmp_path, monkeypatch):
    monkeypatch.setattr(info, "get_settings", lambda: SimpleNamespace(data_root=tmp_path, stock_dir=tmp_path, sector_dir=tmp_path))


def test_offline_never_calls_loader():
    rows, state = info._cached("missing", lambda: pytest.fail("offline request"), offline=True)
    assert rows == [] and state["status"] == "unavailable"


def test_cached_timestamp_and_failure_fallback(monkeypatch):
    rows, state = info._cached("feed", lambda: [{"title": "original"}], ttl=0)
    fetched = state["fetched_at"]
    original_now = info._now()
    monkeypatch.setattr(info, "_now", lambda: original_now + timedelta(hours=1))
    def unavailable():
        raise RuntimeError("source down")
    rows, state = info._cached("feed", unavailable, ttl=0)
    assert rows[0]["title"] == "original" and state["stale"]
    assert state["fetched_at"] == fetched


def test_parse_rss_atom_and_dates():
    rss = '<rss><channel><item><title>A &amp; B</title><link>https://example.com/a</link><pubDate>Tue, 06 Oct 2026 14:00:00 GMT</pubDate></item></channel></rss>'
    assert info.parse_feed(rss, "fed")[0]["published_at"] == "2026-10-06T14:00:00+00:00"
    atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>ECB</title><link href="https://example.com/b"/><updated>2026-10-06T14:00:00Z</updated></entry></feed>'
    assert info.parse_feed(atom, "ecb")[0]["url"] == "https://example.com/b"
    with pytest.raises(ValueError):
        info.parse_feed("<html>maintenance</html>", "fed")
    assert info.timestamp("2026-10-06 14:00:00", chinese=True) == "2026-10-06T06:00:00+00:00"
    assert info.timestamp("2026-10-06 14:00:00") is None


def test_jsonp_is_parsed_without_execution(monkeypatch):
    payload = {"result": {"cmsArticleWebOld": [{"title": "<em>银行</em>", "code": "1234", "date": "2026-10-06 14:00:00", "content": "摘要"}]}}
    monkeypatch.setattr(info, "_request", lambda *a, **k: "stockAnalysis(" + json.dumps(payload) + ");")
    rows = info.fetch_search("银行", 10)
    assert rows[0]["title"] == "银行" and rows[0]["url"].endswith("1234.html")
    monkeypatch.setattr(info, "_request", lambda *a, **k: "stockAnalysis({}); malicious();")
    with pytest.raises(ValueError):
        info.fetch_search("银行", 10)


def test_news_dedup_keeps_sources_and_drops_unknown_dates():
    now = info._now().isoformat()
    row = {"title": "same event", "summary": "summary", "published_at": now, "url": "https://a.test/1", "source": "fed", "publisher": "FED", "source_kind": "official_release"}
    second = {**row, "url": "https://b.test/2", "source": "ecb"}
    report = info._news_result([row, second, {**row, "published_at": None}], [{"status": "stale"}], days=1, limit=10, query="")
    assert len(report["items"]) == 1 and len(report["items"][0]["references"]) == 2
    assert report["status"] == "partial" and len(report["warnings"]) == 3


def test_financial_periods_and_missing_values(monkeypatch):
    def request(url, **kwargs):
        assert kwargs["params"]["filter"] == '(SECUCODE="600519.SH")'
        return {"result": {"data": [{"REPORT_DATE": "2026-06-30", "EPSJB": "3.5", "ROEJQ": "--"}]}}
    monkeypatch.setattr(info, "_request", request)
    row = info.fetch_financials("600519", 8)[0]
    assert row["eps"] == 3.5 and row["roe_pct"] is None
    assert row["period_basis"] == "year_to_date" and row["published_at"] is None


def test_peer_comparison_never_compares_different_periods(monkeypatch):
    def cached(key, loader, **kwargs):
        day = "2026-06-30" if "600519" in key else "2025-12-31"
        return [{"report_date": day}], {"status": "ok"}
    monkeypatch.setattr(info, "_cached", cached)
    result = info.financial_analysis("600519", peers=["000001"])
    assert result["peer_comparison"][1]["report"] is None


def test_global_sources_requested_only_once(monkeypatch):
    calls = []
    def cached(key, loader, **kwargs):
        calls.append(key)
        return [], {"status": "unavailable"}
    monkeypatch.setattr(info, "_cached", cached)
    assert info.global_market_news()["status"] == "partial"
    assert len(calls) == 3 and len(set(calls)) == 3


def test_stock_membership_queries_preserve_provenance(tmp_path, monkeypatch):
    import sqlite3
    with sqlite3.connect(tmp_path / "stock_data.db") as conn:
        conn.executescript("CREATE TABLE stock_basic(symbol TEXT,name TEXT); INSERT INTO stock_basic VALUES('600519','贵州茅台');")
    with sqlite3.connect(tmp_path / "sector_data.db") as conn:
        conn.executescript("CREATE TABLE sector_member(sector_code TEXT,stock_code TEXT,updated_date TEXT);"
            "CREATE TABLE sector_basic(sector_code TEXT,sector_name TEXT);"
            "INSERT INTO sector_member VALUES('BK0001','600519','2026-09-30');"
            "INSERT INTO sector_basic VALUES('BK0001','白酒');")
    def cached(key, loader, **kwargs):
        return [{"title": "same", "summary": "", "published_at": info._now().isoformat(),
                 "url": "https://example.com/1", "publisher": "publisher", "source": "eastmoney", "source_kind": "news_search"}], {"status": "ok"}
    monkeypatch.setattr(info, "_cached", cached)
    result = info.stock_news("600519")
    assert result["local_memberships"][0]["updated_date"] == "2026-09-30"
    assert result["concepts"] == ["白酒"] and result["name"] == "贵州茅台"
    assert len(result["items"][0]["associations"]) == 3
    assert info.stock_news("600519", concepts=[])["concepts"] == []


async def test_all_new_mcp_tools_support_no_network_offline(monkeypatch):
    from stock_analysis_mcp import server
    monkeypatch.setattr(info, "_request", lambda *a, **k: pytest.fail("offline tool initiated network"))
    for name, args in [
        ("search_market_news", {}), ("get_global_market_news", {}),
        ("get_stock_related_news", {"symbol": "600519"}),
        ("analyze_stock_financials", {"symbol": "600519"}),
    ]:
        result = json.loads((await server.call_tool(name, {**args, "offline": True}))[0].text)
        assert result["error"] is None
        assert result["data"]["status"] == "partial" and result["warnings"]


def test_local_valuation_retains_quote_date(tmp_path, monkeypatch):
    import sqlite3
    with sqlite3.connect(tmp_path / "stock_data.db") as conn:
        conn.executescript("CREATE TABLE stock_spot(symbol TEXT,updated_date TEXT,pe_dynamic REAL,pb REAL,total_market_cap REAL,latest_price REAL);"
                           "INSERT INTO stock_spot VALUES('600519','2026-09-30',20,5,1000,123);")
    monkeypatch.setattr(info, "_cached", lambda *a, **k: ([{"report_date": "2026-06-30"}], {"status": "ok"}))
    value = info.financial_analysis("600519")["companies"][0]["local_valuation"]
    assert value["quote_date"] == "2026-09-30" and value["pe_dynamic"] == 20


@pytest.mark.integration
def test_information_sources_live():
    # Three feeds/endpoint requests plus one search, bounded; no market-wide jobs.
    for source, url in info.FEEDS.items():
        assert info.parse_feed(info._request(url, text=True), source)
    assert info.fetch_search("600519", 3)
    assert info.fetch_financials("600519", 2)
