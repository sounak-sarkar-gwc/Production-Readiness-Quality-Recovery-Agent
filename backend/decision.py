"""Recovery option generation -- the core agent's Decision responsibility.

Deterministic, not an LLM call: ranks the recovery actions historically used
for a similar defect type by success rate and resolution time. The LLM is
used for narrative reasoning (investigation, recommendation write-up), not
for this comparison -- the underlying numbers should be reproducible.
"""

from . import data_loader as dl
from .specialists.waste_energy import estimate_waste_energy

ACTION_LABELS = {
    "ADJUST_PARAM": "Adjust process parameters",
    "REPLACE_TOOL": "Replace tool",
    "MATERIAL_SWAP": "Swap material batch",
    "RECALIBRATE_MACHINE": "Recalibrate machine",
}


def generate_recovery_options(defect_type: str, include_waste_energy: bool = False) -> list:
    matches = dl.historical_incidents[dl.historical_incidents["defect_type"] == defect_type]
    if matches.empty:
        return [{
            "action_id": "MANUAL_INSPECTION",
            "label": "Manual inspection / hold line",
            "historical_occurrences": 0,
            "recommended": True,
            "reason": f"No historical recovery action on record for defect type {defect_type}; defaulting to manual hold.",
        }]

    grouped = {}
    for _, r in matches.iterrows():
        action_id = r["recovery_action_id"]
        g = grouped.setdefault(action_id, {"successes": 0, "total": 0, "resolution_minutes": []})
        g["total"] += 1
        g["successes"] += 1 if r["success"] else 0
        g["resolution_minutes"].append(r["resolution_time_min"])

    options = []
    for action_id, g in grouped.items():
        success_rate = g["successes"] / g["total"]
        avg_resolution = sum(g["resolution_minutes"]) / len(g["resolution_minutes"])
        option = {
            "action_id": action_id,
            "label": ACTION_LABELS.get(action_id, action_id),
            "historical_occurrences": g["total"],
            "historical_success_rate": round(success_rate, 2),
            "avg_resolution_minutes": round(avg_resolution, 1),
        }
        if include_waste_energy:
            option["waste_energy"] = estimate_waste_energy(action_id)
        options.append(option)

    options.sort(key=lambda o: (-o["historical_success_rate"], o["avg_resolution_minutes"]))
    for i, o in enumerate(options):
        o["recommended"] = i == 0
    return options
