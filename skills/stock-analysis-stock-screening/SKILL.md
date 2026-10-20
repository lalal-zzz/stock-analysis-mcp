---
name: stock-analysis-stock-screening
description: Screen Chinese A-shares by valuation, liquidity, momentum, name, sector, or programmatic chart-pattern conditions. Use for general 选股, 条件筛选, 板块选股, 排名, or building a reproducible candidate universe; use stock-analysis-rising-patterns when every top candidate needs chart-by-chart review.
---

# Reproducible stock screening

Screening produces candidates, not recommendations. Record the data cutoff, universe, filters, sort field, result count, and coverage so the result can be reproduced.

## Choose the correct entry point

- Numeric/name/sector conditions: `screen_stocks`.
- Raw five-family pattern scan for stocks: `scan_patterns`.
- Raw pattern scan for sector indices: `scan_sector_patterns`.
- Ranked rising-structure workflow with 20 deep reviews: `screen_rising_candidates` followed by `prepare_stock_analysis` for every result.

The market-wide scanners currently use the legacy five-detector engine. The new trendline/channel/range/non-symmetric W/M engine is exposed per symbol through `get_key_levels.data.market_structure`; use it to review finalists and do not describe the initial scan as a full-market scan of the new structures.

Call `get_data_status` first for market-wide scans. Below 95% K-line coverage, label the scan partial and do not imply that omitted stocks failed the filter.

## Numeric screening

`screen_stocks` supports:

- price: `min_price`, `max_price`
- daily move: `min_change_pct`, `max_change_pct`
- activity: `min_volume_ratio`, `min_turnover_rate`, `max_turnover_rate`
- valuation: `min_pe`, `max_pe`, `min_pb`, `max_pb`
- size: `min_market_cap`, `max_market_cap`, `min_float_market_cap`
- trend: `min_sixty_day_change`, `min_ytd_change`
- range: `min_amplitude`, `max_amplitude`

Also use `sector_code`, `name_keyword`, `top_n`, and a supported `sort_by`. State units and missing-value behavior; valuation filters can systematically exclude loss-making or incomplete records.

## Pattern taxonomy

The low-level engine has five detectors:

| Detector | High-level label | Required interpretation |
|---|---|---|
| `trend_pullback` | trend pullback | Existing uptrend must remain intact; compare pullback depth and volume contraction |
| `w_bottom` | W-bottom | Distinguish forming right bottom, neckline approach, breakout, and retest |
| `m_neckline` | `neckline_reclaim` | Below-neckline rejection is bearish; only a reclaim/hold supports a bullish thesis |
| `box_breakout` | box breakout | Report box dates, upper boundary, breakout volume, and whether price fell back inside |
| `ma_rebound` | `major_ma_rebound` | MA120/250 support is a rebound by default, not proof of trend reversal |

`fibonacci_confluence` is a high-level evidence filter. It requires an actual 0.382/0.5/0.618/0.786 price near another MA, trendline, or structural level and must not stand alone.

For stock finalists, read `get_key_levels.sector_context`. Report the primary industry before concepts, sector trend, breadth, capital-flow fields, data coverage, and whether stock/sector directions are aligned, mixed, or conflicting. Flow and breadth may strengthen an already aligned structure but cannot turn a range/opposite structure into directional alignment. Sector history shorter than 260 daily bars cannot support a long-cycle monthly conclusion.

For finalist review, prefer directional position evidence in `market_structure.positions`: identify anchor scale and A/B, use A/B/C only for projection, distinguish retracement/position/projection ratios, and use independent dependency groups rather than raw line count. `market_structure.double_patterns` can contain non-symmetric `m_top`; do not confuse it with legacy bullish `m_neckline`/`neckline_reclaim`.

## Top-down workflow

1. Use `get_sector_list` to identify sector direction and capital flow, while checking sector K-line freshness separately.
   For board popularity, use `get_sector_hot_rank`. It returns THS concept heat, price change and signed differences against the previous saved fetch. `heat_delta > 0` means observed heat increased; `rank_improvement > 0` means rank improved. Use `comparison="daily_1430"` and optional `trade_date="YYYY-MM-DD"` for read-only comparison of saved 14:30-window captures against the exact previous trading day. Capture via CLI `sector-hot-1430 --capture` during 14:30–14:35 Asia/Shanghai; missing captures cannot be reconstructed. Source `hot_rank_chg` remains raw with an unverified comparison basis. Fetch time is not the source trading date; do not claim today's/live heat or two-day price continuation. Missing/left-list members have unknown heat, not zero. THS codes must not be passed to Eastmoney BK tools. See `docs/sector-hot-rank.md` for cache and observation semantics.
2. Use `screen_stocks(sector_code=...)` or numeric conditions to define the universe.
3. Use `scan_patterns(symbols=[...])` when structure is required.
4. Check `get_stock_belong_sectors` and `get_rank_trend_data` as context, not causal proof.
5. For finalists, call `prepare_stock_analysis` and inspect monthly, weekly, and daily evidence.
6. When trendline/channel/W/M/Fibonacci structure affects the ranking, call `get_key_levels` for each finalist and state that this is finalist review rather than market-wide new-engine coverage.

## Output

Return a sortable table containing code/name, cutoff date, filter values, pattern/stage if applicable, score components, support/resistance/invalidation, sector context, and warnings. Include rejected-count or missing-data notes when available. Do not invent enough names to fill `top_n`; fewer qualifying stocks is a valid result.

Wave parsing and persistent real-time alert subscriptions are not exposed by current MCP tools. Do not rank by an inferred wave label or promise ongoing notification.
