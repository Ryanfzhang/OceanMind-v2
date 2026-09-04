"""Machine-readable local capability report for Ocean Partner execution."""

from __future__ import annotations

import platform
import sys
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from ocean_partner.desktop_contract import (
    DESKTOP_BACKEND_SCHEMA,
    DESKTOP_RUNTIME_CAPABILITY_SCHEMA,
    DESKTOP_RUNTIME_CONNECTIONS,
)
from ocean_partner.sandbox import (
    SandboxUnavailableError,
    current_python_runtime,
    get_sandbox_execution_capabilities,
)
from ocean_partner.scientific_runtime import frozen_scientific_runtime_capability
from ocean_partner.skills import ocean_skill_metadata

_OPTIONAL_EXTRAS = {
    "numpy": "array computation",
    "pandas": "tabular data",
    "xarray": "labelled array and NetCDF workflows",
    "matplotlib": "static plotting",
    "h5py": "HDF5 bindings required by h5netcdf",
    "h5netcdf": "NetCDF via h5py",
    "netCDF4": "NetCDF4 bindings",
    "zarr": "Zarr stores",
    "cartopy": "map projections",
    "gsw": "TEOS-10 seawater calculations",
}

def ocean_doctor() -> dict[str, Any]:
    """Describe the exact local execution capabilities without attempting installation."""

    sandbox = get_sandbox_execution_capabilities()
    interpreter_error: str | None = None
    selected_runtime = None
    try:
        selected_runtime = current_python_runtime()
        interpreter_executable = str(selected_runtime.executable)
    except (OSError, RuntimeError, SandboxUnavailableError) as exc:
        interpreter_executable = None
        interpreter_error = str(exc)
    expert_execution_available = sandbox.available and interpreter_executable is not None
    frozen_runtime = frozen_scientific_runtime_capability()
    extras = {
        name: {
            "available": selected_runtime is not None
            and name in selected_runtime.available_modules,
            "purpose": purpose,
        }
        for name, purpose in _OPTIONAL_EXTRAS.items()
    }
    netcdf_available = extras["netCDF4"]["available"] or (
        extras["h5netcdf"]["available"] and extras["h5py"]["available"]
    )
    return {
        "schema_version": "ocean-doctor/v1",
        "backend_schema": DESKTOP_BACKEND_SCHEMA,
        "interpreter": {
            "executable": interpreter_executable,
            "implementation": platform.python_implementation(),
            "version": selected_runtime.version if selected_runtime is not None else None,
            "environment": (
                selected_runtime.environment_name if selected_runtime is not None else "ocean"
            ),
            "prefix": str(selected_runtime.prefix) if selected_runtime is not None else None,
        },
        "sandbox": asdict(sandbox),
        "frozen_scientific_runtime": frozen_runtime,
        "extras": extras,
        "capabilities": {
            "expert_code_execution": expert_execution_available,
            "web_search": True,
            "jina_reader": True,
            "maps": extras["cartopy"]["available"],
            "zarr": extras["zarr"]["available"],
            "gsw": extras["gsw"]["available"],
            "netcdf": netcdf_available,
        },
        "unavailable_reasons": {
            "expert_code_execution": (
                None
                if expert_execution_available
                else interpreter_error or sandbox.reason
            ),
            "web_search": None,
            "jina_reader": None,
            "maps": None if extras["cartopy"]["available"] else "cartopy is not installed",
            "zarr": None if extras["zarr"]["available"] else "zarr is not installed",
            "gsw": None if extras["gsw"]["available"] else "gsw is not installed",
            "netcdf": (
                None
                if netcdf_available
                else "install h5netcdf with h5py or install netCDF4"
            ),
        },
        "runtime": {
            "backend_sys_prefix": sys.prefix,
            "sandbox_sys_prefix": (
                str(selected_runtime.prefix) if selected_runtime is not None else None
            ),
        },
    }


def desktop_runtime_capabilities(
    *,
    skill_capabilities: Iterable[str] = (),
) -> dict[str, Any]:
    """Return the bounded runtime projection that may cross into a renderer.

    ``ocean_doctor`` remains an operator-facing local diagnostic and can include
    executable or installation details. The Desktop handshake must never expose
    those details to an untrusted renderer, so this projection is deliberately
    constructed from a small, reviewed allowlist rather than by filtering the
    diagnostic document after the fact.
    """

    doctor = ocean_doctor()
    capabilities = doctor["capabilities"]
    frozen_runtime = doctor["frozen_scientific_runtime"]
    scientific_runtime: dict[str, Any] = {
        "available": bool(frozen_runtime.get("available")),
    }
    if scientific_runtime["available"]:
        schema_version = frozen_runtime.get("schema_version")
        fingerprint = frozen_runtime.get("fingerprint_sha256")
        dependency_count = frozen_runtime.get("dependency_count")
        if isinstance(schema_version, str):
            scientific_runtime["schema_version"] = schema_version
        if isinstance(fingerprint, str):
            scientific_runtime["fingerprint_sha256"] = fingerprint
        if isinstance(dependency_count, int) and not isinstance(dependency_count, bool):
            scientific_runtime["dependency_count"] = dependency_count

    return {
        "schema_version": DESKTOP_RUNTIME_CAPABILITY_SCHEMA,
        "backend_schema": DESKTOP_BACKEND_SCHEMA,
        "connections": [
            {
                "id": identifier,
                "label": label,
                "available": bool(capabilities.get(identifier)),
            }
            for identifier, label in DESKTOP_RUNTIME_CONNECTIONS
        ],
        "scientific_runtime": scientific_runtime,
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "version": skill.version,
            }
            for skill in ocean_skill_metadata(capabilities=skill_capabilities)
        ],
    }


__all__ = ["desktop_runtime_capabilities", "ocean_doctor"]
