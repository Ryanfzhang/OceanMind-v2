"""Server-bound principals and capability checks for Ocean transports."""

from __future__ import annotations

from dataclasses import dataclass

from oceanx.protocol.v2.models import ClientKind


DESKTOP_CAPABILITIES = frozenset(
    {
        "session.open",
        "workspace.read",
        "workspace.write",
        "request.read",
        "request.cancel",
        "system.shutdown",
        "agent.submit",
        "interaction.respond",
        "task.read",
        "task.write",
        "task_result.interactive_view.get",
    }
)

REQUEST_CAPABILITIES = {
    "session.open": "session.open",
    "session.submit": "agent.submit",
    "task.create": "task.write",
    "task.list": "task.read",
    "task.get": "task.read",
    "task.open": "task.read",
    "task.rename": "task.write",
    "task.archive": "task.write",
    "task.reopen": "task.write",
    "task.delete": "task.write",
    "task.snapshot.get": "task.read",
    "task.agent_transcript.get": "task.read",
    "task.output.list": "task.read",
    "workspace.open": "workspace.write",
    "workspace.snapshot.get": "workspace.read",
    "request.status.get": "request.read",
    "request.cancel": "request.cancel",
    "interaction.respond": "interaction.respond",
    "artifact.list": "workspace.read",
    "artifact.get": "workspace.read",
    "artifact.resource.grant": "workspace.read",
    "task_result.resource.grant": "task.read",
    "task_result.interactive_view.get": "task.read",
    "artifact.versions.get": "workspace.read",
    "artifact.create": "workspace.write",
    "dataset.import": "workspace.write",
    "paper.import": "workspace.write",
    "paper.register": "workspace.write",
    "hypothesis.activate": "workspace.write",
    "portable.export.create": "workspace.write",
    "disclosure.policy.get": "workspace.read",
    "disclosure.policy.set": "workspace.write",
    "system.shutdown": "system.shutdown",
}


@dataclass(frozen=True)
class Principal:
    """Authenticated local actor with server-owned capabilities."""

    subject: str
    client_kind: ClientKind
    capabilities: frozenset[str]

    @property
    def key(self) -> str:
        return f"{self.subject}:{self.client_kind}"

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities


def principal_for_client_kind(client_kind: ClientKind) -> Principal:
    """Bind a local client kind to its fixed Phase 1 capability set."""

    if client_kind == "desktop":
        return Principal(
            subject="user:local",
            client_kind="desktop",
            capabilities=DESKTOP_CAPABILITIES,
        )
    raise ValueError(f"Unsupported client kind: {client_kind}")


def required_capability(request_type: str) -> str | None:
    """Return the capability needed before a typed request reaches a service."""

    return REQUEST_CAPABILITIES.get(request_type)


__all__ = [
    "DESKTOP_CAPABILITIES",
    "REQUEST_CAPABILITIES",
    "Principal",
    "principal_for_client_kind",
    "required_capability",
]
