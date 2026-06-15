from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

# Load .env so LANGFUSE_* and APP_* vars exist before the Langfuse client initializes.
load_dotenv()

try:
    # Langfuse v3 exposes `observe` and `get_client` at the top level
    # (the old `langfuse.decorators` module was removed in v3).
    from langfuse import get_client, observe

    class _LangfuseContext:
        """Compatibility shim: keeps the v2-style `langfuse_context` API the
        agent already calls, but routes everything to the Langfuse v3 client."""

        _TRACE_FIELDS = {
            "name", "user_id", "session_id", "version",
            "input", "output", "metadata", "tags", "public",
        }

        def update_current_trace(self, **kwargs: Any) -> None:
            payload = {k: v for k, v in kwargs.items() if k in self._TRACE_FIELDS and v is not None}
            if payload:
                get_client().update_current_trace(**payload)

        def update_current_observation(self, metadata: Any = None, usage_details: Any = None, **_: Any) -> None:
            meta: dict[str, Any] = dict(metadata) if isinstance(metadata, dict) else {}
            if usage_details is not None:
                meta["usage_details"] = usage_details
            if meta:
                get_client().update_current_span(metadata=meta)

    langfuse_context = _LangfuseContext()

except Exception:  # pragma: no cover - langfuse unavailable: degrade to no-op
    def observe(*args: Any, **kwargs: Any):
        if args and callable(args[0]):  # bare @observe usage
            return args[0]

        def decorator(func):
            return func

        return decorator

    class _DummyContext:
        def update_current_trace(self, **kwargs: Any) -> None:
            return None

        def update_current_observation(self, **kwargs: Any) -> None:
            return None

    langfuse_context = _DummyContext()


def tracing_enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def flush_traces() -> None:
    """Force-send any buffered traces (called on app shutdown)."""
    try:
        get_client().flush()
    except Exception:  # pragma: no cover
        pass
