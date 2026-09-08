# Headless benchmark runner

`ocean run` and `ocean batch` are local, headless clients of OceanMind's existing
Protocol v2 backend. They do **not** change Desktop interactions, agent prompts,
Coordinator decisions, Expert scheduling, or paper-selection semantics. No HTTP
server or open port is required. Start the commands on the machine holding the data.

## Linux preparation

Use a normal, non-root account with a dedicated scientific Python environment.
Install the project as usual (`uv sync --extra ocean-runtime`, or
`python -m pip install -e '.[ocean-runtime]'`). On Debian/Ubuntu:

```sh
sudo apt-get install bubblewrap libseccomp2
export OCEAN_SANDBOX_PYTHON="/absolute/path/to/scientific-env/bin/python"
ocean doctor
ocean sandbox-self-check
```

The self-check **must pass on the target server** before starting a real benchmark.
Linux needs working unprivileged user namespaces, not merely an installed `bwrap`.
Some HPC/container/AppArmor policies forbid them. In that case the execution fails
closed: use a host where an administrator permits this isolation mechanism. Do not
disable the sandbox or run generated code with root/privileged container permissions.
The runner does not install packages or change host security settings automatically.

Linux uses bubblewrap namespaces plus a native libseccomp filter. Host datasets and
the scientific environment are mounted read-only; only each execution's declared
working/output/temporary directories are writable. Scientific code has no network,
does not inherit API keys, and cannot fork when the existing policy disallows it.
The normal backend still makes model and literature network calls using its existing
tools. CPU/file/process/open-file limits, wall time, bounded streams, RSS monitoring,
and existing output validation remain in use. RSS/aggregate disk checks are monitored
or postflight limits, **not hard cgroup-wide quotas**. For hostile multi-tenant use,
additional administrator-managed per-job cgroup quotas are advisable.

This is an additional platform backend: macOS Seatbelt and the Windows broker
remain the default paths on their respective platforms.

## Model configuration

The commands use the same role-specific profile mechanism as Desktop, but on the
server. Existing configured profiles work unchanged. When using `uv sync`, activate
`.venv` first (`source .venv/bin/activate`) or prefix commands with `uv run`.
To configure a new server:

```sh
ocean configure-models < /secure/path/role-config.json
```

The input is `{"roles": {"coordinator": {...}, "expert": {...}, "skill_curator": {...}}}`.
Each role has `provider` (`openai` or `anthropic`), `model`, `base_url`
(or null), and `api_key`. Keep that file private, outside datasets/results and source
control. OpenAI-compatible providers such as DeepSeek use `openai` with their custom
base URL. The command does not echo keys. `OCEANMIND_CONFIG_DIR` can select an isolated
server configuration directory. Do not put credentials inside query JSONL files.

## One query

```sh
ocean run \
  --query "Inspect the chlorophyll data and quantify its seasonal variation, including uncertainty and limitations." \
  --dataset /srv/ocean-data/CMOMS/chlorophyll_2011.nc \
  --output /srv/ocean-runs/smoke-001 \
  --timeout 3600
```

Repeat `--dataset` for multiple authorized sources. The output directory must be
new and must not overlap any source directory. Files are registered through
`dataset.import` with `local_reference`, not copied or linked into new datasets.
The query is submitted unchanged. Files/directories use the same accepted dataset
formats and symlink restrictions as Desktop.

## A query set

Each nonempty line in `queries.jsonl` is one JSON object:

```json
{"id":"D01","query":"Calculate the seasonal surface chlorophyll cycle and explain the treatment of missing values.","datasets":["/srv/ocean-data/CMOMS/chlorophyll_2011.nc"],"timeout_seconds":3600,"literature_mode":"search_only"}
```

```sh
ocean batch --queries queries.jsonl --output /srv/ocean-runs/benchmark-001
ocean batch --queries queries.jsonl --output /srv/ocean-runs/benchmark-001 --resume
```

IDs must be unique and contain only letters, digits, `_` and `-` (start with a
letter/digit). Relative dataset paths resolve against the query file's directory,
not the shell's working directory. All queries/paths are validated before the
first backend starts. Keep evaluator rubrics/reference answers in a separate file;
unknown query fields are rejected instead of accidentally disclosing grading data.

Cases run **sequentially** in this first version; each case's Experts can still
work concurrently under the existing Coordinator. Each case/attempt has a separate
backend process, SQLite state, task and output workspace. No histories or candidate
experience databases are shared between cases. Skill Curator is disabled only for
these backend processes, so skills do not evolve during evaluation. Desktop's
default remains enabled.

For the canonical Q01–Q30 tasks, `benchmarking/server/prepare_queries.py` can
generate this JSONL from explicit shared data bindings. Inputs can be directories
such as `/srv/ocean-data/MODIS_Aqua/chlorophyll/`; data is referenced, not copied
into each case. The helper checks paths, not scientific time/variable coverage.
See [download and shared archive guide](../../benchmarking/download/README.md).

`timeout_seconds` is an outer wall-clock ceiling including backend startup and
data registration, not an increase to the existing Agent/Expert internal budgets.
Timeout/cancellation first uses the normal request cancellation and shutdown
protocol; an unresponsive backend is killed after a bounded grace period. Ctrl+C
and SIGTERM save the current outcome and stop the batch rather than start more cases.

## Human interactions in unattended runs

By default, any paper selection, permission or question requiring a person ends
that attempt as `needs_interaction`, preserving the question/options in its result.
The runner cancels the pending request and moves to the next case. This is not an
automatically resumable live paper picker; Desktop paper picking is unchanged.

Optional **per-case** fields provide explicit answers:

- `selected_papers`: exact titles or URLs from the expected shortlist. All supplied
  selectors must uniquely match before any selection is sent. No automatic first-N/all choice.
- `permission_tools`: exact tool names whose permission prompts you authorize for
  that case. Leave empty unless the benchmark explicitly grants those operations.
- `answers`: mapping from an exact expected question to its answer. These answers
  cannot approve permission requests.
- `literature_mode`: `ask_before_download` (default), `search_only`, or
  `auto_download_open_access` (explicit download authorization under existing tools).

For autoresearch, the query itself may ask the Coordinator to choose the evidence;
the runner does not add a separate paper-combination or scientific decision rule.
These choices must be fixed consistently across systems being compared.

## Outputs and interpretation

```text
benchmark-001/
  manifest.json                  # query/source metadata and code version
  results.jsonl                  # one record per finished attempt
  D01/attempt-.../
    query.json
    result.json                  # status, task ID, time, usage, terminal payload
    answer.md                    # Coordinator's terminal answer, if returned
    outputs.json                 # existing task output records; no copied data
    events.jsonl                 # protocol stream (64 MiB cap, truncation flag)
    backend.log                  # backend diagnostics (64 MiB cap)
    state/                       # isolated databases and canonical artifacts
    workspace/OceanMind Tasks/   # normal editable reports/notebooks/results
```

`completed` means the backend returned normally, **not** that the hypothesis is
true, the science is correct, or the benchmark passed. Preserve the raw final
response/provenance for a separate evaluator. Other statuses are `failed`,
`timed_out`, `needs_interaction`, and `cancelled`. Token usage is preserved separately
for Coordinator and Experts when reported; missing usage is not zero. Monetary
cost is not estimated. Progress is on stderr; stdout contains a JSON summary.
Exit code is 0 only if every selected case completed, otherwise nonzero.

`--resume` skips completed cases with the same manifest and retries unsuccessful
cases in new attempt folders. It does not reuse an interrupted agent checkpoint.
Source identity checks use path/size/mtime metadata, not expensive hashes of large
datasets or recursive directory contents. Freeze the source collection, model
profiles, skills and code checkout for a benchmark; changing these calls for a new
output directory. Two runners cannot write the same batch concurrently. After an
uncatchable crash (e.g. SIGKILL), check that no runner/backend remains before manually
removing that batch's `.runner.lock` and resuming. Outputs contain research logs and
local paths; review them before sharing.

## Verification

```sh
pytest -q tests/test_oceanx/test_batch.py tests/test_sandbox/test_linux.py
```

The Linux benchmark CI workflow exercises real namespaces/seccomp and a real backend
handshake/task/data-reference cycle without invoking paid models. Scientific model
quality and completion still require a subsequent real-query benchmark run.
