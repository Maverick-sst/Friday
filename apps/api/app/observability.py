"""Central observability layer (OTEL + Langfuse) — OTEL_LANGFUSE_EXECUTION_PRD.

This module is the ONLY place that imports `langfuse`. Every other module uses
the small exported surface here, so business code never couples to Langfuse:

- `observation()` — nested span/agent/generation/tool observations (no-op when
  tracing is disabled).
- `propagate_attributes()` — PRD 26 shared context on the active span.
- `get_trace_id()` / `get_span_id()` — PRD 14 SSE correlation.
- `flush_telemetry()` — PRD 22 best-effort flush at shutdown.

Design constraints honored:
- Messaging "tracing enabled" is a config decision (PRD 19/20), never business logic.
- Langfuse is imported only after settings/env are loaded (PRD 40 / skill rule:
  never import before env vars exist).
- PRD 21: a Langfuse failure can never break a mission/agent/tool call — every
  entry is wrapped in try/except and business exceptions always propagate.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator

from app.core.config import Settings, get_settings

logger = logging.getLogger("acg.observability")

_client = None
_enabled = None


def _load_client():
    """Load the Langfuse (OTel) client. Idempotent; best-effort."""
    global _client, _enabled
    if _enabled is not None:
        return _client
    settings: Settings = get_settings()
    if not settings.langfuse_ready:
        logger.warning(
            "Langfuse not configured (langfuse_enabled=%s keys=%s) — observability disabled",
            settings.langfuse_enabled,
            bool(settings.langfuse_public_key and settings.langfuse_secret_key),
        )
        _enabled = False
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_base_url,
            environment=settings.langfuse_tracing_environment,
            # PRD 19: sample rate is config, never hard-coded.
            sample_rate=settings.langfuse_sample_rate,
        )
        _enabled = True
        logger.info(
            "observability enabled -> %s (env=%s rate=%.2f)",
            settings.langfuse_base_url,
            settings.langfuse_tracing_environment,
            settings.langfuse_sample_rate,
        )
    except Exception as exc:  # PRD 21: observability must never break the runtime.
        logger.warning("failed to init Langfuse; observability disabled: %s", exc)
        _enabled = False
        _client = None
    return _client


def _override_client(client=None, *, enabled_flag: bool = False) -> None:
    """TEST-ONLY seam: inject a fake tracing client (or force-disable).

    Lets observability tests exercise span nesting / failure semantics without
    Cloud credentials. Not used in production code paths.
    """
    global _client, _enabled
    _client = client
    _enabled = enabled_flag if client is not None else False


def enabled() -> bool:
    """True when tracing is active (credentials present + not disabled)."""
    _load_client()
    return bool(_enabled)


def _current(callable_name: str):
    """Accessor for langfuse.current_* helpers (best-effort)."""
    client = _load_client()
    if client is None:
        return None
    try:
        return getattr(client.current_observation, callable_name)(client)
    except Exception:
        return None


def get_trace_id() -> str | None:
    """Best-effort current trace id for SSE correlation (PRD 14)."""
    client = _load_client()
    if client is None:
        return None
    # Langfuse v4 exposes standalone get_trace_id(); older SDKs expose it on
    # current_observation. Try both defensively (best-effort only).
    try:
        import langfuse as _lf

        return _lf.get_trace_id() or None
    except Exception:
        pass
    return _current("get_trace_id")


def get_span_id() -> str | None:
    """Best-effort current span id for SSE correlation (PRD 14)."""
    client = _load_client()
    if client is None:
        return None
    try:
        import langfuse as _lf

        return _lf.get_span_id() or None
    except Exception:
        pass
    return _current("get_span_id")


def propagate_attributes(**attrs: Any) -> None:
    """Attach attributes to the current span (PRD 26). Best-effort."""
    client = _load_client()
    if client is None:
        return
    try:
        client.propagate_attributes(**attrs)  # OTel-native attribute propagation
    except Exception:
        pass


@contextmanager
def observation(
    *,
    name: str,
    as_type: str = "span",
    input: Any = None,
    **attrs: Any,
) -> Iterator[Any]:
    """Open a nested observation (span/generation/tool/agent) on the active span.

    No-op identity context manager when tracing is disabled.
    """
    client = _load_client()
    if client is None:
        yield None
        return
    # Best-effort: if creating/entering the observation itself fails (e.g.
    # Langfuse API drift), degrade to a no-op WITHOUT touching business code
    # (PRD 21/25).
    handle = None
    try:
        handle = client.start_as_current_observation(as_type=as_type, name=name)
        handle = handle.__enter__()
        handle.update(input=input, **attrs)
    except Exception as exc:
        logger.warning("trace observation %r failed best-effort: %s", name, exc)
        if handle is not None:
            try:
                handle.__exit__(None, None, None)
            except Exception:
                pass
        yield None
        return

    try:
        yield handle
    except BaseException as exc:
        # Business exception (e.g. LLMPermanentError). Mark the observation
        # failed best-effort, then RE-RAISE so runtime semantics are unchanged.
        try:
            handle.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            handle.__exit__(None, None, None)
        except Exception:
            pass


def flush_telemetry() -> None:
    """Best-effort flush of buffered traces (startup/shutdown + short scripts).

    PRD 22: export is async/batched; flush only at well-defined boundaries.
    """
    client = _load_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        pass


def traced(
    *,
    name: str | None = None,
    as_type: str = "span",
):
    """Decorator: wrap an async function in a nested observation.

    Name defaults to the function name. Extra attributes can be passed either
    at decoration time (static) or at call time via `_trace_attrs` kwarg.
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any):
            span_name = name or fn.__name__
            extra = kwargs.pop("_trace_attrs", {}) or {}
            with observation(name=span_name, as_type=as_type, **extra):
                return await fn(*args, **kwargs)

        return wrapper

    return decorator