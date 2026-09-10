"""Small, lossless-on-disk projection of older Expert code exchanges."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage


def _payload(message: ToolMessage) -> dict | None:
    if not isinstance(message.content, str):
        return None
    try:
        value = json.loads(message.content)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def compact_expert_history(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Project only older, durably saved code/logs; never edit checkpoint state.

    Preserve instructions, prose, candidate metadata, tool pairing and the last
    two complete code exchanges and latest failure. Full code and logs remain readable by path.
    Unknown/legacy result shapes without a recovery path are left untouched.
    """
    calls = {
        call["id"]: call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call.get("name") == "ocean_expert_run_code" and call.get("id")
    }
    completed = [
        message
        for message in messages
        if isinstance(message, ToolMessage)
        and message.tool_call_id in calls
        and (data := _payload(message)) is not None
        and data.get("execution_id")
        and data.get("state") not in {None, "running"}
    ]
    replacements: dict[str, dict] = {}
    results: dict[str, ToolMessage] = {}
    latest_failure = next(
        (
            message.tool_call_id
            for message in reversed(completed)
            if _payload(message).get("state") != "succeeded"
        ),
        None,
    )
    for message in completed[:-2]:
        # Diagnostic successes must not push the unresolved error out of view.
        if message.tool_call_id == latest_failure:
            continue
        data = _payload(message)
        logs = data.get("logs")
        logs = logs if isinstance(logs, dict) else {}
        saved_code = data.get("saved_code_path")
        # Older durable payloads predate saved_code_path but use the same layout.
        if not saved_code and isinstance(logs.get("stdout"), str):
            stdout_path = Path(logs["stdout"])
            if (
                stdout_path.parent.name == "logs"
                and stdout_path.parent.parent.name == data["execution_id"]
            ):
                saved_code = str(stdout_path.parent.parent / "code" / "analysis.py")
        if not isinstance(saved_code, str) or not saved_code:
            continue
        call_id = message.tool_call_id
        args = calls[call_id].get("args")
        if not isinstance(args, dict) or not isinstance(args.get("code"), str):
            continue
        replacement = (
            "# Older executed code omitted from model context (not for re-execution).\n"
            f"# Read the complete script with ocean_read_file: {saved_code}\n"
        )
        if len(args["code"]) > len(replacement):
            replacements[call_id] = {**args, "code": replacement}
        projected = dict(data)
        changed = False
        for stream in ("stdout", "stderr"):
            value = data.get(stream)
            # Never hide evidence unless the full stream has a retrieval path.
            if isinstance(value, str) and len(value) > 600 and logs.get(stream):
                projected[stream] = (
                    value[:240] + f"\n[older excerpt; full text: {logs[stream]}]\n" + value[-240:]
                )
                projected[f"{stream}_truncated"] = True
                changed = True
        if changed:
            results[call_id] = message.model_copy(update={"content": json.dumps(projected)})

    projected_messages: list[AnyMessage] = []
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id in results:
            message = results[message.tool_call_id]
        elif isinstance(message, AIMessage) and any(
            c.get("id") in replacements for c in message.tool_calls
        ):
            updated_calls = [
                {**call, "args": replacements[call["id"]]}
                if call.get("id") in replacements
                else call
                for call in message.tool_calls
            ]
            content = message.content
            if isinstance(content, list):
                # Anthropic/native and normalized content blocks may duplicate
                # parsed tool_calls. Update copies so the old code cannot leak.
                content = [
                    {
                        **block,
                        ("input" if block.get("type") == "tool_use" else "args"): replacements[
                            block["id"]
                        ],
                    }
                    if isinstance(block, dict)
                    and block.get("id") in replacements
                    and block.get("type") in {"tool_use", "tool_call"}
                    else block
                    for block in content
                ]
            extra = dict(message.additional_kwargs)
            extra.pop("tool_calls", None)  # Parsed calls are authoritative.
            message = message.model_copy(
                update={
                    "tool_calls": updated_calls,
                    "content": content,
                    "additional_kwargs": extra,
                }
            )
        projected_messages.append(message)
    return projected_messages


class ExpertContextMiddleware(AgentMiddleware):
    """Apply the projection on every call, including checkpoint resume."""

    name = "ocean_expert_context"

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(request.override(messages=compact_expert_history(request.messages)))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(request.override(messages=compact_expert_history(request.messages)))
