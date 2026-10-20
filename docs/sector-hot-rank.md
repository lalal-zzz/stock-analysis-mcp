# 同花顺热门概念板块

`get_sector_hot_rank(limit=20, offline=false)` 获取同花顺网页使用的热门概念榜。
接口：`https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate?type=concept`。
来源页面：https://eq.10jqka.com.cn/webpage/ths-hot-list/index.html 。这是网页接口，非稳定性承诺的开放 API。

## 返回字段与差值

| 字段 | 含义 |
| --- | --- |
| sector_code / code_namespace | 同花顺六位板块代码 / ths；不等于东方财富 BK 代码 |
| heat_rank / heat_value | 来源 order / rate；网页展示热度，不是资金或人数，算法未公开 |
| change_pct | 来源 rise_and_fall，百分比值，不乘100 |
| source_rank_change_raw | 来源 hot_rank_chg；比较时点与符号语义尚未核实，不自行解释 |
| heat_delta | 当前热度减上一次保存观测热度；正值升温，负值降温 |
| rank_improvement | 上次排名减当前排名；正值排名改善，负值排名下降 |
| source_as_of | 当前为 null；响应没有可核实的行情时间 |

差值使用相邻**真实获取观测**，不是自动的日变化或一小时变化。`comparison` 返回两次获取时间。
首次获取或新进入榜单的板块差值为 null；离榜代码单独返回，不能当作热度降为零。
计算使用完整来源榜单，`limit` 只截取输出，避免把用户没请求的板块误判为离榜。
缓存读取不产生新观测，也不会把当前观测与自身比较。

## 存储与缺失

复用 information-cache，键前缀 `sector-hot:v1:`，TTL 为300秒，支持只读离线模式和来源失败时过期缓存回退。
真实网络成功获取后，在同目录 `sector-hot-snapshots.sqlite3` 保存不可覆盖的完整归一化榜单。
快照写入失败仍返回来源数据并披露差值缺失。离线缓存缺失不联网、不创建库。
`fetched_at` 只表示获取时间；即使 HTTP 成功，也不能据此称作今天、实时或最新交易日行情。
因此有数据时状态仍为 partial，并在 MCP 外层提升口径警告。

## 范围与研究限制

当前仅接入来源热门概念列表，实测返回20条；不代表全部概念、行业或全市场排名，也未提供历史日期查询。
不根据名称自动拼接东方财富资金数据：两个平台的成分与板块定义不同。
资金聚合、成分重叠分摊与“高热度高涨幅是否延续两日”仍需单独研究和历史验证，本工具不输出预测胜率。
现有本地资金快照仅3个日期，板块成分缺历史版本，不能据此宣称已经验证资金增强效果。

本次接入验证包含真实源请求、MCP封装、缓存/离线/失败回退、正负差值、首次/新入/离榜、代码隔离、异常响应。
离线测试不依赖当日板块名称或热度。
