from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Audit log is a SEPARATE, append-only stream from the application log
# (data/logs.jsonl). It records *who did what to which resource and with what
# outcome* for security/compliance review. It intentionally stores only
# non-PII metadata (hashed user id, feature, model, outcome) — never the raw
# user message or answer text.
AUDIT_LOG_PATH = Path(os.getenv("AUDIT_LOG_PATH", "data/audit.jsonl"))


def audit_log(
    *,
    action: str,
    resource: str,
    outcome: str,
    actor: str | None = None,
    correlation_id: str | None = None,
    **fields: Any,
) -> None:
    """Append one structured audit event to AUDIT_LOG_PATH.

    Args:
        action: verb describing the operation (e.g. "chat.request", "incident.enable").
        resource: object acted upon (e.g. feature name or incident name).
        outcome: "success" | "failure" | "denied".
        actor: hashed user id or operator identity (no raw PII).
        correlation_id: request id to join with logs/traces.
        **fields: extra non-PII metadata (model, error_type, etc.).
    """
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "audit",
        "action": action,
        "resource": resource,
        "outcome": outcome,
        "actor": actor,
        "correlation_id": correlation_id,
        "env": os.getenv("APP_ENV", "dev"),
    }
    # Drop None extras so the line stays compact, but keep explicit keys above.
    record.update({k: v for k, v in fields.items() if v is not None})

    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
