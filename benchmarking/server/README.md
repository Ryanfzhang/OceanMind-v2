# Running OceanX on a shared-data server

## Benchmark file delivery (opt-in)

Both `--query` and `--queries` run every OceanX model role with **deepseek-v4-pro**
using the configured Coordinator API endpoint and credential. This override is
process-local: saved settings and frontend role models are untouched. Configure
a Coordinator endpoint that serves this model before running; no fallback to Flash
is intended. Each attempt records `model_protocol.json`. Curator remains disabled.
For Claude comparisons explicitly pass `--model deepseek-v4-pro`; check actual
reported model usage for auxiliary calls as well as the main model.

For science comparisons that should not depend on Desktop interactive-view publication,
use the separate runner:

```bash
python benchmarking/server/run_oceanx.py --queries /absolute/queries.jsonl \
  --output /absolute/new-benchmark-run
```

For an ad-hoc query test, the same delivery and literature-selection policy applies:

```bash
python benchmarking/server/run_oceanx.py --query "Your research question" \
  --dataset /absolute/temperature.zarr --dataset /absolute/salinity.zarr \
  --timeout 7200 --output /absolute/new-query-test
```

This replaces only `ocean_publish_outputs` inside the dedicated benchmark backend
process. Coordinator-accepted candidates (including ScientificFigure `.nc` files)
and their declared supporting files are copied, hash-checked, and recorded under
`attempt-*/delivery_manifest.json`. Assigned input references
are preserved as provenance, including multiple sources. Input datasets are not
copied. NetCDF views are rendered to PNG with the existing backend figure-reproduction
templates; the receipt includes PNG paths and the reusable `render_figures.py`.
This does not repeat the analysis or claim scientific correctness. Set
`OCEAN_BENCH_RENDER_PYTHON` to the Python environment with matplotlib, xarray and
NetCDF dependencies if needed (defaults to `OCEAN_SANDBOX_PYTHON`, then runner Python).
The Coordinator receives a file-delivery receipt and can return its final answer
without registering a Desktop result. `answer.md` and the runtime `result.json`
retain their usual meanings. `outputs.json` still describes Desktop results and
can therefore be empty; evaluate the separate benchmark manifests instead.

All single-query and JSONL tests use the same per-attempt layout:

```text
attempt-*/
  answer.md
  figures/<expert>/           # current PNGs; revisions update the same names
  analysis.ipynb             # editable visualization of saved derived data
  artifacts/<expert>/        # OceanX derived data and declared supporting files
  render_figures.py
  delivery_manifest.json     # current accepted files, not historical receipts
  events.jsonl, backend.log  # raw execution evidence retained
```

Continuation references are deduplicated and the latest saved candidate for an
Expert/file is used when accepted. Different Experts have stable namespaces;
genuinely conflicting unqualified names return explicit owner-qualified choices.
Final collection reads only the current manifest, never every historical work order.
Older receipt-based runs can still be collected without rewriting their results.
Claude uses the same `answer.md`, `figures/`, `analysis.ipynb` layout and retains its
`code/` and `outputs/` relative paths. A missing notebook is not fabricated.

Use this same adapter for both OceanX arms. Record the delivery mode in comparisons;
`delivery_protocol.json` records it and the adapter hash for each attempt. This is
an intentional delivery intervention and is not a test of native UI publication.
Literature selection interactions automatically select all offered papers, as
authorized for unattended benchmarks; this policy is recorded in the attempt.
The normal `ocean batch` command, production code, scientific prompts, tree policy,
and execution sandbox are unchanged; only the benchmark model override above applies. This adapter does not
auto-approve unrelated permissions, fabricate a missing final answer, or turn an
interrupted run into a completed one. Existing attempts are not rewritten.

### Collecting all results

The test runner automatically collects finished attempt records on exit, including
failed/interrupted attempts that have a `result.json`. It prints the collection
directory. Open `collected/collection-*/index.md` for the full run, or inspect
`summary.json` for runtime status, final-answer presence, PNG counts, usage and
collection errors. Each task/attempt contains the original `answer.md`, a portable
`review.md` with a PNG gallery, and verified delivery files (`.nc`, PNG, renderer,
declared supporting outputs and provenance). All retries remain separate;
no successful attempt is silently substituted for a failed one.

You can collect again without rerunning the models:

```bash
python benchmarking/server/collect_oceanx.py --run /absolute/benchmark-run \
  --output /absolute/new-collected-results
```

The collector does not scrape unaccepted workspace files, copy input datasets,
execute agent programs, or infer a final answer from intermediate reports. Old
native runs without benchmark receipts retain their answer/status but will not
automatically gain PNG outputs. Collections are review inputs, not scientific
scores; use the separate evaluation workflow to judge the selected evidence.

This is the execution guide. [Download setup](../download/README.md), [data/paper correspondence](../preparation/DATA_PREPARATION.md), and [export/judging](../evaluation/README.md) each have one separate home.

## 1. One-time environment and model checks

From the repository root, with a normal account (no sudo required for Python):

```bash
conda create -n oceanx-bench python=3.11
conda activate oceanx-bench
python -m pip install -r benchmarking/server/requirements.txt
python -m pip install -e .
export OCEAN_SANDBOX_PYTHON="$CONDA_PREFIX/bin/python"
ocean doctor
ocean sandbox-self-check
```

The Linux host also needs bubblewrap, libseccomp and working unprivileged namespaces.
If the self-check fails because of administrator policy, downloads alone do not
make the benchmark runnable; ask the administrator to permit the required
execution environment. Do not disable isolation. See the [headless runtime
contract](../../docs/evals/headless-benchmark.md).

Existing OceanX model profiles on this server can be reused. Otherwise run
`ocean configure-models < /absolute/private/role-config.json` with the supported
role profiles (`coordinator`, `expert`, `skill_curator`). Keep keys outside the
shared data/archive/repository. The server configuration is separate from cc-switch.
The batch runner disables Curator for the primary comparison.

## 2. The data root is exactly the downloader's --output

If both download commands used `--output /import/home4/share`, the expected layout is:

```text
/import/home4/share/
  _download_all/coverage.json
  MODIS_Aqua/chlorophyll/...
  CMEMS_Gulf/thetao/...
  OISST/sst/...
  ...
```

Then `--data-root /import/home4/share` is correct. If the downloader instead used
`/import/home4/share/ocean-data`, use that exact child directory. Do not move/copy
data into individual query folders. Give the runner read access to the selected
variable directories, not the entire shared directory or evaluator references.

Before the first official run, recheck the downloaded files:

```bash
python benchmarking/download/download_all.py verify --output /import/home4/share
```

This hashes existing files and can take time on a large archive; it does not
download or call models. A nonzero exit or incomplete coverage means fix/resume
the download first. Do not run download and verification against the same archive
concurrently. The current numerical inventory covers all 15 non-CMOMS tasks.

## 3. Run the 12 autoresearch tasks first

These are Q13–Q24. They do not require a supplied private-data or full-text reading
pack. Literature searches may still occur under the configured identical policy.

```bash
python benchmarking/server/prepare_queries.py \
  --data-root /import/home4/share --preset autoresearch \
  --output "$HOME/oceanx-bench-inputs/autoresearch.jsonl"

python benchmarking/server/run_oceanx.py --queries "$HOME/oceanx-bench-inputs/autoresearch.jsonl" \
  --output "$HOME/oceanx-bench-runs/autoresearch-r1"
```

The preparation command checks current catalogue/download completion reports and
source paths before producing anything; it leaves the English queries unchanged.
It does not copy NetCDF files or invoke a model. This is not a second hash audit
or independent scientific coverage review. Existing output JSONL files are not overwritten.

The default per-task time limit is 3600 seconds; `--timeout 7200` at preparation
changes it explicitly. Use the same limit for both compared agents. The output
run directory must be new and separate from source directories. Runs invoke the
configured model and consume API credits.

For a single inexpensive pilot, use the existing explicit selection:

```bash
python benchmarking/server/prepare_queries.py \
  --data-root /import/home4/share --tasks Q21 \
  --bindings /import/home4/share/_download_all/data_bindings.json \
  --output "$HOME/oceanx-bench-inputs/q21.jsonl"
python benchmarking/server/run_oceanx.py --queries "$HOME/oceanx-bench-inputs/q21.jsonl" \
  --output "$HOME/oceanx-bench-runs/q21-pilot"
```

Explicit `--tasks` checks paths only; use the download verification above. It also
supports manual CMOMS bindings once authorized, without adding CMOMS downloads.

## 4. Add Q28–Q30 only after staging the readings

The full non-CMOMS preset contains Q13–Q24 and Q28–Q30. The latter three queries
require the selected related-work full texts, not just citations or abstracts.

Place authorized PDFs under e.g. `/import/home4/share/Papers/Q28/` and create a
separate custom bindings JSON with a `papers` list for Q28, Q29 and Q30. Their
required reading counts are 2, 3 and 3 respectively; see the canonical tasks.
If copying the generated bindings as a starting point, do not edit the generated
copy in place: a later download rerun regenerates it.

```bash
python benchmarking/server/prepare_queries.py \
  --data-root /import/home4/share --preset non-cmoms \
  --bindings "$HOME/oceanx-paper-bindings.json" \
  --output "$HOME/oceanx-bench-inputs/non-cmoms.jsonl"
python benchmarking/server/run_oceanx.py --queries "$HOME/oceanx-bench-inputs/non-cmoms.jsonl" \
  --output "$HOME/oceanx-bench-runs/non-cmoms-r1"
```

The preset rejects missing/duplicate/non-PDF reading files; it cannot establish
their bibliographic identity, completeness or licensing. Review and freeze the
same full texts for both systems. Do not include evaluator checklists or target images.

## 5. Results, retries and evaluation

The batch writes `results.jsonl` and per-attempt `query.json`, `result.json`,
`answer.md`, event logs and derived artifacts. It runs cases sequentially, while
the existing Coordinator/Experts still collaborate normally within a case.

Resume the same batch with `python benchmarking/server/run_oceanx.py --queries ... --output ... --resume`.
This retries unsuccessful cases in new attempts; it is not a resumed model checkpoint
or an independent repetition. For an independent repetition use a new output directory.

`completed` is a runtime outcome, not a score or proof of correct science.
Unexpected human-input requests become `needs_interaction`; no blanket approval
is added by this workflow. Preserve failed/timed-out attempts in the denominator.

[Export, numerical comparison and LLM judging](../evaluation/README.md#export-and-judging-cli-reference)
are separate. All current per-query references remain draft: independently compute
and validate references on the frozen downloaded data before official grading.
Do not change `draft` to `validated` just to bypass the evaluator.

## 6. Claude Code + DeepSeek: run the same JSONL and keep results

`run_claude.py` runs the installed **Claude Code**, not Claude Science. It inherits
your existing direct DeepSeek API configuration/environment. No cc-switch or new
API configuration is required. Activate the same scientific Python environment;
make sure `claude --version` and your existing CLI API connection work there.

```bash
python benchmarking/server/run_claude.py \
  --queries "$HOME/oceanx-bench-inputs/autoresearch.jsonl" \
  --output "$HOME/claude-bench-runs/autoresearch-r1" \
  --allow-tools Read Glob Grep Bash Write Edit NotebookEdit WebSearch WebFetch
```

This explicitly approves the listed Claude tools for unattended execution. There
is **no** `--dangerously-skip-permissions`; other permissions remain governed by
Claude settings. Without `--allow-tools`, no new approvals are added and required
operations may be denied. Bash approval permits general shell execution: this
runner does not add OceanX's OS sandbox, enforce read-only files, or block network
uploads. Use an account/job environment restricted to the intended data/output,
with original data mounted read-only where possible. Do not expose private keys,
evaluator references, other attempts or unrelated data to executable agent code.
The read-only and literature restrictions in the prompt are instructions, not
security boundaries. `--add-dir` grants access to supplied dataset directories;
individual file references do not automatically grant their whole parent folder.

The wrapper reads exactly the same JSONL schema/query text and uses each case's
timeout. It adds only execution instructions (paths, output location, literature
policy), saved in `prompt.txt`. Existing OceanX-specific `permission_tools` cannot
be translated to Claude names and are rejected before execution; generated default
JSONL files have no such approvals. Questions/selected papers are supplied as
context, not handled by an OceanX protocol adapter.

Options:
- `--claude /absolute/path/to/claude` if the executable is not on PATH.
- `--model MODEL_ID` only if you want to override the existing CLI model selection.
- `--model-label LABEL` records an experiment label without changing API routing.
- Add `--resume` with the same arguments to skip completed cases and create new
  attempts for others. Never overwrites an earlier attempt. Inputs, tool approvals,
  model options and runner version must match; otherwise start a new output folder.

Run a pilot first by replacing the JSONL with `q21.jsonl` and using a fresh output
directory. No command-line API key is needed. Keep the actual API model/version,
user settings, plugins, hooks and environment fixed across comparisons. The runner
does not copy those settings or secrets into results. Each task starts a fresh
process/session with `--no-session-persistence`, no `--continue`/`--resume` passed
to Claude, and a new working directory outside the repository. Global Claude
memory/customizations may still load; this is not a claim of fully isolated memory.

```text
claude-bench-runs/autoresearch-r1/
  manifest.json              # inputs, CLI version, runner hash and explicit options
  results.jsonl              # one record per finished attempt
  Q13/attempt-.../
    query.json               # original query + resolved dataset paths
    prompt.txt               # exact text sent to Claude
    command.json             # CLI flags, no copied API configuration
    token_usage.json         # whole-call model totals, cache counts and deduplicated observed steps
    events.jsonl             # live Claude stream (64 MiB limit)
    stderr.log               # stderr (64 MiB limit)
    claude_result.json       # terminal CLI result if emitted
    answer.md                # final response if emitted, including error responses
    partial_answer.md        # available intermediate assistant text
    result.json              # status, elapsed seconds, exit code, usage when reported
    artifacts.json           # workspace file paths/sizes, without following symlinks
    workspace/               # generated code, notebooks, figures and tables in place
```

No data or generated artifacts are copied for auditing. No code/image is invented
if the agent fails to produce it. A success CLI result with no reported error is
`completed`, **not** scientifically verified. Nonzero exit, error result, missing
terminal result or exceeded log limit becomes `failed`; permission denials in a
terminal result conservatively become `needs_interaction`. No automatic tool
approval escalation occurs. Timeouts become `timed_out`; SIGINT/SIGTERM records
`cancelled` and stops the batch. Failed/timed-out cases retain partial files and
the next case runs. Process-group cleanup covers ordinary descendants, not escaped
daemon sessions; hard resource confinement needs host/job isolation. SIGKILL or
machine failure can leave an attempt without `result.json`; its files remain and
`--resume` creates a fresh attempt. CLI cost figures, if present, are provider/CLI
reports and may not reflect a third-party DeepSeek bill.

The current evaluation packer still accepts only `oceanx`/`claude-science`, so do
not relabel these Claude Code results to use it. This addition saves baseline
results only; automatic cross-system packing and judging are separate work.

CLI contract: [programmatic use](https://code.claude.com/docs/en/headless) and
[CLI flags](https://code.claude.com/docs/en/cli-reference). Tested with a simulated
CLI subprocess, not your server's paid DeepSeek API.

### Claude token accounting

Use `token_usage.json` → `whole_call_totals` for comparison. Its source is the final
`result.modelUsage` summed across models, including subagents. Report input,
output, cache-read and cache-creation tokens separately. `result.usage` only covers
the main loop: do not add it to the model totals. Raw `events.jsonl` and
`claude_result.json` are retained for audit. Per-step messages are deduplicated by
message ID and parent tool ID; their output-token placeholders are not treated as
actual totals. Missing final usage stays unknown, never zero. These are CLI-reported
counts, not a billing audit; `total_cost_usd` is not the DeepSeek invoice.

See [Claude's usage documentation](https://code.claude.com/docs/en/agent-sdk/cost-tracking).

## Tests

```bash
python -m pytest benchmarking/tests tests/test_oceanx/test_batch.py
```

Tests use fixtures/no paid model calls. Passing on macOS does not prove the
server's Linux sandbox passes. No desktop or multi-agent runtime code is changed.
