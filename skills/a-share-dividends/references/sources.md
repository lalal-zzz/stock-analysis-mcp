# 已核实的数据来源

核实日期：2026-10-08。执行时检查最新官方文档和返回字段。

## 东方财富 / AKShare

[AKShare股票数据文档](https://akshare.akfamily.xyz/data/stock/stock.html)：查找 `stock_fhps_em` 和 `stock_fhps_detail_em`。
[分红送配页面](https://data.eastmoney.com/yjfp/)、[单股详情](https://data.eastmoney.com/yjfp/detail/000001.html)、[开源接口实现](https://github.com/akfamily/akshare/blob/master/akshare/stock_feature/stock_fhps_em.py)。

`ak.stock_fhps_em(date="20251231")` 是指定分红报告期的批量表，文档支持YYYY0630及YYYY1231；不是指定公告日全市场接口，不能用两个报告期证明每股最近一次已覆盖。`ak.stock_fhps_detail_em(symbol="000001")` 返回单股详情。公开文档包括预案公告日、股权登记日、除权除息日、方案进度、最新公告日期，未列派息日。

附带脚本沿用开源实现中的公开网页接口，按字段名解析，避免依赖列顺序。它不是有稳定承诺的商业API，可能失效，不擅自规避限制。

实测地址 `https://datacenter-web.eastmoney.com/api/data/v1/get`，表 `RPT_SHAREBONUS_DET`，pageSize/pageNumber分页，REPORT_DATE按报告期筛选，SECURITY_CODE按单股筛选。

| 原始字段 | 含义 |
|---|---|
| SECUCODE / SECURITY_CODE / SECURITY_NAME_ABBR | 数据商证券标识、代码、简称 |
| REPORT_DATE | 报告期 |
| PLAN_NOTICE_DATE | 预案公告日 |
| NOTICE_DATE | 最新公告日，非自动等于实施公告日 |
| EQUITY_RECORD_DATE | 股权登记日 |
| EX_DIVIDEND_DATE | 除权除息日 |
| ASSIGN_PROGRESS | 方案进度 |
| IMPL_PLAN_PROFILE | 方案原文，用于核对金额口径 |
| PRETAX_BONUS_RMB | 原始现金数值，结合方案原文核实每10股等单位 |
| BONUS_RATIO / IT_RATIO | 送股/转增原始比例，保留原单位待核实 |

未确认派息日字段，脚本输出null；由实施公告补齐。最新方案快照不是历史逐版本公告档案。

## Tushare Pro

[官方dividend文档](https://tushare.pro/document/2?doc_id=103) 提供 `pro.dividend(ts_code="000001.SZ")`，可按ann_date、record_date、ex_date、imp_ann_date查询，至少指定一个条件。字段包括实施进度、预案/决案公告日、实施公告日、登记日、除息日、派息日、红股上市日、每股含税/税后分红和送转比例。

截至核实日，文档列起始2000-01-01、每次2000行、至少2000积分及每日更新窗口。执行前再次确认实际权限、截断/分页行为与沪深北覆盖。不要在聊天索取token，使用本地配置/环境变量，不打印或写入skill。

## 公告与消息

正式来源：[巨潮](https://www.cninfo.com.cn/)、[上交所公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/)、[深交所](https://www.szse.cn/)、[北交所](https://www.bse.cn/)。按证券标识检索权益分派实施及更正公告，提取A股适用方案、登记日、除息日、红利发放安排、送转上市日和差异化分配。

新闻扩大线索覆盖，不能作全市场底表，也不能将报道日当公司公告日。A+H公司分别处理市场和币种。“10派X”是每10股，不直接与Tushare每股字段相加。
