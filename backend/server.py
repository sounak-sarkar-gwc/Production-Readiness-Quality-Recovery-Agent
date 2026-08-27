"""
Production Readiness, Quality & Recovery Agent -- Backend API Server.

FastAPI server implementing the 6-phase loop (Readiness -> Detection ->
Impact -> Investigation -> Recovery -> Human Approval -> Verification) as a
set of endpoints under the /prq prefix, on its own port (default 8100) --
deliberately distinct from any other agent backend that might run alongside
it.

The core agent is invoked as a stateless, single-turn tool-calling loop per
request (see agent_core.run_agent_turn) rather than a persisted multi-turn
session: each phase endpoint is independently triggerable and does not
depend on conversational memory from a previous call.

Guardrails in this file:
  - every intentional error response goes through the AppError hierarchy ->
    a single JSON envelope shape, never a raw traceback
  - machine_id/order_id/incident_id are validated against real data before
    any processing happens, so a typo'd ID is a clean 404, not undefined
    behavior three functions deep
  - a threading.Lock guards the in-memory incident store's read-modify-write
    sequences (create-if-absent, approve, verify) -- FastAPI runs these sync
    handlers in a thread pool, so concurrent requests are a real
    possibility, not a hypothetical
  - X-API-Key auth (opt-in via BACKEND_API_KEY) and per-route rate limits on
    the two LLM-backed endpoints, since those cost real tokens against a
    free-tier quota
  - request-id tagging + access logging on every request
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import agent_core, data_loader as dl, decision, tools
from .config import settings
from .errors import AppError, ConflictError, NotFoundError
from .logging_config import configure_logging
from .middleware import RequestContextMiddleware, SecurityHeadersMiddleware, require_api_key
from .models import ApprovalPayload, QualityEventPayload, ReadinessCheckRequest, VerifyPayload
from .specialists.vision_quality import check_vision_quality, ingest_quality_event

configure_logging()
logger = logging.getLogger("prq.server")

if not settings.agent_configured:
    logger.warning(
        "GEMINI_API_KEY is not set -- /readiness/check and /quality/events "
        "(the two LLM-backed endpoints) will return 503 until it's configured."
    )
if not settings.auth_enabled:
    logger.warning("BACKEND_API_KEY is not set -- API auth is DISABLED. Fine for local dev, not for prod.")

PREFIX = "/prq"

app = FastAPI(
    title="Production Readiness, Quality & Recovery Agent API",
    description="Backend for the readiness -> detection -> impact -> investigation -> recovery -> approval -> verification loop.",
    version="0.2.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])
app.state.limiter = limiter

app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

protected_router = APIRouter(prefix=PREFIX, dependencies=[Depends(require_api_key)])

# ---------------------------------------------------------------------------
# In-memory state (POC only -- resets on restart, single-process only)
# ---------------------------------------------------------------------------
_readiness_store: Dict[str, Dict[str, Any]] = {}
_incidents_store: Dict[str, Dict[str, Any]] = {}
_activity_log: list = []
_incident_counter = 0
_store_lock = threading.Lock()


def _log(event: str) -> None:
    _activity_log.insert(0, {"timestamp": datetime.now().strftime("%H:%M:%S"), "event": event})


def _next_incident_id() -> str:
    global _incident_counter
    _incident_counter += 1
    return f"PRQ-INC-{_incident_counter:04d}"


def _require_machine(machine_id: str) -> None:
    if not dl.machine_exists(machine_id):
        raise NotFoundError(f"Unknown machine_id '{machine_id}'.")


def _require_order(order_id: str) -> None:
    if not dl.order_exists(order_id):
        raise NotFoundError(f"Unknown order_id '{order_id}'.")


# ---------------------------------------------------------------------------
# Error handling -- every intentional error becomes {"error": {...}}
# ---------------------------------------------------------------------------
def _envelope(request: Request, error_type: str, message: str, details: Any = None) -> dict:
    body = {
        "error": {
            "type": error_type,
            "message": message,
            "request_id": getattr(request.state, "request_id", "-"),
        }
    }
    if details is not None:
        body["error"]["details"] = details
    return body


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    logger.warning("%s: %s", exc.error_type, exc.message)
    return JSONResponse(status_code=exc.status_code, content=_envelope(request, exc.error_type, exc.message, exc.details))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=_envelope(request, "validation_error", "Invalid request body/parameters.", jsonable_encoder(exc.errors())),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content=_envelope(request, "http_error", str(exc.detail)))


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content=_envelope(request, "rate_limited", f"Rate limit exceeded: {exc.detail}"))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(status_code=500, content=_envelope(request, "internal_error", "An unexpected error occurred."))


# ---------------------------------------------------------------------------
# Health -- unauthenticated, for infra liveness/readiness probes
# ---------------------------------------------------------------------------
@app.get(f"{PREFIX}/health")
def get_health():
    return {
        "status": "ok",
        "service": "Production Readiness, Quality & Recovery Agent",
        "version": "0.2.0",
        "timestamp": datetime.now().isoformat(),
        "agent_model": settings.agent_model,
    }


@app.get(f"{PREFIX}/health/ready")
def get_readiness_probe():
    checks = {"datasets_loaded": True, "gemini_configured": settings.agent_configured}
    healthy = all(checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
    )


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
@protected_router.get("/machines")
def get_machines():
    return {"total": len(dl.machines), "machines": dl.machines.to_dict("records")}


@protected_router.get("/machines/{machine_id}")
def get_machine(machine_id: str):
    _require_machine(machine_id)
    machine = dl.machines[dl.machines["machine_id"] == machine_id].iloc[0].to_dict()
    machine["current_order"] = dl.current_order_for_machine(machine_id)
    return machine


# ---------------------------------------------------------------------------
# Phase 1 -- Readiness ("Can we start?")
# ---------------------------------------------------------------------------
@protected_router.post("/readiness/check")
@limiter.limit(settings.rate_limit_llm)
def post_readiness_check(request: Request, payload: ReadinessCheckRequest):
    _require_machine(payload.machine_id)
    if payload.order_id:
        _require_order(payload.order_id)
        order = dl.order_by_id(payload.order_id)
    else:
        order = dl.current_order_for_machine(payload.machine_id)
    if order is None:
        raise NotFoundError(f"No production order found for machine '{payload.machine_id}'.")
    order_id = order["order_id"]

    tool_defs, tool_impls = tools.readiness_tools(payload.machine_id, order_id)
    system_prompt = (
        "You are the readiness-decision component of a manufacturing Production "
        "Readiness, Quality & Recovery Agent. Call check_material_flow, "
        "check_skill_match, and check_quality_readiness for the given machine/order, "
        "then state clearly: is this order ready to start? List any blockers by name "
        "and, if the skill-match check offers an alternative operator, recommend one. "
        "Be concise -- 3-5 sentences."
    )
    user_message = f"Evaluate readiness for order {order_id} on machine {payload.machine_id}."

    result = agent_core.run_agent_turn(system_prompt, user_message, tool_defs, tool_impls)
    checks = {c["name"]: c["result"] for c in result["tool_calls"]}
    blockers = [name for name, r in checks.items() if r.get("status") not in ("GREEN",)]

    record = {
        "order_id": order_id,
        "machine_id": payload.machine_id,
        "checked_at": time.time(),
        "checks": checks,
        "blockers": blockers,
        "ready": not blockers,
        "narrative": result["final_text"],
    }
    with _store_lock:
        _readiness_store[order_id] = record
    _log(f"Readiness check run for {order_id} on {payload.machine_id}: {'READY' if not blockers else 'BLOCKED - ' + ', '.join(blockers)}")
    return record


@protected_router.get("/readiness/{order_id}")
def get_readiness(order_id: str):
    record = _readiness_store.get(order_id)
    if not record:
        raise NotFoundError(f"No readiness check on record for '{order_id}'. POST /prq/readiness/check first.")
    return record


# ---------------------------------------------------------------------------
# Phase 2/3 -- Detection ("Are we producing good parts? Isolated or growing?")
# ---------------------------------------------------------------------------
@protected_router.post("/quality/events")
@limiter.limit(settings.rate_limit_llm)
def post_quality_event(request: Request, payload: QualityEventPayload):
    _require_machine(payload.machine_id)
    _require_order(payload.order_id)

    ingest_quality_event({
        "machine_id": payload.machine_id,
        "order_id": payload.order_id,
        "timestamp": payload.timestamp,
        "units_inspected": payload.units_inspected,
        "units_defective": payload.units_defective,
        "defect_rate": payload.defect_rate,
        "defect_type": payload.defect_type,
    })

    trend = check_vision_quality(payload.machine_id, payload.order_id)
    _log(f"Quality event ingested for {payload.machine_id}/{payload.order_id}: defect_rate={payload.defect_rate:.1%}")

    incident = _create_incident(payload.machine_id, payload.order_id, payload.timestamp) if trend.get("rising") else None
    return {"trend": trend, "incident": incident}


@protected_router.get("/quality/{machine_id}/trend")
def get_quality_trend(machine_id: str, order_id: Optional[str] = None):
    _require_machine(machine_id)
    if order_id:
        _require_order(order_id)
    return check_vision_quality(machine_id, order_id)


# ---------------------------------------------------------------------------
# Phase 4/5 -- Investigation + Recovery decision
# ---------------------------------------------------------------------------
def _create_incident(machine_id: str, order_id: str, since_timestamp: str) -> Dict[str, Any]:
    # Reserve (or reuse) the incident slot under the lock -- fast, no I/O --
    # so two concurrent quality-event posts for the same machine/order can't
    # both create a duplicate incident. The slow Gemini call happens after
    # the lock is released.
    with _store_lock:
        existing_open = next(
            (i for i in _incidents_store.values()
             if i["machine_id"] == machine_id and i["order_id"] == order_id
             and i["status"] not in ("RESOLVED", "REJECTED")),
            None,
        )
        if existing_open:
            return existing_open

        incident_id = _next_incident_id()
        _incidents_store[incident_id] = {
            "incident_id": incident_id,
            "machine_id": machine_id,
            "order_id": order_id,
            "since_timestamp": since_timestamp,
            "reported_at": time.time(),
            "status": "INVESTIGATING",
            "approved": None,
        }

    tool_defs, tool_impls = tools.investigation_tools(machine_id, order_id, since_timestamp)
    system_prompt = (
        "You are the investigation component of a manufacturing Production Readiness, "
        "Quality & Recovery Agent. Call check_vision_quality, check_impact, and "
        "check_root_cause for the given machine/order/since_timestamp. Then summarize: "
        "what is happening, how many units are potentially affected, and the most likely "
        "cause with your confidence level. State findings as evidence-based, not certainty. "
        "4-6 sentences."
    )
    user_message = f"Investigate the rising defect trend on {machine_id}, order {order_id}, since {since_timestamp}."

    try:
        result = agent_core.run_agent_turn(system_prompt, user_message, tool_defs, tool_impls)
    except AppError:
        # Investigation failed (e.g. Gemini unavailable) -- leave the
        # incident visible and flagged rather than losing the fact that a
        # rising trend was detected at all.
        with _store_lock:
            _incidents_store[incident_id]["status"] = "INVESTIGATION_FAILED"
        raise

    findings = {c["name"]: c["result"] for c in result["tool_calls"]}
    vision = findings.get("check_vision_quality", {})
    impact = findings.get("check_impact", {})
    root_cause = findings.get("check_root_cause", {})
    defect_type = next((t["defect_type"] for t in vision.get("trend", []) if t.get("defect_type")), "UNKNOWN")
    recovery_options = decision.generate_recovery_options(defect_type, include_waste_energy=True)

    with _store_lock:
        _incidents_store[incident_id].update({
            "defect_type": defect_type,
            "status": "AWAITING_APPROVAL",
            "vision_quality": vision,
            "impact": impact,
            "root_cause": root_cause,
            "investigation_narrative": result["final_text"],
            "recovery_options": recovery_options,
            "verification": None,
        })
        incident = dict(_incidents_store[incident_id])

    _log(f"Incident {incident_id} opened for {machine_id}/{order_id}: {defect_type}, "
         f"~{impact.get('potentially_affected_units', '?')} units potentially affected.")
    return incident


@protected_router.get("/incidents")
def get_incidents(status: Optional[str] = None):
    items = list(_incidents_store.values())
    if status:
        items = [i for i in items if i["status"] == status.upper()]
    return sorted(items, key=lambda i: i["reported_at"], reverse=True)


@protected_router.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    incident = _incidents_store.get(incident_id)
    if not incident:
        raise NotFoundError(f"Unknown incident '{incident_id}'.")
    return incident


# ---------------------------------------------------------------------------
# Phase 5/6 -- Human Approval + Verification
# ---------------------------------------------------------------------------
@protected_router.post("/incidents/{incident_id}/approve")
def approve_incident(incident_id: str, payload: ApprovalPayload):
    with _store_lock:
        incident = _incidents_store.get(incident_id)
        if not incident:
            raise NotFoundError(f"Unknown incident '{incident_id}'.")
        if incident["status"] != "AWAITING_APPROVAL":
            raise ConflictError(f"Incident '{incident_id}' is not awaiting approval (status={incident['status']}).")

        incident["approved"] = payload.approved
        incident["approval_notes"] = payload.notes
        incident["decided_at"] = time.time()
        incident["status"] = "RECOVERY_IN_PROGRESS" if payload.approved else "REJECTED"
        result = dict(incident)

    _log(f"Incident {incident_id} {'APPROVED' if payload.approved else 'REJECTED'} by supervisor.")
    return result


@protected_router.post("/incidents/{incident_id}/verify")
def verify_incident(incident_id: str, payload: VerifyPayload):
    with _store_lock:
        incident = _incidents_store.get(incident_id)
        if not incident:
            raise NotFoundError(f"Unknown incident '{incident_id}'.")
        if incident["status"] != "RECOVERY_IN_PROGRESS":
            raise ConflictError(f"Incident '{incident_id}' has no approved recovery in progress (status={incident['status']}).")

        sample_rate = payload.units_defective / payload.units_inspected if payload.units_inspected else 0.0
        baseline = incident.get("vision_quality", {}).get("baseline_defect_rate", 0.01)
        recovered = sample_rate <= max(baseline * 1.5, 0.01)

        incident["verification"] = {
            "units_inspected": payload.units_inspected,
            "units_defective": payload.units_defective,
            "sample_defect_rate": round(sample_rate, 4),
            "baseline_defect_rate": baseline,
            "recovered": recovered,
            "verified_at": time.time(),
        }
        incident["status"] = "RESOLVED" if recovered else "ESCALATED"
        result = dict(incident)

    _log(f"Incident {incident_id} verification sample: {sample_rate:.1%} defect rate -> "
         f"{'RECOVERED' if recovered else 'NOT RECOVERED, escalating'}.")
    return result


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@protected_router.get("/dashboard/summary")
def get_dashboard_summary():
    incidents = list(_incidents_store.values())
    return {
        "total_incidents": len(incidents),
        "awaiting_approval": len([i for i in incidents if i["status"] == "AWAITING_APPROVAL"]),
        "recovery_in_progress": len([i for i in incidents if i["status"] == "RECOVERY_IN_PROGRESS"]),
        "resolved": len([i for i in incidents if i["status"] == "RESOLVED"]),
        "escalated": len([i for i in incidents if i["status"] == "ESCALATED"]),
        "readiness_checks_run": len(_readiness_store),
        "readiness_blocked": len([r for r in _readiness_store.values() if not r["ready"]]),
        "activity_log": _activity_log[:20],
    }


app.include_router(protected_router)

if __name__ == "__main__":
    import uvicorn

    print(f"Starting Production Readiness, Quality & Recovery Agent backend on http://localhost:{settings.port}{PREFIX}")
    uvicorn.run("backend.server:app", host="0.0.0.0", port=settings.port, reload=not settings.is_production)
