"""TDX 0x056a auction snapshots, not executions or individual orders.

Protocol layout: injoyai/tdx (MIT), docs/third-party-notices.md.
"""
from __future__ import annotations

import math
import struct
from datetime import datetime
from zoneinfo import ZoneInfo

from ..network import tdx_request, provider_available, mark_provider_ok, mark_provider_fail

HOSTS = ("62.234.50.143", "124.71.9.153")
PROVIDER = "tdx_auction"
REFERENCE = "https://github.com/injoyai/tdx/blob/master/protocol/model_call_auction.go"
TZ = ZoneInfo("Asia/Shanghai")


def decode_series(payload: bytes, day: str) -> list[dict]:
    """One uint16 count followed by count 16-byte little-endian records.

    Unmatched is signed int32, not int16 (large queues otherwise flip direction).
    Volume is lots; normalized output uses shares. No resampling or padding.
    """
    if len(payload) < 2:
        raise ValueError("TDX auction response missing count")
    count = struct.unpack_from("<H", payload)[0]
    if count > 1000 or len(payload) != 2 + 16 * count:
        raise ValueError("TDX auction response count/length mismatch")
    result = []
    previous = -1
    for index in range(count):
        minute, price, matched, unmatched, reserved, second = struct.unpack_from("<HfIiBB", payload, 2 + 16 * index)
        if minute >= 1440 or second >= 60 or not math.isfinite(price) or price < 0:
            raise ValueError("invalid TDX auction record")
        seconds = minute * 60 + second
        if seconds < previous:
            raise ValueError("TDX auction records not ordered")
        previous = seconds
        if not 9 * 3600 + 15 * 60 <= seconds <= 9 * 3600 + 25 * 60:
            continue
        clock = f"{minute // 60:02d}:{minute % 60:02d}:{second:02d}"
        price = round(price, 3)
        volume = matched * 100
        signed = unmatched * 100
        direction = "buy" if signed > 0 else "sell" if signed < 0 else "balanced"
        result.append({
            "time": datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=TZ).isoformat(),
            "price": price if price > 0 else None,
            "volume": volume, "matched_volume": volume,
            "amount": round(price * volume, 2) if price > 0 else None,
            "amount_kind": "derived_price_times_volume", "volume_kind": "virtual_matched",
            "unmatched_buy_volume": max(signed, 0), "unmatched_sell_volume": max(-signed, 0),
            "unmatched_signed_volume": signed, "unmatched_direction": direction,
            "unmatched_amount": round(price * abs(signed), 2) if price > 0 else None,
            "source_volume_lots": matched, "source_unmatched_signed_lots": unmatched,
            "source_index": index,
            "phase": "cancellable" if clock < "09:20:00" else
                     "non_cancellable" if clock < "09:25:00" else "opening_match_boundary",
        })
    return result


def fetch_auction_series(symbol: str, day: str) -> dict:
    if not provider_available(PROVIDER):
        return {"status": "unavailable", "reason": "provider_cooldown"}
    market = 1 if symbol.startswith("6") else 2 if symbol.startswith(("8", "9")) else 0
    failures = []
    had_response = False
    for host in HOSTS:
        try:
            payload = tdx_request(host, market, symbol, int(day.replace("-", "")), timeout=4)
            rows = decode_series(payload, day)
            had_response = True
            if rows:
                mark_provider_ok(PROVIDER)
                return {"status": "ok", "records": rows, "source_host": host,
                        "source_url": REFERENCE, "source": PROVIDER, "requested_date": day,
                        "date_validation": "explicit_request_date_not_echoed_by_protocol"}
        except (OSError, ValueError, struct.error) as exc:
            failures.append(f"{host}: {exc}")
    if not had_response:
        mark_provider_fail(PROVIDER)
    return {"status": "unavailable", "reason": "empty_or_failed", "failures": failures}
