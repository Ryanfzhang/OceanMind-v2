"""Shared test fixtures."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def explicit_test_python_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic while production defaults to Conda environment ocean."""

    monkeypatch.setenv("OCEAN_SANDBOX_PYTHON", sys.executable)
