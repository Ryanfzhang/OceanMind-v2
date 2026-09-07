# OceanX / Claude Science: non-CMOMS server evaluation

## Actual readiness

This directory is preparation for a two-system comparison, not a claim that both
agents already run unattended. Claude Science is not installed on the user's server.
No Claude Code `-p` command, fabricated Science API, login token or automatic GUI
driver is substituted for Claude Science.

The 13 non-CMOMS tasks are **Q05, Q15–Q23, Q28–Q30** (1 analysis, 9 autoresearch,
3 idea/testing). This is a selected subset, not the original 30-task benchmark.
See `non_cmoms.json` for blocking inputs. None is declared scientifically ready
merely because a download endpoint exists. Current canonical queries remain in
`benchmarking/tasks`; do not rewrite them differently for different agents.

## 1. Conda environment

For Claude Science's user-level Linux launcher/sandbox dependencies, use the
separate **Conda** spec file (not `pip install -r`):

```bash
conda create -n claude-science-tools --override-channels -c conda-forge \
  --file benchmarking/server/claude-science-requirements.txt
conda activate claude-science-tools
```

This does not require sudo. It does not install the Science/CC Switch applications,
replace Science's managed analysis environments, enable kernel user namespaces,
or provide a graphical desktop for CC Switch. This user-level dependency route
must still pass the actual Science startup checks on the target Linux host.

From the repository root on Linux:

```bash
conda create -n oceanx-bench python=3.11
conda activate oceanx-bench
python -m pip install -r benchmarking/server/requirements.txt
python -m pip install -e .
export OCEAN_SANDBOX_PYTHON="$CONDA_PREFIX/bin/python"
ocean doctor
ocean sandbox-self-check
python -m pip freeze > /srv/ocean-evaluation/python-freeze.txt
```

Create `/srv/ocean-evaluation` or substitute a writable evaluator directory first.
The requirements are compatible ranges, not a bitwise environment lock; retain
the resolved freeze, OS, Conda environment export and code commit for each run.
The host also needs `curl`, `bubblewrap`, `libseccomp2` and working unprivileged
namespaces. Have the administrator provide them if necessary. Do not disable the
sandbox, run generated code as root, or assume installation implies a passing self-check.
This Python environment does not install Claude Science or cc-switch. Verify the
actual scientific environment used by Science independently after installation.

## 2. Download available public products

Reuse the tested standalone downloader; no CMOMS authorization or data is needed:

```bash
python benchmarking/download/download_data.py --list
python benchmarking/download/download_data.py \
  --output /srv/ocean-data/public --modis-bbox 104 121 1 25
```

The bbox is an example enclosing rectangle, **not** a validated scientific mask.
The command previews current remote metadata, coordinates and missing months.
Only after inspecting the report, repeat with `--execute`. Add `--allow-missing`
only if you explicitly accept downloading available months while leaving missing
months unresolved; it is not permission to fill gaps or treat an incomplete task
as ready. See [download guide](../download/README.md) for date/product options.

Currently automated: MODIS monthly chlorophyll, SeaWiFS monthly chlorophyll,
NOAA blended monthly winds. Gulf reanalysis/altimetry/profile products, events,
ETOPO5 and scientific masks are still unresolved. Do not silently replace them
with generic CMEMS/Argo/new bathymetry. Q05/Q21 are the first preparation targets;
Q22/Q28 require a sampling-scale decision; Q23 requires gaps/mask/EEMD validation.

Freeze one evaluator-owned input manifest per task after inspecting the data:

```json
{
  "validated": false,
  "task_id": "Q05",
  "selection": "Exact product/release, time, region/mask, units, grid and coverage decisions",
  "files": [{"path": "/srv/ocean-data/actual-file.nc", "sha256": "ACTUAL_FILE_SHA256"}]
}
```

Include all required data, masks, metadata and permitted background papers.
Use the downloader's verified receipt hashes or hash local files. Set `validated`
only after human/data preparation validation; the packer does NOT perform that
scientific validation. Both agents must use the SAME manifest, read-only files
and query. Shared original files are not duplicated into result bundles.

## 3. DeepSeek configuration

### OceanX

Uses its existing role settings, independently of cc-switch:

```bash
export OCEANMIND_CONFIG_DIR=/srv/private/oceanx-bench-config
ocean configure-models < /srv/private/role-config.json
```

Privately create `role-config.json` with this structure, repeating a full profile
for `coordinator`, `expert`, and `skill_curator`:

```json
{"roles": {
  "coordinator": {"provider":"openai", "base_url":"https://api.deepseek.com/v1", "model":"YOUR_MODEL_ID", "api_key":"YOUR_KEY"},
  "expert": {"provider":"openai", "base_url":"https://api.deepseek.com/v1", "model":"YOUR_MODEL_ID", "api_key":"YOUR_KEY"},
  "skill_curator": {"provider":"openai", "base_url":"https://api.deepseek.com/v1", "model":"YOUR_MODEL_ID", "api_key":"YOUR_KEY"}
}}
```

Keep private directories owner-only and secret files mode `0600`, outside source,
data and exports. Replace the model with a real enabled model ID. The primary
batch runner disables Curator; it does not spend review calls during the benchmark.

### Claude Science + cc-switch

Install Science from its [official product page](https://claude.com/product/claude-science)
using its current Linux instructions and legitimate account access. Confirm the
CLI version/help before configuring unattended operation. Configure DeepSeek in
the installed cc-switch provider using its official Anthropic-compatible base:
`https://api.deepseek.com/anthropic`. Keep the real key in cc-switch's private store.

[Community integration reference](https://github.com/ZhangYiqun018/claude-science-cc-switch-guide)
documents main `/v1/messages` routing, model mapping and thinking/tool-choice
adaptation. It is not an official Science provider contract; some scripts use
macOS `launchctl` and cannot be copied directly to Linux. `managed_endpoints`
alone does not establish replacement of the main research agent. Do not fabricate
login credentials. No Science login/profile changes are made by these scripts.

Before benchmarking, verify one real task that invokes Python, writes a small
table and a figure, then exports them. Check that main/sub-agent/reviewer calls
use the intended model, tools work, and no unnoticed fallback to Claude occurs.
Do not log keys. Record Science version, cc-switch version, resolved model and
the compatibility rewrites; they are part of the treatment being evaluated.

**Pending Science adapter contract:** create isolated project/session; register
approved inputs; submit the exact query; observe explicit completion/error or
interaction; export the final answer and selected derived artifacts. Only implement
this after verifying the installed version's real interface. If only GUI operation
exists, use a verified version-pinned UI adapter or label runs manual-assisted.
`claude -p` is Claude Code and is NOT a substitute.

## 4. Run OceanX now, Science after interface validation

Prepare a runner JSONL from canonical queries and resolved file paths. Only use
the allowed QueryCase fields documented in [headless runner](../../docs/evals/headless-benchmark.md).
`non_cmoms.json` and `task_info.json` are not runner inputs.

```bash
ocean batch --queries /srv/ocean-inputs/public-queries.jsonl \
  --output /srv/ocean-runs/oceanx-r1
```

Use a fresh output directory for each repetition. `--resume` retries failures,
not an independent repeat; preserve every attempt. Unexpected interactions become
`needs_interaction`. Fix the human intervention, paper policy and budgets equally
for both agents, rather than auto-approving one system's prompts. For both systems,
reset conversation/project memory and freeze initial Skills between tasks. Science
isolation must be validated, not assumed. No cross-system results/references may
be exposed to either agent. Read-only data permission is NOT protection against
reading evaluator answers if you mount the entire benchmark repository.

## 5. Common result bundle (implemented)

Both systems export the SAME schema. OceanX uses its existing `query.json` and
`result.json` to check task/query identity and status. Science currently requires
an explicit export and status; the packer is not a Science execution driver.

```bash
python benchmarking/evaluation/bench_eval.py pack \
  --agent oceanx --task Q05 --run-id r1 --model-label YOUR_MODEL_ID \
  --source /srv/ocean-runs/oceanx-r1/Q05/attempt-ACTUAL_ID \
  --data-manifest /srv/ocean-evaluation/Q05-inputs.json \
  --report answer.md \
  --include RELATIVE_PATH_TO_ANALYSIS.ipynb \
  --include RELATIVE_PATH_TO_SMALL_DERIVED_TABLE.csv \
  --include RELATIVE_PATH_TO_FIGURE.png \
  --out /srv/ocean-evaluation/bundles/oceanx-Q05-r1.zip
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

Keep evaluators/reference answers outside both agent workspaces. Start with
`../evaluation/reference.example.json`: it is intentionally **draft** and the
judge refuses it. Fill actual reference results; freeze task/query/data hashes
and per-task criteria/weights, then independently validate it. Do not flip its
status merely to make the command run. Its weights are a proposal, not a validated
benchmark rubric. Autoresearch/idea tasks require their own rubrics, not Q05's.

### Numerical comparison

Use a trusted task-specific extractor to read actual derived result tables; do not
use the evaluated agent's claim of correctness as the reference. Independently
compute reference scalars from the SAME data. Example file shapes:

```json
{"task_id":"Q05", "query_sha256":"...", "data_fingerprint":"...",
 "metrics":{"january_anomaly_2011_percent":12.3}}
```

```json
{"status":"validated", "task_id":"Q05", "query_sha256":"...", "data_fingerprint":"...",
 "metrics":{"january_anomaly_2011_percent":{"value":12.3,"atol":0.01,"rtol":0.001}}}
```

Numbers above demonstrate syntax ONLY; they are not Q05 answers or approved tolerances.

```bash
python benchmarking/evaluation/bench_eval.py compare \
  --actual /srv/ocean-evaluation/Q05-actual.json \
  --reference /srv/ocean-evaluation/Q05-numeric-reference.json \
  --out /srv/ocean-evaluation/Q05-numeric-checks.json
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
  --bundle /srv/ocean-evaluation/bundles/oceanx-Q05-r1.zip \
  --reference /srv/ocean-evaluation/Q05-reference.json \
  --model YOUR_JUDGE_MODEL_ID \
  --out /srv/ocean-evaluation/scores/oceanx-Q05-r1-judgeA.json
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
can be a valid scientific result. This subset is only 13 selected tasks, with 1
simple-analysis task; do not generalize its aggregate to the full benchmark.

**Still pending:** full data selection/validation and golden outputs; 13 finalized
rubrics; trusted metric extractors; Science installation/cc-switch smoke test and
verified driver; an automatic cross-system scheduler and score aggregator. This
change delivers the common export/judge components, not that completed experiment.

Offline tests:

```bash
python -m pytest benchmarking/evaluation/test_bench_eval.py benchmarking/download/test_download_data.py
```
