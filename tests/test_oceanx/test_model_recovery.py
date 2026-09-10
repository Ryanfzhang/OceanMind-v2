from types import SimpleNamespace

import pytest

from oceanx.agent_contract import (
    AssistantTurnComplete,
    ConversationMessage,
    ErrorEvent,
    TextBlock,
    ToolExecutionStarted,
    UsageSnapshot,
)
from oceanx.model_recovery import model_error_event, model_events


class ProviderError(Exception):
    def __init__(self, status, message="provider failed", retry_after=None):
        super().__init__(message)
        self.status_code = status
        self.response = SimpleNamespace(headers={"retry-after": retry_after})


@pytest.mark.parametrize(
    "status, code, retryable",
    [
        (401, "model_configuration_error", False),
        (403, "model_configuration_error", False),
        (400, "model_request_error", False),
        (408, "provider_timeout", True),
        (429, "provider_rate_limit", True),
        (503, "provider_unavailable", True),
    ],
)
def test_status_classification(status, code, retryable):
    event = model_error_event(ProviderError(status, "connection failed"))
    assert (event.code, event.retryable) == (code, retryable)


def test_quota_validation_and_timeout_are_distinct():
    assert not model_error_event(ProviderError(429, "insufficient_quota")).retryable
    assert not model_error_event(ValueError("invalid schema")).retryable
    assert model_error_event(TimeoutError()).code == "provider_timeout"
    error = ConnectionError("failed")
    error.__cause__ = OSError("sensitive connection details")
    assert model_error_event(error).code == "network_failure"


def test_expert_interruption_is_visible_without_changing_scientific_handoff():
    from oceanx.team.models import ExpertResult, ExpertResultOrigin, WorkFailureCode, WorkStatus
    result = ExpertResult(
        work_order_id="work_paused",
        status=WorkStatus.INCOMPLETE,
        result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
        text="Earlier analysis is preserved.",
        error="Model delivery retries exhausted; resume later.",
        failure_code=WorkFailureCode.PROVIDER_UNAVAILABLE,
    )
    payload = result.coordinator_payload()
    assert set(payload) == {"text", "outputs"}
    assert result.error in payload["text"]
    assert result.text in payload["text"]
    completed = ExpertResult(work_order_id="work_answer", status=WorkStatus.COMPLETED,
                             text="Evidence is insufficient.")
    assert completed.coordinator_payload()["text"] == completed.text


def answer():
    return AssistantTurnComplete(
        message=ConversationMessage(
            role="assistant", content=[TextBlock(text="Evidence is insufficient.")]
        ),
        usage=UsageSnapshot(),
    )


class Engine:
    def __init__(self, events):
        self.events = events
        self.resumes = 0
        self.submissions = 0

    async def submit_message(self, text, *, request_id):
        self.submissions += 1
        for event in self.events:
            yield event

    async def resume_message(self, *, request_id):
        self.resumes += 1
        yield answer()


@pytest.mark.asyncio
async def test_retry_after_and_saved_resume(monkeypatch):
    waits = []

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr("oceanx.model_recovery.asyncio.sleep", sleep)
    engine = Engine([model_error_event(ProviderError(429, retry_after="25"))])
    events = [e async for e in model_events(engine, "question", "req")]
    assert waits == [25]
    assert engine.submissions == engine.resumes == 1
    assert isinstance(events[-1], AssistantTurnComplete)


@pytest.mark.asyncio
async def test_long_retry_after_pauses_without_retrying_early():
    engine = Engine([model_error_event(ProviderError(429, retry_after="3600"))])
    events = [e async for e in model_events(engine, "question", "req")]
    assert engine.resumes == 0
    assert events[-1].retries_exhausted


@pytest.mark.asyncio
async def test_configuration_failure_returns_immediately():
    engine = Engine([model_error_event(ProviderError(401))])
    events = [e async for e in model_events(engine, "question", "req")]
    assert engine.resumes == 0
    assert events[-1].recoverable is False
    assert events[-1].retries_exhausted is False


@pytest.mark.asyncio
async def test_empty_delivery_recovers_but_scientific_incomplete_answer_does_not(monkeypatch):
    async def sleep(delay):
        pass

    monkeypatch.setattr("oceanx.model_recovery.asyncio.sleep", sleep)
    engine = Engine([])
    events = [e async for e in model_events(engine, "question", "req")]
    assert engine.resumes == 1
    assert isinstance(events[-1], AssistantTurnComplete)
    engine = Engine([answer()])
    events = [e async for e in model_events(engine, "question", "req")]
    assert engine.resumes == 0


@pytest.mark.asyncio
async def test_no_replay_while_tool_is_active():
    engine = Engine(
        [
            ToolExecutionStarted(tool_name="ocean_assign", tool_input={}),
            ErrorEvent(message="connection lost", code="network_failure", retryable=True),
        ]
    )
    events = [e async for e in model_events(engine, "question", "req")]
    assert engine.resumes == 0
    assert events[-1].retries_exhausted
