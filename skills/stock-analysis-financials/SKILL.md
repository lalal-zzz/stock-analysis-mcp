---
name: stock-analysis-financials
description: Download and analyze A-share financial data from Sohu, Tencent, Sina and Eastmoney, with source-specific report types, units and history limits. Use for 财务数据下载、财报、盈利质量、现金流或同业比较.
---

# stock-analysis-financials

Use `download_stock_financials(symbol="600519", provider="sohu", report_type="all", periods=4)` for source-specific reports. Providers are auto/all/eastmoney/sohu/tencent/sina; types are indicators/balance/income/cashflow/revenue_segments/all. Explicit providers never silently substitute another provider. Capabilities and actual period counts are returned.

Sohu supplies the latest-page indicators and three statements (normally 5/4 periods); Tencent supplies one latest summary and recent revenue composition; Sina supplies up to 20 statement periods; Eastmoney supplies up to 20 indicator periods. Do not present Tencent as an historical three-statement API or Sohu's current page as full history. Mainland A shares only; reject HK/US/funds/B shares.

Use `analyze_stock_financials(symbol="600519", provider="auto", periods=8, peers=[], compare_sources=false, offline=false)` for indicators and peers. Automatic indicator fallback goes Eastmoney → Sohu → Tencent. compare_sources=true keeps indicator sources separate and discloses period-matched EPS/BPS differences. Supply at most five verified peers only when needed.

CLI export: `python -m stock_analysis_mcp.cli financial-download --symbol 600519 --provider sohu --report-type all --periods 4 --output reports/600519-finance.json`. It exports JSON and long-form CSV with provider/report date/raw value. No market-wide jobs are started. offline=true or --offline reads only caches; --dry-run prints the plan without network or writes.

Read report_date, published_at/published_date, period_basis, source URLs and cache status. Amounts normalize to CNY, _pct fields are percentage points, per-share amounts are CNY/share. Sohu's indicator amount sections are 万元 while statement amounts are 元. Tencent may use 亿/万元 suffixes. Preserve raw values, sections, audit status and consolidated scope when returned. Balance-sheet values are stocks at the report date; income/cash flow are year-to-date. Do not compare a half-year amount to a full-year amount as growth or call it one quarter/TTM.

Do not equate Sohu 主营业务收入 with Tencent/Eastmoney 营业总收入, reported net profit with confirmed parent net profit, or reported ROE with weighted ROE. Keep source-disclosed differences; never average or splice providers into a synthetic report. Revenue composition dimensions overlap: do not sum product and region totals together.

Keep missing/null values visible. Compare peers only at comparison_report_date and verify business-model comparability, especially banks versus industrial firms. Discuss EPS, profitability, cash generation and liquidity/debt ratios when present; do not invent PE/PB, fair value or unavailable statement lines. Data is the current available revision, not point-in-time evidence for historical backtests. Offline=true uses existing cache and never downloads financial reports.

When local_valuation is present, cite quote_date and the local source for PE/PB, price and market capitalization. These are existing dated snapshots; dynamic PE is not an independently calculated TTM multiple. A missing local valuation does not authorize downloading the market universe.

