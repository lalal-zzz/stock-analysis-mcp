---
name: stock-analysis
description: Route stock research across data, screening, chart, report, multi-timeframe, rising-pattern, backtest, news and financial workflows. Use for broad stock-research requests or when the correct specialized workflow is unclear.
---

# Stock Analysis research router

Use the MCP as a local-first evidence service. Programs fetch, normalize, calculate, rank, render, and backtest; the agent interprets the evidence and states uncertainty. Never turn a score or chart label into a guaranteed price prediction.

## Start with data quality

Call `get_data_status` before market-wide work or when freshness matters.

- Compare the latest date with the latest completed A-share trading day, not the calendar date.
- For a full-market claim, require `stock.kline_coverage.full_market_ready=true` or coverage of at least 95%. Otherwise label the result partial.
- Reuse an existing database. Initialize only missing datasets; use incremental synchronization for stale data.
- State the adjustment type, data cutoff, missing fields, provider warnings, and whether intraday data is live or delayed.

## Route the request

| Request | Skill | Primary tools |
|---|---|---|
| Initialize, update, inspect, repair data | `stock-analysis-data-init` | `get_data_status`, `init_full_data`, `update_daily_data`, `sync_stock_kline_universe` |
| General factor/sector/name screening | `stock-analysis-stock-screening` | `screen_stocks`, `get_sector_list`, `scan_patterns` |
| Find rising structures and deeply review 20 stocks | `stock-analysis-rising-patterns` | `screen_rising_candidates`, `prepare_stock_analysis` |
| Analyze one stock | `stock-analysis-report-generation` | `prepare_stock_analysis`, `generate_stock_report`, `get_key_levels` |
| Compare monthly/weekly/daily/intraday structure or historical shape similarity | `stock-analysis-multi-timeframe` | `prepare_stock_analysis`, `get_stock_kline_period`, `find_cross_timeframe_similar_patterns` |
| Read candlestick images, trendlines, channels, ranges, W/M, and directional position | `stock-analysis-chart-trend` | `render_stock_charts`, `get_key_levels` |
| Validate signals or candidate rules | `stock-analysis-strategy-backtest` | `backtest_pattern_strategy`, pattern backtest/optimize CLI |
| News and official policy releases | `stock-analysis-news` | `search_market_news` |
| Company/concept news timelines | `stock-analysis-related-news` | `get_stock_related_news` |
| Global macro and market news context | `stock-analysis-global-markets` | `get_global_market_news` |
| A-share multi-source financial download, metrics and peers | `stock-analysis-financials` | `download_stock_financials`, `analyze_stock_financials` |
| Index, ETF/LOF, fund NAV, convertible and exchange bond queries | This router; see `docs/multi-asset-data.md` | `list_market_instruments`, `get_market_quote`, `get_market_kline`, `get_fund_nav`, `get_convertible_bond_info` |

Multi-asset requests do not require stock initialization. Start with `list_market_instruments` for the requested asset_type and preserve the returned market/type/code identity. Exchange instruments require an explicit market or prefix: never apply A-share prefix guessing to ETFs, indices or bonds. Fund directory entries use otc for NAV identity and may include listed funds; this does not imply OTC subscription eligibility. Keep NAV separate from OHLC prices, IOPV separate from published NAV, and conversion triggers separate from redemption announcements. Preserve paging, stale/offline warnings and quote dates. New assets are queried on demand in namespaced JSON caches, not included in stock screening, chart rendering, pattern scans or trading backtests. Bond coverage is SH/SZ exchange quotes only, may include convertibles, and has no interbank/yield/ratings coverage.

News/financial requests do not require stock-history initialization. Respect offline=true when downloads are paused. Financial providers have different statement/indicator/history capabilities; inspect the returned limits. These tools do not provide a complete web search or real-time global quotes.

## Shared analysis contract

1. Separate observed data, calculated evidence, interpretation, and conditional scenario.
2. Prefer exact dates and prices over adjectives such as “near support”.
3. Separate four layers: observed structure, relative position, interpretation, and price event. `get_key_levels.data.market_structure` contains the new multi-scale observation/position snapshot; the legacy full-market scanners still use `trend_pullback`, `w_bottom`, `m_neckline`, `box_breakout`, and `ma_rebound`.
4. Resolve timeframe conflicts explicitly. Monthly and weekly structure constrain the daily thesis; intraday bars refine timing but do not overturn a broken higher timeframe.
5. Every bullish thesis needs confirmation conditions, invalidation level, nearby resistance, chase risk, and data-quality caveats.
6. Do not fabricate missing values, pad a candidate list, claim a causal explanation from price patterns, or issue deterministic buy/sell commands.
7. Backtest discoveries remain candidate rules. Production filters change only after out-of-sample evidence and explicit human approval.

## Current structure-engine boundary

- A trendline is a two-anchor candidate and becomes confirmed only after a later independent third touch. Report its status and touch indices.
- A confirmed diagonal is local, with a bounded anchor span and extension window. If the structure snapshot marks it `horizontal_level`/`converted`, treat it as horizontal support or resistance and do not extrapolate the old slope.
- Structure snapshots are as-of calculations: do not use a future touch to explain an earlier daily result. Channels must expose whether the second boundary is an independent confirmed line or a fixed translation of the confirmed baseline.
- `market_structure` includes parallel channels, horizontal ranges, non-symmetric `w_bottom`/`m_top` skeletons, direction-aware retracement/projection objects, and dependency-deduplicated price zones.
- Fibonacci is a position framework tied to a named scale and A/B or A/B/C anchors. A touched ratio is not automatically support, resistance, or reversal.
- Wave parsing is intentionally not implemented in the new engine. Do not invent Elliott labels or treat the legacy local wave helper as a production count.
- SQLite alert primitives exist in the Python package, but no MCP background worker or subscription tool is registered yet. Do not claim continuous monitoring, push delivery, or that an alert has been scheduled.

## Tool result handling

All MCP results use `{data, meta, warnings, error}`. Check `error` first, preserve `warnings`, and cite `meta.fetched_at` or the underlying market-data cutoff when freshness matters.
