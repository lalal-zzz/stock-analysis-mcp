# Architecture

`stock-analysis-mcp` is a local-first A-share data and research service. It separates deterministic data/calculation work from agent interpretation.

## Runtime boundary

```text
MCP client
  → index.js (Node ESM stdio shim)
  → python -m stock_analysis_mcp.server
  → 30 registered MCP handlers
  → data / tools / strategies
  → user-owned SQLite databases and report/chart files
```

The Node shim resolves Python from `STOCK_ANALYSIS_PYTHON`, the managed runtime file, then `python`. The Python server owns MCP semantics and wraps every result in `{data, meta, warnings, error}`.

## Source layout

```text
src/stock_analysis_mcp/
├── core/
│   ├── config.py              # env → config.toml → defaults
│   ├── constants.py           # schema/indicator/pattern versions and research defaults
│   └── registry.py            # future provider/feature extension registries
├── data/
│   ├── providers/             # Tencent, Sina, Sohu and board-name mapping adapters
│   ├── network.py             # curl_cffi, cookies, IPv4, retries and circuit breakers
│   ├── sources.py             # spot, daily K-line, popularity and trade-calendar sources
│   ├── assets.py              # explicit market/type/code, multi-asset paging, quotes/K-lines/NAV
│   ├── storage/               # stock/sector SQLite schemas, readers and writers
│   ├── search.py              # local queries and coverage/status reporting
│   ├── sync.py                # quick/research/full initialization and daily sync
│   ├── build/                 # rebuild, backfill, capture, indicators and cleanup
│   ├── indicators.py          # cached technical/factor calculations
│   ├── paging.py              # bounded concurrent pagination
│   ├── util.py                # parsers and shared conversion helpers
│   └── progress.py            # stderr-only progress abstraction
├── tools/
│   ├── data_manager.py        # data/status/screening facade
│   ├── stock_data.py          # stock and multi-period K-lines
│   ├── sector_data.py         # board lists, members and Eastmoney K-lines
│   ├── analysis.py            # legacy single-stock technical summary
│   └── research.py            # top-20 ranking and per-stock evidence packet
├── strategies/
│   ├── patterns/              # pivots, key levels, detectors and dual universe
│   ├── pattern_backtest.py    # event-signal collection and detailed CLI reports
│   ├── pattern_optimize.py    # time-split beam-search candidates
│   ├── similarity/             # cross-stock/timeframe normalized pattern matching
│   ├── structure/              # structure and key-level primitives
│   └── position/               # position/risk helpers
│   └── trading_backtest.py    # callable event + executable trading backtest
├── charting.py                # daily/weekly/monthly PNG rendering
├── cli.py                     # maintenance and strategy CLI
└── server.py                  # 30-tool MCP registry

skills/                        # thirteen Agent Skills, discovered dynamically
index.js                       # Node-to-Python stdio shim
bin/ and lib/                  # installer, configuration and agent adapters
```

## Data flow

```text
providers
  → normalization and source metadata
  → adjustment-aware K-lines
  → SQLite + coverage/version state
  → indicators and key levels
  → five detector families
  → high-level score/lifecycle/Fibonacci evidence
  → 20 evidence packets and charts
  → agent review
  → event/trading backtest
  → candidate rule requiring human approval
```

## Persistence model

- Stock K-line logical key: `symbol + date + adjust_type`.
- Legacy `symbol + date` databases migrate in place; QFQ stays in the original table and other adjustments can use sidecar tables.
- `source` and `fetched_at` preserve provenance.
- `data_coverage` records period, range, rows, source, status and last error.
- `pattern_signals` caches normalized lifecycle/scoring evidence with engine version.
- Schema, indicator and pattern-engine versions are independent. Indicator changes must not trigger K-line downloads.
- Daily refresh is incremental: only stale symbols are fetched, with a short overlap window; indicators are recalculated from the merged local history.
- Coverage is treated as a readiness contract: partial/degraded provider results never overwrite a healthy prior snapshot or claim full-market readiness.
- WAL mode supports concurrent readers; writes remain serialized through the storage layer.

## Provider policy

- Stock daily K-line: Tencent primary, then akshare/Sina, then Sohu.
- Sector historical K-line: Eastmoney only. Mixing board providers changes constituents and volume scale, so stale data is preferable to silent cross-source corruption.
- Eastmoney K-line calls use host rotation and a circuit breaker. During cooldown, retain completed data and report partial coverage.
- New network calls must use `data/network.py`, not direct `requests` or `httpx`.

## Research contracts

- Full-market output requires at least 95% eligible-symbol K-line coverage.
- Low-level pattern scanning has five detector families. High-level research canonicalizes `m_neckline` to `neckline_reclaim`, `ma_rebound` to `major_ma_rebound`, and treats Fibonacci confluence as a sixth evidence category.
- `screen_rising_candidates` ranks up to 20; `prepare_stock_analysis` supplies monthly/weekly/daily values and charts for independent agent review.
- Backtests use confirmed historical information and next-open entry. Results and common factors never mutate production rules automatically.

## Extension rules

- Put provider adapters in `data/providers/` and normalize them before storage.
- Put reusable calculations/detectors in `data/` or `strategies/`.
- Keep MCP handlers thin; add a registered tool only for a stable user-facing workflow.
- When the registry changes, synchronize `package.json`, both README files, AGENTS/CLAUDE guidance, and affected Skills.
- Add migration and coverage tests for every persistent-schema change.
