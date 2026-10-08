from __future__ import annotations

import hashlib

from .models import AlertEvent, AlertState, PriceUpdate, ZoneRule


def _relation(rule: ZoneRule, price: float) -> str:
    if price < rule.lower:
        return "below"
    if price > rule.upper:
        return "above"
    return "inside"


def _event(rule: ZoneRule, state: AlertState, update: PriceUpdate,
           event_type: str, confirmed: bool) -> AlertEvent:
    raw = f"{rule.zone_id}:{state.episode}:{event_type}"
    return AlertEvent(
        idempotency_key=hashlib.sha1(raw.encode()).hexdigest(),
        zone_id=rule.zone_id, symbol=rule.symbol, timeframe=rule.timeframe,
        episode=state.episode, event_type=event_type, timestamp=update.timestamp,
        close=float(update.close), lower=rule.lower, upper=rule.upper,
        confirmed=confirmed,
    )


def evaluate_zone(rule: ZoneRule, state: AlertState | None,
                  update: PriceUpdate) -> tuple[AlertState, list[AlertEvent]]:
    """Evaluate one update with per-episode dedupe and hysteresis reset.

    Intrabar crossings are temporary. Breakout/breakdown require a closed bar.
    """
    state = state or AlertState(zone_id=rule.zone_id)
    if state.zone_id != rule.zone_id:
        raise ValueError("alert state belongs to another zone")
    events = []
    if rule.invalidated and not state.invalidated:
        state.invalidated = True
        if "invalidated" not in state.emitted:
            events.append(_event(rule, state, update, "invalidated", True))
            state.emitted.add("invalidated")
        state.last_timestamp = update.timestamp
        return state, events
    if state.invalidated:
        return state, events

    previous = state.relation
    relation = _relation(rule, update.close)
    high = update.close if update.high is None else update.high
    low = update.close if update.low is None else update.low
    touched = high >= rule.lower and low <= rule.upper
    distance = (rule.lower - update.close if update.close < rule.lower
                else update.close - rule.upper if update.close > rule.upper else 0.0)
    near = distance / max(update.close, 1e-12) <= rule.approach_pct

    if relation != "inside" and near and "approaching" not in state.emitted:
        events.append(_event(rule, state, update, "approaching", False))
        state.emitted.add("approaching")
    if touched and "touched" not in state.emitted:
        events.append(_event(rule, state, update, "touched", False))
        state.emitted.add("touched")

    above_break = update.close > rule.upper * (1 + rule.break_buffer_pct)
    below_break = update.close < rule.lower * (1 - rule.break_buffer_pct)
    crossed_before = "crossed_intrabar" in state.emitted
    if update.closed and above_break and (previous in {"inside", "below", "unknown"} or crossed_before):
        if "breakout" not in state.emitted:
            events.append(_event(rule, state, update, "breakout", True))
            state.emitted.add("breakout")
    elif update.closed and below_break and (previous in {"inside", "above", "unknown"} or crossed_before):
        if "breakdown" not in state.emitted:
            events.append(_event(rule, state, update, "breakdown", True))
            state.emitted.add("breakdown")
    elif not update.closed and ((previous != "above" and above_break)
                                or (previous != "below" and below_break)):
        if "crossed_intrabar" not in state.emitted:
            events.append(_event(rule, state, update, "crossed_intrabar", False))
            state.emitted.add("crossed_intrabar")

    reset_far = (update.close > rule.upper * (1 + rule.reset_pct)
                 or update.close < rule.lower * (1 - rule.reset_pct))
    if reset_far and previous == relation and previous in {"above", "below"} and not events and state.emitted:
        state.episode += 1
        state.emitted.clear()
    state.relation = relation
    state.last_timestamp = update.timestamp
    return state, events
