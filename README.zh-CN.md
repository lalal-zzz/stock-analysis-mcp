[English](README.md) | [中文](README.zh-CN.md)

# 股票分析 MCP + Skills

[![npm version](https://img.shields.io/npm/v/stock-analysis-mcp.svg)](https://www.npmjs.com/package/stock-analysis-mcp)
[![Python](https://img.shields.io/pypi/pyversions/stock-analysis-mcp.svg)](https://pypi.org/project/stock-analysis-mcp/)
[![License](https://img.shields.io/github/license/lalal-zzz/stock-analysis-mcp)](LICENSE)

**股票分析 MCP + Skills** 是一个面向 AI Agent 的本地优先 A 股分析工作台。它将多个公开行情源整合为可复用的本地 SQLite 证据库，覆盖行情、指标、板块资金、K 线结构、多周期研判、研究报告和可复现回测。

它围绕一条完整投研链路工作：**检查数据质量 → 构建候选池 → 查看数值与图表证据 → 比较情景 → 用样本外回测验证规则**。项目不会给出收益保证，也不会自动发出交易指令。

数据、上涨形态、20只逐股分析和双层回测的统一约定见 [实施计划](IMPLEMENTATION_PLAN.md)。

## Agent 一键配置（npm）

```bash
npm install -g stock-analysis-mcp
stock-analysis install --agents auto   # 自动检测并配置 Agent + 安装 Skill
stock-analysis setup --data-root "D:/MarketData"   # 指定 SQLite 数据目录
stock-analysis doctor                  # 检查运行时 / 配置 / Agent 状态
```

**支持的 Agent**（`install --agents auto` 自动检测并配置）：

| Agent | 写入的配置 | Skill 安装 |
|-------|-----------|-----------|
| Claude Code | `~/.claude.json` | ✅ `~/.claude/skills/` |
| Codex | `~/.codex/config.toml` | ✅ `~/.codex/skills/` |
| Cursor | `~/.cursor/mcp.json` | —（工具描述自解释）|
| VS Code Copilot | VS Code 用户级 `mcp.json` | — |
| Qoder | `~/.qoder/mcp.json` | ✅ `~/.qoder/skills/` |

安装器使用 [`uv`](https://docs.astral.sh/uv/) 管理隔离的 Python 环境（需先安装 `uv`）。修改任何 Agent 配置前都会询问并自动备份，可通过 `stock-analysis uninstall` 还原。OpenCode 等其他 MCP 客户端的手动配置模板可通过 `stock-analysis install --agents auto --dry-run` 打印。

配置解析顺序为 **环境变量 → `~/.stock-analysis/config.toml` → 默认值**。推荐使用 `STOCK_ANALYSIS_DATA_DIR`、`STOCK_ANALYSIS_STOCK_DATA_DIR`、`STOCK_ANALYSIS_SECTOR_DATA_DIR` 和 `STOCK_ANALYSIS_PYTHON`；旧环境变量仍作为兼容别名生效。

## 核心能力

| 能力 | 说明 |
|------|------|
| 全市场行情 | 上市 A 股全量列表及价格 / 涨跌幅 / PE / PB / 市值 / 量比 / 换手率 |
| 历史 K 线 | 日线数据（开高低收量额），支持前复权/后复权/不复权 |
| 技术指标 | MA5/10/20/30/60/100/120/200/250、RSI、MACD、BOLL、KDJ、ATR%、量能倍率、收益率、区间高低点和BIAS |
| 人气排名 | 东方财富人气榜单 + 历史排名趋势追踪（含股吧年历史） |
| 板块分析 | 概念与行业板块行情、资金流、成分股、K线和指标 |
| 多条件选股 | 18 种条件自由组合：价格区间 / PE / PB / 市值 / 涨跌幅 / 量比 / 换手 / 振幅 / 板块限定 |
| 形态研究 | 5类底层检测器、高层颈线收复/大均线反弹标准名，以及斐波那契共振证据 |
| 深度研判 | 固定筛选20只候选，对每只读取月/周/日数值与图表证据 |
| 策略回测 | 5/10/20日事件研究、次日开盘交易模拟、成本和时间外检验 |

---

## 快速安装

```bash
# Claude Code 一键安装（推荐）
claude mcp add stock-analysis -- npx stock-analysis-mcp

# 或 npm 全局安装
npm install -g stock-analysis-mcp

# 或 pip 安装
pip install stock-analysis-mcp
```

**前置要求**: Python >= 3.10 | Node.js >= 18

---

## 数据详情

### 股票行情数据

| 数据项 | 字段 |
|--------|------|
| 基础行情 | 最新价、今开、昨收、最高、最低 |
| 涨跌 | 涨跌幅、涨跌额、振幅、涨速 |
| 成交 | 成交量、成交额 |
| 活跃度 | 量比、换手率 |
| 估值 | 动态市盈率(PE)、市净率(PB)、TTM 市盈率 |
| 市值 | 总市值、流通市值 |
| 趋势 | 60 日涨跌幅、年初至今涨跌幅 |
| 资金 | 主力资金净流入 |

### 股票 K 线数据

| 数据项 | 说明 |
|--------|------|
| 日线 K 线 | 开盘价、最高价、最低价、收盘价、成交量、成交额 |
| 复权方式 | 前复权(qfq) / 后复权(hfq) / 不复权 |
| 附加字段 | 涨跌幅、换手率、振幅 |
| 唯一标识 | `symbol + date + adjust_type`，同时记录来源与抓取时间 |
| 旧库兼容 | 原地迁移并复用已有前复权数据，不复制或要求重新下载大库 |

### 技术指标 (自动计算)

| 类别 | 指标 |
|------|------|
| 均线 | MA5、MA10、MA20、MA30、MA60、MA100、MA120、MA200、MA250 |
| 相对强弱 | RSI6、RSI14、RSI24 |
| MACD | DIF、DEA、MACD 柱 |
| 布林带 | 上轨、中轨、下轨 (BOLL) |
| KDJ | K、D、J 值 |
| 波动率 | ATR14、ATR百分比 |
| 量能 | VOL_MA5/10/20、5/20日量能倍率 |
| 收益与区间 | 5/10/20/60日收益、20/60/120日高低点 |
| 乖离率 | BIAS20、BIAS60、BIAS250 |

### 人气排名数据

| 数据项 | 说明 |
|--------|------|
| 人气排名 | 东方财富实时人气榜单 (排名/价格/涨跌幅/量比/换手率) |
| 历史趋势 | 支持查询任意股票 N 天内的人气排名变化走势（滚动一年收盘排名） |

### 板块数据

| 数据项 | 说明 |
|--------|------|
| 板块列表 | 概念板块(~400个) + 行业板块(~80个) |
| 板块行情 | 板块指数、涨跌幅 |
| 资金流向 | 主力净流入、超大单净流入、大单净流入、中单净流入、小单净流入 (含占比%) |
| 领涨股 | 板块龙头股票 |
| 板块 K 线 | 板块历史指数走势（东财单源） |
| 板块成分股 | 每只成分股的行情/换手率/量比/PE/PB |

### 分析报告

| 模块 | 内容 |
|------|------|
| 基础信息 | 价格、涨跌幅、PE、PB、市值、量比、换手率 |
| 趋势分析 | 均线排列状态、5/10/20/60 日涨跌幅、方向判断 |
| 技术指标 | RSI 状态、MACD 金叉死叉、KDJ 超买超卖、BOLL 位置、ATR 波幅 |
| 支撑阻力 | 均线支撑/阻力位 + BOLL 上下轨，按强度排序 |
| 风险评估 | 超买超卖风险、波动率风险、估值风险、流动性风险、趋势破位风险 |
| 条件情景 | 确认条件、失效位、支撑压力与风险收益假设 |

---

## MCP 工具 (25 个)

| 工具 | 功能 |
|------|------|
| `init_full_data` | 支持 quick、research（全市场320根日K）和长历史full模式；旧quick参数保持兼容 |
| `update_daily_data` | 每日增量刷新，股票K线范围支持 `none|tracked|all` |
| `get_data_status` | 查看本地数据库状态（数据量/更新时间/存储路径） |
| `screen_stocks` | 万能多条件选股：18 种条件自由组合(价格/PE/PB/市值/涨跌幅/量比/换手/振幅)，支持板块限定、名称搜索、多字段排序 |
| `get_kline_local_or_net` | 获取个股历史 K 线（本地优先，不足自动下载并缓存技术指标） |
| `get_stock_kline_period` | 个股多周期 K 线：1/5/15/30/60 分钟线 + 日/周/月线（纯网络实时） |
| `get_rank_trend_data` | 查询个股 N 天内的人气排名历史走势 |
| `get_sector_list` | 获取概念/行业板块列表及其行情数据 |
| `get_stock_belong_sectors` | 反向查询：某只股票属于哪些板块 |
| `generate_stock_report` | 生成个股综合分析报告（趋势/支撑阻力/风险等级/仓位建议） |
| `scan_patterns` | 股票形态扫描：全市场或指定股票、指定日期，普通档 / strict 优中选优档 |
| `scan_sector_patterns` | 板块形态扫描（concept / industry 或指定板块） |
| `get_pattern_history` | 单标的（股票/板块）历史形态信号列表 |
| `get_key_levels` | 单标的关键位：MA 体系 / 斐波那契回调位 / 结构位（颈线/前高/箱体） |
| `render_stock_charts` | 生成日K、周K、月K分析图 |
| `sync_stock_kline_universe` | 批量同步股票K线与指标并报告覆盖率 |
| `screen_rising_candidates` | 六类上涨结构评分并返回前20只候选 |
| `prepare_stock_analysis` | 为逐股AI研判准备月周日数值和图表 |
| `find_cross_timeframe_similar_patterns` | 目标取最近N根K线，与全市场全部历史N根窗口比较；返回相似片段及后续上涨/震荡/下跌概率 |
| `backtest_pattern_strategy` | 事件研究与5～20日交易回测 |
| `search_market_news` | 带来源时间、链接与缓存状态的有限新闻检索 |
| `get_stock_related_news` | 公司与概念消息的关联依据和时间线 |
| `get_global_market_news` | 官方政策发布与有限全球市场新闻 |
| `analyze_stock_financials` | 多期财务指标及指定同业同报告期比较 |
| `download_stock_financials` | A股多源财务下载：搜狐/新浪报表、腾讯摘要与主营构成、东方财富指标；独立缓存与分析 |

### MCP 调用约定

每个工具都公开 JSON Schema；服务端会校验必填字段、未知字段、基础类型、枚举值和数值范围。调用结果统一为 `{data, meta, warnings, error}`：先检查 `error`，再保留 `warnings`，并以 `meta` 和数据自身的截止日期判断新鲜度。工具不会承诺持续监控、推送通知或确定性买卖结论。


附带 **12 个 Agent Skill**（由 `stock-analysis install` 自动安装），教授 AI 如何组合使用这些工具完成复杂选股和报告工作流：

| Skill | 用途 |
|-------|------|
| `stock-analysis` | 主索引 — 先查数据状态再开展研究的工作流 |
| `stock-analysis-data-init` | 复用已有数据、初始化、覆盖率更新与故障排查 |
| `stock-analysis-stock-screening` | 选股条件组合与筛选套路 |
| `stock-analysis-report-generation` | 基于证据的单股报告与条件情景 |
| `stock-analysis-multi-timeframe` | 月周日与分时联立、周期冲突解释 |
| `stock-analysis-strategy-backtest` | 策略回测与参数调优指南 |
| `stock-analysis-chart-trend` | K线图结构归因与趋势线分析 |
| `stock-analysis-rising-patterns` | 20只上涨形态候选逐股月周日深度分析 |

### 每个 Skill 的结果展示

这些 Skill 都以“可复核结果”为目标：不是只返回一段结论，而是把 MCP 数据整理成固定的结果卡片、表格或回测结果包。

| Skill | 结果视图 | 典型展示内容 |
|-------|---------|-------------|
| `stock-analysis` | 投研路由卡 | 选择的工作流、数据质量门槛、工具链、警告和下一步动作 |
| `stock-analysis-data-init` | 数据健康报告 | 预期交易日、各数据集最新日期与覆盖率、成功/失败项、数据源警告、剩余缺口 |
| `stock-analysis-stock-screening` | 可排序候选表 | 代码/名称、截止日期、筛选条件、形态阶段、评分构成、板块背景、支撑/阻力/失效位、淘汰与缺失数据说明 |
| `stock-analysis-report-generation` | 单股证据研报 | 结论与置信度、数据质量、月/周/日证据、关键位表、牛/基准/熊情景、风险与局限 |
| `stock-analysis-multi-timeframe` | 多周期对照矩阵 | 月/周/日/分时指标、周期一致/冲突矩阵、主次解释、精确确认位与失效位 |
| `stock-analysis-chart-trend` | K 线看图卡 | 截止日期、枢轴点、趋势线/通道/箱体/W-M/斐波那契区域、程序与图表是否一致、量能证据 |
| `stock-analysis-rising-patterns` | 候选卡片 + 对比表 | 每只返回股票一张证据卡、复核后排序、当前候选与历史样本共性、覆盖率限制 |
| `stock-analysis-strategy-backtest` | 回测结果包 | 股票池/区间、覆盖率、信号与交易数、假设、事件和组合指标、训练/测试与年度稳定性、报告/CSV 路径 |

示例结果卡片：

```text
000001 平安银行 · 2026-09-17 · 前复权 · 日线486根
结构：周线回调 / 日线颈线收复（candidate → triggered）
证据：收盘价 12.34｜MA20 12.10｜颈线 12.28｜量能 1.42 倍
确认：日线收盘站上 12.28 并保持；失效：收盘跌破 11.86
警告：板块数据有延迟；图表确认可用
```

具体数值、日期、覆盖率和警告始终以当前 MCP 返回为准；上例只用于说明结果的展示形式。

---

## 完整投研流程

```text
get_data_status
  → 只更新或回填缺失数据
  → screen_rising_candidates(top_n=20)
  → 对每只候选调用 prepare_stock_analysis
  → 逐只读取月K、周K、日K图
  → 汇总当前样本、历史成功和失败样本共性
  → backtest_pattern_strategy(mode="both")
  → 人工确认后才修改正式规则
```

只有合格股票K线覆盖率达到95%时才能称为全市场结果。底层5类检测器是 `trend_pullback`、`w_bottom`、`m_neckline`、`box_breakout` 和 `ma_rebound`；高层报告将后两类名称统一为 `neckline_reclaim` 与 `major_ma_rebound`。第六类 `fibonacci_confluence` 是增强证据，不是独立反转形态。

## 新闻与财务证据

已接入四项工具与 Skills，来源为美联储/欧洲央行官方 RSS 及东方财富新闻/财务接口。使用 `offline=true` 可保持暂停下载；完整来源、示例和边界见[资讯来源文档](docs/information-sources.md)。财务下载增加搜狐/新浪三张报表与腾讯摘要/主营构成，分别披露可用期数。当前不提供完整互联网搜索、实时全球报价或产业链知识图谱。

## 统一 CLI 工具

除了 MCP 服务外，本项目提供统一的命令行工具用于批量数据构建、回填与形态策略回测：

```bash
# 数据全量重建预览与执行
python -m stock_analysis_mcp.cli rebuild --dry-run
python -m stock_analysis_mcp.cli rebuild --workers 8 --with-sectors

# 历史数据缺口自动检测与回填
python -m stock_analysis_mcp.cli backfill --start 2026-01-01

# 收盘后晚间自动采集
python -m stock_analysis_mcp.cli daily-capture

# 数据库冗余清理与 VACUUM 压缩
python -m stock_analysis_mcp.cli cleanup

# 形态扫描与策略回测
python -m stock_analysis_mcp.cli pattern-scan --universe sectors --date 2026-08-14
python -m stock_analysis_mcp.cli pattern-backtest --universe stocks --sample 300
python -m stock_analysis_mcp.cli pattern-optimize --cache signals.csv
```

---

## 本地数据库

数据存储在本地 SQLite 数据库中，采用 WAL 模式实现高并发读写，首次初始化后查询速度极快，无需联网：

| 数据库 | 默认路径（Windows）| 默认路径（Linux/macOS）| 内容 |
|--------|-------------------|----------------------|------|
| 股票数据库 | `~/Desktop/股票信息/stock_data.db` | `~/.stock-analysis/data/stocks/stock_data.db` | 行情、分复权K线、人气、指标、综合表、覆盖率与形态信号 |
| 板块数据库 | `~/Desktop/分析板块/sector_data.db` | `~/.stock-analysis/data/sectors/sector_data.db` | 板块行情、资金流、东财单源K线、成分股和指标 |

可通过环境变量 `STOCK_ANALYSIS_STOCK_DATA_DIR` 和 `STOCK_ANALYSIS_SECTOR_DATA_DIR` 自定义路径。

---

## 环境变量

解析顺序：**环境变量 → `~/.stock-analysis/config.toml` → 默认值**。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STOCK_ANALYSIS_PYTHON` | 托管运行时 → `python` | Node 入口使用的 Python 解释器路径 |
| `STOCK_ANALYSIS_DATA_DIR` | Win: `~/Desktop`；Linux/macOS: `~/.stock-analysis/data` | 两个数据库的根目录 |
| `STOCK_ANALYSIS_STOCK_DATA_DIR` | `<data_root>/股票信息`（Linux/macOS 默认为 `stocks`）| 股票数据库目录 |
| `STOCK_ANALYSIS_SECTOR_DATA_DIR` | `<data_root>/分析板块`（Linux/macOS 默认为 `sectors`）| 板块数据库目录 |
| `STOCK_ANALYSIS_CONFIG` | `~/.stock-analysis/config.toml` | 覆盖 config.toml 路径 |
| `EASTMONEY_COOKIE` | 自动从 Edge 提取 | 东方财富 API Cookie（提升请求成功率） |

---

## 客户端配置

### Claude Desktop / Claude Code

```bash
claude mcp add stock-analysis -- npx stock-analysis-mcp
```

或手动编辑 `claude_desktop_config.json`：

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

运行 `stock-analysis install --agents codex`，或在 `~/.codex/config.toml` 中添加：

```toml
[mcp_servers.stock-analysis]
command = "npx"
args = ["stock-analysis-mcp"]
```

### Cursor

运行 `stock-analysis install --agents cursor`，或将同样的 JSON 块加入 `~/.cursor/mcp.json`。

### VS Code Copilot

运行 `stock-analysis install --agents copilot`，或编辑 VS Code 用户级 `mcp.json`（Windows：`%APPDATA%\Code\User\mcp.json`，Linux：`~/.config/Code/User/mcp.json`）：

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

运行 `stock-analysis install --agents qoder`，或在 `~/.qoder/mcp.json` 中添加标准 `mcpServers` 配置块。

### OpenCode / 其他 MCP 客户端

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

### 直接 Python 运行

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

## 使用示例

```python
# 已有数据库直接复用；只有缺数据时才初始化
init_full_data(mode="quick")

# 全市场上涨形态扫描前推荐
init_full_data(mode="research", workers=6, resume=True)

# 或长历史完整初始化（用于回测，可断点续传）
init_full_data(mode="full", workers=6, resume=True)

# 每日收盘后增量更新；新鲜度按最近交易日判断
update_daily_data(stock_kline_mode="tracked")

# 3. 选股：找涨幅>3%、PE<30、量比>1.5 的放量突破股
screen_stocks({"min_change_pct":3, "max_pe":30, "min_volume_ratio":1.5})

# 4. 选股：芯片板块内 PE<50 的股票，按人气排名排序
screen_stocks({"max_pe":50}, sector_code="BK1090", sort_by="popularity_rank")

# 5. 选股：搜索名称含"银行"的股票
screen_stocks(name_keyword="银行")

# 6. 获取平安银行 K 线并缓存技术指标
get_kline_local_or_net("000001", days=250)

# 7. 查看平安银行近 30 天人气变化
get_rank_trend_data("000001", days=30)

# 8. 生成平安银行综合分析报告
generate_stock_report("000001")
# 返回：趋势/支撑阻力/风险等级/仓位建议

# 9. 形态扫描：识别全市场符合形态的标的
scan_patterns(strict=True)

# 10. 筛选20只上涨结构候选，并为每只准备月周日深研数据
screen_rising_candidates(top_n=20, strict=True)
prepare_stock_analysis("000001", days=500, include_chart=True)

# 11. 同时执行事件研究和可执行交易模拟
backtest_pattern_strategy(mode="both", split="2022-01-01")
```

---

## 开发

```bash
git clone https://github.com/lalal-zzz/stock-analysis-mcp.git
cd stock-analysis-mcp
pip install -e ".[dev]"
pytest                    # Python 单元测试
npm run test:node         # Node 安装器测试
pytest -m integration     # 可选：真实网络 + 写本地库的集成测试
```

## 数据一致性与维护

参见[数据修复与验证](docs/data-repair.md)：只读审计、带归档的全历史修复、断点与失败重试、
本地价格预警、人工审核规则启用和每日净值回测。
历史条数、最新交易日和复权一致性覆盖分别披露。安装器包含绘图依赖，发布前验证两代MCP SDK。

## 许可证

MIT License

### News and financial Skills

| Skill | Purpose |
|---|---|
| `stock-analysis-news` | News evidence and citations |
| `stock-analysis-related-news` | Company/concept event timelines |
| `stock-analysis-global-markets` | Macro policy and global market context |
| `stock-analysis-financials` | Multi-period financial metrics and peers |
