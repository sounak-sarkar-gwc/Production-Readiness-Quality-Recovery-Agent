"""Root-Cause Evidence Check (MVP specialist).

What changed on/near the machine shortly before the defect trend started,
and does history show a similar defect linked to a similar cause?
"""

from datetime import datetime, timedelta

from .. import data_loader as dl

LOOKBACK_MINUTES = 60


def check_root_cause(machine_id: str, order_id: str = None, since_timestamp: str = None) -> dict:
    order = dl.order_by_id(order_id) if order_id else dl.current_order_for_machine(machine_id)
    since_dt = datetime.fromisoformat(since_timestamp) if since_timestamp else None
    window_start = (since_dt - timedelta(minutes=LOOKBACK_MINUTES)) if since_dt else None

    mach_events = dl.machine_events[dl.machine_events["machine_id"] == machine_id].sort_values("timestamp")
    if since_dt is not None:
        mach_events = mach_events[
            (mach_events["timestamp"] >= window_start) & (mach_events["timestamp"] <= since_dt)
        ]
    recent_events = [
        {"timestamp": str(r["timestamp"]), "event_type": r["event_type"], "details": r["details"]}
        for _, r in mach_events.tail(10).iterrows()
    ]

    tool_id = order.get("tool_id") if order else None
    hist = dl.historical_incidents[dl.historical_incidents["machine_id"] == machine_id]
    hist_by_tool = hist[hist["tool_id"] == tool_id] if tool_id else hist.iloc[0:0]

    historical_matches = [
        {
            "incident_id": r["incident_id"],
            "tool_id": r["tool_id"],
            "defect_type": r["defect_type"],
            "root_cause": r["root_cause"],
            "recovery_action_id": r["recovery_action_id"],
            "success": bool(r["success"]),
            "date": str(r["date"]),
        }
        for _, r in hist.iterrows()
    ]

    likely_cause = None
    confidence = "LOW"
    tool_change_event = next(
        (e for e in recent_events if e["event_type"] in ("TOOL_INSTALLED", "TOOL_REMOVED")), None
    )
    if tool_change_event and not hist_by_tool.empty:
        # Same machine, different tool than the one implicated historically --
        # read this as "a new tool was just swapped in and defects match a
        # known tool-related failure mode on this machine", not "this exact
        # tool is the historically bad one".
        likely_cause = (
            f"Tool change immediately preceded the defect trend ({tool_change_event['details']}). "
            f"This machine has {len(hist_by_tool)} historical incident(s) on a different tool with the "
            f"same failure pattern ({hist_by_tool.iloc[0]['defect_type']} / {hist_by_tool.iloc[0]['root_cause']})."
        )
        confidence = "MEDIUM"
    elif tool_change_event:
        likely_cause = f"Tool change immediately preceded the defect trend: {tool_change_event['details']}."
        confidence = "MEDIUM"
    elif not hist.empty:
        likely_cause = f"No recent machine event stands out; closest historical match: {hist.iloc[0]['root_cause']}."
        confidence = "LOW"
    else:
        likely_cause = "No correlating machine event or historical incident found."
        confidence = "LOW"

    return {
        "machine_id": machine_id,
        "order_id": order.get("order_id") if order else order_id,
        "lookback_window_minutes": LOOKBACK_MINUTES,
        "recent_machine_events": recent_events,
        "historical_matches": historical_matches,
        "likely_cause": likely_cause,
        "confidence": confidence,
        "reason": likely_cause,
    }
