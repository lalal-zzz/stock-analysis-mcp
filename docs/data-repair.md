# 数据完整性与复权修复

三种价格口径分别保存：`qfq` 前复权、`hfq` 后复权、空字符串不复权。不同口径不能互相填补。
腾讯分钟接口不提供复权选择，调整后的分钟请求改走支持该参数的东财接口。
搜狐只能用于不复权。返回为空比把普通价格标为复权价格更可靠。

## 审计与修复

```bash
python -m stock_analysis_mcp.cli data-audit --output reports/data-audit.json
python -m stock_analysis_mcp.cli data-repair --dry-run --output reports/repair-plan.json
python -m stock_analysis_mcp.cli data-repair --symbols 000001,600000 --adjust all --output reports/repair-small.json
python -m stock_analysis_mcp.cli data-repair --adjust all --with-sectors --output reports/repair-all.json
```

修复按股票、复权类型断点续传。失败记录保留错误和10分钟后的重试时间，冷却后重复同一命令继续。
请求经过腾讯全局限速和东财熔断。程序不会删除整库，也不会用更短的历史覆盖现有历史。
整段替换之前，旧价格以压缩JSON保存在 `kline_repair_archive`；相关指标和结构缓存失效后重算。
需要撤销某项修复时，使用 `data-restore --batch-id BATCH_ID`；撤销前也会归档当前价格。
JSONL日志保存每项结果，JSON检查点保存最近100项；完成后输出完整JSON。

每个数据源独立拉取整段历史；中途失败的页不能与另一个数据源拼接。
常规日更比较重叠区间的OHLC和来源；发现复权基准或来源改变，整段重新下载。
原始行情与复权因子的统一重建尚未作为默认方式；当前方式是独立保留提供商的三种完整序列。

覆盖率必须同时查看历史条数、状态和最新完成交易日。交易日历缺失或过期时不能确认新鲜度。
新股短历史、停牌与退市不能简单认定为下载失败。报告保留交易日缺口，等待有停复牌证据的分类。
`full_market_complete=false` 表示上市起点或缺口仍未得到完整证明，不等于程序未完成已选股票的下载。

## 板块、预警和规则

板块采用东财单一价格口径。失败进入 `sector_retry_queue`，冷却后重新运行包含板块的修复命令。
状态接口披露板块K线覆盖和指标版本。

预警规则是包含 `zone_id,symbol,timeframe,lower,upper` 的JSON数组，价格使用不复权口径。
`timeframe` 支持分钟周期和101/102/103，或daily/weekly/monthly。

```bash
python -m stock_analysis_mcp.cli alerts-watch --rules zones.json --poll-seconds 60
python -m stock_analysis_mcp.cli strategy-rule propose --registry rules.json --filters filters.json --evidence backtest.json
python -m stock_analysis_mcp.cli strategy-rule activate --registry rules.json --version VERSION --approved-by REVIEWER --effective-date YYYY-MM-DD
```

事件写入本地SQLite，通过 `get_data_status` 查看最近事件；程序不会向外部账户发送消息。
候选规则提案不会启用。人工批准后还需设置 `STOCK_ANALYSIS_RULE_REGISTRY` 指向该文件才会使用；
生效日期之前的历史仍使用原规则。`rollback` 子命令用同样的审批字段回滚到既有版本。

## 回测

交易模拟包含买卖成本、可配置滑点、成交量参与上限、跳空止损、跌停延期退出和未平仓持仓。
净值逐日估值并导出净值/现金/仓位暴露CSV；可通过 `benchmark_symbol` 指定有本地历史的基准。
该模型仍不是逐笔成交仿真；回测报告公开成交约定、价格口径和数据不足警告。
共同因子分箱只使用训练期阈值，测试期不参与阈值估计；候选规则不会自动修改生产规则。
