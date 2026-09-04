# ADR 0004: Dataset Materialization And Reproducibility Levels

- Status: Accepted
- Date: 2026-07-11
- Decision IDs: OD-09

## Context

A checksum of a currently visible file does not mean the bytes required to
rerun an old analysis were saved.  Ocean must express both the identity it saw
and the strength of its replay promise, especially when an external source
changes during or after a run.

## Decision

DatasetArtifact and run input records use exactly one of four materialization
levels:

| Level | Required evidence | Rerun availability | UI wording |
| --- | --- | --- | --- |
| `materialized_snapshot` | Stored full input bytes, relative stored path, full checksum, source identity | `stored_bytes` | "Snapshot stored; this input can rerun while the project store is intact." |
| `cached_subset` | Stored subset bytes/checksum, parent identity, exact selection and query | `stored_subset` | "Only this selected subset is stored; the full source is not retained." |
| `immutable_remote_version` | Provider, asset ID, provider-backed immutable version, license snapshot, request query/identity | `remote_if_available` | "May be reacquired only while the named provider/version remains available and licensed." |
| `reference_only` | External URI and observed fingerprint/identity, with no stored old bytes | `not_guaranteed` | "This run references an external source and may not be reproducible later." |

Before and after every AnalysisRun, the backend records an identity of every
input.  For ordinary local files this is SHA-256, byte size, and mtime; the
source contract for a Zarr or remote provider is refined later without
upgrading a metadata fingerprint into a content hash.  A pre/post mismatch
makes the attempt `source_changed_during_run` and prevents publish, even if
the frozen input copy and checks otherwise succeed.

`reference_only` never claims a past byte snapshot.  If its old bytes later
cannot be recovered, downstream evidence becomes `source_unavailable`; a
materialized snapshot remains rerunnable despite a change at its original
path.  UI must surface the level and rerun availability before execution, not
as an after-the-fact provenance footnote.

## Walking-Skeleton Evidence

The temporary `MaterializationRecord` validates the required fields and
user-visible rerun semantics for all four levels.  The actual synthetic run
uses `materialized_snapshot`: it snapshots `datasets/synthetic_ocean.nc` into
the run directory and writes both source and snapshot identities into the
manifest.  A timed fixture mutates the source during execution and proves that
the result is non-publishable.

The temporary class is intentionally not a production ArtifactStore schema.
Phase 1 moves these exact semantics into versioned Pydantic protocol models;
Phase 2 persists them in SQLite plus immutable content storage.

## Consequences

- A user can choose not to copy a large source, but the choice must explicitly
  lower the rerun guarantee.
- Remote provider selection, licenses, and identity details remain gated by
  OD-04.  No current walking-skeleton record performs a remote fetch.
- A successful checksum comparison cannot erase a source-change event or turn
  a `reference_only` record into a snapshot.

## Verification

`tests/test_spikes/test_ocean_walking_skeleton.py` validates all four record
shapes, their rerun labels, the real stored-snapshot manifest, and the
source-change publish gate.
