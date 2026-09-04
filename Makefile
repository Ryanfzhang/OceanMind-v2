PYTHON ?= .venv/bin/python
PYTEST = PYTHONPATH=src $(PYTHON) -m pytest
RUFF_TARGETS = \
	src/ocean_partner/agent.py \
	src/ocean_partner/agent_contract.py \
	src/ocean_partner/agent_tools.py \
	src/ocean_partner/deep_runtime.py \
	src/ocean_partner/model_config.py \
	src/ocean_partner/tool_history.py \
	src/ocean_partner/web_search.py \
	src/ocean_partner/runtime.py \
	src/ocean_partner/tools.py \
	src/ocean_partner/team/orchestrator.py \
	src/ocean_partner/sandbox \
	tests/test_ocean_partner/test_deep_agent_runtime.py

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
	$(PYTEST) -q tests/test_ocean_partner

test-wheel:
	$(PYTHON) scripts/check_ocean_wheel_contents.py --dist dist

build:
	cd frontend/ocean-desktop && npm run build
	$(PYTHON) -m build
	$(PYTHON) scripts/check_ocean_wheel_contents.py --dist dist

release-manifest:
	test -n "$(APP)" && test -n "$(VERSION)" && test -n "$(OUTPUT)"
	$(PYTHON) scripts/generate_desktop_release_manifest.py --app "$(APP)" --output "$(OUTPUT)" --version "$(VERSION)"
