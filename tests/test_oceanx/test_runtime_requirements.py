import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_dependencies_match_packaged_manifest_and_two_install_entries():
    project = tomllib.loads((ROOT/'pyproject.toml').read_text())
    runtime = project['project']['optional-dependencies']['ocean-runtime']
    assert json.loads((ROOT/'src/oceanx/resources/runtime/dependencies.json').read_text()) == runtime
    assert '-e .[ocean-runtime]' in (ROOT/'requirements.txt').read_text()
    assert '-r ../requirements.txt' in (ROOT/'benchmarking/requirements.txt').read_text()
    for old in ['requirements-ocean.txt', 'benchmarking/download/requirements.txt',
                'benchmarking/server/requirements.txt', 'src/oceanx/resources/runtime/requirements.txt',
                'src/oceanx/resources/runtime/requirements-ocean.txt']:
        assert not (ROOT/old).exists()
