from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from ocean_partner.agent_contract import (
    AssistantTurnComplete,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from ocean_partner.agent_tools import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from ocean_partner.deep_runtime import (
    DeepAgentEngine,
    TokenBudgetWindDownMiddleware,
    _disable_generic_deep_agent_tools,
    build_deep_agent_engine,
)
from ocean_partner.model_config import OceanModelProfile
from ocean_partner.tool_history import ToolHistoryRepairMiddleware, repair_tool_history


class _BoundFakeModel(FakeMessagesListChatModel):
    bound_tool_names: list[tuple[str, ...]] = Field(default_factory=list)
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **_kwargs: Any):
        self.bound_tool_names.append(
            tuple(
                tool.name if hasattr(tool, "name") else str(tool.get("name", ""))
                for tool in tools
            )
        )
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen_messages.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class _IncrementInput(BaseModel):
    value: int


class _IncrementTool(BaseTool):
    name = "ocean_increment"
    description = "Increment one test value."
    input_model = _IncrementInput

    def __init__(self) -> None:
        self.contexts: list[ToolExecutionContext] = []

    async def execute(
        self, arguments: _IncrementInput, context: ToolExecutionContext
    ) -> ToolResult:
        self.contexts.append(context)
        return ToolResult(output=str(arguments.value + 1))


class _NestedToolEventGraph:
    """Minimal v2 event stream with a child agent running inside one tool."""

    async def aget_state(self, _config: dict[str, Any]) -> Any:
        return type("Snapshot", (), {"values": {"messages": ()}})()

    async def astream_events(
        self,
        _inputs: dict[str, Any],
        *,
        config: dict[str, Any],
        version: str,
    ):
        del config
        assert version == "v2"
        coordinator_tool_call = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ocean_assign",
                    "args": {"expert": "data"},
                    "id": "call-assign",
                    "type": "tool_call",
                }
            ],
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
            },
        )
        expert_private_answer = AIMessage(
            content="Expert private answer that must not become Coordinator prose.",
            usage_metadata={
                "input_tokens": 900_000,
                "output_tokens": 90_000,
                "total_tokens": 990_000,
            },
        )
        coordinator_final = AIMessage(
            content="Coordinator final answer.",
            usage_metadata={
                "input_tokens": 200,
                "output_tokens": 20,
                "total_tokens": 220,
            },
        )
        events = (
            {
                "event": "on_chat_model_start",
                "run_id": "coordinator-model-1",
                "parent_ids": ["root-graph"],
                "data": {},
            },
            {
                "event": "on_chat_model_end",
                "run_id": "coordinator-model-1",
                "parent_ids": ["root-graph"],
                "data": {"output": coordinator_tool_call},
            },
            {
                "event": "on_tool_start",
                "name": "ocean_assign",
                "run_id": "assign-run",
                "parent_ids": ["root-graph"],
                "data": {"input": {"expert": "data"}},
            },
            {
                "event": "on_chat_model_start",
                "run_id": "expert-model",
                "parent_ids": ["root-graph", "assign-run"],
                "data": {},
            },
            {
                "event": "on_chat_model_end",
                "run_id": "expert-model",
                "parent_ids": ["root-graph", "assign-run"],
                "data": {"output": expert_private_answer},
            },
            {
                "event": "on_tool_start",
                "name": "expert_private_tool",
                "run_id": "expert-tool-run",
                "parent_ids": ["root-graph", "assign-run", "expert-model"],
                "data": {"input": {"path": "private"}},
            },
            {
                "event": "on_tool_end",
                "name": "expert_private_tool",
                "run_id": "expert-tool-run",
                "parent_ids": ["root-graph", "assign-run", "expert-model"],
                "data": {"output": "private output"},
            },
            {
                "event": "on_tool_end",
                "name": "ocean_assign",
                "run_id": "assign-run",
                "parent_ids": ["root-graph"],
                "data": {"output": "durable ExpertResult receipt"},
            },
            {
                "event": "on_chat_model_start",
                "run_id": "coordinator-model-2",
                "parent_ids": ["root-graph"],
                "data": {},
            },
            {
                "event": "on_chat_model_end",
                "run_id": "coordinator-model-2",
                "parent_ids": ["root-graph"],
                "data": {"output": coordinator_final},
            },
        )
        for event in events:
            yield event


def test_tool_history_repair_closes_dangling_calls_and_drops_orphans() -> None:
    repaired = repair_tool_history(
        [
            HumanMessage(content="start"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "ocean_increment", "args": {"value": 1}, "id": "call-1"}
                ],
            ),
            HumanMessage(content="continue after interruption"),
            ToolMessage(content="orphan", tool_call_id="missing-call"),
        ]
    )
    synthetic = [message for message in repaired if isinstance(message, ToolMessage)]
    assert len(synthetic) == 1
    assert synthetic[0].tool_call_id == "call-1"
    assert synthetic[0].status == "error"


def test_token_budget_enters_wind_down_then_returns_a_hard_limit_handoff() -> None:
    model = _BoundFakeModel(responses=[AIMessage(content="unused")])
    middleware = TokenBudgetWindDownMiddleware(
        max_input_tokens=100,
        max_output_tokens=100,
    )
    request = ModelRequest(model=model, messages=[], system_prompt="Base expert policy")
    first = ModelResponse(
        result=[
            AIMessage(
                content="continue",
                usage_metadata={
                    "input_tokens": 85,
                    "output_tokens": 10,
                    "total_tokens": 95,
                },
            )
        ]
    )
    middleware.wrap_model_call(request, lambda _request: first)

    observed: list[ModelRequest] = []
    second = ModelResponse(
        result=[
            AIMessage(
                content="final",
                usage_metadata={
                    "input_tokens": 15,
                    "output_tokens": 5,
                    "total_tokens": 20,
                },
            )
        ]
    )
    response = middleware.wrap_model_call(
        request,
        lambda modified: observed.append(modified) or second,
    )
    assert response is second
    assert observed
    assert "delivery reserve" in str(observed[0].system_message.content)

    handler_called = False

    def unexpected_handler(_request: ModelRequest) -> ModelResponse:
        nonlocal handler_called
        handler_called = True
        return second

    terminal = middleware.wrap_model_call(request, unexpected_handler)
    assert not handler_called
    assert "reached its token budget" in str(terminal.result[0].content)


@pytest.mark.asyncio
async def test_nested_agent_events_inside_a_tool_do_not_leak_into_parent_stream() -> None:
    operation = lambda request_id, turn_id, call_id: f"{request_id}:{turn_id}:{call_id}"
    engine = DeepAgentEngine(
        graph=_NestedToolEventGraph(),
        thread_id="coordinator-thread",
        system_prompt="Coordinator test",
        max_turns=4,
        operation_id_factory=operation,
    )

    events = [
        event
        async for event in engine.submit_message("coordinate", request_id="req-nested")
    ]

    assistant_turns = [
        event for event in events if isinstance(event, AssistantTurnComplete)
    ]
    assert len(assistant_turns) == 2
    assert [event.message.text for event in assistant_turns if event.message.text] == [
        "Coordinator final answer."
    ]
    assert sum(event.usage.input_tokens for event in assistant_turns) == 300
    assert [
        event.tool_name for event in events if isinstance(event, ToolExecutionStarted)
    ] == ["ocean_assign"]
    assert [
        event.tool_name for event in events if isinstance(event, ToolExecutionCompleted)
    ] == ["ocean_assign"]


@pytest.mark.asyncio
async def test_deep_agent_exposes_only_ocean_tools_and_returns_one_final_answer(
    tmp_path: Path,
) -> None:
    model = _BoundFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ocean_increment",
                        "args": {"value": 1},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The bounded result is 2."),
        ]
    )
    # Fake model provider identity is its llm type.
    _disable_generic_deep_agent_tools("_boundfakemodel")
    backend = StateBackend()
    registry = ToolRegistry()
    increment = _IncrementTool()
    registry.register(increment)
    operation = lambda request_id, turn_id, call_id: f"{request_id}:{turn_id}:{call_id}"
    graph = create_deep_agent(
        model=model,
        tools=registry.as_langchain_tools(cwd=tmp_path, operation_id_factory=operation),
        system_prompt="Ocean test",
        middleware=[
            FilesystemMiddleware(backend=backend, tools=["read_file"]),
            ToolHistoryRepairMiddleware(),
        ],
        subagents=[],
        backend=backend,
        checkpointer=MemorySaver(),
        name="ocean-test",
    )
    engine = DeepAgentEngine(
        graph=graph,
        thread_id="expert-thread",
        system_prompt="Ocean test",
        max_turns=4,
        operation_id_factory=operation,
    )

    events = [event async for event in engine.submit_message("run", request_id="req-1")]

    assert any(isinstance(event, ToolExecutionStarted) for event in events)
    assert any(isinstance(event, ToolExecutionCompleted) for event in events)
    final = [event for event in events if isinstance(event, AssistantTurnComplete)][-1]
    assert final.message.text == "The bounded result is 2."
    assert increment.contexts[0].tool_call_id == "call-1"
    assert increment.contexts[0].operation_id == "req-1:langgraph:call-1"
    assert model.bound_tool_names
    assert set(model.bound_tool_names[0]) == {"ocean_increment"}


@pytest.mark.asyncio
async def test_sqlite_checkpoint_resumes_the_same_expert_after_runtime_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend restart reuses graph state instead of replaying tool envelopes."""

    import ocean_partner.deep_runtime as runtime_module

    profile = OceanModelProfile(
        name="fixture",
        label="Fixture",
        provider="openai",
        model="fixture-model",
        base_url=None,
        credential_slot="fixture",
        api_key="fixture-key",
    )
    _disable_generic_deep_agent_tools("_boundfakemodel")
    registry = ToolRegistry()
    operation = lambda request_id, turn_id, call_id: f"{request_id}:{turn_id}:{call_id}"

    first_model = _BoundFakeModel(responses=[AIMessage(content="First persisted answer.")])
    monkeypatch.setattr(runtime_module, "create_chat_model", lambda _profile: first_model)
    first, close_first = await build_deep_agent_engine(
        profile=profile,
        tools=registry,
        system_prompt="Ocean persistence test",
        cwd=tmp_path,
        thread_id="expert:persistent",
        max_turns=4,
        operation_id_factory=operation,
    )
    first_events = [
        event async for event in first.submit_message("first request", request_id="req-1")
    ]
    assert [
        event.message.text
        for event in first_events
        if isinstance(event, AssistantTurnComplete)
    ][-1] == "First persisted answer."
    await close_first()

    second_model = _BoundFakeModel(responses=[AIMessage(content="Second persisted answer.")])
    monkeypatch.setattr(runtime_module, "create_chat_model", lambda _profile: second_model)
    second, close_second = await build_deep_agent_engine(
        profile=profile,
        tools=registry,
        system_prompt="Ocean persistence test",
        cwd=tmp_path,
        thread_id="expert:persistent",
        max_turns=4,
        operation_id_factory=operation,
    )
    try:
        second_events = [
            event async for event in second.submit_message("second request", request_id="req-2")
        ]
        assert [
            event.message.text
            for event in second_events
            if isinstance(event, AssistantTurnComplete)
        ][-1] == "Second persisted answer."
        observed = second_model.seen_messages[-1]
        assert any(
            isinstance(message, AIMessage) and message.content == "First persisted answer."
            for message in observed
        )
        assert any(
            isinstance(message, HumanMessage) and message.content == "second request"
            for message in observed
        )
    finally:
        await close_second()
