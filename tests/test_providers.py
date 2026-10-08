"""多数据源 provider 层单元测试(全部离线, mock 网络层)"""

import pytest

from stock_analysis_mcp.data import network
from stock_analysis_mcp.data.providers import boardmap, sina, sohu, tencent


def _reset_health():
    network._provider_fail_count.clear()
    network._provider_cooldown_until.clear()


# ═══════════════════ 熔断器 ═══════════════════


def test_circuit_breaker_opens_after_consecutive_failures():
    _reset_health()
    assert network.provider_available("x")
    network.mark_provider_fail("x")
    network.mark_provider_fail("x")
    assert network.provider_available("x")  # 未达阈值
    network.mark_provider_fail("x")  # 第 3 次 → 冷却
    assert not network.provider_available("x")


def test_circuit_breaker_resets_on_success():
    _reset_health()
    network.mark_provider_fail("x")
    network.mark_provider_fail("x")
    network.mark_provider_ok("x")
    network.mark_provider_fail("x")
    network.mark_provider_fail("x")
    assert network.provider_available("x")  # 成功清零, 未连续 3 次


# ═══════════════════ 腾讯 ═══════════════════


def test_tencent_prefixed_symbol():
    assert tencent._prefixed("600000") == "sh600000"
    assert tencent._prefixed("sh600000") == "sh600000"
    assert tencent._prefixed("000001") == "sz000001"
    assert tencent._prefixed("920000") == "bj920000"
    assert tencent._prefixed("830000") == "bj830000"
    assert tencent._prefixed("abc") is None


def test_tencent_normalize_rows_field_order():
    """腾讯行顺序为 [时间, open, close, high, low, volume] — 第2列开盘第3列收盘"""
    rows = [
        ["2026-08-13", "9.160", "9.180", "9.200", "9.100", "528860.000"],
        ["2026-08-14", "9.140", "9.100", "9.170", "9.060", "436231.000"],
    ]
    items = tencent._normalize_rows(rows, "sh600000", "date")
    assert len(items) == 2
    first, second = items
    assert first["open"] == 9.160 and first["close"] == 9.180
    assert first["high"] == 9.200 and first["low"] == 9.100
    assert first["change_pct"] is None  # 首行无前收盘
    # 次行: close 9.10 vs prev 9.18
    assert second["change_amount"] == pytest.approx(-0.08, abs=1e-9)
    assert second["change_pct"] == pytest.approx(-0.8715, abs=0.001)
    assert second["amplitude"] == pytest.approx((9.17 - 9.06) / 9.18 * 100, abs=0.01)
    assert second["amount"] is None and second["turnover_rate"] is None
    assert second["symbol"] == "600000"


def test_tencent_normalize_rows_tolerates_extra_elements():
    """部分行带第 7 元素(除权信息对象), 解析不应出错"""
    rows = [
        ["2026-08-13", "9.16", "9.18", "9.20", "9.10", "528860", {"qfq": 1.0}],
        ["2026-08-14", "9.14", "9.10", "9.17", "9.06", "436231", {"qfq": 1.0}, "extra"],
    ]
    items = tencent._normalize_rows(rows, "sh600000", "date")
    assert len(items) == 2
    assert items[1]["close"] == 9.10


def test_tencent_mkline_uses_datetime_key(monkeypatch):
    payload = {"code": 0, "data": {"sh600000": {"m5": [
        ["202608141455", "9.11", "9.10", "9.11", "9.10", "6100.00", {}, "0.18"],
        ["202608141500", "9.10", "9.12", "9.13", "9.10", "17651.00", {}, "0.53"],
    ]}}}
    calls = {}

    def fake_get(url, params=None, **kw):
        calls["url"] = url
        calls["param"] = params["param"]
        return payload

    monkeypatch.setattr(tencent, "http_get", fake_get)
    items = tencent.fetch_stock_kline("600000", limit=10, klt="5", adjust="")
    assert calls["param"] == "sh600000,m5,,,10"
    assert len(items) == 2
    assert items[0]["datetime"] == "202608141455"
    assert items[0]["open"] == 9.11 and items[0]["close"] == 9.10
    assert items[1]["change_pct"] == pytest.approx((9.12 - 9.10) / 9.10 * 100, abs=0.01)


def test_tencent_daily_marks_fail_on_network_error(monkeypatch):
    monkeypatch.setattr(tencent, "http_get", lambda *a, **kw: None)
    _reset_health()
    rows = tencent.fetch_stock_kline("600000", limit=10)
    assert rows == []
    assert network._provider_fail_count.get("tencent") == 1


def test_tencent_daily_like_pages_and_trims(monkeypatch):
    """翻页逻辑: 请求 count+1 根算预收盘, 最终取最近 want 根"""
    pages = [
        {"code": 0, "data": {"sh600000": {"qfqday": [
            ["2026-08-12", "9.21", "9.17", "9.22", "9.12", "467825.0"],
            ["2026-08-13", "9.16", "9.18", "9.20", "9.10", "528860.0"],
            ["2026-08-14", "9.14", "9.10", "9.17", "9.06", "436231.0"],
        ]}}},
    ]
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params["param"])
        return pages[len(calls) - 1]

    monkeypatch.setattr(tencent, "http_get", fake_get)
    rows = tencent.fetch_stock_kline(
        "600000", limit=2, klt="101", adjust="qfq",
        start_date="20260801", end_date="20260816",
    )
    # 请求的 count = 2+1 = 3
    assert calls[0] == "sh600000,day,2026-08-01,2026-08-16,3,qfq"
    # 返回最近 2 根(最老那根作为预收盘被丢弃)
    assert [r["date"] for r in rows] == ["2026-08-13", "2026-08-14"]
    assert rows[0]["change_pct"] is not None


def test_tencent_parse_qt_text_gbk_and_units():
    fields = [""] * 45
    fields[1] = "浦发银行"
    fields[2] = "600000"
    fields[3] = "9.10"
    fields[4] = "9.18"
    fields[5] = "9.14"
    fields[30] = "20260814161455"
    fields[31] = "-0.08"
    fields[32] = "-0.87"
    fields[33] = "9.17"
    fields[34] = "9.06"
    fields[36] = "436231"
    fields[37] = "39759"
    fields[38] = "0.13"
    fields[39] = "5.92"
    text = f'v_sh600000="{"~".join(fields)}";\n'
    items = tencent._parse_qt_text(text)
    assert len(items) == 1
    item = items[0]
    assert item["name"] == "浦发银行"
    assert item["latest_price"] == 9.10
    assert item["change_pct"] == -0.87
    assert item["amount"] == pytest.approx(39759 * 10000)  # 万 → 元
    assert item["volume"] == 436231


# ═══════════════════ 新浪 ═══════════════════


def test_sina_normalize_member_units():
    r = {
        "symbol": "sh600176", "code": "600176", "name": "中国巨石",
        "trade": "44.390", "pricechange": 2.49, "changepercent": 5.943,
        "settlement": "41.900", "open": "42.400", "high": "44.620",
        "low": "41.900", "volume": 278486976, "amount": 12173014901,
        "per": 54.048, "pb": 5.504, "turnoverratio": 7.01198,
    }
    item = sina._normalize_member(r, "BK0438")
    assert item["stock_code"] == "600176"
    assert item["latest_price"] == 44.390
    assert item["volume"] == pytest.approx(2784869.76)  # 股 → 手
    assert item["turnover"] == 12173014901
    assert item["amplitude"] == pytest.approx((44.62 - 41.90) / 41.90 * 100, abs=0.01)
    assert item["volume_ratio"] is None
    assert item["sector_code"] == "BK0438"


def test_sina_normalize_member_rejects_bad_code():
    assert sina._normalize_member({"code": "60017"}, "BK1") is None
    assert sina._normalize_member({"code": "abc123"}, "BK1") is None


def test_sina_fetch_node_maps(monkeypatch):
    hy_text = ('var S_Finance_bankuai_sinaindustry = {"new_blhy":"new_blhy,玻璃行业,19,1.2,'
               '0.28,1.65,603570618,17507420617,sh600176,5.94,44.39,2.49,中国巨石"};')
    gn_text = ('var S_Finance_bankuai_class = {"gn_hwqc":"gn_hwqc,华为概念,97,25.6,'
               '0.30,1.19,1795279779,40774654815,sz300814,11.74,155.88,16.38,中航路"};')

    def fake_text(url, **kw):
        return hy_text if "newSinaHy" in url else gn_text

    monkeypatch.setattr(sina, "http_get_text", fake_text)
    maps = sina.fetch_node_maps()
    assert maps["industry"]["玻璃行业"] == "new_blhy"
    assert maps["concept"]["华为概念"] == "gn_hwqc"


def test_sina_members_pagination(monkeypatch):
    page1 = [{"code": "600000", "name": "浦发银行", "trade": "9.10", "settlement": "9.18",
              "volume": 43623100, "amount": 397586127, "high": "9.17", "low": "9.06"}] * 100
    page2 = [{"code": "600004", "name": "白云机场", "trade": "1.0", "settlement": "1.0",
              "volume": 100, "amount": 100, "high": "1.0", "low": "1.0"}]

    def fake_get(url, params=None, **kw):
        return page1 if params["page"] == "1" else page2

    monkeypatch.setattr(sina, "http_get", fake_get)
    items = sina.fetch_sector_members("new_blhy", sector_code="BK0438")
    assert len(items) == 101
    assert {i["stock_code"] for i in items} == {"600000", "600004"}


# ═══════════════════ 搜狐 ═══════════════════


def test_sohu_kline_daily_units():
    """搜狐 amount 万元 → 元, 涨跌幅带 % 解析, 振幅按前收算"""
    payload = [{
        "status": 0,
        "hq": [
            ["2026-08-14", "9.14", "9.10", "-0.08", "-0.87%", "9.06", "9.17",
             "436231", "39758.61", "0.13%", "100.00"],
            ["2026-08-13", "9.16", "9.18", "0.01", "0.11%", "9.10", "9.20",
             "528860", "48389.42", "0.16%", "124.00"],
        ],
    }]

    rows = sohu._parse_hishq(payload, "600000")
    assert len(rows) == 2
    first = rows[0]
    assert first["open"] == 9.14 and first["close"] == 9.10
    assert first["amount"] == pytest.approx(39758.61 * 10000)
    assert first["change_pct"] == -0.87
    assert first["turnover_rate"] == 0.13
    # 前收 = close - change = 9.10 + 0.08 = 9.18
    assert first["amplitude"] == pytest.approx((9.17 - 9.06) / 9.18 * 100, abs=0.01)
    assert first["volume"] == 436231


def test_sohu_kline_rejects_error_status():
    assert sohu._parse_hishq([{"status": 2, "msg": "stock code non-existent"}], "1") == []
    assert sohu._parse_hishq([], "1") == []


def test_sohu_member_html_parse_dedup():
    html = (
        '<li>个 股<a href="/cn/600000/index.shtml">个 股</a></li>'
        '<tr><td class="e1">600000</td><td class="e2">'
        '<a href="/cn/600000/index.shtml" target="_blank">浦发银行</a></td></tr>'
        '<tr><td class="e1">600004</td><td class="e2">'
        '<a href="/cn/600004/index.shtml" target="_blank">白云机场</a></td></tr>'
    )
    items = sohu._parse_member_html(html, "BK0438")
    codes = [i["stock_code"] for i in items]
    assert codes.count("600000") == 1  # 去重
    assert "600004" in codes
    # 导航链接先出现(名称为"个 股")也不得抢占
    assert [i["stock_name"] for i in items if i["stock_code"] == "600000"] == ["浦发银行"]


def test_sohu_bk_index_from_html(monkeypatch):
    html = ('<a href="bk_3122.shtml" target="_blank">食品饮料</a>'
            '<a href="bk_3098.shtml">银行</a>')

    def fake_text(url, **kw):
        return html

    monkeypatch.setattr(sohu, "http_get_text", fake_text)
    sohu._bk_index_cache._value = None   # 清空 TtlCache
    index = sohu.get_name_bk_index(refresh=True)
    assert index["食品饮料"] == "3122"
    assert index["银行"] == "3098"


# ═══════════════════ 映射层 ═══════════════════


def test_boardmap_resolves_by_explicit_name(monkeypatch):
    monkeypatch.setattr(tencent, "get_name_pt_index", lambda refresh=False: {"食品饮料": "pt01801120"})
    assert boardmap.resolve_tencent_pt("BK0438", "食品饮料") == "pt01801120"
    assert boardmap.resolve_tencent_pt("BK0438", "不存在的板块") is None


def test_boardmap_falls_back_to_local_db(monkeypatch):
    monkeypatch.setattr(tencent, "get_name_pt_index", lambda refresh=False: {"银行": "pt01"})
    monkeypatch.setattr(
        boardmap, "sector_name_from_local", lambda code: "银行" if code == "BK0475" else None,
    )
    assert boardmap.resolve_tencent_pt("BK0475") == "pt01"
    assert boardmap.resolve_tencent_pt("BK9999") is None


def test_boardmap_sina_node_merges_types(monkeypatch):
    monkeypatch.setattr(sina, "get_name_node_index", lambda refresh=False: {
        "industry": {"银行": "new_yh"}, "concept": {"华为概念": "gn_hwqc"},
    })
    assert boardmap.resolve_sina_node("BK0475", "银行")[0] == "new_yh"
    assert boardmap.resolve_sina_node("BK0679", "华为概念")[0] == "gn_hwqc"


def test_boardmap_normalizes_names(monkeypatch):
    monkeypatch.setattr(tencent, "get_name_pt_index", lambda refresh=False: {"AI眼镜": "ptX"})
    assert boardmap.resolve_tencent_pt("BK1234", "AI 眼镜") == "ptX"


# ═══════════════════ 降级链 ═══════════════════


def test_stock_history_chain_tencent_primary(monkeypatch):
    """腾讯有数据时不再触达 akshare/搜狐"""
    from stock_analysis_mcp.tools import stock_data

    tencent_rows = [{
        "date": "2026-08-14", "symbol": "600000", "open": 9.14, "close": 9.10,
        "high": 9.17, "low": 9.06, "volume": 436231, "amount": None,
        "change_pct": -0.87, "change_amount": -0.08, "amplitude": 1.2,
        "turnover_rate": None,
    }]
    monkeypatch.setattr(
        "stock_analysis_mcp.data.providers.tencent.fetch_stock_kline",
        lambda *a, **kw: list(tencent_rows),
    )
    monkeypatch.setattr(stock_data, "_akshare_history",
                        lambda *a: pytest.fail("akshare 不应被触达"))
    monkeypatch.setattr(
        "stock_analysis_mcp.data.providers.sohu.fetch_stock_kline_daily",
        lambda *a, **kw: pytest.fail("sohu 不应被触达"),
    )
    rows = stock_data._stock_history_sync("600000", "20260101", "20260816")
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-08-14"


def test_stock_history_chain_falls_to_sohu(monkeypatch):
    """腾讯空 + 东财空 → 搜狐兜底"""
    from stock_analysis_mcp.tools import stock_data

    monkeypatch.setattr(
        "stock_analysis_mcp.data.providers.tencent.fetch_stock_kline",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(stock_data, "_akshare_history", lambda *a: None)
    sohu_rows = [{
        "date": "2026-08-14", "symbol": "600000", "open": 9.14, "close": 9.10,
        "high": 9.17, "low": 9.06, "volume": 436231, "amount": 397586100.0,
        "change_pct": -0.87, "change_amount": -0.08, "amplitude": 1.2,
        "turnover_rate": 0.13,
    }]
    monkeypatch.setattr(
        "stock_analysis_mcp.data.providers.sohu.fetch_stock_kline_daily",
        lambda *a, **kw: list(sohu_rows),
    )
    rows = stock_data._stock_history_sync("600000", "20260801", "20260816", adjust="")
    assert len(rows) == 1
    assert rows[0]["amount"] == pytest.approx(397586100.0)


def test_clip_kline_rows_filters_and_sorts_desc():
    from stock_analysis_mcp.tools.stock_data import _clip_kline_rows

    rows = [
        {"date": "2026-08-14"}, {"date": "2026-07-01"},
        {"date": "2026-08-13"}, {"date": "2025-12-31"},
    ]
    clipped = _clip_kline_rows(rows, "20260701", "20260816")
    assert [r["date"] for r in clipped] == ["2026-08-14", "2026-08-13", "2026-07-01"]


async def test_sector_members_chain_sina_primary(monkeypatch):
    """新浪命中映射时直接返回, 不触达东财"""
    from stock_analysis_mcp.tools import sector_data

    monkeypatch.setattr(
        boardmap, "resolve_sina_node",
        lambda code, name=None: ("new_blhy", "玻璃行业"),
    )
    monkeypatch.setattr(
        sina, "fetch_sector_members",
        lambda node, sector_code="": [{"stock_code": "600176", "stock_name": "中国巨石"}],
    )
    monkeypatch.setattr(
        sector_data, "_fetch_all_pages",
        lambda *a, **kw: pytest.fail("东财 clist 不应被触达"),
    )
    items = await sector_data.get_sector_members("BK0438", "玻璃行业")
    assert items[0]["stock_code"] == "600176"


async def test_sector_members_chain_em_fallback(monkeypatch):
    """新浪无映射(概念板块多数对不上) → 东财"""
    from stock_analysis_mcp.tools import sector_data

    monkeypatch.setattr(boardmap, "resolve_sina_node", lambda code, name=None: (None, name))

    async def fake_pages(params):
        return [
            {"f12": "600000", "f14": "浦发银行", "f2": 9.10, "f3": -0.87, "f8": 0.13},
        ]

    monkeypatch.setattr(sector_data, "_fetch_all_pages", fake_pages)
    items = await sector_data.get_sector_members("BK0438")
    assert items[0]["stock_code"] == "600000"
    assert items[0]["change_pct"] == -0.87


def test_sector_kline_em_only_with_breaker(monkeypatch):
    """板块K线为东财单源: 正常时走 try_kline_hosts; 熔断冷却期不发请求直接空返"""
    import stock_analysis_mcp.tools.sector_data as sector_data
    from stock_analysis_mcp.data import network

    _reset_health()
    calls = []

    def fake_kline(params, timeout=15):
        calls.append(params)
        return {"data": {"name": "食品饮料", "klines": [
            "2026-08-14,14475.50,14328.89,14475.50,14308.59,21623500,33818310000,"
            "1.15,-1.12,-161.88,2.33",
        ]}}

    monkeypatch.setattr(network, "try_kline_hosts", fake_kline)
    items = sector_data._sector_kline_net_sync("BK0438", 5, 101, "食品饮料")
    assert calls, "应调用东财K线"
    assert items[0]["trade_date"] == "2026-08-14"
    assert items[0]["sector_name"] == "食品饮料"
    assert items[0]["volume"] == 21623500.0

    # 熔断冷却期: fetch_em_kline 的 availability 门控生效, 不再发请求
    network._provider_cooldown_until["em_kline"] = network.time.time() + 60
    calls.clear()
    items = sector_data._sector_kline_net_sync("BK0438", 5, 101, "食品饮料")
    assert not calls and items == []
    _reset_health()
