"""
腾讯行情数据源

- 个股 K 线: web.ifzq.gtimg.cn/appstock/app/fqkline/get (日/周/月, 支持前/后复权)
- 分钟 K 线: ifzq.gtimg.cn/appstock/app/kline/mkline (m1/m5/m15/m30/m60)
- 板块 K 线: web.ifzq.gtimg.cn/appstock/app/kline/kline + pt 板块代码
- 板块排行: proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank (行业/概念)
- 批量行情: qt.gtimg.cn/q=sh600000,sz000001,... (GBK, 单请求约 90 只)

单位约定(与东财对齐): 日/周/月K成交量单位为手, 分钟K成交量单位为手。
腾讯日K行内不含成交额/换手率, 对应字段返回 None。

接口限制: 单次请求最多 640 根(count>640 会异常返回更少且起始日期后移),
深历史通过日期区间向前翻页获取。
"""

import threading
import time

from ..util import TtlCache
from ..network import (
    http_get,
    http_get_text,
    mark_provider_fail,
    mark_provider_ok,
)

PROVIDER = "tencent"

FQKLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
BOARD_RANK_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"
QUOTE_URL = "https://qt.gtimg.cn/q="

_request_lock = threading.Lock()
_last_request = 0.0
_http_get = http_get


def http_get(*args, **kwargs):
    """Bound aggregate request rate across downloader threads."""
    global _last_request
    with _request_lock:
        delay = 0.3 - (time.monotonic() - _last_request)
        if delay > 0:
            time.sleep(delay)
        _last_request = time.monotonic()
    return _http_get(*args, **kwargs)

PAGE_LIMIT = 640  # 单次请求最大条数, 超过会被服务端异常截断
QUOTE_BATCH = 80  # qt.gtimg.cn 单请求代码数上限(实测 90+ 可用, 留余量)

_KLT_UNIT = {"101": "day", "102": "week", "103": "month"}
_KLT_MINUTE = {"1", "5", "15", "30", "60"}

_QT_FIELDS = {
    # v_..."=" 内 ~ 分隔字段的下标(实测对齐)
    1: "name", 2: "raw_code", 3: "latest_price", 4: "pre_close", 5: "open",
    30: "datetime", 31: "change_amount", 32: "change_pct", 33: "high",
    34: "low", 36: "volume", 37: "amount", 38: "turnover_rate", 39: "pe_dynamic",
}


def _f(val) -> float | None:
    try:
        v = float(val)
        return v
    except (TypeError, ValueError):
        return None


# ── 个股 K 线 ──


def fetch_stock_kline(symbol: str, limit: int = 250, klt: str = "101",
                      adjust: str = "qfq", start_date: str = None,
                      end_date: str = None) -> list[dict]:
    """
    个股 K 线(腾讯主源)。失败(网络)返回 [], 由降级链处理。

    返回行按时间升序, 键: date(或分钟级 datetime)/symbol/open/close/high/low/volume/
    amount(None)/amplitude/change_pct/change_amount/turnover_rate(None)。
    """
    if not provider_ready():
        return []

    prefixed = _prefixed(symbol)
    if prefixed is None:
        return []

    if klt in _KLT_MINUTE:
        # mkline has no adjustment selector; never silently return raw bars
        # for an adjusted request. The caller can use Eastmoney instead.
        return [] if adjust else _mkline(prefixed, symbol, klt, limit)
    unit = _KLT_UNIT.get(klt, "day")
    return _daily_like(prefixed, symbol, unit, adjust, limit, start_date, end_date)


def _prefixed(symbol: str) -> str | None:
    text = str(symbol).strip().lower()
    code = text[-6:] if len(text) >= 6 and text[-6:].isdigit() else None
    if not code:
        return None
    if text.startswith(("sh", "sz", "bj")):
        return text[:2] + code
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("8", "9", "4")):
        return "bj" + code
    return "sz" + code


def _bare(symbol: str) -> str:
    text = str(symbol).strip().lower()
    return text[-6:]


def _iso_date(text: str) -> str:
    """20260110 / 2026-01-10 → 2026-01-10(腾讯接口要求 ISO 格式)"""
    compact = str(text or "").replace("-", "").strip()
    if len(compact) == 8:
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return str(text or "").strip()


def _daily_like(prefixed: str, symbol: str, unit: str, adjust: str,
                limit: int, start_date: str, end_date: str) -> list[dict]:
    """日/周/月K: 复权走 fqkline, 不复权走 kline/kline; 按日期区间向前翻页"""
    want = max(1, min(limit, 5000))
    use_fq = adjust in ("qfq", "hfq")
    base = FQKLINE_URL if use_fq else KLINE_URL
    fq_suffix = adjust if use_fq else ""
    rows_key = f"{fq_suffix}{unit}"
    start = _iso_date(start_date) if start_date else ""
    end = _iso_date(end_date) if end_date else ""
    requested_start, requested_end = start, end

    all_rows: list = []
    for _ in range(10):  # 最多翻 10 页(640*10 根, 覆盖几十年)
        count = min(PAGE_LIMIT, want - len(all_rows) + 1)  # +1 根用于算涨跌
        if count <= 0:
            break
        param = f"{prefixed},{unit},{start},{end},{count},{fq_suffix}"
        payload = http_get(base, params={"param": param}, retries=2, timeout=15)
        if payload is None:
            mark_provider_fail(PROVIDER)
            return []
        mark_provider_ok(PROVIDER)

        box = (payload.get("data") or {}).get(prefixed) or {}
        rows = box.get(rows_key) or []
        if not rows:
            break
        all_rows = rows + all_rows  # 接口按日期升序, 向前拼接
        if len(rows) < count or (start and rows[0][0] <= start):
            break
        end = _shift_date(rows[0][0], -1)
        if not end:
            break

    if not all_rows:
        return []
    result = _normalize_rows(all_rows, symbol, "date")
    result = [r for r in result if (not requested_start or r["date"] >= requested_start)
              and (not requested_end or r["date"] <= requested_end)][-want:]
    for row in result:
        row.update(source=PROVIDER, adjust_type=adjust)
    return result


def _mkline(prefixed: str, symbol: str, klt: str, limit: int) -> list[dict]:
    """分钟K: 行格式 [YYYYMMDDHHMM, open, close, high, low, volume, {}, 均价]"""
    want = max(1, min(limit, 2000))
    param = f"{prefixed},m{klt},,,{want}"
    payload = http_get(MKLINE_URL, params={"param": param}, retries=2, timeout=15)
    if payload is None:
        mark_provider_fail(PROVIDER)
        return []
    mark_provider_ok(PROVIDER)

    box = (payload.get("data") or {}).get(prefixed) or {}
    rows = box.get(f"m{klt}") or []
    return _normalize_rows(rows, symbol, "datetime")[-want:]


def _normalize_rows(rows: list, symbol: str, time_key: str) -> list[dict]:
    """腾讯行 [时间, open, close, high, low, volume, (额外元素...)] → 统一 12 字段

    注意腾讯字段顺序: 第2列为开盘、第3列为收盘。
    change_pct/change_amount/amplitude 依赖前收盘, 首行无前收盘时置 None。
    """
    items = []
    prev_close = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            open_ = _f(row[1])
            close = _f(row[2])
            high = _f(row[3])
            low = _f(row[4])
            volume = _f(row[5])
        except (TypeError, ValueError):
            continue
        if close is None:
            continue
        item = {
            time_key: str(row[0]), "symbol": _bare(symbol),
            "open": open_, "close": close, "high": high, "low": low,
            "volume": volume, "amount": None, "turnover_rate": None,
        }
        if prev_close:
            item["change_amount"] = close - prev_close
            item["change_pct"] = round((close - prev_close) / prev_close * 100, 4)
            item["amplitude"] = (
                round((high - low) / prev_close * 100, 4)
                if high is not None and low is not None else None
            )
        else:
            item["change_amount"] = item["change_pct"] = item["amplitude"] = None
        items.append(item)
        prev_close = close
    return items


def _shift_date(iso_date: str, days: int) -> str | None:
    try:
        import datetime as _dt

        d = _dt.date.fromisoformat(str(iso_date)[:10]) + _dt.timedelta(days=days)
        return d.isoformat()
    except ValueError:
        return None


# ── 板块 K 线 ──


def fetch_board_kline(pt_code: str, limit: int = 250, klt: str = "101") -> list[dict]:
    """板块指数当日 bar(pt 代码)。

    实测限制(2026-08): 腾讯 ifzq 的 kline/kline、fqkline、mkline 对板块
    代码一律只返回最新 1 根(count/日期区间被忽略, 分钟线直接报错)。
    因此本函数只用于取"板块当日行情", 不能做历史K线源; 板块K线历史
    仅东财 push2his 提供(口径: 腾讯板块成分集合与东财不同, 聚合量
    有 ±20% 级差异, 跨源混写会引入断层)。
    """
    if not provider_ready():
        return []
    unit = _KLT_UNIT.get(klt, "day")
    want = max(1, min(limit, PAGE_LIMIT))
    param = f"{pt_code},{unit},,,{want}"
    payload = http_get(KLINE_URL, params={"param": param}, retries=2, timeout=15)
    if payload is None:
        mark_provider_fail(PROVIDER)
        return []
    mark_provider_ok(PROVIDER)

    box = (payload.get("data") or {}).get(pt_code) or {}
    rows = box.get(unit) or []
    return _normalize_rows(rows, pt_code, "date")[-want:]


# ── 板块排行(名称→pt 映射数据源) ──


def fetch_board_rank(board_type: str = "hy", count: int = 1000) -> list[dict]:
    """
    行业/概念板块排行。board_type: "hy" 行业 / "gn" 概念。

    返回行: code(pt)/name/zdf(涨跌幅%)/turnover(成交额,万)/zljlr(主力净流入,万)/
    hsl(换手率)/lb(量比)/lzg(领涨股 dict)/zdf_d5/zdf_d20 等。
    """
    if not provider_ready():
        return []
    rows: list[dict] = []
    offset = 0
    while offset < count:
        params = {
            "board_type": board_type, "sort_type": "price", "direct": "down",
            "offset": str(offset), "count": "100",
        }
        payload = http_get(BOARD_RANK_URL, params=params, retries=2, timeout=15)
        if payload is None:
            mark_provider_fail(PROVIDER)
            return rows if rows else []
        mark_provider_ok(PROVIDER)
        data = payload.get("data") or {}
        batch = data.get("rank_list") or []
        rows.extend(batch)
        total = data.get("total") or 0
        offset += 100
        if not batch or offset >= total:
            break
    return rows


# ── 批量实时行情(GBK) ──


def fetch_quote_batch(prefixed_symbols: list[str]) -> list[dict]:
    """
    qt.gtimg.cn 批量行情, 自动按 80 只分批。
    volume 单位手, amount 单位万元(已 ×10000 转为元)。
    """
    result: list[dict] = []
    for i in range(0, len(prefixed_symbols), QUOTE_BATCH):
        batch = [s for s in prefixed_symbols[i:i + QUOTE_BATCH] if s]
        if not batch:
            continue
        text = http_get_text(
            QUOTE_URL + ",".join(batch), retries=2, timeout=15, encoding="gbk",
        )
        if text is None:
            mark_provider_fail(PROVIDER)
            continue
        mark_provider_ok(PROVIDER)
        result.extend(_parse_qt_text(text))
    return result


def _parse_qt_text(text: str) -> list[dict]:
    import re

    items = []
    for m in re.finditer(r'v_(\w+)="([^"]*)"', text):
        prefixed, raw = m.group(1), m.group(2)
        if not raw:
            continue
        parts = raw.split("~")
        item = {"prefixed": prefixed, "raw_code": _bare(prefixed)}
        for idx, name in _QT_FIELDS.items():
            if idx < len(parts):
                val = parts[idx]
                item[name] = val if name in ("name", "datetime", "raw_code") else _f(val)
        if item.get("amount") is not None:
            item["amount"] = item["amount"] * 10000  # 万 → 元
        items.append(item)
    return items


def provider_ready() -> bool:
    from ..network import provider_available

    return provider_available(PROVIDER)


# ── 名称 → pt 代码索引(进程内缓存 24h) ──

_PT_INDEX_TTL = 24 * 3600


def _fetch_pt_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for board_type in ("hy", "gn"):
        for row in fetch_board_rank(board_type):
            name = str(row.get("name", "")).strip()
            code = str(row.get("code", "")).strip()
            if name and code:
                index.setdefault(name, code)
    return index or None


_pt_index_cache = TtlCache(_PT_INDEX_TTL)


def get_name_pt_index(refresh: bool = False) -> dict[str, str]:
    """{板块名: pt 代码}, 数据来自行业+概念两个排行接口, 进程内缓存 24h"""
    return _pt_index_cache.get(_fetch_pt_index, refresh=refresh) or {}
