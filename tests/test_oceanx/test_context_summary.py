import pytest
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import Field

from oceanx.agent_contract import AssistantTextDelta, AssistantTurnComplete
from oceanx.agent_tools import ToolRegistry
from oceanx.context_summary import (
    SUMMARY_MAX_OUTPUT_TOKENS,
    SUMMARY_PROMPT,
    SUMMARY_TAG,
    build_context_summary,
    context_archive_reader,
)
from oceanx.deep_runtime import build_deep_agent_engine
from oceanx.model_config import OceanModelProfile


class _SummaryFakeModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        if "OceanX scientific continuity requirements:" in str(messages):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="PRIVATE WORKING MEMORY: arithmetic peak 3.29 mg/m3 in July; "
                            "harmonic phase is a different estimand. Saved candidate /outputs/chl.nc; "
                            "not published. Next: resolve coverage uncertainty.",
                            usage_metadata={
                                "input_tokens": 700,
                                "output_tokens": 100,
                                "total_tokens": 800,
                            },
                        )
                    )
                ]
            )
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_summary_uses_same_model_with_earlier_threshold_and_bounded_output():
    model = ChatOpenAI(model="deepseek-v4-pro", api_key="fixture", max_tokens=64000)
    middleware = build_context_summary(model, StateBackend())
    assert middleware.name == "SummarizationMiddleware"
    assert middleware.model.model_name == model.model_name
    assert middleware.model.max_tokens == SUMMARY_MAX_OUTPUT_TOKENS
    assert model.max_tokens == 64000  # main model stays unchanged
    assert SUMMARY_TAG in middleware.model.tags
    assert middleware._lc_helper.trim_tokens_to_summarize is None
    short = [HumanMessage(content="x") for _ in range(6)]
    enough = [HumanMessage(content="x") for _ in range(12)]
    assert not middleware._should_summarize(short, 25000)
    assert not middleware._should_summarize(enough, 23000)
    assert middleware._should_summarize(enough, 25000)
    assert "averaging order" in SUMMARY_PROMPT
    assert "hypothesis/Test IDs" in SUMMARY_PROMPT


def test_small_model_window_keeps_safety_trigger():
    model = ChatOpenAI(model="fixture", api_key="fixture", profile={"max_input_tokens": 16000})
    middleware = build_context_summary(model, StateBackend())
    assert middleware._should_summarize([HumanMessage(content="x")] * 8, 11000)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"path": "/etc/passwd"},
        {"path": "/conversation_history/../secrets"},
        {"path": "/conversation_history/a", "offset": -1},
        {"path": "/conversation_history/a", "limit": 12001},
    ],
)
async def test_archive_reader_rejects_invalid_scope_before_backend_access(args):
    # Bare StateBackend cannot operate outside a graph: this also proves that
    # rejected requests do not touch storage at all.
    result = await context_archive_reader(StateBackend()).ainvoke(args)
    assert "error" in result


@pytest.mark.asyncio
async def test_summary_is_private_metered_and_archive_survives_sqlite_resume(tmp_path, monkeypatch):
    import oceanx.context_summary as summary_module
    import oceanx.deep_runtime as runtime_module

    monkeypatch.setattr(summary_module, "SUMMARY_TRIGGER_TOKENS", 2000)
    profile = OceanModelProfile(
        name="fixture",
        label="Fixture",
        provider="openai",
        model="fixture",
        base_url=None,
        credential_slot="fixture",
        api_key="fixture",
    )
    model = _SummaryFakeModel(responses=[AIMessage(content="Public research answer.")])
    monkeypatch.setattr(runtime_module, "create_chat_model", lambda _: model)
    runtime_module._disable_generic_deep_agent_tools("_summaryfakemodel")
    engine, close = await build_deep_agent_engine(
        profile=profile,
        tools=ToolRegistry(),
        system_prompt="Scientific fixture",
        cwd=tmp_path,
        thread_id="expert:summary",
        max_turns=10,
        max_input_tokens=100000,
        operation_id_factory=lambda *args: ":".join(args),
    )
    budget = engine._summary_usage_callback.__self__
    old = []
    for i in range(6):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        old.append(cls(content=f"old-{i} RAW_EVIDENCE 3.29 mg/m3 " + "old evidence " * 180))
    for i in range(6):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        old.append(cls(content=f"recent-{i} working script /analysis.py"))
    await engine.graph.aupdate_state(engine._config("r1"), {"messages": old})
    try:
        events = [e async for e in engine.submit_message("Continue the question", request_id="r1")]
        answers = [e for e in events if isinstance(e, AssistantTurnComplete)]
        assert answers, events
        assert answers[-1].message.text == "Public research answer."
        assert not any("PRIVATE WORKING MEMORY" in e.message.text for e in answers)
        assert not any(
            "PRIVATE WORKING MEMORY" in e.text for e in events if isinstance(e, AssistantTextDelta)
        )
        summary_usage = [e for e in answers if e.usage.input_tokens == 700]
        assert len(summary_usage) == 1  # replacement, not duplicate summarizers
        assert not summary_usage[0].message.text
        assert budget.input_tokens == 700
        assert budget.output_tokens == 100
        summary_calls = [
            m for m in model.seen if "OceanX scientific continuity requirements:" in str(m)
        ]
        assert len(summary_calls) == 1
        assert "old-0 RAW_EVIDENCE" in str(summary_calls[0])  # no default 4k trim
        assert "PRIVATE WORKING MEMORY" in str(model.seen[-1])
        assert "recent-5" in str(model.seen[-1])
        state = (await engine.graph.aget_state(engine._config("r1"))).values
        archives = [p for p in state["files"] if p.startswith("/conversation_history/")]
        assert len(archives) == 1
        archive = archives[0]
    finally:
        await close()

    # Rebuild the same graph/thread and recover the offloaded evidence with the
    # domain-only reader. The tool performs no local filesystem access.
    restored_model = _SummaryFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "read-archive",
                        "name": "ocean_read_context_archive",
                        "args": {"path": archive, "limit": 500},
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Recovered original evidence."),
        ]
    )
    monkeypatch.setattr(runtime_module, "create_chat_model", lambda _: restored_model)
    restored, close = await build_deep_agent_engine(
        profile=profile,
        tools=ToolRegistry(),
        system_prompt="Scientific fixture",
        cwd=tmp_path,
        thread_id="expert:summary",
        max_turns=10,
        operation_id_factory=lambda *args: ":".join(args),
    )
    try:
        events = [e async for e in restored.submit_message("Read the archive", request_id="r2")]
        assert [e.message.text for e in events if isinstance(e, AssistantTurnComplete)][
            -1
        ] == "Recovered original evidence."
        tool_messages = [m for m in restored_model.seen[-1] if isinstance(m, ToolMessage)]
        assert any(
            "RAW_EVIDENCE" in str(m.content) and '"next_offset": 500' in str(m.content)
            for m in tool_messages
        )
        # A guessed path from another expert/thread must not expose that archive.
        restored.thread_id = "expert:other"
        await restored.graph.ainvoke(
            {"messages": [HumanMessage(content="Read that same archive")]},
            restored._config("r3"),
        )
        other_tools = [m for m in restored_model.seen[-1] if isinstance(m, ToolMessage)]
        assert any("error" in str(m.content) for m in other_tools)
        assert not any("RAW_EVIDENCE" in str(m.content) for m in other_tools)
    finally:
        await close()
