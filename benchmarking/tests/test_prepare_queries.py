"""Shared filesystem to the real OceanX query schema; no model or download calls."""
import json
from pathlib import Path

import pytest

import download_data as download
import prepare_queries as prepare
from oceanx.batch import load_queries


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "shared"
    control = root / "_download_all"
    control.mkdir(parents=True)
    manifest = json.loads(prepare.MANIFEST.read_text())
    for key, group in manifest["groups"].items():
        # Path-only fixtures; this test makes no scientific-data validity claim.
        for folder in group["folders"]:
            target = root / group["data_type"] / folder / "2011" / "test.nc"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"path fixture")
        download.write_json(control / f"{key}.report.json", {
            "group_sha256": download.fingerprint(group), "complete": True,
            "completed_files": 1, "expected_files": 1,
        })
    download.write_json(control / "coverage.json", {
        "catalogue": manifest["version"],
        "tasks": {task: {"numerical_inputs_complete": True, "missing_groups": []} for task in manifest["tasks"]},
    })
    return root


def test_autoresearch_preset_produces_12_loadable_cases_without_data_copy(archive, tmp_path):
    output = tmp_path / "inputs/autoresearch.jsonl"
    before = sorted(p.relative_to(archive) for p in archive.rglob("*.nc"))
    assert prepare.main(["--data-root", str(archive), "--preset", "autoresearch", "--output", str(output)]) == 0
    cases = load_queries(output)
    assert [case.id for case in cases] == [f"Q{i}" for i in range(13, 25)]
    for case in cases:
        original = json.loads((prepare.TASKS / case.id / "task_info.json").read_text())
        assert case.query == original["query"]
        assert all(p.is_dir() and p.is_relative_to(archive.resolve()) for p in case.datasets)
    assert sorted(p.relative_to(archive) for p in archive.rglob("*.nc")) == before
    assert not list(output.parent.rglob("*.nc"))


@pytest.mark.parametrize("fault", ["old_version", "incomplete", "missing_report", "empty_data"])
def test_preset_rejects_bad_archive_before_writing(archive, tmp_path, fault):
    control = archive / "_download_all"
    if fault == "old_version":
        doc = json.loads((control / "coverage.json").read_text())
        doc["catalogue"] = "old"
        download.write_json(control / "coverage.json", doc)
    elif fault == "incomplete":
        doc = json.loads((control / "P_MODIS.report.json").read_text())
        doc["complete"] = False
        download.write_json(control / "P_MODIS.report.json", doc)
    elif fault == "missing_report":
        (control / "P_MODIS.report.json").unlink()
    else:
        (archive / "MODIS_Aqua/chlorophyll/2011/test.nc").unlink()
    output = tmp_path / "blocked.jsonl"
    with pytest.raises((ValueError, OSError)):
        prepare.main(["--data-root", str(archive), "--preset", "autoresearch", "--output", str(output)])
    assert not output.exists()


def test_non_cmoms_preset_requires_all_readings(archive, tmp_path):
    with pytest.raises(ValueError, match="Q28.*full-text"):
        prepare.preset_inputs("non-cmoms", archive)
    bindings = {}
    for task, count in [("Q28", 2), ("Q29", 3), ("Q30", 3)]:
        papers = []
        for index in range(count):
            path = archive / "Papers" / task / f"{index}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF-1.7\nTest only, not a scientific full text")
            papers.append(str(path.relative_to(archive)))
        bindings[task] = {"papers": papers}
    ids, bound = prepare.preset_inputs("non-cmoms", archive, bindings)
    assert len(ids) == 15 and len(bound["Q30"]["papers"]) == 3
    (archive / bindings["Q28"]["papers"][0]).write_bytes(b"<html>login</html>")
    with pytest.raises(ValueError, match="not a PDF"):
        prepare.preset_inputs("non-cmoms", archive, bindings)
