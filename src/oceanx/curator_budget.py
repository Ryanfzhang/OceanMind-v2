"""Durable conservative token reservations; restart cannot reset the daily limit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from oceanx.model_config import ocean_config_dir


class CuratorLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_rounds: int = Field(default=8, ge=1, le=32)
    max_output_tokens: int = Field(default=4096, ge=256, le=16384)
    review_token_budget: int = Field(default=200_000, ge=1)
    daily_token_budget: int = Field(default=1_000_000, ge=1)
    evidence_char_budget: int = Field(default=48_000, ge=1, le=256_000)
    timeout_seconds: float = Field(default=180, ge=1, le=1800)

    @classmethod
    def from_settings(cls) -> CuratorLimits:
        path = ocean_config_dir() / "settings.json"
        if not path.exists():
            return cls()
        settings = json.loads(path.read_text())
        if not isinstance(settings, dict):
            raise ValueError("Settings must be a JSON object")  # noqa: TRY004 - invalid config value
        return cls.model_validate(settings.get("curator_limits", {}))


class CuratorBudgetExceeded(ValueError):
    pass


class ReviewBudget:
    def __init__(self, store, workspace_id: str, limits: CuratorLimits) -> None:
        self.store, self.workspace_id, self.limits = store, workspace_id, limits
        self.reserved = 0

    def reserve(self, messages, schema: str) -> str:
        # UTF-8 bytes are a deliberately conservative input estimate, NOT billing
        # tokens. Reserve output too; missing usage or crashes keep the full charge.
        amount = sum(
            len(str(m.content).encode("utf-8"))
            + len(str(getattr(m, "tool_calls", "")).encode("utf-8"))
            + 256
            for m in messages
        )
        amount += len(schema.encode("utf-8")) + self.limits.max_output_tokens + 2048
        if self.reserved + amount > self.limits.review_token_budget:
            raise CuratorBudgetExceeded("Per-review token reservation budget exhausted")
        now = datetime.now(UTC).isoformat()
        call_id = "curator_call_" + uuid4().hex
        with self.store._transaction() as db:
            used = db.execute(
                "SELECT COALESCE(SUM(reserved_tokens), 0) FROM curator_calls WHERE day = ?",
                (now[:10],),
            ).fetchone()[0]
            if used + amount > self.limits.daily_token_budget:
                raise CuratorBudgetExceeded("Daily learning budget exhausted")
            db.execute(
                "INSERT INTO curator_calls VALUES (?, ?, ?, ?, NULL, 'reserved', ?)",
                (call_id, self.workspace_id, now[:10], amount, now),
            )
        self.reserved += amount
        return call_id

    def finish(self, call_id: str, response) -> None:
        usage = getattr(response, "usage_metadata", None) or {}
        actual = usage.get("total_tokens")
        actual = actual if isinstance(actual, int) and actual >= 0 else None
        with self.store._transaction() as db:
            previous = db.execute(
                "SELECT reserved_tokens FROM curator_calls WHERE call_id = ?", (call_id,)
            ).fetchone()
            # Never refund an estimate; provider usage is recorded separately for tuning.
            db.execute(
                "UPDATE curator_calls SET state = 'returned', actual_tokens = ?, reserved_tokens = MAX(reserved_tokens, ?) WHERE call_id = ?",
                (actual, actual or 0, call_id),
            )
        if previous:
            self.reserved += max(0, (actual or 0) - previous[0])


def record_attempt(
    store, review_id: str, workspace_id: str, ids: list[str], state: str, reads: list[dict]
) -> None:
    now = datetime.now(UTC).isoformat()
    with store._transaction() as db:
        db.execute(
            """INSERT INTO curator_review_attempts VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(review_id) DO UPDATE SET state=excluded.state,
            evidence_reads_json=excluded.evidence_reads_json, updated_at=excluded.updated_at""",
            (review_id, workspace_id, json.dumps(ids), state, json.dumps(reads), now, now),
        )
