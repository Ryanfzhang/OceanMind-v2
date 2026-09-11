"""Deep Agents + LangGraph runtime used by every OceanMind participant."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from oceanx.agent_contract import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ConversationMessage,
    ErrorEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    UsageSnapshot,
    _message_text,
)
from oceanx.agent_tools import ToolRegistry
from oceanx.context_summary import SUMMARY_TAG, build_context_summary, context_archive_reader
from oceanx.expert_context import ExpertContextMiddleware
from oceanx.model_config import OceanModelProfile, create_chat_model
from oceanx.model_recovery import model_error_event
from oceanx.storage import OceanPaths
from oceanx.tool_history import ToolHistoryRepairMiddleware

TOKEN_WIND_DOWN_RATIO = 0.85
log = logging.getLogger(__name__)
_TOKEN_WIND_DOWN_INSTRUCTION = """# Agent token-budget wind-down

This agent run has entered its delivery reserve. Stop open-ended exploration. Use another tool only
when it directly repairs the latest concrete error or saves a specifically requested missing output.
Otherwise finish now from durable evidence: state the supported conclusion, cite the saved outputs,
and make every unresolved limitation explicit. Do not start a broader replacement analysis.
For a Coordinator, summarize accepted evidence and unfinished work without claiming the research is
complete. A budget limit is not evidence for any hypothesis or a reason to force terminal tree states.
"""
_TOKEN_BUDGET_FINAL = (
    "This agent run reached its token budget. Any saved executions and outputs are partial evidence, "
    "not a completed research conclusion. Continue only the unresolved work in a follow-up."
)


def _disable_generic_deep_agent_tools(provider: str) -> None:
    """Ocean agents receive domain tools only, never generic shell/file probes."""

    register_harness_profile(
        provider,
        HarnessProfile(
            excluded_tools=frozenset(
                {
                    "ls",
                    "read_file",
                    "write_file",
                    "edit_file",
                    "delete",
                    "glob",
                    "grep",
                    "execute",
                    "task",
                }
            ),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )


def _ai_message(value: Any) -> AIMessage | None:
    if isinstance(value, AIMessage):
        return value
    if isinstance(value, dict):
        for key in ("output", "message"):
            if isinstance(value.get(key), AIMessage):
                return value[key]
        generations = value.get("generations")
        if isinstance(generations, list):
            for generation in generations:
                if isinstance(generation, list):
                    for item in generation:
                        message = getattr(item, "message", None)
                        if isinstance(message, AIMessage):
                            return message
                message = getattr(generation, "message", None)
                if isinstance(message, AIMessage):
                    return message
    message = getattr(value, "message", None)
    return message if isinstance(message, AIMessage) else None


def _usage(message: AIMessage) -> UsageSnapshot:
    usage = message.usage_metadata or {}
    return UsageSnapshot(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )


class TokenBudgetWindDownMiddleware(AgentMiddleware):
    """Let the model finish normally, with a token-metered delivery reserve.

    The ordinary Deep Agent stop condition remains an assistant response without tool calls. This
    middleware only steers the next model turn once either existing input/output ceiling reaches 85%,
    then provides a no-tool partial handoff if a later turn crosses the hard ceiling. Resource, wall,
    and graph-step limits remain independent safety backstops.
    """

    name = "ocean_token_budget_wind_down"

    def __init__(
        self,
        *,
        max_input_tokens: int,
        max_output_tokens: int,
        wind_down_ratio: float = TOKEN_WIND_DOWN_RATIO,
    ) -> None:
        self.max_input_tokens = max(0, int(max_input_tokens))
        self.max_output_tokens = max(0, int(max_output_tokens))
        self.wind_down_ratio = min(1.0, max(0.0, float(wind_down_ratio)))
        self.input_tokens = 0
        self.output_tokens = 0

    def _hard_limit_reached(self) -> bool:
        return (
            self.max_input_tokens > 0
            and self.input_tokens >= self.max_input_tokens
        ) or (
            self.max_output_tokens > 0
            and self.output_tokens >= self.max_output_tokens
        )

    def _wind_down_reached(self) -> bool:
        return (
            self.max_input_tokens > 0
            and self.input_tokens >= self.max_input_tokens * self.wind_down_ratio
        ) or (
            self.max_output_tokens > 0
            and self.output_tokens >= self.max_output_tokens * self.wind_down_ratio
        )

    def _record(self, response: ModelResponse) -> None:
        for message in response.result:
            if not isinstance(message, AIMessage):
                continue
            usage = _usage(message)
            self.record_usage(usage)

    def record_usage(self, usage: UsageSnapshot) -> None:
        """Include internal summary calls in the same cumulative token ceiling."""
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    @staticmethod
    def _wind_down_request(request: ModelRequest) -> ModelRequest:
        system = request.system_message
        if system is None:
            return request.override(
                system_message=SystemMessage(content=_TOKEN_WIND_DOWN_INSTRUCTION)
            )
        content = system.content
        base = content if isinstance(content, str) else str(content)
        return request.override(
            system_message=system.model_copy(
                update={"content": f"{base}\n\n{_TOKEN_WIND_DOWN_INSTRUCTION}"}
            )
        )

    def _before_call(self, request: ModelRequest) -> tuple[ModelRequest, ModelResponse | None]:
        if self._hard_limit_reached():
            return request, ModelResponse(result=[AIMessage(
                content=_TOKEN_BUDGET_FINAL,
                additional_kwargs={"oceanx_budget_exhausted": True},
            )])
        if self._wind_down_reached():
            return self._wind_down_request(request), None
        return request, None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        request, terminal = self._before_call(request)
        if terminal is not None:
            return terminal
        response = handler(request)
        self._record(response)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        request, terminal = self._before_call(request)
        if terminal is not None:
            return terminal
        response = await handler(request)
        self._record(response)
        return response


def _tool_output(value: Any) -> tuple[str, bool]:
    status = getattr(value, "status", None)
    content = getattr(value, "content", value)
    if isinstance(content, str):
        text = content
    else:
        text = str(content)
    return text, status == "error"


class DeepAgentEngine:
    """Product gateway around one checkpointed LangGraph thread."""

    def __init__(
        self,
        *,
        graph: Any,
        thread_id: str,
        system_prompt: str,
        max_turns: int,
        operation_id_factory: Any,
        summary_usage_callback: Callable[[UsageSnapshot], None] | None = None,
    ) -> None:
        self.graph = graph
        self.thread_id = thread_id
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.operation_id_factory = operation_id_factory
        self._summary_usage_callback = summary_usage_callback
        self._messages: list[ConversationMessage] = []
        self._seed_messages: list[BaseMessage] = []
        self._model_call_state_hook: Callable[[bool], None] | None = None
        self._turn_context = ""
        self._stream_turn_index = 0
        self._empty_response_pending = False

    @property
    def messages(self) -> list[ConversationMessage]:
        return [message.model_copy(deep=True) for message in self._messages]

    @property
    def compaction_generation(self) -> int:
        # LangGraph owns checkpoint generations. This compatibility value is
        # retained only for the existing task metadata envelope.
        return 0

    def set_max_turns(self, max_turns: int | None) -> None:
        if max_turns is not None:
            self.max_turns = max(1, int(max_turns))

    def set_system_prompt(self, prompt: str) -> None:
        # The authored policy is immutable; the backend may refresh a bounded
        # workspace context for the next user turn.
        self._turn_context = (
            prompt[len(self.system_prompt) :].strip()
            if prompt.startswith(self.system_prompt)
            else prompt.strip()
        )

    def set_ask_user_prompt(self, _prompt: Any) -> None:
        return None

    def set_permission_checker(self, _checker: Any) -> None:
        return None

    def set_permission_prompt(self, _prompt: Any) -> None:
        return None

    def set_final_response_guard(self, _guard: Any) -> None:
        return None

    def set_model_call_state_hook(self, hook: Callable[[bool], None] | None) -> None:
        self._model_call_state_hook = hook

    def has_pending_continuation(self) -> bool:
        if not self._messages:
            return False
        last = self._messages[-1]
        return last.role == "assistant" and bool(last.tool_uses)

    def load_messages(
        self,
        messages: list[ConversationMessage],
        *,
        compaction_generation: int = 0,
    ) -> None:
        del compaction_generation
        # Old task checkpoints are imported as text-only context. Provider
        # tool envelopes are deliberately not replayed into the new graph.
        self._messages = [message.model_copy(deep=True) for message in messages]
        self._seed_messages = []
        for message in messages:
            if not message.text.strip():
                continue
            if message.role == "user":
                self._seed_messages.append(HumanMessage(content=message.text))
            elif message.role == "assistant":
                self._seed_messages.append(AIMessage(content=message.text))

    def _config(self, request_id: str | None) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": self.thread_id,
                "request_id": request_id or self.thread_id,
            },
            # Each model/tool cycle consumes several graph steps. max_turns is
            # a model-turn budget, so give the graph bounded internal headroom.
            "recursion_limit": max(12, self.max_turns * 6),
        }

    async def _input_messages(self, config: dict[str, Any], text: str) -> list[BaseMessage]:
        snapshot = await self.graph.aget_state(config)
        existing = snapshot.values.get("messages", ()) if snapshot.values else ()
        prefix = self._seed_messages if not existing else []
        self._seed_messages = []
        contextual_text = (
            f"{self._turn_context}\n\n# Current researcher request\n{text}"
            if self._turn_context
            else text
        )
        self._turn_context = ""
        return [*prefix, HumanMessage(content=contextual_text)]

    async def _refresh_messages(self, config: dict[str, Any]) -> BaseMessage | None:
        snapshot = await self.graph.aget_state(config)
        raw = snapshot.values.get("messages", ()) if snapshot.values else ()
        self._messages = [
            ConversationMessage.from_langchain(message)
            for message in raw
            if isinstance(message, BaseMessage)
        ]
        return raw[-1] if raw and isinstance(raw[-1], BaseMessage) else None

    async def submit_message(
        self, text: str, *, request_id: str | None = None, _resume: bool = False
    ) -> AsyncIterator[StreamEvent]:
        config = self._config(request_id)
        if _resume:
            snapshot = await self.graph.aget_state(config)
            if not snapshot.next:
                if not self._empty_response_pending:
                    yield ErrorEvent(message="No pending model checkpoint to resume", code="model_error", retryable=False)
                    return
                # An empty terminal AI message leaves no graph node pending. A bounded delivery
                # reminder resumes reasoning from saved messages, never the original tool calls.
                inputs = {"messages": [HumanMessage(content=(
                    "[Runtime delivery recovery] The previous model response ended without an "
                    "answer. Continue the pending assignment from the saved conversation and tool "
                    "results. Reuse completed work; do not repeat successful tool calls. Decide "
                    "whether further work is needed or deliver the evidence-bound answer, including "
                    "unresolved limitations. This reminder grants no new authority and does not "
                    "change the researcher's requested language."
                ))]}
            else:
                inputs = None
        else:
            inputs = {"messages": await self._input_messages(config, text)}
            self._stream_turn_index = 0
        pending_calls: deque[dict[str, Any]] = deque()
        tool_started: dict[str, tuple[float, str, str | None]] = {}
        opaque_tool_runs: set[str] = set()
        model_turns: dict[str, str] = {}
        completed_model_runs: set[str] = set()
        turn_index = self._stream_turn_index
        last_model_message: AIMessage | None = None
        self._empty_response_pending = False
        try:
            async for event in self.graph.astream_events(inputs, config=config, version="v2"):
                name = str(event.get("event") or "")
                run_id = str(event.get("run_id") or "")
                parent_ids = {
                    str(parent_id)
                    for parent_id in (event.get("parent_ids") or ())
                    if parent_id
                }
                # A tool is an event boundary. In particular, ``ocean_assign``
                # runs another DeepAgent graph internally. LangGraph exposes
                # those child model/tool events in the same v2 event stream,
                # but they are the tool's private implementation rather than
                # turns made by this agent. Project only the outer tool receipt
                # into the parent stream.
                if parent_ids & opaque_tool_runs:
                    continue
                data = event.get("data") or {}
                is_summary = (
                    SUMMARY_TAG in (event.get("tags") or ())
                    or (event.get("metadata") or {}).get("lc_source") == "summarization"
                )
                if name == "on_chat_model_start":
                    turn_index += 1
                    self._stream_turn_index = turn_index
                    turn_id = f"{request_id or self.thread_id}:turn:{turn_index}"
                    model_turns[run_id] = turn_id
                    if self._model_call_state_hook is not None:
                        self._model_call_state_hook(True)
                    continue
                if name == "on_chat_model_stream":
                    if is_summary:
                        continue
                    chunk = data.get("chunk")
                    if isinstance(chunk, AIMessageChunk):
                        delta = _message_text(chunk)
                        if delta:
                            yield AssistantTextDelta(
                                text=delta,
                                turn_id=model_turns.get(run_id),
                                request_id=request_id,
                            )
                    continue
                if name == "on_chat_model_end":
                    if run_id in completed_model_runs:
                        continue
                    completed_model_runs.add(run_id)
                    if self._model_call_state_hook is not None:
                        self._model_call_state_hook(False)
                    message = _ai_message(data.get("output"))
                    if message is None:
                        continue
                    if is_summary:
                        usage = _usage(message)
                        if self._summary_usage_callback is not None:
                            self._summary_usage_callback(usage)
                        # Meter internal work without presenting its prose as a
                        # research answer, tool request, or final response.
                        yield AssistantTurnComplete(
                            message=ConversationMessage.from_langchain(AIMessage(content="")),
                            usage=usage,
                            turn_id=model_turns.get(run_id),
                            request_id=request_id,
                        )
                        continue
                    last_model_message = message
                    log.info("Model response request=%s turn=%s finish=%s text_chars=%s tools=%s",
                             request_id, turn_index,
                             message.response_metadata.get("finish_reason") or
                             message.response_metadata.get("stop_reason"),
                             len(_message_text(message)), len(message.tool_calls))
                    turn_id = model_turns.get(run_id) or f"{request_id or self.thread_id}:turn:{turn_index}"
                    for call in message.tool_calls:
                        pending_calls.append(
                            {
                                "id": str(call.get("id") or ""),
                                "name": str(call.get("name") or ""),
                                "args": dict(call.get("args") or {}),
                                "turn_id": turn_id,
                            }
                        )
                    yield AssistantTurnComplete(
                        message=ConversationMessage.from_langchain(message),
                        usage=_usage(message),
                        turn_id=turn_id,
                        request_id=request_id,
                    )
                    continue
                if name == "on_tool_start":
                    opaque_tool_runs.add(run_id)
                    tool_name = str(event.get("name") or "")
                    call = next(
                        (item for item in pending_calls if item["name"] == tool_name),
                        None,
                    )
                    if call is not None:
                        pending_calls.remove(call)
                    else:
                        call = {
                            "id": run_id,
                            "name": tool_name,
                            "args": dict(data.get("input") or {}),
                            "turn_id": f"{request_id or self.thread_id}:turn:{turn_index}",
                        }
                    call_id = call["id"] or run_id
                    operation_id = self.operation_id_factory(
                        request_id or self.thread_id, "langgraph", call_id
                    )
                    tool_started[run_id] = (time.monotonic(), call_id, operation_id)
                    yield ToolExecutionStarted(
                        tool_name=tool_name,
                        tool_input=dict(call["args"]),
                        tool_call_id=call_id,
                        turn_id=call["turn_id"],
                        request_id=request_id,
                        operation_id=operation_id,
                    )
                    continue
                if name in {"on_tool_end", "on_tool_error"}:
                    tool_name = str(event.get("name") or "")
                    started, call_id, operation_id = tool_started.pop(
                        run_id, (time.monotonic(), run_id, run_id)
                    )
                    output, status_error = _tool_output(
                        data.get("output") if name == "on_tool_end" else data.get("error")
                    )
                    yield ToolExecutionCompleted(
                        tool_name=tool_name,
                        output=output,
                        is_error=name == "on_tool_error" or status_error,
                        tool_call_id=call_id,
                        turn_id=f"{request_id or self.thread_id}:turn:{turn_index}",
                        request_id=request_id,
                        operation_id=operation_id,
                        duration_seconds=max(0.0, time.monotonic() - started),
                    )
            final_message = await self._refresh_messages(config)
            if (isinstance(final_message, AIMessage)
                    and final_message.additional_kwargs.get("oceanx_budget_exhausted") is True):
                self._empty_response_pending = False
                yield ErrorEvent(message=_message_text(final_message),
                                 code="budget_exhausted", retryable=False)
                return
            if (last_model_message is None or not _message_text(last_model_message).strip()
                    or last_model_message.tool_calls):
                metadata = last_model_message.response_metadata if last_model_message else {}
                extra = last_model_message.additional_kwargs if last_model_message else {}
                finish = metadata.get("finish_reason") or metadata.get("stop_reason")
                log.warning("Missing model answer request=%s finish=%s tool_calls=%s",
                            request_id, finish,
                            len(last_model_message.tool_calls) if last_model_message else 0)
                if finish in {"length", "max_tokens", "content_filter", "refusal"} or extra.get("refusal"):
                    yield ErrorEvent(message=f"Model did not deliver an answer (finish_reason={finish or 'refusal'}).",
                                     code="model_output_error", retryable=False)
                else:
                    self._empty_response_pending = True
                    yield ErrorEvent(message="Model stream ended without a final answer.",
                                     code="empty_model_response", retryable=True)
        except Exception as exc:  # noqa: BLE001 - provider adapters have no common error base
            if self._model_call_state_hook is not None:
                self._model_call_state_hook(False)
            await self._refresh_messages(config)
            yield model_error_event(exc)

    def resume_message(self, *, request_id: str | None = None) -> AsyncIterator[StreamEvent]:
        """Resume the pending graph node without appending another user message."""
        return self.submit_message("", request_id=request_id, _resume=True)


async def build_deep_agent_engine(
    *,
    profile: OceanModelProfile,
    tools: ToolRegistry,
    system_prompt: str,
    cwd: Path,
    thread_id: str,
    max_turns: int,
    max_input_tokens: int = 0,
    max_output_tokens: int = 0,
    operation_id_factory: Any,
) -> tuple[DeepAgentEngine, Callable[[], Awaitable[None]]]:
    """Build a checkpointed domain-only Deep Agent graph."""

    _disable_generic_deep_agent_tools(profile.provider)
    model = create_chat_model(profile)
    state_backend = StateBackend()
    # Replacing the built-in filesystem middleware with an empty tool list is
    # intentional: all source reads and code execution go through audited Ocean
    # domain services instead of dozens of exploratory file/shell calls.
    # Deep Agents requires ``read_file`` as filesystem scaffolding. The Ocean
    # harness profile excludes it from the model-visible tool set, so no generic
    # source probing is possible.
    filesystem = FilesystemMiddleware(backend=state_backend, tools=["read_file"])
    checkpoint_path = OceanPaths.for_project(cwd).root / "langgraph.sqlite3"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_context: AbstractAsyncContextManager[AsyncSqliteSaver] = (
        AsyncSqliteSaver.from_conn_string(str(checkpoint_path))
    )
    checkpointer = await checkpoint_context.__aenter__()
    await checkpointer.conn.execute("PRAGMA journal_mode=WAL")
    await checkpointer.conn.execute("PRAGMA busy_timeout=30000")
    await checkpointer.setup()
    middleware: list[AgentMiddleware] = [
        filesystem,
        build_context_summary(model, state_backend),
        ExpertContextMiddleware(),
        ToolHistoryRepairMiddleware(),
    ]
    token_budget = None
    if max_input_tokens > 0 or max_output_tokens > 0:
        token_budget = TokenBudgetWindDownMiddleware(
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
        )
        middleware.append(token_budget)
    graph = create_deep_agent(
        model=model,
        tools=[
            *tools.as_langchain_tools(cwd=cwd, operation_id_factory=operation_id_factory),
            context_archive_reader(state_backend),
        ],
        system_prompt=system_prompt,
        middleware=middleware,
        subagents=[],
        backend=state_backend,
        checkpointer=checkpointer,
        name="oceanmind",
    )
    engine = DeepAgentEngine(
        graph=graph,
        thread_id=thread_id,
        system_prompt=system_prompt,
        max_turns=max_turns,
        operation_id_factory=operation_id_factory,
        summary_usage_callback=token_budget.record_usage if token_budget is not None else None,
    )

    async def close() -> None:
        await checkpoint_context.__aexit__(None, None, None)

    return engine, close


__all__ = [
    "TOKEN_WIND_DOWN_RATIO",
    "DeepAgentEngine",
    "TokenBudgetWindDownMiddleware",
    "build_deep_agent_engine",
]
