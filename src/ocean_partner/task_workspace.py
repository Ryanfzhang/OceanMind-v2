"""Materialize each task's sources, Expert work, results, views, and reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Any

from ocean_partner.artifacts.models import ArtifactVersion
from ocean_partner.backend.store import RequestStore, ResearchTaskRecord, TaskArtifactRecord
from ocean_partner.storage import OceanPaths

TASKS_DIRECTORY_NAME = "OceanMind Tasks"
TASK_MANIFEST_NAME = "task-manifest.json"
TASK_MANIFEST_SCHEMA = "ocean-task-workspace/v2"
EXPERT_SESSION_MANIFEST_NAME = "agent-workspace.json"
EXPERT_SESSION_MANIFEST_SCHEMA = "ocean-agent-workspace/v1"
_README_START = "<!-- OCEANMIND:START -->"
_README_END = "<!-- OCEANMIND:END -->"
_SUMMARY_START = "<!-- OCEANMIND:SUMMARY:START -->"
_SUMMARY_END = "<!-- OCEANMIND:SUMMARY:END -->"
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_DIRECTORIES = (
    "data",
    "analysis",
    "outputs",
    "views",
    "report",
    "supplementary",
    "results",
)


class TaskWorkspaceProjectionError(RuntimeError):
    """A task folder cannot be projected without risking canonical or user data."""


@dataclass(frozen=True)
class MaterializedFile:
    path: str
    canonical_uri: str
    mime_type: str
    sha256: str
    status: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "canonical_uri": self.canonical_uri,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "status": self.status,
        }


class TaskWorkspaceProjector:
    """Keep one stable, inspectable directory per research task."""

    def __init__(self, *, paths: OceanPaths, store: RequestStore) -> None:
        self.paths = paths
        self.store = store
        self._lock = threading.RLock()

    def sync(self, task_id: str) -> Path:
        with self._lock:
            task = self._task(task_id)
            root = self._task_root(self._workspace_root(task), task)
            for name in _DIRECTORIES:
                self._ensure_directory(root / name)
            previous = self._read_manifest(root)
            artifacts = self.store.list_task_artifacts(task_id=task_id)
            artifact_payloads, files = self._project_artifacts(
                root=root,
                artifacts=artifacts,
                previous=previous,
            )
            self._write_sources(root, artifacts)
            self._write_requirements(root)
            self._write_summary(root, task)
            manifest = {
                "schema_version": TASK_MANIFEST_SCHEMA,
                "task": task.as_summary(),
                "workspace_path": str(self._workspace_root(task)),
                "canonical_store": str(self.paths.root),
                "artifacts": artifact_payloads,
                "materialized_files": [item.as_payload() for item in files],
                "managed_directories": list(_DIRECTORIES),
            }
            self._atomic_json(root / TASK_MANIFEST_NAME, manifest)
            self._write_readme(root, task, artifacts)
            return root

    def ensure_task_root(self, task_id: str) -> Path:
        with self._lock:
            task = self._task(task_id)
            root = self._task_root(self._workspace_root(task), task)
            for name in _DIRECTORIES:
                self._ensure_directory(root / name)
            return root

    def write_supplementary_notebook(
        self,
        *,
        task_id: str,
        content: bytes,
    ) -> Path:
        """Write the task's single user-editable analysis notebook.

        Scientific payloads remain in their accepted TaskResult locations.  The
        notebook refers to those files and is the only supplementary file
        projected into the user-facing task directory.
        """

        with self._lock:
            root = self.ensure_task_root(task_id)
            target = root / "supplementary" / "analysis.ipynb"
            if target.exists() and (target.is_symlink() or not target.is_file()):
                raise TaskWorkspaceProjectionError(
                    "Supplementary analysis notebook path is not a regular file"
                )
            self._atomic_bytes(target, content)
            return target

    def expert_session_root(self, task_id: str, session_key: str) -> Path:
        """Return one stable root for a logical Expert session.

        A Coordinator follow-up is a new WorkOrder, but it is not a new
        participant. Keeping executions below the logical ``job_key`` lets a
        later delta round reuse durable results without reaching into another
        WorkOrder's private scratch directory.
        """

        with self._lock:
            root = self.ensure_task_root(task_id)
            sessions = root / "analysis" / "expert-sessions"
            self._ensure_directory(sessions)
            session_digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
            legacy_session = sessions / _safe_name(
                session_key,
                fallback="expert-session",
                limit=96,
            )
            preferred_session = sessions / (
                _safe_name(
                    session_key,
                    fallback="expert-session",
                    limit=72,
                )
                + "--"
                + session_digest[:12]
            )
            # Existing tasks keep their pre-v1 directory. New logical Agents
            # use a digest suffix so filename truncation can never merge two
            # otherwise distinct session keys.
            session = legacy_session if legacy_session.exists() else preferred_session
            self._ensure_directory(session)
            manifest_path = session / EXPERT_SESSION_MANIFEST_NAME
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise TaskWorkspaceProjectionError(
                        "Expert session ownership manifest is invalid"
                    ) from exc
                if (
                    manifest.get("schema_version") != EXPERT_SESSION_MANIFEST_SCHEMA
                    or manifest.get("task_id") != task_id
                    or manifest.get("session_key_sha256") != session_digest
                ):
                    raise TaskWorkspaceProjectionError(
                        "Expert session directory belongs to a different logical Agent"
                    )
            else:
                self._atomic_json(
                    manifest_path,
                    {
                        "schema_version": EXPERT_SESSION_MANIFEST_SCHEMA,
                        "task_id": task_id,
                        "session_key_sha256": session_digest,
                        "ownership": "single_logical_agent",
                        "workspace": "workspace",
                        "immutable_executions": "executions",
                        "publication_authority": "coordinator_only",
                    },
                )
            # These directories are private to this logical Agent. Follow-up
            # rounds reuse them; sibling Agents receive different session roots.
            for name in ("workspace", "executions", "result-bundles"):
                self._ensure_directory(session / name)
            return session

    def _task(self, task_id: str) -> ResearchTaskRecord:
        task = self.store.get_research_task(task_id)
        if task is None:
            raise TaskWorkspaceProjectionError(f"Research task is unavailable: {task_id}")
        return task

    def _workspace_root(self, task: ResearchTaskRecord) -> Path:
        workspace = self.store.workspace_snapshot(task.workspace_id)
        if workspace.path is None:
            raise TaskWorkspaceProjectionError("Task workspace has no durable local path")
        root = Path(workspace.path).expanduser().resolve()
        if not root.is_dir():
            raise TaskWorkspaceProjectionError(f"Task workspace directory is unavailable: {root}")
        return root

    def _task_root(self, workspace: Path, task: ResearchTaskRecord) -> Path:
        tasks = workspace / TASKS_DIRECTORY_NAME
        tasks.mkdir(parents=True, exist_ok=True)
        if tasks.is_symlink():
            raise TaskWorkspaceProjectionError("OceanMind Tasks cannot be a symbolic link")
        for candidate in tasks.iterdir():
            if candidate.is_dir() and not candidate.is_symlink():
                if self._read_manifest(candidate).get("task", {}).get("task_id") == task.task_id:
                    return candidate
        base = f"{_safe_name(task.title, fallback='Untitled task', limit=72)}--{_task_suffix(task.task_id)}"
        candidate = tasks / base
        index = 2
        while candidate.exists():
            candidate = tasks / f"{base}--{index}"
            index += 1
        candidate.mkdir()
        self._atomic_json(
            candidate / TASK_MANIFEST_NAME,
            {
                "schema_version": TASK_MANIFEST_SCHEMA,
                "task": task.as_summary(),
                "workspace_path": str(workspace),
                "canonical_store": str(self.paths.root),
                "artifacts": [],
                "materialized_files": [],
                "managed_directories": [],
            },
        )
        return candidate

    def _project_artifacts(
        self,
        *,
        root: Path,
        artifacts: list[TaskArtifactRecord],
        previous: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[MaterializedFile]]:
        prior_files = {
            str(item.get("path")): item
            for item in previous.get("materialized_files", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        payloads: list[dict[str, Any]] = []
        materialized: list[MaterializedFile] = []
        for link in artifacts:
            artifact = link.artifact
            target_directory = _artifact_directory(root, artifact)
            files: list[MaterializedFile] = []
            store_root = (
                artifact.content.get("store_root")
                if artifact.artifact_type == "dataset"
                and artifact.content.get("source_kind") == "directory"
                else None
            )
            canonical_root = None
            target_root = None
            if isinstance(store_root, str):
                canonical_root = self.paths.resolve_uri(artifact.manifest_uri).parent / store_root
                target_root = target_directory / _artifact_filename(artifact, store_root)
            for record in artifact.files:
                source = self.paths.resolve_uri(record.uri)
                if canonical_root is not None and target_root is not None:
                    try:
                        target = target_root / source.relative_to(canonical_root)
                    except ValueError as exc:
                        raise TaskWorkspaceProjectionError(
                            "Directory dataset file is outside its declared store root"
                        ) from exc
                else:
                    target = target_directory / _artifact_filename(artifact, source.name)
                projected = self._materialize(
                    root=root,
                    source=source,
                    target=target,
                    canonical_uri=record.uri,
                    mime_type=record.mime_type,
                    sha256=record.sha256,
                    prior=prior_files.get(target.relative_to(root).as_posix()),
                )
                files.append(projected)
                materialized.append(projected)
            payloads.append(
                {
                    "ref": artifact.ref.model_dump(mode="json"),
                    "artifact_type": artifact.artifact_type,
                    "schema_version": artifact.schema_version,
                    "title": artifact.title,
                    "summary": artifact.summary,
                    "relations": list(link.relations),
                    "origin_request_ids": list(link.origin_request_ids),
                    "linked_at": link.linked_at,
                    "canonical_manifest_uri": artifact.manifest_uri,
                    "content": artifact.content,
                    "files": [item.as_payload() for item in files],
                }
            )
        return payloads, materialized

    def _materialize(
        self,
        *,
        root: Path,
        source: Path,
        target: Path,
        canonical_uri: str,
        mime_type: str,
        sha256: str,
        prior: dict[str, Any] | None,
    ) -> MaterializedFile:
        if not source.is_file() or source.is_symlink() or _sha256(source) != sha256:
            raise TaskWorkspaceProjectionError(f"Canonical artifact file is unavailable: {canonical_uri}")
        target.parent.mkdir(parents=True, exist_ok=True)
        relative = target.relative_to(root).as_posix()
        if target.exists():
            if not target.is_file() or target.is_symlink():
                status = "conflict"
            elif _sha256(target) == sha256:
                status = "current"
            else:
                status = "user_modified" if prior is not None else "conflict"
            return MaterializedFile(relative, canonical_uri, mime_type, sha256, status)
        _clone_or_copy(source, target)
        if _sha256(target) != sha256:
            target.unlink(missing_ok=True)
            raise TaskWorkspaceProjectionError(f"Projected artifact checksum failed: {relative}")
        return MaterializedFile(relative, canonical_uri, mime_type, sha256, "materialized")

    def _write_sources(self, root: Path, artifacts: list[TaskArtifactRecord]) -> None:
        sources = [
            {
                "ref": link.artifact.ref.model_dump(mode="json"),
                "artifact_type": link.artifact.artifact_type,
                "title": link.artifact.title,
                "summary": link.artifact.summary,
                "content": link.artifact.content,
                "provenance": link.artifact.provenance,
                "canonical_manifest_uri": link.artifact.manifest_uri,
            }
            for link in artifacts
            if link.artifact.artifact_type in {"dataset", "paper"}
        ]
        self._atomic_json(
            root / "data" / "sources.json",
            {"schema_version": "ocean-task-sources/v2", "sources": sources},
        )

    @staticmethod
    def _write_requirements(root: Path) -> None:
        source = Path(__file__).resolve().parents[2] / "requirements.txt"
        content = (
            source.read_text(encoding="utf-8")
            if source.is_file()
            else resource_files("ocean_partner")
            .joinpath("resources/runtime/requirements.txt")
            .read_text(encoding="utf-8")
        )
        TaskWorkspaceProjector._atomic_text(root / "analysis" / "requirements.txt", content)

    def _write_summary(self, root: Path, task: ResearchTaskRecord) -> None:
        snapshot = self.store.task_snapshot(task_id=task.task_id, transcript_limit=500)
        assistants = [item for item in snapshot.transcript if item.role == "assistant" and not item.interrupted]
        if not assistants:
            return
        generated = (
            f"{_SUMMARY_START}\n# {task.title}\n\n"
            f"OceanMind task: `{task.task_id}`\n\n{assistants[-1].text.rstrip()}\n"
            f"{_SUMMARY_END}\n"
        )
        self._replace_managed_block(root / "report" / "summary.md", generated, _SUMMARY_START, _SUMMARY_END)

    def _write_readme(
        self,
        root: Path,
        task: ResearchTaskRecord,
        artifacts: list[TaskArtifactRecord],
    ) -> None:
        generated = "\n".join(
            [
                _README_START,
                f"# {task.title}",
                "",
                f"- OceanMind task: `{task.task_id}`",
                f"- Status: `{task.status}`",
                f"- Linked artifacts: {len(artifacts)}",
                "",
                "## Folder guide",
                "",
                "- `data/`: task inputs and source provenance",
                "- `analysis/`: Expert code, notebooks, and environment requirements",
                "- `outputs/`: derived data and supporting evidence",
                "- `views/`: interactive-view data and previews",
                "- `report/`: conclusions and formal reports",
                "- `supplementary/analysis.ipynb`: the single editable notebook for this analysis",
                "- `results/`: internal accepted-result storage used by the notebook (normally do not browse)",
                "",
                _README_END,
                "",
            ]
        )
        self._replace_managed_block(root / "README.md", generated, _README_START, _README_END)

    @staticmethod
    def _replace_managed_block(path: Path, generated: str, start_marker: str, end_marker: str) -> None:
        if not path.exists():
            TaskWorkspaceProjector._atomic_text(path, generated)
            return
        if not path.is_file() or path.is_symlink():
            TaskWorkspaceProjector._atomic_text(path.with_name(f"{path.stem}.oceanmind{path.suffix}"), generated)
            return
        existing = path.read_text(encoding="utf-8")
        start = existing.find(start_marker)
        end = existing.find(end_marker)
        if start >= 0 and end >= start:
            end += len(end_marker)
            TaskWorkspaceProjector._atomic_text(path, f"{existing[:start]}{generated.rstrip()}{existing[end:]}")
        else:
            TaskWorkspaceProjector._atomic_text(path.with_name(f"{path.stem}.oceanmind{path.suffix}"), generated)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        if path.exists():
            if path.is_symlink() or not path.is_dir():
                raise TaskWorkspaceProjectionError(f"Managed task path is not a directory: {path.name}")
            return
        path.mkdir(parents=True)

    @staticmethod
    def _read_manifest(root: Path) -> dict[str, Any]:
        path = root / TASK_MANIFEST_NAME
        if not path.is_file() or path.is_symlink():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        TaskWorkspaceProjector._atomic_text(
            path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            temporary.chmod(0o644)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            temporary.chmod(0o644)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _artifact_directory(root: Path, artifact: ArtifactVersion) -> Path:
    if artifact.artifact_type == "dataset":
        return root / ("outputs" if artifact.schema_version == "ocean-derived-dataset/v1" else "data")
    if artifact.artifact_type == "interactive_view":
        return root / "views"
    if artifact.artifact_type == "report":
        return root / "report"
    return root / "outputs"


def _artifact_filename(artifact: ArtifactVersion, source_name: str) -> str:
    source = Path(source_name)
    suffix = "".join(source.suffixes)[-24:]
    stem = source.name[: -len(suffix)] if suffix else source.name
    return (
        f"{_safe_name(artifact.title, fallback=artifact.artifact_type, limit=64)}--"
        f"{_safe_name(artifact.ref.artifact_id[-12:], limit=16)}--v{artifact.ref.version:04d}--"
        f"{_safe_name(stem, fallback=artifact.artifact_type, limit=36)}{suffix}"
    )


def _safe_name(value: str, *, fallback: str = "item", limit: int = 80) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = _INVALID_FILENAME.sub("-", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .-") or fallback
    if normalized.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        normalized = f"{normalized}-item"
    return normalized[:limit].rstrip(" .-") or fallback


def _task_suffix(task_id: str) -> str:
    token = task_id[5:] if task_id.startswith("task_") else task_id
    return _safe_name(token[:12], fallback=hashlib.sha256(task_id.encode()).hexdigest()[:12])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clone_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        try:
            import ctypes

            if ctypes.CDLL("/usr/lib/libSystem.B.dylib").clonefile(
                os.fsencode(source), os.fsencode(target), 0
            ) == 0:
                return
        except (AttributeError, OSError):
            pass
    shutil.copy2(source, target)


__all__ = ["TASKS_DIRECTORY_NAME", "TaskWorkspaceProjectionError", "TaskWorkspaceProjector"]
