---
name: stock-analysis-financials
description: Analyze multi-period A-share profitability, growth, cash flow and solvency metrics, with optional same-period peer comparison. Use for 财务分析、财报、盈利质量、现金流或同业比较.
---

# stock-analysis-financials

Use `analyze_stock_financials(symbol="600519", periods=8, peers=[], offline=false)`. Supply at most five verified peers only when comparison is needed. Evidence comes from Eastmoney's financial indicators; this is not a complete three-statement parser or exchange-filing archive.

Read report_date, published_at, period_basis, source URLs and cache status. Amounts are CNY, _pct fields are percentage points, per-share amounts are CNY/share. Report-period flow metrics are year-to-date; do not compare a half-year amount to a full-year amount as growth or call it one quarter/TTM. Same-period published YoY fields can support growth analysis.

Keep missing/null values visible. Compare peers only at comparison_report_date and verify business-model comparability, especially banks versus industrial firms. Discuss EPS, profitability, cash generation and liquidity/debt ratios when present; do not invent PE/PB, fair value or unavailable statement lines. Data is the current available revision, not point-in-time evidence for historical backtests. Offline=true uses existing cache and never downloads financial reports.

When local_valuation is present, cite quote_date and the local source for PE/PB, price and market capitalization. These are existing dated snapshots; dynamic PE is not an independently calculated TTM multiple. A missing local valuation does not authorize downloading the market universe.

