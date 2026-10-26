# 开盘竞价过程：免费数据与精度上限

## 调用

```python
get_stock_auction(symbol="000001")
get_stock_auction(symbol="600000", trade_date="2026-10-26")
get_stock_auction(symbol="000001", trade_date="2026-10-26", offline=True)
get_stock_order_book(symbol="600000")
```

无需初始化K线库。不安装后台任务。重点是保留来源数据和披露实际精度。

## 数据源

优先查询通达信公开主站 `0x056a` 竞价过程快照，明确传入请求日期。返回09:15:00–09:25:00范围的全部来源记录，包含采样时间、参考价、虚拟匹配量、未匹配量和方向。协议解析参考[MIT项目injoyai/tdx](https://github.com/injoyai/tdx/blob/master/protocol/model_call_auction.go)，许可见[第三方声明](third-party-notices.md)。不新增第三方运行依赖。二进制连接、限时收包和解压在 `data/network.py` 中统一处理，最多尝试两个公开节点，使用已有熔断机制。

普通HTTP请求仍通过已有 `http_get`，不直接使用requests/httpx。东方财富分钟分时、采样价量及独立09:25正式撮合作为补充；专用源失败时降级并明确提示未匹配量缺失。

协议请求日期、市场和代码均明确指定，但响应不回显这些标识，`date_validation=explicit_request_date_not_echoed_by_protocol` 披露这一限制。历史覆盖不保证；实测2026-10-23历史请求为空，不以当天或最近交易日替代。离线查询严格不联网。

## 字段与单位

| 字段 | 含义 |
|---|---|
| `records[].time` | 北京时间，时间戳精确到秒，不代表每秒都有记录 |
| `price` | 该快照的虚拟匹配参考价，元；无有效参考价时null |
| `volume` / `matched_volume` | 虚拟匹配量，股；来源手数乘100 |
| `amount` | 参考价乘匹配股数，元，标注 `derived_price_times_volume` |
| `unmatched_buy_volume` | 买方未匹配量，股 |
| `unmatched_sell_volume` | 卖方未匹配量，股 |
| `unmatched_signed_volume` | 买方为正、卖方为负、平衡为0，股 |
| `unmatched_direction` | buy / sell / balanced |
| `unmatched_amount` | 参考价乘未匹配股数绝对值，元，为推导值 |
| `source_volume_lots` / `source_unmatched_signed_lots` | 来源原始手数，后者按有符号32位解码，避免大额排队翻转方向 |
| `latest_virtual_match` | 最后一条过程快照，不代表最终开盘成交 |
| `opening_match` | 独立取得的09:25正式撮合；缺失为null |
| `minute_records` | 补充分钟分时原值，不能替代匹配/未匹配量 |

专用过程快照即使时间为09:25，也仍标为虚拟匹配。虚拟匹配快照不是新增成交量，不能逐条相加。未匹配量是竞价参考价下揭示的剩余量，不是全部价格档位的买卖挂单总和。变化不能直接等同于逐笔撤单。

东方财富正式撮合记录可能带有09:25之后几秒的来源时间，不能只接受恰好09:25:00。实测2026-10-26浦发银行为09:25:02、9.48元、3952手。补充明细允许09:25:00至09:26:00前的非虚拟匹配记录，保留来源时间，不改写为09:25:00。

## 09:26查询检查

程序没有收盘后禁止回读的时段限制。2026-10-26 22:26联网回读确认，两个标的仍能取得早盘竞价过程；09:26模拟时钟测试确认程序可同时处理过程快照与独立正式撮合记录。模拟测试不证明远端发布时效：尚未在交易日09:26当刻现场验证数据是否齐全，也不承诺09:26:00一定取得全部记录。

## 精度：实际测量，不承诺每秒全量

`precision` 每次返回：

- `data_kind=auction_process_snapshots`、`order_by_order_available=false`。
- `timestamp_resolution_seconds=1`：字段能表示秒，不是1秒采样保证。
- 首末记录时间、实际覆盖秒数、最小/中位/最大采样间隔。
- 09:15:00–09:25:00含两端共601个秒位的缺失数量与 `every_second_available`。
- `exchange_to_client_latency_verified=false`：没有验证交易所到客户端的端到端延迟。

`coverage` 另披露分钟缺口、样本数量、未匹配量是否存在、完整委托历史是否存在。分钟有数据不能视为该分钟内逐秒齐全。缺秒不补点、不插值，不把重复轮询当新增行情。

2026-10-26实测：

| 标的 | 过程点数 | 首末时间 | 间隔中位数 | 最大相邻间隔 |
|---|---:|---|---:|---:|
| 000001 平安银行 | 67 | 09:15:00–09:24:57 | 9秒 | 18秒 |
| 600000 浦发银行 | 77 | 09:15:03–09:24:59 | 6秒 | 33秒 |

这些是当天两个标的的实测值，不是所有证券、交易日的固定刷新频率。免费源能观察参考价与匹配/未匹配量的过程，但不能声称获得每秒所有委托、撤单。09:25前是申报和虚拟撮合，不能将其当作已经发生的逐笔成交。

## 缓存与盘口

数据保存在配置数据根目录 `information-cache/auction.sqlite3`，与K线库独立。远端失败时保留本地历史并明确 `origin=archive`；无记录则披露缺失。

`get_stock_order_book` 获取当前五档原值、来源时间与实际获取时间。`f530`是五档字段组。只有来源时间在竞价窗口、距获取时刻0至60秒且有有效价格的快照才留档。盘后盘口不能回填早盘。

五档中的 `auction_indication` 如果存在，只是按沪市交易所字段布局给出的解释，`provider_mapping_verified=false`，不能冒充已核验数据。专用竞价序列的未匹配量直接来自协议字段，不依赖这种解释。

## 逐笔数据源核验

要提高到逐笔委托/撤单级别，必须单独核验事件序号、毫秒时间、委托号、事件类型、市场差异、历史范围与发布延迟。[迅投文档](https://dict.thinktrader.net/nativeApi/xtdata.html)列出逐笔委托、逐笔成交和沪深不同撤单字段，但不能凭接口名称断言竞价全段实时可得。目前没有验证到能免费提供沪深竞价全段实时逐笔事件的接口。

## English

The primary source is a free TDX auction-process snapshot stream with indicative price, matched volume and signed unmatched volume. These are sampled snapshots, not order-by-order events. The response measures actual intervals and missing seconds, preserves source points without interpolation, separates opening execution and discloses date/identity and latency verification limits. Offline reads only use archived data. Historical availability is not guaranteed.
