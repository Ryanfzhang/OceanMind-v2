"""Crash-consistent recognition of Expert executions and formal task results."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ocean_partner.backend.store import CodeExecutionRecord, RequestStore
from ocean_partner.task_results import TaskResultStore
from ocean_partner.task_workspace import TaskWorkspaceProjector


log = logging.getLogger(__name__)

_FINGERPRINT_TRANSIENT_KEYS = frozenset(
    {
        "execution_id",
        "duration_seconds",
        "logs",
        "output_root",
        "input_manifest",
        "code_path",
        "work_root",
        "attempt_number",
        "attempt_limit",
        "output_bytes",
        "ended_at",
        "fingerprint",
    }
)


def execution_result_fingerprint(payload: dict[str, Any]) -> str:
    """Return the stable scientific fingerprint shared by write and recovery paths."""

    stable = {
        key: value
        for key, value in payload.items()
        if key not in _FINGERPRINT_TRANSIENT_KEYS
    }
    return hashlib.sha256(
        json.dumps(
            stable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def write_execution_result_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Atomically publish the manifest that proves one execution reached settlement."""

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class ExpertRecoveryReport:
    settled_executions: int
    failed_executions: int
    restored_result_bindings: int
    interrupted_workstreams: int


class ExpertRecoveryService:
    """Reconcile all durable Expert effects before interrupted work is closed."""

    def __init__(
        self,
        *,
        store: RequestStore,
        task_workspaces: TaskWorkspaceProjector,
        task_results: TaskResultStore,
    ) -> None:
        self.store = store
        self.task_workspaces = task_workspaces
        self.task_results = task_results

    def recover(self) -> ExpertRecoveryReport:
        settled = self._settle_execution_manifests()
        failed = self.store.fail_running_code_executions()
        bindings = self._restore_result_bindings()
        interrupted = self.store.interrupt_active_team_work()
        return ExpertRecoveryReport(
            settled_executions=settled,
            failed_executions=failed,
            restored_result_bindings=bindings,
            interrupted_workstreams=interrupted,
        )

    def _settle_execution_manifests(self) -> int:
        recovered = 0
        for record in self.store.list_running_code_executions():
            payload = self._verified_execution_payload(record)
            if payload is None:
                continue
            ended_at = str(payload.pop("recovered_ended_at"))
            try:
                self.store.finish_code_execution(
                    execution_id=record.execution_id,
                    state="succeeded",
                    result=payload,
                    ended_at=ended_at,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                log.warning(
                    "Could not settle recovered Expert execution %s: %s",
                    record.execution_id,
                    exc,
                )
                continue
            recovered += 1
        return recovered

    def _verified_execution_payload(
        self, record: CodeExecutionRecord
    ) -> dict[str, Any] | None:
        code_path_value = record.request.get("code_path")
        if not isinstance(code_path_value, str) or not code_path_value:
            return None
        task_root = self.task_workspaces.ensure_task_root(record.task_id).resolve()
        work_root = Path(code_path_value).resolve().parent
        try:
            work_root.relative_to(task_root)
        except ValueError:
            return None
        manifest_path = work_root / "result-bundles" / f"{record.execution_id}.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(manifest, dict):
            return None
        if (
            manifest.get("schema_version") != "ocean-execution-result/v1"
            or manifest.get("execution_id") != record.execution_id
            or manifest.get("state") != "succeeded"
        ):
            return None
        fingerprint = manifest.get("fingerprint")
        if (
            not isinstance(fingerprint, str)
            or fingerprint != execution_result_fingerprint(manifest)
        ):
            return None

        output_names = manifest.get("output_files")
        output_records = manifest.get("outputs")
        if not isinstance(output_names, list) or not isinstance(output_records, list):
            return None
        records_by_name = {
            item.get("name"): item
            for item in output_records
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if len(records_by_name) != len(output_records) or set(records_by_name) != set(
            output_names
        ):
            return None

        output_root = work_root / "executions" / record.execution_id / "outputs"
        verified_outputs: list[dict[str, Any]] = []
        total_bytes = 0
        for name in output_names:
            if not isinstance(name, str) or not self._safe_output_name(name):
                return None
            candidate = output_root / name
            if candidate.is_symlink():
                return None
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(output_root.resolve())
            except (OSError, ValueError):
                return None
            if not resolved.is_file():
                return None
            raw_size = resolved.stat().st_size
            digest = self._sha256(resolved)
            expected = records_by_name[name]
            if expected.get("bytes") != raw_size or expected.get("sha256") != digest:
                return None
            verified_outputs.append(
                {"name": name, "bytes": raw_size, "sha256": digest}
            )
            total_bytes += raw_size

        logs_root = work_root / "executions" / record.execution_id / "logs"
        stdout = self._read_text(logs_root / "stdout.txt", manifest.get("stdout"))
        stderr = self._read_text(logs_root / "stderr.txt", manifest.get("stderr"))
        ended_at = manifest.get("ended_at")
        if not isinstance(ended_at, str) or not ended_at:
            ended_at = record.started_at
        attempt_number = self._positive_int(
            manifest.get("attempt_number", record.request.get("attempt_number")), 1
        )
        attempt_limit = self._positive_int(
            manifest.get("attempt_limit", record.request.get("attempt_limit")),
            attempt_number,
        )
        return {
            "execution_id": record.execution_id,
            "state": "succeeded",
            "returncode": manifest.get("returncode"),
            "stdout": stdout,
            "stderr": stderr,
            "duration_seconds": float(manifest.get("duration_seconds", 0.0)),
            "output_files": list(output_names),
            "outputs": verified_outputs,
            "output_bytes": total_bytes,
            "limit_trigger": manifest.get("limit_trigger"),
            "code_path": str(work_root / "analysis.py"),
            "work_root": str(work_root),
            "result_bundle_path": str(manifest_path),
            "result_fingerprint": fingerprint,
            "attempt_number": attempt_number,
            "attempt_limit": max(attempt_number, attempt_limit),
            "reused_existing_execution": False,
            # Private recovery input, removed before the payload is committed.
            "recovered_ended_at": ended_at,
        }

    def _restore_result_bindings(self) -> int:
        restored = 0
        for work in self.store.list_active_team_work():
            task_id = work.work_order.task_id
            if task_id is None:
                continue
            for result in self.task_results.list(task_id=task_id):
                if (
                    result.work_order_id != work.work_order.work_order_id
                    or result.execution_id is None
                    or not result.execution_output_names
                    or result.kind not in {"interactive_view", "report"}
                    or result.ref in work.checkpoint.result_refs
                ):
                    continue
                bindings = {
                    output_name: (result.execution_id, result.ref)
                    for output_name in result.execution_output_names
                }
                try:
                    self.store.record_workstream_results(
                        work.work_order.work_order_id,
                        (result.ref,),
                        output_bindings=bindings,
                        result_metadata={
                            "kind": result.kind,
                            "title": result.title,
                            "summary": result.summary,
                        },
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    log.warning(
                        "Could not restore ResultBundle binding for %s: %s",
                        result.ref.key,
                        exc,
                    )
                    continue
                restored += 1
        return restored

    @staticmethod
    def _safe_output_name(value: str) -> bool:
        path = PurePosixPath(value)
        return bool(
            not path.is_absolute()
            and path.parts
            and all(part not in {"", ".", ".."} for part in path.parts)
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _read_text(path: Path, fallback: object) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return str(fallback or "")

    @staticmethod
    def _positive_int(value: object, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default
