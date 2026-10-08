---
name: stock-analysis-report-generation
description: Produce an evidence-based technical research report for one Chinese A-share using local data, monthly/weekly/daily structure, exact key levels, patterns, sector context, popularity, and risk scenarios. Use for 单股分析, 深度分析, 技术报告, 支撑阻力, 风险评估, or 研报.
---

# Single-stock evidence report

Build a decision-audit-friendly report, not a promotional “institutional” verdict. Clearly distinguish source data, calculations, interpretation, and conditional scenarios.

## Assemble the evidence

Prefer `prepare_stock_analysis(symbol, days=500, include_chart=true)` because it packages data quality, monthly/weekly/daily summaries, key levels, recent patterns, sector context, popularity, and chart paths.

Supplement only when needed:

- `generate_stock_report(symbol)`: legacy daily technical/risk summary.
- `analyze_stock_financials(symbol, periods=8)`: optional dated multi-period financial evidence; retain cumulative-period and missing-data caveats.
- `get_stock_related_news(symbol, days=30)`: optional cited company/sector news timeline; distinguish retrieval association from verified company exposure.
- `get_key_levels(universe="stocks", symbol=...)`: legacy flat levels plus the unified `market_structure` snapshot.
- `get_pattern_history(universe="stocks", symbol=...)`: historical detector context.
- `get_stock_kline_period`: live weekly/monthly or intraday timing data when the request requires it.

Check every MCP envelope for `error` and `warnings`. State the price-bar cutoff separately from the retrieval time.

## Analysis order

1. Data quality: adjustment, first/last date, bar count, stale/missing fields.
2. Monthly structure: long-cycle direction, historical range, major resistance/support.
3. Weekly structure: higher highs/lows, MA arrangement, impulse versus correction.
4. Daily structure: active pattern, lifecycle, volume, momentum, and exact key levels.
5. Optional intraday structure: timing context only; identify whether the bar is incomplete.
6. Sector and popularity: supporting context with their own freshness dates, not proof of future movement.
7. Bull/base/bear scenarios with confirmation and invalidation.

## Numeric requirements

- Quote every referenced MA, neckline, trendline, pivot, box boundary, and Fibonacci level as an actual price when available.
- For trendlines, state scale, two anchors, third-touch confirmation, current status, and current line price. Keep two-point lines labeled candidate.
- State the validity window. Converted long-span lines must be reported as horizontal support/resistance with their retained level, never as an active diagonal trendline.
- For a stock report, include `sector_context`: primary industry, selected concepts, sector trend, breadth, flow, alignment label, and K-line coverage warning. Keep stock evidence and sector evidence in separate fields so sector strength does not overwrite a broken stock structure.
- For channels/ranges, report both boundaries and each side's touch evidence. Do not infer a complete channel from one confirmed baseline.
- For W/M, preserve unequal left/right durations and distinguish bearish `m_top` from legacy bullish `m_neckline`/`neckline_reclaim`.
- For position evidence, name the A/B anchor and scale. Use C only for projection; distinguish retracement ratio, A-based position ratio, C-based projection ratio, and movement phase.
- Describe confluence by deduplicated dependency groups/families, not the number of overlapping labels.
- Give the dates/prices defining the selected swing and calculate its move and retracement.
- Include MA20/60/120/250, 5/20-day volume ratios, ATR or ATR%, recent 5/10/20/60-day returns, and 20/60/120-day range where available.
- If multiple pivot selections are plausible, provide the main and alternate level instead of claiming false precision.
- Do not infer fundamental value from PE/PB alone or compare “industry valuation” without a real peer dataset.

## Risk framing

Provide nearest support, invalidation, overhead resistance, gap/limit/suspension/liquidity risk, higher-timeframe conflict, chase distance, and missing-data risk. A simple reward/risk ratio may be shown only as a scenario based on explicit entry, stop, and target assumptions; it is not a success probability.

Do not prescribe a personalized allocation or deterministic buy/sell action unless the user supplies constraints and explicitly asks for a hypothetical plan. Even then, label it educational and conditional.

## Output structure

Use:

1. concise conclusion and confidence;
2. data-quality block;
3. monthly/weekly/daily evidence table;
4. pattern lifecycle and program-versus-chart agreement;
5. exact key-level table;
6. volume/momentum and sector/popularity context;
7. bull/base/bear scenario table;
8. invalidation and risks;
9. limitations and non-advisory statement.

Every conclusion must be traceable to a date, price, calculated metric, or inspected chart feature.

The new engine does not implement wave parsing, and current MCP tools do not start persistent alert monitoring. Do not include Elliott labels or claim that a report has scheduled future warnings.
