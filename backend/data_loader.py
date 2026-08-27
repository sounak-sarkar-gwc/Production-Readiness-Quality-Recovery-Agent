"""Loads the plant datasets into pandas DataFrames once at process start,
from either local CSVs (default) or Supabase, controlled by DATA_SOURCE.

All specialist checks and the core agent read through this module's
DataFrames rather than the source directly, so the rest of the codebase is
identical either way -- switching DATA_SOURCE is the only change needed.
See supabase_schema.sql and migrate_to_supabase.py for the Supabase side.

Loading is fail-fast: a missing/unreadable/empty required dataset raises
RuntimeError immediately at import time (i.e. at process startup), not as a
mysterious KeyError/AttributeError the first time some unlucky request
touches an empty DataFrame.
"""

import math
from pathlib import Path

import pandas as pd

from .config import settings


def sanitize(value):
    """Recursively replaces NaN floats (pandas' representation of empty CSV
    cells, e.g. a still-RUNNING order's actual_end) with None, since NaN is
    not valid JSON."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


DATA_DIR = Path(__file__).resolve().parent.parent

MACHINES_CSV = DATA_DIR / "machines.csv"
MATERIALS_CSV = DATA_DIR / "materials.csv"
OPERATORS_CSV = DATA_DIR / "operators.csv"
TOOLING_CSV = DATA_DIR / "tooling.csv"
PRODUCTION_ORDERS_CSV = DATA_DIR / "production_orders.csv"
QUALITY_EVENTS_CSV = DATA_DIR / "quality_events.csv"
HISTORICAL_INCIDENTS_CSV = DATA_DIR / "historical_incidents.csv"
RECOVERY_ACTIONS_CSV = DATA_DIR / "recovery_actions.csv"
MACHINE_EVENTS_CSV = DATA_DIR / "machine_events.csv"
WASTE_ENERGY_LOG_CSV = DATA_DIR / "waste_energy_log.csv"


def _load_required_csv(name: str, path: Path, **read_csv_kwargs) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"Required dataset '{name}' not found at {path}.")
    try:
        df = pd.read_csv(path, **read_csv_kwargs)
    except pd.errors.EmptyDataError as exc:
        raise RuntimeError(f"Required dataset '{name}' at {path} is empty or unreadable.") from exc
    except Exception as exc:  # malformed CSV, bad dtypes, etc.
        raise RuntimeError(f"Failed to load required dataset '{name}' from {path}: {exc}") from exc
    if df.empty:
        raise RuntimeError(f"Required dataset '{name}' at {path} has no rows.")
    return df


_supabase_client = None


def _get_supabase_client():
    global _supabase_client
    if _supabase_client is None:
        if not settings.supabase_url or not settings.supabase_key:
            raise RuntimeError(
                "DATA_SOURCE=supabase but SUPABASE_URL/SUPABASE_KEY are not set in backend/.env."
            )
        from supabase import create_client  # imported lazily -- only needed in this mode

        _supabase_client = create_client(settings.supabase_url, settings.supabase_key)
    return _supabase_client


def _load_required_supabase(name: str, table_name: str, parse_dates=None) -> pd.DataFrame:
    client = _get_supabase_client()
    rows = []
    page_size = 1000
    start = 0
    try:
        while True:
            resp = client.table(table_name).select("*").range(start, start + page_size - 1).execute()
            batch = resp.data
            rows.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch required dataset '{name}' from Supabase table '{table_name}': {exc}") from exc

    if not rows:
        raise RuntimeError(
            f"Required dataset '{name}' has no rows in Supabase table '{table_name}'. "
            f"Run `python -m backend.migrate_to_supabase` first."
        )
    df = pd.DataFrame(rows)
    if parse_dates:
        for col in parse_dates:
            df[col] = pd.to_datetime(df[col])
    return df


def _load_required(name: str, path: Path, table_name: str = None, **read_csv_kwargs) -> pd.DataFrame:
    if settings.using_supabase:
        return _load_required_supabase(name, table_name or name, parse_dates=read_csv_kwargs.get("parse_dates"))
    return _load_required_csv(name, path, **read_csv_kwargs)


machines = _load_required("machines", MACHINES_CSV)
materials = _load_required("materials", MATERIALS_CSV)
operators = _load_required("operators", OPERATORS_CSV)
tooling = _load_required("tooling", TOOLING_CSV)
production_orders = _load_required("production_orders", PRODUCTION_ORDERS_CSV)
quality_events = _load_required("quality_events", QUALITY_EVENTS_CSV, parse_dates=["timestamp"])
historical_incidents = _load_required(
    "historical_incidents", HISTORICAL_INCIDENTS_CSV, parse_dates=["date"]
)
recovery_actions = _load_required(
    "recovery_actions", RECOVERY_ACTIONS_CSV, parse_dates=["initiated_at", "completed_at"]
)
machine_events = _load_required("machine_events", MACHINE_EVENTS_CSV, parse_dates=["timestamp"])
waste_energy_log = _load_required("waste_energy_log", WASTE_ENERGY_LOG_CSV)

_KNOWN_MACHINE_IDS = set(machines["machine_id"])
_KNOWN_ORDER_IDS = set(production_orders["order_id"])


def machine_exists(machine_id: str) -> bool:
    return machine_id in _KNOWN_MACHINE_IDS


def order_exists(order_id: str) -> bool:
    return order_id in _KNOWN_ORDER_IDS


def current_order_for_machine(machine_id: str):
    """Best-effort 'current' order for a machine: the RUNNING one if there is
    one, otherwise the most recently started order on record."""
    mach_orders = production_orders[production_orders["machine_id"] == machine_id]
    if mach_orders.empty:
        return None
    running = mach_orders[mach_orders["status"] == "RUNNING"]
    if not running.empty:
        return sanitize(running.iloc[-1].to_dict())
    with_start = mach_orders.dropna(subset=["actual_start"])
    if with_start.empty:
        return None
    with_start = with_start.sort_values("actual_start")
    return sanitize(with_start.iloc[-1].to_dict())


def order_by_id(order_id: str):
    rows = production_orders[production_orders["order_id"] == order_id]
    if rows.empty:
        return None
    return sanitize(rows.iloc[0].to_dict())
