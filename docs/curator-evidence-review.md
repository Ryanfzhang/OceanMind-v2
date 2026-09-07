# Evidence-aware Skill Curator

This is an upgrade to the **explicit experience inbox**, not an automatic
conversation recap. No saved notes means no review/model call. The Coordinator's
completion decision, Expert sessions, and foreground research lifecycle are unchanged.

## Capture

Agents still call `ocean_save_experience(text)` with one optional concise note
(2,000 characters maximum). The prompt asks for applicability, the problem or
correction, the effective method, verification, and remaining uncertainty. These
are writing guidance, not required form fields or backend scientific rules.
Preferences, untested suggestions, and validated methods must remain distinct.

The store attaches the research request, task, Expert/work-order identity, tool
call/turn IDs, transcript/history positions, loaded Skill identities/versions,
and existing execution IDs. It stores references, not extra copies of datasets,
figures, or notebooks. Older notes retain their existing identities; missing
source metadata is not invented.

## Review and permissions

- Only explicit pending notes enter the periodic Curator inbox (default batch 16).
- The originating round must have a recorded terminal state. Failed/interrupted
  rounds can contain useful lessons; terminal does **not** mean scientifically valid.
- Review/publication waits while the task or its Experts are running. Identity
  checks are repeated after review and within the atomic Skill installation.
- Initial context contains notes, source references, terminal-state metadata,
  and the Skill catalog, not the whole conversation.
- `read_skill(name)` reads an exact catalog Skill before an update.
- `read_evidence(experience_id, kind, record_id, cursor, offset)` lists related
  record IDs (8 per page) or reads one record (4,000 characters per page).
  Kinds are `conversation`, `results`, `executions`, and `expert_messages`.
  Conversation/results/executions are restricted to the note's task, including
  later follow-ups; Expert history is restricted to the originating logical Expert.
- The reviewer cannot choose another workspace/task, run code, fetch URLs, or
  read arbitrary files. Source text is untrusted evidence, never instructions.
- Known credential patterns, signed URL queries, home paths, and stored reasoning
  fields are filtered before disclosure. Pattern filtering is defence in depth,
  **not** a guarantee of detecting every possible secret. Agents must never save
  credentials. Oversized records (>256,000 characters) are explicitly unavailable;
  the reviewer must use related summaries or keep the note pending.
- The LLM decides whether to ignore, defer, report a product bug, create, or update.
  It should prefer existing Skills, preserve applicability/uncertainty, and inspect
  final results and corrections when needed. No repeat-count threshold is required.

## Cost control

Optional `curator_limits` in the existing OceanMind `settings.json`:

```json
{
  "curator_limits": {
    "max_rounds": 8,
    "max_output_tokens": 4096,
    "review_token_budget": 200000,
    "daily_token_budget": 1000000,
    "evidence_char_budget": 48000,
    "timeout_seconds": 180
  }
}
```

Preserve the other settings/profiles when adding this section. Restart the backend
to apply limits. Invalid limits pause learning, not the research application.

Budgets use **conservative token reservations**: UTF-8 input size plus tool-schema
and framing allowance plus maximum output tokens, charged before each model call.
They are not exact billing tokens or dollar limits. Actual provider-reported token
usage is recorded separately; cached tokens are not treated as free. Reservations
are not refunded after missing usage, timeout, cancellation, or process exit.
The UTC daily counter is shared across workspaces using the same state database,
and survives restarts. Separate state databases do not share a global account cap.

Budget/timeout failures leave notes pending. Completed semantic decisions are not
replayed; uncertain notes are eligible for re-review after a day. A failed review
retries on a later periodic tick, subject to the persisted daily budget. Recovery
restarts an unfinished review from its notes; it does not replay an interrupted
model tool loop. Review attempts and consulted record references are retained in
SQLite. **No automatic recap or all-library consolidation pass is added.**

## History and recovery

The existing frontend shows Skill changes under the originating research round,
including the version and review reason. History and explicit rollback are available
through the CLI (there is no new frontend rollback button in this change):

```sh
ocean learning-status --state-dir /path/to/state
ocean skill-history --state-dir /path/to/state --workspace-id ws_example --skill example-skill
ocean skill-rollback --state-dir /path/to/state --workspace-id ws_example --skill example-skill --version 1 --expected-version 3
```

Rollback creates version 4 with version 1's content; it does not delete versions
2/3 or rewrite source notes. Version `0` restores the bundled original where one
exists. The expected-version guard prevents overwriting a concurrent update.
The current loaded context of a running Expert is not rewritten: future Skill
loads use the new version. Rollback is user-operated, never an autonomous Curator tool.

Schema migration 46 adds provenance, usage, and review ledgers. Existing databases
are backed up using the normal pre-migration backup mechanism.
