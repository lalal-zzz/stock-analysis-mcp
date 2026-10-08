---
name: stock-analysis-data-init
description: Initialize, inspect, incrementally update, migrate, and troubleshoot the local Stock Analysis SQLite databases. Use for 数据初始化, 数据更新, 数据覆盖率, 数据库状态, 缺口回填, storage paths, or stale market data.
---

# Local data initialization and maintenance

Preserve existing data. Start with `get_data_status`; never recommend deleting a database merely because its schema or latest date is old.

## Freshness decision

1. Determine the latest completed A-share trading day. On weekends and holidays, “latest” is the previous trading day.
2. Inspect each dataset independently: stock K-lines, stock indicators, spot snapshots, popularity, combined rows, sector lists, sector K-lines, sector members, and sector indicators.
3. Use coverage, not only `MAX(date)`. A single current row does not make a universe current.
4. Full-market research requires at least 95% eligible-stock K-line coverage and at least 260 bars per analyzed symbol.

## Reuse or initialize

- Existing database: run incremental update/backfill. Schema migrations are idempotent and preserve old rows.
- Empty database, quick lookup use: `init_full_data(mode="quick")`.
- Full-market screening: `init_full_data(mode="research", workers=6, resume=true)`; target is at least 320 daily bars plus indicators.
- Long-history backtest: `init_full_data(mode="full", workers=6, resume=true)`.
- Legacy callers using `quick=true/false` remain supported, but new workflows should use explicit `mode`.

K-line identity is `symbol + date + adjust_type`. Legacy QFQ rows remain in place; additional adjustment variants may use compatibility sidecar tables. Indicator, schema, and pattern-engine versions are tracked separately so an indicator upgrade does not force a K-line download.

## Daily and gap updates

Use `update_daily_data` after market close:

```text
update_daily_data(
  stock_kline_mode="tracked",
  include_sector_members=true,
  skip_non_trading_day=true
)
```

- `none`: quotes/ranks/sectors only.
- `tracked`: update symbols represented in K-line coverage; default and safest daily mode.
- `all`: update the entire stock list; disclose runtime and provider-rate-limit risk.

Use `sync_stock_kline_universe` for explicit full-universe or symbol-list K-line synchronization. Keep `resume=true`; if a provider is cooling down, retain completed rows and retry only failed/missing coverage later.

CLI maintenance is appropriate for local operators:

```bash
python -m stock_analysis_mcp.cli backfill --start YYYY-MM-DD --end YYYY-MM-DD
python -m stock_analysis_mcp.cli daily-capture
python -m stock_analysis_mcp.cli cleanup --dry-run
```

Never run `rebuild --force`, delete a DB, or VACUUM a large live DB unless the user explicitly requests it and the exact paths and recovery plan are confirmed.

## Provider and coverage caveats

- Stock daily K-line fallback: Tencent → akshare/Sina → Sohu. Adjustment/source metadata must remain visible.
- Sector historical K-lines are Eastmoney-only to avoid cross-provider volume discontinuities. A current sector list does not imply every sector K-line is current.
- Eastmoney K-line hosts can impose temporary IP limits. Respect the circuit breaker; do not hammer all hosts during cooldown.
- A current popularity snapshot can exist on a non-trading day; distinguish ranking date from price-bar date.

## Storage paths

Resolution is environment variable → `~/.stock-analysis/config.toml` → platform default. Use `stock-analysis setup --data-root <dir>` to change the root. Report the resolved paths from `get_data_status` rather than assuming them.

## Completion report

For inconsistent adjustment histories, run `data-audit` before `data-repair`; consult `docs/data-repair.md`.
Do not treat raw Sohu bars as adjusted bars or merge partial histories from different providers.
Report verified-basis and fresh trading-day coverage separately from historical row coverage.

Return the expected trading date, latest date and coverage for each updated dataset, successes/failures, provider warnings, and remaining gaps. Never say “full-market update complete” below 95% coverage.
