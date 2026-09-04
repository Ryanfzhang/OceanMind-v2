# ADR 0009: Artifact Dependency Impact Projections

- Status: Accepted
- Date: 2026-07-11
- Decision IDs: OD-10

## Context

Versioned evidence must remain reproducible as historical evidence while the
workspace tells a researcher when an active conclusion depends on something
that has changed, failed review, been tombstoned, or can no longer be rerun.
A single mutable `status` field would conflate artifact lifecycle, machine
verification, human review, and dependency impact.

## Decision

1. Every immutable artifact version has an independent projection with
   `lifecycle_state`, `review_state`, `verification_state`, and `impact_state`.
   Impact states are `current`, `stale`, `invalidated`, and
   `source_unavailable`; precedence is `invalidated` > `source_unavailable` >
   `stale` > `current`.
2. The propagating intrinsic relations are `derived_from`, `uses_dataset`,
   `generated_by_run`, `implements_spec`, `implements_plan`,
   `supports_claim`, `contradicts_claim`, `tests_hypothesis`, and `motivates`.
   Contextual relations such as `reviews`, `displayed_in_scene`,
   `opens_plot`, and `included_in_report` do not alter the source artifact,
   but report and scene projections surface the referenced artifact's current
   impact state.
3. A newer version of an upstream artifact makes active dependents `stale`.
   A rejected/tombstoned upstream version or an explicit invalidating decision
   makes propagating dependents `invalidated`.  A changed, non-materialized
   `reference_only` input whose historical bytes are unavailable makes
   dependents `source_unavailable`; materialized snapshots do not inherit a
   source-path change.
4. Each impact update stores a structured reason chain containing the source
   artifact ref, source event/ref, relation traversed, and predecessor reason.
   Rebuilds use only authoritative artifact versions, links, lifecycle/review
   records, and state events.  They do not inspect assistant text or infer
   state from filesystem names.
5. Propagation is breadth-first with a visited set.  New propagating links
   that would form an intrinsic dependency cycle are rejected.  Contextual
   cycles are allowed but cannot affect propagation.
6. A researcher may explicitly retain stale evidence in a report through a
   `DecisionArtifact`, but the preview/export must keep the reason chain.
   Invalidated evidence cannot be presented as a current conclusion; a
   source-unavailable result may be cited only with its rerun limitation.

## Consequences

- Rejection of an execution attempt never rewrites a previously created
  ArtifactVersion.  It changes separate projections and propagates structured
  impact to dependent artifacts.
- The projection can be discarded and deterministically rebuilt.  This is a
  projection system with an event audit trail, not a claim that all state is
  event-sourced.
- Report Builder and Plot Studio read projections rather than attempting to
  derive scientific validity from filenames, green checks, or assistant prose.

## Verification

- `tests/test_ocean_partner/test_impact_projection.py`
- `tests/test_ocean_partner/test_artifact_store.py`
- `tests/test_ocean_partner/test_portable_export.py`
