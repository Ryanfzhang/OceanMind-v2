"""Protocol v2 stdio host; stdout is reserved exclusively for OHJSON frames."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import BinaryIO, TextIO

from ocean_partner.backend.events import BackendClient, EventBus
from ocean_partner.backend.router import OceanRequestRouter
from ocean_partner.backend.store import RequestStore
from ocean_partner.artifacts.files import ArtifactFileStore
from ocean_partner.artifacts.service import ArtifactService
from ocean_partner.exports import PortableExportService
from ocean_partner.workspace import WorkspaceService
from ocean_partner.expert_execution import ExpertCodeExecutionService
from ocean_partner.expert_deliverables import ExpertDeliverableService
from ocean_partner.expert_recovery import ExpertRecoveryService
from ocean_partner.protocol.v2.models import ClientKind, EventEnvelope
from ocean_partner.storage import OceanPaths
from ocean_partner.skill_curator import SkillCurator
from ocean_partner.task_workspace import TaskWorkspaceProjector
from ocean_partner.task_results import TaskResultStore
from ocean_partner.team.orchestrator import (
    OceanTeamOrchestrator,
    OceanTeamSettings,
)


FrameWriter = Callable[[str], Awaitable[None] | None]
PROTOCOL_PREFIX = "OHJSON:"


class JsonlStdioAdapter:
    """Adapt raw JSON-lines input to Protocol v2 without exposing logs on stdout."""

    def __init__(
        self,
        *,
        router: OceanRequestRouter,
        event_bus: EventBus,
        write_frame: FrameWriter,
        expected_client_kind: ClientKind = "desktop",
    ) -> None:
        self.router = router
        self.event_bus = event_bus
        self.write_frame = write_frame
        self.client = BackendClient(
            transport="stdio",
            expected_client_kind=expected_client_kind,
            sender=self._send_event,
        )
        self._started = False
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        if not self._started:
            await self.event_bus.register(self.client)
            self._started = True

    async def close(self) -> None:
        if self._started:
            await self.event_bus.unregister(self.client)
            self._started = False

    async def handle_line(self, raw: str) -> None:
        """Process one input JSON object; malformed input becomes a typed event."""

        await self.start()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        await self.router.handle_payload(self.client, payload)

    async def run(
        self,
        *,
        input_stream: BinaryIO | None = None,
    ) -> int:
        """Read UTF-8 JSONL until EOF or a successful system.shutdown request."""

        stream = input_stream or sys.stdin.buffer
        await self.start()
        try:
            while not self.router.shutdown_requested:
                raw = await asyncio.to_thread(stream.readline)
                if not raw:
                    break
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if line.strip():
                    await self.handle_line(line)
        finally:
            await self.close()
        return 0

    async def _send_event(self, event: EventEnvelope) -> None:
        frame = f"{PROTOCOL_PREFIX}{event.model_dump_json()}\n"
        async with self._write_lock:
            result = self.write_frame(frame)
            if isinstance(result, Awaitable):
                await result


class OceanBackendHost:
    """Own the Phase 1 store, shared event bus, router, and stdio adapter."""

    def __init__(
        self,
        state_directory: Path,
        *,
        write_frame: FrameWriter,
        team_settings: OceanTeamSettings | None = None,
        expected_client_kind: ClientKind = "desktop",
    ) -> None:
        self.paths = OceanPaths.for_state_root(state_directory).ensure()
        self.store = RequestStore(self.paths.database)
        self.task_workspace_projector = TaskWorkspaceProjector(
            paths=self.paths,
            store=self.store,
        )
        self.task_results = TaskResultStore(
            task_workspaces=self.task_workspace_projector,
        )
        self.artifact_service = ArtifactService(
            store=self.store,
            files=ArtifactFileStore(self.paths),
        )
        self.portable_export_service = PortableExportService(
            paths=self.paths,
            store=self.store,
            files=ArtifactFileStore(self.paths),
        )
        self.workspace_service = WorkspaceService(store=self.store)
        self.expert_code_execution = ExpertCodeExecutionService(
            store=self.store,
            paths=self.paths,
            task_workspaces=self.task_workspace_projector,
        )
        self.expert_deliverables = ExpertDeliverableService(
            store=self.store,
            task_workspaces=self.task_workspace_projector,
            task_results=self.task_results,
        )
        self.skill_curator = SkillCurator(
            store=self.store,
            task_results=self.task_results,
        )
        # Filesystem intents are recovered before active request records are marked interrupted.
        self.recovered_artifact_operations = self.artifact_service.recover_pending()
        self.expert_recovery = ExpertRecoveryService(
            store=self.store,
            task_workspaces=self.task_workspace_projector,
            task_results=self.task_results,
        )
        self.recovered_expert_work = self.expert_recovery.recover()
        self.event_bus = EventBus()
        self.team = OceanTeamOrchestrator(
            store=self.store,
            artifacts=self.artifact_service,
            task_results=self.task_results,
            expert_code_execution=self.expert_code_execution,
            expert_deliverables=self.expert_deliverables,
            domain_event_emitter=self.event_bus.emit_workspace,
            settings=team_settings,
        )
        self.router = OceanRequestRouter(
            store=self.store,
            event_bus=self.event_bus,
            artifact_service=self.artifact_service,
            portable_export_service=self.portable_export_service,
            team_orchestrator=self.team if self.team.settings.enabled else None,
            task_workspace_projector=self.task_workspace_projector,
            task_results=self.task_results,
            skill_curator=self.skill_curator,
        )
        self.stdio = JsonlStdioAdapter(
            router=self.router,
            event_bus=self.event_bus,
            write_frame=write_frame,
            expected_client_kind=expected_client_kind,
        )

    async def run_stdio(self, *, input_stream: BinaryIO | None = None) -> int:
        self.skill_curator.start()
        return await self.stdio.run(input_stream=input_stream)

    async def close(self) -> None:
        await self.skill_curator.close()
        await self.stdio.close()
        await self.router.shutdown_active_analysis()
        if not self.router.shutdown_requested:
            self.router.interrupt_active_requests()
        self.store.close()


async def run_stdio_backend(
    state_directory: Path,
    *,
    expected_client_kind: ClientKind = "desktop",
) -> int:
    """Run the production stdio adapter using stdout only for protocol frames."""

    output: TextIO = sys.stdout

    def write_frame(frame: str) -> None:
        output.write(frame)
        output.flush()

    host = OceanBackendHost(
        state_directory,
        write_frame=write_frame,
        expected_client_kind=expected_client_kind,
    )
    try:
        return await host.run_stdio()
    finally:
        await host.close()


__all__ = [
    "JsonlStdioAdapter",
    "OceanBackendHost",
    "PROTOCOL_PREFIX",
    "run_stdio_backend",
]
