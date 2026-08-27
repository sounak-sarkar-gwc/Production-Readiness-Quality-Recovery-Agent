"""Builds the Anthropic tool schema + local implementation map for each
phase's agent turn. machine_id/order_id/since_timestamp are bound as
closures from the request context rather than left for the model to guess,
since the caller (server.py) already knows them from the URL/body.
"""

from .specialists.material_flow import check_material_flow
from .specialists.skill_match import check_skill_match
from .specialists.quality_readiness import check_quality_readiness
from .specialists.vision_quality import check_vision_quality
from .specialists.impact import check_impact
from .specialists.root_cause import check_root_cause


def readiness_tools(machine_id: str, order_id: str = None):
    tools = [
        {
            "name": "check_material_flow",
            "description": "Checks whether the required material batch for this order is staged and available.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "check_skill_match",
            "description": "Checks whether the assigned operator is certified and on shift; suggests an alternative if not.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "check_quality_readiness",
            "description": "Checks whether the required tool is mounted/in-use and calibration is on record.",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]
    impls = {
        "check_material_flow": lambda: check_material_flow(machine_id, order_id),
        "check_skill_match": lambda: check_skill_match(machine_id, order_id),
        "check_quality_readiness": lambda: check_quality_readiness(machine_id, order_id),
    }
    return tools, impls


def investigation_tools(machine_id: str, order_id: str, since_timestamp: str = None):
    tools = [
        {
            "name": "check_vision_quality",
            "description": "Returns the defect-rate trend for this order and whether it is rising.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "check_impact",
            "description": "Estimates potentially affected units, batch, and WIP exposure since the defect trend started.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "check_root_cause",
            "description": "Correlates recent machine events (tool changes, param drift, etc.) and historical incidents to find a likely cause.",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]
    impls = {
        "check_vision_quality": lambda: check_vision_quality(machine_id, order_id),
        "check_impact": lambda: check_impact(machine_id, order_id, since_timestamp),
        "check_root_cause": lambda: check_root_cause(machine_id, order_id, since_timestamp),
    }
    return tools, impls
