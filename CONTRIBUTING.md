# Contributing to OceanMind

OceanMind is a local-first multi-agent workbench for ocean-science research.

## Development setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,ocean-runtime]"

cd frontend/ocean-desktop
npm ci
```

## Validation

```bash
.venv/bin/python -m pytest -q
make lint

cd frontend/ocean-desktop
npm run check
npm test -- --run
npm run build
```

For changes to packaging, also build the Python wheel and Desktop sidecar:

```bash
.venv/bin/python -m hatchling build -t wheel
.venv/bin/python scripts/check_ocean_wheel_contents.py

cd frontend/ocean-desktop
npm run build:sidecar
```

## Pull requests

- Keep changes scoped and preserve existing task history and local research data.
- Add focused tests for behavioral changes.
- Keep scientific claims tied to inspectable evidence and reproducible outputs.
- Update the changelog for user-visible changes.
