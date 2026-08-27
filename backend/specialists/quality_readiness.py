"""Quality Readiness Check (MVP specialist).

Are inspection prerequisites -- calibration on record, and a tool actually
mounted and in use -- ready before the order starts?
"""

from .. import data_loader as dl


def check_quality_readiness(machine_id: str, order_id: str = None) -> dict:
    order = dl.order_by_id(order_id) if order_id else dl.current_order_for_machine(machine_id)
    if order is None:
        return {"status": "RED", "reason": f"No production order found for {machine_id}."}

    tool_id = order.get("tool_id")
    tool_rows = dl.tooling[dl.tooling["tool_id"] == tool_id]
    tool_ok = not tool_rows.empty and tool_rows.iloc[0]["status"] == "IN_USE"

    cal_events = dl.machine_events[
        (dl.machine_events["machine_id"] == machine_id)
        & (dl.machine_events["event_type"] == "CALIBRATION")
    ].sort_values("timestamp")
    last_cal = cal_events.iloc[-1] if not cal_events.empty else None

    issues = []
    if tool_rows.empty:
        issues.append(f"Tool {tool_id} is not in the tooling master.")
    elif not tool_ok:
        issues.append(f"Tool {tool_id} is on record as {tool_rows.iloc[0]['status']}, not IN_USE.")
    if last_cal is None:
        issues.append(f"No calibration event on record for {machine_id}.")

    return {
        "status": "GREEN" if not issues else "YELLOW",
        "order_id": order["order_id"],
        "tool_id": tool_id,
        "tool_status": None if tool_rows.empty else tool_rows.iloc[0]["status"],
        "tool_wear_level": None if tool_rows.empty else tool_rows.iloc[0]["wear_level"],
        "last_calibration": None if last_cal is None else str(last_cal["timestamp"]),
        "reason": "; ".join(issues) if issues else "Tooling in use and calibration on record.",
    }
