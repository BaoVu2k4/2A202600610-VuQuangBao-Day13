from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env first so every module below sees LANGFUSE_* / APP_* / LOG_* vars.
load_dotenv()

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from structlog.contextvars import bind_contextvars

from .agent import LabAgent
from .audit import audit_log
from .incidents import disable, enable, status
from .logging_config import configure_logging, get_logger
from .metrics import record_error, snapshot
from .middleware import CorrelationIdMiddleware
from .pii import hash_user_id, summarize_text
from .schemas import ChatRequest, ChatResponse
from .tracing import flush_traces, tracing_enabled

configure_logging()
log = get_logger()
app = FastAPI(title="Day 13 Observability Lab")
app.add_middleware(CorrelationIdMiddleware)
agent = LabAgent()


@app.on_event("startup")
async def startup() -> None:
    log.info(
        "app_started",
        service=os.getenv("APP_NAME", "day13-observability-lab"),
        env=os.getenv("APP_ENV", "dev"),
        payload={"tracing_enabled": tracing_enabled()},
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    # Make sure buffered traces are sent to Langfuse before the process exits.
    flush_traces()


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "tracing_enabled": tracing_enabled(), "incidents": status()}


@app.get("/metrics")
async def metrics() -> dict:
    return snapshot()


@app.get("/dashboard")
async def dashboard() -> FileResponse:
    # no-store so the browser always fetches the latest dashboard build.
    return FileResponse(
        Path(__file__).parent / "dashboard.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    # Enrich every log in this request with stable request context.
    actor = hash_user_id(body.user_id)
    bind_contextvars(
        user_id_hash=actor,
        session_id=body.session_id,
        feature=body.feature,
        model=agent.model,
        env=os.getenv("APP_ENV", "dev"),
    )

    log.info(
        "request_received",
        service="api",
        payload={"message_preview": summarize_text(body.message)},
    )
    try:
        result = agent.run(
            user_id=body.user_id,
            feature=body.feature,
            session_id=body.session_id,
            message=body.message,
        )
        log.info(
            "response_sent",
            service="api",
            latency_ms=result.latency_ms,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            payload={"answer_preview": summarize_text(result.answer)},
        )
        # Audit trail: record the access without storing message/answer content.
        audit_log(
            action="chat.request",
            resource=body.feature,
            outcome="success",
            actor=actor,
            correlation_id=request.state.correlation_id,
            session_id=body.session_id,
            model=agent.model,
        )
        return ChatResponse(
            answer=result.answer,
            correlation_id=request.state.correlation_id,
            latency_ms=result.latency_ms,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            quality_score=result.quality_score,
        )
    except Exception as exc:  # pragma: no cover
        error_type = type(exc).__name__
        record_error(error_type)
        log.error(
            "request_failed",
            service="api",
            error_type=error_type,
            payload={"detail": str(exc), "message_preview": summarize_text(body.message)},
        )
        audit_log(
            action="chat.request",
            resource=body.feature,
            outcome="failure",
            actor=actor,
            correlation_id=request.state.correlation_id,
            session_id=body.session_id,
            model=agent.model,
            error_type=error_type,
        )
        raise HTTPException(status_code=500, detail=error_type) from exc


@app.post("/incidents/{name}/enable")
async def enable_incident(name: str, request: Request) -> JSONResponse:
    try:
        enable(name)
        log.warning("incident_enabled", service="control", payload={"name": name})
        audit_log(
            action="incident.enable",
            resource=name,
            outcome="success",
            actor="operator",
            correlation_id=request.state.correlation_id,
        )
        return JSONResponse({"ok": True, "incidents": status()})
    except KeyError as exc:
        audit_log(
            action="incident.enable",
            resource=name,
            outcome="denied",
            actor="operator",
            correlation_id=request.state.correlation_id,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/incidents/{name}/disable")
async def disable_incident(name: str, request: Request) -> JSONResponse:
    try:
        disable(name)
        log.warning("incident_disabled", service="control", payload={"name": name})
        audit_log(
            action="incident.disable",
            resource=name,
            outcome="success",
            actor="operator",
            correlation_id=request.state.correlation_id,
        )
        return JSONResponse({"ok": True, "incidents": status()})
    except KeyError as exc:
        audit_log(
            action="incident.disable",
            resource=name,
            outcome="denied",
            actor="operator",
            correlation_id=request.state.correlation_id,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
