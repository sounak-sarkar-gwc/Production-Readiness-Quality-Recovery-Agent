"""Impact Check (MVP specialist).

Given a machine/order and the timestamp a defect trend started rising, how
many units are potentially affected, and what's the batch/WIP/downstream
exposure?
"""

from datetime import datetime

from .. import data_loader as dl
from .vision_quality import _events_for


def check_impact(machine_id: str, order_id: str, since_timestamp: str = None) -> dict:
    order = dl.order_by_id(order_id)
    if order is None:
        return {"status": "UNKNOWN", "reason": f"Order {order_id} not found."}

    events = _events_for(machine_id, order_id)
    if since_timestamp is None:
        # Default: first reading where the defect rate is non-zero.
        onset = next((e for e in events if float(e["defect_rate"]) > 0), None)
        since_timestamp = str(onset["timestamp"]) if onset else None

    since_dt = datetime.fromisoformat(since_timestamp) if since_timestamp else None

    affected_units = 0
    units_inspected_since = 0
    units_defective_since = 0
    for e in events:
        ts = e["timestamp"]
        ts_dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
        if since_dt is None or ts_dt >= since_dt:
            units_inspected_since += int(e["units_inspected"])
            units_defective_since += int(e["units_defective"])

    quantity = int(order.get("quantity") or 0)
    # Potentially affected = everything produced on this order from onset
    # through to the planned end, not just what's been inspected so far --
    # inspection is a sample, not every unit.
    if units_inspected_since > 0:
        sample_defect_rate = units_defective_since / units_inspected_since
    else:
        sample_defect_rate = 0.0
    affected_units = round(quantity * min(1.0, max(sample_defect_rate, units_inspected_since / max(quantity, 1))))

    return {
        "order_id": order_id,
        "machine_id": machine_id,
        "since_timestamp": since_timestamp,
        "order_quantity": quantity,
        "units_inspected_since_onset": units_inspected_since,
        "units_defective_since_onset": units_defective_since,
        "sample_defect_rate_since_onset": round(sample_defect_rate, 4),
        "potentially_affected_units": affected_units,
        "material_batch": order.get("material_batch"),
        "reason": (
            f"Since {since_timestamp}, {units_defective_since}/{units_inspected_since} inspected units "
            f"were defective ({sample_defect_rate:.1%}); extrapolated across the {quantity}-unit order that "
            f"is ~{affected_units} potentially affected units."
        ),
    }
