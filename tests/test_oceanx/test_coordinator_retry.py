import asyncio

import pytest

from oceanx.agent_contract import ErrorEvent, StatusEvent
from oceanx.backend.router import _coordinator_events


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
@pytest.mark.parametrize('retryable, expected', [(True, 2), (False, 0)])
async def test_retries_are_bounded_and_do_not_resubmit(retryable, expected, monkeypatch):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr('oceanx.backend.router.asyncio.sleep', sleep)
    engine = BrokenEngine(retryable)
    events = [e async for e in _coordinator_events(engine, 'question', 'req')]
    assert engine.submissions == 1
    assert engine.resumes == expected
    assert delays == ([1, 2] if retryable else [])
    assert sum(isinstance(e, StatusEvent) for e in events) == expected
    assert isinstance(events[-1], ErrorEvent)


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
