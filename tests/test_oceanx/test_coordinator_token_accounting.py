from types import SimpleNamespace

import pytest

from oceanx.agent import OceanAgentBudget
from oceanx.agent_contract import (
    AssistantTurnComplete,
    ConversationMessage,
    TextBlock,
    UsageSnapshot,
)
from oceanx.backend.events import BackendClient, EventBus
from oceanx.backend.router import OceanRequestRouter
from oceanx.backend.store import RequestStore
from oceanx.protocol.v2.models import parse_request


@pytest.mark.asyncio
async def test_final_answer_survives_cumulative_token_threshold(tmp_path, monkeypatch):
    store = RequestStore(tmp_path / "state.sqlite3")
    router = OceanRequestRouter(store=store, event_bus=EventBus())
    emitted = []

    async def send(event):
        emitted.append(event)

    async def noop(*args, **kwargs):
        pass

    client = BackendClient(
        transport="stdio",
        expected_client_kind="desktop",
        sender=send,
        client_id="client",
        session_id="session",
        workspace_id="ws",
        principal="user:local:desktop",
    )
    request = parse_request(
        {
            "protocol_version": 2,
            "expected_workspace_revision": 0,
            "request_id": "req_tokens",
            "type": "session.submit",
            "payload": {"text": "Finish the research"},
            "context": {"client_id": "client", "session_id": "session", "workspace_id": "ws"},
        }
    )
    store.reserve(request, principal=client.principal)
    store.mark_in_progress("req_tokens")
    monkeypatch.setattr(router, "_append_transcript_item", noop)
    monkeypatch.setattr(router, "_emit_agent_stream_event", noop)
    monkeypatch.setattr(router, "_emit_team_snapshot", noop)
    monkeypatch.setattr(
        router, "_broadcast_committed_terminal", lambda client, terminal: send(terminal)
    )

    class Engine:
        max_turns = 200

        def set_max_turns(self, value):
            self.max_turns = value

        async def submit_message(self, text, *, request_id):
            yield AssistantTurnComplete(
                message=ConversationMessage(
                    role="assistant", content=[TextBlock(text="Evidence-based final answer.")]
                ),
                usage=UsageSnapshot(input_tokens=1_100_000, output_tokens=210_000),
                turn_id="turn",
                request_id=request_id,
            )

    session = SimpleNamespace(
        workspace_id="ws",
        task_id=None,
        runtime=SimpleNamespace(
            engine=Engine(),
            provider_id="fixture",
            model_id="fixture",
        ),
    )
    try:
        await router._execute_agent_request(
            client=client,
            request=request,
            agent_session=session,
            context_audit_ids=(),
            budget=OceanAgentBudget(),
            submitted_text="Finish",
            visible_text="Finish",
        )
        terminal = emitted[-1]
        assert terminal.type == "request.completed"
        assert terminal.payload.result["assistant_text"] == "Evidence-based final answer."
        assert terminal.payload.result["usage"]["input_tokens"] == 1_100_000
    finally:
        store.close()
