# CLAUDE.md

Essential guidance for Claude Code. See [AGENTS.md](AGENTS.md) for operational detail and [ARCHITECTURE.md](ARCHITECTURE.md) for design contracts.

## Project

Local-first A-share intelligent research MCP. A Node ESM shim launches the Python stdio server, which exposes 25 tools and stores user-owned stock/sector data in WAL-mode SQLite. Twelve Agent Skills compose the tools into data maintenance, screening, chart review, single-stock analysis, multi-timeframe analysis, rising-pattern research, and backtesting.

## Runtime

- `index.js`: resolves `STOCK_ANALYSIS_PYTHON` → managed `runtime.json` → `python`, sets `PYTHONPATH`, and proxies stdio.
- `src/stock_analysis_mcp/server.py`: real MCP server and 25-tool `@register` registry.
- `bin/` and `lib/`: installer/configuration/agent adapters; root `skills/` is copied dynamically to Claude Code, Codex and Qoder.
- Every tool result is `{data, meta, warnings, error}`.

## Important modules

- `data/network.py`: the only network entry point; curl_cffi, Edge cookies, IPv4, host rotation and circuit breakers.
- `data/storage.py`: schemas, WAL, adjustment-aware storage, in-place legacy migration, coverage and signal caches.
- `data/sync.py`: quick/research/full initialization and daily `none|tracked|all` K-line updates.
- `data/build.py`: rebuild, gap fill, daily capture, indicator maintenance and cleanup.
- `data/sources.py` and `data/providers/`: stock/sector/provider degradation.
- `strategies/patterns.py`: confirmed pivots, key levels, five detector families and stock/sector universes.
- `tools/research.py`: top-20 scoring and per-stock monthly/weekly/daily evidence packets.
- `strategies/trading_backtest.py`: event and executable trading backtests.
- `strategies/similarity.py`: normalized cross-timeframe price-volume matching and historical outcomes.
- `charting.py`: daily/weekly/monthly PNG generation.

## Commands

```bash
pip install -e ".[dev]"
pytest
pytest -m integration
npm run test:node

python -m stock_analysis_mcp.cli rebuild --dry-run
python -m stock_analysis_mcp.cli backfill --start YYYY-MM-DD
python -m stock_analysis_mcp.cli daily-capture
python -m stock_analysis_mcp.cli pattern-scan --universe stocks --strict
python -m stock_analysis_mcp.cli pattern-backtest --universe stocks --sample 300
```

Integration tests use real public APIs and local databases, so they are opt-in. There is no configured linter, formatter or type checker.

## Non-obvious invariants

1. Use `http_get`/`http_get_text`; never add direct `requests` or `httpx` calls.
2. Stock daily K-line degradation is Tencent → akshare/Sina → Sohu. Sector historical K-lines remain Eastmoney-only because cross-source constituents and volume scales differ.
3. Respect provider cooldown. Preserve partial progress and report incomplete coverage instead of retrying aggressively.
4. K-line identity includes adjustment type. Do not merge QFQ, HFQ and unadjusted prices.
5. Existing large databases migrate in place. Do not replace them with a table copy or require deletion.
6. Full-market claims require at least 95% eligible-stock K-line coverage and at least 260 bars for pattern analysis.
7. Pivots are usable only after right-side confirmation; backtest entries use next-day open.
8. Fibonacci confluence is evidence, not an independent detector. High-level aliases are `neckline_reclaim` ← `m_neckline` and `major_ma_rebound` ← `ma_rebound`.
9. Backtests and optimizers create candidate rules only and never mutate production filters.
10. When tools change, synchronize `package.json`, both READMEs, AGENTS/CLAUDE guidance and affected Skills.

## Data workflow

Call `get_data_status` first. Compare freshness with the latest completed trading day, not “today”. Reuse existing data; use `init_full_data(mode="research")` only when market-wide coverage is missing, and `update_daily_data(stock_kline_mode="tracked")` for normal daily maintenance.

The advanced loop is:

```text
status/update → screen_rising_candidates(top_n=20)
→ prepare_stock_analysis for every result
→ agent reads month/week/day charts
→ commonality comparison
→ backtest_pattern_strategy(mode="both")
→ explicit human approval before rule activation
```

## Skill format

Root Skills follow `skills/<skill-id>/SKILL.md` with YAML `name` and discriminating `description`. The installer discovers folders dynamically; do not maintain a separate hard-coded skill map.
