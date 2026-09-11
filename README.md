安装与环境配置见 [INSTALL.md](INSTALL.md)；测评见 [benchmarking/INSTALL.md](benchmarking/INSTALL.md)。

# OceanMind

OceanMind is a local-first multi-agent workbench for ocean-science research. It supports
ordinary conversation, paper and dataset inspection, reproducible scientific analysis,
interactive results, and longer autoresearch workflows through one Coordinator-led loop.

## Runtime architecture

For server-side evaluation without Electron, use `ocean run --query ... --output ...`
or `ocean batch --queries queries.jsonl --output ...`. See the
[headless benchmark guide](docs/evals/headless-benchmark.md) for Linux sandbox setup,
read-only shared data, interaction policies and batch resume. Desktop and agent
orchestration behavior are unchanged by this additional client.

```text
Electron Desktop
    ↕ Ocean protocol v2
OceanMind backend
    ├─ Coordinator (Deep Agents + LangGraph)
    ├─ Expert threads (Deep Agents + LangGraph)
    ├─ Ocean domain tools
    ├─ ResultBundle + conclusion/evidence bindings
    ├─ SQLite task and LangGraph checkpoints
    └─ fail-closed scientific Python sandbox
```

The Coordinator decides whether to answer directly or create a bounded TodoPlan. Independent
todos are dispatched concurrently; each todo owns one persistent Expert thread. An Expert uses
ordinary model tool calls and returns one normal final answer. There is no second handoff protocol
or result-formatting agent loop.

Scientific code execution persists valid outputs as they are produced. `ExpertResult` combines
plain text, result references, and conclusions bound to the visual or report outputs that support
them. A provider interruption therefore does not erase completed figures or rerun finished code.

LangGraph SQLite checkpoints are the source of truth for participant conversation state. The
OceanMind task database stores product state, result bundles, progress, and a compact transcript
projection for the Desktop UI.

Python and sandbox checks run when an Expert actually invokes code execution, not when a
text-only or literature assignment starts. Code execution still fails closed if its runtime
is unavailable.

Each analysis round has one editable `supplementary/analysis-*/analysis.ipynb`. A later round
gets a separate notebook; retries do not overwrite existing files or user edits. Notebooks
read the accepted NetCDF results in place, without copying scientific data. Their renderer
preserves contour, uncertainty-band, vector and category layers, axis scales and color domains;
unsupported layers raise an error rather than silently disappear.

## Experience and Skills

Agents can explicitly save a short reusable experience. The periodic Skill Curator receives
these notes plus an all-role Skill metadata catalog, chooses relevant documents with `read_skill`,
and submits structured decisions. Full Skill bodies are not preloaded. An update requires reading
the current document and a version-checked commit. Scientific judgment remains with the Curator;
the backend checks document structure, identity and write safety. Successful commits notify the
Desktop to refresh the affected task's results without restarting its research workflow.

## Model providers

The Desktop settings support Anthropic-compatible and OpenAI-compatible APIs. OpenAI-compatible
profiles can point at providers such as DeepSeek through a custom base URL. New credentials are
stored outside the project under `~/.oceanmind`; existing local settings can be read once from the
previous configuration location during migration.

## Project state

New projects use:

```text
<project>/.oceanmind/
    workspace.sqlite3
    langgraph.sqlite3
    artifacts/
    datasets/
    runs/
    staging/
    exports/
```

## Development

Create the `oceanx` environment once with `conda env create -f environment.yml`.
For an existing environment, use `conda env update -n oceanx -f environment.yml`.
This installs Python 3.11, Node.js 24, and the Python requirements in one environment.

```bash
conda activate oceanx
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"

cd frontend/ocean-desktop
npm ci
npm run check
npm run build
```

Keep `conda activate oceanx` active when running `npm start`, `npm run dev`, or
`npm run build:sidecar`. Local desktop startup and sidecar builds use that
environment's Python and installed dependencies, not a repository `.venv`.
`requirements.txt` includes the scientific runtime and PyInstaller build dependency.
Node.js is managed by `environment.yml`. On Apple Silicon, check that
`node -p process.arch` reports `arm64`; after changing Node architecture, rerun
`npm ci` to replace platform-specific frontend dependencies.
`OCEAN_PYTHON` is an explicit override; otherwise `CONDA_PREFIX` takes priority
over Python on `PATH`. A broken active Conda environment reports an error rather
than silently selecting another interpreter. Installed desktop packages still
use their bundled backend. Scientific code execution also follows active Conda
by default; `OCEAN_CONDA_ENV` and `OCEAN_SANDBOX_PYTHON` remain explicit scientific
runtime overrides. Without activation, scientific execution still looks for the
named `oceanx` environment.

### Runtime context summaries

All Coordinator and Expert graphs reuse DeepAgents' built-in summarization.
OceanX starts summarizing at 24,000 context tokens **and** 12 messages, retaining
the latest six messages; models with a known context window also retain an
earlier 65%-of-window safety trigger. These are initial cost-oriented settings,
not scientific completion criteria or a promised benchmark speedup.
Short conversations remain unsummarized. Summaries preserve scientific quantities,
statistical definitions, evidence IDs, working-file locations and unresolved work.
The same configured model/API produces the summary with at most 4,096 output
tokens, and its usage counts toward the existing cumulative budget. Internal
summary text is not emitted as an agent answer.

DeepAgents archives evicted history in the thread's checkpointed state.
`ocean_read_context_archive` can retrieve bounded text excerpts from that archive
after a restart; it cannot read local files or another expert's thread. Existing
code/log retrieval, research-tree decisions, and output publication are unchanged.

Run the OceanMind backend tests:

```bash
python -m pytest -q tests/test_oceanx
```

The macOS scientific sandbox starts a nested Seatbelt process. When tests are themselves running
inside another restrictive sandbox, run the trusted-execution tests from a normal host terminal.

Build and inspect the Python wheel:

```bash
python -m hatchling build -t wheel
python scripts/check_ocean_wheel_contents.py
```

The wheel contains only `oceanx`; the former agent-runtime package is not shipped.
