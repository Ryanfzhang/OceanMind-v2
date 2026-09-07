"""Benchmark transport regression tests; no API key, paid model or data download."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oceanx.backend.host import OceanBackendHost
from oceanx.batch import (
    BatchClient,
    NeedsInteraction,
    QueryCase,
    interaction_answer,
    load_queries,
    run_batch,
    run_case,
)
from oceanx.cli import app


def test_manifest_paths_relative_to_manifest_and_ids_unique(tmp_path: Path):
    source = tmp_path / "shared.nc"
    source.write_bytes(b"unchanged data")
    queries = tmp_path / "queries.jsonl"
    row = {"id": "D01", "query": "分析数据", "datasets": ["shared.nc"]}
    queries.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert load_queries(queries)[0].datasets == [source]
    queries.write_text((json.dumps(row) + "\n") * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case id"):
        load_queries(queries)


@pytest.mark.parametrize("row", [
    {"id": "../bad", "query": "hi"}, {"id": "a", "query": " "},
    {"id": "a", "query": "hi", "timeout_seconds": float("nan")},
    {"id": "a", "query": "hi", "hidden_answer": "must not be fed to agent"},
])
def test_invalid_manifest_rejected_before_any_backend(tmp_path: Path, row):
    queries = tmp_path / "queries.jsonl"
    queries.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError):
        load_queries(queries)


def test_interaction_is_never_blanket_approved():
    case = QueryCase(id="a", query="test", permission_tools=["specific_tool"],
                     answers={"Which region?": "South China Sea"}, selected_papers=["A paper"])
    assert interaction_answer(case, {"kind": "permission", "tool_name": "specific_tool"}) == "allow"
    assert interaction_answer(case, {"kind": "permission", "tool_name": "shell"}) is None
    assert interaction_answer(case, {"kind": "permission", "question": "Which region?"}) is None
    assert interaction_answer(case, {"kind": "question", "question": "Which region?"}) == "South China Sea"
    assert interaction_answer(case, {"kind": "question", "question": "Different question?"}) is None
    payload = {"kind": "paper_selection", "options": [
        {"paper_id": "p1", "title": "A paper"}, {"paper_id": "p2", "title": "B paper"},
    ]}
    assert json.loads(interaction_answer(case, payload))["selected_paper_ids"] == ["p1"]
    ambiguous = {"kind": "paper_selection", "options": [
        {"paper_id": "p1", "title": "A paper"}, {"paper_id": "p2", "title": "A paper"},
    ]}
    assert interaction_answer(case, ambiguous) is None
    case.selected_papers.append("Missing paper")
    assert interaction_answer(case, payload) is None


@pytest.mark.asyncio
async def test_actual_backend_creates_task_and_references_data_without_copying(tmp_path: Path):
    source = tmp_path / "shared.csv"
    source.write_text("x,y\n1,2\n", encoding="utf-8")
    run = tmp_path / "case"
    run.mkdir()
    client = BatchClient(run, QueryCase(id="D01", query="do not execute", datasets=[source]))
    try:
        async with asyncio.timeout(45):
            await client.start()  # Real host + protocol, deliberately no session.submit/model call.
            assert client.context["task_id"].startswith("task_")
            assert client.revision > 0
            assert not list(run.rglob("*.csv"))
            assert source.read_text() == "x,y\n1,2\n"
            artifacts = await client.request("artifact.list", {})
            assert artifacts["type"] == "request.completed"
    finally:
        await client.close()
    assert client.process.returncode == 0


class FakeClient:
    behavior = "completed"
    closed = 0

    def __init__(self, directory, case):
        self.context = {"task_id": "task_fake"}
        self.events_truncated = False

    async def start(self):
        pass

    async def analyze(self):
        if self.behavior == "interaction":
            raise NeedsInteraction({"kind": "permission", "question": "Allow?"})
        if self.behavior == "timeout":
            await asyncio.sleep(5)
        if self.behavior == "crash":
            raise RuntimeError("backend disconnected")
        if self.behavior == "cancel":
            raise asyncio.CancelledError
        return {"type": "request.completed", "payload": {"result": {
            "assistant_text": "Final answer", "usage": {"input_tokens": 12, "output_tokens": 3},
            "team_provenance": {"actual_usage": {"input_tokens": 40}},
        }}}

    async def request(self, kind, payload):
        return {"payload": {"result": {"outputs": []}}}

    async def close(self):
        type(self).closed += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior,status", [
    ("completed", "completed"), ("interaction", "needs_interaction"),
    ("timeout", "timed_out"), ("crash", "failed"), ("cancel", "cancelled"),
])
async def test_case_persists_outcome_and_always_cleans_up(tmp_path, monkeypatch, behavior, status):
    monkeypatch.setattr("oceanx.batch.BatchClient", FakeClient)
    monkeypatch.setattr(FakeClient, "behavior", behavior)
    monkeypatch.setattr(FakeClient, "closed", 0)
    case = QueryCase(id="a", query="test", timeout_seconds=.03)
    if behavior == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await run_case(case, tmp_path / "case")
    else:
        await run_case(case, tmp_path / "case")
    result = json.loads((tmp_path / "case/result.json").read_text())
    assert result["status"] == status
    assert FakeClient.closed == 1
    if status == "completed":
        assert (tmp_path / "case/answer.md").read_text() == "Final answer"
        assert result["coordinator_usage"]["input_tokens"] == 12
        assert result["expert_usage"]["input_tokens"] == 40


@pytest.mark.asyncio
async def test_batch_resume_skip_completed_and_reject_changed_query(tmp_path, monkeypatch):
    monkeypatch.setattr("oceanx.batch.BatchClient", FakeClient)
    cases = [QueryCase(id="a", query="first"), QueryCase(id="b", query="second")]
    output = tmp_path / "batch"
    results = await run_batch(cases, output)
    assert len(results) == 2
    assert results[0]["directory"] != results[1]["directory"]
    assert await run_batch(cases, output, resume=True) == results
    assert len((output / "results.jsonl").read_text().splitlines()) == 2
    with pytest.raises(ValueError, match="changed"):
        await run_batch([QueryCase(id="a", query="changed")], output, resume=True)
    with pytest.raises(ValueError, match="exists"):
        await run_batch(cases, output)


@pytest.mark.asyncio
async def test_failed_cases_do_not_stop_batch_and_resume_uses_new_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr("oceanx.batch.BatchClient", FakeClient)
    monkeypatch.setattr(FakeClient, "behavior", "crash")
    cases = [QueryCase(id="a", query="first"), QueryCase(id="b", query="second")]
    output = tmp_path / "batch"
    first = await run_batch(cases, output)
    assert [r["status"] for r in first] == ["failed", "failed"]
    monkeypatch.setattr(FakeClient, "behavior", "completed")
    second = await run_batch(cases, output, resume=True)
    assert first[0]["directory"] != second[0]["directory"]
    assert all(r["status"] == "completed" for r in second)


@pytest.mark.asyncio
async def test_batch_output_must_not_be_in_source_dataset(tmp_path):
    with pytest.raises(ValueError, match="non-overlapping"):
        await run_batch([QueryCase(id="a", query="test", datasets=[tmp_path])], tmp_path / "outputs")


def test_public_commands_and_invalid_manifest_exit_without_model_call(tmp_path):
    runner = CliRunner()
    for command in ("run", "batch", "configure-models"):
        assert runner.invoke(app, [command, "--help"]).exit_code == 0
    queries = tmp_path / "bad.jsonl"
    queries.write_text('{"id":"a","query":" "}')
    result = runner.invoke(app, ["batch", "--queries", str(queries), "--output", str(tmp_path / "outputs")])
    assert result.exit_code != 0
    assert not (tmp_path / "outputs").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_curator_opt_out_does_not_change_desktop_default(tmp_path, monkeypatch, enabled):
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _: None)
    calls = []
    monkeypatch.setattr(host.skill_curator, "start", lambda: calls.append("started"))

    async def empty_stdio(**kwargs):
        return 0

    monkeypatch.setattr(host.stdio, "run", empty_stdio)
    try:
        if enabled:
            await host.run_stdio()  # Existing Desktop call uses its unchanged default.
        else:
            await host.run_stdio(skill_curator=False)
        assert calls == (["started"] if enabled else [])
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_analyze_continues_same_request_after_explicit_paper_selection(tmp_path, monkeypatch):
    case = QueryCase(id="a", query="query unchanged", selected_papers=["Selected paper"])
    client = BatchClient(tmp_path, case)
    sent = []
    frames = iter([
        {"type": "agent.text", "request_id": "req_expert", "payload": {"text": "Not final"}},
        {"type": "interaction.requested", "request_id": "req_run", "payload": {
            "interaction_id": "int_paper", "kind": "paper_selection", "options": [
                {"paper_id": "p1", "title": "Selected paper"},
                {"paper_id": "p2", "title": "Not selected"},
            ],
        }},
        {"type": "request.completed", "request_id": "req_response", "payload": {}},
        {"type": "request.completed", "request_id": "req_run", "payload": {"result": {"assistant_text": "Done"}}},
    ])

    async def send(kind, payload):
        sent.append((kind, payload))
        return "req_run" if kind == "session.submit" else "req_response"

    async def receive():
        return next(frames)

    monkeypatch.setattr(client, "send", send)
    monkeypatch.setattr(client, "receive", receive)
    event = await client.analyze()
    assert event["request_id"] == "req_run"
    assert [kind for kind, _ in sent] == ["session.submit", "interaction.respond"]
    assert sent[0][1]["text"] == case.query
    assert json.loads(sent[1][1]["answer"]) == {"selected_paper_ids": ["p1"]}


def test_public_configure_models_does_not_echo_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OCEANMIND_CONFIG_DIR", str(tmp_path / "config"))
    payload = {"roles": {role: {
        "provider": "openai", "model": "test-model", "base_url": "https://example.invalid/v1",
        "api_key": "test-only-not-a-real-key",
    } for role in ("coordinator", "expert", "skill_curator")}}
    result = CliRunner().invoke(app, ["configure-models"], input=json.dumps(payload))
    assert result.exit_code == 0
    assert "test-only-not-a-real-key" not in result.output
    assert set(json.loads(result.output)["roles"]) == {"coordinator", "expert", "skill_curator"}
