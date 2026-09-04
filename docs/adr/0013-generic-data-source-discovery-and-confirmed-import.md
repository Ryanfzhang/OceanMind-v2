# ADR 0013: Generic Data-Source Discovery and Confirmed Import

- Status: Accepted
- Date: 2026-07-17

## Context

The first real remote-data implementation used NOAA OISST as a narrow
engineering validation. That does not make OISST an Ocean Research Partner
workflow or a suitable agent-facing product abstraction. A researcher asks for
data in terms of variables, spatial and temporal coverage, vertical domain and
scientific intent; the system must select from compatible configured sources
without turning the agent into an arbitrary web client.

## Decision

1. The model-visible remote-data contract is source-neutral:
   `dataset_source_discover`, `dataset_import_preview`, and
   `dataset_import_fetch`.
2. Discovery accepts a structured data requirement: variables, geographic
   bounding box, time range, vertical domain and optional region label. It
   returns only compatible adapters registered by the backend, their coverage,
   constraints and a source identifier.
3. Preview and fetch accept a `source_id` returned by discovery. Preview is
   network-free and binds the selected source, subset, byte limit and
   provenance facts into a proposal hash. Fetch requires the exact hash,
   current workspace revision and explicit researcher acknowledgement.
4. A provider adapter owns source-specific validation, request construction,
   response checks, staging cleanup, artifact content and provenance. Adding a
   provider must not add a provider-specific model tool or sidebar action.
5. The initial adapter is NOAA OISST v2.1. It is a factual candidate for
   complete-calendar-year, surface-SST requirements only; its limits are
   reported by discovery. The legacy typed Protocol endpoint remains a
   compatibility surface, not an agent capability.
6. If no configured adapter matches, the agent must say so, offer local Data
   upload or ask the researcher to choose an additional provider, and must not
   invent URLs, shell commands or unbounded download code.

## Consequences

- The product can grow from one validated source to multiple scientific data
  products without changing the agent loop or teaching it a dataset-specific
  command vocabulary.
- A new adapter needs its own provider identity, licence/citation snapshot,
  credential policy, size/network limits, reproducibility contract and tests.
- This does not authorize generic web search, arbitrary URLs, shell access or
  automated imports without confirmation.

## Verification

- `tests/test_ocean_partner/test_remote_oisst_dataset.py` exercises discovery,
  preview, confirmed import and the no-candidate path.
- `tests/test_ocean_partner/test_ocean_runtime.py` proves that the
  model-visible registry contains only generic source tools and no OISST tool.
