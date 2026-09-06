"""Headless local client of the unchanged Desktop protocol, for benchmark runs.

Each case owns a backend process, database and workspace. Dataset imports are
read-only local references. This runner never grades science, schedules Experts,
or treats a transport completion as a scientifically successful answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ocean_partner import __version__

_PREFIX = "OHJSON:"
_TERMINALS = {"request.completed", "request.failed", "request.cancelled", "system.error"}
_LOG_LIMIT = 64 * 1024 * 1024


class QueryCase(BaseModel):
    """Only model-visible inputs; hidden grading references belong elsewhere."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    query: str = Field(min_length=1, max_length=32_000)
    datasets: list[Path] = Field(default_factory=list, max_length=64)
    timeout_seconds: float = Field(default=3600, gt=0, le=604800, allow_inf_nan=False)
    literature_mode: Literal["ask_before_download", "auto_download_open_access", "search_only"] = "ask_before_download"
    selected_papers: list[str] = Field(default_factory=list, max_length=30)
    permission_tools: list[str] = Field(default_factory=list, max_length=30)
    answers: dict[str, str] = Field(default_factory=dict)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


def load_queries(path: Path) -> list[QueryCase]:
    cases: list[QueryCase] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                case = QueryCase.model_validate_json(line)
                if case.id in seen:
                    raise ValueError(f"duplicate case id: {case.id}")
                case = resolve_datasets(case, path.resolve().parent)
            except ValueError as exc:
                raise ValueError(f"{path.name}:{number}: {exc}") from exc
            seen.add(case.id)
            cases.append(case)
    if not cases:
        raise ValueError("Query file is empty")
    return cases


def resolve_datasets(case: QueryCase, base: Path) -> QueryCase:
    paths = []
    for item in case.datasets:
        candidate = item.expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            candidate = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"Dataset does not exist: {candidate}") from exc
        if not (candidate.is_file() or candidate.is_dir()) or candidate == Path(candidate.anchor):
            raise ValueError(f"Invalid dataset path: {candidate}")
        paths.append(candidate)
    return case.model_copy(update={"datasets": paths})


def interaction_answer(case: QueryCase, payload: dict[str, Any]) -> str | None:
    """Only act on explicit, exact user-supplied choices; never select/approve all."""
    kind = payload.get("kind")
    if kind == "permission":
        return "allow" if payload.get("tool_name") in case.permission_tools else None
    if kind == "question":
        return case.answers.get(payload.get("question", "")) or None
    if kind == "paper_selection" and case.selected_papers:
        normalize = lambda value: " ".join(str(value).split()).casefold().rstrip("/")
        wanted = {normalize(item) for item in case.selected_papers}
        matches: dict[str, set[str]] = {selector: set() for selector in wanted}
        for option in payload.get("options", []):
            hits = wanted & {normalize(option.get("title", "")), normalize(option.get("url", ""))}
            for hit in hits:
                matches[hit].add(option["paper_id"])
        if all(len(ids) == 1 for ids in matches.values()):
            selected_set = set().union(*matches.values())
            selected = [option["paper_id"] for option in payload.get("options", []) if option["paper_id"] in selected_set]
            return json.dumps({"selected_paper_ids": selected})
    return None


class BackendFailure(RuntimeError):
    def __init__(self, event: dict[str, Any]):
        self.event = event
        super().__init__(json.dumps(event.get("payload", {}), ensure_ascii=False))


class NeedsInteraction(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(str(payload.get("question", "Interaction required")))


class BatchClient:
    """One trusted local stdio client; no network listener and no UI changes."""

    def __init__(self, directory: Path, case: QueryCase) -> None:
        self.directory = directory
        self.case = case
        self.process: asyncio.subprocess.Process | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.context: dict[str, Any] = {}
        self.revision = 0
        self.active_request: str | None = None
        self.events_size = 0
        self.events_truncated = False

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "ocean_partner", "backend",
            "--state-dir", str(self.directory / "state"), "--no-skill-curator",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=_LOG_LIMIT,
            start_new_session=os.name == "posix",
        )
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        await self.request("system.handshake", {
            "client_kind": "desktop", "client_version": __version__,
            "supported_protocol_versions": [2],
        })
        self.context["workspace_id"] = f"ws_batch_{uuid4().hex}"
        workspace = self.directory / "workspace"
        workspace.mkdir()
        await self.request("workspace.open", {"path": str(workspace)})
        task = await self.request("task.create", {"title": self.case.id})
        self.context["task_id"] = task["payload"]["result"]["task"]["task_id"]
        for path in self.case.datasets:
            await self.request("dataset.import", {
                "local_path": str(path), "materialization_level": "local_reference",
            })

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        size = 0
        with (self.directory / "backend.log").open("wb") as handle:
            while chunk := await self.process.stderr.read(65536):
                remaining = max(0, _LOG_LIMIT - size)
                handle.write(chunk[:remaining])
                size += len(chunk)

    async def send(self, kind: str, payload: dict[str, Any]) -> str:
        assert self.process is not None and self.process.stdin is not None
        request_id = f"req_{uuid4().hex}"
        frame: dict[str, Any] = {
            "protocol_version": 2, "request_id": request_id, "type": kind, "payload": payload,
        }
        if kind != "system.handshake":
            frame["context"] = dict(self.context)
            frame["expected_workspace_revision"] = self.revision
        self.process.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()
        return request_id

    async def receive(self) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                raise RuntimeError("Backend exited before a terminal response; see backend.log")
            line = raw.decode("utf-8")
            if not line.startswith(_PREFIX):
                continue  # Libraries can print non-protocol diagnostics.
            event = json.loads(line[len(_PREFIX):])
            payload = event.get("payload", {})
            if event["type"] == "system.ready":
                self.context.update(client_id=payload["client_id"], session_id=payload["session_id"])
            revision = payload.get("workspace_revision")
            if isinstance(revision, int):
                self.revision = max(self.revision, revision)
            encoded = (json.dumps(event, ensure_ascii=False) + "\n").encode()
            if self.events_size + len(encoded) <= _LOG_LIMIT:
                with (self.directory / "events.jsonl").open("ab") as handle:
                    handle.write(encoded)
                self.events_size += len(encoded)
            else:
                self.events_truncated = True
            return event

    async def request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = await self.send(kind, payload)
        while True:
            event = await self.receive()
            if event.get("request_id") != request_id:
                continue
            if event["type"] == "system.ready":
                return event
            if event["type"] in _TERMINALS:
                if event["type"] != "request.completed":
                    raise BackendFailure(event)
                return event

    async def analyze(self) -> dict[str, Any]:
        self.active_request = await self.send("session.submit", {
            "text": self.case.query, "literature_acquisition_mode": self.case.literature_mode,
        })
        response_ids: set[str] = set()
        while True:
            event = await self.receive()
            if event.get("request_id") in response_ids and event["type"] in _TERMINALS:
                if event["type"] != "request.completed":
                    raise BackendFailure(event)
                continue
            if event.get("request_id") != self.active_request:
                continue
            if event["type"] == "interaction.requested":
                answer = interaction_answer(self.case, event["payload"])
                if answer is None:
                    raise NeedsInteraction(event["payload"])
                response_ids.add(await self.send("interaction.respond", {
                    "interaction_id": event["payload"]["interaction_id"], "answer": answer,
                }))
            if event["type"] in _TERMINALS:
                self.active_request = None
                return event

    async def close(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.returncode is None:
                async with asyncio.timeout(15):
                    if self.active_request and self.context:
                        await self.request("request.cancel", {
                            "target_request_id": self.active_request,
                            "reason": "Headless case stopped by its runner",
                        })
                    if self.context:
                        await self.request("system.shutdown", {})
                    if self.process.stdin:
                        self.process.stdin.close()
                    await self.process.wait()
        except (TimeoutError, OSError, RuntimeError, ValueError):
            pass
        finally:
            if self.process.returncode is None:
                if os.name == "posix":
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    self.process.kill()
                await self.process.wait()
            if self.stderr_task:
                try:
                    await asyncio.wait_for(self.stderr_task, 2)
                except TimeoutError:
                    self.stderr_task.cancel()


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def run_case(case: QueryCase, directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=False)
    _write_json(directory / "query.json", case.model_dump(mode="json"))
    client = BatchClient(directory, case)
    started = time.monotonic()
    result: dict[str, Any] = {"id": case.id, "directory": str(directory), "status": "failed"}
    interrupted = False
    try:
        async with asyncio.timeout(case.timeout_seconds):
            await client.start()
            event = await client.analyze()
            result["terminal_event"] = event
            result["status"] = {
                "request.completed": "completed", "request.cancelled": "cancelled",
            }.get(event["type"], "failed")
            payload = event.get("payload", {}).get("result", {})
            (directory / "answer.md").write_text(payload.get("assistant_text", ""), encoding="utf-8")
            result["coordinator_usage"] = payload.get("usage")
            result["expert_usage"] = payload.get("team_provenance", {}).get("actual_usage")
            outputs = await client.request("task.output.list", {"task_id": client.context["task_id"]})
            _write_json(directory / "outputs.json", outputs["payload"]["result"])
    except NeedsInteraction as exc:
        result.update(status="needs_interaction", interaction=exc.payload)
    except TimeoutError:
        result.update(status="timed_out", error="Case wall-time budget exceeded")
    except asyncio.CancelledError:
        result.update(status="cancelled", error="Runner interrupted")
        interrupted = True
    except BackendFailure as exc:
        result.update(status="failed", terminal_event=exc.event)
    except Exception as exc:  # noqa: BLE001 - persist one infrastructure failure, then continue the batch.
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        await client.close()
        result.update(
            task_id=client.context.get("task_id"), elapsed_seconds=round(time.monotonic() - started, 3),
            events_truncated=client.events_truncated,
        )
        _write_json(directory / "result.json", result)
    if interrupted:
        raise asyncio.CancelledError
    return result


async def run_batch(cases: list[QueryCase], output: Path, *, resume: bool = False) -> list[dict[str, Any]]:
    """Run sequentially; resume skips completed cases, retries others in new folders.

    Resume is *batch* resume, not a promise to resume a partially spent model turn.
    The source manifest fingerprint is metadata-only; it does not hash large data.
    """
    output = output.expanduser().resolve()
    if len({case.id for case in cases}) != len(cases) or not cases:
        raise ValueError("Batch requires nonempty, unique case IDs")
    for case in cases:
        for source in case.datasets:
            if output == source or output.is_relative_to(source) or source.is_relative_to(output):
                raise ValueError("Batch output and source data must be separate, non-overlapping paths")
    manifest = {
        "version": __version__, "cases": [case.model_dump(mode="json") for case in cases],
        "source_stats": {
            str(path): {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
            for case in cases for path in case.datasets
        },
    }
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if output.exists():
        if not resume:
            raise ValueError("Output directory exists; choose a new directory or use --resume")
        prior = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if prior.get("fingerprint") != fingerprint:
            raise ValueError("Query/source manifest changed; use a new output directory")
    else:
        output.mkdir(parents=True)
        _write_json(output / "manifest.json", {**manifest, "fingerprint": fingerprint})
    lock = output / ".runner.lock"
    try:
        lock_handle = lock.open("x")
    except FileExistsError as exc:
        raise ValueError("Batch is locked; do not run two writers in the same output directory") from exc
    results = []
    # SIGTERM from a scheduler should use the same cancel/cleanup path as Ctrl+C.
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    if os.name == "posix" and current is not None:
        loop.add_signal_handler(signal.SIGTERM, current.cancel)
    try:
        with lock_handle:
            lock_handle.write(str(os.getpid()))
            lock_handle.flush()
            for case in cases:
                case_root = output / case.id
                attempts = sorted(case_root.glob("attempt-*/result.json")) if case_root.exists() else []
                prior = json.loads(attempts[-1].read_text(encoding="utf-8")) if attempts else None
                if resume and prior and prior.get("status") == "completed":
                    results.append(prior)
                    print(f"[{case.id}] skipped (completed)", file=sys.stderr, flush=True)
                    continue
                directory = case_root / f"attempt-{time.time_ns()}-{uuid4().hex[:8]}"
                print(f"[{case.id}] running", file=sys.stderr, flush=True)
                result = await run_case(case, directory)
                results.append(result)
                with (output / "results.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(f"[{case.id}] {result['status']}", file=sys.stderr, flush=True)
    finally:
        if os.name == "posix":
            loop.remove_signal_handler(signal.SIGTERM)
        lock.unlink()
    return results
