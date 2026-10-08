# 新闻与财务证据来源

新增4个MCP工具与4个Skills，总计24个工具、12个Skills。原有20个接口保持兼容。

| 能力 | 工具 | 来源和边界 |
|---|---|---|
| 新闻搜索 | `search_market_news` | Fed/ECB官方RSS；有关键词时加东方财富，单次至多100条搜索结果 |
| 个股与概念消息 | `get_stock_related_news` | 股票代码、本地名称、用户概念或最多5个本地板块成员记录；展示检索关联依据 |
| 全球市场背景 | `get_global_market_news` | 官方政策发布加市场关键词检索；不提供实时市场报价 |
| 财务分析 | `analyze_stock_financials` | 东方财富最多20期主要财务指标；最多5个指定同业，只比较相同报告期 |

## 来源选择与实测

- [美联储官方RSS目录](https://www.federalreserve.gov/feeds/feeds.htm)，使用 `https://www.federalreserve.gov/feeds/press_all.xml`。
- [欧洲央行官方RSS目录](https://www.ecb.europa.eu/home/html/rss.en.html)，使用 `https://www.ecb.europa.eu/rss/press.html`。
- [AKShare接口文档](https://akshare.akfamily.xyz/data/stock/stock.html)记录东方财富新闻与财务指标字段；程序直接经本项目网络层调用对应提供商，不复制第三方代码中的固定Cookie。
- 东方财富新闻接口 `https://search-api-web.eastmoney.com/search/jsonp`，财务接口 `https://datacenter.eastmoney.com/securities/api/data/get`。它们是网页使用的公开接口，没有可保证的SLA；结构变化需适配。

2026-10-08的少量验证中，两项RSS可解析，600519新闻与2期财务指标可获取。一次验证不能证明长期稳定性。
官方RSS发布政策原文，覆盖范围有限；东方财富补充检索范围，并非官方原始公告。

## 缓存、限流与离线

独立缓存目录为 `<data_root>/information-cache`，不改写现有行情数据库。
新闻缓存15分钟，财务缓存24小时；单请求超时10秒、不自动重试、同主机请求间隔至少0.5秒。
同主机连续3次网络失败进入现有10分钟熔断冷却。多个官方来源分别披露状态，不互相冒充。
联网失败可降级到7天内旧缓存，必须标记过期且保留原获取时间。离线模式可读取更旧缓存，但明确标为过期。
`offline=true` 只读缓存，不请求网络，不初始化行情，不创建空缓存。

```text
search_market_news(query="银行", days=7, limit=30, offline=false)
get_stock_related_news(symbol="600519", concepts=["白酒"], days=30, offline=false)
get_global_market_news(query="美股", days=7, offline=false)
analyze_stock_financials(symbol="600519", periods=8, peers=[], offline=false)
```

省略concepts时使用最多5个已有本地板块记录；传空数组可只查公司。板块所属日期会披露，不能默认仍有效。
所有消息保留原链接、出版者和检索提供商；摘要最多280字符，不抓取全文。
按规范化标题和UTC发布日期归并并保留参考来源与关联依据；这不是语义事件去重，转载也不是独立证据。
缺少可靠发布时间的消息不纳入时间窗口；没有结果不证明事件不存在。

财务金额单位为人民币元，每股指标为元/股，`_pct` 为百分数。
已有本地行情时附带PE/PB、市值和价格快照及quote_date，不触发行情刷新，也不把快照当作实时估值。
报告期收入利润为年初累计，不能冒充单季度或TTM；同业缺少相同报告期时保留null。
数据为当前可得修订版，不能用于无未来信息的历史时点回测。尚不提供完整三张报表、实时估值、原始公告归档或完整产业链关系图。

## 验证

单元测试：`python -m pytest tests/test_information.py`。
少量联网验证：`python -m pytest tests/test_information.py -m integration`，只检查两项RSS、3条股票新闻和2期财务指标。
