PYTHON ?= .venv/bin/python
PYTEST = PYTHONPATH=src $(PYTHON) -m pytest
RUFF_TARGETS = \
	src/oceanx/agent.py \
	src/oceanx/agent_contract.py \
	src/oceanx/agent_tools.py \
	src/oceanx/deep_runtime.py \
	src/oceanx/model_config.py \
	src/oceanx/tool_history.py \
	src/oceanx/web_search.py \
	src/oceanx/runtime.py \
	src/oceanx/tools.py \
	src/oceanx/team/orchestrator.py \
	src/oceanx/sandbox \
	tests/test_oceanx/test_deep_agent_runtime.py

.PHONY: lint typecheck test test-contract test-e2e-offline test-wheel build release-manifest

lint:
	$(PYTHON) -m ruff check $(RUFF_TARGETS)

typecheck:
	cd frontend/ocean-desktop && npm run check

test:
	$(PYTEST) -q

test-contract:
	PYTHONPATH=src $(PYTHON) scripts/export_protocol_v2.py
	cd frontend/ocean-desktop && npm run check

test-e2e-offline:
	$(PYTEST) -q tests/test_oceanx

test-wheel:
	$(PYTHON) scripts/check_ocean_wheel_contents.py --dist dist

build:
	cd frontend/ocean-desktop && npm run build
	$(PYTHON) -m build
	$(PYTHON) scripts/check_ocean_wheel_contents.py --dist dist

release-manifest:
	test -n "$(APP)" && test -n "$(VERSION)" && test -n "$(OUTPUT)"
	$(PYTHON) scripts/generate_desktop_release_manifest.py --app "$(APP)" --output "$(OUTPUT)" --version "$(VERSION)"
