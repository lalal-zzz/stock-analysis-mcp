"""High-level research workflows composed from the local data and pattern engines."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from ..core.constants import DEFAULT_ANALYSIS_BARS, DEFAULT_RESEARCH_BARS, MIN_PATTERN_BARS
from ..core.parallel import run_parallel
from ..data.network import normalize_symbol
from ..data.search import get_db_status, get_rank_trend, get_sectors_by_stock, get_stock_kline_local
from ..data.storage import query_stock_db, save_pattern_signals
from ..data.sync import sync_stock_kline_universe
from ..strategies.patterns import (
    PATTERN_NAMES, PATTERN_STRICT_FILTERS, detect_patterns, get_key_levels, get_universe_list,
    passes_filter, prepare_df,
)

PATTERN_ALIASES = {"m_neckline": "neckline_reclaim", "ma_rebound": "major_ma_rebound"}
PATTERN_INPUT_ALIASES = {v: k for k, v in PATTERN_ALIASES.items()}


def _canonical(pattern: str) -> str:
    return PATTERN_ALIASES.get(pattern, pattern)


def _stage(signal: dict) -> tuple[str, bool, str]:
    variant = signal.get("variant", "")
    pattern = signal.get("pattern")
    if "retest" in variant or "hold" in variant or pattern == "m_neckline":
        return "retesting", True, "actionable"
    if "breakout" in variant:
        return "confirmed", True, "actionable"
    if variant in {"right_bottom", "MA120_hold", "MA250_hold", "MA60_hold"}:
        return "candidate", False, "wait_confirmation"
    return "triggered", False, "wait_confirmation"


def _fib_map(levels: dict) -> dict:
    result = {}
    for row in levels.get("levels", []):
        typ = str(row.get("type", ""))
        for ratio in ("382", "500", "618", "786"):
            if ratio in typ:
                result[{"382": "0.382", "500": "0.5", "618": "0.618", "786": "0.786"}[ratio]] = row["value"]
    return result


def _nearest(levels: list[dict]) -> float | None:
    return levels[0]["value"] if levels else None


def _enrich_signal(signal: dict, levels: dict, period: dict | None = None,
                   sector_score: float = 0.0) -> dict:
    stage, confirmed, entry_status = _stage(signal)
    trend = signal.get("trend") or levels.get("trend") or "range"
    base = float(signal.get("score") or 0.0)
    pattern_score = min(30.0, base * 30.0)
    daily_score = 20.0 if trend == "up" else (10.0 if trend == "range" else 3.0)
    higher_score = float((period or {}).get("higher_score", 7.5))
    vol = signal.get("vol_ratio")
    volume_score = min(15.0, max(0.0, float(vol or 0.0) * 7.5))
    resonance = int(signal.get("resonance") or 0)
    confluence_score = min(10.0, resonance * 3.5 + (2.0 if signal.get("fib_level") else 0.0))
    risk_penalty = 0.0
    warnings = []
    if trend == "down":
        risk_penalty += 15.0
        warnings.append("日线趋势仍偏弱")
    if vol is not None and confirmed and float(vol) < 1.0:
        risk_penalty += 10.0
        warnings.append("确认信号量能不足")
    total = max(0.0, min(100.0, pattern_score + daily_score + higher_score
                         + volume_score + min(10.0, sector_score) + confluence_score - risk_penalty))
    fib = _fib_map(levels)
    support = signal.get("key_level_value") or _nearest(levels.get("support", []))
    resistance = _nearest(levels.get("resistance", []))
    neckline = signal.get("neckline_value")
    invalid_level = support
    if signal.get("pattern") == "w_bottom" and signal.get("right_bottom_price"):
        invalid_level = round(float(signal["right_bottom_price"]) * 0.98, 3)
    elif signal.get("pattern") == "m_neckline" and neckline:
        invalid_level = round(float(neckline) * 0.98, 3)
    elif signal.get("pattern") == "box_breakout" and signal.get("box_high"):
        invalid_level = signal["box_high"]
    evidence = [
        f"形态标准度 {base:.2f}", f"日线趋势 {trend}",
        f"共振数 {resonance}", f"量能倍率 {float(vol):.2f}" if vol is not None else "量能倍率缺失",
    ]
    if signal.get("hit_levels"):
        evidence.append(f"命中关键位 {signal['hit_levels']}")
    return {**signal, "raw_pattern": signal.get("pattern"),
            "pattern": _canonical(signal.get("pattern", "")), "signal_date": signal.get("date"),
            "stage": stage, "confirmed": confirmed, "entry_status": entry_status,
            "trend_state": trend, "support": support, "resistance": resistance,
            "neckline": neckline, "invalid_level": invalid_level,
            "fib_levels": fib, "evidence": evidence,
            "warnings": warnings, "score_raw": base, "score": round(total, 2),
            "score_breakdown": {"pattern": round(pattern_score, 2), "daily": daily_score,
                                "higher_timeframe": higher_score, "volume": round(volume_score, 2),
                                "sector": round(min(10.0, sector_score), 2),
                                "confluence": round(confluence_score, 2),
                                "risk_penalty": round(risk_penalty, 2)},
            "factors": {k: signal.get(k) for k in (
                "change_rate", "turnover", "vol_ratio", "bias60", "rsi14",
                "resonance", "wave_phase", "potential_gain")}}


def _period_frame(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    frame = df.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return frame.set_index("date").resample(rule).agg(agg).dropna().reset_index()


def _period_summary(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {"period": label, "bars": 0, "trend": "unknown"}
    close = pd.to_numeric(df["close"], errors="coerce")
    latest = float(close.iloc[-1])
    mas = {f"MA{w}": round(float(close.rolling(w).mean().iloc[-1]), 3)
           for w in (5, 10, 20, 60) if len(df) >= w}
    trend = "up" if mas.get("MA20") and latest > mas["MA20"] and (
        not mas.get("MA60") or mas["MA20"] > mas["MA60"]) else "down"
    changes = {f"return_{w}": round((latest / float(close.iloc[-w - 1]) - 1) * 100, 2)
               for w in (5, 10, 20) if len(df) > w and close.iloc[-w - 1] > 0}
    return {"period": label, "bars": len(df), "last_date": str(df["date"].iloc[-1])[:10],
            "close": round(latest, 3), "trend": trend, "moving_averages": mas,
            "recent_returns_pct": changes,
            "range_20": {"high": round(float(df["high"].tail(20).max()), 3),
                         "low": round(float(df["low"].tail(20).min()), 3)}}


def _higher_timeframe(df: pd.DataFrame) -> dict:
    weekly = _period_summary(_period_frame(df, "W-FRI"), "weekly")
    monthly = _period_summary(_period_frame(df, "ME"), "monthly")
    score = (8.0 if weekly["trend"] == "up" else 2.0) + (7.0 if monthly["trend"] == "up" else 1.0)
    return {"weekly": weekly, "monthly": monthly, "higher_score": score}


def _sector_context(symbol: str) -> tuple[list[dict], float]:
    sectors = get_sectors_by_stock(symbol)
    positives = [s for s in sectors if (s.get("sector_change_pct") or 0) > 0]
    score = min(10.0, len(positives) * 2.0)
    return sectors, score


def screen_rising_candidates(*, top_n: int = 20, lookback_days: int = 20,
                             patterns: list[str] | None = None, min_score: float = 0.6,
                             strict: bool = True, workers: int = 8,
                             symbols: list[str] | None = None) -> dict:
    """Scan recent signals, enrich them with higher-timeframe evidence, and rank candidates."""
    status = get_db_status()
    coverage = status["stock"]["kline_coverage"]
    universe = get_universe_list("stocks")
    if symbols:
        wanted = {normalize_symbol(s) for s in symbols}
        universe = universe[universe["symbol"].isin(wanted)]
    cutoff = (date.today() - timedelta(days=max(lookback_days * 2, 30))).isoformat()
    requested_patterns = patterns
    fib_only = requested_patterns == ["fibonacci_confluence"]
    if requested_patterns:
        requested_patterns = [PATTERN_INPUT_ALIASES.get(p, p) for p in requested_patterns
                              if p != "fibonacci_confluence"] or None

    def _scan_one(row):
        try:
            df = prepare_df("stocks", row.symbol, tail=420)
            if len(df) < MIN_PATTERN_BARS:
                return None
            expected = coverage.get("expected_trade_date")
            if not expected or str(df.iloc[-1]["date"])[:10] != expected:
                return {"scan_error":"stale or unverified calendar", "symbol":row.symbol}
            sigs = detect_patterns(df, row.symbol, row.name, requested_patterns, min_score=min_score)
            recent = [s for s in sigs if s["date"] >= cutoff and
                      (not strict or passes_filter(s, PATTERN_STRICT_FILTERS))]
            if not recent:
                return None
            signal = max(recent, key=lambda s: (s["date"], s["score"]))
            levels = get_key_levels("stocks", row.symbol)
            higher = _higher_timeframe(df)
            sectors, sector_score = _sector_context(row.symbol)
            enriched = _enrich_signal(signal, levels, higher, sector_score)
            if fib_only and not enriched["fib_levels"]:
                return None
            enriched["higher_timeframe"] = {"weekly": higher["weekly"], "monthly": higher["monthly"]}
            enriched["sectors"] = sectors[:10]
            return enriched
        except Exception as exc:
            return {"scan_error":str(exc), "symbol":row.symbol}

    candidates = []
    failed_scans = []
    for _row, result in run_parallel(list(universe.itertuples(index=False)), _scan_one,
                                     workers=max(1, min(int(workers), 16))):
        if isinstance(result, Exception):
            raise result
        if result and "scan_error" in result:
            failed_scans.append(result)
        elif result:
            candidates.append(result)
    candidates.sort(key=lambda x: (x["score"], x["signal_date"]), reverse=True)
    results = candidates[:max(1, min(int(top_n), 100))]
    if results:
        save_pattern_signals(results)
    return {"as_of_date": max((r["signal_date"] for r in results), default=None),
            "universe_size": len(universe), "scanned": coverage["symbols"],
            "skipped": max(0, len(universe) - coverage["symbols"]),
            "coverage_ratio": coverage["coverage_ratio"],
            "full_market_ready": coverage["full_market_ready"],
            "warning": None if coverage["full_market_ready"] else "K线覆盖率不足95%，结果不代表完整市场",
            "results": results, "failed_scans": failed_scans,
            "warnings": (["部分股票扫描失败或行情过期"] if failed_scans else []) +
                ([] if coverage["full_market_ready"] else ["复权一致性与新鲜度尚未覆盖95%的股票"])}


async def prepare_stock_analysis(symbol: str, *, days: int = 500,
                                 include_chart: bool = True,
                                 refresh_if_stale: bool = True) -> dict:
    """Build the numeric and visual evidence packet consumed by the analysis Skill."""
    sym = normalize_symbol(symbol)
    from ..data.quality import latest_completed_trade_day
    import asyncio
    expected = await asyncio.to_thread(latest_completed_trade_day, allow_network=refresh_if_stale)
    klines = get_stock_kline_local(sym, max(days, MIN_PATTERN_BARS), "qfq")
    stale = not expected or not klines or str(klines[-1].get("date", "")) < expected
    if refresh_if_stale and (len(klines) < MIN_PATTERN_BARS or stale):
        await sync_stock_kline_universe([sym], target_bars=max(DEFAULT_RESEARCH_BARS, days), resume=False)
        klines = get_stock_kline_local(sym, max(days, DEFAULT_RESEARCH_BARS), "qfq")
        stale = not expected or not klines or str(klines[-1].get("date", "")) < expected
    if len(klines) < MIN_PATTERN_BARS:
        raise RuntimeError(f"{sym} K线不足: {len(klines)} < {MIN_PATTERN_BARS}")
    df = prepare_df("stocks", sym, tail=max(days, DEFAULT_RESEARCH_BARS))
    basic = query_stock_db(
        """SELECT b.symbol,b.name,s.* FROM stock_basic b LEFT JOIN stock_spot s ON s.symbol=b.symbol
           WHERE b.symbol=?""", (sym,))
    levels = get_key_levels("stocks", sym)
    sigs = detect_patterns(df, sym, (basic[0].get("name") if basic else sym), min_score=0.5)
    latest = [_enrich_signal(s, levels, _higher_timeframe(df)) for s in sigs[-10:]]
    periods = _higher_timeframe(df)
    periods["daily"] = _period_summary(df, "daily")
    sectors, _ = _sector_context(sym)
    charts: dict[str, Any] = {}
    chart_warning = None
    if include_chart:
        try:
            from ..charting import generate_analysis_charts
            charts = generate_analysis_charts(sym, days=max(days, DEFAULT_RESEARCH_BARS), klines=klines)
        except Exception as exc:
            chart_warning = str(exc)
    return {"stock": basic[0] if basic else {"symbol": sym},
            "data_quality": {"bars": len(klines), "first_date": klines[0]["date"],
                             "last_date": klines[-1]["date"], "stale": stale,
                             "expected_trade_date": expected,
                             "adjust_type": "qfq"},
            "timeframes": {k: v for k, v in periods.items() if k != "higher_score"},
            "key_levels": levels, "patterns": latest,
            "sector_context": sectors, "popularity": get_rank_trend(sym, 30),
            "charts": charts, "warnings": (["K线新鲜度未确认或存在缺口"] if stale else [])
                                        + ([chart_warning] if chart_warning else [])}
