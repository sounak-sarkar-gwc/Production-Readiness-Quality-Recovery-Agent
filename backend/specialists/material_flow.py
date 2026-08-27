"""Material Flow Check (MVP specialist).

Is the material for the machine's current/next order actually staged and
available, not just present somewhere in the plant?
"""

from .. import data_loader as dl


def check_material_flow(machine_id: str, order_id: str = None) -> dict:
    order = dl.order_by_id(order_id) if order_id else dl.current_order_for_machine(machine_id)
    if order is None:
        return {
            "status": "RED",
            "reason": f"No production order found for {machine_id}.",
        }

    material_id = order.get("material_id")
    material_batch = order.get("material_batch")
    mat_rows = dl.materials[dl.materials["material_id"] == material_id]

    if mat_rows.empty:
        return {
            "status": "RED",
            "order_id": order["order_id"],
            "material_id": material_id,
            "required_batch": material_batch,
            "reason": f"Material {material_id} is not in the material master at all.",
        }

    mat = mat_rows.iloc[0]
    batch_match = mat_rows[mat_rows["batch_id"] == material_batch]

    if batch_match.empty:
        return {
            "status": "YELLOW",
            "order_id": order["order_id"],
            "material_id": material_id,
            "material_name": mat["material_name"],
            "required_batch": material_batch,
            "known_batches": mat_rows["batch_id"].tolist(),
            "reason": (
                f"{mat['material_name']} is known, but the specific batch "
                f"{material_batch} required by {order['order_id']} is not on record "
                f"as received/staged. Available batches: {mat_rows['batch_id'].tolist()}."
            ),
        }

    return {
        "status": "GREEN",
        "order_id": order["order_id"],
        "material_id": material_id,
        "material_name": mat["material_name"],
        "batch_id": material_batch,
        "supplier": batch_match.iloc[0]["supplier"],
        "received_date": str(batch_match.iloc[0]["received_date"]),
        "reason": f"{mat['material_name']} batch {material_batch} is on record as received and available.",
    }
