# 多品种基础数据

新增五个 MCP 工具，提供按需查询，不需要执行股票初始化。

| 工具 | 功能 |
|---|---|
| `list_market_instruments` | 分页指数、ETF、LOF、基金目录、可转债和沪深交易所债券列表 |
| `get_market_quote` | 在品种目录中确认市场和代码后返回行情 |
| `get_market_kline` | 指数、ETF/LOF、可转债日／周／月未复权 K 线；普通债券历史尚未接入 |
| `get_fund_nav` | 分页历史单位净值、累计净值、日增长率、申购／赎回状态 |
| `get_convertible_bond_info` | 行情、正股关联、转股价／价值／溢价率、纯债价值及条款参考触发价 |

## 标的身份和示例

身份是 `market:asset_type:symbol`，如 `sh:index:000001` 和 `otc:fund:000001`。交易所品种必须传 `market` 或市场前缀。不会沿用“6 开头就是上海”的股票规则。指数支持 sh/sz/csi；ETF、LOF、可转债和债券支持 sh/sz。`fund` 的 otc 标识用于净值缓存，目录也包含场内基金，不代表全部允许场外申购。

```json
{"tool":"list_market_instruments","arguments":{"asset_type":"etf","page":1,"page_size":100}}
{"tool":"get_market_quote","arguments":{"symbol":"sh510300","asset_type":"etf"}}
{"tool":"get_market_kline","arguments":{"symbol":"sh000001","asset_type":"index","start_date":"2026-01-01","period":"weekly","limit":100}}
{"tool":"get_fund_nav","arguments":{"symbol":"000001","start_date":"2026-01-01","page":1,"page_size":100}}
{"tool":"get_convertible_bond_info","arguments":{"symbol":"sh110076"}}
{"tool":"list_market_instruments","arguments":{"asset_type":"bond","page":1,"page_size":100}}
```

`asset_type` 允许 index/etf/lof/fund/convertible_bond/bond。列表与净值 `page` 为 1–1000，`page_size` 为 1–100。K 线 `period` 为 daily/weekly/monthly，`limit` 为 1–2000。日期使用 YYYY-MM-DD，开始默认 2020-01-01，结束默认上海时区今天。K 线只返回区间内最多 limit 根，固定未复权。

## 来源、缓存与覆盖

- 东方财富 `push2delay` clist：指数、ETF/LOF、可转债。行情可能延迟；`quote_time` 是源提供的交易时间，`source.fetched_at` 仅是抓取时间。百分比保留百分数点口径，例如 6.96 表示 6.96%，不转换为 0.0696。
- 东方财富 `push2his`：未复权 K 线，使用既有熔断和主机轮转；失败时不跨源拼接。成交量保留源原始单位，不跨品种直接比较。价格字段 open/close/high/low 与源列次序一致。
- 天天基金 `fundcode_search.js`：基金目录，只提取 JSON，不执行远程 JavaScript。
- 天天基金 `f10/lsjz`：单位／累计净值及申赎状态，分页以源总记录数为准。每页记录按日期升序，page=1 是源最近一页；累计净值不作为分红再投资收益率。
- 新浪 `Market_Center.getHQNodeData` 的 hs_z：交易所债券集合，只保留沪深代码。可能包含可转债；不将此集合宣称为纯普通债券或全债券市场。源报价仅给时分秒，`quote_date=null`，无法确认交易日期。总数未知时 `has_more` 按原始页长度估计，可能需再读一页确认结束。

复用现有原子 JSON 缓存，缓存位于 `<data_root>/information-cache`，新键以 `assets:v1:` 开头，包含品种、市场、代码、日期、周期和分页。不会写入股票／板块数据库。目录行情缓存 5 分钟，基金目录 24 小时，K 线／净值 1 小时。`offline=true` 不联网；缓存缺失返回 partial 与警告，源失败可使用旧缓存并明确标记 stale。K 线联网抓取前必须用新鲜目录确认品种。

单标的查询最多检查 50 页目录，避免无界全市场请求。首次查询靠后的代码可能较慢；找不到时说明验证受限，不把它断言为不存在。列表查询只代表单页，不是全市场下载完成。不同 page_size 的页缓存不同；离线行情验证读取 page_size=100 的目录页，建议预先用这个页大小查询。

`source` 含 provider、url、抓取时点和缓存状态。净值和 K 线还返回 `as_of`。请同时检查外层 `{data, meta, warnings, error}` 和内层 source/status；缺失数值为 null，不补零。

## 尚未覆盖

本次补充的是基础数据查询。现有股票筛选、图表、形态扫描、数据库全市场同步和回测仍仅面向原有股票／板块，不能把新标的直接传入并宣称支持。

未接入基金持仓、费率、经理历史、完整分红事件与货币基金每万份收益／七日年化；未接入指数成分权重历史、普通债券历史K线、债券评级／付息计划／到期收益率、银行间债券和正式强赎公告。普通债券报价已验证，但现有东财K线源未返回所测普通债券数据，因而工具明确拒绝普通债券K线请求。IOPV 不是正式净值，条款触发价不是已经公告强赎。期货、期权、港美股不在本次范围。

接口结构参考 [AKShare 基金公开接口文档](https://github.com/akfamily/akshare/blob/main/docs/data/fund/fund_public.md) 和本机安装的 AKShare 基金、指数、债券适配器源代码；请求统一走本项目网络层。
