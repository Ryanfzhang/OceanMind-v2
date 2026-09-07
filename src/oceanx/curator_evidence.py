"""Bounded, read-only evidence access for explicitly saved experience.

No filesystem or network access. IDs are resolved through an allowlisted note;
the model cannot choose a workspace, task, SQL query, or path. Database access is
kept here rather than exposing a general-purpose store tool to the reviewer.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oceanx.backend.store import RequestStore
    from oceanx.research_learning import SavedExperience


def redact(value: Any) -> Any:
    """Defence in depth for stored messages, not a guarantee against arbitrary secrets."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if re.search(
                    r"(?i)(password|secret|credential|api[_-]?key|access[_-]?token|authorization|cookie)",
                    key,
                )
                else redact(item)
            )
            for key, item in value.items()
            if key not in {"reasoning", "reasoning_content", "thinking", "encrypted_content"}
        }
    if isinstance(value, (list, tuple)):
        return [
            redact(item)
            for item in value
            if not (
                isinstance(item, dict)
                and item.get("type") in {"thinking", "reasoning", "redacted_thinking"}
            )
        ]
    if not isinstance(value, str):
        return value
    value = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED KEY]",
        value,
    )
    value = re.sub(
        r"https?://[^\s\"<>]+",
        lambda m: (
            re.sub(r"(https?://)([^/@]+@)", r"\1[REDACTED]@", m[0].split("?")[0])
            + ("?[REDACTED QUERY]" if "?" in m[0] else "")
        ),
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer [REDACTED]", value)
    value = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b",
        "[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)((?:api[_-]?key|password|secret|access[_-]?token|authorization|cookie)[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
        r"\1[REDACTED]",
        value,
    )
    return re.sub(r"/(?:Users|home)/[^\s\"'<>]+", "[PRIVATE PATH]", value)


def capture_source_context(store: RequestStore, **scope: Any) -> dict[str, Any]:
    with store._lock:
        db = store._connection
        seq = db.execute(
            "SELECT MAX(sequence) FROM task_transcript_items WHERE task_id = ?", (scope["task_id"],)
        ).fetchone()[0]
        usage = db.execute(
            """SELECT usage_id, resource_name, resource_version FROM resource_usage_records
            WHERE workspace_id = ? AND resource_kind = 'skill' AND
            ((? IS NOT NULL AND work_order_id = ?) OR
             (? IS NULL AND request_id = ? AND agent_id = ?))
            ORDER BY created_at, usage_id""",
            (
                scope["workspace_id"],
                scope["work_order_id"],
                scope["work_order_id"],
                scope["work_order_id"],
                scope["request_id"],
                scope["agent_id"],
            ),
        ).fetchall()
        work = store.get_team_work(scope["work_order_id"]) if scope["work_order_id"] else None
        expert_seq = None
        if work:
            order = work.work_order
            expert_seq = db.execute(
                "SELECT MAX(sequence) FROM expert_session_message_history WHERE workspace_id = ? AND task_scope = ? AND participant_key = ? AND job_key = ?",
                (
                    scope["workspace_id"],
                    scope["task_id"],
                    order.profile_id or f"{order.authority.value}:{order.semantic_role}",
                    order.job_key or order.work_order_id,
                ),
            ).fetchone()[0]
        return {
            "turn_id": scope["turn_id"],
            "tool_call_id": scope["tool_call_id"],
            "transcript_sequence_at_save": seq,
            "expert_history_sequence_at_save": expert_seq,
            "loaded_skills": [dict(row) for row in usage],
            "execution_ids_at_save": [
                row[0]
                for row in db.execute(
                    """SELECT execution_id FROM code_executions WHERE workspace_id = ? AND task_id = ?
                AND (? IS NULL OR work_order_id = ?) ORDER BY started_at, execution_id""",
                    (
                        scope["workspace_id"],
                        scope["task_id"],
                        scope["work_order_id"],
                        scope["work_order_id"],
                    ),
                ).fetchall()
            ],
        }


def round_context(store: RequestStore, note: SavedExperience) -> dict[str, Any]:
    """Freeze task evidence identity; unknown/running rounds never pass publication gates."""
    with store._lock:
        db = store._connection
        task = db.execute(
            "SELECT * FROM research_tasks WHERE task_id = ? AND workspace_id = ?",
            (note.task_id, note.workspace_id),
        ).fetchone()
        workflow = store.get_task_workflow(note.request_id)
        request = store.get_request(note.request_id)
        state = (
            workflow.state
            if workflow
            and workflow.task_id == note.task_id
            and workflow.workspace_id == note.workspace_id
            else "unknown"
        )
        if (
            request
            and request.task_id == note.task_id
            and request.workspace_id == note.workspace_id
        ):
            state = request.state
        active = bool(task and task["active_request_id"])
        running_work = db.execute(
            """SELECT 1 FROM team_work_records WHERE workspace_id = ?
            AND json_extract(work_order_json, '$.task_id') = ? AND state IN ('queued','running') LIMIT 1""",
            (note.workspace_id, note.task_id),
        ).fetchone()
        terminal = state in {
            "completed",
            "incomplete",
            "failed",
            "cancelled",
            "interrupted",
            "succeeded",
        }
        seq = db.execute(
            "SELECT MAX(sequence) FROM task_transcript_items WHERE task_id = ?", (note.task_id,)
        ).fetchone()[0]
        work_time = db.execute(
            "SELECT MAX(updated_at) FROM team_work_records WHERE workspace_id = ? AND json_extract(work_order_json, '$.task_id') = ?",
            (note.workspace_id, note.task_id),
        ).fetchone()[0]
        execution_stamp = db.execute(
            "SELECT COUNT(*), MAX(ended_at) FROM code_executions WHERE workspace_id = ? AND task_id = ?",
            (note.workspace_id, note.task_id),
        ).fetchone()
        history_stamp = db.execute(
            "SELECT COUNT(*), MAX(recorded_at) FROM expert_session_message_history WHERE workspace_id = ? AND task_scope = ?",
            (note.workspace_id, note.task_id),
        ).fetchone()
        context = {
            "state": state,
            "eligible": bool(task and terminal and not active and not running_work),
            "task_revision": task["task_revision"] if task else None,
            "latest_transcript_sequence": seq,
            "latest_work_update": work_time,
            "round_updated_at": workflow.updated_at
            if workflow
            else (request.updated_at if request else None),
            "execution_stamp": tuple(execution_stamp),
            "history_stamp": tuple(history_stamp),
        }
        context["fingerprint"] = hashlib.sha256(
            json.dumps(context, sort_keys=True).encode()
        ).hexdigest()
        return context


class CuratorEvidence:
    def __init__(
        self, store: RequestStore, notes: tuple[SavedExperience, ...], *, max_chars: int = 48_000
    ) -> None:
        self.store = store
        self.notes = {note.experience_id: note for note in notes}
        self.snapshots = {note.experience_id: round_context(store, note) for note in notes}
        self.max_chars = max_chars
        self.used_chars = 0
        self.reads: list[dict[str, Any]] = []

    def unchanged(self, experience_id: str) -> bool:
        current = round_context(self.store, self.notes[experience_id])
        return current["eligible"] and current == self.snapshots[experience_id]

    def _query(self, note: SavedExperience, kind: str) -> tuple[str, tuple]:
        if kind == "conversation":
            return (
                "SELECT item_id AS id, sequence AS position, role, text AS body FROM task_transcript_items WHERE task_id = ?",
                (note.task_id,),
            )
        if kind == "results":
            return (
                """SELECT work_order_id AS id, rowid AS position, state AS role,
                json_object('work_order', json(work_order_json), 'result', json(result_json)) AS body
                FROM team_work_records WHERE workspace_id = ? AND json_extract(work_order_json, '$.task_id') = ?""",
                (note.workspace_id, note.task_id),
            )
        if kind == "executions":
            return (
                """SELECT execution_id AS id, rowid AS position, state AS role,
                json_object('request', json(request_json), 'result', json(result_json)) AS body
                FROM code_executions WHERE workspace_id = ? AND task_id = ?""",
                (note.workspace_id, note.task_id),
            )
        if kind == "expert_messages":
            work = self.store.get_team_work(note.work_order_id) if note.work_order_id else None
            if (
                not work
                or work.workspace_id != note.workspace_id
                or work.work_order.task_id != note.task_id
            ):
                raise ValueError("This note has no associated Expert history")
            # Only the originating Expert session, including later corrections/follow-ups.
            return (
                """SELECT CAST(sequence AS TEXT) AS id, sequence AS position, 'expert_message' AS role,
                message_json AS body FROM expert_session_message_history
                WHERE workspace_id = ? AND task_scope = ? AND participant_key = ? AND job_key = ?""",
                (
                    note.workspace_id,
                    note.task_id,
                    work.work_order.profile_id
                    or f"{work.work_order.authority.value}:{work.work_order.semantic_role}",
                    work.work_order.job_key or work.work_order.work_order_id,
                ),
            )
        raise ValueError("kind must be conversation, results, executions, or expert_messages")

    def read(
        self,
        experience_id: str,
        kind: str = "conversation",
        record_id: str = "",
        cursor: int = 0,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List IDs (8/page), or read one ID (4K characters/page); no arbitrary paths."""
        if experience_id not in self.notes:
            raise ValueError("Experience is outside this review")
        if cursor < 0 or offset < 0:
            raise ValueError("Offsets must be non-negative")
        if self.used_chars >= self.max_chars:
            raise ValueError("Evidence read budget exhausted; keep uncertain notes pending")
        if not self.unchanged(experience_id):
            raise ValueError("Related task changed or is running; retry review later")
        note = self.notes[experience_id]
        sql, values = self._query(note, kind)
        with self.store._lock:
            if record_id:
                size = self.store._connection.execute(
                    f"SELECT length(body) FROM ({sql}) WHERE id = ?", (*values, record_id)
                ).fetchone()
                if size and size[0] and size[0] > 256_000:
                    raise ValueError(
                        "Record exceeds safe review size; inspect related result summaries or keep pending"
                    )
                rows = self.store._connection.execute(
                    f"SELECT * FROM ({sql}) WHERE id = ?", (*values, record_id)
                ).fetchall()
            else:
                rows = self.store._connection.execute(
                    f"SELECT id, position, role FROM ({sql}) WHERE position > ? ORDER BY position LIMIT 9",
                    (*values, cursor),
                ).fetchall()
        if record_id:
            if not rows:
                raise ValueError("Record is not related to this experience")
            raw = str(rows[0]["body"] or "")
            if kind != "conversation":
                parsed = json.loads(raw)
                if kind == "expert_messages":
                    # Never expose stored reasoning or hidden provider metadata.
                    parsed = {
                        key: parsed[key]
                        for key in ("role", "content", "tool_calls", "tool_call_id")
                        if key in parsed
                    }
                raw = json.dumps(redact(parsed), ensure_ascii=False)
            else:
                raw = redact(raw)
            size = min(4_000, self.max_chars - self.used_chars)
            content = raw[offset : offset + size]
            response = {
                "record_id": record_id,
                "content": content,
                "next_offset": offset + len(content) if offset + len(content) < len(raw) else None,
            }
            self.used_chars += len(content)
        else:
            response = {
                "records": [dict(row) for row in rows[:8]],
                "next_cursor": rows[7]["position"] if len(rows) > 8 else None,
            }
            if self.used_chars + len(json.dumps(response)) > self.max_chars:
                raise ValueError("Evidence read budget exhausted; keep uncertain notes pending")
            self.used_chars += len(json.dumps(response))
        self.reads.append(
            {
                "experience_id": experience_id,
                "kind": kind,
                "record_id": record_id,
                "cursor": cursor,
                "offset": offset,
            }
        )
        return response
