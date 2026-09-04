# ADR 0015: Task Conversation Checkpoints

- Status: Accepted
- Date: 2026-07-14

## Context

The visible chat transcript is not sufficient to continue an agentic
conversation: tool-use and tool-result blocks, ordering, and compacted memory
are required by `QueryEngine`. Persisting transcript text as model context
would silently change model behavior after restart.

## Decision

After a successful task-bound agent request, the backend serializes the
canonical `ConversationMessage[]`, enforces a 10,000-message and 2 MiB limit,
hashes canonical JSON, and writes it as a versioned checkpoint. The request
terminal event, checkpoint, task generation, checkpoint pointer, and active
request clear commit in one SQLite transaction.

Only the backend can read checkpoint messages. It validates schema version and
SHA-256 before calling `QueryEngine.load_messages()`. Failed, cancelled, and
interrupted requests retain the preceding checkpoint and mark their display
rows interrupted instead of becoming memory.

## Consequences

- A renderer cannot inject history by modifying its transcript cache.
- Corrupt or unsupported checkpoint data fails closed with a typed protocol
  error; the read-only transcript remains available.
- A task runtime is cacheable only while live. On reconstruction it loads the
  durable checkpoint and regenerates workspace context and system prompt.

## Verification

- `tests/test_ocean_partner/test_research_tasks.py`
- `tests/test_ocean_partner/test_agent_router.py`
