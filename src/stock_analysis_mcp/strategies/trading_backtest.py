"""Callable two-layer pattern backtest used by MCP and Skills."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json

import numpy as np
import pandas as pd

from ..data.storage import get_db_paths
from .pattern_backtest import collect_signals
from .patterns import BACKTEST_START, prepare_df


def _event_summary(df: pd.DataFrame) -> list[dict]:
    rows = []
    if df.empty:
        return rows
    for (pattern, variant), group in df.groupby(["pattern", "variant"], dropna=False):
        row = {"pattern": pattern, "variant": variant, "samples": len(group)}
        for days in (5, 10, 20):
            values = pd.to_numeric(group.get(f"ret_{days}"), errors="coerce").dropna()
            row[f"win_rate_{days}"] = round(float((values > 0).mean()), 4) if len(values) else None
            row[f"avg_return_{days}"] = round(float(values.mean()), 6) if len(values) else None
            row[f"median_return_{days}"] = round(float(values.median()), 6) if len(values) else None
        rows.append(row)
    return rows


def _simulate_trade(signal: dict, buy_cost_bps: float, sell_cost_bps: float,
                    max_holding_days: int = 20, reward_risk: float = 2.0,
                    end: str | None = None, slippage_bps: float = 5.0) -> dict | None:
    df = prepare_df("stocks", signal["symbol"], start=signal["date"], end=end)
    if len(df) < 2:
        return None
    entry_idx = next((i for i, d in enumerate(df["date"]) if d > signal["date"]), None)
    if entry_idx is None:
        return None
    entry = float(df.iloc[entry_idx]["open"]) * (1 + slippage_bps / 10000)
    entry_bar = df.iloc[entry_idx]
    if (float(entry_bar["high"]) == float(entry_bar["low"])
            and float(entry_bar.get("change_rate") or 0) >= 9.5):
        return None  # one-price limit-up cannot be bought
    hist = prepare_df("stocks", signal["symbol"], end=signal["date"], tail=30)
    if hist.empty or entry <= 0:
        return None
    prev_close = hist["close"].shift(1)
    tr = pd.concat([(hist["high"] - hist["low"]),
                    (hist["high"] - prev_close).abs(),
                    (hist["low"] - prev_close).abs()], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean()) if tr.notna().any() else entry * 0.04
    risk = min(entry * 0.08, max(atr * 2.0, entry * 0.02))
    stop = entry - risk
    target = entry + reward_risk * risk
    deadline = entry_idx + max_holding_days - 1
    end_idx = len(df) - 1
    exit_price = float(df.iloc[end_idx]["close"])
    exit_reason = "open_at_data_end"
    exit_idx = end_idx
    for i in range(entry_idx, end_idx + 1):
        row = df.iloc[i]
        locked_down = (float(row["high"]) == float(row["low"])
                       and float(row.get("change_rate") or 0) <= -9.5)
        if locked_down:
            continue
        # Conservative convention when both are touched in the same bar.
        if float(row["low"]) <= stop and not locked_down:
            exit_price, exit_reason, exit_idx = min(stop, float(row["open"])), "stop", i
            break
        if float(row["high"]) >= target:
            exit_price, exit_reason, exit_idx = target, "target_2r", i
            break
        if i >= deadline:
            exit_price, exit_reason, exit_idx = float(row["close"]), "time_exit", i
            break
    closed = exit_reason != "open_at_data_end"
    if closed:
        exit_price *= 1 - slippage_bps / 10000
    gross = exit_price / entry - 1.0
    buy_fee = buy_cost_bps / 10000
    sell_fee = sell_cost_bps / 10000 if closed else 0.0
    net = (exit_price / entry) * (1 - sell_fee) / (1 + buy_fee) - 1
    marks = [{"date": str(df.iloc[i]["date"]),
              "net_value": float(df.iloc[i]["close"]) / entry / (1 + buy_fee)}
             for i in range(entry_idx, exit_idx + 1)]
    if closed:
        marks[-1]["net_value"] = 1 + net
    return {"symbol": signal["symbol"], "pattern": signal["pattern"],
            "signal_date": signal["date"], "entry_date": str(df.iloc[entry_idx]["date"]),
            "exit_date": str(df.iloc[exit_idx]["date"]), "entry": round(entry, 4),
            "exit": round(exit_price, 4), "stop": round(stop, 4), "target": round(target, 4),
            "holding_days": exit_idx - entry_idx + 1, "exit_reason": exit_reason,
            "gross_return": round(gross, 6), "net_return": round(net, 6),
            "closed": closed, "marks": marks,
            "entry_volume": float(entry_bar.get("volume") or 0),
            "slippage_bps": slippage_bps}


def _portfolio_summary(trades: list[dict], max_positions: int = 10,
                       initial_capital: float = 1_000_000,
                       max_volume_fraction: float = 0.01) -> dict:
    if not trades:
        return {"trades": 0, "total_return": 0.0, "max_drawdown": 0.0,
                "win_rate": None, "profit_loss_ratio": None, "equity_series": [],
                "accepted_trades": [], "rejected_trades": 0}
    from ..data.quality import cached_trade_dates
    dates = {d for t in trades for d in (t["entry_date"], t["exit_date"])}
    dates.update(m["date"] for t in trades for m in t.get("marks", []))
    first, last = min(dates), max(dates)
    dates.update(d for d in cached_trade_dates() if first <= d <= last)
    accepted, active, rejected, curve = [], [], 0, []
    cash = initial_capital
    previous_equity = initial_capital
    for day in sorted(dates):
        start_equity = previous_equity
        for trade in sorted((t for t in trades if t["entry_date"] == day), key=lambda t: t["symbol"]):
            if len(active) >= max_positions or any(p["trade"]["symbol"] == trade["symbol"] for p in active):
                rejected += 1
                continue
            budget = min(cash, start_equity / max_positions)
            if "entry" in trade:
                volume = trade.get("entry_volume", 0)
                if not volume or budget / trade["entry"] > volume * 100 * max_volume_fraction:
                    rejected += 1
                    continue
            if budget <= 0:
                rejected += 1
                continue
            cash -= budget
            active.append({"trade": trade, "budget": budget, "value": budget})
            accepted.append(trade)
        keep = []
        for position in active:
            trade = position["trade"]
            mark = next((m for m in trade.get("marks", []) if m["date"] == day), None)
            if mark:
                position["value"] = position["budget"] * mark["net_value"]
            if trade["exit_date"] == day and trade.get("closed", True):
                cash += position["budget"] * (1 + trade["net_return"])
            else:
                keep.append(position)
        active = keep
        held = sum(p["value"] for p in active)
        previous_equity = cash + held
        curve.append({"date": day, "nav": previous_equity / initial_capital,
                      "exposure": held / previous_equity if previous_equity > 0 else 0,
                      "positions": len(active), "cash": cash})
    nav = pd.Series([1.0] + [p["nav"] for p in curve])
    max_dd = float((nav / nav.cummax() - 1).min())
    daily_returns = nav.pct_change().dropna().to_numpy()
    equity = float(nav.iloc[-1])
    closed_trades = [t for t in accepted if t.get("closed", True)]
    returns = np.array([x["net_return"] for x in closed_trades], dtype=float)
    gains, losses = returns[returns > 0], returns[returns <= 0]
    pnl = float(gains.mean() / abs(losses.mean())) if len(gains) and len(losses) and losses.mean() else None
    if len(curve) > 1:
        first, last = pd.Timestamp(curve[0]["date"]), pd.Timestamp(curve[-1]["date"])
        years = max((last - first).days / 365.25, 1 / 365.25)
        annualized = equity ** (1 / years) - 1.0
    else:
        annualized = None
    dret = np.array(daily_returns, dtype=float)
    sharpe = float(dret.mean() / dret.std(ddof=1) * np.sqrt(252)) if len(dret) > 1 and dret.std(ddof=1) > 0 else None
    return {"trades": len(accepted), "total_return": round(equity - 1.0, 6),
            "max_drawdown": round(max_dd, 6), "win_rate": round(float((returns > 0).mean()), 4) if len(returns) else None,
            "avg_return": round(float(returns.mean()), 6) if len(returns) else None,
            "profit_loss_ratio": round(pnl, 4) if pnl is not None else None,
            "annualized_return": round(annualized, 6) if annualized is not None else None,
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "equity_series": curve, "accepted_trades": accepted,
            "rejected_trades": rejected, "open_positions": len(active),
            "initial_capital": initial_capital}


def _commonality(df: pd.DataFrame, split: str) -> list[dict]:
    """Report stable candidate factors; never mutates live filters."""
    if df.empty or "ret_10" not in df:
        return []
    factors = ["score", "vol_ratio", "bias60", "rsi14", "resonance", "change_rate"]
    rows = []
    for factor in factors:
        if factor not in df:
            continue
        values = pd.to_numeric(df.get(factor), errors="coerce")
        valid = df[values.notna()].copy()
        if len(valid) < 100:
            continue
        training = valid[valid["date"] < split]
        if len(training) < 50:
            continue
        edges = np.unique(np.quantile(pd.to_numeric(training[factor]), [0, .25, .5, .75, 1]))
        if len(edges) < 2:
            continue
        edges[0], edges[-1] = -np.inf, np.inf
        valid["bucket"] = pd.cut(pd.to_numeric(valid[factor]), edges, include_lowest=True)
        for bucket, group in valid.groupby("bucket", observed=True):
            train = group[group["date"] < split]
            test = group[group["date"] >= split]
            if len(train) < 50 or len(test) < 20:
                continue
            tr = float((train["ret_10"] > 0).mean())
            te = float((test["ret_10"] > 0).mean())
            rows.append({"factor": factor, "range": str(bucket), "train_samples": len(train),
                         "test_samples": len(test), "train_win10": round(tr, 4),
                         "test_win10": round(te, 4), "gap": round(abs(tr - te), 4),
                         "candidate_for_manual_review": abs(tr - te) <= 0.10 and te > 0.5})
    return sorted(rows, key=lambda x: (x["candidate_for_manual_review"], x["test_win10"]), reverse=True)


def backtest_pattern_strategy(*, start: str = BACKTEST_START, end: str | None = None,
                              patterns: list[str] | None = None, sample: int | None = None,
                              workers: int = 8, mode: str = "both", split: str = "2022-01-01",
                              buy_cost_bps: float = 8.0, sell_cost_bps: float = 13.0,
                              benchmark_symbol: str | None = None,
                              slippage_bps: float = 5.0,
                              initial_capital: float = 1_000_000,
                              max_volume_fraction: float = 0.01) -> dict:
    if mode not in {"event", "trading", "both"}:
        raise ValueError("mode must be event|trading|both")
    if min(buy_cost_bps, sell_cost_bps, slippage_bps) < 0 or initial_capital <= 0 or not 0 < max_volume_fraction <= 1:
        raise ValueError("invalid cost, capital or participation limit")
    df = collect_signals("stocks", start, end, patterns, (20, 60, 120, 250), 0.015,
                         sample, workers)
    event = _event_summary(df) if mode in {"event", "both"} else []
    trades = []
    if mode in {"trading", "both"} and not df.empty:
        for signal in df.to_dict(orient="records"):
            trade = _simulate_trade(signal, buy_cost_bps, sell_cost_bps, end=end, slippage_bps=slippage_bps)
            if trade:
                trades.append(trade)
    portfolio = _portfolio_summary(trades, initial_capital=initial_capital, max_volume_fraction=max_volume_fraction)
    accepted_trades = portfolio.pop("accepted_trades", [])
    common = _commonality(df, split)
    report_dir = Path(get_db_paths()["stock_dir"]) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stamp += f"_{datetime.now().microsecond:06d}"
    report_path = report_dir / f"pattern_backtest_{stamp}.md"
    trades_path = report_dir / f"pattern_backtest_{stamp}_trades.csv"
    equity_path = report_dir / f"pattern_backtest_{stamp}_equity.csv"
    json_path = report_dir / f"pattern_backtest_{stamp}.json"
    warnings = []
    curve = portfolio.get("equity_series", [])
    if benchmark_symbol and curve:
        reference = prepare_df("stocks", benchmark_symbol, start=curve[0]["date"], end=curve[-1]["date"])
        if reference.empty or str(reference.iloc[0]["date"]) > curve[0]["date"]:
            warnings.append("基准数据不足，未生成基准比较")
        else:
            close = {str(r["date"]): float(r["close"]) for r in reference.to_dict("records")}
            anchor = float(reference.iloc[0]["close"])
            last_mark = anchor
            for point in curve:
                last_mark = close.get(point["date"], last_mark)
                point["benchmark_nav"] = last_mark / anchor
                point["excess_nav"] = point["nav"] - point["benchmark_nav"]
    if portfolio.get("open_positions"):
        warnings.append("数据截止时仍有未平仓持仓，按市值计入净值")
    pd.DataFrame(curve).to_csv(equity_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([{k: v for k, v in t.items() if k != "marks"} for t in accepted_trades]).to_csv(trades_path, index=False, encoding="utf-8-sig")
    report_path.write_text(
        "# 形态策略双层回测\n\n"
        f"- 信号数: {len(df)}\n- 交易数: {portfolio['trades']}\n"
        f"- 组合收益: {portfolio['total_return']:.2%}\n"
        f"- 最大回撤: {portfolio['max_drawdown']:.2%}\n"
        f"- 规则更新: 仅生成候选，须人工确认后启用。\n",
        encoding="utf-8")
    result = {"mode": mode, "period": {"start": start, "end": end, "split": split},
            "assumptions": {"entry": "next_open", "max_holding_days": 20,
                            "target": "2R", "fallback_stop": "min(2ATR,8%)",
                            "buy_cost_bps": buy_cost_bps, "sell_cost_bps": sell_cost_bps,
                            "max_positions": 10, "slippage_bps": slippage_bps,
                            "initial_capital": initial_capital, "max_volume_fraction": max_volume_fraction,
                            "price_basis": "qfq", "liquidity_volume_unit": "hands",
                            "benchmark_symbol": benchmark_symbol,
                            "limit_execution": "conservative one-price limit bars",
                            "calendar": "cached exchange calendar; observed dates if unavailable"},
            "signals": len(df), "event_summary": event,
            "portfolio": portfolio, "trades_preview": [{k:v for k,v in t.items() if k != "marks"} for t in accepted_trades[:500]],
            "commonality_candidates": common, "rules_mutated": False,
            "report_path": str(report_path), "trades_path": str(trades_path),
            "equity_path": str(equity_path), "json_path": str(json_path), "warnings": warnings}
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result
