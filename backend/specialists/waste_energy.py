"""Waste/Energy Impact Check (stretch specialist).

Backed by waste_energy_log.csv -- per-historical-occurrence scrap/rework
units, material waste, and energy draw for each recovery action actually
taken. The per-row figures themselves are derived from a documented
placeholder methodology (see the log's generation notes), not measured
plant telemetry, but the aggregation here -- averaging real historical rows
per action_id -- is real.
"""

from .. import data_loader as dl


def estimate_waste_energy(action_id: str) -> dict:
    matches = dl.waste_energy_log[dl.waste_energy_log["action_id"] == action_id]
    if matches.empty:
        return {
            "action_id": action_id,
            "reason": "No historical waste/energy record for this action_id.",
        }

    n = len(matches)
    avg_downtime_min = round(matches["downtime_minutes"].mean(), 1)
    avg_scrap_units = round(matches["scrap_units"].mean(), 1)
    avg_rework_units = round(matches["rework_units"].mean(), 1)
    avg_material_waste_kg = round(matches["material_waste_kg"].mean(), 2)
    avg_energy_kwh = round(matches["energy_kwh"].mean(), 2)
    avg_material_cost_usd = round(matches["material_cost_usd"].mean(), 2)
    avg_energy_cost_usd = round(matches["energy_cost_usd"].mean(), 2)

    return {
        "action_id": action_id,
        "historical_occurrences": n,
        "avg_downtime_minutes": avg_downtime_min,
        "avg_scrap_units": avg_scrap_units,
        "avg_rework_units": avg_rework_units,
        "avg_material_waste_kg": avg_material_waste_kg,
        "avg_energy_kwh": avg_energy_kwh,
        "avg_material_cost_usd": avg_material_cost_usd,
        "avg_energy_cost_usd": avg_energy_cost_usd,
        "avg_total_cost_usd": round(avg_material_cost_usd + avg_energy_cost_usd, 2),
        "reason": (
            f"{action_id} averaged {avg_downtime_min:.1f} min downtime, {avg_scrap_units:.0f} scrap + "
            f"{avg_rework_units:.0f} rework units, {avg_material_waste_kg:.1f}kg material waste, and "
            f"{avg_energy_kwh:.1f}kWh energy across {n} historical occurrence(s) -- "
            f"~${avg_material_cost_usd + avg_energy_cost_usd:.2f} total per occurrence."
        ),
    }
