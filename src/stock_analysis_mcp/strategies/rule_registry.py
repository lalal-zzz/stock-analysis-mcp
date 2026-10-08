"""Explicit, reviewed rule versions; proposal creation never activates rules."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


def validate_filters(filters):
    from .patterns.constants import PATTERN_NAMES
    if not isinstance(filters, dict) or not filters:
        raise ValueError("nonempty pattern filters required")
    for pattern, conditions in filters.items():
        if pattern not in PATTERN_NAMES or not isinstance(conditions, dict):
            raise ValueError("unknown pattern or invalid conditions")
        for key, value in conditions.items():
            if key.endswith("_in"):
                if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
                    raise ValueError("categorical conditions require a nonempty string list")
            else:
                import math
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise ValueError("numeric conditions require [lower, upper]")
                if any(v is not None and (not isinstance(v, (int,float)) or not math.isfinite(v)) for v in value):
                    raise ValueError("bounds must be finite")
                if value[0] is not None and value[1] is not None and value[0] > value[1]:
                    raise ValueError("lower bound exceeds upper bound")


def read_registry(path: str | Path) -> dict:
    file = Path(path)
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"versions":[],"events":[]}


def write_registry(path, registry):
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(file)


def propose(path, filters, *, evidence: str) -> str:
    validate_filters(filters)
    if not evidence:
        raise ValueError("out-of-sample evidence reference required")
    version = hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]
    registry = read_registry(path)
    if not any(r["id"] == version for r in registry["versions"]):
        registry["versions"].append({"id":version,"filters":filters,"evidence":evidence,
                                     "proposed_at":datetime.now().isoformat()})
    write_registry(path, registry)
    return version


def activate(path, version, *, approved_by: str, effective_date: str, rollback=False):
    if not approved_by.strip():
        raise ValueError("explicit human approval attribution required")
    datetime.strptime(effective_date, "%Y-%m-%d")
    registry = read_registry(path)
    if not any(r["id"] == version for r in registry["versions"]):
        raise ValueError("unknown version")
    registry["events"].append({"version":version,"approved_by":approved_by,
        "effective_date":effective_date,"recorded_at":datetime.now().isoformat(),
        "action":"rollback" if rollback else "activate"})
    write_registry(path, registry)


def active_filters(as_of: str) -> dict | None:
    path = os.environ.get("STOCK_ANALYSIS_RULE_REGISTRY")
    if not path:
        return None
    registry = read_registry(path)
    effective = [e for e in registry["events"] if e["effective_date"] <= as_of]
    if not effective:
        return None
    event = max(enumerate(effective), key=lambda pair:(pair[1]["effective_date"], pair[0]))[1]
    filters = next(v["filters"] for v in registry["versions"] if v["id"] == event["version"])
    validate_filters(filters)
    return filters
