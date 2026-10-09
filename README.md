[English](README.md) | [中文](README.zh-CN.md)

# Stock Analysis MCP + Skills

[![npm version](https://img.shields.io/npm/v/stock-analysis-mcp.svg)](https://www.npmjs.com/package/stock-analysis-mcp)
[![Python](https://img.shields.io/pypi/pyversions/stock-analysis-mcp.svg)](https://pypi.org/project/stock-analysis-mcp/)
[![License](https://img.shields.io/github/license/lalal-zzz/stock-analysis-mcp)](LICENSE)

**Stock Analysis MCP + Skills** is a local-first A-share analysis workbench for AI Agents. It combines multiple public market-data providers into reusable local SQLite evidence: market data, indicators, sector flow, chart structure, multi-timeframe review, research reports, and reproducible backtests.

It is designed for a complete research loop: **check data quality → build a candidate universe → inspect numeric and chart evidence → compare scenarios → validate rules with out-of-sample backtests**. It does not provide guaranteed predictions or automatic trading instructions.

See the [unified implementation plan](IMPLEMENTATION_PLAN.md) for the data, rising-pattern, per-stock AI review, and backtest contracts.

## News and financial evidence

News tools and Skills provide bounded news search, company/concept timelines, global macro context and multi-period financial metrics. Sources are official Fed/ECB RSS and Eastmoney search/financial data. Use `offline=true` when downloads are paused. A-share financial downloads support Sohu/Sina statements and Tencent summaries/composition, with source-specific limits. Real-time global quotes and an industry-chain knowledge graph remain outside this implementation. See [source documentation](docs/information-sources.md).

## Agent setup (npm)

```bash
npm install -g stock-analysis-mcp
stock-analysis install --agents auto   # auto-configure detected Agents + copy skills
stock-analysis setup --data-root "D:/MarketData"   # choose where SQLite data lives
stock-analysis doctor                  # verify runtime / config / agent status
```

**Supported Agents** (auto-detected and configured by `install --agents auto`):

| Agent | Config written | Skills installed |
|-------|----------------|------------------|
| Claude Code | `~/.claude.json` | ✅ `~/.claude/skills/` |
| Codex | `~/.codex/config.toml` | ✅ `~/.codex/skills/` |
| Cursor | `~/.cursor/mcp.json` | — (tools are self-describing) |
| VS Code Copilot | VS Code user `mcp.json` | — |
| Qoder | `~/.qoder/mcp.json` | ✅ `~/.qoder/skills/` |

The installer manages an isolated Python environment with [`uv`](https://docs.astral.sh/uv/); install `uv` first if it is not already available. It asks before changing any Agent configuration and creates a backup (restorable via `stock-analysis uninstall`). For OpenCode and other MCP clients, a manual JSON template is printed by `stock-analysis install --agents auto --dry-run`.

The data directory is user-owned and is never placed in the npm package directory. Settings resolve as **env var → `~/.stock-analysis/config.toml` → default**. Use `STOCK_ANALYSIS_DATA_DIR`, `STOCK_ANALYSIS_STOCK_DATA_DIR`, `STOCK_ANALYSIS_SECTOR_DATA_DIR`, and `STOCK_ANALYSIS_PYTHON`; legacy environment names remain supported for compatibility.

## Core Capabilities

| Capability | Details |
|------------|---------|
| Market Data | Full listed A-share universe: price / change% / PE / PB / market cap / volume ratio / turnover |
| K-lines | Daily OHLCV with forward/backward/no adjustment |
| Indicators | MA5/10/20/30/60/100/120/200/250, RSI, MACD, BOLL, KDJ, ATR%, volume ratios, returns, rolling ranges and BIAS |
| Popularity | Eastmoney real-time sentiment rankings + historical trends (rolling 1-year Guba rank) |
| Sectors | Concept and industry boards with quotes, capital flow, members, K-lines and indicators |
| Screening | 18 composable conditions: price range / PE / PB / cap / change% / volume / turnover / sector filter |
| Pattern Research | Five detector families plus canonical neckline-reclaim / major-MA-rebound labels and Fibonacci confluence evidence |
| Deep Review | Rank 20 candidates, then inspect every monthly/weekly/daily numeric and chart packet |
| Backtesting | 5/10/20-day event studies and next-open trading simulation with costs and out-of-sample checks |

---

## Quick Install

```bash
# Claude Code (recommended)
claude mcp add stock-analysis -- npx stock-analysis-mcp

# or npm global
npm install -g stock-analysis-mcp

# or pip
pip install stock-analysis-mcp
```

**Requirements**: Python >= 3.10 | Node.js >= 18

---

## Data Details

### Stock Market Data

| Category | Fields |
|----------|--------|
| Basics | Latest price, open, previous close, high, low |
| Change | Change%, change amount, amplitude, velocity |
| Volume | Volume, turnover amount |
| Activity | Volume ratio, turnover rate |
| Valuation | Dynamic PE, PB, TTM PE |
| Market Cap | Total market cap, circulating market cap |
| Trend | 60-day change%, YTD change% |
| Fund Flow | Net major capital inflow |

### K-line Data

| Category | Details |
|----------|---------|
| Daily K-line | Open, high, low, close, volume, amount |
| Adjustment | Forward (qfq) / backward (hfq) / none |
| Extra Fields | Change%, turnover rate, amplitude |
| Identity | `symbol + date + adjust_type`, with source and fetch time |
| Compatibility | Existing databases migrate in place; legacy QFQ data is reused without a bulk copy |

### Technical Indicators (auto-calculated)

| Category | Indicators |
|----------|------------|
| Moving Avg | MA5, MA10, MA20, MA30, MA60, MA100, MA120, MA200, MA250 |
| RSI | RSI6, RSI14, RSI24 |
| MACD | DIF, DEA, MACD histogram |
| Bollinger | Upper, middle, lower bands |
| KDJ | K, D, J values |
| Volatility | ATR14 and ATR% |
| Volume | VOL_MA5/10/20 and 5/20-day volume ratios |
| Returns & Range | 5/10/20/60-day returns and 20/60/120-day highs/lows |
| Bias | BIAS20, BIAS60, BIAS250 |

### Popularity Rankings

| Category | Details |
|----------|---------|
| Real-time | Eastmoney popularity board (rank, price, change%, turnover rate) |
| History Trend | Query N-day ranking trend for any stock (AES-decrypted rolling year data) |

### Sector Data

| Category | Details |
|----------|---------|
| Sector List | Concept (~400) + Industry (~80) sectors |
| Quotes | Index level, change% |
| Capital Flow | Net inflow, super-large, large, medium, small net inflow (with %) |
| Leaders | Sector leading stock |
| K-line | Historical sector index K-line (single-source Eastmoney) |
| Members | Member quotes, turnover, volume ratio, PE, PB |

### Analysis Reports

| Section | Content |
|---------|---------|
| Overview | Price, change%, PE, PB, market cap, turnover rate |
| Trend | MA arrangement, 5/10/20/60-day change%, direction assessment |
| Indicators | RSI status, MACD golden/death cross, KDJ overbought/oversold, BOLL pos, ATR |
| S&R Levels | Ranked support and resistance levels from MAs and BOLL bands |
| Risk Assessment | Overbought/oversold, volatility, valuation, liquidity, and breakdown risk |
| Scenarios | Conditional confirmation, invalidation, support/resistance and risk/reward assumptions |

---

## MCP Tools (25 tools)

| Tool | Module | Purpose |
|------|--------|---------|
| `init_full_data` | `data_manager` | `quick`, `research` (320-bar universe), or long-history `full` initialization; legacy `quick` calls remain compatible |
| `update_daily_data` | `data_manager` | Daily incremental refresh with `stock_kline_mode=none|tracked|all` |
| `get_data_status` | `data_manager` | Local DB status (row counts / last update / paths) |
| `screen_stocks` | `data_manager` | Universal screening: 18 conditions + sector filter + name search + sort |
| `get_kline_local_or_net` | `data_manager` | Daily K-line, local-first with cached technical indicators |
| `get_stock_kline_period` | `stock_data` | Multi-period K-line (1/5/15/30/60m, day/week/month, real-time) |
| `get_rank_trend_data` | `data_manager` | Historical popularity ranking trend (N days) |
| `get_sector_list` | `sector_data` | Concept/industry sector list with capital flow |
| `get_stock_belong_sectors` | `data_manager` | Reverse lookup: stock $\rightarrow$ sectors |
| `generate_stock_report` | `analysis` | Technical report: trend / support & resistance / risk / position |
| `render_stock_charts` | `charting` | Render daily/weekly/monthly candlestick PNGs with auto trendlines (chart extra) |
| `scan_patterns` | `strategies/patterns` | Stock chart-pattern scan (normal or strict filter) |
| `scan_sector_patterns` | `strategies/patterns` | Sector chart-pattern scan (concept/industry or symbols) |
| `get_pattern_history` | `strategies/patterns` | Historical pattern signals for one symbol (stock/sector) |
| `get_key_levels` | `strategies/patterns` | Current key levels: MA system / Fibonacci / structure (highs/lows) |
| `sync_stock_kline_universe` | `data/sync` | Research-mode universe K-line and indicator synchronization with coverage reporting |
| `screen_rising_candidates` | `tools/research` | Rank the top 20 rising-pattern candidates with multi-timeframe evidence |
| `prepare_stock_analysis` | `tools/research` | Build the monthly/weekly/daily numeric and chart packet for per-stock AI review |
| `find_cross_timeframe_similar_patterns` | `strategies/similarity` | Compare the latest N bars with all historical N-bar windows and estimate conditional outcome probabilities |
| `backtest_pattern_strategy` | `strategies/trading_backtest` | Event study plus executable 5–20 day trading simulation |
| `search_market_news` | `data/information` | 带来源时间、链接与缓存状态的有限新闻检索 |
| `get_stock_related_news` | `data/information` | 公司与概念消息的关联依据和时间线 |
| `get_global_market_news` | `data/information` | 官方政策发布与有限全球市场新闻 |
| `analyze_stock_financials` | `data/information` | 多期财务指标及指定同业同报告期比较 |
| `download_stock_financials` | `data/financials` | A股多源财务下载：搜狐/新浪报表、腾讯摘要与主营构成、东方财富指标；独立缓存与分析 |


### MCP call contract

Every tool publishes a JSON Schema. The server validates required and unknown fields, basic types, enum values, and numeric ranges. Results use `{data, meta, warnings, error}`: check `error` first, preserve `warnings`, and use both `meta` and the data cutoff date for freshness. The server does not provide persistent monitoring, push notifications, or deterministic buy/sell decisions.

Ships with **13 Agent Skills** (installed automatically by `stock-analysis install`):

| Skill | Purpose |
|-------|---------|
| `stock-analysis` | Main index — check data readiness before research |
| `stock-analysis-data-init` | Existing-data reuse, initialization, coverage-aware updates and troubleshooting |
| `stock-analysis-stock-screening` | Composing screening conditions and workflows |
| `stock-analysis-report-generation` | Evidence-based single-stock report and conditional scenarios |
| `stock-analysis-multi-timeframe` | Monthly/weekly/daily/intraday analysis and conflict resolution |
| `stock-analysis-strategy-backtest` | Chart pattern backtesting and parameter optimization |
| `stock-analysis-chart-trend` | Per-stock visual structure attribution with pivot and line uncertainty checks |
| `stock-analysis-rising-patterns` | Rank 20 candidates, deeply review every monthly/weekly/daily chart, and summarize common traits |
| [a-share-dividends](skills/a-share-dividends/SKILL.md) | Historical/latest dividends and announced future dates, with source and coverage checks |

### A-share dividends

This project owns [a-share-dividends](skills/a-share-dividends/SKILL.md), migrated from the sibling trading repository. Its standalone script queries Eastmoney by symbol or report period, checks pagination and archives raw JSON. Formal-announcement verification is an agent workflow; payment dates, full-market latest-event coverage and account adjustments are not automatically completed by this script. It is a Skill helper, not an additional registered MCP tool.

```powershell
python -X utf8 skills/a-share-dividends/scripts/fetch_dividends.py --symbol 000001 --as-of 2026-10-09 --output <output-dir>/000001-dividends-20261009.json
```

Use an existing output directory and a new filename. Source limits and field definitions are in [references/sources.md](skills/a-share-dividends/references/sources.md).

### What each Skill delivers

The Skills are output-oriented: each one turns MCP tool results into a consistent, reviewable artifact rather than a loose paragraph.

| Skill | Result view | Typical result contents |
|-------|-------------|-------------------------|
| `stock-analysis` | Research route card | Chosen workflow, data-quality gate, tool chain, warnings, and next action |
| `stock-analysis-data-init` | Data health report | Expected trading date, latest date, per-dataset coverage, successes/failures, provider warnings, remaining gaps |
| `stock-analysis-stock-screening` | Sortable candidate table | Code/name, cutoff, filters, pattern/stage, score components, sector context, support/resistance/invalidation, rejected or missing-data notes |
| `stock-analysis-report-generation` | Single-stock evidence report | Conclusion/confidence, data block, monthly/weekly/daily evidence, key-level table, scenarios, risks, limitations |
| `stock-analysis-multi-timeframe` | Timeframe comparison matrix | Monthly/weekly/daily/intraday metrics, agreement/conflict matrix, primary/alternate view, exact confirmation and invalidation levels |
| `stock-analysis-chart-trend` | Chart review card | Chart cutoff, pivots, trendlines/channels/ranges/W-M/Fibonacci zones, program-vs-chart agreement, volume evidence |
| `stock-analysis-rising-patterns` | Candidate cards + comparison | One evidence card per returned stock, reviewed ranking, current-vs-history commonality, and coverage caveats |
| `stock-analysis-strategy-backtest` | Backtest result pack | Universe/period, coverage, signal/trade counts, assumptions, event and portfolio metrics, train/test and yearly stability, report/CSV paths |

Example compact result card:

```text
000001 平安银行 · 2026-09-17 · qfq · 486 daily bars
Structure: weekly pullback / daily neckline reclaim (candidate → triggered)
Evidence: close 12.34 | MA20 12.10 | neckline 12.28 | volume 1.42x
Confirm: daily close above 12.28 and hold; Invalidate: close below 11.86
Warnings: sector data delayed; chart confirmation available
```

Exact values, dates, coverage and warnings always come from the current MCP response; the example above only illustrates the presentation format.

---

## Research workflow

```text
get_data_status
  → update or fill only missing coverage
  → screen_rising_candidates(top_n=20)
  → prepare_stock_analysis for every returned stock
  → inspect monthly, weekly and daily charts
  → compare reviewed candidates and extract common traits
  → backtest_pattern_strategy(mode="both")
  → human approval before any production-rule change
```

A full-market claim requires at least 95% eligible-stock K-line coverage. The five low-level pattern detectors are `trend_pullback`, `w_bottom`, `m_neckline`, `box_breakout`, and `ma_rebound`. High-level reports rename two of them to `neckline_reclaim` and `major_ma_rebound`; `fibonacci_confluence` is supporting evidence rather than a standalone reversal detector.

## Unified CLI

In addition to the MCP server, a unified CLI is provided for database maintenance, backfilling, pattern analysis, and strategy backtesting:

```bash
# Rebuild local databases with dry-run preview
python -m stock_analysis_mcp.cli rebuild --dry-run
python -m stock_analysis_mcp.cli rebuild --workers 8 --with-sectors

# Auto-detect and backfill historical data gaps
python -m stock_analysis_mcp.cli backfill --start 2026-01-01

# Daily evening capture after market close
python -m stock_analysis_mcp.cli daily-capture

# Cleanup database redundancy and VACUUM
python -m stock_analysis_mcp.cli cleanup

# Pattern scanning and backtesting
python -m stock_analysis_mcp.cli pattern-scan --universe sectors --date 2026-08-14
python -m stock_analysis_mcp.cli pattern-backtest --universe stocks --sample 300
python -m stock_analysis_mcp.cli pattern-optimize --cache signals.csv
```

---

## Local Database

Data is stored in local SQLite databases using WAL mode for high-concurrency read/write operations:

| Database | Default Path (Windows) | Default Path (Linux/macOS) | Contents |
|----------|------------------------|----------------------------|----------|
| Stock DB | `~/Desktop/股票信息/stock_data.db` | `~/.stock-analysis/data/stocks/stock_data.db` | stock quotes + adjustment-aware K-lines + popularity + indicators + combined + coverage/signals |
| Sector DB | `~/Desktop/分析板块/sector_data.db` | `~/.stock-analysis/data/sectors/sector_data.db` | sector quotes + capital flow + Eastmoney-only K-lines + members + indicators |

Override with `STOCK_ANALYSIS_STOCK_DATA_DIR` and `STOCK_ANALYSIS_SECTOR_DATA_DIR`.

---

## Environment Variables

Resolution order: **env var → `~/.stock-analysis/config.toml` → default**.

| Variable | Default | Purpose |
|----------|---------|---------|
| `STOCK_ANALYSIS_PYTHON` | managed runtime $\rightarrow$ `python` | Python interpreter for the Node shim |
| `STOCK_ANALYSIS_DATA_DIR` | Win: `~/Desktop`; Linux/macOS: `~/.stock-analysis/data` | Root directory for both databases |
| `STOCK_ANALYSIS_STOCK_DATA_DIR` | `<data_root>/股票信息` (Linux/macOS: `stocks`) | Stock database directory |
| `STOCK_ANALYSIS_SECTOR_DATA_DIR` | `<data_root>/分析板块` (Linux/macOS: `sectors`) | Sector database directory |
| `STOCK_ANALYSIS_CONFIG` | `~/.stock-analysis/config.toml` | Override config.toml path |
| `EASTMONEY_COOKIE` | auto-extract from Edge | Manual Eastmoney API cookie string |

---

## Client Configuration

### Claude Desktop / Claude Code

```bash
claude mcp add stock-analysis -- npx stock-analysis-mcp
```

Or manually in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "stock-analysis": {
      "command": "npx",
      "args": ["stock-analysis-mcp"]
    }
  }
}
```

### Codex

Run `stock-analysis install --agents codex`, or add to `~/.codex/config.toml`:

```toml
[mcp_servers.stock-analysis]
command = "npx"
args = ["stock-analysis-mcp"]
```

### Cursor

Run `stock-analysis install --agents cursor`, or add the JSON block to `~/.cursor/mcp.json`.

### VS Code Copilot

Run `stock-analysis install --agents copilot`, or add to VS Code user `mcp.json` (Windows: `%APPDATA%\Code\User\mcp.json`):

```json
{
  "servers": {
    "stock-analysis": {
      "type": "stdio",
      "command": "npx",
      "args": ["stock-analysis-mcp"]
    }
  }
}
```

### Qoder

Run `stock-analysis install --agents qoder`, or add to `~/.qoder/mcp.json`.

### OpenCode / Other MCP Clients

```json
{
  "mcpServers": {
    "stock-analysis": {
      "command": "npx",
      "args": ["stock-analysis-mcp"]
    }
  }
}
```

### Direct Python Execution

```json
{
  "mcpServers": {
    "stock-analysis": {
      "command": "python",
      "args": ["-m", "stock_analysis_mcp.server"]
    }
  }
}
```

---

## Example Usage

```python
# Reuse an existing DB; initialize only when required
init_full_data(mode="quick")

# Recommended before full-market rising-pattern screening
init_full_data(mode="research", workers=6, resume=True)

# Or long-history full init (large resumable download for backtesting)
init_full_data(mode="full", workers=6, resume=True)

# Daily update after market close; compare freshness with the latest trading day
update_daily_data(stock_kline_mode="tracked")

# 3. Screen: change > 3%, PE < 30, volume ratio > 1.5
screen_stocks({"min_change_pct": 3, "max_pe": 30, "min_volume_ratio": 1.5})

# 4. Screen: semiconductor sector with PE < 50, sorted by popularity rank
screen_stocks({"max_pe": 50}, sector_code="BK1090", sort_by="popularity_rank")

# 5. Search: stocks containing "银行" (Bank)
screen_stocks(name_keyword="银行")

# 6. Get Ping An Bank K-line with cached indicators
get_kline_local_or_net("000001", days=250)

# 7. Ping An Bank 30-day popularity trend
get_rank_trend_data("000001", days=30)

# 8. Generate full analysis report
generate_stock_report("000001")

# 9. Chart pattern scan: market-wide strictly filtered signals
scan_patterns(strict=True)

# 10. Rank 20 rising candidates, then prepare every candidate for AI chart review
screen_rising_candidates(top_n=20, strict=True)
prepare_stock_analysis("000001", days=500, include_chart=True)

# 11. Validate event outcomes and executable portfolio assumptions
backtest_pattern_strategy(mode="both", split="2022-01-01")
```

---

## Development

```bash
git clone https://github.com/lalal-zzz/stock-analysis-mcp.git
cd stock-analysis-mcp
pip install -e ".[dev]"
pytest                    # Python unit tests
npm run test:node         # Node installer tests
pytest -m integration     # Opt-in: real network + local DB writes
```

## Data consistency and maintenance

See [data repair and validation](docs/data-repair.md) for read-only audits, archived full-history repair,
resumable retries, local zone alerts, reviewed rule activation, and daily-equity backtest outputs.
Historical row coverage, current trading-day coverage, and verified price-basis coverage are reported separately.
The installer includes chart dependencies. CI tests both supported MCP SDK generations before publishing.

## License

MIT License

### News and financial Skills

| Skill | Purpose |
|---|---|
| `stock-analysis-news` | News evidence and citations |
| `stock-analysis-related-news` | Company/concept event timelines |
| `stock-analysis-global-markets` | Macro policy and global market context |
| `stock-analysis-financials` | Multi-period financial metrics and peers |
