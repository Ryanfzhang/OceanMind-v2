"""OceanMind-owned message and streaming contracts.

The desktop protocol must not depend on a particular agent harness.  Deep
Agents/LangGraph messages are translated into these small product-level types
at the runtime boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


class ConversationMessage(BaseModel):
    """Serializable, provider-neutral task memory exposed to OceanMind."""

    role: Literal["user", "assistant", "tool"]
    content: list[TextBlock | ToolUseBlock | ToolResultBlock] = Field(default_factory=list)

    @classmethod
    def from_user_text(cls, text: str) -> ConversationMessage:
        return cls(role="user", content=[TextBlock(text=text)])

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [block for block in self.content if isinstance(block, ToolUseBlock)]

    @classmethod
    def from_langchain(cls, message: BaseMessage) -> ConversationMessage:
        if isinstance(message, HumanMessage):
            role: Literal["user", "assistant", "tool"] = "user"
        elif isinstance(message, ToolMessage):
            role = "tool"
        else:
            role = "assistant"
        blocks: list[TextBlock | ToolUseBlock | ToolResultBlock] = []
        text = _message_text(message)
        if text:
            blocks.append(TextBlock(text=text))
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                blocks.append(
                    ToolUseBlock(
                        id=str(call.get("id") or ""),
                        name=str(call.get("name") or ""),
                        input=dict(call.get("args") or {}),
                    )
                )
        elif isinstance(message, ToolMessage):
            blocks.append(
                ToolResultBlock(
                    tool_use_id=str(message.tool_call_id),
                    content=text,
                    is_error=message.status == "error",
                )
            )
        return cls(role=role, content=blocks)


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            parts.append(str(block.get("text", "")))
    return "".join(parts)


@dataclass(frozen=True)
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class AssistantTextDelta:
    text: str
    turn_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class AssistantTurnComplete:
    message: ConversationMessage
    usage: UsageSnapshot
    turn_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class ToolExecutionStarted:
    tool_name: str
    tool_input: dict[str, Any]
    tool_call_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True)
class ToolExecutionCompleted:
    tool_name: str
    output: str
    is_error: bool = False
    tool_call_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    operation_id: str | None = None
    duration_seconds: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ErrorEvent:
    message: str
    recoverable: bool = True
    code: str = "unknown"
    retryable: bool | None = None


@dataclass(frozen=True)
class StatusEvent:
    message: str


@dataclass(frozen=True)
class CompactProgressEvent:
    phase: Literal[
        "hooks_start",
        "context_collapse_start",
        "context_collapse_end",
        "session_memory_start",
        "session_memory_end",
        "compact_start",
        "compact_retry",
        "compact_end",
        "compact_failed",
    ]
    trigger: Literal["auto", "manual", "reactive"]
    message: str | None = None
    attempt: int | None = None
    checkpoint: str | None = None
    metadata: dict[str, Any] | None = None


StreamEvent = (
    AssistantTextDelta
    | AssistantTurnComplete
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | ErrorEvent
    | StatusEvent
    | CompactProgressEvent
)


__all__ = [
    "AssistantTextDelta",
    "AssistantTurnComplete",
    "CompactProgressEvent",
    "ConversationMessage",
    "ErrorEvent",
    "StatusEvent",
    "StreamEvent",
    "TextBlock",
    "ToolExecutionCompleted",
    "ToolExecutionStarted",
    "ToolResultBlock",
    "ToolUseBlock",
    "UsageSnapshot",
]
