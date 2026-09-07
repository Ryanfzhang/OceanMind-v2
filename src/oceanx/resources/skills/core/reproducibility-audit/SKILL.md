---
name: reproducibility-audit
description: Audit whether an Ocean analysis can be inspected and rerun from its actual inputs, code, environment, provenance, and declared exploratory boundaries.
metadata:
  origin: adapted
  sources:
    - https://github.com/wentorai/research-plugins/tree/main/skills/research/funding/open-science-guide
  roles:
    - data_reproducibility_expert
    - statistical_inference_expert
    - literature_reproduction_expert
---

# Reproducibility Audit

Use this skill when a WorkOrder asks whether evidence can be rerun, independently inspected, shared,
or compared with a paper. Do not perform a full audit merely because an ordinary analysis produced a
file.

## Trace the actual evidence chain

Check the material links from source to conclusion:

- immutable input identity, version or checksum and access conditions;
- selections, parameters, transformations, code and execution environment;
- persisted outputs and the claims they support;
- failed, changed, exploratory, or omitted analyses that affect interpretation;
- dependencies that cannot be redistributed or may disappear.

A checksum establishes byte identity, not scientific correctness or rerunability. A notebook without
the accepted source paths and environment is not a complete reproduction package. Audit the files in
place; do not copy the same data or result again merely to create an audit bundle.

## Distinguish reproducibility levels

State separately what is:

- inspectable from preserved evidence;
- computationally rerunnable in the available environment;
- independently reproducible from described methods and accessible sources;
- shareable under the source license, privacy, embargo, or provider terms.

Preserve the boundary between preregistered or otherwise confirmatory choices and exploratory changes.
If no plan existed before analysis, say so rather than manufacturing one retrospectively.

## Apply open-science principles proportionately

When sharing is actually requested, assess whether the material is findable, accessible under stated
conditions, interoperable through documented formats and variable semantics, and reusable through
provenance, license, and known-limit descriptions. Repository deposit, DOI creation, publication, or
license changes require an explicit user request and are not implied by this skill.

Return the smallest evidence-backed audit that answers the WorkOrder: what can be rerun, what can only
be inspected historically, what cannot be shared, and what material evidence is missing. Stop rather
than claiming reproducibility when inputs are unpinned, the environment is unknown, execution checks
failed, or source access cannot be verified.

Consult `references/review/reproducibility.md` for the OceanMind execution record and
`references/coding/large-array-practices.md` when chunked or remote arrays materially affect reruns.
