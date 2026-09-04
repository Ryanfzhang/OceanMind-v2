from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "generate_desktop_release_manifest.py"
    specification = importlib.util.spec_from_file_location("desktop_release_manifest", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _fixture_app(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "Ocean Research Partner.app"
    sidecar = app / "Contents" / "Resources" / "sidecar" / "ocean-backend" / "_internal"
    sidecar.mkdir(parents=True)
    (app / "Contents" / "Resources" / "main.js").write_text("desktop", encoding="utf-8")
    metadata = sidecar / "fixture-1.2.3.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixture\nVersion: 1.2.3\nLicense: MIT\n",
        encoding="utf-8",
    )
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    (desktop / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "desktop"},
                    "node_modules/react": {"version": "18.3.1", "license": "MIT"},
                },
            }
        ),
        encoding="utf-8",
    )
    return app, desktop


def _fixture_windows_app(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "win-unpacked"
    sidecar = app / "resources" / "sidecar" / "ocean-backend" / "_internal"
    sidecar.mkdir(parents=True)
    (app / "Ocean Research Partner.exe").write_bytes(b"desktop")
    metadata = sidecar / "fixture-windows-4.5.6.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixture-windows\nVersion: 4.5.6\nLicense: BSD-3-Clause\n",
        encoding="utf-8",
    )
    desktop = tmp_path / "desktop-windows"
    desktop.mkdir()
    (desktop / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "desktop"},
                    "node_modules/react": {"version": "18.3.1", "license": "MIT"},
                },
            }
        ),
        encoding="utf-8",
    )
    return app, desktop


def test_release_manifest_is_deterministic_and_atomic(tmp_path: Path):
    module = _module()
    app, desktop = _fixture_app(tmp_path)

    first = module.build_release_documents(
        app_path=app,
        desktop_root=desktop,
        application_name="Ocean Research Partner",
        application_version="0.1.0",
    )
    second = module.build_release_documents(
        app_path=app,
        desktop_root=desktop,
        application_name="Ocean Research Partner",
        application_version="0.1.0",
    )
    assert first == second
    assert first["checksums.json"]["bundle"]["files"]
    assert {item["purl"] for item in first["sbom.cdx.json"]["components"]} == {
        "pkg:npm/react@18.3.1",
        "pkg:pypi/fixture@1.2.3",
    }

    output = tmp_path / "release-metadata"
    module.write_release_documents(first, output)
    assert set(path.name for path in output.iterdir()) == {
        "checksums.json",
        "licenses.json",
        "sbom.cdx.json",
    }
    with pytest.raises(module.ReleaseManifestError, match="already exists"):
        module.write_release_documents(first, output)


def test_release_manifest_includes_the_windows_frozen_sidecar_dependencies(tmp_path: Path):
    module = _module()
    app, desktop = _fixture_windows_app(tmp_path)

    documents = module.build_release_documents(
        app_path=app,
        desktop_root=desktop,
        application_name="Ocean Research Partner",
        application_version="0.1.0",
    )

    assert {item["purl"] for item in documents["sbom.cdx.json"]["components"]} == {
        "pkg:npm/react@18.3.1",
        "pkg:pypi/fixture-windows@4.5.6",
    }
    assert documents["licenses.json"]["components"][-1] == {
        "purl": "pkg:pypi/fixture-windows@4.5.6",
        "name": "fixture-windows",
        "version": "4.5.6",
        "licenses": [{"license": {"name": "BSD-3-Clause"}}],
    }


@pytest.mark.parametrize("layout", ("missing", "ambiguous"))
def test_release_manifest_rejects_a_missing_or_ambiguous_frozen_sidecar(
    tmp_path: Path, layout: str
):
    module = _module()
    app = tmp_path / "Ocean Research Partner.app"
    app.mkdir()
    (app / "desktop.bin").write_bytes(b"desktop")
    if layout == "ambiguous":
        (app / "Contents" / "Resources" / "sidecar").mkdir(parents=True)
        (app / "resources" / "sidecar").mkdir(parents=True)
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    (desktop / "package-lock.json").write_text('{"packages": {}}', encoding="utf-8")

    with pytest.raises(module.ReleaseManifestError, match="exactly one frozen sidecar"):
        module.build_release_documents(
            app_path=app,
            desktop_root=desktop,
            application_name="Ocean Research Partner",
            application_version="0.1.0",
        )


def test_release_manifest_rejects_a_symlink_that_escapes_the_bundle(tmp_path: Path):
    module = _module()
    app, _desktop = _fixture_app(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (app / "Contents" / "Resources" / "escape").symlink_to(outside)

    with pytest.raises(module.ReleaseManifestError, match="escapes"):
        module.collect_bundle_inventory(app)
