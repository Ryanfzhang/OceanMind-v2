from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from oceanx.scientific_runtime import (
    FROZEN_SCIENTIFIC_RUNTIME_MODULES,
    capture_frozen_scientific_runtime_manifest,
)


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_desktop_sidecar.py"
    specification = importlib.util.spec_from_file_location("build_desktop_sidecar", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_frozen_sidecar_baseline_is_generated_by_the_final_executable(monkeypatch, tmp_path: Path) -> None:
    module = _module()
    manifest = capture_frozen_scientific_runtime_manifest(
        required_modules=FROZEN_SCIENTIFIC_RUNTIME_MODULES
    )
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps(manifest.payload), stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    executable = tmp_path / "ocean-backend"
    executable.write_text("binary", encoding="utf-8")
    output = tmp_path / "ocean-scientific-runtime.json"

    module._write_frozen_runtime_baseline(executable, output)

    assert captured["command"] == [str(executable), "scientific-runtime"]
    assert captured["kwargs"]["timeout"] == module.FROZEN_RUNTIME_PROBE_TIMEOUT_SECONDS
    assert json.loads(output.read_text(encoding="utf-8")) == manifest.payload


def test_invalid_frozen_sidecar_baseline_does_not_replace_existing_file(monkeypatch, tmp_path: Path) -> None:
    module = _module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="{}", stderr=""),
    )
    executable = tmp_path / "ocean-backend"
    executable.write_text("binary", encoding="utf-8")
    output = tmp_path / "ocean-scientific-runtime.json"
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(RuntimeError, match="incompatible"):
        module._write_frozen_runtime_baseline(executable, output)

    assert output.read_text(encoding="utf-8") == "existing"
