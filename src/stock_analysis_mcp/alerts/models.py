from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ZoneRule:
    zone_id: str
    symbol: str
    timeframe: str
    lower: float
    upper: float
    approach_pct: float = 0.01
    break_buffer_pct: float = 0.002
    reset_pct: float = 0.015
    invalidated: bool = False

    def __post_init__(self):
        import math
        if not all(math.isfinite(x) for x in (self.lower, self.upper, self.approach_pct,
                                              self.break_buffer_pct, self.reset_pct)):
            raise ValueError("zone values must be finite")
        if not self.zone_id or not self.symbol or self.timeframe not in {"1","5","15","30","60","101","102","103","daily","weekly","monthly"}:
            raise ValueError("zone id, symbol and supported timeframe required")
        if min(self.approach_pct, self.break_buffer_pct, self.reset_pct) < 0:
            raise ValueError("zone buffers must be nonnegative")
        if self.lower > self.upper:
            raise ValueError("zone lower cannot exceed upper")
        if self.lower <= 0:
            raise ValueError("zone prices must be positive")


@dataclass(frozen=True)
class PriceUpdate:
    timestamp: str
    close: float
    high: float | None = None
    low: float | None = None
    closed: bool = False

    def __post_init__(self):
        import math
        if not self.timestamp or not math.isfinite(self.close) or self.close <= 0:
            raise ValueError("valid timestamp and positive finite raw close required")
        if any(v is not None and not math.isfinite(v) for v in (self.high, self.low)):
            raise ValueError("high and low must be finite")
        if self.high is not None and self.low is not None and self.high < self.low:
            raise ValueError("high cannot be below low")


@dataclass
class AlertState:
    zone_id: str
    episode: int = 1
    relation: str = "unknown"
    emitted: set[str] = field(default_factory=set)
    invalidated: bool = False
    last_timestamp: str | None = None


@dataclass(frozen=True)
class AlertEvent:
    idempotency_key: str
    zone_id: str
    symbol: str
    timeframe: str
    episode: int
    event_type: str
    timestamp: str
    close: float
    lower: float
    upper: float
    confirmed: bool
