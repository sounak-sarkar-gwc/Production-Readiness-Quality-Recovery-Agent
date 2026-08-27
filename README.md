# Production Readiness, Quality & Recovery Agent

*From "Can We Start?" to "Did We Recover?"*

An agentic manufacturing system that connects two problems usually handled separately: whether a production order is genuinely ready to start (material staged, tooling ready, operator qualified, quality prerequisites met), and whether an isolated defect is quietly becoming a growing quality problem once production is underway.

One core agent owns the full decision loop and invokes narrowly-scoped specialist checks as tools when it needs evidence:

```
Readiness → Detection → Impact → Investigation → Recovery → Human Approval → Verification
```

See [`Production_Readiness_Quality_Recovery_Agent.docx`](Production_Readiness_Quality_Recovery_Agent.docx) for the full solution approach document.

## Status

Backend only. No frontend yet — everything is exercised via the included [Postman collection](backend/postman_collection.json).

## Architecture

```
backend/
├── server.py                 # FastAPI app -- every HTTP endpoint, under /prq
├── config.py                 # all env vars, validated once at startup
├── data_loader.py            # loads the datasets (CSV or Supabase) into DataFrames
├── agent_core.py             # Gemini tool-calling loop (retries, timeout, error handling)
├── tools.py                  # binds specialist functions into Gemini tool schemas per phase
├── decision.py               # recovery-option ranking (deterministic, not LLM)
├── models.py                 # request-body validation
├── errors.py                 # typed exceptions -> consistent JSON error envelope
├── middleware.py             # request-ID tagging, security headers, X-API-Key auth
├── logging_config.py         # structured logging setup
├── migrate_to_supabase.py    # one-off CSV -> Supabase upload script
├── supabase_schema.sql       # table definitions for Supabase
├── postman_collection.json   # importable Postman collection with worked examples
└── specialists/              # the individual checks the core agent calls as tools
    ├── material_flow.py      # is the required material batch staged?
    ├── skill_match.py        # is the operator certified & on shift?
    ├── quality_readiness.py  # is tooling mounted + calibrated?
    ├── vision_quality.py     # defect-rate trend, rising or not
    ├── impact.py             # potentially affected units since defect onset
    ├── root_cause.py         # correlates machine events + history
    └── waste_energy.py       # scrap/energy cost estimate per recovery action (stretch)
```

Datasets (`*.csv` at repo root, or the same tables in Supabase — see below):
`machines`, `materials`, `operators`, `tooling`, `production_orders`, `quality_events`, `historical_incidents`, `recovery_actions`, `machine_events`, `waste_energy_log`.

**Request flow:** an endpoint validates the machine/order/incident ID exists, then for the two LLM-backed endpoints builds a phase-specific tool list and hands it to `agent_core.run_agent_turn()`, which runs Gemini's tool-calling loop against the relevant `specialists/*.py` functions. Recovery options are ranked deterministically from history, not by the LLM. Every error becomes a typed exception with a consistent JSON shape.

| Endpoint | Uses Gemini? |
|---|---|
| `POST /prq/readiness/check` | Yes |
| `POST /prq/quality/events` | Only if it detects a rising defect trend |
| everything else | No |

In-memory incident/readiness state resets on restart and never writes back to the data source — data only flows one direction (source → backend) at startup.

## Setup

**Requirements:** Python 3.10+.

```bash
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

Edit `backend/.env`:
- `GEMINI_API_KEY` — required for the two LLM-backed endpoints. Free tier at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
- `DATA_SOURCE` — `csv` (default, reads the local files) or `supabase` (see below).
- Everything else has a sane default; see the comments in `.env.example`.

### Optional: Supabase as the data source

1. Create a project at [supabase.com](https://supabase.com).
2. Run [`backend/supabase_schema.sql`](backend/supabase_schema.sql) in its SQL Editor.
3. Set `SUPABASE_URL` and `SUPABASE_KEY` (the `service_role` key — server-side only, never expose it client-side) in `backend/.env`.
4. `python -m backend.migrate_to_supabase` — uploads all 10 local CSVs.
5. Set `DATA_SOURCE=supabase` and restart.

## Running it

From the repo root (not from inside `backend/` — it's run as a module so the relative imports resolve):

```bash
python -m backend.server
```

Auto-reloads on code changes when `ENV=development` (the default). Listens on `http://localhost:8100`.

Verify it's up:
```bash
curl http://localhost:8100/prq/health
curl http://localhost:8100/prq/health/ready
```

Then import [`backend/postman_collection.json`](backend/postman_collection.json) into Postman — it has both a guided walkthrough and standalone examples covering every endpoint and every check's success/blocked scenarios.

## Guardrails

- Typed error hierarchy → every intentional failure returns `{"error": {"type", "message", "request_id"}}`, never a raw traceback
- Machine/order/incident IDs validated against real data before processing
- Retry with backoff on transient Gemini errors only (5xx, 429); a bad key or unknown model fails immediately
- Per-route rate limits, tighter on the LLM-backed endpoints (free-tier quota protection)
- Optional `X-API-Key` auth (`BACKEND_API_KEY` in `.env`; blank disables it)
- Thread-safe in-memory store (a lock guards incident create/approve/verify against concurrent requests)
- Fail-fast startup: a missing/empty dataset raises immediately, not on the first unlucky request
