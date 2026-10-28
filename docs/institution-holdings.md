# 股东与机构披露持仓

新增两个 MCP 工具（总计35个），不需要初始化行情数据库：

| 工具 | 用途 |
|---|---|
| `get_stock_holdings` | 个股十大股东或十大流通股东及历史披露 |
| `search_institution_holdings` | 按股东完整名称精确反查，或按报告期分页查询市场明细 |

示例参数：

```json
{"symbol":"000001","report_date":"2026-06-30","scope":"float_top10","category":"social_security"}
```

```json
{"holder_name":"全国社保基金一零一组合","report_date":"2026-06-30","scope":"float_top10"}
```

通用参数：`scope=top10|float_top10`（默认后者）、`page=1..100`、`page_size=1..500`（默认100）、`offline`、`refresh`。个股或精确股东查询省略报告期会按报告期倒序分页，而不是自动限定最新一期；市场查询必须指定季度末报告期。

类别：`all`、`social_security`（社保）、`pension`（基本养老）、`fund`、`insurance`、`qfii`、`broker`、`trust`、`other`。按股东名称与来源类型关键词分类；other包含个人及未识别主体，不代表机构。基金类别不能直接认定为公募。

**分类在当前来源页内执行**。返回来源总数、总页数、页内原始行数与has_more；空分类页仍可能有后续页。最多支持来源100页，不能据此声称全市场穷尽。按精确股东名称查询不合并不同组合或账户。

来源为东方财富 `RPT_DMSK_HOLDERS` / `RPT_F10_EH_FREEHOLDERS`，通过项目网络层访问；字段参考 [AKShare来源适配](https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_gdfx_em.py)。持股数量单位股，比率为百分数值；总股本比例和流通股比例分列，变化量和变化比例保留来源口径，不根据缺失记录推断清仓或新进。

报告期、公告日和来源更新时间分别保存，缺失公告日不以更新时间冒充。它们是定期披露快照，不是实时持仓，也不是全部机构持仓；十大股东与十大流通股东有重叠，不能相加，多个报告期也不能相加。

缓存使用独立 `information-cache`，24小时有效，刷新失败可回退7天内旧缓存并标记stale；offline只读缓存。源故障与成功空结果区分，源失效不会冒充无持仓。不会写入股票K线库、影响同步或参与回测。
