"""Real Desktop host/store/router with deterministic, offline Agent runtime doubles.

Only this test process replaces model execution. Production has no scenario switches.
Control files live in Playwright's disposable project; they never touch a real project.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import socket
import sys
from pathlib import Path

from oceanx.agent import OceanAgentRuntime
from oceanx.agent_contract import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ConversationMessage,
    TextBlock,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    ToolUseBlock,
    UsageSnapshot,
)
from oceanx.agent_tools import ToolExecutionContext
from oceanx.backend.host import OceanBackendHost
from oceanx.skill_curator import SkillCuratorDecision
from oceanx.team.models import CoordinatorDecision, CoordinatorResult
from oceanx.team.orchestrator import (
    OceanTeamSettings,
    _ParticipantRunResult,
    _ParticipantState,
)
from oceanx.tools import OceanAssignmentInput


def deny_network(*_args, **_kwargs):
    raise RuntimeError("E2E fixtures must never access a model API or external network")


class ScriptedEngine:
    """Replace model decisions, not request lifecycle, tools, or persistence."""

    max_turns = 20
    compaction_generation = 0

    def __init__(self, host, services, workspace):
        self.host, self.services, self.workspace = host, services, workspace
        self.messages = []
        self.question_handler = None

    def set_max_turns(self, value):
        self.max_turns = value

    def set_ask_user_prompt(self, handler):
        self.question_handler = handler

    def set_permission_checker(self, handler):
        pass

    def set_permission_prompt(self, handler):
        pass

    def set_system_prompt(self, prompt):
        pass

    def load_messages(self, messages, *, compaction_generation=0):
        self.messages = list(messages)
        self.compaction_generation = compaction_generation

    def has_pending_continuation(self):
        return False

    async def submit_message(self, text, *, request_id):
        self.messages.append(ConversationMessage.from_user_text(text))
        task_id = self.services.task_id
        title = self.host.store.get_research_task(task_id).title
        self.host.audit("model_started", request_id=request_id, task_id=task_id)
        try:
            if "WAIT_RUNNING" in text:
                yield AssistantTextDelta(
                    text="Fixture analysis is running.",
                    request_id=request_id,
                    turn_id=f"{request_id}:running",
                )
                await asyncio.Event().wait()

            papers = "PAPERS" in text
            if papers:
                async for event in self.assign(request_id, "discover"):
                    yield event
                narration = "Candidate paper details: Paper Alpha tests seasonal mixing; Paper Beta provides independent salinity evidence."
                event_ids = {"request_id": request_id, "turn_id": f"{request_id}:papers"}
                yield AssistantTextDelta(text=narration, **event_ids)
                yield AssistantTurnComplete(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text=narration),
                            ToolUseBlock(
                                id="select", name="ocean_request_paper_selection", input={}
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(),
                    **event_ids,
                )
                yield ToolExecutionStarted(
                    tool_name="ocean_request_paper_selection",
                    tool_input={},
                    tool_call_id="select",
                    **event_ids,
                )
                selected = await self.services.paper_selection_sink(
                    {
                        "question": "Choose evidence for the next stage.",
                        "papers": [
                            {
                                "paper_id": "alpha",
                                "title": "Paper Alpha",
                                "topic": "Seasonal mixing",
                            },
                            {
                                "paper_id": "beta",
                                "title": "Paper Beta",
                                "topic": "Salinity evidence",
                            },
                        ],
                    },
                    ToolExecutionContext(
                        cwd=self.workspace,
                        request_id=request_id,
                        operation_id=f"{request_id}:papers",
                    ),
                )
                self.host.audit(
                    "papers_selected",
                    request_id=request_id,
                    selected=selected["selected_paper_ids"],
                )
                yield ToolExecutionCompleted(
                    tool_name="ocean_request_paper_selection",
                    output=json.dumps(selected),
                    tool_call_id="select",
                    **event_ids,
                )
                async for event in self.assign(request_id, "read"):
                    yield event
            elif "RESULT" in text:
                async for event in self.assign(request_id, "analyze"):
                    yield event

            answer = f"Conclusion for {title}: fixture evidence reviewed."
            if papers:
                answer += " Selected papers: " + ", ".join(selected["selected_paper_ids"])
            if "OBJECTS" in text:
                from oceanx.expert_deliverables import hydrate_ocean_view_netcdf
                from oceanx.scientific_view import ScientificFigure

                is_map = "MAP" in text
                dense = is_map and "DENSE" in text
                figure = ScientificFigure(plot_kind="spatial_map" if is_map else "scatter", title="Object binding fixture")
                if dense:
                    import numpy as np

                    x, y = np.linspace(110, 130, 80), np.linspace(15, 40, 110)
                    xx, yy = np.meshgrid(x, y)
                    valid = ((xx - 120) / 9) ** 2 + ((yy - 27.5) / 14) ** 2 < 1
                    values = np.where(valid, 2 + 7 * np.exp(-((xx - 119 - np.sin(yy)) / 4) ** 2), np.nan)
                    panel = figure.panel(x=x, y=y)
                    panel.field2d(values, variable="signal", units="units", colorbar_label="Signal (units)")
                    for index in range(5):
                        mask = valid & (values >= 5) & (yy >= 15 + index * 5) & (yy < 20 + index * 5)
                        # Holes/disconnected pieces and different-width row runs reproduce real masks.
                        mask &= ~((xx > 119) & (xx < 120) & (yy > 27) & (yy < 29))
                        feature_id = ("first", "second")[index] if index < 2 else f"region_{index + 1}"
                        figure.add_feature(id=feature_id, label=f"Region {index + 1}: selected cells (peak signal >= 5 units; evidence coverage and full interpretation remain available in the selected object details)", mask=mask)
                else:
                    panel = figure.panel(x=[120, 121, 122], y=[20, 21, 22], x_label="X", y_label="Y")
                    if is_map:
                        panel.field2d([[1, 1, 2], [1, 2, 2], [2, 2, 2]], units="1", field_kind="categorical", category_labels={1: "Group A", 2: "Group B"})
                    else:
                        panel.scatter()
                    figure.add_feature(id="first", label="First object", bounds=(119.8, 19.8, 120.4, 20.4))
                    figure.add_feature(id="second", label="Second object", point=(122, 22))
                output = figure.save(self.workspace / "object-fixture.nc")
                payload = hydrate_ocean_view_netcdf(output)
                result = self.host.task_results.put(
                    workspace_id=self.services.workspace_id, task_id=task_id, kind="interactive_view",
                    title=figure.title, origin_request_id=request_id,
                    content={"data_file": "data.nc", "dataset_file": "data.nc", "render_status": "interactive", "view_kind": figure.plot_kind,
                             "output_path": "outputs/object-fixture.nc", "features": [{"id": f["id"], "label": f["label"]} for f in payload["features"]]},
                    files={"data.nc": output},
                )
                key = f"{result.ref.task_id}/{result.ref.result_id}@v{result.ref.version}"
                answer += f"\n\n[[result:{key}#first|Wrong name]]\n\n[[result:{key}#second]]\n\n[[result:{key}#missing]]"
                self.host.store.record_coordinator_result(
                    request_id=request_id, result=CoordinatorResult(answer_markdown=answer, decision=CoordinatorDecision.ANSWERED, result_refs=(result.ref,)),
                )
            if "RESULT" in text:
                notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": []}
                path = self.host.task_workspace_projector.write_supplementary_notebook(
                    task_id=task_id,
                    request_id=request_id,
                    content=json.dumps(notebook).encode(),
                )
                result = self.host.task_results.put(
                    workspace_id=self.services.workspace_id,
                    task_id=task_id,
                    kind="file",
                    title="Analysis notebook",
                    origin_request_id=request_id,
                    content={"role": "supplementary_figure_notebook", "file": "analysis.ipynb"},
                    workspace_files={"analysis.ipynb": path},
                    materialization_key=f"fixture-notebook:{request_id}",
                )
                self.host.store.record_coordinator_result(
                    request_id=request_id,
                    result=CoordinatorResult(
                        answer_markdown=answer,
                        decision=CoordinatorDecision.ANSWERED,
                        result_refs=(result.ref,),
                    ),
                )
            message = ConversationMessage(role="assistant", content=[TextBlock(text=answer)])
            self.messages.append(message)
            yield AssistantTextDelta(
                text=answer, request_id=request_id, turn_id=f"{request_id}:final"
            )
            yield AssistantTurnComplete(
                message=message,
                usage=UsageSnapshot(),
                request_id=request_id,
                turn_id=f"{request_id}:final",
            )
        except asyncio.CancelledError:
            self.host.audit("model_cancelled", request_id=request_id)
            raise

    async def assign(self, request_id, wave):
        payload = OceanAssignmentInput(
            plan_goal="Review fixture evidence",
            dispatch=(wave,),
            todos=(
                {
                    "todo_id": wave,
                    "question": f"{wave} fixture evidence",
                    "why_this_expert": "Literature evidence",
                    "profile_id": "literature_reproduction_expert",
                    "expert_key": "literature",
                    "expected_outputs": ("answer",),
                    "done_when": "Return the bounded finding",
                },
            ),
        ).model_dump(mode="json")
        ids = {"request_id": request_id, "turn_id": f"{request_id}:{wave}", "tool_call_id": wave}
        yield ToolExecutionStarted(tool_name="ocean_assign", tool_input=payload, **ids)
        result = await self.services.team_assign_sink(
            payload,
            ToolExecutionContext(
                cwd=self.workspace, request_id=request_id, operation_id=f"{request_id}:{wave}"
            ),
        )
        yield ToolExecutionCompleted(
            tool_name="ocean_assign", output=json.dumps(result, default=str), **ids
        )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--client-kind", required=True)
    args = parser.parse_args()
    root = args.state_dir.parent
    if not (root / ".e2e-project").is_file():
        raise RuntimeError("Refusing to run a fixture against a non-test project")
    socket.create_connection = deny_network
    socket.socket.connect = deny_network

    def emit(frame):
        sys.stdout.write(frame)
        sys.stdout.flush()

    host = OceanBackendHost(
        args.state_dir,
        write_frame=emit,
        team_settings=OceanTeamSettings(enabled=True, max_transient_retries=0),
    )

    def audit(kind, **values):
        with (root / "fixture-audit.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"kind": kind, **values}) + "\n")

    host.audit = audit

    async def runtime(services, workspace, _budget, _operation):
        async def close():
            pass

        return OceanAgentRuntime(
            provider_id=services.provider_id,
            model_id="fixture",
            engine=ScriptedEngine(host, services, workspace),
            base_system_prompt="Offline E2E fixture",
            _close=close,
        )

    async def participant(binding):
        audit(
            "expert_started",
            request_id=binding.work_order.parent_request_id,
            job_key=binding.work_order.job_key,
            session_key=binding.session_key,
        )
        return _ParticipantRunResult(
            child_id=binding.child_id,
            state=_ParticipantState.COMPLETED,
            last_assistant_text="Fixture Expert evidence is ready.",
        )

    host.router.agent_runtime_factory = runtime
    host.router.provider_id_resolver = lambda: "desktop_checkpoint_fixture"
    host.router.model_id_resolver = lambda: "fixture"
    host.team._run_participant = participant

    async def reviewer(payload, _read_skill, _read_evidence):
        return [
            SkillCuratorDecision(
                decision="create",
                experience_ids=tuple(
                    note["experience_id"] for note in payload["saved_experiences"]
                ),
                target_skill="e2e-coordinate-check",
                reason="Reusable coordinate safeguard",
                confidence=0.99,
                skill_markdown="---\nname: e2e-coordinate-check\ndescription: Check coordinate units before calculating gradients.\nmetadata:\n  roles:\n    - data_reproducibility_expert\n---\n\nCheck coordinate units before calculating gradients.\n",
            )
        ]

    host.skill_curator.reviewer = reviewer

    async def controls():
        while True:
            trigger = root / "review-experience.json"
            if trigger.is_file():
                data = json.loads(trigger.read_text())
                trigger.unlink()
                host.store.save_experience(
                    workspace_id=data["workspace_id"],
                    task_id=data["task_id"],
                    request_id=data["request_id"],
                    agent_id="coordinator",
                    agent_role="coordinator",
                    text="Check coordinate units before calculating gradients.",
                )
                results = await host.skill_curator.review_workspace(
                    workspace_id=data["workspace_id"]
                )
                audit("curator_committed", count=len(results))
            await asyncio.sleep(0.1)

    control_task = asyncio.create_task(controls())
    try:
        await host.stdio.run()
    finally:
        control_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await control_task
        await host.close()


if __name__ == "__main__":
    asyncio.run(main())
