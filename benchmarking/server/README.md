# Running OceanX on a shared-data server

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

ocean batch --queries "$HOME/oceanx-bench-inputs/autoresearch.jsonl" \
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
ocean batch --queries "$HOME/oceanx-bench-inputs/q21.jsonl" \
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
ocean batch --queries "$HOME/oceanx-bench-inputs/non-cmoms.jsonl" \
  --output "$HOME/oceanx-bench-runs/non-cmoms-r1"
```

The preset rejects missing/duplicate/non-PDF reading files; it cannot establish
their bibliographic identity, completeness or licensing. Review and freeze the
same full texts for both systems. Do not include evaluator checklists or target images.

## 5. Results, retries and evaluation

The batch writes `results.jsonl` and per-attempt `query.json`, `result.json`,
`answer.md`, event logs and derived artifacts. It runs cases sequentially, while
the existing Coordinator/Experts still collaborate normally within a case.

Resume the same batch with `ocean batch --queries ... --output ... --resume`.
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

## Tests

```bash
python -m pytest benchmarking/tests tests/test_oceanx/test_batch.py
```

Tests use fixtures/no paid model calls. Passing on macOS does not prove the
server's Linux sandbox passes. No desktop or multi-agent runtime code is changed.
