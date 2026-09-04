# ADR 0006: Model Data Disclosure And Log Redaction

- Status: Accepted
- Date: 2026-07-11
- Decision IDs: OD-12

## Context

Sandboxing an analysis subprocess prevents arbitrary network access from that
process, but it does not prevent an allowed tool result from sending local
data, source paths, or logs to a model provider.  Ocean needs a workspace
policy before data is read into a model-facing response, with redaction as a
secondary defense rather than the permission mechanism.

## Decision

Every workspace owns a versioned `ModelDataDisclosurePolicy` with one decision
per category:

```text
metadata
aggregate_statistics
raw_samples
document_text
diagnostic_excerpts
```

The local policy history is immutable. A version created from an explicit TUI
confirmation records the confirming request ID; an internal/default policy row
is represented as unconfirmed so it cannot be mistaken for a user decision.

Each category is `allow`, `prompt`, or `deny`. The initial policy allows
bounded metadata and aggregate statistics, while raw samples, document text,
and diagnostic excerpts are `deny`. A future `prompt` request is denied unless
an explicit transport-bound approval callback authorizes that exact disclosure.
A `deny` request returns a safe structured refusal and must not smuggle values
through an error message.

The Phase 0.5 data tools enforce the decision before reading a raw sample.  The
spike uses a maximum of 16 points and 2,048 serialized bytes as deliberately
small probe budgets; Phase 1 carries policy-versioned budgets forward and may
raise them only within the plan's documented endpoint hard cap.  Full
stdout/stderr remain local run artifacts.  A normal `analysis_run` result has
only status, execution trust, checks, output counts/bytes, and project-relative
manifest location.

Raw diagnostics require the explicit `analysis_diagnostic_excerpt` operation.
It applies the policy, enforces a character bound, removes the run-root path,
redacts common secret assignments and long numeric tables, then returns the
sanitized excerpt.  Redaction is defense in depth and is not presented as a
complete sensitive-data classifier.

Every decision and successful disclosure creates a local audit record with:

```text
event, timestamp_utc, policy_version, provider_id, model_id, tool_name,
category, subject, decision, approved, requested_bytes, disclosed_bytes,
content_sha256
```

The audit never embeds the disclosed raw content.  Changing a policy to be
stricter affects later requests immediately; loosening it does not resend old
content.  A provider/model change must cause the policy to be re-evaluated in
the future workspace service.

## Consequences

- A local execution claim is never treated as a claim that local data did not
  leave the machine.  The disclosure audit states what the model-facing tool
  was allowed to receive.
- No paper/document-text tool is enabled by this ADR.  The `document_text`
  category reserves the decision point until OD-05 selects the literature
  ingestion and untrusted-document contract.
- Phase 1 replaces the spike callback with a transport-bound user permission
  request and persistent workspace policy record.  A graphical client cannot
  fabricate that approval.

## Verification

`tests/test_spikes/test_ocean_walking_skeleton.py` proves that a raw sample
obeys the policy point cap, adversarial stdout remains local, an approved
excerpt removes a secret/path/numeric table, and the resulting audit contains
policy/provider/model/tool fields without the raw secret.
