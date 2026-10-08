"""
股票分析 MCP Server — 精简入口
暴露25个工具，包含行情、结构研究、新闻和多源A股财务下载分析。
"""

import asyncio
import json
from datetime import datetime, timezone

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ListToolsResult, CallToolResult

from .tools.data_manager import (
    init_full_data,
    update_daily_data,
    get_data_status,
    get_kline_local_or_net,
    get_rank_trend_data,
    get_stock_belong_sectors,
    screen_stocks,
)
from .tools.analysis import generate_stock_report
from .tools.sector_data import get_sector_list
from .tools.stock_data import get_stock_kline_period
from .strategies.patterns import (
    scan_universe,
    get_pattern_history,
    get_key_levels,
)
from .data.sync import sync_stock_kline_universe
from .tools.research import prepare_stock_analysis, screen_rising_candidates
from .strategies.trading_backtest import backtest_pattern_strategy
from .strategies.similarity import find_cross_timeframe_similar_patterns

server = Server("stock-analysis-mcp")


def _sdk_decorator(method):
    """SDK 1 uses decorators; SDK 2 uses constructor callbacks."""
    decorator = getattr(server, method, None)
    return decorator() if decorator else lambda function: function

TOOL_HANDLERS = {}


def register(name, description, input_schema):
    def decorator(func):
        if name in TOOL_HANDLERS:
            raise ValueError(f"工具重复注册: {name}")
        TOOL_HANDLERS[name] = {"func": func, "description": description, "schema": input_schema}
        return func
    return decorator


# ═══════════════════ 数据管理 (3) ═══════════════════

@register("init_full_data", (
    "一次性下载数据到本地SQLite(首次必调)。quick=true为快速模式(秒级): 仅股票列表+实时行情+人气排名, "
    "板块成分股在首次使用时自动下载缓存; 显式mode=research同步320根全市场日K, "
    "mode=full同步长历史; 省略mode时保持旧quick参数语义"
), {
    "type": "object",
    "properties": {
        "include_sector_members": {"type": "boolean", "description": "是否下载板块成分股(完整模式下耗时但完整),默认true"},
        "quick": {"type": "boolean", "description": "快速初始化模式(秒级可用),默认false"},
        "mode": {"type": "string", "enum": ["quick", "research", "full"],
                 "description": "初始化模式; 省略时兼容quick参数"},
        "workers": {"type": "integer", "minimum": 1, "maximum": 16},
        "resume": {"type": "boolean", "description": "按覆盖记录断点续传,默认true"},
    },
    "required": [],
})
async def _init(include_sector_members=True, quick=False, mode=None, workers=8, resume=True) -> dict:
    return await init_full_data(include_sector_members, quick, mode, workers, resume)


@register("update_daily_data", "增量每日更新(建议收盘后运行)", {
    "type": "object",
    "properties": {
        "include_sector_members": {"type": "boolean", "description": "是否更新板块成分股,默认true"},
        "stock_kline_mode": {"type": "string", "enum": ["none", "tracked", "all"],
                             "description": "股票K线更新范围,默认tracked"},
        "skip_non_trading_day": {"type": "boolean", "description": "非交易日跳过,默认true"},
    },
    "required": [],
})
async def _update(include_sector_members=True, stock_kline_mode="tracked",
                  skip_non_trading_day=True) -> dict:
    return await update_daily_data(include_sector_members, stock_kline_mode,
                                   skip_non_trading_day)


@register("get_data_status", "查看本地数据库状态(数据量/更新时间/路径)", {
    "type": "object", "properties": {}, "required": [],
})
async def _status() -> dict:
    return await get_data_status()


# ═══════════════════ 选股 (1) ═══════════════════

@register("screen_stocks", (
    "万能本地选股: 18种条件自由组合(价格/涨跌幅/PE/PB/市值/量比/换手/振幅/60日涨幅/年初至今涨幅), "
    "支持板块限定、名称关键词、多字段排序。无本地数据时回退网络。"
    "示例: screen_stocks({\"min_change_pct\":3,\"max_pe\":30,\"min_volume_ratio\":1.5}) 找放量突破低估值股; "
    "screen_stocks(sector_code=\"BK1090\") 查看板块成分股; "
    "screen_stocks(name_keyword=\"银行\",sort_by=\"pe_dynamic\") 按名称搜银行股并PE排序"
), {
    "type": "object",
    "properties": {
        "conditions": {
            "type": "object",
            "properties": {k: {"type": "number"} for k in (
                "min_price", "max_price", "min_change_pct", "max_change_pct",
                "min_volume_ratio", "min_turnover_rate", "max_turnover_rate",
                "min_pe", "max_pe", "min_pb", "max_pb", "min_market_cap", "max_market_cap",
                "min_float_market_cap", "min_sixty_day_change", "min_ytd_change",
                "min_amplitude", "max_amplitude")},
            "description": (
                "全部可选: min_price,max_price, min_change_pct,max_change_pct, "
                "min_volume_ratio, min_turnover_rate,max_turnover_rate, "
                "min_pe,max_pe, min_pb,max_pb, min_market_cap,max_market_cap, "
                "min_float_market_cap, min_sixty_day_change, min_ytd_change, "
                "min_amplitude,max_amplitude"
            ),
        },
        "top_n": {"type": "integer", "minimum": 1, "maximum": 10000, "description": "返回数量,默认50"},
        "sort_by": {"type": "string", "description": (
            "change_pct/volume_ratio/turnover_rate/pe_dynamic/pb/"
            "total_market_cap/latest_price/volume/amplitude/popularity_rank"
        )},
        "sector_code": {"type": "string", "description": "可选,限定板块(如BK1090), 等效于查该板块成分股"},
        "name_keyword": {"type": "string", "description": "可选,名称关键词模糊搜索"},
    },
    "required": [],
})
async def _screen(conditions=None, top_n=50, sort_by="change_pct",
                  sector_code=None, name_keyword=None) -> list[dict]:
    for key, value in (conditions or {}).items():
        if key.startswith("min_"):
            maximum = (conditions or {}).get("max_" + key[4:])
            if maximum is not None and value > maximum:
                raise ValueError(f"{key} cannot exceed max_{key[4:]}")
    return await screen_stocks(conditions, top_n, sort_by, sector_code, name_keyword)


# ═══════════════════ 数据查询 (5) ═══════════════════

@register("get_kline_local_or_net", "个股K线(本地优先,不足自动从网络下载并缓存技术指标)", {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "股票代码"},
        "days": {"type": "integer", "minimum": 1, "description": "默认300个交易日"},
        "adjust": {"type": "string", "enum": ["qfq", "hfq", ""], "description": "复权方式: qfq前复权/hfq后复权/空字符串不复权, 默认qfq"},
    },
    "required": ["symbol"],
})
async def _kline(symbol, days=300, adjust="qfq") -> list[dict]:
    return await get_kline_local_or_net(symbol, days, adjust)


@register("get_stock_kline_period", (
    "个股多周期K线(纯网络实时): 1/5/15/30/60分钟线, 101日线, 102周线, 103月线。"
    "与 get_kline_local_or_net(仅日线+本地缓存)互补, 适合盘中看分时结构"
), {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "股票代码"},
        "period": {"type": "string", "enum": ["1", "5", "15", "30", "60", "101", "102", "103"],
                   "description": "周期: 1/5/15/30/60(分钟) 101(日) 102(周) 103(月), 默认60"},
        "limit": {"type": "integer", "minimum": 1, "description": "返回条数,默认240"},
        "adjust": {"type": "string", "enum": ["qfq", "hfq", ""], "description": "复权方式, 默认qfq"},
    },
    "required": ["symbol"],
})
async def _kline_period(symbol, period="60", limit=240, adjust="qfq") -> list[dict]:
    return await get_stock_kline_period(symbol, period, limit, adjust)


@register("get_rank_trend_data", "个股人气排名历史趋势(近N天排行变化)", {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "股票代码"},
        "days": {"type": "integer", "description": "默认30"},
    },
    "required": ["symbol"],
})
async def _rank_trend(symbol, days=30) -> list[dict]:
    return await get_rank_trend_data(symbol, days)


@register("get_sector_list", "行业/概念板块列表及行情(涨跌幅/资金流向),可按字段自行排序", {
    "type": "object",
    "properties": {
        "sector_type": {"type": "string", "enum": ["concept", "industry"], "description": "concept(概念)/industry(行业),默认concept"},
    },
    "required": [],
})
async def _sectors(sector_type="concept") -> list[dict]:
    return await get_sector_list(sector_type)


@register("get_stock_belong_sectors", "反查: 某只股票属于哪些板块(股票→板块)", {
    "type": "object",
    "properties": {"stock_code": {"type": "string", "description": "股票代码"}},
    "required": ["stock_code"],
})
async def _belong(stock_code) -> list[dict]:
    return await get_stock_belong_sectors(stock_code)


# ═══════════════════ 分析报告 (1) ═══════════════════

@register("generate_stock_report", "个股综合分析报告(趋势/支撑阻力/风险等级/仓位建议)", {
    "type": "object",
    "properties": {"symbol": {"type": "string", "description": "股票代码"}},
    "required": ["symbol"],
})
async def _report(symbol) -> dict:
    return await generate_stock_report(symbol)


# ═══════════════════ 形态识别 (4) ═══════════════════

_PATTERN_SCHEMA_COMMON = {
    "date": {"type": "string", "description": "扫描日期(YYYY-MM-DD), 默认本地库最新交易日"},
    "patterns": {
        "type": "array", "items": {"type": "string"},
        "description": "形态列表: trend_pullback/ma_rebound/w_bottom/m_neckline/box_breakout, 默认全部",
    },
    "min_score": {"type": "number", "minimum": 0, "maximum": 1, "description": "形态标准度阈值, 默认0.6"},
    "workers": {"type": "integer", "minimum": 1, "description": "并发线程数, 默认8"},
}


@register("scan_patterns", (
    "股票形态扫描: 全市场或指定股票, 识别五类上涨形态(趋势回调企稳/下跌均线反弹/W底/M形颈线/"
    "平台放量突破), 输出信号含评分/关键点位/共振/因子; strict=true 只留优中选优档信号"
), {
    "type": "object",
    "properties": {
        **_PATTERN_SCHEMA_COMMON,
        "strict": {"type": "boolean", "description": "只返回 strict 档(优中选优)信号, 默认false"},
        "no_filter": {"type": "boolean", "description": "不过滤(返回全部原始信号), 默认false"},
        "symbols": {
            "type": "array", "items": {"type": "string"},
            "description": "指定股票代码/名称列表(省略=全市场扫描)",
        },
    },
    "required": [],
})
async def _scan_patterns(date=None, patterns=None, min_score=0.6, workers=8,
                          strict=False, no_filter=False, symbols=None) -> list[dict]:
    return await asyncio.to_thread(
        scan_universe, "stocks", date=date, patterns=patterns,
        strict=strict, no_filter=no_filter, workers=workers, symbols=symbols,
        min_score=min_score)


@register("scan_sector_patterns", (
    "板块形态扫描: 概念/行业板块全量或指定板块, 识别与股票同一套五类形态(板块指数K线, "
    "历史不足260根K线的板块自动跳过), 输出信号含评分/关键点位/共振/因子"
), {
    "type": "object",
    "properties": {
        **_PATTERN_SCHEMA_COMMON,
        "sector_type": {"type": "string", "enum": ["concept", "industry"],
                        "description": "板块类型: concept(概念)/industry(行业), 默认全部"},
        "symbols": {
            "type": "array", "items": {"type": "string"},
            "description": "指定板块代码/名称列表(如 BK1090/1090/人工智能, 省略=全板块扫描)",
        },
    },
    "required": [],
})
async def _scan_sector_patterns(date=None, patterns=None, min_score=0.6, workers=8,
                                 sector_type=None, symbols=None) -> list[dict]:
    return await asyncio.to_thread(
        scan_universe, "sectors", date=date, patterns=patterns,
        workers=workers, symbols=symbols, sector_type=sector_type,
        min_score=min_score)


@register("get_pattern_history", "单标的(股票/板块)历史形态信号列表, 按日期升序", {
    "type": "object",
    "properties": {
        "universe": {"type": "string", "enum": ["stocks", "sectors"],
                      "description": "标的宇宙: stocks(股票)/sectors(板块), 默认stocks"},
        "symbol": {"type": "string", "description": "股票代码或板块代码(支持 BK1090/1090/名称)"},
        "start": {"type": "string", "description": "起始日期, 默认2010-01-01"},
        "end": {"type": "string", "description": "截止日期, 默认最新"},
        "patterns": {
            "type": "array", "items": {"type": "string"},
            "description": "形态列表, 默认全部",
        },
        "min_score": {"type": "number", "minimum": 0, "maximum": 1, "description": "形态标准度阈值, 默认0.6"},
    },
    "required": ["symbol"],
})
async def _pattern_history(universe="stocks", symbol=None, start=None, end=None,
                           patterns=None, min_score=0.6) -> list[dict]:
    return await asyncio.to_thread(
        get_pattern_history, universe, symbol, start=start, end=end,
        patterns=patterns, min_score=min_score)


@register("get_key_levels", "单标的(股票/板块)当前关键位: MA体系/斐波那契回调位/结构位(前高前低)+趋势判定", {
    "type": "object",
    "properties": {
        "universe": {"type": "string", "enum": ["stocks", "sectors"],
                      "description": "标的宇宙: stocks(股票)/sectors(板块), 默认stocks"},
        "symbol": {"type": "string", "description": "股票代码或板块代码(支持 BK1090/1090/名称)"},
    },
    "required": ["symbol"],
})
async def _key_levels(universe="stocks", symbol=None) -> dict:
    return await asyncio.to_thread(get_key_levels, universe, symbol)


# ═══════════════════ 图表渲染 (1) ═══════════════════

@register("render_stock_charts", (
    "个股K线看图: 生成 日K/周K/月K 三张蜡烛图 PNG(含自动趋势线/颈线/MA/成交量), "
    "返回图片路径供 Read 读图做结构归因。需要 chart extra (pip install 'stock-analysis-mcp[chart]')"
), {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "股票代码"},
        "days": {"type": "integer", "minimum": 30, "description": "日K根数, 默认500"},
    },
    "required": ["symbol"],
})
async def _render_charts(symbol, days=500) -> dict:
    from .charting import generate_analysis_charts
    return await asyncio.to_thread(generate_analysis_charts, symbol, days=days)


# ═══════════════════ 高级研究工作流 (5) ═══════════════════

@register("sync_stock_kline_universe", "批量同步全市场或指定股票的日K与指标,支持覆盖率统计和断点续传", {
    "type": "object", "properties": {
        "symbols": {"type": "array", "items": {"type": "string"}, "description": "省略=全市场"},
        "target_bars": {"type": "integer", "minimum": 260, "description": "目标交易日根数,默认320"},
        "adjust": {"type": "string", "enum": ["qfq", "hfq", ""]},
        "workers": {"type": "integer", "minimum": 1, "maximum": 16},
        "resume": {"type": "boolean"},
    }, "required": [],
})
async def _sync_universe(symbols=None, target_bars=320, adjust="qfq", workers=6, resume=True):
    return await sync_stock_kline_universe(symbols, target_bars, adjust, workers, resume)


@register("screen_rising_candidates", "六类上涨结构全市场初筛与综合评分;默认返回20只并披露数据覆盖率", {
    "type": "object", "properties": {
        "top_n": {"type": "integer", "minimum": 1, "maximum": 100},
        "lookback_days": {"type": "integer", "minimum": 1, "maximum": 60},
        "patterns": {"type": "array", "items": {"type": "string"}},
        "min_score": {"type": "number", "minimum": 0, "maximum": 1},
        "strict": {"type": "boolean"}, "workers": {"type": "integer", "minimum": 1, "maximum": 16},
        "symbols": {"type": "array", "items": {"type": "string"}},
    }, "required": [],
})
async def _rising_candidates(top_n=20, lookback_days=20, patterns=None, min_score=0.6,
                             strict=True, workers=8, symbols=None):
    return await asyncio.to_thread(screen_rising_candidates, top_n=top_n,
                                   lookback_days=lookback_days, patterns=patterns,
                                   min_score=min_score, strict=strict, workers=workers,
                                   symbols=symbols)


@register("prepare_stock_analysis", "组装逐股AI研判所需的月周日数值、形态、关键位、板块、人气和图表", {
    "type": "object", "properties": {
        "symbol": {"type": "string"},
        "days": {"type": "integer", "minimum": 260},
        "include_chart": {"type": "boolean"},
        "refresh_if_stale": {"type": "boolean"},
    }, "required": ["symbol"],
})
async def _prepare_analysis(symbol, days=500, include_chart=True, refresh_if_stale=True):
    return await prepare_stock_analysis(symbol, days=days, include_chart=include_chart,
                                        refresh_if_stale=refresh_if_stale)


@register("find_cross_timeframe_similar_patterns", (
    "用目标股票最近N根K线，与全市场所有股票的历史N根窗口比较。默认最近30根日K对全市场历史日K；"
    "先向量化粗筛价格路径，再用价格、量、回撤、振幅和DTW完整评分并统计后续表现"
), {
    "type": "object", "properties": {
        "symbol": {"type": "string", "description": "股票代码"},
        "query_period": {"type": "string", "enum": ["1", "5", "15", "30", "60", "101", "102", "103"],
                         "description": "当前形态周期,默认101(日K)"},
        "candidate_periods": {"type": "array", "items": {"type": "string", "enum": ["1", "5", "15", "30", "60", "101", "102", "103"]},
                              "description": "搜索周期,默认日/周/月"},
        "query_bars": {"type": "integer", "minimum": 12, "maximum": 240, "description": "统一比较K线根数,默认30；日K时即最近30个交易日"},
        "candidate_window_bars": {"type": "array", "items": {"type": "integer", "minimum": 12, "maximum": 240},
                                  "description": "高级时间伸缩窗口；默认省略并严格等于query_bars"},
        "search_bars": {"type": "integer", "minimum": 0, "maximum": 5000,
                        "description": "每只股票历史K线范围；0表示本地全部历史,默认0"},
        "top_n": {"type": "integer", "minimum": 1, "maximum": 30, "description": "返回数量,默认10"},
        "min_score": {"type": "number", "minimum": 0, "maximum": 1, "description": "最低相似度,默认0.55"},
        "adjust": {"type": "string", "enum": ["qfq", "hfq", ""], "description": "复权方式,默认qfq"},
        "candidate_scope": {"type": "string", "enum": ["self", "symbols", "market"],
                            "description": "候选范围: 单股历史/指定股票池/本地市场,默认market"},
        "candidate_symbols": {"type": "array", "items": {"type": "string"},
                              "description": "candidate_scope=symbols时的候选代码"},
        "search_mode": {"type": "string", "enum": ["history", "latest"],
                        "description": "history查所有历史窗口并统计后验,默认history; latest仅比较当前形态"},
        "max_symbols": {"type": "integer", "minimum": 0, "maximum": 10000,
                        "description": "股票池上限,0表示全部本地股票,默认0"},
        "workers": {"type": "integer", "minimum": 1, "maximum": 16,
                    "description": "本地数据读取并发,默认8"},
        "probability_sample_size": {"type": "integer", "minimum": 10, "maximum": 500,
                                    "description": "后续走势概率统计使用的相似样本数,默认100"},
        "move_threshold_pct": {"type": "number", "minimum": 0, "maximum": 20,
                               "description": "上涨/下跌分类阈值,默认正负2%"},
    }, "required": ["symbol"],
})
async def _similar_patterns(symbol, query_period="101", candidate_periods=None,
                            query_bars=30, candidate_window_bars=None,
                            search_bars=0, top_n=10, min_score=0.55, adjust="qfq",
                            candidate_scope="market", candidate_symbols=None,
                            search_mode="history", max_symbols=0, workers=8,
                            probability_sample_size=100, move_threshold_pct=2.0):
    return await find_cross_timeframe_similar_patterns(
        symbol, query_period, candidate_periods, query_bars,
        candidate_window_bars, search_bars, top_n, min_score, adjust,
        candidate_scope, candidate_symbols, search_mode, max_symbols, workers,
        probability_sample_size, move_threshold_pct)


@register("backtest_pattern_strategy", "形态信号5/10/20日事件研究 + 次日开盘/止损/2R/20日退出交易回测", {
    "type": "object", "properties": {
        "start": {"type": "string"}, "end": {"type": "string"},
        "patterns": {"type": "array", "items": {"type": "string"}},
        "sample": {"type": "integer", "minimum": 1},
        "workers": {"type": "integer", "minimum": 1, "maximum": 16},
        "mode": {"type": "string", "enum": ["event", "trading", "both"]},
        "split": {"type": "string"},
        "buy_cost_bps": {"type": "number", "minimum": 0},
        "sell_cost_bps": {"type": "number", "minimum": 0},
        "benchmark_symbol": {"type": "string"},
        "slippage_bps": {"type": "number", "minimum": 0},
        "initial_capital": {"type": "number", "minimum": 1},
        "max_volume_fraction": {"type": "number", "minimum": 0.000001, "maximum": 1},
    }, "required": [],
})
async def _backtest_strategy(start="2010-01-01", end=None, patterns=None, sample=None,
                             workers=8, mode="both", split="2022-01-01",
                             buy_cost_bps=8.0, sell_cost_bps=13.0, benchmark_symbol=None,
                             slippage_bps=5.0, initial_capital=1_000_000, max_volume_fraction=0.01):
    return await asyncio.to_thread(backtest_pattern_strategy, start=start, end=end,
                                   patterns=patterns, sample=sample, workers=workers,
                                   mode=mode, split=split, buy_cost_bps=buy_cost_bps,
                                   sell_cost_bps=sell_cost_bps, benchmark_symbol=benchmark_symbol,
                                   slippage_bps=slippage_bps, initial_capital=initial_capital,
                                   max_volume_fraction=max_volume_fraction)


# ═══════════════════ MCP 生命周期 ═══════════════════

_NEWS_PROPERTIES = {
    "query": {"type": "string"},
    "days": {"type": "integer", "minimum": 1, "maximum": 365},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    "offline": {"type": "boolean", "description": "只读资讯缓存，不发起网络请求"},
}


@register("search_market_news", "有限来源新闻搜索：官方Fed/ECB RSS和东方财富关键词，披露发布时间、链接、缓存与覆盖范围", {
    "type": "object", "properties": _NEWS_PROPERTIES, "required": [],
})
async def _news(query="", days=7, limit=30, offline=False):
    from .data.information import search_news
    return await asyncio.to_thread(search_news, query, days, limit, offline)


@register("get_stock_related_news", "股票与用户指定概念的消息时间线；保留关联检索依据，不推断因果影响", {
    "type": "object", "properties": {
        **{k: v for k, v in _NEWS_PROPERTIES.items() if k != "query"},
        "symbol": {"type": "string"}, "concepts": {"type": "array", "items": {"type": "string"}},
    }, "required": ["symbol"],
})
async def _related_news(symbol, concepts=None, days=30, limit=30, offline=False):
    from .data.information import stock_news
    return await asyncio.to_thread(stock_news, symbol, concepts, days, limit, offline)


@register("get_global_market_news", "全球市场新闻与官方宏观政策背景，保留来源范围；不提供实时行情报价", {
    "type": "object", "properties": _NEWS_PROPERTIES, "required": [],
})
async def _global_news(query="全球市场", days=7, limit=30, offline=False):
    from .data.information import global_market_news
    return await asyncio.to_thread(global_market_news, query, days, limit, offline)


@register("analyze_stock_financials", "A股多期财务指标与指定同业同报告期比较：盈利、成长、现金流、偿债能力及来源证据", {
    "type": "object", "properties": {
        "symbol": {"type": "string"}, "periods": {"type": "integer", "minimum": 1, "maximum": 20},
        "peers": {"type": "array", "items": {"type": "string"}}, "offline": {"type": "boolean"},
        "provider": {"type": "string", "enum": ["auto", "eastmoney", "sohu", "tencent"]},
        "compare_sources": {"type": "boolean", "description": "并列下载已支持指标的来源，披露同报告期差异"},
    }, "required": ["symbol"],
})
async def _financials(symbol, periods=8, peers=None, offline=False, provider="auto", compare_sources=False):
    from .data.information import financial_analysis
    return await asyncio.to_thread(financial_analysis, symbol, periods, peers, offline, provider, compare_sources)


@register("download_stock_financials", "沪深北A股多源财务下载与基础分析：搜狐指标和三张报表、腾讯摘要和主营构成、新浪报表、东方财富指标；分别缓存不混用口径", {
    "type": "object", "properties": {
        "symbol": {"type": "string"},
        "provider": {"type": "string", "enum": ["auto", "all", "eastmoney", "sohu", "tencent", "sina"]},
        "report_type": {"type": "string", "enum": ["all", "indicators", "balance", "income", "cashflow", "revenue_segments"]},
        "periods": {"type": "integer", "minimum": 1, "maximum": 20},
        "offline": {"type": "boolean"}, "refresh": {"type": "boolean", "description": "联网时跳过新鲜缓存；离线时忽略"},
    }, "required": ["symbol"],
})
async def _download_financials(symbol, provider="auto", report_type="indicators", periods=8, offline=False, refresh=False):
    from .data.financials import download_financials
    return await asyncio.to_thread(download_financials, symbol, provider, report_type, periods, offline, refresh)


@_sdk_decorator("list_tools")
async def list_tools() -> list[Tool]:
    return [
        Tool(name=n, description=i["description"], inputSchema=i["schema"])
        for n, i in TOOL_HANDLERS.items()
    ]


def _validate_arguments(name: str, arguments, schema: dict) -> list[str]:
    """轻量 JSON-Schema 校验, 返回错误列表(空列表=通过)。"""
    errors = []
    if arguments is not None and not isinstance(arguments, dict):
        return ["参数必须为 JSON 对象"]
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    args = arguments if isinstance(arguments, dict) else {}
    for req in schema.get("required", []) or []:
        if req not in args:
            errors.append(f"缺少必填参数: {req}")
    for key in args:
        if key not in props:
            errors.append(f"未知参数: {key}")
            continue
        rule = props.get(key, {})
        value = args[key]
        expected = rule.get("type")
        valid_type = {
            "string": lambda x: isinstance(x, str),
            "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
            "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
            "boolean": lambda x: isinstance(x, bool),
            "array": lambda x: isinstance(x, list),
            "object": lambda x: isinstance(x, dict),
        }.get(expected)
        if valid_type and not valid_type(value):
            errors.append(f"参数 {key} 类型错误: 需要 {expected}")
            continue
        if "enum" in rule and value not in rule["enum"]:
            errors.append(f"参数 {key} 值无效: 允许值为 {rule['enum']}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                errors.append(f"参数 {key} 不能小于 {rule['minimum']}")
            if "maximum" in rule and value > rule["maximum"]:
                errors.append(f"参数 {key} 不能大于 {rule['maximum']}")
        if isinstance(value, list) and "items" in rule:
            for index, item in enumerate(value):
                errors.extend(_validate_arguments(name, {f"{key}[{index}]": item}, {
                    "properties": {f"{key}[{index}]": rule["items"]}}))
        if isinstance(value, dict) and "properties" in rule:
            errors.extend(_validate_arguments(name, value, rule))
        if isinstance(value, float):
            import math
            if not math.isfinite(value):
                errors.append(f"参数 {key} 必须是有限数值")
    return errors


@_sdk_decorator("call_tool")
async def call_tool(name, arguments) -> list[TextContent]:
    info = TOOL_HANDLERS.get(name)
    now = datetime.now(timezone.utc).isoformat()
    if not info:
        return [TextContent(type="text", text=json.dumps({"data": None,
            "meta": {"source": "stock-analysis", "fetched_at": now}, "warnings": [],
            "error": {"code": "UNKNOWN_TOOL", "message": f"未知工具: {name}"}}, ensure_ascii=False))]
    errors = _validate_arguments(name, arguments, info["schema"])
    if errors:
        envelope = {"data": None, "meta": {"source": "stock-analysis", "fetched_at": now},
                    "warnings": [], "error": {"code": "INVALID_PARAMS", "message": "; ".join(errors)}}
        return [TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))]
    try:
        args = arguments if isinstance(arguments, dict) else {}
        result = await info["func"](**args)
        warnings = result.get("warnings", []) if isinstance(result, dict) else []
        if not isinstance(warnings, list):
            warnings = [str(warnings)]
        envelope = {
            "data": result,
            "meta": {
                "source": "stock-analysis",
                "fetched_at": now,
            },
            "warnings": warnings,
            "error": None,
        }
        if isinstance(result, dict) and result.get("status") in {"error", "failed"}:
            envelope["error"] = {"code": "DATA_ERROR", "message": str(result.get("error") or result.get("status"))}
        return [TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False, default=str))]
    except Exception as e:
        envelope = {
            "data": None,
            "meta": {"source": "stock-analysis", "fetched_at": now},
            "warnings": [],
            "error": {"code": "TOOL_ERROR", "message": str(e)},
        }
        return [TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))]


if not hasattr(server, "list_tools"):
    async def _sdk_list_tools(context, params):
        return ListToolsResult(tools=await list_tools())

    async def _sdk_call_tool(context, params):
        content = await call_tool(params.name, params.arguments)
        failed = bool(json.loads(content[0].text).get("error"))
        return CallToolResult(content=content, isError=failed)

    server = Server("stock-analysis-mcp", on_list_tools=_sdk_list_tools,
                    on_call_tool=_sdk_call_tool)


def main():
    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    asyncio.run(run())


if __name__ == "__main__":
    main()
