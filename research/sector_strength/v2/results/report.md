# 真实数据第二轮验证

下载 778 只股票的新浪原始历史，共 4,069,083 条记录；进入面板 23 个行业，面板日期 2016-01-04 至 2023-12-29。

原始新浪压缩数据符号头逐只核验；778只股票与上轮腾讯未复权数据作交叉核对。来源日期截断保留，抓取时间不冒充源市场时间。

本轮仅使用2017年起的数据训练、2020～2023年逐年回溯留出。上轮2024～2026年数据不重新作为新测试集。每个年度的训练/验证标签必须在各自区间结束前成熟；ridge标准化只拟合训练集；alpha只按前一年验证IC选择。新模型由上轮结果启发，且当前成分股回看历史存在偏差，因此本轮不能称为严格前瞻验证。

| 模型 | 测试日期数 | 5日平均IC | IC 95%分块区间 | 高−低5日收益差 |
|---|---:|---:|---|---:|
| nonmonotonic_heat | 946 | 0.0125 | [-0.014978985085316743, 0.041098056893680915] | 0.071个百分点 |
| v1_linear_frozen | 946 | 0.0002 | [-0.03196727478510253, 0.03202198213548571] | 0.027个百分点 |
| v1_selected_frozen | 946 | 0.0016 | [-0.027342993454950306, 0.02843120610660714] | 0.001个百分点 |
| walk_forward_ridge | 946 | -0.0090 | [-0.03855032242630515, 0.0210949186292505] | 0.003个百分点 |

逐年结果：

| 年份 | 模型 | IC | 高−低收益差 |
|---|---|---:|---:|
| 2020 | v1_linear_frozen | 0.0228 | 0.204个百分点 |
| 2020 | v1_selected_frozen | 0.0274 | 0.115个百分点 |
| 2020 | nonmonotonic_heat | 0.0266 | 0.124个百分点 |
| 2020 | walk_forward_ridge | -0.0284 | -0.008个百分点 |
| 2021 | v1_linear_frozen | 0.0180 | 0.012个百分点 |
| 2021 | v1_selected_frozen | 0.0103 | -0.020个百分点 |
| 2021 | nonmonotonic_heat | 0.0415 | 0.152个百分点 |
| 2021 | walk_forward_ridge | 0.0365 | 0.074个百分点 |
| 2022 | v1_linear_frozen | 0.0022 | 0.037个百分点 |
| 2022 | v1_selected_frozen | 0.0040 | 0.062个百分点 |
| 2022 | nonmonotonic_heat | 0.0014 | 0.058个百分点 |
| 2022 | walk_forward_ridge | 0.0249 | 0.235个百分点 |
| 2023 | v1_linear_frozen | -0.0424 | -0.146个百分点 |
| 2023 | v1_selected_frozen | -0.0356 | -0.155个百分点 |
| 2023 | nonmonotonic_heat | -0.0196 | -0.053个百分点 |
| 2023 | walk_forward_ridge | -0.0692 | -0.290个百分点 |


所有数字为次日开盘开始的重叠事件统计，未计成本，不是组合年化收益。配对改善区间见 paired_results.json；分市场状态诊断见 regime_results.json。

## 数据限制

- current constituent and survival selection bias
- historical ST and exchange limit-price states unavailable; candidate-only limits
- historical sector baskets are not official Eastmoney indices
- current archived qfq factors, no historical publication snapshots
- no fees, slippage, portfolio capacity or executable fills
- predeclared new models motivated by prior-seen later period; historical reuse is not fully independent prospective evidence

涨停池请求20261103但响应qdate=20261104，已拒绝用其核验涨停；所有涨停字段仍标为候选。创业板2020-08-24前后10%/20%制度变化按日期处理。

输出包含原始压缩行情、解码缓存、请求身份与哈希、实验预登记、训练/验证/测试边界、标准化与权重、所有模型逐日结果。没有自动修改生产规则。

补充覆盖限制：虽然预定训练范围从2017年开始，但严格的95%成员/每日指标覆盖与至少8个行业门槛，使实际首个合格训练信号为2018-05-30。首个回归窗口仅有1628条行业日训练记录。没有因结果较弱而放宽门槛。

汇总结果：所有模型的平均IC 95%区间均覆盖零；非单调热度相对上轮固定模型的配对IC改善区间[-0.000703,+0.024622]同样覆盖零。不能认定已找到稳定强势延续预测能力。
