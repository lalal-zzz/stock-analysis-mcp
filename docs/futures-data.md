# 国内期货数据

新增三个按需查询工具，不需要初始化股票数据库。

| 工具 | 参数与用途 |
|---|---|
| `list_futures_products` | 可选 `exchange`: shfe/ine/dce/czce/cffex/gfex，返回来源品种名称和 node |
| `get_futures_quotes` | 必填 `node`，可选 page、page_size，返回该品种合约行情 |
| `get_futures_kline` | 必填 `symbol`，period 为 daily 或 1/5/15/30/60 分钟；支持 start_date、end_date、limit |

三个工具均支持 `offline=true`。数据存于 information-cache 下 futures:v1 命名空间，不进入股票同步、筛选、图表或回测。

示例：先调用 `list_futures_products(exchange="cffex")`，从结果取沪深300对应 node，再调用 `get_futures_quotes(node="qz_qh")` 查询合约代码。`get_futures_kline(symbol="IF0", period="daily", limit=320)` 查询来源主力连续日线；具体月份代码应从行情结果取得。

数据来自新浪公开接口，字段参考 [AKShare 官方期货接口说明](https://github.com/akfamily/akshare/blob/main/docs/data/futures/futures.md)，HTTP 请求复用项目网络层。支持来源目录内商品、股指和国债期货，不代表六家交易所所有上市品种均已覆盖；例如来源未列出的新品种不会自行补造。INE 品种从新浪合并的上海目录中拆分。

行情为第三方公开快照，可能延迟；source_time 可能只有时间没有日期，不能以 fetched_at 代替行情时间。成交量、持仓量保留来源口径，不推断单双边。结算价为来源值，0 或缺失转为 null，不能代替正式结算数据。分钟线仅有近期窗口；日线返回来源可用历史，不保证给定日期区间完整，available_start/end 提供源数据边界。夜盘时间保留来源标记，不推算所属交易日。最新日线可能仍在形成。

以 0 结尾的主力连续代码（如 RB0、IF0）是来源拼接序列，可能存在换月跳空，不保证复权，不是可直接交易合约。郑商所三位月份代码存在跨十年歧义，保留来源代码。K线接口仅使用独立的新浪期货命名空间，不使用股票代码猜测，不对交易所归属作额外推断；未知代码返回来源不可用状态。

缓存：品种目录24小时、合约快照30秒、K线5分钟；过期回退、离线未命中和网络错误通过 source/warnings 披露。暂未提供期权、逐笔、全深度委托、保证金/手续费、仓单库存、席位排名或全市场历史批量同步。
