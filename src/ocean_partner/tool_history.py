"""Repair interrupted LangChain tool exchanges before every model call."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

log = logging.getLogger(__name__)
_INTERRUPTED_RESULT = "Tool execution was interrupted before completion."


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _normalize_ids(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Give blank streamed call ids stable positional values and preserve pairing."""

    pending: list[str] = []
    normalized: list[AnyMessage] = []
    for message_index, message in enumerate(messages):
        if isinstance(message, AIMessage):
            pending.clear()
            calls: list[dict[str, object]] = []
            changed = False
            for call_index, call in enumerate(message.tool_calls):
                call_id = call.get("id")
                if _blank(call_id):
                    call_id = f"ocean_repair_{message_index}_{call_index}"
                    changed = True
                pending.append(str(call_id))
                calls.append({**call, "id": call_id})
            if changed:
                extra = dict(message.additional_kwargs)
                extra.pop("tool_calls", None)
                message = message.model_copy(
                    update={"tool_calls": calls, "additional_kwargs": extra}
                )
        elif isinstance(message, ToolMessage):
            if _blank(message.tool_call_id) and pending:
                message = message.model_copy(update={"tool_call_id": pending.pop(0)})
        else:
            pending.clear()
        normalized.append(message)
    return normalized


def repair_tool_history(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Close dangling calls and remove tool results whose call is absent."""

    repaired: list[AnyMessage] = []
    pending: dict[str, str | None] = {}

    def close_pending() -> None:
        for call_id, name in pending.items():
            repaired.append(
                ToolMessage(
                    content=_INTERRUPTED_RESULT,
                    tool_call_id=call_id,
                    name=name,
                    status="error",
                )
            )
        pending.clear()

    for message in _normalize_ids(messages):
        if isinstance(message, ToolMessage):
            if message.tool_call_id in pending:
                repaired.append(message)
                pending.pop(message.tool_call_id)
            continue
        if pending:
            close_pending()
        if isinstance(message, AIMessage):
            valid_calls = [
                call
                for call in message.tool_calls
                if isinstance(call.get("id"), str)
                and call.get("id")
                and isinstance(call.get("name"), str)
                and call.get("name")
            ]
            extra = dict(message.additional_kwargs)
            # Parsed calls are authoritative. Keeping a stale raw payload is
            # how blank ids and malformed function names reach strict APIs.
            extra.pop("tool_calls", None)
            message = message.model_copy(
                update={
                    "tool_calls": valid_calls,
                    "invalid_tool_calls": [],
                    "additional_kwargs": extra,
                }
            )
            pending.update(
                {str(call["id"]): str(call["name"]) for call in valid_calls}
            )
        repaired.append(message)
    if pending:
        close_pending()
    return repaired


class ToolHistoryRepairMiddleware(AgentMiddleware):
    """Apply provider-valid repair at the model boundary, including mid-run."""

    name = "ocean_tool_history_repair"

    def _modified(self, request: ModelRequest) -> ModelRequest:
        repaired = repair_tool_history(request.messages)
        if len(repaired) != len(request.messages):
            log.warning("Repaired an interrupted OceanMind tool exchange")
        return request.override(messages=repaired)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._modified(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._modified(request))


__all__ = ["ToolHistoryRepairMiddleware", "repair_tool_history"]
