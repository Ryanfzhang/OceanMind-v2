# OceanMind

OceanMind is a local-first multi-agent workbench for ocean-science research. It supports
ordinary conversation, paper and dataset inspection, reproducible scientific analysis,
interactive results, and longer autoresearch workflows through one Coordinator-led loop.

## Runtime architecture

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

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,ocean-runtime]"

cd frontend/ocean-desktop
npm ci
npm run check
npm run build
```

Run the OceanMind backend tests:

```bash
.venv/bin/python -m pytest -q tests/test_ocean_partner
```

The macOS scientific sandbox starts a nested Seatbelt process. When tests are themselves running
inside another restrictive sandbox, run the trusted-execution tests from a normal host terminal.

Build and inspect the Python wheel:

```bash
.venv/bin/python -m hatchling build -t wheel
.venv/bin/python scripts/check_ocean_wheel_contents.py
```

The wheel contains only `ocean_partner`; the former agent-runtime package is not shipped.
