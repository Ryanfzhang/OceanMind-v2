# Evaluator protocol

## Per-query references

For every query Q01–Q30, use its per-task files (only paper-reproduction tasks need source images):

```text
tasks/Qxx/task_info.json
tasks/Qxx/target_study/checklist.json
tasks/Qxx/target_study/images/
```

The autoresearch English checklist combines five criteria, weights, required evidence, per-criterion `source_basis`, paper DOI, comparison scope and original-image provenance. No separate CMOMS-wide scoring document is needed. This layout follows [ResearchClawBench's task organization](https://github.com/InternScience/ResearchClawBench/blob/main/CONTRIBUTING.md); our evaluator JSON format is intentionally retained.

## Basic tasks: one figure and one short conclusion

Q01–Q06 use the shared B1 archive (2011, six variables, full depth). Each has an English checklist in its own task directory; no paper target image or elaborate research report is required.

| Query | Single figure | Short conclusion |
| --- | --- | --- |
| [Q01](../tasks/Q01/target_study/checklist.json) | Monthly surface chlorophyll line chart | Highest / lowest months |
| [Q02](../tasks/Q02/target_study/checklist.json) | Summer chlorophyll vertical profile | Sampled maximum and whether it is resolved |
| [Q03](../tasks/Q03/target_study/checklist.json) | Summer / winter MLD distribution comparison | Which seasonal sample has the deeper median |
| [Q04](../tasks/Q04/target_study/checklist.json) | Summer mean daily subsurface salinity-maximum map | Where sampled maxima are high / low |
| [Q05](../tasks/Q05/target_study/checklist.json) | Monthly surface speed line chart | Strongest / weakest months |
| [Q06](../tasks/Q06/target_study/checklist.json) | Annual observed bottom hypoxic-day map | Greatest observed exposure or no observed hypoxia |

Basic weights: **method 20, numerical correctness 50, validity/support 10, figure 10, conclusion 10**. These are benchmark design choices, not paper-derived findings. Retained calculation code allows checking; do not demand additional user-facing maps, diagnostic tables, a notebook per result or a long report. Internal reference outputs listed in a checklist are for verification, not extra figure requirements. A correct null finding can earn full credit.

Independent reference calculations must use the same input arrays and masks. Each checklist lists expected numerical outputs and edge cases; none contains fabricated CMOMS answer values. Freeze tolerances and numerical tie handling before viewing evaluated attempts. Compare full coordinate-aligned fields and masks where relevant, not just one chosen scalar. The existing scalar comparison helper does not implement these array oracles; numerical reference generation and validation remain pending. A text-only LLM cannot verify a figure or replace numerical checks.

For Q03, thermodynamic conventions follow [TEOS-10 sigma0](https://www.teos-10.org/pubs/gsw/html/gsw_sigma0.html) and [potential-temperature conversion](https://www.teos-10.org/pubs/gsw/html/gsw_CT_from_pt.html); the supplied header labels temp as potential temperature. The 10 m reference, density threshold and seasonal sample definitions are explicit benchmark choices. Q06's unit conversion follows the supplied oxygen header. Other basic rules are direct mathematical definitions in the public query, not claimed paper results.

## Idea and hypothesis testing: Q25–Q30

The [shared rubric](IDEA_RUBRIC.md) defines 14 individually anchored 0–4 items, with category weights **10 / 15 / 35 / 30 / 10** and a 100-point total. Every per-query checklist contains the complete anchors, plus task-specific scope and the basis for selecting its 2–3 selected related-work papers. [Queries and reading packs](../preparation/IDEA_TASKS.md) are now specified.

These tasks have no unique gold hypothesis, expected sign or target image. Judge literature fidelity against the actual supplied source texts; judge calculations with independent code/numerical evidence. Contradiction or a well-supported inconclusive result can earn full credit. Assess scientific value and the explained extension, comparison, synthesis or boundary test, not worldwide first discovery. A related idea already studied elsewhere is not automatically penalized. Mere restatement of a supplied conclusion is insufficient; changing years alone is not a scientific rationale. No predetermined example hypothesis is a target answer. Keep draft status until full texts, data and audit procedures are validated; no fixed-answer generation is required for an open hypothesis.

The current helper passes criterion anchors to the judge, but it does not automatically stage full papers, perform scientific code audits/replay, or orchestrate the two-judge protocol. Do not mistake a single JSON score for a verified end-to-end evaluation.

## Scoring

Score each criterion0–4: **0** absent/fundamentally incorrect; **1** major flaws; **2** useful partial evidence; **3** substantially correct with minor gaps; **4** fully demonstrated. Total = sum(weight × score /4), out of100. Weights and anchors are benchmark design choices, not authored by the cited papers.

A plan or quoted conclusion is not an executed result. Reward evidence, not verbosity, matching colors or obtaining the expected sign. Input/geometry checks and reproducible delivery are engineering safeguards, not claims that the paper performed additional experiments. Equivalent justified implementations are acceptable unless the task requires a particular definition. Do not impose extensions absent from the selected paper analysis.

For autoresearch Q07–Q24, C5 covers source-attributed report, figures, machine-readable diagnostics and one editable `analysis.ipynb` with dependencies and relative/configurable paths. Replay must be independently recorded; a file or stored notebook output is not proof of execution. Score scientific correctness under C1–C4, not twice under C5.

## Original images and numerical answers

Compare only specified panels/diagnostics. Judge scientific structure, axes, units, coverage and quantitative support, not pixel identity. Every stored crop includes source DOI, PDF page, source-panel label, PDF SHA-256 and image SHA-256. `source_regions` records the main crop plus any original shared axis/legend strips, their placement and uniform scale; no curves or numerical labels are redrawn. `grouping_reason` explains retained small groups. Original rights remain with rights holders; review reuse permissions before publishing crops or sending them externally. Images were extracted from original PDFs using the PDF skill workflow and visually checked, not recreated.

`visual_checks` specifies required scientific information, acceptable alternative representations and the exact source panel for each criterion. `visual_scoring_policy` is passed to the judge, together with panel context and the comparison mode. Map evidence many-to-many: one submitted plot can satisfy several checks, and several plots can satisfy one check. Plot style, number of panels and visual similarity carry no points. Use the same five weights; context-only panels and alternative source-product examples are not extra deliverables. A matching sentence or curve shape without correct calculations is insufficient.

A published figure is **not** an independently computed answer on the final input arrays. Freeze input hashes, masks, preprocessing and tolerances, compute trusted reference outputs independently, and obtain scientific review before changing `status` from `draft` to `validated`. Keep `data_fingerprint` null until actual inputs are frozen. Never produce reference values from the same agent attempt being graded.

Q09–Q11 are approved method transfers to2018–2022: correct disagreement with the climatological paper can receive full marks. For matched-paper tasks, verify release/sample/run equivalence before using printed values as numerical targets. Resolve missing central inputs before comparing systems rather than dropping criteria or changing weights for one system.


**Accessible-data revision (2026-09-08):** Q13–Q22 now use reduced public-data analyses. Their retained source panels are **context only**, not required outputs or numerical ground truth. The per-query adapted criteria specify the required evidence; do not penalize absence of old EEMD/NPP/glider/float diagnostics. Q23/Q24 retain their paper-derived methods. Q28–Q30 use public inputs with the same 14-item rigor rubric; model-internal validation must not be called independent observational validation.

## Reference-panel index

These **70 retained original-paper units across 18 tasks** include historical context for adapted tasks and are evaluator evidence, not a requirement to generate 70 output images. A unit can be one panel or a small group whose axes, legend or scientific comparison must remain together. Each link leads to that query's English checklist and exact panel paths.

| Query / checklist | Reference units | Scientific scope |
|---|---:|---|
| [Q07](../tasks/Q07/target_study/checklist.json) | 3 | Time-depth circulation; three-layer hotspot group; IR/AR comparison |
| [Q08](../tasks/Q08/target_study/checklist.json) | 3 | Separate upper, middle and deep circulation diagnostics |
| [Q09](../tasks/Q09/target_study/checklist.json) | 2 | Model seasonal-map pair; regional-cycle group with shared axes |
| [Q10](../tasks/Q10/target_study/checklist.json) | 4 | Four gateway DIN seasonal cycles; TN is context only |
| [Q11](../tasks/Q11/target_study/checklist.json) | 12 | Ten summer/winter depth maps with colorbars; absolute and contrast profiles |
| [Q12](../tasks/Q12/target_study/checklist.json) | 1 | Fig. 2C hypoxic volume only, with inset and scenario legend |
| [Q13](../tasks/Q13/target_study/checklist.json) | 4 | Context only; current required outputs are defined in the adapted checklist |
| [Q14](../tasks/Q14/target_study/checklist.json) | 1 | Context only; current required outputs are defined in the adapted checklist |
| [Q15](../tasks/Q15/target_study/checklist.json) | 2 | Context only; current required outputs are defined in the adapted checklist |
| [Q16](../tasks/Q16/target_study/checklist.json) | 2 | Context only; current required outputs are defined in the adapted checklist |
| [Q17](../tasks/Q17/target_study/checklist.json) | 5 | Context only; current required outputs are defined in the adapted checklist |
| [Q18](../tasks/Q18/target_study/checklist.json) | 4 | Context only; current required outputs are defined in the adapted checklist |
| [Q19](../tasks/Q19/target_study/checklist.json) | 2 | Context only; current required outputs are defined in the adapted checklist |
| [Q20](../tasks/Q20/target_study/checklist.json) | 8 | Context only; current required outputs are defined in the adapted checklist |
| [Q21](../tasks/Q21/target_study/checklist.json) | 3 | Context only; current required outputs are defined in the adapted checklist |
| [Q22](../tasks/Q22/target_study/checklist.json) | 7 | Context only; current required outputs are defined in the adapted checklist |
| [Q23](../tasks/Q23/target_study/checklist.json) | 3 | SST/threshold; seasonal timing distribution; annual event metrics |
| [Q24](../tasks/Q24/target_study/checklist.json) | 4 | Heat-budget series; surface-flux series; early and late contribution panels |

## Running and isolation

Stage the query and approved data into a fresh agent workspace. **Never expose the repository, `target_study/`, checklists or evaluator outputs to the agent.** The directory convention is not an access-control mechanism. Keep independent literature search permitted under the same policy for both systems. Use separate attempt workspaces and disable cross-task experience transfer in the primary comparison.

Use [server instructions](../server/README.md) for result packaging. Once a reference is genuinely validated, the existing judge accepts its per-task path:

```bash
python benchmarking/evaluation/bench_eval.py judge \\
  --bundle /srv/ocean-evaluation/bundles/oceanx-Q13-r1.zip \\
  --reference benchmarking/tasks/Q13/target_study/checklist.json \\
  --out /srv/ocean-evaluation/scores/oceanx-Q13-r1.json
```

Draft references are deliberately rejected; this command is not runnable until validation. Remote submission additionally requires explicit `--send` and configuration from the server guide. Visual judging requires `--images` and a vision-capable judge; unread images are unassessed, not checked. The limit is 16 candidate-plus-reference images and 64 MiB of raw image bytes per request; limits fail explicitly rather than silently discarding panels. The largest reference (Q11) uses 12 images, leaving room for four submitted figures. Select comprehensive submitted figures explicitly; their panels need not follow the reference layout. Do not omit required scientific evidence just to fit a budget.

The current export helper supports the selected 15-task non-CMOMS subset (Q13–Q24, Q28–Q30), not unrestricted CMOMS export. Basic Q01–Q06 require CMOMS authorization and are excluded from that public-data run. Its allowlist has been updated to the new IDs. It does not run either agent automatically, execute notebooks or generate scientific reference answers.

Report completion/failure rates alongside quality scores, retain all attempts, record first attempts separately from retries, and compare paired results. Tasks sharing a paper/dataset are correlated; also report paper-family-level summaries and do not interpret18 tasks as18 independent discoveries. Human review should resolve judge disagreements and ambiguous source methods.

## Offline tests

```bash
python -m pytest benchmarking/tests
```

## Export and judging CLI reference

These commands do not run the agents. The current packer accepts `oceanx` and `claude-science`; it does not yet offer a Claude Code export label. The separate `server/run_claude.py` records Claude Code runs but does not integrate this packer. Do not label Claude Code as Claude Science.

## 5. Common result bundle (implemented)

Both systems export the SAME schema. OceanX uses its existing `query.json` and
`result.json` to check task/query identity and status. Science currently requires
an explicit export and status; the packer is not a Science execution driver.

```bash
python benchmarking/evaluation/bench_eval.py pack \
  --agent oceanx --task Q21 --run-id r1 --model-label YOUR_MODEL_ID \
  --source /srv/ocean-runs/oceanx-r1/Q21/attempt-ACTUAL_ID \
  --data-manifest /srv/ocean-evaluation/Q21-inputs.json \
  --report answer.md \
  --include RELATIVE_PATH_TO_ANALYSIS.ipynb \
  --include RELATIVE_PATH_TO_SMALL_DERIVED_TABLE.csv \
  --include RELATIVE_PATH_TO_FIGURE.png \
  --out /srv/ocean-evaluation/bundles/oceanx-Q21-r1.zip
```

For Science, point `--source` at its real export folder, use `--agent claude-science`
and `--status completed` (or the actual terminal state), and select its actual
report/files. The files are not regenerated by a third LLM. For failures without
an answer, omit `--report`; preserve the failure bundle for the denominator.

Bundle: `manifest.json` (task/query/data fingerprint, agent/model/run/status and
available usage), `answer.md`, and explicitly selected evidence. Every exported
file has a hash. No source datasets, hidden reference answers, state databases,
credentials or raw logs are collected automatically. No recursive copy, symlink
following or file overwrite. Only selected small derived artifacts are copied
into the ZIP; original data remain in place. Each file is limited to 20 MiB and
the selected payload to 64 MiB. Bundles are review evidence, not standalone runtime
environments; preserve the original run for notebook replay with original data.

This is a LOCAL export, not a guarantee of secret anonymization. Inspect reports,
notebook source and tables before using `--send`; they can contain sensitive text.
Model/agent identity is excluded from structured judge context, but content can
still reveal the author. Use equivalent artifact selection rules on both sides.

## 6. Numerical and LLM judging (implemented primitives; references still needed)

Keep evaluators/reference answers outside both agent workspaces. Use the query's
`benchmarking/tasks/Qxx/target_study/checklist.json`; these are intentionally **draft** and
the judge refuses them until independently validated. Freeze inputs, numerical
references, query/data hashes and tolerances first. Do not flip status merely to
make the command run. `reference.example.json` demonstrates the format only.
Q01–Q06 now all require CMOMS B1 and are excluded from this public-data workflow.

### Numerical comparison

Use a trusted task-specific extractor to read actual derived result tables; do not
use the evaluated agent's claim of correctness as the reference. Independently
compute reference scalars from the SAME data. Example file shapes:

```json
{"task_id":"Q21", "query_sha256":"...", "data_fingerprint":"...",
 "metrics":{"january_anomaly_2011_percent":12.3}}
```

```json
{"status":"validated", "task_id":"Q21", "query_sha256":"...", "data_fingerprint":"...",
 "metrics":{"january_anomaly_2011_percent":{"value":12.3,"atol":0.01,"rtol":0.001}}}
```

Numbers above demonstrate syntax ONLY; they are not Q21 answers or approved tolerances.

```bash
python benchmarking/evaluation/bench_eval.py compare \
  --actual /srv/ocean-evaluation/Q21-actual.json \
  --reference /srv/ocean-evaluation/Q21-numeric-reference.json \
  --out /srv/ocean-evaluation/Q21-numeric-checks.json
```

Comparison checks each required scalar using `abs(error) <= atol + rtol*abs(reference)`;
missing/non-finite actual values fail. It does not validate the extractor, units,
spatial maps or methodology. Reference computation and extraction are still
task-specific work, not implemented golden analyses. Do not treat visually similar
plots, correlation alone, or pixel similarity as scientific equivalence.

### LLM evidence review

Dry run validates input and performs NO API call:

```bash
python benchmarking/evaluation/bench_eval.py judge \
  --bundle /srv/ocean-evaluation/bundles/oceanx-Q21-r1.zip \
  --reference /srv/ocean-evaluation/Q21-reference.json \
  --model YOUR_JUDGE_MODEL_ID \
  --out /srv/ocean-evaluation/scores/oceanx-Q21-r1-judgeA.json
```

After reviewing exactly what will be sent, set `BENCH_JUDGE_API_KEY` securely in
the terminal environment and add `--send`. The default base is
`https://api.deepseek.com/v1`; use `--base-url` for another OpenAI-compatible judge.
Use an independently configured judge, ideally a different model family than
the contestants, and a second judge with the SAME reference. Missing/malformed
responses cause failure, not a zero score; there are no automatic paid retries.

Each call is capped at 80,000 text characters and 4,096 output tokens, with a
120-second timeout. Oversized text is rejected, not silently truncated. `--images`
opts into up to eight total candidate/reference figures for a confirmed vision-
capable judge. Add reference figures as `{"path":"reference.png","sha256":"..."}`
in reference `figures`. Without `--images`, figure inspection is explicitly marked
unavailable. Notebook rich outputs are not decoded automatically; export selected
figures separately. Notebook code is read, NEVER executed by this evaluator.

Outputs contain every criterion's 0–4 score, evidence IDs and concise justification,
weighted score out of 100, provider-reported usage and hashes of bundle/reference/
prompt/evaluator. This is LLM evidence review, not an execution or numerical
certificate. API failures, unseen figures and incomplete evidence remain distinct.
Do notebook replay in a dedicated restricted execution environment with read-only
data and no judge credentials, not on the judge host using unrestricted nbclient.

## 7. Report the comparison honestly

Report all attempted tasks, status distribution, completion rate, per-track quality,
independent numerical checks, independent notebook replay, time and available usage.
Do not silently exclude failures or average only successful retries. Keep first
attempt outcomes and retry outcomes separate. Missing usage is unknown, not zero.
Use the same frozen task/reference for both systems; show paired task differences,
repeat variation and human-adjudicated judge disagreements. A rejected hypothesis
can be a valid scientific result. This subset contains 15 tasks: 12 autoresearch and 3 idea/testing, with no basic-analysis tasks. Report the actual attempted subset and do not generalize its aggregate to all 30 tasks.

**Still pending:** input validation and independent golden outputs; validation of the
18 autoresearch draft checklists under `tasks/Qxx/target_study/checklist.json`,
plus separate basic/idea rubrics; trusted metric extractors; server/API validation and evaluation integration of the Claude Code recorder; an automatic cross-system scheduler and score aggregator. This
change delivers the common export/judge components, not that completed experiment.

Offline tests:

```bash
python -m pytest benchmarking/tests
```
