-- Production Readiness, Quality & Recovery Agent -- Supabase schema.
-- Run this once in the Supabase SQL Editor before running migrate_to_supabase.py.
-- Mirrors the 9 local CSVs 1:1 so data_loader.py's DataFrames look identical
-- regardless of which source (CSV or Supabase) they came from.

create table if not exists machines (
  machine_id text primary key,
  machine_name text not null,
  line text,
  machine_type text,
  current_product text,
  status text,
  location text
);

create table if not exists materials (
  id bigserial primary key,
  material_id text not null,
  material_name text,
  material_type text,
  batch_id text not null,
  supplier text,
  received_date date,
  unique (material_id, batch_id)
);

create table if not exists operators (
  operator_id text primary key,
  name text not null,
  shift text,
  certifications text,
  status text
);

create table if not exists tooling (
  tool_id text primary key,
  tool_name text,
  machine_id text references machines(machine_id),
  installed_at timestamp,
  status text,
  wear_level text,
  cycles_since_install integer
);

create table if not exists production_orders (
  order_id text primary key,
  product_id text,
  machine_id text references machines(machine_id),
  material_id text,
  material_batch text,
  tool_id text,
  operator_id text,
  quantity integer,
  status text,
  planned_start timestamp,
  actual_start timestamp,
  planned_end timestamp,
  actual_end timestamp
);

create table if not exists quality_events (
  event_id text primary key,
  order_id text,
  machine_id text references machines(machine_id),
  "timestamp" timestamp not null,
  units_inspected integer not null,
  units_defective integer not null,
  defect_rate numeric,
  defect_type text
);

create table if not exists historical_incidents (
  incident_id text primary key,
  machine_id text references machines(machine_id),
  tool_id text,
  defect_type text,
  root_cause text,
  recovery_action_id text,
  resolution_time_min integer,
  success boolean,
  date date
);

create table if not exists recovery_actions (
  action_log_id text primary key,
  incident_id text references historical_incidents(incident_id),
  action_id text,
  initiated_at timestamp,
  completed_at timestamp,
  approved_by text,
  outcome text
);

create table if not exists machine_events (
  event_id text primary key,
  machine_id text references machines(machine_id),
  "timestamp" timestamp not null,
  event_type text,
  related_order_id text,
  related_tool_id text,
  operator_id text,
  details text
);

create table if not exists waste_energy_log (
  log_id text primary key,
  incident_id text references historical_incidents(incident_id),
  machine_id text references machines(machine_id),
  action_id text,
  downtime_minutes numeric,
  scrap_units integer,
  rework_units integer,
  material_waste_kg numeric,
  energy_kwh numeric,
  material_cost_usd numeric,
  energy_cost_usd numeric,
  outcome text
);

-- indexes matching the lookups the backend actually does
create index if not exists idx_production_orders_machine on production_orders(machine_id);
create index if not exists idx_quality_events_machine_order on quality_events(machine_id, order_id);
create index if not exists idx_machine_events_machine_ts on machine_events(machine_id, "timestamp");
create index if not exists idx_historical_incidents_machine on historical_incidents(machine_id);
create index if not exists idx_waste_energy_log_action on waste_energy_log(action_id);

-- RLS is off by default for tables created this way, which is fine here:
-- only the backend (using the service_role key, server-side only) ever
-- talks to Supabase directly. If a browser/frontend is ever given direct
-- Supabase access with the anon key, enable RLS and add policies first.
