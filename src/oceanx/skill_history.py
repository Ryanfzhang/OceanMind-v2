"""Explicit user rollback; restoration creates a new revision, never rewrites history."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from oceanx.backend.store import RequestStoreError
from oceanx.skills import LITERATURE_CAPABILITY, load_ocean_skill


def rollback_skill(
    store, *, workspace_id: str, skill_name: str, version: int, expected_version: int
):
    if version < 0:
        raise RequestStoreError("Version must be non-negative (0 restores the bundled Skill)")
    with store._transaction() as db:
        latest = db.execute(
            "SELECT * FROM evolved_skill_documents WHERE workspace_id = ? AND skill_name = ? ORDER BY version DESC LIMIT 1",
            (workspace_id, skill_name),
        ).fetchone()
        if latest is None or latest["version"] != expected_version:
            raise RequestStoreError("Skill changed or does not exist; inspect history again")
        if version == 0:
            content, metadata = load_ocean_skill(
                skill_name, capabilities=(LITERATURE_CAPABILITY,), role=None
            )
            description, roles = metadata.description, list(metadata.roles)
            source_ids = latest["source_experience_ids_json"]
        else:
            target = db.execute(
                "SELECT * FROM evolved_skill_documents WHERE workspace_id = ? AND skill_name = ? AND version = ?",
                (workspace_id, skill_name, version),
            ).fetchone()
            if target is None:
                raise RequestStoreError("Requested version does not exist in this workspace")
            content, description = target["content"], target["description"]
            roles, source_ids = (
                json.loads(target["roles_json"]),
                target["source_experience_ids_json"],
            )
        new_version = expected_version + 1
        db.execute(
            "UPDATE evolved_skill_documents SET status = 'retired' WHERE workspace_id = ? AND skill_name = ?",
            (workspace_id, skill_name),
        )
        db.execute(
            """INSERT INTO evolved_skill_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (
                workspace_id,
                skill_name,
                new_version,
                description,
                json.dumps(roles),
                content,
                hashlib.sha256(content.encode()).hexdigest(),
                source_ids,
                "user-rollback",
                f"User restored version {version}; superseded version {expected_version}.",
                datetime.now(UTC).isoformat(),
            ),
        )
    return next(
        item
        for item in store.list_evolved_skill_revisions(
            workspace_id=workspace_id, skill_name=skill_name, active_only=True
        )
    )
