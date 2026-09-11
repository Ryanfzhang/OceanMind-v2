"""Task-scoped code execution owned directly by an OceanMind Expert."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from oceanx.artifacts.models import ArtifactRef
from oceanx.backend.store import CodeExecutionRecord, RequestStore, TeamWorkRecord
from oceanx.datasets import resolve_dataset_source
from oceanx.expert_recovery import (
    execution_result_fingerprint,
    write_execution_result_manifest,
)
from oceanx.sandbox import (
    ResourceLimits,
    SandboxExecutionPolicy,
    SandboxUnavailableError,
    current_python_runtime,
    run_sandboxed_command,
)
from oceanx.scientific_view import ScientificFigure, ScientificPanel
from oceanx.storage import OceanPaths, StoragePolicyError
from oceanx.task_workspace import TaskWorkspaceProjector

SCIENTIFIC_VIEW_IMPORT = "from oceanx.scientific_view import ScientificFigure"


def _documented_signature(owner: type[Any], method: str | None = None) -> str:
    """Render a public signature directly from the runtime implementation."""

    callable_object = owner if method is None else getattr(owner, method)
    signature = inspect.signature(callable_object)
    parameters = [
        parameter
        for name, parameter in signature.parameters.items()
        if name not in {"self", "figure"} and (name != "panel_id" or method in {"add_feature", "panel"})
    ]
    signature = signature.replace(parameters=parameters, return_annotation=inspect.Signature.empty)
    name = owner.__name__ if method is None else f"{owner.__name__}.{method}"
    return f"{name}{signature}"


SCIENTIFIC_VIEW_API_CONTRACT = {
    "contract_version": "ocean-scientific-view-python/v1",
    "figure": _documented_signature(ScientificFigure),
    "panel": _documented_signature(ScientificFigure, "panel"),
    "panel_axis_options": _documented_signature(ScientificPanel, "__init__"),
    "layers": {
        method: _documented_signature(ScientificPanel, method)
        for method in (
            "line",
            "scatter",
            "heatmap",
            "field2d",
            "categories",
            "band",
            "vector",
            "contour_paths",
            "contour_grid",
            "annotations",
            "reference",
        )
    },
    "save": _documented_signature(ScientificFigure, "save"),
    "add_feature": _documented_signature(ScientificFigure, "add_feature"),
    "examples": {
        "object_binding": (
            "# Optional: name actual plotted objects that the answer discusses, before save.\n"
            "fig.add_feature(id='region_a', label='Region A', mask=region_mask)\n"
            "# Or point=(x, y), bounds=(xmin, ymin, xmax, ymax), or layer_id='series_a'\n"
            "# for a line/scatter declared with layer_id='series_a'. Select panel_id for multi-panel figures.\n"
            "fig.save('analysis.nc')\n"
            "# Cite [[output:analysis.nc#region_a|Region A]]; use only IDs actually saved.\n"
            "# Date-axis coordinates may be ISO dates. Categorical field2d accepts category_labels."
        ),
        "ts_scatter": (
            "fig = ScientificFigure(\n"
            "    plot_kind='ts_diagram',\n"
            "    title='T-S diagram',  # <-- MODIFY: describe the scientific view\n"
            "    conclusions=('Four water masses are resolved.',),  # <-- MODIFY: evidence-backed conclusion\n"
            ")\n"
            "panel = fig.panel(\n"
            "    x=salinity,  # <-- MODIFY: x data\n"
            "    y=temperature,  # <-- MODIFY: y data\n"
            "    x_label='Salinity',  # <-- MODIFY: x variable label\n"
            "    x_units='PSU',  # <-- MODIFY: x units\n"
            "    y_label='Temperature',  # <-- MODIFY: y variable label\n"
            "    y_units='degC',  # <-- MODIFY: y units\n"
            ")\n"
            "panel.scatter(\n"
            "    color_values=depth,  # <-- MODIFY: third variable used for colour\n"
            "    color_scale='linear',\n"
            "    palette='viridis',\n"
            "    radius=1.2,\n"
            "    opacity=0.25,\n"
            "    colorbar_label='Depth (m)',  # <-- MODIFY: label and units for colour data\n"
            ")\n"
            "fig.save('ts_diagram.nc')  # <-- MODIFY: stable output filename"
        ),
        "filled_contour": (
            "fig = ScientificFigure(plot_kind='section', title='Temperature section'); "
            "panel = fig.panel(x=latitude, y=depth, "
            "x_label='Latitude', x_units='degrees_north', y_label='Depth', y_units='m', "
            "y_reverse=True); panel.field2d(temperature, variable='temperature', "
            "units='degC', render='filled_contour', interpolation='linear', levels=20, "
            "colorbar_label='Temperature (degC)'); fig.save('temperature_section.nc')"
        ),
        "profile_line": (
            "fig = ScientificFigure(plot_kind='profile', title='Temperature profile'); "
            "panel = fig.panel(x=temperature, y=depth, "
            "x_label='Temperature', x_units='degC', y_label='Depth', y_units='m', "
            "y_reverse=True); panel.line(); fig.save('temperature_profile.nc')"
        ),
    },
    "spatial_map_rule": (
        "plot_kind='spatial_map' requires exactly one panel and exactly one field2d layer; "
        "save to a .nc output"
    ),
    "report_rule": (
        "Experts do not create or publish report files. Return report-ready conclusions, checks, "
        "and limitations in the final answer; the Coordinator owns report compilation."
    ),
    "view_semantics_rule": (
        "Choose plot_kind and a layer method that match the scientific object. Profiles use line; "
        "T-S samples use scatter; regular two-dimensional scalar grids use field2d. "
        "ScientificFigure.save() writes one self-describing NetCDF file automatically; "
        "never flatten arrays or construct renderer JSON manually."
    ),
    "modify_marker_rule": (
        "Every value marked '# <-- MODIFY' is a scientific-semantic choice that the Expert must "
        "set from the assigned data and question. Unmarked presentation parameters have reviewed "
        "defaults and should remain unchanged unless the evidence requires a different encoding."
    ),
}

_SCIENTIFIC_VIEW_RUNNER = '''"""Framework entry point for one OceanMind analysis program."""

from __future__ import annotations

import sys
from pathlib import Path

from oceanx.scientific_view import ScientificFigure, ScientificMap


def main() -> None:
    script = Path(sys.argv[1]).resolve()
    sys.argv = [str(script)]
    namespace = {
        "__name__": "__main__",
        "__file__": str(script),
        "__package__": None,
        "ScientificFigure": ScientificFigure,
        "ScientificMap": ScientificMap,
    }
    source = script.read_text(encoding="utf-8")
    exec(compile(source, str(script), "exec"), namespace, namespace)


if __name__ == "__main__":
    main()
'''


class ExpertCodeExecutionError(RuntimeError):
    """An Expert code request violates its task or input boundary."""


class ExpertRuntimeUnavailableError(ExpertCodeExecutionError):
    """The backend cannot safely execute Expert-authored Python in this process."""


def _scientific_view_source() -> Path:
    """Return the real Python source bundled for sandboxed Expert programs."""

    source = Path(__file__).resolve().with_name("scientific_view.py")
    if not source.is_file():
        raise ExpertRuntimeUnavailableError(
            "The ScientificFigure runtime is missing from the OceanMind backend bundle"
        )
    return source


def _analysis_probe_source() -> Path:
    source = Path(__file__).resolve().with_name("analysis_probe.py")
    if not source.is_file():
        raise ExpertRuntimeUnavailableError("The AnalysisContext probe is missing")
    return source


def _install_scientific_view_runtime(code_root: Path) -> None:
    """Install the framework-owned view builder beside one Expert program.

    The selected scientific Python is intentionally isolated from the backend
    source tree. Supplying the small pure-Python builder inside the execution
    root makes the documented import real without exposing application code or
    asking the model to discover host paths.
    """

    package_root = code_root / "oceanx"
    package_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_scientific_view_source(), package_root / "scientific_view.py")
    (package_root / "__init__.py").write_text(
        '"""OceanMind sandbox runtime support."""\n\n'
        "from .scientific_view import ScientificFigure, ScientificMap\n\n"
        '__all__ = ["ScientificFigure", "ScientificMap"]\n',
        encoding="utf-8",
    )
    (code_root / "_oceanmind_runner.py").write_text(
        _SCIENTIFIC_VIEW_RUNNER,
        encoding="utf-8",
    )


@dataclass(frozen=True)
class ExpertCodeExecutionResult:
    execution_id: str
    state: str
    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    output_files: tuple[str, ...]
    outputs: tuple[dict[str, object], ...]
    output_bytes: int
    limit_trigger: str | None
    code_path: str
    work_root: str
    result_bundle_path: str
    result_fingerprint: str
    attempt_number: int
    attempt_limit: int
    discovered_results: tuple[dict[str, object], ...] = ()
    reused_existing_execution: bool = False
    invalid_candidate_results: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, object]:
        return {
            "execution_id": self.execution_id,
            "state": self.state,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "logs": {
                stream: str(
                    Path(self.work_root)
                    / "executions"
                    / self.execution_id
                    / "logs"
                    / f"{stream}.txt"
                )
                for stream in ("stdout", "stderr")
            },
            "duration_seconds": self.duration_seconds,
            "output_files": list(self.output_files),
            "outputs": [dict(item) for item in self.outputs],
            "output_bytes": self.output_bytes,
            "limit_trigger": self.limit_trigger,
            "code_path": self.code_path,
            "work_root": self.work_root,
            "result_bundle_path": self.result_bundle_path,
            "result_fingerprint": self.result_fingerprint,
            "attempt_number": self.attempt_number,
            "attempt_limit": self.attempt_limit,
            "discovered_results": [dict(item) for item in self.discovered_results],
            "reused_existing_execution": self.reused_existing_execution,
            "invalid_candidate_results": list(self.invalid_candidate_results),
        }


@dataclass(frozen=True)
class ExpertMountedSource:
    """One backend-resolved Task Source mounted into an Expert sandbox."""

    handle: str
    kind: str
    ref: ArtifactRef
    title: str
    paths: tuple[Path, ...]
    manifest: dict[str, object]


@dataclass(frozen=True)
class ExpertPythonRuntime:
    """Interpreter selection pinned for the lifetime of one backend process."""

    executable: Path
    prefix: Path
    environment_name: str
    version: str
    read_roots: tuple[Path, ...]
    package_roots: tuple[Path, ...]
    requirements: tuple[str, ...]


class ExpertCodeExecutionService:
    """Run one Expert-authored Python program in its bounded workspace."""

    def __init__(
        self,
        *,
        store: RequestStore,
        paths: OceanPaths,
        task_workspaces: TaskWorkspaceProjector,
        limits: ResourceLimits | None = None,
    ) -> None:
        self.store = store
        self.paths = paths
        self.task_workspaces = task_workspaces
        self.limits = limits or ResourceLimits(
            wall_time_seconds=600.0,
            cpu_time_seconds=480,
            memory_bytes=2_147_483_648,
            disk_bytes=536_870_912,
            stdout_bytes=2_097_152,
            stderr_bytes=2_097_152,
            output_file_count=256,
            output_total_bytes=1_073_741_824,
        )
        self._runtime: ExpertPythonRuntime | None = None
        self._runtime_error: str | None = None
        try:
            selected = current_python_runtime()
            self._runtime = ExpertPythonRuntime(
                executable=selected.executable,
                prefix=selected.prefix,
                environment_name=selected.environment_name,
                version=selected.version,
                read_roots=selected.read_roots,
                package_roots=selected.package_roots,
                requirements=selected.requirements,
            )
        except (OSError, RuntimeError, SandboxUnavailableError) as exc:
            self._runtime_error = str(exc)
        self._analysis_context_locks: dict[str, asyncio.Lock] = {}
        self._task_dataset_contexts: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[str, object]
        ] = {}

    @property
    def runtime_unavailable_reason(self) -> str | None:
        """Return the process-pinned runtime failure, if startup validation failed."""

        return self._runtime_error

    def review_evidence(self, work_order_id: str) -> list[dict[str, object]]:
        """Forward declared dependencies, not private conversations or inferred verdicts.

        Full result text is sent once per assigned review round. Scripts, arrays and
        logs are locations only and remain in place for selective read-only checks.
        """
        work = self.store.get_team_work(work_order_id)
        if work is None or not work.work_order.review or not work.work_order.task_id:
            return []
        order = work.work_order
        latest = {}
        for record in self.store.list_task_team_work(
            workspace_id=work.workspace_id, task_id=order.task_id
        ):
            if record.work_order.todo_id in order.depends_on:
                latest[record.work_order.todo_id] = record
        evidence: list[dict[str, object]] = []
        for todo_id in order.depends_on:
            origin = latest.get(todo_id)
            if origin is None:
                evidence.append(
                    {"todo_id": todo_id, "result": None, "availability": "not_returned"}
                )
                continue
            source = origin.work_order
            if (source.profile_id, source.expert_key) == (order.profile_id, order.expert_key):
                raise ExpertCodeExecutionError(
                    "Independent review requires a different Expert instance"
                )
            root = self.task_workspaces.expert_session_root(
                order.task_id, source.job_key or source.work_order_id
            ).resolve()
            paths: list[str] = []

            def retain(path: Path, root: Path = root, paths: list[str] = paths) -> None:
                resolved = path.resolve()
                if resolved.is_relative_to(root) and resolved.exists():
                    paths.append(str(resolved))

            retain(root / "workspace")
            executions = []
            for execution in self._agent_job_executions(source.work_order_id):
                if execution.result is None:
                    continue
                base = root / "executions" / execution.execution_id
                locations = {}
                for name, path in {
                    "code": base / "code" / "analysis.py",
                    "inputs": base / "inputs.json",
                    "outputs": base / "outputs",
                    "stdout": base / "logs" / "stdout.txt",
                    "stderr": base / "logs" / "stderr.txt",
                }.items():
                    before = len(paths)
                    retain(path)
                    if len(paths) > before:
                        locations[name] = paths[-1]
                executions.append(
                    {
                        "execution_id": execution.execution_id,
                        "state": execution.state,
                        "locations": locations,
                    }
                )
            evidence.append(
                {
                    "todo_id": todo_id,
                    "work_order_id": source.work_order_id,
                    "profile_id": source.profile_id,
                    "expert_key": source.expert_key,
                    "question": source.task_goal,
                    "round_state": origin.state.value,
                    "result": origin.result.coordinator_payload()
                    if origin.result is not None
                    else None,
                    "interruption": origin.result.error if origin.result is not None else None,
                    "executions": executions,
                    "read_only_paths": list(dict.fromkeys(paths)),
                }
            )
        return evidence

    def read_expert_file(
        self,
        *,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        path: str,
        offset: int = 0,
        limit: int = 6_000,
    ) -> dict[str, object]:
        """Read saved text across rounds of the same logical Expert session."""
        task = self.store.get_research_task(task_id)
        work = self.store.get_team_work(work_order_id)
        if (
            task is None
            or task.workspace_id != workspace_id
            or work is None
            or work.workspace_id != workspace_id
            or work.work_order.task_id != task_id
        ):
            raise ExpertCodeExecutionError("Expert file session is unavailable")
        if offset < 0 or not 1 <= limit <= 12_000:
            raise ExpertCodeExecutionError("Invalid text offset or limit")
        if path.startswith("expert-report:"):
            report_work_id = path.removeprefix("expert-report:")
            origin = self.store.get_team_work(report_work_id)
            current_job = work.work_order.job_key or work_order_id
            if (
                origin is None
                or origin.workspace_id != workspace_id
                or origin.work_order.task_id != task_id
                or (origin.work_order.job_key or report_work_id) != current_job
                or origin.result is None
            ):
                raise ExpertCodeExecutionError("Report is outside this Expert's session or unavailable")
            text = json.dumps({
                "work_order_id": report_work_id,
                "assignment": origin.work_order.scientific_assignment(),
                "status": origin.state.value,
                "result": origin.result.coordinator_payload(),
            }, ensure_ascii=False, sort_keys=True, indent=2)
            end = offset + limit
            return {
                "path": path, "content": text[offset:end], "offset": offset,
                "next_offset": end if end < len(text) else None, "eof": end >= len(text),
            }
        root = self.task_workspaces.expert_session_root(
            task_id, work.work_order.job_key or work_order_id
        ).resolve()
        target = Path(path)
        target = (target if target.is_absolute() else root / target).resolve()
        review_paths = [
            Path(p)
            for item in self.review_evidence(work_order_id)
            for p in item.get("read_only_paths", [])
        ]
        if not target.is_relative_to(root) and not any(
            target == p or (p.is_dir() and target.is_relative_to(p)) for p in review_paths
        ):
            raise ExpertCodeExecutionError("File is outside this Expert's session")
        if not target.is_file():
            raise ExpertCodeExecutionError("Saved text file does not exist")
        try:
            with target.open(encoding="utf-8") as stream:
                remaining = offset
                while remaining:
                    skipped = stream.read(min(remaining, 8_192))
                    if not skipped:
                        break
                    remaining -= len(skipped)
                content = stream.read(limit + 1)
                if "\x00" in content:
                    raise UnicodeError("binary content")
        except (OSError, UnicodeError) as exc:
            raise ExpertCodeExecutionError(
                "Cannot read this file as UTF-8 text; use scientific code for binary data"
            ) from exc
        eof = len(content) <= limit
        return {
            "path": str(target),
            "content": content[:limit],
            "offset": offset,
            "next_offset": None if eof else offset + limit,
            "eof": eof,
        }

    def require_runtime(self) -> ExpertPythonRuntime:
        """Return the pinned runtime or fail before an Expert model is started."""

        if self._runtime is None:
            raise ExpertRuntimeUnavailableError(
                self._runtime_error or "Python execution runtime is unavailable"
            )
        return self._runtime

    def _agent_job_executions(self, work_order_id: str) -> tuple[CodeExecutionRecord, ...]:
        """Return executions visible to the current logical Expert session."""

        work = self.store.get_team_work(work_order_id)
        if work is None:
            raise ExpertCodeExecutionError("Expert code WorkOrder is unavailable")
        if work.work_order.job_key is None:
            return self.store.list_code_executions(work_order_id)
        return self.store.list_agent_job_code_executions(
            workspace_id=work.workspace_id,
            task_id=work.work_order.task_id,
            parent_request_id=work.work_order.parent_request_id,
            job_key=work.work_order.job_key,
        )

    @staticmethod
    def _bounded_evidence_text(value: object, *, limit: int = 6_000) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        half = max(1, (limit - 64) // 2)
        return (
            text[:half]
            + "\n... [middle omitted; read the log path for full text] ...\n"
            + text[-half:]
        )

    def _execution_manifest_entry(
        self,
        record: CodeExecutionRecord,
        *,
        task_root: Path,
        read_only_roots: list[Path],
        include_private_logs: bool,
        include_log_excerpts: bool = True,
        formal_outputs_only: bool = False,
    ) -> dict[str, object] | None:
        """Project one immutable execution into an Expert-readable result contract.

        Sibling Experts receive durable outputs and bundle metadata, never another
        Expert's private transcript.  A resumed logical Expert session additionally
        receives its own bounded stdout/stderr excerpts and log paths.
        """

        if record.result is None or record.state == "running":
            return None
        origin = self.store.get_team_work(record.work_order_id)
        saved_output_names = {
            name
            for item in (origin.checkpoint.result_bundle.items if origin is not None else ())
            if item.execution_id == record.execution_id
            for name in (item.output_name, *item.supporting_output_names)
        }
        if formal_outputs_only and not saved_output_names:
            return None
        # Failed executions remain private diagnostics unless the Coordinator
        # accepted one of their complete declared outputs as a formal result.
        if record.state != "succeeded" and not saved_output_names:
            return None
        persisted_work_root = record.result.get("work_root")
        if not isinstance(persisted_work_root, str) or not persisted_work_root:
            return None
        prior_work_root = Path(persisted_work_root).resolve()
        try:
            prior_work_root.relative_to(task_root)
        except ValueError:
            return None
        prior_output_root = (
            prior_work_root / "executions" / record.execution_id / "outputs"
        ).resolve()
        output_names = record.result.get("output_files", ())
        if not isinstance(output_names, list):
            output_names = []
        if formal_outputs_only or (record.state != "succeeded" and saved_output_names):
            output_names = [name for name in output_names if name in saved_output_names]
        outputs: list[dict[str, str]] = []
        for output_name in output_names:
            if not isinstance(output_name, str):
                continue
            candidate = (prior_output_root / output_name).resolve()
            try:
                candidate.relative_to(prior_output_root)
            except ValueError:
                continue
            if candidate.is_file() and not candidate.is_symlink():
                outputs.append({"name": output_name, "path": str(candidate)})
        if outputs:
            read_only_roots.append(prior_output_root)

        bundle_path: str | None = None
        persisted_bundle = record.result.get("result_bundle_path")
        if isinstance(persisted_bundle, str) and persisted_bundle:
            candidate = Path(persisted_bundle).resolve()
            try:
                candidate.relative_to(prior_work_root)
            except ValueError:
                candidate = Path()
            if candidate.is_file() and not candidate.is_symlink():
                bundle_path = str(candidate)
                read_only_roots.append(candidate.parent)

        entry: dict[str, object] = {
            "execution_id": record.execution_id,
            "execution_state": record.state,
            "origin_work_order_id": record.work_order_id,
            "origin_profile_id": (origin.work_order.profile_id if origin is not None else None),
            "origin_role": (origin.work_order.semantic_role if origin is not None else None),
            "session_round": (origin.work_order.session_round if origin is not None else None),
            "purpose": record.request.get("purpose"),
            "outputs": outputs,
            "result_bundle_path": bundle_path,
            "result_fingerprint": record.result.get("result_fingerprint"),
        }
        if include_private_logs:
            prior_logs_root = (
                prior_work_root / "executions" / record.execution_id / "logs"
            ).resolve()
            logs: dict[str, str] = {}
            for name in ("stdout.txt", "stderr.txt"):
                candidate = prior_logs_root / name
                if candidate.is_file() and not candidate.is_symlink():
                    logs[name.removesuffix(".txt")] = str(candidate)
            if logs:
                read_only_roots.append(prior_logs_root)
            entry["logs"] = logs
            if include_log_excerpts:
                entry.update(
                    {
                        "stdout_excerpt": self._bounded_evidence_text(
                            record.result.get("stdout", ""), limit=1_500
                        ),
                        "stderr_excerpt": self._bounded_evidence_text(
                            record.result.get("stderr", ""), limit=1_500
                        ),
                    }
                )
        return entry

    async def get_task_dataset_context(
        self,
        *,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        sources: tuple[ExpertMountedSource, ...] | None = None,
    ) -> dict[str, object]:
        """Return the persistent DatasetContext for this task and source set.

        The first query builds metadata once per immutable Task Source. Parallel
        Experts, later queries, and additional code calls reuse the same in-memory
        object. After a backend restart the task-owned JSON restores it without
        re-running the scientific probe.
        """

        runtime = self.require_runtime()
        mounted = sources or self.resolve_work_order_sources(
            workspace_id=workspace_id,
            work_order_id=work_order_id,
        )
        context_key = (
            task_id,
            tuple(sorted((source.ref.key, source.handle) for source in mounted)),
        )
        existing = self._task_dataset_contexts.get(context_key)
        if existing is not None:
            return existing
        task_root = self.task_workspaces.ensure_task_root(task_id).resolve()
        context_root = task_root / "analysis-context"
        context_root.mkdir(exist_ok=True)
        source_contexts: list[dict[str, object]] = []
        for source in mounted:
            cache_key = hashlib.sha256(source.ref.key.encode("utf-8")).hexdigest()[:32]
            cache_path = context_root / f"{cache_key}.json"
            lock_key = f"{task_id}:{source.ref.key}"
            lock = self._analysis_context_locks.setdefault(lock_key, asyncio.Lock())
            async with lock:
                if cache_path.is_file() and not cache_path.is_symlink():
                    try:
                        cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        cached = None
                    if isinstance(cached, dict):
                        source_contexts.extend(
                            {
                                **item,
                                "handle": source.handle,
                                "title": source.title,
                            }
                            for item in cached.get("sources", ())
                            if isinstance(item, dict)
                        )
                        continue

                probe_root = context_root / f"probe-{cache_key}"
                temporary_root = probe_root / "temporary"
                probe_root.mkdir(exist_ok=True)
                temporary_root.mkdir(exist_ok=True)
                probe_script = probe_root / "analysis_probe.py"
                shutil.copy2(_analysis_probe_source(), probe_script)
                probe_input = probe_root / "input.json"
                probe_output = probe_root / "output.json"
                probe_input.write_text(
                    json.dumps({"sources": [source.manifest]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                try:
                    result = await run_sandboxed_command(
                        (
                            str(runtime.executable),
                            str(probe_script),
                            str(probe_input),
                            str(probe_output),
                        ),
                        policy=SandboxExecutionPolicy(
                            read_only_roots=source.paths,
                            runtime_read_roots=runtime.read_roots,
                            writable_roots=(probe_root,),
                            output_root=probe_root,
                            temporary_root=temporary_root,
                            limits=ResourceLimits(
                                wall_time_seconds=min(120.0, self.limits.wall_time_seconds),
                                cpu_time_seconds=min(90, self.limits.cpu_time_seconds),
                                memory_bytes=self.limits.memory_bytes,
                                disk_bytes=min(67_108_864, self.limits.disk_bytes),
                                stdout_bytes=65_536,
                                stderr_bytes=65_536,
                                output_file_count=16,
                                output_total_bytes=16_777_216,
                            ),
                            allow_child_processes=False,
                        ),
                        cwd=probe_root,
                        environment={
                            "CONDA_DEFAULT_ENV": runtime.environment_name,
                            "CONDA_PREFIX": str(runtime.prefix),
                            "PYTHONNOUSERSITE": "1",
                        },
                    )
                    if probe_output.is_file():
                        payload = json.loads(probe_output.read_text(encoding="utf-8"))
                    else:
                        stderr = result.stderr.decode("utf-8", errors="replace")[-2_000:]
                        payload = {
                            "schema_version": "ocean-analysis-context/v1",
                            "sources": [
                                {
                                    "handle": source.handle,
                                    "kind": source.kind,
                                    "title": source.title,
                                    "path": source.manifest.get("path"),
                                    "format": source.manifest.get("format"),
                                    "inspection": "unavailable",
                                    "error": stderr or f"probe exited with {result.returncode}",
                                }
                            ],
                        }
                except (OSError, RuntimeError, SandboxUnavailableError, ValueError) as exc:
                    payload = {
                        "schema_version": "ocean-analysis-context/v1",
                        "sources": [
                            {
                                "handle": source.handle,
                                "kind": source.kind,
                                "title": source.title,
                                "path": source.manifest.get("path"),
                                "format": source.manifest.get("format"),
                                "inspection": "unavailable",
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        ],
                    }
                cache_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                source_contexts.extend(
                    {**item, "handle": source.handle, "title": source.title}
                    for item in payload.get("sources", ())
                    if isinstance(item, dict)
                )
        context_material = "\0".join(
            (task_id, *(f"{ref_key}\0{handle}" for ref_key, handle in context_key[1]))
        )
        context: dict[str, object] = {
            "schema_version": "ocean-analysis-context/v1",
            "context_id": "datasetctx_"
            + hashlib.sha256(context_material.encode("utf-8")).hexdigest()[:24],
            "scope": "task",
            "task_id": task_id,
            "sources": source_contexts,
            "instruction": (
                "This persistent task DatasetContext is authoritative. Use these exact "
                "paths, dimensions, coordinates, variables, and units; do not rebuild "
                "or rediscover it in later queries."
            ),
        }
        self._task_dataset_contexts[context_key] = context
        return context

    async def run_python(
        self,
        *,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        child_id: str,
        purpose: str,
        code: str,
    ) -> ExpertCodeExecutionResult:
        if not code.strip():
            raise ExpertCodeExecutionError("Expert code cannot be empty")
        task = self.store.get_research_task(task_id)
        if task is None or task.workspace_id != workspace_id:
            raise ExpertCodeExecutionError("Expert code task is unavailable")
        work_record = self.store.get_team_work(work_order_id)
        if work_record is None:
            raise ExpertCodeExecutionError("Expert code WorkOrder is unavailable")
        reused = self._reusable_successful_execution(
            work_record=work_record,
            task_id=task_id,
            code=code,
        )
        if reused is not None:
            return reused

        runtime = self.require_runtime()
        attempt_number, attempt_limit = self._next_execution_attempt(work_order_id)
        sources = self.resolve_work_order_sources(
            workspace_id=workspace_id,
            work_order_id=work_order_id,
        )
        analysis_context = await self.get_task_dataset_context(
            workspace_id=workspace_id,
            task_id=task_id,
            work_order_id=work_order_id,
            sources=sources,
        )

        session_key = work_record.work_order.job_key or work_order_id
        work_root = self.task_workspaces.expert_session_root(task_id, session_key)
        execution_id = f"codeexec_{uuid4().hex}"
        executions_root = work_root / "executions"
        executions_root.mkdir(exist_ok=True)
        # One logical Expert owns one persistent scientific working directory.
        # Individual executions remain immutable below ``executions/`` for
        # provenance, while arrays, checkpoints, and reusable intermediate
        # products live here across code calls and Coordinator follow-ups.
        working_root = work_root / "workspace"
        working_root.mkdir(exist_ok=True)
        execution_root = executions_root / execution_id
        code_root = execution_root / "code"
        output_root = execution_root / "outputs"
        temporary_root = execution_root / "temporary"
        logs_root = execution_root / "logs"
        result_manifest = execution_root / "result-events.jsonl"
        for directory in (
            execution_root,
            code_root,
            output_root,
            temporary_root,
            logs_root,
        ):
            directory.mkdir(exist_ok=True)

        _install_scientific_view_runtime(code_root)

        editable_code = work_root / "analysis.py"
        editable_code.write_text(code, encoding="utf-8")
        code_path = code_root / "analysis.py"
        shutil.copy2(editable_code, code_path)
        notebook_path = code_root / "analysis.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {
                            "cell_type": "markdown",
                            "metadata": {},
                            "source": [
                                "# OceanMind Expert analysis\n",
                                f"Purpose: {purpose}\n",
                                (
                                    "Inputs are pinned in inputs.json; reusable intermediate data "
                                    "belongs in OCEAN_WORK_DIR and formal deliverables in "
                                    "OCEAN_OUTPUT_DIR.\n"
                                ),
                            ],
                        },
                        {
                            "cell_type": "code",
                            "execution_count": None,
                            "metadata": {},
                            "outputs": [],
                            "source": [SCIENTIFIC_VIEW_IMPORT + "\n"],
                        },
                        {
                            "cell_type": "code",
                            "execution_count": None,
                            "metadata": {},
                            "outputs": [],
                            "source": code.splitlines(keepends=True),
                        },
                    ],
                    "metadata": {
                        "kernelspec": {
                            "display_name": f"OceanMind ({runtime.environment_name})",
                            "language": "python",
                            "name": "python3",
                        },
                        "language_info": {"name": "python", "version": runtime.version},
                    },
                    "nbformat": 4,
                    "nbformat_minor": 5,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        requirements_path = code_root / "requirements.txt"
        requirements_path.write_text(
            "\n".join(runtime.requirements) + "\n",
            encoding="utf-8",
        )

        inputs: list[dict[str, object]] = []
        read_only_roots: list[Path] = []
        for source in sources:
            inputs.append(source.manifest)
            read_only_roots.extend(source.paths)
        task_root = self.task_workspaces.ensure_task_root(task_id).resolve()
        job_executions = self._agent_job_executions(work_order_id)
        job_execution_ids = {record.execution_id for record in job_executions}
        prior_executions: list[dict[str, object]] = []
        recent_execution_ids = {prior.execution_id for prior in job_executions[-3:]}
        for prior in job_executions:
            entry = self._execution_manifest_entry(
                prior,
                task_root=task_root,
                read_only_roots=read_only_roots,
                include_private_logs=True,
                include_log_excerpts=prior.execution_id in recent_execution_ids,
            )
            if entry is not None:
                prior_executions.append(entry)

        order = work_record.work_order
        review_evidence = self.review_evidence(work_order_id)
        for item in review_evidence:
            read_only_roots.extend(Path(p) for p in item.get("read_only_paths", []))
        shared_results: list[dict[str, object]] = []
        for sibling in self.store.list_request_code_executions(
            workspace_id=workspace_id,
            task_id=task_id,
            parent_request_id=work_record.work_order.parent_request_id,
        ):
            if sibling.execution_id in job_execution_ids:
                continue
            sibling_work = self.store.get_team_work(sibling.work_order_id)
            # A Todo dependency is also the data-flow edge. Independent
            # Experts do not inherit one another's exploratory executions.
            if order.todo_id is not None and (
                sibling_work is None or sibling_work.work_order.todo_id not in order.depends_on
            ):
                continue
            entry = self._execution_manifest_entry(
                sibling,
                task_root=task_root,
                read_only_roots=read_only_roots,
                include_private_logs=False,
                formal_outputs_only=True,
            )
            if entry is not None:
                shared_results.append(entry)
        manifest_payload = {
            "review_evidence": review_evidence,
            "work_order_id": work_order_id,
            "assignment": order.scientific_assignment(),
            "runtime": {
                "scientific_view": {
                    "available": True,
                    "import": SCIENTIFIC_VIEW_IMPORT,
                    "api_contract": SCIENTIFIC_VIEW_API_CONTRACT,
                },
                "storage": {
                    "working_directory": {
                        "environment_variable": "OCEAN_WORK_DIR",
                        "path": str(working_root),
                        "lifetime": "logical_expert_session",
                    },
                    "output_directory": {
                        "environment_variable": "OCEAN_OUTPUT_DIR",
                        "path": str(output_root),
                        "lifetime": "current_code_execution",
                    },
                },
            },
            "inputs": inputs,
            "analysis_context": analysis_context,
            "prior_executions": prior_executions,
            "shared_results": shared_results,
            "instructions": {
                "inputs": "Use the exact paths declared above; do not search the filesystem.",
                "assignment": (
                    "The complete immutable WorkOrder is in assignment. Treat it as the "
                    "authoritative objective after any conversation compaction; never try to "
                    "recover the assignment from code files, old logs, or directory listings."
                ),
                "runtime": (
                    "ScientificFigure is already injected as a global before analysis.py starts. "
                    "Use it directly and follow runtime.scientific_view.api_contract without imports, "
                    "signature inspection, module discovery, or backend source-tree probing."
                ),
                "working_directory": (
                    "Use OCEAN_WORK_DIR for reusable arrays, checkpoints, caches, and other "
                    "intermediate scientific state. It is the same writable directory for every "
                    "code call in this logical Expert session, including Coordinator follow-ups. "
                    "Read your own earlier intermediate files there directly; do not search the "
                    "task tree or guess a previous execution's OCEAN_OUTPUT_DIR."
                ),
                "prior_outputs": (
                    "These are durable results from this logical Expert session, including "
                    "earlier WorkOrders. Reuse their excerpts, full logs, and outputs when they "
                    "satisfy the incremental assignment. Publication, formatting, or handoff "
                    "failures do not require recomputation."
                ),
                "shared_results": (
                    "These are immutable task results produced by sibling Experts in the same "
                    "foreground request. They expose outputs and result bundles, not private "
                    "conversation memory. Reuse them for downstream analysis, reporting, or "
                    "publication instead of searching for or recomputing the same result."
                ),
                "outputs": (
                    "Write only formal deliverables intended for ExpertResult under "
                    "OCEAN_OUTPUT_DIR. It is new for each code call; intermediate arrays and "
                    "checkpoints belong in OCEAN_WORK_DIR."
                ),
                "format_preference": (
                    "When the same scientific data is available as Zarr and another "
                    "equivalent representation, prefer Zarr for chunked/lazy analysis. "
                    "Fall back only when that Zarr store is unavailable or incompatible, "
                    "and record the concrete fallback reason."
                ),
            },
        }
        input_manifest = execution_root / "inputs.json"
        input_manifest.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Human-visible stable contract for the whole workstream.  Every
        # execution receives an immutable copy, while continuations can inspect
        # one predictable file without rediscovering sandbox layout.
        (work_root / "inputs.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        started_at = datetime.now(UTC).isoformat()
        self.store.start_code_execution(
            execution_id=execution_id,
            workspace_id=workspace_id,
            task_id=task_id,
            work_order_id=work_order_id,
            child_id=child_id,
            request={
                "purpose": purpose,
                "source_handles": [source.handle for source in sources],
                "code_path": str(editable_code),
                "attempt_number": attempt_number,
                "attempt_limit": attempt_limit,
            },
            started_at=started_at,
        )

        try:
            return await self._run_started_python(
                execution_id=execution_id,
                runtime=runtime,
                read_only_roots=tuple(read_only_roots),
                input_manifest=input_manifest,
                execution_root=execution_root,
                code_root=code_root,
                code_path=code_path,
                editable_code=editable_code,
                notebook_path=notebook_path,
                output_root=output_root,
                working_root=working_root,
                temporary_root=temporary_root,
                logs_root=logs_root,
                result_manifest=result_manifest,
                work_root=work_root,
                started_at=started_at,
                attempt_number=attempt_number,
                attempt_limit=attempt_limit,
            )
        except BaseException as exc:
            terminal_state = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            record = self.store.get_code_execution(execution_id)
            if record is not None and record.state == "running":
                self.store.finish_code_execution(
                    execution_id=execution_id,
                    state=terminal_state,
                    result={"error": str(exc) or type(exc).__name__},
                    ended_at=datetime.now(UTC).isoformat(),
                )
            raise

    async def _run_started_python(
        self,
        *,
        execution_id: str,
        runtime: ExpertPythonRuntime,
        read_only_roots: tuple[Path, ...],
        input_manifest: Path,
        execution_root: Path,
        code_root: Path,
        code_path: Path,
        editable_code: Path,
        notebook_path: Path,
        output_root: Path,
        working_root: Path,
        temporary_root: Path,
        logs_root: Path,
        result_manifest: Path,
        work_root: Path,
        started_at: str,
        attempt_number: int,
        attempt_limit: int,
    ) -> ExpertCodeExecutionResult:
        """Run and persist one already-started execution as one terminal transaction."""

        runtime_environment = {
            "CONDA_DEFAULT_ENV": runtime.environment_name,
            "CONDA_PREFIX": str(runtime.prefix),
            "OCEAN_INPUT_MANIFEST": str(input_manifest),
            "OCEAN_WORK_DIR": str(working_root),
            "OCEAN_OUTPUT_DIR": str(output_root),
            "OCEAN_RESULT_MANIFEST": str(result_manifest),
            "OCEAN_TEMP_DIR": str(temporary_root),
            "PYTHONNOUSERSITE": "1",
        }

        runner_path = code_root / "_oceanmind_runner.py"

        result = await run_sandboxed_command(
            (str(runtime.executable), str(runner_path), str(code_path)),
            policy=SandboxExecutionPolicy(
                read_only_roots=read_only_roots,
                runtime_read_roots=runtime.read_roots,
                writable_roots=(execution_root, working_root),
                output_root=output_root,
                temporary_root=temporary_root,
                limits=self.limits,
                allow_child_processes=False,
                allow_network=True,
            ),
            # Relative output paths are ordinary in analysis code. Running
            # from the declared output root makes them durable automatically
            # instead of silently leaving successful PNG/JSON files in code/.
            cwd=output_root,
            environment=runtime_environment,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        ended_at = datetime.now(UTC).isoformat()
        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        setup_cell = notebook["cells"][1]
        setup_cell["execution_count"] = 1
        code_cell = notebook["cells"][2]
        code_cell["execution_count"] = 1
        code_cell["outputs"] = [
            *(
                [
                    {
                        "name": "stdout",
                        "output_type": "stream",
                        "text": stdout.splitlines(keepends=True),
                    }
                ]
                if stdout
                else []
            ),
            *(
                [
                    {
                        "name": "stderr",
                        "output_type": "stream",
                        "text": stderr.splitlines(keepends=True),
                    }
                ]
                if stderr
                else []
            ),
        ]
        notebook["metadata"]["oceanmind_execution"] = {
            "execution_id": execution_id,
            "state": result.status.value,
            "returncode": result.returncode,
            "duration_seconds": result.duration_seconds,
            "limit_trigger": result.limit_trigger,
            "started_at": started_at,
            "ended_at": ended_at,
        }
        notebook_path.write_text(
            json.dumps(notebook, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (logs_root / "stdout.txt").write_text(stdout, encoding="utf-8")
        (logs_root / "stderr.txt").write_text(stderr, encoding="utf-8")
        output_files = tuple(
            path.relative_to(output_root).as_posix()
            for path in sorted(output_root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        )
        output_records: list[dict[str, object]] = []
        for output_name in output_files:
            output_path = output_root / output_name
            digest = hashlib.sha256()
            with output_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            output_records.append(
                {
                    "name": output_name,
                    "bytes": output_path.stat().st_size,
                    "sha256": digest.hexdigest(),
                }
            )
        declaration_errors: list[str] = []
        discovered_results = self._read_result_events(
            result_manifest,
            output_files=output_files,
            errors=declaration_errors,
        )
        result_bundle = {
            "schema_version": "ocean-execution-result/v1",
            "execution_id": execution_id,
            "state": result.status.value,
            "returncode": result.returncode,
            "limit_trigger": result.limit_trigger,
            "duration_seconds": result.duration_seconds,
            "output_files": list(output_files),
            "outputs": output_records,
            "discovered_results": list(discovered_results),
            "invalid_candidate_results": declaration_errors,
            "output_bytes": result.output_summary.total_bytes,
            "stdout": self._bounded_evidence_text(stdout),
            "stderr": self._bounded_evidence_text(stderr, limit=2_000),
            "logs": {
                "stdout": str(logs_root / "stdout.txt"),
                "stderr": str(logs_root / "stderr.txt"),
            },
            "output_root": str(output_root),
            "working_root": str(working_root),
            "input_manifest": str(input_manifest),
            "code_path": str(editable_code),
            "work_root": str(work_root),
            "attempt_number": attempt_number,
            "attempt_limit": attempt_limit,
            "ended_at": ended_at,
        }
        result_fingerprint = execution_result_fingerprint(result_bundle)
        result_bundle["fingerprint"] = result_fingerprint
        result_bundles_root = work_root / "result-bundles"
        result_bundles_root.mkdir(exist_ok=True)
        result_bundle_path = result_bundles_root / f"{execution_id}.json"
        write_execution_result_manifest(result_bundle_path, result_bundle)
        write_execution_result_manifest(work_root / "latest-result.json", result_bundle)
        payload = ExpertCodeExecutionResult(
            execution_id=execution_id,
            state=result.status.value,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=result.duration_seconds,
            output_files=output_files,
            outputs=tuple(output_records),
            output_bytes=result.output_summary.total_bytes,
            limit_trigger=result.limit_trigger,
            code_path=str(editable_code),
            work_root=str(work_root),
            result_bundle_path=str(result_bundle_path),
            result_fingerprint=result_fingerprint,
            attempt_number=attempt_number,
            attempt_limit=attempt_limit,
            discovered_results=discovered_results,
            invalid_candidate_results=tuple(declaration_errors),
        )
        self.store.finish_code_execution(
            execution_id=execution_id,
            state=payload.state,
            result=payload.as_payload(),
            ended_at=ended_at,
        )
        return payload

    def _reusable_successful_execution(
        self,
        *,
        work_record: TeamWorkRecord,
        task_id: str,
        code: str,
    ) -> ExpertCodeExecutionResult | None:
        """Reuse an exact program whose terminal result is already durable.

        This guard runs before runtime validation and attempt reservation.  A
        transport retry can therefore reissue the same tool call and obtain the
        saved execution without starting Python again. This also covers useful
        stdout-only calculations; raw outputs and framework declarations remain
        execution evidence regardless of later Coordinator publication.
        """

        reusable_execution_ids = set(work_record.checkpoint.successful_execution_ids)
        if not reusable_execution_ids:
            return None
        task_root = self.task_workspaces.ensure_task_root(task_id).resolve()
        records = self._agent_job_executions(work_record.work_order.work_order_id)
        for record in reversed(records):
            if (
                record.execution_id not in reusable_execution_ids
                or record.state != "succeeded"
                or record.result is None
            ):
                continue
            persisted_work_root = record.result.get("work_root")
            if not isinstance(persisted_work_root, str) or not persisted_work_root:
                continue
            work_root = Path(persisted_work_root).resolve()
            try:
                work_root.relative_to(task_root)
            except ValueError:
                continue
            code_candidate = work_root / "executions" / record.execution_id / "code" / "analysis.py"
            if code_candidate.is_symlink():
                continue
            try:
                code_path = code_candidate.resolve(strict=True)
                code_path.relative_to(work_root)
                persisted_code = code_path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue
            if persisted_code != code:
                continue
            payload = record.result
            try:
                return ExpertCodeExecutionResult(
                    execution_id=record.execution_id,
                    state=record.state,
                    returncode=payload.get("returncode"),
                    stdout=str(payload.get("stdout", "")),
                    stderr=str(payload.get("stderr", "")),
                    duration_seconds=float(payload.get("duration_seconds", 0.0)),
                    output_files=tuple(payload.get("output_files", ())),
                    outputs=tuple(dict(item) for item in payload.get("outputs", ())),
                    output_bytes=int(payload.get("output_bytes", 0)),
                    limit_trigger=payload.get("limit_trigger"),
                    code_path=str(payload.get("code_path", code_path)),
                    work_root=str(work_root),
                    result_bundle_path=str(payload["result_bundle_path"]),
                    result_fingerprint=str(payload["result_fingerprint"]),
                    attempt_number=int(payload.get("attempt_number", 1)),
                    attempt_limit=int(payload.get("attempt_limit", 1)),
                    discovered_results=tuple(
                        dict(item) for item in payload.get("discovered_results", ())
                    ),
                    invalid_candidate_results=tuple(payload.get("invalid_candidate_results", ())),
                    reused_existing_execution=True,
                )
            except (KeyError, TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _read_result_events(
        manifest: Path,
        *,
        output_files: tuple[str, ...],
        errors: list[str] | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Read current and legacy save events without silently losing declarations."""

        def reject(line_number: int, reason: str) -> None:
            if errors is not None and len(errors) < 32:
                errors.append(f"Saved-result event {line_number}: {reason[:400]}")

        if manifest.is_symlink():
            reject(0, "result manifest must not be a symlink")
            return ()
        if not manifest.exists():
            return ()
        available_outputs = set(output_files)
        discovered: dict[str, dict[str, object]] = {}
        try:
            lines = manifest.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            reject(0, f"cannot read result manifest: {exc}")
            return ()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                reject(line_number, "invalid JSON")
                continue
            if (
                not isinstance(event, dict)
                or event.get("schema_version") != "ocean-result-event/v1"
            ):
                reject(line_number, "unsupported result-event schema")
                continue
            normalized = dict(event)
            if event.get("kind") == "report":
                output_name = event.get("report_output")
            elif event.get("kind") == "interactive_view":
                # Every current view uses data_output. field_output is only a
                # read-compatible alias for maps saved by the legacy runtime.
                output_name = event.get("data_output")
                legacy_name = event.get("field_output")
                if event.get("view_kind") == "spatial_map" and legacy_name is not None:
                    if output_name is not None and output_name != legacy_name:
                        reject(line_number, "conflicting data_output and legacy field_output")
                        continue
                    output_name = legacy_name
                    normalized.pop("field_output", None)
                    normalized["data_output"] = output_name
            else:
                reject(line_number, "unsupported result kind")
                continue
            if not isinstance(output_name, str) or output_name not in available_outputs:
                reject(line_number, f"declared output is missing or unavailable: {output_name!r}")
                continue
            dataset_output = normalized.get("dataset_output")
            if isinstance(dataset_output, str) and dataset_output not in available_outputs:
                reject(line_number, f"declared dataset is unavailable: {dataset_output}")
                continue
            preview = normalized.get("preview_output")
            if isinstance(preview, str) and preview not in available_outputs:
                normalized.pop("preview_output", None)
            attachments = normalized.get("attachment_outputs")
            if isinstance(attachments, list):
                normalized["attachment_outputs"] = [
                    name
                    for name in attachments
                    if isinstance(name, str) and name in available_outputs
                ]
            # save() atomically replaces the same file: the last declaration
            # must describe those bytes, not a superseded title or conclusion.
            discovered[output_name] = normalized
        return tuple(discovered.values())

    def _next_execution_attempt(self, work_order_id: str) -> tuple[int, int]:
        """Reserve code capacity from the WorkOrder's existing tool safety budget.

        The Expert still decides whether a result is scientifically adequate and may
        repair failed code. There is deliberately no second 3/5/6-attempt policy:
        token wind-down owns normal termination, while the already-declared WorkBudget
        tool ceiling remains a loose runaway-loop backstop.
        """

        record = self.store.get_team_work(work_order_id)
        if record is None:
            raise ExpertCodeExecutionError("Expert code WorkOrder is unavailable")
        attempt_limit = record.work_order.budget.max_tool_calls
        # Attempt capacity belongs to one Coordinator assignment, not to the
        # long-lived semantic Expert.  Earlier assignments remain readable via
        # ``_agent_job_executions`` below, but must not consume a later,
        # independently scoped assignment's execution allowance.
        prior_executions = self.store.list_code_executions(work_order_id)
        attempt_count = len(prior_executions)
        if attempt_count >= attempt_limit:
            raise ExpertCodeExecutionError(
                "This assignment has reached its WorkOrder tool-call safety allowance "
                f"({attempt_limit}). Reuse successful execution evidence and return the "
                "candidate answer now; a focused follow-up WorkOrder can continue any "
                "explicitly unresolved delta."
            )
        recent_fingerprints = [
            record.result.get("result_fingerprint")
            for record in prior_executions[-2:]
            if record.result is not None
            and isinstance(record.result.get("result_fingerprint"), str)
        ]
        if len(recent_fingerprints) == 2 and recent_fingerprints[0] == recent_fingerprints[1]:
            raise ExpertCodeExecutionError(
                "The last two executions produced the same durable result and no new "
                "evidence. Reuse the persisted ResultBundle and return the supported "
                "result or its explicit limitation; do not repeat the same computation."
            )
        return attempt_count + 1, attempt_limit

    def resolve_work_order_sources(
        self,
        *,
        workspace_id: str,
        work_order_id: str,
    ) -> tuple[ExpertMountedSource, ...]:
        """Resolve every immutable WorkOrder Task Source once on the server.

        Code tool arguments deliberately have no input-selection field. The durable
        WorkOrder is the single authority for sandbox inputs, so an Expert cannot omit,
        replace, or broaden them on an individual call.
        """

        record = self.store.get_team_work(work_order_id)
        if record is None or record.workspace_id != workspace_id:
            raise ExpertCodeExecutionError("Expert code WorkOrder is unavailable")

        sources: list[ExpertMountedSource] = []
        for index, evidence in enumerate(record.work_order.input_refs, start=1):
            if evidence.kind not in {"artifact", "dataset", "paper"}:
                continue
            match = re.fullmatch(r"([a-z][a-z0-9_]{2,127})@v(\d+)", evidence.ref)
            if match is None:
                raise ExpertCodeExecutionError(
                    f"WorkOrder input is not an immutable artifact reference: {evidence.ref!r}"
                )
            ref = ArtifactRef(artifact_id=match.group(1), version=int(match.group(2)))
            artifact = self.store.get_artifact(workspace_id=workspace_id, ref=ref)
            if artifact is None:
                raise ExpertCodeExecutionError(
                    f"WorkOrder input artifact is unavailable: {evidence.ref}"
                )
            if evidence.kind == "dataset" and artifact.artifact_type != "dataset":
                raise ExpertCodeExecutionError(
                    f"WorkOrder dataset input is not a dataset: {evidence.ref}"
                )
            handle = evidence.locator or f"source_{index}"
            if artifact.artifact_type == "dataset":
                dataset = resolve_dataset_source(
                    store=self.store,
                    paths=self.paths,
                    workspace_id=workspace_id,
                    ref=ref,
                )
                paths = (dataset.path,)
                files = [
                    {
                        "path": str(dataset.path),
                        "format": dataset.format,
                    }
                ]
                primary_path = str(dataset.path)
                format_hint = dataset.format
            else:
                resolved_files: list[dict[str, object]] = []
                resolved_paths: list[Path] = []
                try:
                    for artifact_file in artifact.files:
                        path = self.paths.resolve_uri(artifact_file.uri)
                        if not path.exists():
                            raise ExpertCodeExecutionError(
                                f"Task Source file is unavailable: {artifact_file.uri}"
                            )
                        resolved_paths.append(path)
                        resolved_files.append(
                            {
                                "path": str(path),
                                "mime_type": artifact_file.mime_type,
                                "size_bytes": artifact_file.size_bytes,
                            }
                        )
                except StoragePolicyError as exc:
                    raise ExpertCodeExecutionError(
                        f"Task Source file cannot be mounted: {artifact.title}"
                    ) from exc
                paths = tuple(resolved_paths)
                files = resolved_files
                primary_path = str(paths[0]) if len(paths) == 1 else None
                format_hint = paths[0].suffix.lstrip(".").lower() if len(paths) == 1 else None

            manifest: dict[str, object] = {
                "handle": handle,
                "kind": artifact.artifact_type,
                "title": artifact.title,
                "files": files,
                "metadata": artifact.content,
            }
            # Retain the simple path/format keys for compact single-file programs.
            if primary_path is not None:
                manifest["path"] = primary_path
            if format_hint:
                manifest["format"] = format_hint
            sources.append(
                ExpertMountedSource(
                    handle=handle,
                    kind=artifact.artifact_type,
                    ref=ref,
                    title=artifact.title,
                    paths=paths,
                    manifest=manifest,
                )
            )
        return tuple(sources)


__all__ = [
    "ExpertCodeExecutionError",
    "ExpertCodeExecutionResult",
    "ExpertCodeExecutionService",
]
