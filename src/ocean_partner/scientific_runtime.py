"""Deterministic scientific-runtime manifests for expert-owned scientific code."""

from __future__ import annotations

import hashlib
import importlib
import json
import platform
import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


_DISTRIBUTION_NORMALIZER = re.compile(r"[-_.]+")
FROZEN_SCIENTIFIC_RUNTIME_SCHEMA = "ocean-frozen-scientific-runtime/v1"
FROZEN_SCIENTIFIC_RUNTIME_FILENAME = "ocean-scientific-runtime.json"
FROZEN_SCIENTIFIC_RUNTIME_MODULES = (
    "numpy",
    "pandas",
    "scipy",
    "xarray",
    "matplotlib",
    "PIL",
    "h5py",
    "h5netcdf",
    "zarr",
    "fsspec",
    "dask",
    "rasterio",
    "pyproj",
    "shapely",
    "netCDF4",
    "cartopy",
    "gsw",
)


@dataclass(frozen=True)
class ScientificRuntimeManifest:
    """One canonical runtime manifest and its stable content fingerprint."""

    payload: dict[str, Any]
    sha256: str


def capture_scientific_runtime_manifest(
    *,
    required_modules: Iterable[str],
    python_executable: Path | None = None,
) -> ScientificRuntimeManifest:
    """Capture the interpreter, dependencies, and native scientific libraries.

    The manifest is deliberately independent from a code-execution identifier, so
    two executions in the same unchanged interpreter produce the same fingerprint.
    It is private local provenance; public protocol projections expose only the
    resulting SHA-256.
    """

    executable = (python_executable or Path(sys.executable)).resolve(strict=False)
    required = tuple(sorted({name for name in required_modules if name}))
    payload = {
        "schema_version": "ocean-scientific-runtime/v1",
        "interpreter": {
            "executable": str(executable),
            "implementation": platform.python_implementation(),
            "version": list(sys.version_info[:3]),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "required_modules": list(required),
        "distributions": _distributions(),
        "native_runtime": _native_runtime(),
    }
    return scientific_runtime_manifest(payload)


def capture_frozen_scientific_runtime_manifest(
    *,
    required_modules: Iterable[str],
) -> ScientificRuntimeManifest:
    """Capture the package-level scientific runtime identity for a frozen sidecar.

    This package-level manifest deliberately excludes an install
    path and host OS patch release. A signed app may be moved after installation
    and still contain exactly the same frozen Python and native scientific
    dependencies. The package verifier compares this record against a fresh
    invocation of the final sidecar before it accepts the bundle.
    """

    required = tuple(sorted({name for name in required_modules if name}))
    payload = {
        "schema_version": FROZEN_SCIENTIFIC_RUNTIME_SCHEMA,
        "interpreter": {
            "implementation": platform.python_implementation(),
            "version": list(sys.version_info[:3]),
        },
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "required_modules": list(required),
        "distributions": _distributions(),
        "native_runtime": _native_runtime(),
    }
    return frozen_scientific_runtime_manifest(payload)


def scientific_runtime_manifest(payload: dict[str, Any]) -> ScientificRuntimeManifest:
    """Normalize an internally generated manifest and compute its fingerprint."""

    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    normalized = json.loads(canonical)
    return ScientificRuntimeManifest(
        payload=normalized,
        sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def frozen_scientific_runtime_manifest(payload: dict[str, Any]) -> ScientificRuntimeManifest:
    """Validate and hash the bounded, install-location-independent package record."""

    if set(payload) != {
        "schema_version",
        "interpreter",
        "platform",
        "required_modules",
        "distributions",
        "native_runtime",
    }:
        raise ValueError("Frozen scientific runtime manifest has incompatible fields")
    if payload.get("schema_version") != FROZEN_SCIENTIFIC_RUNTIME_SCHEMA:
        raise ValueError("Frozen scientific runtime manifest schema is incompatible")
    interpreter = payload.get("interpreter")
    platform_payload = payload.get("platform")
    modules = payload.get("required_modules")
    distributions = payload.get("distributions")
    native_runtime = payload.get("native_runtime")
    if (
        not isinstance(interpreter, dict)
        or set(interpreter) != {"implementation", "version"}
        or not isinstance(interpreter.get("implementation"), str)
        or not interpreter["implementation"]
        or not isinstance(interpreter.get("version"), list)
        or len(interpreter["version"]) != 3
        or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in interpreter["version"])
        or not isinstance(platform_payload, dict)
        or set(platform_payload) != {"system", "machine"}
        or any(not isinstance(platform_payload.get(key), str) or not platform_payload[key] for key in platform_payload)
        or not isinstance(modules, list)
        or modules != sorted(set(modules))
        or any(not isinstance(module, str) or not module for module in modules)
        or not isinstance(distributions, list)
        or not isinstance(native_runtime, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in native_runtime.items())
    ):
        raise ValueError("Frozen scientific runtime manifest has invalid fields")
    for distribution in distributions:
        if not isinstance(distribution, dict) or set(distribution) not in (
            {"name", "version"},
            {"name", "version", "direct_url", "editable"},
        ):
            raise ValueError("Frozen scientific runtime manifest distribution is invalid")
        if any(not isinstance(distribution.get(key), str) or not distribution[key] for key in ("name", "version")):
            raise ValueError("Frozen scientific runtime manifest distribution is invalid")
        if "direct_url" in distribution and (
            not isinstance(distribution["direct_url"], str)
            or not distribution["direct_url"]
            or not isinstance(distribution["editable"], bool)
        ):
            raise ValueError("Frozen scientific runtime manifest distribution is invalid")
    if distributions != sorted(distributions, key=lambda item: (item["name"], item["version"])):
        raise ValueError("Frozen scientific runtime distributions are not ordered")
    return scientific_runtime_manifest(payload)


def frozen_scientific_runtime_capability(
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Return a bounded package-runtime identity without exposing its manifest.

    The full document remains a sidecar-local file. ``doctor`` and the Desktop
    protocol use only this compact projection, so a renderer cannot read local
    interpreter paths, direct-url metadata, or dependency names from it.
    """

    if manifest_path is None:
        if not getattr(sys, "frozen", False):
            return {
                "available": False,
                "reason": "Frozen scientific runtime baseline requires a packaged sidecar",
            }
        manifest_path = Path(sys.executable).resolve().parent / FROZEN_SCIENTIFIC_RUNTIME_FILENAME
    try:
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("Frozen scientific runtime baseline is missing")
        raw = manifest_path.read_bytes()
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Frozen scientific runtime baseline exceeds its size limit")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Frozen scientific runtime baseline is not an object")
        recorded = frozen_scientific_runtime_manifest(payload)
        actual = capture_frozen_scientific_runtime_manifest(
            required_modules=FROZEN_SCIENTIFIC_RUNTIME_MODULES
        )
        if recorded.sha256 != actual.sha256:
            raise ValueError("Frozen scientific runtime baseline does not match the sidecar")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "schema_version": FROZEN_SCIENTIFIC_RUNTIME_SCHEMA,
        "fingerprint_sha256": recorded.sha256,
        "dependency_count": len(recorded.payload["distributions"]),
        "reason": None,
    }


def _distributions() -> list[dict[str, Any]]:
    distributions: list[dict[str, Any]] = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if not isinstance(name, str) or not name.strip():
            continue
        record: dict[str, Any] = {
            "name": _canonical_distribution_name(name),
            "version": distribution.version,
        }
        direct_url = _direct_url_record(distribution)
        if direct_url is not None:
            record["direct_url"] = direct_url["url"]
            record["editable"] = direct_url["editable"]
        distributions.append(record)
    return sorted(distributions, key=lambda item: (item["name"], item["version"]))


def _direct_url_record(distribution: metadata.Distribution) -> dict[str, Any] | None:
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    url = payload.get("url")
    if not isinstance(url, str) or not url:
        return None
    dir_info = payload.get("dir_info")
    return {
        "url": _redact_direct_url(url),
        "editable": isinstance(dir_info, dict) and bool(dir_info.get("editable")),
    }


def _redact_direct_url(value: str) -> str:
    """Keep origin/source identity without persisting URL credentials or queries."""

    parsed = urlsplit(value)
    if not parsed.scheme:
        return value.split("?", 1)[0].split("#", 1)[0]
    host = parsed.hostname or ""
    if parsed.port is not None:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _native_runtime() -> dict[str, str]:
    values: dict[str, str] = {}
    probes = (
        ("hdf5", "h5py", "version.hdf5_version"),
        ("h5netcdf", "h5netcdf", "__version__"),
        ("netcdf", "netCDF4", "getlibversion"),
        ("proj", "pyproj", "proj_version_str"),
        ("geos", "shapely", "geos_version_string"),
    )
    for name, module_name, attribute in probes:
        value = _probe_native_value(module_name, attribute)
        if value is not None:
            values[name] = value
    return values


def _probe_native_value(module_name: str, attribute: str) -> str | None:
    try:
        value: Any = importlib.import_module(module_name)
        for part in attribute.split("."):
            value = getattr(value, part)
        if callable(value):
            value = value()
    except (AttributeError, ImportError, OSError, RuntimeError):
        return None
    return str(value) if value is not None else None


def _canonical_distribution_name(name: str) -> str:
    return _DISTRIBUTION_NORMALIZER.sub("-", name).lower()


__all__ = [
    "FROZEN_SCIENTIFIC_RUNTIME_FILENAME",
    "FROZEN_SCIENTIFIC_RUNTIME_MODULES",
    "FROZEN_SCIENTIFIC_RUNTIME_SCHEMA",
    "ScientificRuntimeManifest",
    "capture_frozen_scientific_runtime_manifest",
    "capture_scientific_runtime_manifest",
    "frozen_scientific_runtime_capability",
    "frozen_scientific_runtime_manifest",
    "scientific_runtime_manifest",
]
