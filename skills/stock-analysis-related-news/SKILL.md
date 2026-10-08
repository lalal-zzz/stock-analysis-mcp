---
name: stock-analysis-related-news
description: Find stock and concept-related news and build evidence-based timelines. Use for 个股新闻、概念消息、题材催化或产业链消息关联.
---

# stock-analysis-related-news

Use `get_stock_related_news(symbol="600519", concepts=["白酒"], days=30, limit=30, offline=false)`. Concepts are optional user research terms, at most five. The tool searches the code, locally known company name, and these terms; it does not prove concept membership or supply a complete industry-chain graph.

When concepts is omitted, the tool searches up to five already-stored sector memberships and discloses their updated_date. Those records can be stale; pass concepts=[] to restrict research to the company. Preserve all associations attached to a grouped event.

Inspect association.query and association.kind. Company-search hits can mention an unrelated issuer; verify named entities in the source before attributing an event. User-concept matches are topic evidence, not direct company exposure. Distinguish direct company facts, verified sector relevance and speculative transmission paths.

Report a dated timeline with original links, relationship evidence, conflicting facts and source/cache limits. Keep interpretation separate from observed events. Use offline=true when networking is paused. Missing local names reduce coverage; do not download stock history just to enrich news. Do not claim that news caused a price move or invent sentiment scores.

