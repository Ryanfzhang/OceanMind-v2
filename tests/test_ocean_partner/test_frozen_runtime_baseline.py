from __future__ import annotations

import json

import pytest

from ocean_partner.scientific_runtime import (
    FROZEN_SCIENTIFIC_RUNTIME_MODULES,
    FROZEN_SCIENTIFIC_RUNTIME_SCHEMA,
    capture_frozen_scientific_runtime_manifest,
    frozen_scientific_runtime_capability,
    frozen_scientific_runtime_manifest,
)


def test_frozen_runtime_manifest_is_path_independent_and_canonical() -> None:
    manifest = capture_frozen_scientific_runtime_manifest(
        required_modules=FROZEN_SCIENTIFIC_RUNTIME_MODULES
    )

    assert manifest.payload["schema_version"] == FROZEN_SCIENTIFIC_RUNTIME_SCHEMA
    assert set(manifest.payload["interpreter"]) == {"implementation", "version"}
    assert set(manifest.payload["platform"]) == {"system", "machine"}
    assert manifest.payload["required_modules"] == sorted(FROZEN_SCIENTIFIC_RUNTIME_MODULES)
    assert "executable" not in json.dumps(manifest.payload)
    assert "release" not in manifest.payload["platform"]
    assert frozen_scientific_runtime_manifest(manifest.payload).sha256 == manifest.sha256


def test_frozen_runtime_capability_rejects_a_tampered_baseline(tmp_path) -> None:
    manifest = capture_frozen_scientific_runtime_manifest(
        required_modules=FROZEN_SCIENTIFIC_RUNTIME_MODULES
    )
    path = tmp_path / "ocean-scientific-runtime.json"
    path.write_text(json.dumps(manifest.payload), encoding="utf-8")

    capability = frozen_scientific_runtime_capability(path)
    assert capability == {
        "available": True,
        "schema_version": FROZEN_SCIENTIFIC_RUNTIME_SCHEMA,
        "fingerprint_sha256": manifest.sha256,
        "dependency_count": len(manifest.payload["distributions"]),
        "reason": None,
    }

    tampered = dict(manifest.payload)
    tampered["native_runtime"] = {**manifest.payload["native_runtime"], "netcdf": "tampered"}
    path.write_text(json.dumps(tampered), encoding="utf-8")
    assert frozen_scientific_runtime_capability(path) == {
        "available": False,
        "reason": "Frozen scientific runtime baseline does not match the sidecar",
    }


def test_frozen_runtime_manifest_rejects_an_install_path() -> None:
    manifest = capture_frozen_scientific_runtime_manifest(
        required_modules=FROZEN_SCIENTIFIC_RUNTIME_MODULES
    )
    malformed = dict(manifest.payload)
    malformed["interpreter"] = {**manifest.payload["interpreter"], "executable": "/private/python"}

    with pytest.raises(ValueError, match="invalid fields"):
        frozen_scientific_runtime_manifest(malformed)


def test_frozen_runtime_manifest_rejects_unknown_distribution_metadata() -> None:
    manifest = capture_frozen_scientific_runtime_manifest(
        required_modules=FROZEN_SCIENTIFIC_RUNTIME_MODULES
    )
    malformed = dict(manifest.payload)
    malformed["distributions"] = [
        {**manifest.payload["distributions"][0], "source_path": "/private/build"},
        *manifest.payload["distributions"][1:],
    ]

    with pytest.raises(ValueError, match="distribution is invalid"):
        frozen_scientific_runtime_manifest(malformed)
