"""Skill-Match Check (MVP specialist).

Is the assigned operator certified and on shift for this machine/product? If
not, who is the best qualified available alternative?
"""

from .. import data_loader as dl


def _is_certified(cert_string: str, product_id: str, machine_id: str) -> bool:
    if not isinstance(cert_string, str):
        return False
    entries = [c.strip() for c in cert_string.split(";") if c.strip()]
    return any(entry == f"{product_id}:{machine_id}" for entry in entries)


def check_skill_match(machine_id: str, order_id: str = None) -> dict:
    order = dl.order_by_id(order_id) if order_id else dl.current_order_for_machine(machine_id)
    if order is None:
        return {"status": "RED", "reason": f"No production order found for {machine_id}."}

    product_id = order.get("product_id")
    assigned_op_id = order.get("operator_id")
    op_rows = dl.operators[dl.operators["operator_id"] == assigned_op_id]

    assigned = op_rows.iloc[0].to_dict() if not op_rows.empty else None
    assigned_ok = (
        assigned is not None
        and assigned["status"] == "ON_SHIFT"
        and _is_certified(assigned["certifications"], product_id, machine_id)
    )

    if assigned_ok:
        return {
            "status": "GREEN",
            "order_id": order["order_id"],
            "assigned_operator": assigned_op_id,
            "reason": f"{assigned['name']} is on shift and certified for {product_id} on {machine_id}.",
        }

    alternatives = []
    for _, op in dl.operators.iterrows():
        if op["operator_id"] == assigned_op_id:
            continue
        if op["status"] == "ON_SHIFT" and _is_certified(op["certifications"], product_id, machine_id):
            alternatives.append({"operator_id": op["operator_id"], "name": op["name"], "shift": op["shift"]})

    reason_bits = []
    if assigned is None:
        reason_bits.append(f"Assigned operator {assigned_op_id} not found.")
    else:
        if assigned["status"] != "ON_SHIFT":
            reason_bits.append(f"{assigned['name']} is {assigned['status']}.")
        if not _is_certified(assigned["certifications"], product_id, machine_id):
            reason_bits.append(f"{assigned['name']} is not certified for {product_id} on {machine_id}.")

    return {
        "status": "GREEN" if alternatives else "RED",
        "order_id": order["order_id"],
        "assigned_operator": assigned_op_id,
        "assigned_operator_ok": False,
        "reason": " ".join(reason_bits) or "Assigned operator is not qualified/available.",
        "alternatives": alternatives,
    }
