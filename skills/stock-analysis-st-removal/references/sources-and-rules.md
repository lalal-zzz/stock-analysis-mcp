# 来源与规则核对

接口于2026-10-18实测。脚本优先调用本项目 `data/network.py` 的 `http_get/http_get_text`，不另外读登录凭据或写代理配置。公开网页接口无稳定性保证；失败最多重试一次，报告失败与覆盖边界。

## 已验证接口

|用途|地址/参数|校验与边界|
|---|---|---|
|当期ST名单|`https://push2.eastmoney.com/webguest/api/qt/clist/get`，`fs=b:BK0511`，`pn/pz=100`，`fields=f12,f14,f13`|`data.total`与全部分页去重计数一致；板块含*ST，普通ST另筛。核对A股类型，不能包含200/900开头B股。该来源不是交易所官方全市场证明。|
|财报目录交叉核对|`https://datacenter-web.eastmoney.com/api/data/v1/get`，`reportName=RPT_DMSK_FN_INCOME`，按`REPORT_DATE`筛选|读取明确报告期并完成分页；保留`SECUCODE/SECURITY_TYPE_CODE/TRADE_MARKET_CODE`。缺报企业可能缺失，因此不能单独当作当前ST全名单。|
|公告历史|`https://np-anotice-stock.eastmoney.com/api/security/ann`，`stock_list`、`page_size=100`、`page_index`、`ann_type=A`、`sr=-1`|核对`total_hits`、日期排序、重复分页、时间截断；保留不属于ST时期的原始实施与处罚公告。|
|公告正文|`https://np-cnotice-stock.eastmoney.com/api/content/ann`，`art_code/page_index/client_source=web`|`notice_content`每块最多约5000字，`page_size`是接口正文分块数量；不等于PDF物理页数。逐块核对公告ID及空响应，记录总块数、已取块数、是否完整。|
|多期财务|`https://datacenter.eastmoney.com/securities/api/data/get`，`type=RPT_F10_FINANCE_MAINFINADATA`、`sty=APP_F10_MAINFINADATA`、`filter=(SECUCODE="代码.市场")`|归母`PARENTNETPROFIT`、扣非`KCFJCXSYJLR`、营收`TOTALOPERATEREVE`；区分报告期与公告日、累计数与单季数、重述前后口径。|
|当前名称与报价|`https://qt.gtimg.cn/q=市场代码`|按GBK解析，核对返回代码和时间；只核名称状态不能证明历史日期的ST身份。|

公告详情：`https://data.eastmoney.com/notices/detail/<代码>/<公告ID>.html`。正式公告优先于媒体和股吧；东方财富是转发载体，重大冲突回查交易所、公司或证监会。

## 规则入口

- [上交所主板上市规则（2026年4月修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/mainipo/c/c_20260424_10816589.shtml)
- [深交所主板上市规则（2026年修订）](https://docs.static.szse.cn/www/lawrules/rule/stock/W020260424747613955674.pdf)
- 创业板、科创板从各自交易所规则目录核对现行版本；不要把主板条款号套给它们。本轮已读取创业板2026版PDF及科创板2026版原文DOCX；具体条款、盈亏与撤销关系见[专项参考](removal-rules-2026.md)。

2026年10月核对：沪深主板处罚导致财务虚假记载ST的撤销均涉及正式处罚作出满12个月及相应年度追溯重述；已提起虚假陈述诉讼时还须检查适用的预计负债要求。内控、资金占用、担保、账户冻结、经营影响及分红类各有条件，不存在统一“三年不亏损即可摘帽”公式。旧案还要核对新旧规则衔接。

不要固化某家公司某年可摘帽日期。作出日、收到日、公告日、停牌日及实施日分别保存，任何一个未知都应明确未知。监管网页发布日期也不能直接当决定作出日。

公告筛选使用实际公开日（`display_time`，缺失则保守回退），正式公告日`notice_date`另存。程序不能因正式日期在明天就排除今天已公开的决定。
