"""patterns/signal.py — 信号构造 / JSON 清洗 / 形态过滤"""

import numpy as np

from .constants import PATTERN_FILTERS, PATTERN_NAMES
from .context import _Ctx


def _base_signal(ctx: _Ctx, i: int, symbol: str, name: str, pattern: str,
                 variant: str, **extra) -> dict:
    sig = {
        "symbol": symbol,
        "name": name,
        "date": ctx.dates[i],
        "pattern": pattern,
        "pattern_cn": PATTERN_NAMES[pattern],
        "variant": variant,
        "close": round(ctx.close[i], 3),
        "change_rate": ctx.change_rate[i],
        "turnover": ctx.turnover[i],
        "turnover_ma20": ctx.turnover_ma20[i],
        "updown_ratio_20": ctx.updown_ratio_20[i],
        "up_days_ratio_20": ctx.up_days_ratio_20[i],
        "vol_ratio": ctx.vol_ratio[i],
        "pullback_vol_shrink": np.nan,
        "rsi14": ctx.RSI14[i],
        "ma_bull": int(ctx.ma_bull[i]),
        "dif_above_zero": int(ctx.dif_above_zero[i]),
        "bias60": ctx.bias60[i],
        "hit_levels": "",
        "fib_level": "",
        "resonance": 0,
        "wave_phase": "",
        "break_days": 0,
        "break_depth": 0.0,
        "potential_gain": np.nan,
        "score": 0.0,
        "trend": "",
        # MACD / KDJ / BOLL 指标因子 (0/1 或数值)
        "macd_gold3": int(ctx.macd_gold3[i]),
        "dif_below0": int(ctx.dif_below0[i]),
        "kdj_gold3": int(ctx.kdj_gold3[i]),
        "kdj_j": ctx.J[i],
        "boll_pos": ctx.boll_pos[i],
        "boll_width": ctx.boll_width[i],
        "rsi6": ctx.rsi6[i],
        "macd_div": 0,
    }
    sig.update(extra)
    return sig


def _sig_clean(sig: dict) -> dict:
    """NaN → None, 便于 JSON 序列化。"""
    return {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
            for k, v in sig.items()}


def passes_filter(sig: dict, filters: dict | None = None) -> bool:
    """按形态专属因子阈值筛选; 支持 <field>_in 类别条件与 (下限, 上限) 数值条件。

    因子缺失/NaN 不通过。
    """
    if filters is None:
        from ..rule_registry import active_filters
        filters = {**PATTERN_FILTERS, **(active_filters(str(sig.get("date", ""))) or {})}
    f = filters.get(sig["pattern"])
    if not f:
        return True
    for key, cond in f.items():
        if key.endswith("_in"):
            field = key[:-3]
            if sig.get(field) not in cond:
                return False
            continue
        lo, hi = cond
        v = sig.get(key)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return False
        if lo is not None and v < lo:
            return False
        if hi is not None and v > hi:
            return False
    return True
