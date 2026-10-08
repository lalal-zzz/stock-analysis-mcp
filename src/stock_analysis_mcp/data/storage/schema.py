"""
data/storage/schema.py — 建表 DDL / 原地迁移 / 连接工厂
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from ...core.constants import INDICATOR_VERSION, PATTERN_ENGINE_VERSION, SCHEMA_VERSION
from .paths import get_sector_db, get_stock_db

_lock = threading.Lock()
_init_lock = threading.Lock()

# 已完成建表的库路径(进程内只建一次; DDL 全部 IF NOT EXISTS, 重复执行也安全)
_schema_ready: set = set()


@contextmanager
def _write_conn(db_path: str) -> Iterator[sqlite3.Connection]:
    """锁内取连接, 自动 commit/close —— 所有写操作统一走此上下文。"""
    with _lock:
        conn = _get_conn(db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    return {row[1]: {"type": row[2], "pk": row[5]} for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_stock_schema(conn: sqlite3.Connection) -> None:
    """Idempotent, in-place migration safe for multi-gigabyte legacy databases."""
    kcols = _table_columns(conn, "stock_kline")
    kpk = [name for name, meta in sorted(kcols.items(), key=lambda x: x[1]["pk"]) if meta["pk"]]
    if kcols and kpk != ["symbol", "date", "adjust_type"]:
        for name, ddl in (("adjust_type", "TEXT NOT NULL DEFAULT 'qfq'"),
                          ("source", "TEXT DEFAULT 'legacy'"),
                          ("fetched_at", "TEXT")):
            if name not in kcols:
                conn.execute(f"ALTER TABLE stock_kline ADD COLUMN {name} {ddl}")

    icols = _table_columns(conn, "stock_indicators")
    ipk = [name for name, meta in sorted(icols.items(), key=lambda x: x[1]["pk"]) if meta["pk"]]
    if icols and ipk != ["symbol", "date", "adjust_type"]:
        if "adjust_type" not in icols:
            conn.execute("ALTER TABLE stock_indicators ADD COLUMN adjust_type TEXT NOT NULL DEFAULT 'qfq'")
    required = {
        # SQLite applies this metadata-only default to legacy rows without
        # rewriting the multi-gigabyte indicator table.  "legacy" deliberately
        # marks those cached values as stale so callers can recompute lazily.
        "indicator_version": "TEXT DEFAULT 'legacy'", "MA120": "REAL", "MA250": "REAL",
        "VOL_MA20": "REAL", "VOL_RATIO5": "REAL", "VOL_RATIO20": "REAL",
        "ATR_PCT": "REAL", "BIAS20": "REAL", "BIAS60": "REAL", "BIAS250": "REAL",
        "RETURN_5": "REAL", "RETURN_10": "REAL", "RETURN_20": "REAL", "RETURN_60": "REAL",
        "HIGH_20": "REAL", "HIGH_60": "REAL", "HIGH_120": "REAL",
        "LOW_20": "REAL", "LOW_60": "REAL", "LOW_120": "REAL",
    }
    icols = _table_columns(conn, "stock_indicators")
    for name, typ in required.items():
        if name not in icols:
            conn.execute(f"ALTER TABLE stock_indicators ADD COLUMN {name} {typ}")
    now = datetime.now().isoformat()
    for key, value in (("schema_version", SCHEMA_VERSION),
                       ("indicator_version", INDICATOR_VERSION),
                       ("pattern_engine_version", PATTERN_ENGINE_VERSION)):
        conn.execute("INSERT OR REPLACE INTO meta(key,value,updated_at) VALUES(?,?,?)", (key, value, now))
    # Backfill coverage for legacy databases so status reflects existing usable data.
    if conn.execute("SELECT 1 FROM data_coverage WHERE data_type='stock_kline' LIMIT 1").fetchone():
        return
    conn.execute("""
        INSERT OR IGNORE INTO data_coverage
            (data_type,symbol,period,adjust_type,first_date,last_date,row_count,source,
             status,data_version,updated_at)
        SELECT 'stock_kline',symbol,'daily',adjust_type,MIN(date),MAX(date),COUNT(*),
               COALESCE(MAX(source),'legacy'),
               CASE WHEN COUNT(*)>=260 THEN 'ready' ELSE 'partial' END,?,?
        FROM stock_kline GROUP BY symbol,adjust_type
    """, (SCHEMA_VERSION, now))


def _migrate_sector_schema(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "sector_kline")
    for name in ("source", "fetched_at"):
        if name not in cols:
            conn.execute(f"ALTER TABLE sector_kline ADD COLUMN {name} TEXT")
    indicators = _table_columns(conn, "sector_indicators")
    for name, ddl in (("indicator_version", "TEXT DEFAULT 'legacy'"), ("MA120", "REAL"),
        ("MA250", "REAL"), ("VOL_MA20", "REAL"), ("VOL_RATIO5", "REAL"),
        ("VOL_RATIO20", "REAL"), ("ATR_PCT", "REAL"), ("BIAS20", "REAL"),
        ("BIAS60", "REAL"), ("BIAS250", "REAL"), ("RETURN_5", "REAL"),
        ("RETURN_10", "REAL"), ("RETURN_20", "REAL"), ("RETURN_60", "REAL"),
        ("HIGH_20", "REAL"), ("HIGH_60", "REAL"), ("HIGH_120", "REAL"),
        ("LOW_20", "REAL"), ("LOW_60", "REAL"), ("LOW_120", "REAL")):
        if name not in indicators:
            conn.execute(f"ALTER TABLE sector_indicators ADD COLUMN {name} {ddl}")
    conn.execute("INSERT OR REPLACE INTO meta(key,value,updated_at) VALUES(?,?,?)",
                 ("schema_version", SCHEMA_VERSION, datetime.now().isoformat()))


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # 跨进程/跨线程并发写时等待锁而不是立刻抛 "database is locked"
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    # 首次连接时幂等建表, 避免 init_full_data 之前调用只读工具时
    # 抛出 "no such table" (如新库上直接调 get_data_status/screen_stocks)
    if db_path not in _schema_ready:
        with _init_lock:
            if db_path not in _schema_ready:
                ddl = STOCK_DDL if db_path == get_stock_db() else SECTOR_DDL
                conn.executescript(ddl)
                if db_path == get_stock_db():
                    _migrate_stock_schema(conn)
                else:
                    _migrate_sector_schema(conn)
                conn.commit()
                _schema_ready.add(db_path)
    return conn


# ════════════════════════════════════════
# 初始化建表
# ════════════════════════════════════════

STOCK_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS stock_basic (
    symbol    TEXT PRIMARY KEY,
    name      TEXT,
    raw_symbol TEXT
);

CREATE TABLE IF NOT EXISTS stock_kline (
    symbol       TEXT,
    date         TEXT,
    adjust_type  TEXT NOT NULL DEFAULT 'qfq',
    source       TEXT,
    fetched_at   TEXT,
    open         REAL,
    high         REAL,
    low          REAL,
    close        REAL,
    volume       REAL,
    amount       REAL,
    amplitude    REAL,
    change_pct   REAL,
    change_amount REAL,
    turnover_rate REAL,
    PRIMARY KEY (symbol, date, adjust_type)
);
CREATE INDEX IF NOT EXISTS idx_kline_date ON stock_kline(date);

CREATE TABLE IF NOT EXISTS stock_spot (
    symbol          TEXT PRIMARY KEY,
    name            TEXT,
    latest_price    REAL,
    change_pct      REAL,
    change_amount   REAL,
    volume          REAL,
    amount          REAL,
    amplitude       REAL,
    high            REAL,
    low             REAL,
    open            REAL,
    pre_close       REAL,
    volume_ratio    REAL,
    turnover_rate   REAL,
    pe_dynamic      REAL,
    pb              REAL,
    total_market_cap REAL,
    float_market_cap REAL,
    speed           REAL,
    sixty_day_change REAL,
    ytd_change      REAL,
    updated_date    TEXT
);

CREATE TABLE IF NOT EXISTS stock_rank (
    symbol         TEXT,
    name           TEXT,
    rank_date      TEXT,
    latest_price   REAL,
    change_pct     REAL,
    volume_ratio   REAL,
    high           REAL,
    low            REAL,
    pre_close      REAL,
    volume         REAL,
    amount         REAL,
    turnover_rate  REAL,
    popularity_rank REAL,
    PRIMARY KEY (rank_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_rank_date ON stock_rank(rank_date);
CREATE INDEX IF NOT EXISTS idx_rank_symbol ON stock_rank(symbol);

CREATE TABLE IF NOT EXISTS stock_indicators (
    symbol    TEXT,
    date      TEXT,
    adjust_type TEXT NOT NULL DEFAULT 'qfq',
    indicator_version TEXT,
    MA5       REAL, MA10 REAL, MA20 REAL, MA30 REAL, MA60 REAL, MA100 REAL, MA120 REAL, MA200 REAL, MA250 REAL,
    RSI6      REAL, RSI14 REAL, RSI24 REAL,
    DIF       REAL, DEA REAL, MACD REAL,
    BOLL_UPPER  REAL, BOLL_MIDDLE REAL, BOLL_LOWER REAL,
    KDJ_K     REAL, KDJ_D REAL, KDJ_J REAL,
    VOL_MA5   REAL, VOL_MA10 REAL, VOL_MA20 REAL, VOL_RATIO5 REAL, VOL_RATIO20 REAL,
    ATR14     REAL, ATR_PCT REAL,
    BIAS20 REAL, BIAS60 REAL, BIAS250 REAL,
    RETURN_5 REAL, RETURN_10 REAL, RETURN_20 REAL, RETURN_60 REAL,
    HIGH_20 REAL, HIGH_60 REAL, HIGH_120 REAL,
    LOW_20 REAL, LOW_60 REAL, LOW_120 REAL,
    PRIMARY KEY (symbol, date, adjust_type)
);
CREATE INDEX IF NOT EXISTS idx_ind_date ON stock_indicators(date);

-- Legacy multi-gigabyte databases keep their original (symbol,date) primary key.
-- Non-qfq variants are stored here, avoiding a full-table copy migration.
CREATE TABLE IF NOT EXISTS stock_kline_variants (
    symbol TEXT, date TEXT, adjust_type TEXT NOT NULL,
    source TEXT, fetched_at TEXT, open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL, amplitude REAL, change_pct REAL,
    change_amount REAL, turnover_rate REAL,
    PRIMARY KEY(symbol,date,adjust_type)
);
CREATE TABLE IF NOT EXISTS stock_indicator_variants (
    symbol TEXT, date TEXT, adjust_type TEXT NOT NULL, indicator_version TEXT,
    MA5 REAL, MA10 REAL, MA20 REAL, MA30 REAL, MA60 REAL, MA100 REAL, MA120 REAL, MA200 REAL, MA250 REAL,
    RSI6 REAL, RSI14 REAL, RSI24 REAL, DIF REAL, DEA REAL, MACD REAL,
    BOLL_UPPER REAL, BOLL_MIDDLE REAL, BOLL_LOWER REAL,
    KDJ_K REAL, KDJ_D REAL, KDJ_J REAL, VOL_MA5 REAL, VOL_MA10 REAL,
    VOL_MA20 REAL, VOL_RATIO5 REAL, VOL_RATIO20 REAL, ATR14 REAL, ATR_PCT REAL,
    BIAS20 REAL, BIAS60 REAL, BIAS250 REAL,
    RETURN_5 REAL, RETURN_10 REAL, RETURN_20 REAL, RETURN_60 REAL,
    HIGH_20 REAL, HIGH_60 REAL, HIGH_120 REAL, LOW_20 REAL, LOW_60 REAL, LOW_120 REAL,
    PRIMARY KEY(symbol,date,adjust_type)
);

CREATE TABLE IF NOT EXISTS data_coverage (
    data_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    period TEXT NOT NULL DEFAULT 'daily',
    adjust_type TEXT NOT NULL DEFAULT '',
    first_date TEXT,
    last_date TEXT,
    row_count INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'partial',
    last_error TEXT,
    data_version TEXT,
    updated_at TEXT,
    PRIMARY KEY (data_type, symbol, period, adjust_type)
);
CREATE INDEX IF NOT EXISTS idx_coverage_status ON data_coverage(data_type, status);

CREATE TABLE IF NOT EXISTS pattern_signals (
    universe TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    pattern TEXT NOT NULL,
    stage TEXT NOT NULL,
    score REAL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    trend_state TEXT,
    entry_status TEXT,
    support REAL,
    resistance REAL,
    neckline REAL,
    invalid_level REAL,
    fib_levels_json TEXT,
    evidence_json TEXT,
    factors_json TEXT,
    engine_version TEXT,
    calculated_at TEXT,
    PRIMARY KEY (universe, symbol, signal_date, pattern)
);

CREATE TABLE IF NOT EXISTS alert_state (
    zone_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    episode INTEGER NOT NULL,
    relation TEXT NOT NULL,
    emitted_json TEXT NOT NULL,
    invalidated INTEGER NOT NULL DEFAULT 0,
    last_timestamp TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
    idempotency_key TEXT PRIMARY KEY,
    zone_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    episode INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    close REAL NOT NULL,
    lower REAL NOT NULL,
    upper REAL NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_events_symbol_time
    ON alert_events(symbol, event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_alert_events_type_time
    ON alert_events(event_type, event_timestamp DESC);

CREATE TABLE IF NOT EXISTS structure_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    universe TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    as_of TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    data_version TEXT,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structure_snapshots_lookup
    ON structure_snapshots(universe, symbol, timeframe, as_of DESC);

CREATE TABLE IF NOT EXISTS daily_stock_info (
    trade_date   TEXT NOT NULL,
    capture_time TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    name         TEXT,
    latest_price REAL, change_pct REAL, change_amount REAL,
    volume REAL, amount REAL, amplitude REAL, turnover_rate REAL,
    high REAL, low REAL, open REAL, pre_close REAL,
    volume_ratio REAL, volume_ratio_5d REAL, amplitude_5d REAL,
    pe_dynamic REAL, pe_ttm REAL, pb REAL,
    total_market_cap REAL, float_market_cap REAL,
    speed REAL, five_min_change REAL, sixty_day_change REAL, ytd_change REAL,
    main_net_inflow REAL, sector_name TEXT,
    PRIMARY KEY (trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_daily_info_sym ON daily_stock_info(symbol, trade_date);

CREATE TABLE IF NOT EXISTS stock_popularity_rank (
    trade_date          TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    rank                INTEGER,
    rank_time           TEXT,
    rank_change         INTEGER,
    hour_rank_change    INTEGER,
    his_rank_change     INTEGER,
    his_rank_change_rank INTEGER,
    hot_rank_score      REAL,
    market_all_count    INTEGER,
    source              TEXT NOT NULL DEFAULT 'guba',
    PRIMARY KEY (trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_pop_rank_sym ON stock_popularity_rank(symbol, trade_date);

CREATE TABLE IF NOT EXISTS stock_daily_combined (
    trade_date   TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    name         TEXT,
    capture_time TEXT,
    open REAL, high REAL, low REAL, close REAL, pre_close REAL,
    latest_price REAL, change_pct REAL, change_amount REAL,
    volume REAL, amount REAL, amplitude REAL, turnover_rate REAL,
    volume_ratio REAL, volume_ratio_5d REAL, amplitude_5d REAL,
    pe_dynamic REAL, pe_ttm REAL, pb REAL,
    total_market_cap REAL, float_market_cap REAL,
    speed REAL, five_min_change REAL, sixty_day_change REAL, ytd_change REAL,
    main_net_inflow REAL, sector_name TEXT,
    popularity_rank INTEGER, rank_time TEXT, rank_source TEXT,
    PRIMARY KEY (trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_combined_sym ON stock_daily_combined(symbol, trade_date);

CREATE TABLE IF NOT EXISTS rebuild_progress (
    step       TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    rows       INTEGER,
    updated_at TEXT,
    PRIMARY KEY (step, symbol)
);
"""

SECTOR_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS sector_basic (
    sector_code     TEXT PRIMARY KEY,
    sector_name     TEXT,
    sector_type     TEXT,
    latest_index    REAL,
    change_pct      REAL,
    main_net_inflow REAL,
    main_net_pct    REAL,
    super_large_net REAL,
    super_large_pct REAL,
    large_net       REAL,
    large_pct       REAL,
    medium_net      REAL,
    medium_pct      REAL,
    small_net       REAL,
    small_pct       REAL,
    lead_stock_name TEXT,
    lead_stock_code TEXT,
    updated_date    TEXT
);

CREATE TABLE IF NOT EXISTS sector_kline (
    sector_code   TEXT,
    trade_date    TEXT,
    open          REAL,
    close         REAL,
    high          REAL,
    low           REAL,
    volume        REAL,
    turnover      REAL,
    amplitude     REAL,
    change_pct    REAL,
    change_amount REAL,
    turnover_rate REAL,
    source TEXT,
    fetched_at TEXT,
    PRIMARY KEY (sector_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_skline_date ON sector_kline(trade_date);

CREATE TABLE IF NOT EXISTS sector_member (
    sector_code    TEXT,
    stock_code     TEXT,
    stock_name     TEXT,
    latest_price   REAL,
    change_pct     REAL,
    change_amount  REAL,
    volume         REAL,
    turnover       REAL,
    amplitude      REAL,
    turnover_rate  REAL,
    volume_ratio   REAL,
    high           REAL,
    low            REAL,
    open_today     REAL,
    close_yesterday REAL,
    pb             REAL,
    pe_dynamic     REAL,
    updated_date   TEXT,
    PRIMARY KEY (sector_code, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_sm_sector ON sector_member(sector_code);
CREATE INDEX IF NOT EXISTS idx_sm_stock ON sector_member(stock_code);

CREATE TABLE IF NOT EXISTS sector_indicators (
    sector_code TEXT,
    trade_date  TEXT,
    MA5 REAL, MA10 REAL, MA20 REAL, MA30 REAL, MA60 REAL, MA100 REAL, MA200 REAL,
    RSI6 REAL, RSI14 REAL, RSI24 REAL,
    DIF REAL, DEA REAL, MACD REAL,
    BOLL_UPPER REAL, BOLL_MIDDLE REAL, BOLL_LOWER REAL,
    KDJ_K REAL, KDJ_D REAL, KDJ_J REAL,
    VOL_MA5 REAL, VOL_MA10 REAL,
    ATR14 REAL,
    PRIMARY KEY (sector_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_sind_date ON sector_indicators(trade_date);
"""


def init_stock_db():
    with _write_conn(get_stock_db()) as conn:
        conn.executescript(STOCK_DDL)


def init_sector_db():
    with _write_conn(get_sector_db()) as conn:
        conn.executescript(SECTOR_DDL)


def init_all():
    init_stock_db()
    init_sector_db()
