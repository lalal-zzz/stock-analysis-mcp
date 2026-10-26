"""Public pre-open minute data and timestamped five-level observations.

These endpoints do not provide order-by-order entrust/cancellation history.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..core.config import get_settings
from .network import http_get, normalize_symbol, to_prefixed_symbol
from .providers.tdx_auction import fetch_auction_series

TZ = ZoneInfo("Asia/Shanghai")
BOOK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
TREND_URL = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
DETAIL_URL = "https://push2.eastmoney.com/api/qt/stock/details/get"
LIMITS = [
    "竞价明细为来源采样快照，不是逐笔委托；09:25前为虚拟匹配价量，不能逐条累加成成交量。",
    "匹配金额按价格×匹配股数计算并标明derived；分钟分时量额为另一口径，零值不代表没有匹配或申报。",
    "明细源未提供经核验的未匹配买卖量；完整历史盘口、逐笔委托和撤单尚未接入。",
]


def _now():
    return datetime.now(TZ)


def _identity(value):
    if not re.fullmatch(r"(?:(?:sh|sz|bj))?[03689]\d{5}", value, re.I):
        raise ValueError("仅支持沪深北A股六位代码或sh/sz/bj前缀")
    symbol = normalize_symbol(value)
    if len(value) > 6 and value.lower() != to_prefixed_symbol(symbol):
        raise ValueError("股票代码与市场前缀不一致")
    return symbol, f"{1 if symbol.startswith('6') else 0}.{symbol}"


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _stamp(value):
    value = _number(value)
    try:
        return datetime.fromtimestamp(value, TZ) if value and value > 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def _phase(clock):
    if "09:15:00" <= clock < "09:20:00":
        return "cancellable"
    if "09:20:00" <= clock < "09:25:00":
        return "non_cancellable"
    if clock == "09:25:00":
        return "opening_match_boundary"
    return "outside_opening_auction"


def _path():
    return get_settings().data_root / "information-cache" / "auction.sqlite3"


def _save(kind, symbol, day, stamp, payload):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS observations (kind TEXT, symbol TEXT, "
                     "day TEXT, stamp TEXT, payload TEXT, PRIMARY KEY(kind,symbol,day,stamp))")
        conn.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?)",
                     (kind, symbol, day, stamp, json.dumps(payload, ensure_ascii=False, allow_nan=False)))


def _read(kind, symbol, day):
    path = _path()
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        rows = conn.execute("SELECT payload FROM observations WHERE kind=? AND symbol=? "
                            "AND day=? ORDER BY stamp", (kind, symbol, day)).fetchall()
    return [json.loads(row[0]) for row in rows]


def get_stock_order_book(symbol: str) -> dict:
    """One fresh request; archive only verified observations in the auction window."""
    symbol, secid = _identity(symbol)
    raw = http_get(BOOK_URL, params={
        "secid": secid, "fltt": "2", "invt": "2",
        # f530 expands the bid/ask group. Individual field names alone are insufficient.
        "fields": "f57,f58,f86,f43,f60,f46,f47,f48,f170,f51,f52,f530",
    }, retries=1, timeout=10)
    observed = _now()
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or data.get("f57") != symbol:
        return {"status": "unavailable", "symbol": symbol, "warnings": ["盘口源不可用或代码不匹配"]}
    as_of = _stamp(data.get("f86"))
    warnings = [LIMITS[0], "集合竞价五档字段保留来源语义，不推算虚拟匹配量、未匹配量或撤单量。"]
    fresh = bool(as_of and 0 <= (observed - as_of).total_seconds() <= 60)
    phase = _phase(as_of.strftime("%H:%M:%S")) if as_of else "unknown"
    result = {
        "status": "ok", "symbol": symbol, "name": data.get("f58"),
        "source": "eastmoney", "source_url": BOOK_URL,
        "observed_at": observed.isoformat(), "source_as_of": as_of.isoformat() if as_of else None,
        "freshness_verified": fresh, "phase": phase,
        "units": {"price": "CNY", "volume": "shares", "amount": "CNY"},
        "latest_price": _number(data.get("f43")), "previous_close": _number(data.get("f60")),
        "open": _number(data.get("f46")), "change_pct": _number(data.get("f170")),
        "limit_up": _number(data.get("f51")), "limit_down": _number(data.get("f52")),
        "bids": [], "asks": [], "auction_indication": None, "archived": False, "warnings": warnings,
    }
    for side, fields in (("bids", [19, 17, 15, 13, 11]), ("asks", [39, 37, 35, 33, 31])):
        for level, field in enumerate(fields, 1):
            volume = _number(data.get(f"f{field + 1}"))
            result[side].append({"level": level, "price": _number(data.get(f"f{field}")),
                                 "volume": volume * 100 if volume is not None else None})
    has_book = any(r["price"] is not None and r["price"] > 0 for s in ("bids", "asks") for r in result[s])
    if not has_book:
        result["status"] = "unavailable"
        warnings.append("来源未返回有效五档价格，缺失值保留null")
    if phase in {"cancellable", "non_cancellable"}:
        # SSE L1 defines price1=virtual price, volume1=matched volume and
        # bid/ask volume2=unmatched volume. EM's forwarding is not live-verified:
        # expose this interpretation separately, never as confirmed provider fields.
        bid, ask = result["bids"][0], result["asks"][0]
        b2, a2 = result["bids"][1], result["asks"][1]
        if (symbol.startswith("6") and bid["price"] is not None and bid["price"] > 0
                and bid["price"] == ask["price"] and bid["volume"] is not None
                and bid["volume"] == ask["volume"]
                and all(r["price"] is None or r["price"] == 0 for r in (b2, a2))):
            buy, sell = b2["volume"], a2["volume"]
            if buy is not None and sell is not None and min(buy, sell) == 0:
                result["auction_indication"] = {
                    "reference_price": bid["price"], "matched_volume": bid["volume"],
                    "matched_amount": round(bid["price"] * bid["volume"], 2),
                    "unmatched_buy_volume": buy, "unmatched_sell_volume": sell,
                    "unmatched_direction": "buy" if buy > 0 else "sell" if sell > 0 else "balanced",
                    "interpretation": "sse_level1_layout_inference", "provider_mapping_verified": False,
                    "amount_kind": "derived_price_times_volume",
                }
                warnings.append("竞价语义按上交所L1字段布局解释，东财转发映射尚未经竞价时段实测确认，不作为已核验字段")
    if not fresh:
        warnings.append("来源行情时点缺失、未来或距获取时刻超过60秒，不作为当前竞价盘口留档")
    if fresh and has_book and phase not in {"unknown", "outside_opening_auction"}:
        result["archived"] = True
        try:
            _save("book", symbol, as_of.date().isoformat(), observed.isoformat(), result)
        except (OSError, sqlite3.Error) as exc:
            result["archived"] = False
            warnings.append(f"盘口留档失败: {exc}")
    return result


def _minutes(data, symbol, day):
    records = {}
    malformed = 0
    for row in data.get("trends") or []:
        try:
            values = row.split(",")
            stamp = datetime.fromisoformat(values[0]).replace(tzinfo=TZ)
            if len(values) != 8:
                raise ValueError("unexpected columns")
        except (ValueError, TypeError, AttributeError, IndexError):
            malformed += 1
            continue
        clock = stamp.strftime("%H:%M:%S")
        if stamp.date().isoformat() != day or not "09:15:00" <= clock <= "09:25:00":
            continue
        records[stamp.isoformat()] = {
            "time": stamp.isoformat(), "phase": _phase(clock),
            **dict(zip(("open", "price", "high", "low", "volume_lots", "amount", "average_price"),
                       map(_number, values[1:]))),
        }
    return [records[key] for key in sorted(records)], malformed


def _details(data, day):
    records = {}
    malformed = 0
    for raw in data.get("details") or []:
        try:
            clock, price, lots, count, flag = raw.split(",")
            if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", clock):
                raise ValueError("invalid clock")
            stamp = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=TZ)
            price, lots = _number(price), _number(lots)
            if price is None or price <= 0 or lots is None or lots < 0:
                raise ValueError("invalid price/volume")
        except (ValueError, TypeError, AttributeError):
            malformed += 1
            continue
        # The opening match can be timestamped a few seconds after 09:25 by
        # this vendor (e.g. 600000: 09:25:02). Do not drop it at 09:25:00.
        in_process = "09:15:00" <= clock <= "09:25:00"
        opening_record = "09:25:00" <= clock < "09:26:00" and flag in {"0", "1", "2"}
        if not in_process and not opening_record:
            continue
        if clock < "09:25:00" and flag != "4":
            malformed += 1
            continue
        virtual = clock < "09:25:00" or flag == "4"
        shares = lots * 100
        records[stamp.isoformat()] = {
            "time": stamp.isoformat(), "phase": _phase(clock) if virtual else "opening_match_boundary", "price": price,
            "volume": shares, "amount": round(price * shares, 2),
            "volume_kind": "virtual_matched" if virtual else "opening_executed",
            "amount_kind": "derived_price_times_volume", "source_flag": flag,
            "source_volume_lots": lots, "source_count_raw": count,
            "unmatched_buy_volume": None, "unmatched_sell_volume": None,
            "unmatched_direction": None,
        }
    return [records[k] for k in sorted(records)], malformed


def _get_eastmoney_auction(symbol: str, trade_date: str | None = None, offline: bool = False) -> dict:
    """Latest source day or archived day; never substitute today's data for a historical request."""
    symbol, secid = _identity(symbol)
    now = _now()
    day = date.fromisoformat(trade_date).isoformat() if trade_date else now.date().isoformat()
    if day > now.date().isoformat():
        raise ValueError("trade_date不能为未来日期")
    cached = _read("auction", symbol, day)
    packet = cached[-1] if cached else None
    warnings = list(LIMITS)
    origin = "archive" if packet else "unavailable"
    if not offline and day == now.date().isoformat():
        raw = http_get(TREND_URL, params={
            "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58", "ndays": "1", "iscr": "1", "iscca": "0",
        }, retries=1, timeout=10)
        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, dict) and data.get("code") == symbol and str(data.get("market")) == secid[0]:
            rows, malformed = _minutes(data, symbol, day)
            if malformed:
                warnings.append(f"跳过{malformed}条格式异常的来源记录")
            if rows:
                detail_raw = http_get(DETAIL_URL, params={
                    "secid": secid, "fields1": "f1,f2,f3,f4,f5",
                    "fields2": "f51,f52,f53,f54,f55", "pos": "0", "fltt": "2",
                }, retries=1, timeout=10)
                detail_data = detail_raw.get("data") if isinstance(detail_raw, dict) else None
                detail_rows = []
                # details has time-only rows. Require a separately timestamped
                # quote on the same day before attaching a calendar date.
                quote = get_stock_order_book(symbol)
                quote_day = (quote.get("source_as_of") or "")[:10]
                if (quote_day == day and isinstance(detail_data, dict) and detail_data.get("code") == symbol
                        and str(detail_data.get("market")) == secid[0]):
                    detail_rows, bad = _details(detail_data, day)
                    if bad:
                        warnings.append(f"跳过{bad}条格式或竞价标记异常的明细")
                elif quote_day != day:
                    warnings.append("明细不带日期且独立行情时点无法确认当日，拒绝为明细附加指定日期")
                if not detail_rows:
                    warnings.append("秒级竞价价量明细不可用，分钟分时不能替代虚拟匹配量")
                previous_rows = (packet or {}).get("records", [])
                merged = {r["time"]: r for r in previous_rows}
                merged.update({r["time"]: r for r in detail_rows})
                packet = {"records": [merged[k] for k in sorted(merged)], "minute_records": rows,
                          "previous_close": _number(data.get("preClose")), "name": data.get("name"),
                          "fetched_at": _now().isoformat(),
                          "detail_fetched_at": _now().isoformat() if detail_rows else (packet or {}).get("detail_fetched_at"),
                          "date_anchor_quote_as_of": quote.get("source_as_of") if detail_rows else (packet or {}).get("date_anchor_quote_as_of")}
                origin = "network" if detail_rows else ("archive" if previous_rows else "minutes_only")
                try:
                    _save("auction", symbol, day, "latest", packet)
                except (OSError, sqlite3.Error) as exc:
                    warnings.append(f"竞价分时留档失败: {exc}")
            else:
                warnings.append("来源没有指定日期09:15–09:25记录，不以其他日期或连续竞价记录替代")
        else:
            warnings.append("竞价分时源不可用或标的身份不匹配")
    rows = packet["records"] if packet else []
    books = _read("book", symbol, day)
    present = {r["time"][11:16] for r in rows}
    expected = [f"09:{minute:02d}" for minute in range(15, 26)]
    missing = [t for t in expected if t not in present]
    if origin == "archive":
        warnings.append("返回本地历史观测；不是当前实时数据")
    if missing:
        warnings.append("竞价分钟覆盖不完整；缺失记录不插值、不补零")
    if not books:
        warnings.append("未采集该日竞价五档快照，盘后无法重建历史盘口；需竞价期间调用get_stock_order_book留档")
    opening = next((r for r in reversed(rows) if r["volume_kind"] == "opening_executed"), None)
    latest_virtual = next((r for r in reversed(rows) if r["volume_kind"] == "virtual_matched"), None)
    if rows:
        warnings.append("明细行只有时分秒，日期依据同次获取的当日分时及独立行情时间推定，不支持远端指定历史日回放")
    return {
        "status": "ok" if rows or books else "unavailable", "symbol": symbol, "trade_date": day,
        "source": "eastmoney", "source_url": DETAIL_URL, "minute_source_url": TREND_URL, "origin": origin,
        "fetched_at": packet["fetched_at"] if packet else None,
        "observed_at": _now().isoformat(), "name": packet.get("name") if packet else None,
        "previous_close": packet.get("previous_close") if packet else None,
        "detail_fetched_at": packet.get("detail_fetched_at") if packet else None,
        "date_anchor_quote_as_of": packet.get("date_anchor_quote_as_of") if packet else None,
        "granularity": "source_sampled_seconds", "units": {"price": "CNY", "volume": "shares", "amount": "CNY"},
        "records": rows, "minute_records": packet.get("minute_records", []) if packet else [],
        "latest_virtual_match": latest_virtual, "opening_match": opening,
        "order_book_observations": books,
        "coverage": {"expected_minutes": 11, "returned_minutes": len(present),
                     "missing_minutes": missing, "minute_grid_complete": not missing,
                     "sample_count": len(rows), "unmatched_volume_available": False,
                     "full_order_history_available": False, "book_observation_count": len(books)},
        "warnings": warnings,
    }


def get_stock_auction(symbol: str, trade_date: str | None = None, offline: bool = False) -> dict:
    """Prefer explicit auction-process snapshots; keep executions as separate evidence."""
    symbol, _ = _identity(symbol)
    day = date.fromisoformat(trade_date).isoformat() if trade_date else _now().date().isoformat()
    if day > _now().date().isoformat():
        raise ValueError("trade_date不能为未来日期")
    cached = _read("process", symbol, day)
    process = None if offline else fetch_auction_series(symbol, day)
    if process and process["status"] == "ok":
        result = _get_eastmoney_auction(symbol, day, offline=False)
        # Source series stays separate from Eastmoney's opening execution.
        rows = process["records"]
        present = {r["time"][11:16] for r in rows}
        missing = [f"09:{m:02d}" for m in range(15, 26) if f"09:{m:02d}" not in present]
        result.update(status="ok", source=process["source"], source_url=process["source_url"],
                      source_host=process["source_host"], date_validation=process["date_validation"],
                      records=rows, latest_virtual_match=rows[-1], origin="network",
                      fetched_at=_now().isoformat(), detail_fetched_at=_now().isoformat())
        result["coverage"].update(returned_minutes=len(present), missing_minutes=missing,
                                  minute_grid_complete=not missing, sample_count=len(rows),
                                  unmatched_volume_available=True)
        result["precision"] = _precision(rows)
        result["warnings"] = [
            "竞价过程为来源采样快照，不是逐秒全量委托；匹配量不能逐条累加。",
            "正未匹配量为买方剩余，负值为卖方剩余；不是全部价格档位的挂单总量。",
            "金额按价格×股数计算；09:25过程快照仍属虚拟匹配，正式成交单独见opening_match。",
            "协议按指定日期请求但响应不回显日期/代码；历史覆盖以主站实际保存为准。",
        ]
        if missing:
            result["warnings"].append("分钟网格存在缺口，不插值、不补零；最后一条可能止于09:24:57或09:24:59")
        if result["opening_match"] is None:
            result["warnings"].append("未获得独立的09:25正式撮合记录，不以虚拟快照代替")
        try:
            _save("process", symbol, day, "latest", result)
        except (OSError, sqlite3.Error) as exc:
            result["warnings"].append(f"竞价过程留档失败: {exc}")
        return result
    if cached:
        result = cached[-1]
        result["origin"] = "archive"
        result["observed_at"] = _now().isoformat()
        result.setdefault("precision", _precision(result["records"]))
        result["warnings"].append("返回本地已存竞价过程；不是当前实时更新")
        return result
    result = _get_eastmoney_auction(symbol, day, offline)
    result["precision"] = _precision(result["records"])
    if not offline:
        result["warnings"].append("专用竞价过程源不可用或该日无记录；当前降级数据缺少未匹配量，无法完整画出买卖失衡变化")
    return result


def _precision(rows: list[dict]) -> dict:
    """Report observed granularity, rather than promising per-second/order coverage."""
    import statistics

    times = sorted({datetime.fromisoformat(r["time"]) for r in rows})
    intervals = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    return {
        "data_kind": "auction_process_snapshots", "order_by_order_available": False,
        "timestamp_resolution_seconds": 1, "expected_seconds_inclusive": 601,
        "observed_seconds": len(times), "every_second_available": len(times) == 601,
        "first_record_at": times[0].isoformat() if times else None,
        "last_record_at": times[-1].isoformat() if times else None,
        "min_interval_seconds": min(intervals) if intervals else None,
        "median_interval_seconds": statistics.median(intervals) if intervals else None,
        "max_interval_seconds": max(intervals) if intervals else None,
        "missing_seconds_count": max(0, 601 - len(times)),
        "exchange_to_client_latency_verified": False,
        "note": "时间戳精确到秒不等于每秒全量；缺口不补造。逐笔申报/撤单流不可用。",
    }
