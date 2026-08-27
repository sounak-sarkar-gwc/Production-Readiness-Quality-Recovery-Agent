"""One-off script: uploads the local CSV datasets into Supabase tables
matching supabase_schema.sql. Run manually (not part of the running server):

    python -m backend.migrate_to_supabase

Requires SUPABASE_URL and SUPABASE_KEY (the service_role key) in
backend/.env, and DATA_SOURCE left as "csv" (the default) so this script
reads the local files rather than trying to fetch from Supabase to upload
back to Supabase. Safe to re-run -- upserts on each table's primary/unique
key rather than blind-inserting duplicates.
"""

import math
import sys

import pandas as pd

from . import data_loader as dl
from .config import settings

BATCH_SIZE = 500


def _clean_records(df: pd.DataFrame) -> list:
    """NaN -> None, Timestamp -> ISO string, so every value is JSON-safe for
    the Supabase REST API."""
    records = []
    for row in df.to_dict("records"):
        clean = {}
        for key, value in row.items():
            if isinstance(value, float) and math.isnan(value):
                clean[key] = None
            elif isinstance(value, pd.Timestamp):
                clean[key] = None if pd.isna(value) else value.isoformat()
            else:
                clean[key] = value
        records.append(clean)
    return records


def _upload(client, table_name: str, df: pd.DataFrame, on_conflict: str) -> None:
    records = _clean_records(df)
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        client.table(table_name).upsert(batch, on_conflict=on_conflict).execute()
    print(f"  {table_name}: {len(records)} rows uploaded")


def main() -> None:
    if settings.using_supabase:
        print(
            "DATA_SOURCE=supabase -- set it to 'csv' (or unset it) before running this "
            "migration, so it reads the local files rather than Supabase itself.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not settings.supabase_url or not settings.supabase_key:
        print("SUPABASE_URL / SUPABASE_KEY not set in backend/.env -- nothing to upload to.", file=sys.stderr)
        sys.exit(1)

    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_key)

    print("Uploading datasets to Supabase...")
    _upload(client, "machines", dl.machines, "machine_id")
    _upload(client, "materials", dl.materials, "material_id,batch_id")
    _upload(client, "operators", dl.operators, "operator_id")
    _upload(client, "tooling", dl.tooling, "tool_id")
    _upload(client, "production_orders", dl.production_orders, "order_id")
    _upload(client, "quality_events", dl.quality_events, "event_id")
    _upload(client, "historical_incidents", dl.historical_incidents, "incident_id")
    _upload(client, "recovery_actions", dl.recovery_actions, "action_log_id")
    _upload(client, "machine_events", dl.machine_events, "event_id")
    _upload(client, "waste_energy_log", dl.waste_energy_log, "log_id")
    print("Done.")


if __name__ == "__main__":
    main()
