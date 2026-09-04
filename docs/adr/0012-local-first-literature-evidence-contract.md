# ADR 0012: Local-First Literature Evidence Contract

- Status: Accepted
- Date: 2026-07-12
- Decision IDs: OD-05

## Context

Literature Mode must let a researcher connect papers, claims, observations,
and hypotheses without treating PDFs, web pages, citations, or model output as
trusted instructions.  A general web-search or PDF-reader tool would let a
model choose arbitrary remote content, blur copyright and disclosure
boundaries, and create an easy path for prompt injection through paper text.

The first version needs a useful local research loop before it needs broad
literature discovery: a researcher can register a citation, attach a PDF they
already possess, record a source locator, ask the agent to synthesize bounded
claims and hypotheses, and decide which hypothesis is active.

## Decision

1. Literature v1 is local-first.  `paper.import` accepts one workspace-local
   PDF after an explicit materialization acknowledgement.  `paper.register`
   records citation metadata only when no local PDF is available.  Generic
   `artifact.create` cannot create a PaperArtifact.
2. A local PDF is copied into a private immutable PaperArtifact version with
   `source_kind=local_pdf` and `materialization_level=materialized_snapshot`.
   A metadata-only paper has `source_kind=metadata_only` and no file.  Both
   retain structured citation metadata only in their artifact content.
3. The backend never extracts, indexes, summarizes, or sends PDF text to a
   model provider.  There is no paper-text, arbitrary file-read, web-search,
   web-fetch, remote PDF download, browser, or catalog-search tool in the
   model-visible Ocean registry.  `document_text_exposed_to_model` is always
   false for v1 paper records.
4. Claims cite exact immutable PaperArtifact refs and bounded source locators
   such as a page, section, figure, or table.  They deliberately store no
   copied PDF excerpt.  `claim_kind` distinguishes author-reported statements
   from synthesis and inference.  An observation derived from data, a figure,
   or literature must pin source refs; only a user-authored `user_report` may
   be source-free, and the agent cannot create that kind.
5. The agent may propose Claim, Observation, Hypothesis, FigureRequest,
   FigureSpec, and AnalysisPlan artifacts.  It cannot create paper records,
   human review state, a user report, or an active hypothesis.  A hypothesis
   must include predictions, falsification criteria, and competing
   explanations.  The TUI's explicit `hypothesis.activate` request is the
   only way to set `active_hypothesis`; its pointer mutation, workspace event,
   and request terminal commit atomically.
6. `literature-evidence-synthesis` becomes capability-enabled only under
   `literature.read`.  This capability exposes research-process guidance, not
   remote discovery or document bytes.  The existing workspace disclosure
   policy continues to deny document text by default.
7. Portable exports copy neither local PaperArtifact PDF bytes nor a manifest
   that claims to include them.  An export that selects or depends on such a
   PDF fails closed.  Citation metadata can later be rendered into a report,
   but redistributing full text requires a separate rights-aware export
   contract.

## Consequences

- Literature v1 is intentionally not a comprehensive search product.  Remote
  search, DOI resolution, publisher APIs, OCR, PDF parsing, embeddings,
  citation-manager synchronization, and full-text question answering each
  require a new provider, copyright, prompt-injection, and disclosure ADR.
- The user can do citation-based framing and paper-backed synthesis without an
  AnalysisRun or Python code.  The agent must label gaps and cross-paper
  conclusions as synthesis or inference rather than attributing them to an
  author.
- A PDF can be retained as local evidence while remaining unavailable to the
  model.  This makes citations and page/figure locators the deliberate bridge
  between human reading and model-assisted research state.

## Verification

- `tests/test_ocean_partner/test_literature_artifacts.py`
- `tests/test_ocean_partner/test_portable_export.py`
- `tests/test_ocean_partner/test_protocol_v2.py`
- Protocol v2 valid/invalid paper fixtures and frontend protocol-fixture check
