"""
多源数据采集模块 —— 移植自"股票信息"项目 em_data_sources.py

数据源:
1. fetch_full_spot()          全市场实时行情快照 (push2 clist 三镜像, 分页并发)
2. fetch_kline_history()      个股全量历史日K (腾讯 fqkline 主源 → akshare → 搜狐)
3. fetch_guba_rank_history()  个股股吧人气排名历史 (gbcdn 年文件, AES-CBC 解密, 滚动一年每日一条)
4. fetch_xuangu_rankings()    全市场人气排名快照 (data.eastmoney.com/dataapi/xuangu/list)
5. load_trade_dates / is_trade_day / previous_trade_day   A股交易日工具(akshare 新浪日历)
6. wait_for_internet()        网络连通性检测

网络约定: 统一复用 data/network.py 的 http_get / http_get_text
(Edge TLS 指纹、强制 IPv4、清空代理、自动携带东财 Cookie)。
"""

import base64
import hashlib
import json
import math
import re
import time

from .network import http_get, http_get_text, normalize_symbol, rotated
from ..core.parallel import run_parallel

# ==========================================
# 数据源 1: 全市场实时行情 (push2 clist)
# ==========================================

SPOT_HOSTS = [
    "https://push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://82.push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://73.push2.eastmoney.com/webguest/api/qt/clist/get",
]
SPOT_FIELDS = (
    "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,"
    "f20,f21,f23,f24,f25,f62,f115,f128,f140,f141,f136,f152"
)
SPOT_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

# f 字段 -> 英文字段名 (与 daily_stock_info / stock_daily_combined 列对齐)
SPOT_FIELD_MAP = {
    "f2": "latest_price", "f3": "change_pct",
    "f4": "change_amount", "f5": "volume",
    "f6": "amount", "f7": "amplitude",
    "f8": "turnover_rate", "f9": "pe_dynamic",
    "f10": "volume_ratio", "f12": "raw_code",
    "f14": "name", "f15": "high",
    "f16": "low", "f17": "open",
    "f18": "pre_close", "f20": "total_market_cap",
    "f21": "float_market_cap", "f23": "pb",
    "f24": "sixty_day_change", "f25": "ytd_change",
    "f62": "main_net_inflow", "f115": "pe_ttm",
    "f128": "sector_name", "f140": "speed",
    "f141": "five_min_change", "f136": "volume_ratio_5d",
    "f152": "amplitude_5d",
}

_SPOT_PAGE_SIZE = 100  # push2 clist 单页上限, pz>100 会被截断为 100
_SPOT_CONCURRENCY = 16


def _safe_float(val):
    if val is None or val in ("-", ""):
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    # NaN (如首日无昨收导致的派生值) 存为 NULL
    return f if f == f else None


def _safe_int(val):
    if val is None or val in ("-", ""):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _spot_page(host_url: str, page: int) -> dict | None:
    """push2 clist 单页(含多 host 内重试, 由 http_get 的 retries 兜底)"""
    params = {
        "fid": "f3", "po": "1", "pz": str(_SPOT_PAGE_SIZE), "pn": str(page),
        "np": "1", "fltt": "2", "invt": "2",
        "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
        "fs": SPOT_FS, "fields": SPOT_FIELDS,
    }
    return http_get(host_url, params=params, retries=2, timeout=30)


def fetch_full_spot(verbose: bool = False) -> list[dict]:
    """全市场 A 股实时行情快照 (含 PE/PB/市值/换手/主力净流入/所属板块)。

    三镜像依次尝试, 首页拿 total 后其余页并发。返回英文字段 dict 列表:
    symbol/name/raw_code + SPOT_FIELD_MAP 全部字段。
    """
    for host_url in rotated(SPOT_HOSTS):
        first = _spot_page(host_url, 1)
        if not first:
            continue
        data = first.get("data")
        if not data or not data.get("diff"):
            continue

        rows = list(data["diff"])
        total = data.get("total", 0) or len(rows)
        pages = math.ceil(total / _SPOT_PAGE_SIZE)
        if verbose:
            print(f"[spot] {host_url.split('/')[2]} total={total}, pages={pages}")

        if pages > 1:
            def _page_fn(p: int):
                return _spot_page(host_url, p)

            for _p, r in run_parallel(list(range(2, pages + 1)), _page_fn,
                                      workers=_SPOT_CONCURRENCY):
                if isinstance(r, Exception):
                    raise r
                d = (r or {}).get("data") or {}
                rows.extend(d.get("diff") or [])

        result = []
        for row in rows:
            item = {}
            for fkey, ename in SPOT_FIELD_MAP.items():
                val = row.get(fkey)
                if ename in ("name", "sector_name", "raw_code"):
                    item[ename] = str(val) if val not in (None, "-", "") else None
                else:
                    item[ename] = _safe_float(val)
            try:
                item["symbol"] = normalize_symbol(str(row.get("f12", "")))
            except ValueError:
                continue
            result.append(item)
        return result
    return []


# ==========================================
# 数据源 2: 个股全量历史日K (腾讯主源 → akshare → 搜狐)
# ==========================================

_FQT_MAP = {"qfq": "1", "hfq": "2", "": "0"}


def fetch_kline_history(symbol: str, adjust: str = "qfq", limit: int = None,
                        start_date: str = None, end_date: str = None) -> list[dict]:
    """个股历史日K (默认返回全部历史, 前复权; limit 可只取最近 N 根)。

    降级链: 腾讯 fqkline(快, 支持复权) → akshare 新浪 → 腾讯 → 搜狐 hisHq(不复权, 兜底)。
    东财 push2his 不作为主源 (IP 限速频繁, 腾讯链路可完全替代)。

    返回 dict 列表, 列名与 stock_kline 表一致 (时间升序):
    date, symbol, open, high, low, close, volume, amount,
    amplitude, change_pct, change_amount, turnover_rate
    """
    symbol = normalize_symbol(symbol)
    want = max(1, min(int(limit) if limit else 5000, 5000))
    start = (start_date or "19900101").replace("-", "")
    end = (end_date or "20500101").replace("-", "")

    # 1. 腾讯主源 (内部按 640 根/页自动翻页)
    from ..data.providers import tencent

    rows = tencent.fetch_stock_kline(
        symbol, limit=want, klt="101", adjust=adjust,
        start_date=start, end_date=end,
    )
    if rows:
        for row in rows:
            row.setdefault("source", "tencent")
            row.setdefault("adjust_type", adjust)
        return _clip_and_sort(rows, start, end, limit)

    # 2. akshare 新浪 (stock_zh_a_daily, 带复权)。仅请求最近 N 根时，
    # 不要把默认的 1990~2050 区间传给备用源；新浪会为每只股票分页抓取
    # 全历史，主源熔断后会把一次日更拖成数小时。
    fallback_start, fallback_end = start, end
    if limit and not start_date:
        from datetime import date as _date, datetime as _datetime, timedelta

        anchor = _date.today()
        if end_date:
            try:
                anchor = min(anchor, _datetime.strptime(end, "%Y%m%d").date())
            except ValueError:
                pass
        calendar_days = max(60, int(want * 2.2) + 30)
        fallback_start = (anchor - timedelta(days=calendar_days)).strftime("%Y%m%d")
        fallback_end = anchor.strftime("%Y%m%d")

    df = _akshare_daily(symbol, fallback_start, fallback_end, adjust)
    if df is not None and not df.empty:
        rows = _akshare_df_rows(df, symbol, fallback_start, fallback_end)
        if rows:
            for row in rows:
                row.setdefault("source", df.attrs.get("provider", "akshare"))
                row.setdefault("adjust_type", adjust)
            return _clip_and_sort(rows, start, end, limit)

    # 3. 搜狐兜底 (不复权, 仅有腾讯+akshare都不可用时才会走到)
    from ..data.providers import sohu

    if adjust:
        return []
    rows = sohu.fetch_stock_kline_daily(symbol, start_date=start, end_date=end)
    for row in rows:
        row.setdefault("source", "sohu")
        row["adjust_type"] = ""
    return _clip_and_sort(rows, start, end, limit)


def fetch_complete_history(symbol: str, adjust: str = "qfq") -> list[dict]:
    """Download each provider's whole history independently, never splice providers."""
    from datetime import date, timedelta
    from .providers import tencent, sohu
    from .quality import validate_prices
    symbol = normalize_symbol(symbol)
    from .quality import latest_completed_trade_day
    anchor = latest_completed_trade_day() or date.today().isoformat()
    pages = []
    end = anchor
    complete = False
    for _ in range(12):
        page = tencent.fetch_stock_kline(symbol, limit=5000, adjust=adjust,
                                        start_date="19900101", end_date=end)
        if not page:
            # Failure midway is incomplete, never accept the partial prefix.
            break
        page = sorted(page, key=lambda r: str(r["date"]))
        pages.extend(page)
        if len(page) < 5000 or page[0]["date"] <= "1990-12-01":
            complete = True
            break
        previous_end = (date.fromisoformat(page[0]["date"]) - timedelta(days=1)).isoformat()
        if previous_end >= end:
            break
        end = previous_end
    if complete:
        rows = sorted({r["date"]: r for r in pages}.values(), key=lambda r: r["date"])
        validate_prices(rows, adjust)
        return rows
    df = _akshare_daily(symbol, "19900101", anchor.replace("-", ""), adjust)
    if df is not None and not df.empty:
        rows = _akshare_df_rows(df, symbol, "19900101", anchor.replace("-", ""))
        rows = sorted({str(r["date"])[:10]: r for r in rows}.values(), key=lambda r: str(r["date"]))
        for r in rows:
            r.update(date=str(r["date"])[:10], source=df.attrs.get("provider", "akshare"), adjust_type=adjust)
        validate_prices(rows, adjust)
        return rows
    if not adjust:
        rows = sohu.fetch_stock_kline_daily(symbol, start_date="19900101", end_date=anchor)
        for r in rows:
            r.update(source="sohu", adjust_type="")
        validate_prices(rows, adjust)
        return rows
    return []


def _akshare_daily(symbol: str, start: str, end: str, adjust: str):
    """akshare 链路: 新浪 stock_zh_a_daily → 腾讯 stock_zh_a_hist_tx"""
    import akshare as ak

    prefixed = ("sh" if symbol.startswith("6") else
                "bj" if symbol.startswith(("8", "9", "4")) else "sz") + symbol

    df = None
    try:
        df = ak.stock_zh_a_daily(symbol=prefixed, start_date=start,
                                 end_date=end, adjust=adjust)
        df.attrs["provider"] = "sina"
    except Exception:
        pass
    if df is None or df.empty:
        try:
            df = ak.stock_zh_a_hist_tx(symbol=prefixed, start_date=start,
                                       end_date=end, adjust=adjust, timeout=15)
            df.attrs["provider"] = "tencent"
        except Exception:
            return None
    return df


def _akshare_df_rows(df, symbol: str, start: str, end: str) -> list[dict]:
    """akshare 日K DataFrame → 统一字段 dict 列表 (单位对齐东财: 量=手, 换手=%)"""
    import pandas as pd

    df = df.reset_index() if "date" not in df.columns else df
    df["date"] = df["date"].astype(str).str[:10]
    df = df.sort_values("date")

    pre_close = df["close"].shift(1)
    change_rate = (df["close"] / pre_close - 1) * 100
    change_amount = df["close"] - pre_close
    amplitude = (df["high"] - df["low"]) / pre_close * 100 if "high" in df.columns else None

    items = []
    for i, (_, row) in enumerate(df.iterrows()):
        # 新浪 volume 单位是股, 东财是手 (÷100 对齐); turnover 是小数, ×100 转百分比
        vol = _safe_float(row.get("volume"))
        vol = vol / 100 if vol is not None else None
        turnover = _safe_float(row.get("turnover"))
        turnover = turnover * 100 if turnover is not None else None
        items.append({
            "symbol": symbol,
            "date": row["date"],
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": vol,
            "amount": _safe_float(row.get("amount")),
            "amplitude": _safe_float(amplitude.iloc[i]) if amplitude is not None else None,
            "change_pct": _safe_float(change_rate.iloc[i]),
            "change_amount": _safe_float(change_amount.iloc[i]),
            "turnover_rate": turnover,
        })
    return items


def _clip_and_sort(rows: list[dict], start: str, end: str, limit: int) -> list[dict]:
    """按请求区间裁剪, 时间升序; limit 时只保留最近 N 根"""
    if not rows:
        return []
    start_iso = f"{start[:4]}-{start[4:6]}-{start[6:8]}" if len(start) == 8 else start
    end_iso = f"{end[:4]}-{end[4:6]}-{end[6:8]}" if len(end) == 8 else end
    clipped = [
        r for r in rows
        if start_iso <= str(r.get("date", ""))[:10] <= end_iso
    ]
    clipped.sort(key=lambda r: str(r.get("date", "")))
    if limit:
        clipped = clipped[-int(limit):]
    return clipped


# ==========================================
# 数据源 3: 股吧人气排名历史 (gbcdn 年文件, AES 解密)
# ==========================================

_GUBA_KEY_RAW = "getUtilsFromFile"
_GUBA_IV = b"getClassFromFile"


def _guba_format_code(code: str) -> str:
    """6 位数字 -> SH600000 / SZ000001 / BJ830799 格式"""
    code_str = str(code).upper().strip()
    code_clean = (
        code_str.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        .replace("SH", "").replace("SZ", "").replace("BJ", "")
    )
    if code_str.startswith(("SH", "SZ", "BJ")):
        prefix = code_str[:2]
    elif code_clean.startswith(("60", "68", "900")):
        prefix = "SH"
    elif code_clean.startswith(("00", "30", "200")):
        prefix = "SZ"
    elif code_clean.startswith(("8", "4")):
        prefix = "BJ"
    else:
        prefix = "SH"
    return f"{prefix}{code_clean}"


def _guba_decrypt(encrypted_b64_str: str) -> str:
    """gbcdn 年文件 AES-CBC 解密: key=md5(getUtilsFromFile), IV=getClassFromFile"""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    key = hashlib.md5(_GUBA_KEY_RAW.encode("utf-8")).hexdigest().encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, _GUBA_IV)
    encrypted_bytes = base64.b64decode(encrypted_b64_str)
    decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), 16)
    return decrypted_bytes.decode("utf-8")


def fetch_guba_rank_history(symbol: str) -> list[dict]:
    """个股股吧人气排名历史 (滚动一年, 每日一条收盘排名)。

    返回 dict 列表 (与 stock_popularity_rank 表列对齐):
    trade_date, symbol, rank, rank_time, rank_change, hour_rank_change,
    his_rank_change, his_rank_change_rank, hot_rank_score, market_all_count
    """
    full_code = _guba_format_code(symbol)
    url = f"https://gbcdn.dfcfw.com/rank/history/year/{full_code}.js"
    text = http_get_text(url, retries=2, timeout=15,
                         extra_headers={"Referer": "https://guba.eastmoney.com/"})
    if not text:
        return []

    match = re.search(r"var\s+\w+\s*=\s*[\"']([^\"']+)[\"']", text)
    if not match:
        return []
    raw_data = json.loads(_guba_decrypt(match.group(1)))
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("data", [])
    if not isinstance(raw_data, list):
        return []

    bare_symbol = normalize_symbol(full_code)
    items = []
    for rec in raw_data:
        calc_time = str(rec.get("CALCTIME") or "")
        rank = rec.get("RANK")
        if not calc_time or rank is None:
            continue
        rank_val = _safe_int(rank)
        if rank_val is None:
            continue
        items.append({
            "trade_date": calc_time[:10],
            "symbol": bare_symbol,
            "rank": rank_val,
            "rank_time": calc_time,
            "rank_change": _safe_int(rec.get("RANKCHANGE")),
            "hour_rank_change": _safe_int(rec.get("HOURRANKCHANGE")),
            "his_rank_change": _safe_int(rec.get("HISRANKCHANGE")),
            "his_rank_change_rank": _safe_int(rec.get("HISRANKCHANGE_RANK")),
            "hot_rank_score": _safe_float(rec.get("HOTRANKSCORE")),
            "market_all_count": _safe_int(rec.get("MARKETALLCOUNT")),
        })
    return items


# ==========================================
# 数据源 4: xuangu 人气排名快照
# ==========================================

XUANGU_URL = "https://data.eastmoney.com/dataapi/xuangu/list"
XUANGU_FIELDS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "NEW_PRICE",
    "CHANGE_RATE", "VOLUME_RATIO", "HIGH_PRICE", "LOW_PRICE",
    "PRE_CLOSE_PRICE", "VOLUME", "DEAL_AMOUNT", "TURNOVERRATE",
    "POPULARITY_RANK",
]


def fetch_xuangu_rankings(page_size: int = 500, max_pages: int = None,
                          verbose: bool = False) -> list[dict]:
    """全市场股吧人气排名快照 (分页拉全, 每行含 POPULARITY_RANK)。"""
    rows = []
    page = 1
    while True:
        params = {
            "st": "CHANGE_RATE",
            "sr": "-1",
            "ps": str(page_size),
            "p": str(page),
            "sty": ",".join(XUANGU_FIELDS),
            "filter": "(POPULARITY_RANK>=0.00)(POPULARITY_RANK<=6000)",
            "source": "SELECT_SECURITIES",
            "client": "WEB",
            "hyversion": "v2",
        }
        payload = http_get(XUANGU_URL, params=params, retries=3, timeout=30,
                           extra_headers={"Referer": "https://data.eastmoney.com/xuangu/"})
        if payload is None:
            raise RuntimeError(f"xuangu 接口请求失败 (page {page})")
        result = payload.get("result") or {}
        page_rows = result.get("data")
        if page_rows is None:
            page_rows = (payload.get("data") or {}).get("list")
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(page_rows)
        if verbose:
            print(f"[xuangu] page {page}: +{len(page_rows)} (total {len(rows)})", flush=True)
        if len(page_rows) < page_size:
            break
        page += 1
        if max_pages and page > max_pages:
            break
        time.sleep(0.25)
    return rows


# ==========================================
# 交易日 / 网络连通性工具
# ==========================================

_trade_dates_cache = None
_trade_dates_lock = __import__("threading").Lock()


def load_trade_dates() -> set:
    """加载 A 股交易日集合 (akshare 新浪交易日历, 带重试, 模块级缓存)。"""
    global _trade_dates_cache
    with _trade_dates_lock:
        if _trade_dates_cache is not None:
            return _trade_dates_cache
        import akshare as ak

        last_err = None
        for attempt in range(1, 4):
            try:
                trade_dates = ak.tool_trade_date_hist_sina()
                if "trade_date" not in trade_dates.columns:
                    raise RuntimeError("akshare trade date response does not contain trade_date")
                _trade_dates_cache = set(trade_dates["trade_date"].astype(str))
                from .quality import cache_trade_dates
                cache_trade_dates(_trade_dates_cache)
                return _trade_dates_cache
            except Exception as e:
                last_err = e
                print(f"trade date check failed (attempt {attempt}/3): {e}", file=__import__("sys").stderr, flush=True)
                time.sleep(2 * attempt)
        raise RuntimeError(f"failed to load trade dates: {last_err}")


def is_trade_day(today) -> bool:
    """判断某日是否 A 股交易日 (格式 date 或 'YYYY-MM-DD')。"""
    from datetime import date as _date

    if isinstance(today, _date):
        today_text = today.strftime("%Y-%m-%d")
    else:
        today_text = str(today)[:10]
    return today_text in load_trade_dates()


def previous_trade_day(today):
    """返回 today (不含) 之前最近的一个交易日, 'YYYY-MM-DD' 字符串。"""
    from datetime import date as _date, timedelta

    if isinstance(today, _date):
        d = today
    else:
        d = _date.fromisoformat(str(today)[:10])
    trade_dates = load_trade_dates()
    for _ in range(30):
        d -= timedelta(days=1)
        if d.strftime("%Y-%m-%d") in trade_dates:
            return d.strftime("%Y-%m-%d")
    raise RuntimeError(f"no trading day found within 30 days before {today}")


def wait_for_internet(timeout_seconds: int = 300) -> bool:
    """网络连通性检测 (curl_cffi, 解决唤醒后连接被拒问题)。"""
    start_time = time.time()
    url = "https://data.eastmoney.com"
    print("Waiting for internet connection...", flush=True)
    while time.time() - start_time < timeout_seconds:
        try:
            resp = http_get_text(url, timeout=5, retries=1)
            if resp is not None:
                elapsed = int(time.time() - start_time)
                print(f"Internet connection established after {elapsed}s.", flush=True)
                return True
        except Exception:
            pass
        elapsed = int(time.time() - start_time)
        print(f"Internet not ready yet ({elapsed}s elapsed, "
              f"{timeout_seconds - elapsed}s remaining). Retrying in 5s...", flush=True)
        time.sleep(5)
    print(f"Internet connection timed out after {timeout_seconds}s.", flush=True)
    return False
