# ADR 0011: NOAA OISST Historical Subset Materialization

- Status: Superseded by ADR 0019; historical task-specific design
- Date: 2026-07-12
- Decision IDs: OD-04

## Context

Phase 5 needs one real remote-data path to prove that the workbench can move
from a cited public scientific product to a reproducible local analysis input.
It must not turn an Ocean agent into a general web client, allow a model to
choose an arbitrary URL, or represent a mutable remote URL as if it were a
frozen scientific input.

The first validation task is an annual 2010 SST field for a clearly named
China marginal-seas bounding box, not a legal or oceanographic polygon:
`105E..125E`, `15N..42N`.  It uses the historical daily high-resolution OISST
asset exposed by NOAA Physical Sciences Laboratory (PSL) through NCSS.  NOAA
NCEI remains the canonical OISST Version 2.1 record and DOI authority.

## Decision

1. The only initial remote provider is
   `noaa_psl_oisst_v2_highres`, with HTTPS origin `psl.noaa.gov` and exact
   historical NCSS asset pattern
   `Datasets/noaa.oisst.v2.highres/sst.day.mean.<year>.nc`.  The canonical
   scientific record is `noaa_ncei_oisst_v21`, DOI `10.25921/RE9P-PT57`.
   The PSL asset is transport, not a claim that its mutable URL is an
   immutable source version.
2. Version 1 permits only variable `sst`, calendar years 1982 through 2020,
   and non-wrapping `0..360` longitude selections with `west < east` and
   `south < north`.  It has no credential flow, does not follow redirects,
   and cannot accept a caller-provided base URL, variable, asset, or query
   parameter.
3. The TUI must send `dataset.remote.oisst.preview` first.  It returns the
   fixed provider/dataset identity, DOI and licence snapshot, exact query,
   spatial and temporal selection, conservative uncompressed-grid estimate,
   64 MiB response limit, and private artifact-store target.  Preview makes
   no network request.
4. A fetch requires `dataset.remote.oisst.fetch` with the exact preview
   SHA-256 and `download_acknowledged: true`.  A mismatch fails before any
   egress.  The backend uses an HTTPS-only no-redirect `httpx` stream, checks
   the response limit while writing private staging, and rejects non-NetCDF
   bytes before artifact commit.
5. A successful response is copied into a private immutable DatasetArtifact
   with materialization level `cached_subset`.  The artifact records the
   selection, query digest, provider/DOI, licence and citation snapshots,
   response version hints (`ETag`/`Last-Modified` when supplied), byte size,
   and SHA-256.  The staged source is removed after atomic artifact commit.
   AnalysisRun consumes only the local immutable artifact file.
   On backend startup, any remaining file in the provider-specific private
   staging directory is removed before request recovery. Such a file has not
   passed artifact commit and must not be retained as plausible retry evidence.
6. The model-visible Ocean registry has no remote downloader, web fetcher,
   generic shell, or catalog-search capability.  Remote acquisition is a
   user-authored TUI action; normal model disclosure policy still controls
   what metadata or samples of the resulting local data can reach a provider.
7. The captured use constraint is: cite the dataset; NOAA supplies the data
   without warranty.  Every artifact retains this snapshot and points to the
   NCEI OISST product record rather than assuming a broad licence from URL
   accessibility.

## Consequences

- The first real remote slice is deliberately narrow.  Additional products,
  current years, antimeridian selections, authentication, catalog search, and
  generic fetch all require a new provider contract and ADR update.
- A failed, oversized, redirected, invalid, or unavailable response creates
  no DatasetArtifact.  Retrying uses a new request ID; replaying a completed
  fetch returns the durable terminal result rather than downloading again.
- The 2010 China marginal-seas field can be analyzed and plotted after
  materialization, while its map label must remain a bounding-box description
  unless a separately reviewed regional mask is introduced.
- A real `2010 / 105E..125E / 15N..42N` materialization has been preserved
  under project-private state and independently smoke-checked with xarray and
  matplotlib: it contains all 365 daily samples, `sst` in `degC`, 0.25-degree
  grid-centre coordinates within the requested selection tolerance, and an
  annual-mean NetCDF/PNG. This is operator validation of the provider and
  materialization path, not a scientific conclusion.
- `scripts/run_oisst_2010_agent_vertical_slice.py` now provides a separate
  operator-controlled vertical slice over that immutable local DatasetArtifact.
  The model must use normal Protocol v2 tools, load the map-review skill and
  packaged Matplotlib reference, write `analysis.py` / `verify.py`, execute a
  sandboxed AnalysisRun, and publish FigureSpec, AnalysisPlan, Selection,
  SpatialLayer, and MapScene evidence. The v2 local recheck verifies the field
  against an independently recomputed annual mean, source coordinates and
  EPSG:4326 outer edges, binary RGBA alpha, code/run provenance, and scene
  linkage. The archived 2010 run passed all 13 technical checks with zero
  annual-field absolute error. This is one automated engineering validation,
  not a scientific conclusion or non-implementer walkthrough.

## Sources

- [NOAA NCEI OISST Version 2.1 product record](https://www.ncei.noaa.gov/products/optimum-interpolation-sst)
- [NOAA PSL high-resolution OISST access page](https://psl.noaa.gov/data/gridded/data.noaa.oisst.v2.highres.html)

## Verification

- `tests/test_oceanx/test_remote_oisst_dataset.py`
- Protocol v2 valid/invalid fixtures for preview and explicit acknowledgement
- backend-start recovery test for uncommitted remote staging
- `scripts/run_oisst_2010_smoke.py` for a live `2010 / 105E..125E /
  15N..42N` materialization and independent xarray/matplotlib annual-mean
  SST smoke
- `scripts/run_oisst_2010_agent_vertical_slice.py --acknowledge-model` for the
  isolated agent-authored SpatialLayer slice; use `--review-only` against an
  existing private state root to re-run independent checks without a provider
  call
