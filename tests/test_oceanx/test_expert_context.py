from __future__ import annotations

import json
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from oceanx.expert_context import ExpertContextMiddleware, compact_expert_history
from oceanx.tool_history import ToolHistoryRepairMiddleware, repair_tool_history


def _exchange(index: int, *, saved_code: bool = True, logs: bool = True):
    execution_id = f"codeexec_{index}"
    root = f"/session/executions/{execution_id}"
    code = f"# immutable script {index}\n" + "print('analysis')\n" * 400
    call = {
        "name": "ocean_expert_run_code",
        "args": {"purpose": f"Check mechanism {index}", "code": code},
        "id": f"call-{index}",
        "type": "tool_call",
    }
    payload = {
        "execution_id": execution_id,
        "state": "succeeded",
        "returncode": 0,
        "stdout": "numerical evidence starts\n" + "long diagnostics\n" * 240 + "evidence ends",
        "stderr": "Traceback context\n" + "repeated warning\n" * 240 + "specific error tail",
        "candidate_results": [
            {
                "path": "hotspots.png",
                "title": "Hotspots",
                "claims": ["A exceeds B"],
                "summary": "Chlorophyll screening, not confirmed harmful blooms",
            }
        ],
        "evidence_refs": ["artifact:evidence-1"],
        "output_files": ["hotspots.png"],
        "publication_state": "awaiting_coordinator_review",
        "result_bundle_path": f"/session/result-bundles/{execution_id}.json",
    }
    if saved_code:
        payload["saved_code_path"] = f"{root}/code/analysis.py"
    if logs:
        payload["logs"] = {stream: f"{root}/logs/{stream}.txt" for stream in ("stdout", "stderr")}
    return (
        AIMessage(content=f"Interpretation {index}; do not infer toxicity.", tool_calls=[call]),
        ToolMessage(content=json.dumps(payload), tool_call_id=call["id"], name=call["name"]),
    )


def _history():
    return [
        SystemMessage(content="Keep all evidence limitations."),
        HumanMessage(content="Make one map; do not upload this dataset."),
        *_exchange(1),
        *_exchange(2),
        *_exchange(3),
        *_exchange(4),
        HumanMessage(content="Continue only the unfinished delivery, without recomputation."),
    ]


def test_old_exchanges_shrink_but_latest_two_and_scientific_metadata_remain() -> None:
    messages = _history()
    originals = [message.model_dump() for message in messages]
    projected = compact_expert_history(messages)

    assert [message.model_dump() for message in messages] == originals
    assert len(projected) == len(messages)
    assert sum(len(str(message.content)) for message in projected) < sum(
        len(str(message.content)) for message in messages
    )
    for index in (2, 4):
        before, after = messages[index], projected[index]
        assert after.content == before.content
        assert after.tool_calls[0]["id"] == before.tool_calls[0]["id"]
        assert after.tool_calls[0]["name"] == before.tool_calls[0]["name"]
        assert after.tool_calls[0]["args"]["purpose"] == before.tool_calls[0]["args"]["purpose"]
        assert len(after.tool_calls[0]["args"]["code"]) < 300
        assert "ocean_read_file" in after.tool_calls[0]["args"]["code"]
        assert "not for re-execution" in after.tool_calls[0]["args"]["code"]
        receipt = json.loads(projected[index + 1].content)
        original_receipt = json.loads(messages[index + 1].content)
        for key in original_receipt.keys() - {"stdout", "stderr"}:
            assert receipt[key] == original_receipt[key]
        for stream in ("stdout", "stderr"):
            assert len(receipt[stream]) < len(original_receipt[stream])
            assert receipt[stream].startswith(original_receipt[stream][:240])
            assert receipt[stream].endswith(original_receipt[stream][-240:])
            assert receipt["logs"][stream] in receipt[stream]
            assert receipt[f"{stream}_truncated"] is True
    for index in (0, 1, 6, 7, 8, 9, 10):
        assert projected[index].model_dump() == originals[index]


@pytest.mark.parametrize("block_type,argument_key", [("tool_use", "input"), ("tool_call", "args")])
def test_native_tool_blocks_and_raw_duplicates_do_not_leak_older_code(
    block_type: str,
    argument_key: str,
) -> None:
    messages = _history()
    first = messages[2]
    call = first.tool_calls[0]
    first.content = [
        {"type": "text", "text": "Scientific interpretation must remain unchanged."},
        {
            "type": block_type,
            "id": call["id"],
            "name": call["name"],
            argument_key: dict(call["args"]),
        },
    ]
    first.additional_kwargs = {
        "provider_note": "keep",
        "tool_calls": [{"arguments": call["args"]["code"]}],
    }
    before = first.model_dump()

    projected = compact_expert_history(messages)

    assert messages[2].model_dump() == before
    assert projected[2].content[0] == first.content[0]
    assert projected[2].content[1][argument_key] == projected[2].tool_calls[0]["args"]
    assert projected[2].additional_kwargs == {"provider_note": "keep"}
    assert call["args"]["code"] not in str(projected[2].model_dump())


@pytest.mark.parametrize(
    "shape", ["no_paths", "malformed", "non_object", "block_content", "unrelated_log_layout"]
)
def test_unknown_or_unrecoverable_receipts_are_not_shortened(shape: str) -> None:
    ai, receipt = _exchange(1, saved_code=False, logs=False)
    if shape == "malformed":
        receipt.content = "{not JSON"
    elif shape == "non_object":
        receipt.content = json.dumps(["not a receipt"])
    elif shape == "block_content":
        receipt.content = [{"type": "text", "text": receipt.content}]
    elif shape == "unrelated_log_layout":
        data = json.loads(receipt.content)
        data["logs"] = {"stdout": "/unrelated/stdout.txt"}
        receipt.content = json.dumps(data)
    messages = [ai, receipt, *_exchange(2), *_exchange(3)]
    before = [message.model_dump() for message in messages]

    assert [message.model_dump() for message in compact_expert_history(messages)] == before


def test_legacy_log_layout_recovers_immutable_code_and_never_uses_mutable_code_path() -> None:
    ai, receipt = _exchange(1, saved_code=False)
    data = json.loads(receipt.content)
    data["code_path"] = "/session/analysis.py"
    receipt.content = json.dumps(data)
    messages = [ai, receipt, *_exchange(2), *_exchange(3)]

    projected = compact_expert_history(messages)

    code = projected[0].tool_calls[0]["args"]["code"]
    assert "/session/executions/codeexec_1/code/analysis.py" in code
    assert "ocean_read_file: /session/analysis.py" not in code


def test_without_saved_logs_only_old_code_is_projected() -> None:
    messages = [*_exchange(1, logs=False), *_exchange(2), *_exchange(3)]
    projected = compact_expert_history(messages)

    assert projected[0].tool_calls[0]["args"]["code"] != messages[0].tool_calls[0]["args"]["code"]
    assert projected[1].model_dump() == messages[1].model_dump()


def test_mixed_batches_keep_ids_and_repair_still_closes_interrupted_call() -> None:
    ai, receipt = _exchange(1)
    ai.tool_calls.append(
        {
            "name": "ocean_read_file",
            "args": {"path": "/session/report.txt"},
            "id": "read-1",
            "type": "tool_call",
        }
    )
    read = ToolMessage(
        content="Independent evidence", name="ocean_read_file", tool_call_id="read-1"
    )
    messages = [HumanMessage(content="start"), ai, read, receipt, *_exchange(2), *_exchange(3)]
    dangling = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ocean_read_file",
                "args": {"path": "/session/next.txt"},
                "id": "dangling",
                "type": "tool_call",
            }
        ],
    )
    messages.append(dangling)
    before = [message.model_dump() for message in messages]

    projected = repair_tool_history(compact_expert_history(messages))

    assert [message.model_dump() for message in messages] == before
    assert projected[1].tool_calls[1] == ai.tool_calls[1]
    assert projected[2] == read
    pending = set()
    for message in projected:
        if isinstance(message, AIMessage):
            assert not pending
            pending.update(call["id"] for call in message.tool_calls)
        elif isinstance(message, ToolMessage):
            assert message.tool_call_id in pending
            pending.remove(message.tool_call_id)
    assert not pending
    assert projected[-1].tool_call_id == "dangling"
    assert projected[-1].status == "error"


def test_running_and_short_exchanges_remain_unchanged() -> None:
    ai, receipt = _exchange(1)
    data = json.loads(receipt.content)
    data["state"] = "running"
    receipt.content = json.dumps(data)
    messages = [ai, receipt, *_exchange(2), *_exchange(3)]
    assert compact_expert_history(messages) == messages

    ai, receipt = _exchange(1)
    ai.tool_calls[0]["args"]["code"] = "print(1)"
    data = json.loads(receipt.content)
    data.update(stdout="1", stderr="")
    receipt.content = json.dumps(data)
    messages = [ai, receipt, *_exchange(2), *_exchange(3)]
    assert compact_expert_history(messages) == messages


@pytest.mark.parametrize("state", ["failed", "timed_out", "cancelled"])
def test_latest_failure_survives_more_than_two_later_diagnostic_successes(state: str) -> None:
    messages = [message for index in range(1, 6) for message in _exchange(index)]
    for index in (1, 3):
        payload = json.loads(messages[index].content)
        payload.update(state=state, returncode=1)
        messages[index].content = json.dumps(payload)
    before = [message.model_dump() for message in messages]

    projected = compact_expert_history(messages)

    # Execution 2 is the latest failure; successful probes 3, 4 and 5 must not evict it.
    assert projected[2].model_dump() == before[2]
    assert projected[3].model_dump() == before[3]
    # Older failure 1 and diagnostic success 3 are still recoverable by path.
    for index in (0, 4):
        assert "Older executed code omitted" in projected[index].tool_calls[0]["args"]["code"]
        assert "logs/stderr.txt" in json.loads(projected[index + 1].content)["stderr"]
    # The two newest executions are fully intact as well.
    assert [message.model_dump() for message in projected[6:]] == before[6:]
    assert [message.model_dump() for message in messages] == before


def test_sync_model_wrapper_projects_only_request() -> None:
    messages = _history()
    before = [message.model_dump() for message in messages]
    model = FakeMessagesListChatModel(responses=[AIMessage(content="done")])
    request = ModelRequest(model=model, messages=messages, system_prompt="Keep this policy")
    response = ModelResponse(result=[AIMessage(content="done")])
    seen = []

    actual = ExpertContextMiddleware().wrap_model_call(
        request, lambda modified: seen.append(modified) or response
    )

    assert actual is response
    assert seen[0].messages == compact_expert_history(messages)
    assert seen[0].system_message == request.system_message
    assert [message.model_dump() for message in request.messages] == before


class _RecordingModel(FakeMessagesListChatModel):
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_messages.append([message.model_copy(deep=True) for message in messages])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.mark.asyncio
async def test_async_model_boundary_retains_full_checkpoint_on_followup() -> None:
    messages = _history()
    before = [message.model_dump() for message in messages]
    model = _RecordingModel(
        responses=[
            AIMessage(content="Saved result ready."),
            AIMessage(content="No further work needed."),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[],
        middleware=[ExpertContextMiddleware(), ToolHistoryRepairMiddleware()],
        checkpointer=MemorySaver(),
    )
    config: dict[str, Any] = {"configurable": {"thread_id": "expert-context-test"}}

    await graph.ainvoke({"messages": messages}, config)
    await graph.ainvoke(
        {"messages": [HumanMessage(content="Deliver without recomputing.")]}, config
    )

    assert len(model.seen_messages) == 2
    for observed in model.seen_messages:
        first_code = next(
            message for message in observed if isinstance(message, AIMessage) and message.tool_calls
        )
        assert "Older executed code omitted" in first_code.tool_calls[0]["args"]["code"]
        assert any(
            isinstance(message, HumanMessage) and message.content == messages[1].content
            for message in observed
        )
    snapshot = await graph.aget_state(config)
    for persisted, original in zip(snapshot.values["messages"], before, strict=False):
        # LangGraph assigns message IDs, but content and all other authored fields remain intact.
        values = persisted.model_dump()
        values["id"] = original["id"]
        assert values == original
    assert [
        message.model_dump() | {"id": original["id"]}
        for message, original in zip(messages, before, strict=True)
    ] == before
