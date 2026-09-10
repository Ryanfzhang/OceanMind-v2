import asyncio
import ast
import inspect
import textwrap
from types import SimpleNamespace

import pytest

from oceanx.agent_contract import ErrorEvent, StatusEvent
from oceanx.backend.router import OceanRequestRouter, _coordinator_events


@pytest.mark.asyncio
async def test_coordinator_enables_token_budget_wind_down(monkeypatch, tmp_path):
    from oceanx import agent

    captured = {}

    async def compose(**kwargs):
        return object()

    async def build(**kwargs):
        captured.update(kwargs)
        return "runtime"

    monkeypatch.setattr(agent, "build_ocean_runtime", compose)
    monkeypatch.setattr(agent, "_build_runtime", build)
    budget = agent.OceanAgentBudget()
    runtime = await agent.build_default_ocean_agent_runtime(
        SimpleNamespace(task_id="task", workspace_id="ws"), tmp_path, budget,
        lambda *args: "operation",
    )
    assert runtime == "runtime"
    assert captured["token_budget_wind_down"] is True
    assert captured["budget"] is budget


def test_coordinator_request_does_not_install_a_model_time_budget():
    # Structural regression: a request must not use the Expert wall-time field
    # or register a model-call timer capable of cancelling slow healthy calls.
    tree = ast.parse(textwrap.dedent(inspect.getsource(OceanRequestRouter._execute_agent_request)))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "max_wall_seconds" not in attributes
    assert "set_model_call_state_hook" not in attributes
    assert "CancelledError" in attributes


class BrokenEngine:
    def __init__(self, retryable):
        self.retryable = retryable
        self.submissions = 0
        self.resumes = 0

    async def submit_message(self, text, *, request_id):
        self.submissions += 1
        yield ErrorEvent(message='connection lost', code='network_failure', retryable=self.retryable)

    async def resume_message(self, *, request_id):
        self.resumes += 1
        yield ErrorEvent(message='connection lost', code='network_failure', retryable=self.retryable)


@pytest.mark.asyncio
@pytest.mark.parametrize('retryable, expected', [(True, 4), (False, 0)])
async def test_retries_are_bounded_and_do_not_resubmit(retryable, expected, monkeypatch):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr('oceanx.backend.router.asyncio.sleep', sleep)
    engine = BrokenEngine(retryable)
    events = [e async for e in _coordinator_events(engine, 'question', 'req')]
    assert engine.submissions == 1
    assert engine.resumes == expected
    assert delays == ([5, 15, 30, 60] if retryable else [])
    assert sum(isinstance(e, StatusEvent) for e in events) == expected
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].retries_exhausted is retryable


@pytest.mark.asyncio
async def test_cancellation_during_backoff_does_not_resume(monkeypatch):
    async def cancelled(delay):
        raise asyncio.CancelledError

    monkeypatch.setattr('oceanx.backend.router.asyncio.sleep', cancelled)
    engine = BrokenEngine(True)
    with pytest.raises(asyncio.CancelledError):
        async for _ in _coordinator_events(engine, 'question', 'req'):
            pass
    assert engine.resumes == 0
