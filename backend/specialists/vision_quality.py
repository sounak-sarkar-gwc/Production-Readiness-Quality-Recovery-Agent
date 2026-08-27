"""Vision Quality Check (MVP specialist, simulated).

Reads the staged quality_events.csv stream (POC stand-in for live camera
inference) plus any events ingested at runtime via POST /prq/quality/events,
and reports whether the defect rate is trending up.

Threshold policy (POC default, mirrors the doc's worked example): flag a
rising trend once the rolling defect rate exceeds 3x the order's earliest
reading, AND is at or above 3.0% absolute.
"""

from .. import data_loader as dl
from ..data_loader import sanitize

RISING_MULTIPLE = 3.0
RISING_FLOOR_PCT = 3.0

_runtime_events = []


def ingest_quality_event(event: dict) -> None:
    """Appends a runtime-reported quality reading (e.g. a live inference
    result) on top of the CSV baseline for this process's lifetime."""
    _runtime_events.append(event)


def _events_for(machine_id: str, order_id: str = None):
    rows = dl.quality_events[dl.quality_events["machine_id"] == machine_id]
    if order_id:
        rows = rows[rows["order_id"] == order_id]
    records = rows.sort_values("timestamp").to_dict("records")
    extra = [
        e for e in _runtime_events
        if e.get("machine_id") == machine_id and (not order_id or e.get("order_id") == order_id)
    ]
    return sorted(records + extra, key=lambda e: str(e["timestamp"]))


def check_vision_quality(machine_id: str, order_id: str = None) -> dict:
    events = _events_for(machine_id, order_id)
    if not events:
        return {"status": "GREEN", "reason": "No quality readings on record yet.", "trend": []}

    trend = [
        {
            "timestamp": str(e["timestamp"]),
            "defect_rate": float(e["defect_rate"]),
            "defect_type": sanitize(e.get("defect_type")),
            "units_inspected": int(e["units_inspected"]),
            "units_defective": int(e["units_defective"]),
        }
        for e in events
    ]

    baseline = trend[0]["defect_rate"] or 0.001
    latest = trend[-1]["defect_rate"]
    rising = latest >= RISING_FLOOR_PCT / 100 and latest >= RISING_MULTIPLE * baseline

    return {
        "status": "RED" if rising else "GREEN",
        "order_id": order_id or events[-1].get("order_id"),
        "baseline_defect_rate": baseline,
        "latest_defect_rate": latest,
        "trend": trend,
        "rising": rising,
        "reason": (
            f"Defect rate rose from {baseline:.1%} to {latest:.1%} "
            f"(>= {RISING_MULTIPLE:g}x baseline and >= {RISING_FLOOR_PCT:g}% absolute)."
            if rising else
            f"Defect rate at {latest:.1%}, within normal range of baseline {baseline:.1%}."
        ),
    }
