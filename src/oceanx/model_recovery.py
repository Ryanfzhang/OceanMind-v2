"""Bounded model-delivery recovery; never judges scientific completion."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import replace
from typing import Any

from oceanx.agent_contract import (
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)

log = logging.getLogger(__name__)
RETRY_DELAYS = (5, 15, 30, 60)
RECOVERABLE_MODEL_CODES = frozenset(
    {
        "network_failure",
        "provider_timeout",
        "provider_rate_limit",
        "provider_unavailable",
        "empty_model_response",
    }
)


def model_error_event(exc: Exception) -> ErrorEvent:
    """Classify provider failures without mistaking auth/validation for transport."""
    status = getattr(exc, "status_code", None)
    message = str(exc)
    lower = message.lower()
    name = type(exc).__name__.lower()
    # Only safe diagnostics: do not log exception bodies, credentials or URLs.
    cause = exc.__cause__ or exc.__context__
    log.warning(
        "Model failure type=%s status=%s cause=%s",
        type(exc).__name__,
        status,
        type(cause).__name__ if cause else None,
    )
    permanent = status in (401, 402, 403) or any(
        s in lower
        for s in (
            "invalid api key",
            "incorrect api key",
            "insufficient_quota",
            "insufficient balance",
            "authentication",
            "permission denied",
        )
    )
    if permanent:
        return ErrorEvent(
            message=message, code="model_configuration_error", retryable=False, recoverable=False
        )
    if status in (400, 404, 422):
        return ErrorEvent(
            message=message, code="model_request_error", retryable=False, recoverable=False
        )
    code = "model_error"
    if status == 429 or "ratelimit" in name or "rate limit" in lower:
        code = "provider_rate_limit"
    elif status == 408 or isinstance(exc, TimeoutError) or "timeout" in name:
        code = "provider_timeout"
    elif isinstance(status, int) and 500 <= status <= 599:
        code = "provider_unavailable"
    elif (
        isinstance(exc, ConnectionError)
        or "connection" in name
        or any(s in lower for s in ("connection", "incomplete response", "peer closed"))
    ):
        code = "network_failure"
    retry_after = None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    try:
        value = float(headers.get("retry-after", ""))
        if math.isfinite(value) and value >= 0:
            retry_after = value
    except (TypeError, ValueError):
        pass
    return ErrorEvent(
        message=message,
        code=code,
        retryable=code in RECOVERABLE_MODEL_CODES,
        retry_after_seconds=retry_after,
    )


async def model_events(
    engine: Any, text: str, request_id: str, *, max_retries: int = len(RETRY_DELAYS)
):
    """Resume the same graph, with cancellable backoff and one total retry allowance."""
    retries = 0
    active_tools = 0
    final_delivered = False
    stream = engine.submit_message(text, request_id=request_id).__aiter__()
    try:
        while True:
            try:
                event = await anext(stream)
            except StopAsyncIteration:
                if final_delivered:
                    return
                event = ErrorEvent(
                    message="Model ended without delivering an answer or a pending tool call.",
                    code="empty_model_response",
                    retryable=True,
                )
            if isinstance(event, ToolExecutionStarted):
                active_tools += 1
                final_delivered = False
            elif isinstance(event, ToolExecutionCompleted):
                active_tools = max(0, active_tools - 1)
            elif isinstance(event, AssistantTurnComplete):
                final_delivered = bool(event.message.text.strip() and not event.message.tool_uses)
            if isinstance(event, ErrorEvent):
                resume = getattr(engine, "resume_message", None)
                transient = event.retryable is True and event.code in RECOVERABLE_MODEL_CODES
                delay = max(
                    RETRY_DELAYS[min(retries, len(RETRY_DELAYS) - 1)],
                    event.retry_after_seconds or 0,
                )
                if (
                    transient
                    and not active_tools
                    and retries < max_retries
                    and delay <= 300
                    and callable(resume)
                ):
                    retries += 1
                    await stream.aclose()
                    yield StatusEvent(
                        message=(
                            f"Model delivery interrupted ({event.code}); waiting {delay:g}s before "
                            f"resuming saved progress (retry {retries}/{max_retries})."
                        )
                    )
                    await asyncio.sleep(delay)
                    final_delivered = False
                    stream = resume(request_id=request_id).__aiter__()
                    continue
                if transient and callable(resume):
                    # Keep the task resumable, but never loop indefinitely or replay active tools.
                    event = replace(
                        event,
                        retries_exhausted=True,
                        retryable=False,
                        message=(
                            "Research paused; saved progress is retained. "
                            "Continue when the model service is available. "
                            f"Last delivery error: {event.message}"
                        ),
                    )
                yield event
                return
            yield event
    finally:
        await stream.aclose()
