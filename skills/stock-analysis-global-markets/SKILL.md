---
name: stock-analysis-global-markets
description: Review global market and macro-policy news from official releases and bounded market searches. Use for 全球市场消息、海外市场、美联储、欧洲央行或跨市场背景.
---

# stock-analysis-global-markets

Use `get_global_market_news(query="全球市场", days=7, limit=30, offline=false)`. It combines unfiltered Fed/ECB official releases with a bounded Eastmoney keyword search. Choose a specific research keyword when useful; state the chosen scope.

Separate central-bank decisions, commentary, reported market moves and inference about A-share transmission. Cite original links and publication timestamps, preserve source failures, and disclose caches. The tool supplies news, not real-time index, yield, FX or commodity prices. Do not turn a quoted rate in an old release into a current market quote. Coverage is limited to these sources, not all countries or all asset classes. Respect offline=true when downloads are paused.

