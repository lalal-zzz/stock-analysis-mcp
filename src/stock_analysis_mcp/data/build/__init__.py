"""
data/build 包 — 数据构建 / 维护 (原 build.py 拆分, 本包为门面 re-export)

模块布局:
    helpers.py   共享小工具 (日期/计数/标的清单/翻页下载/板块取数) + 网络入口门面
    rebuild.py   全量重建步骤与编排 (rebuild_full_data, 断点续传 + dry-run)
    backfill.py  缺口检测与补齐 (backfill_data)
    capture.py   晚间采集 (daily_capture)
    cleanup.py   数据库清理 (cleanup_database)

兼容性: 对外符号与原 build.py 完全一致; 测试可继续 monkeypatch
`build.fetch_* / build.wait_for_internet` (内部经包门面动态解析)。
"""

from ..sources import (
    fetch_full_spot,
    fetch_guba_rank_history,
    fetch_kline_history,
    fetch_xuangu_rankings,
    wait_for_internet,
)
from .backfill import (
    backfill_data,
    backfill_history,
    backfill_rank,
    get_missing_trade_dates,
    rebuild_combined,
)
from .capture import (
    daily_capture,
    save_rank_snapshot,
    save_recent_klines,
    save_spot_snapshot,
)
from .cleanup import cleanup_database
from .rebuild import (
    compute_all_sector_indicators,
    compute_all_stock_indicators,
    download_sector_full_history,
    rebuild_full_data,
    step_combined,
    step_guba_rank,
    step_kline,
    step_spot,
    update_stock_indicators_since,
)

__all__ = [
    # 编排入口
    "rebuild_full_data", "backfill_data", "daily_capture", "cleanup_database",
    # 重建步骤
    "step_spot", "step_kline", "step_guba_rank", "step_combined",
    "compute_all_stock_indicators", "update_stock_indicators_since",
    "download_sector_full_history", "compute_all_sector_indicators",
    # 回填
    "get_missing_trade_dates", "backfill_history", "backfill_rank", "rebuild_combined",
    # 采集
    "save_rank_snapshot", "save_spot_snapshot", "save_recent_klines",
    # 网络入口 (测试 monkeypatch 重定向点, 内部经包门面动态解析)
    "fetch_full_spot", "fetch_guba_rank_history", "fetch_kline_history",
    "fetch_xuangu_rankings", "wait_for_internet",
]
