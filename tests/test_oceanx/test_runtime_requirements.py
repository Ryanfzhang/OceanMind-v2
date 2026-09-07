from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _requirements(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_ocean_runtime_requirements_remain_one_consistent_install_contract() -> None:
    standard_requirements = _requirements(ROOT / "requirements.txt")
    root_requirements = _requirements(ROOT / "requirements-ocean.txt")
    packaged_standard_requirements = _requirements(
        ROOT / "src/oceanx/resources/runtime/requirements.txt"
    )
    packaged_requirements = _requirements(
        ROOT / "src/oceanx/resources/runtime/requirements-ocean.txt"
    )
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_extra = pyproject["project"]["optional-dependencies"]["ocean-runtime"]

    assert standard_requirements == root_requirements
    assert standard_requirements == packaged_standard_requirements
    assert root_requirements == packaged_requirements
    assert root_requirements == runtime_extra
