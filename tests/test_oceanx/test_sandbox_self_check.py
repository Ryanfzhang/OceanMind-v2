"""Regression tests for the executable sandbox self-check used by desktop packaging."""

from __future__ import annotations

import pytest

from oceanx.sandbox import get_sandbox_execution_capabilities
from oceanx.sandbox_self_check import run_sandbox_self_check


@pytest.mark.asyncio
async def test_sandbox_self_check_proves_the_local_read_write_contract():
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    report = await run_sandbox_self_check()

    assert report["schema_version"] == "ocean-sandbox-self-check/v1"
    assert report["backend"] == capabilities.backend
    assert report["passed"] is True
    assert report["checks"] == {
        "declared_output_written": True,
        "outside_read_denied": True,
        "scientific_imports": {
            "zarr": True,
            "netCDF4": True,
            "gsw": True,
        },
    }
