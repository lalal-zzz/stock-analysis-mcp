"""第三批重构的回归测试: data/paging 统一分页、TtlCache、skills 目录结构。"""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_analysis_mcp.data.paging import fetch_all_pages
from stock_analysis_mcp.data.util import TtlCache


REPO = Path(__file__).resolve().parents[1]


# ── data/paging ──

def _make_pages(rows_per_page, total):
    """模拟 push2 clist: 第1页返回 total, 后续页返回对应数据"""
    def fetch_page(pn):
        start = (pn - 1) * rows_per_page
        rows = [{"i": i} for i in range(start, min(start + rows_per_page, total))]
        return {"data": rows, "count": total if rows else 0}
    return fetch_page


def _extract(payload):
    data = (payload or {}).get("data") or []
    return data, (payload or {}).get("count", 0) or 0


def test_fetch_all_pages_multi_page():
    rows = asyncio.run(fetch_all_pages(
        _make_pages(100, 350), _extract, page_size=100, concurrency=8))
    assert len(rows) == 350
    assert [r["i"] for r in rows] == list(range(350))  # 页序保持


def test_fetch_all_pages_single_page():
    rows = asyncio.run(fetch_all_pages(
        _make_pages(100, 50), _extract, page_size=100))
    assert len(rows) == 50


def test_fetch_all_pages_empty_first():
    rows = asyncio.run(fetch_all_pages(
        _make_pages(100, 0), _extract, page_size=100))
    assert rows == []


def test_fetch_all_pages_serial_fallback():
    """count 缺失(人气排名场景)时串行翻页直到短页"""
    calls = []

    def fetch_page(pn):
        calls.append(pn)
        # count 永远返回 0 (缺失), 但每页都有满页数据, 第3页短页
        rows = [{"i": pn * 100 + k} for k in range(100 if pn < 3 else 10)]
        return {"data": rows, "count": 0}

    rows = asyncio.run(fetch_all_pages(
        fetch_page, _extract, page_size=100, serial_fallback=True))
    assert len(rows) == 210
    assert calls == [1, 2, 3]  # 串行, 短页即停


# ── TtlCache ──

def test_ttl_cache_roundtrip():
    cache = TtlCache(60)
    assert cache.get(lambda: {"a": 1}) == {"a": 1}
    assert cache.get(lambda: {"b": 2}) == {"a": 1}  # TTL 内不重新拉取


def test_ttl_cache_failure_keeps_old():
    cache = TtlCache(60)
    cache.get(lambda: {"old": True})
    assert cache.get(lambda: None) == {"old": True}   # fetcher 失败返回旧值
    assert cache.get(lambda: {"new": True}, refresh=True) == {"new": True}


def test_ttl_cache_expiry():
    cache = TtlCache(0.01)
    cache.get(lambda: 1)
    time.sleep(0.03)
    assert cache.get(lambda: 2) == 2


# ── skills/ 目录结构 (GitHub skills 仓库惯例) ──

def test_skills_layout():
    skills = sorted(d.name for d in (REPO / "skills").iterdir() if d.is_dir())
    assert skills == [
        "stock-analysis",
        "stock-analysis-chart-trend",
        "stock-analysis-data-init",
        "stock-analysis-financials",
        "stock-analysis-global-markets",
            "stock-analysis-multi-timeframe",
        "stock-analysis-news",
        "stock-analysis-related-news",
            "stock-analysis-report-generation",
            "stock-analysis-rising-patterns",
            "stock-analysis-stock-screening",
        "stock-analysis-strategy-backtest",
    ]
    for d in skills:
        assert (REPO / "skills" / d / "SKILL.md").exists(), d
        head = (REPO / "skills" / d / "SKILL.md").read_text(encoding="utf-8").splitlines()[:4]
        assert head[0].strip() == "---", d          # YAML frontmatter


def test_skill_frontmatter_name_matches_dir():
    import re
    for d in sorted((REPO / "skills").iterdir()):
        if not d.is_dir():
            continue
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"---\nname:\s*(\S+)", text)
        assert m, d
        assert m.group(1) == d.name, f"{d.name}: frontmatter name 与目录名不一致"
