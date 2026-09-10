"""Unified downloader completeness and original-source subsetting; no live network or keys."""
import copy
import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest


def _refresh_in_process(control, manifest, key):
    import download_all as module
    import download_data as data
    data.write_json(control / f"{key}.report.json", {
        "complete": True, "group_sha256": data.fingerprint(manifest["groups"][key]),
    })
    for _ in range(10):
        module.refresh_summary(control, manifest)

import download_all as all_data
import download_data as down
import ncei_oisst as ncei


def test_all_non_cmoms_tasks_have_only_implemented_groups():
    m = all_data.load_manifest()
    assert len(m["tasks"]) == 15 and len(m["groups"]) == 6
    assert {g["adapter"] for g in m["groups"].values()} == {"cmems", "era5", "erddap", "ncei"}
    assert not set(m["groups"]) & {"G1", "G3", "I2", "S4"}
    assert not all_data.coverage(m, {})["all_numerical_inputs_complete"]
    reports = {key: {"complete": True, "group_sha256": down.fingerprint(g)} for key, g in m["groups"].items()}
    assert all_data.coverage(m, reports)["all_numerical_inputs_complete"]
    reports["P_GULF"]["complete"] = False
    status = all_data.coverage(m, reports)
    assert not status["tasks"]["Q30"]["numerical_inputs_complete"]
    assert status["tasks"]["Q21"]["numerical_inputs_complete"]
    reports["P_MODIS"]["group_sha256"] = "old"
    assert not all_data.coverage(m, reports)["tasks"]["Q21"]["numerical_inputs_complete"]


def test_service_depth_halo_baselines_and_no_duplicate_bindings():
    m = all_data.load_manifest()
    gulf = all_data.group_plan(m["groups"]["P_GULF"])
    assert len(gulf) == 7 * 12 * 3
    assert len({c["relative_path"] for c in gulf}) == len(gulf)
    assert all(c["relative_path"].startswith("CMEMS_Gulf/") for c in gulf)
    assert all(("maximum_depth" not in c["request"]) == (c["variables"] == ["zos"]) for c in gulf)
    ecs = all_data.group_plan(m["groups"]["P_ECS"])
    assert {int(c["period"][:4]) for c in ecs} == {*range(1993, 2012), 2023}
    assert {int(c["period"][5:]) for c in ecs} == set(range(5, 12))
    assert all(c["bbox"] == [119, 129, 24, 35] for c in ecs)
    for b in all_data.bindings(m).values():
        assert len(b["datasets"]) == len(set(b["datasets"]))
        assert all(len(Path(p).parts) == 2 for p in b["datasets"])


def test_ncei_plan_all_days_and_shared_download(tmp_path, monkeypatch):
    group = copy.deepcopy(all_data.load_manifest()["groups"]["P_OISST"])
    group.update(start="2020-02-28", end="2020-03-01", bbox=[120, 120.5, 25, 25.5])
    chunks = ncei.plan(group)
    assert len(chunks) == 6 and chunks[2]["period"] == "2020-02-29"
    assert "202002/oisst-avhrr-v02r01.20200229.nc" in chunks[2]["url"]
    native = tmp_path / "native.nc"
    with netCDF4.Dataset(native, "w") as ds:
        for name, vals in [("time", [0]), ("zlev", [0]), ("lat", [24.875, 25.125, 25.375, 25.625]), ("lon", [119.875, 120.125, 120.375, 120.625])]:
            ds.createDimension(name, len(vals))
            v = ds.createVariable(name, "f8", (name,))
            v[:] = vals
            v.units = "days since 2020-02-28 12:00:00" if name == "time" else "degrees"
        for name in ["sst", "ice"]:
            v = ds.createVariable(name, "i2", ("time", "zlev", "lat", "lon"), fill_value=-999)
            v.units = "degree_C" if name == "sst" else "%"
            v.scale_factor = 0.01
            v.add_offset = 0.0
            v[:] = np.full((1, 1, 4, 4), 25.3)
    archive = tmp_path / "archive"
    archive.mkdir()
    calls = []
    def fetch(url, destination, timeout):
        calls.append(url)
        destination.write_bytes(native.read_bytes())
    monkeypatch.setattr(ncei, "curl", fetch)
    records = []
    ncei.execute(chunks[:2], archive, records.append)
    assert len(calls) == 1 and len(records) == 2
    for chunk in chunks[:2]:
        path = archive / chunk["relative_path"]
        assert ncei.verify(path, chunk) == {chunk["variables"][0]: 4}
        with netCDF4.Dataset(path) as ds:
            v = ds.variables[chunk["variables"][0]]
            assert np.allclose(v[:], 25.3)
            v.set_auto_maskandscale(False)
            assert np.all(v[:] == 2530)  # packed data not accidentally scaled twice
    ncei.execute(chunks[:2], archive, records.append)
    assert len(calls) == 1
    assert all_data.verify_existing(chunks[:2], archive) is None
    (archive / chunks[0]["relative_path"]).write_bytes(b"corrupt")
    with pytest.raises(down.DownloadError):
        all_data.verify_existing(chunks[:2], archive)


def test_adapted_rubrics_do_not_require_private_paper_panels():
    for i in range(13, 23):
        ref = json.loads((all_data.HERE.parent / f"tasks/Q{i}/target_study/checklist.json").read_text())
        assert "adapt" in ref["reference_text"]
        assert all(v["requirement"] == "context_only" for c in ref["criteria"] for v in c.get("visual_checks", []))


def test_failed_group_is_not_complete_or_secret_logged(tmp_path, monkeypatch):
    m = {"version": "test", "groups": {"g": {"phase": "services", "adapter": "cmems", "data_type": "CMEMS", "folders": ["thetao"]}}, "tasks": {"Q13": ["g"]}, "masks": {}}
    monkeypatch.setattr(all_data, "load_manifest", lambda: m)
    def fail(group):
        raise RuntimeError("secret=NEVER-LOG-THIS")
    monkeypatch.setattr(all_data, "group_plan", fail)
    assert all_data.main(["services", "--output", str(tmp_path), "--execute"]) == 1
    report = (tmp_path / "_download_all/g.report.json").read_text()
    assert "NEVER-LOG" not in report
    assert not json.loads((tmp_path / "_download_all/coverage.json").read_text())["all_numerical_inputs_complete"]


def test_two_phases_accumulate_coverage_and_verify_detects_missing(tmp_path, monkeypatch):
    groups = {
        "p": {"phase": "public", "adapter": "erddap", "data_type": "MODIS_Aqua", "folders": ["chlorophyll"]},
        "s": {"phase": "services", "adapter": "cmems", "data_type": "CMEMS", "folders": ["thetao"]},
    }
    m = {"version": "test", "groups": groups, "tasks": {"Q21": ["p"], "Q24": ["p", "s"]}, "masks": {}}
    monkeypatch.setattr(all_data, "load_manifest", lambda: m)
    monkeypatch.setattr(all_data, "group_plan", lambda g: [{"relative_path": g["data_type"] + "/fixture.nc"}])
    monkeypatch.setattr(down, "transfer", lambda chunk, root, **kw: {"path": str(root / chunk["relative_path"])})
    monkeypatch.setattr(all_data.services, "transfer", down.transfer)
    assert all_data.main(["public", "--output", str(tmp_path), "--execute"]) == 0
    path = tmp_path / "_download_all/coverage.json"
    assert not json.loads(path.read_text())["all_numerical_inputs_complete"]

    assert all_data.main(["services", "--output", str(tmp_path), "--execute"]) == 0
    assert json.loads(path.read_text())["all_numerical_inputs_complete"]
    def missing(chunks, root):
        raise down.DownloadError("Missing fixture")
    monkeypatch.setattr(all_data, "verify_existing", missing)
    assert all_data.main(["verify", "--output", str(tmp_path)]) == 1
    assert not json.loads(path.read_text())["all_numerical_inputs_complete"]


def test_parallel_service_resume_and_failure_preserve_other_files(tmp_path):
    chunks = all_data.group_plan(all_data.load_manifest()["groups"]["P_ERA5"])[:3]
    for chunk in chunks:
        final = down.safe_destination(tmp_path, chunk["relative_path"])
        down.ensure_collection(final, chunk)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"preserved verified download fixture")
        down.write_json(final.with_suffix(".receipt.json"), {
            "request_sha256": chunk["request_sha256"], "sha256": down.file_hash(final),
        })
    records = list(all_data.services.execute_chunks(chunks, tmp_path, workers=2))
    assert len(records) == 3
    assert all(item["state"] == "verified_existing" for item in records)
    damaged = tmp_path / chunks[0]["relative_path"]
    damaged.write_bytes(b"corrupt")
    records = list(all_data.services.execute_chunks(chunks, tmp_path, workers=2))
    assert sum("error_type" in item for item in records) == 1
    assert sum(item.get("state") == "verified_existing" for item in records) == 2
    assert damaged.read_bytes() == b"corrupt"  # No replacement or network redownload.


def test_service_workers_and_duplicate_destinations_are_validated(tmp_path):
    with pytest.raises(SystemExit):
        all_data.main(["services", "--output", str(tmp_path), "--workers", "0"])
    with pytest.raises(down.DownloadError, match="Duplicate"):
        list(all_data.services.execute_chunks([{"relative_path": "same.nc"}] * 2, tmp_path))


def test_phase_locks_allow_different_phases_but_exclude_duplicates_and_verify(tmp_path):
    import fcntl
    with all_data.phase_lock(tmp_path, "public"):
        with all_data.phase_lock(tmp_path, "services"):
            for phase in ["public", "services", "verify"]:
                with pytest.raises(down.DownloadError, match="lock busy"):
                    with all_data.phase_lock(tmp_path, phase):
                        pytest.fail("conflicting phase admitted")
            with (tmp_path / '.download.lock').open('a') as old:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(old, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with all_data.phase_lock(tmp_path, "verify"):
        with pytest.raises(down.DownloadError):
            with all_data.phase_lock(tmp_path, "public"):
                pytest.fail("download during verification")
    with (tmp_path / '.download.lock').open('a') as old:
        fcntl.flock(old, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(down.DownloadError):
            with all_data.phase_lock(tmp_path, "services"):
                pytest.fail("download during legacy writer")


def test_shared_archive_lock_uses_read_write_descriptor(tmp_path, monkeypatch):
    import fcntl
    import os
    original = fcntl.flock
    def network_flock(file, operation):
        if operation & fcntl.LOCK_SH:
            mode = fcntl.fcntl(file.fileno(), fcntl.F_GETFL) & os.O_ACCMODE
            assert mode == os.O_RDWR  # Required for shared/exclusive locking on NFS.
        return original(file, operation)
    monkeypatch.setattr(fcntl, 'flock', network_flock)
    with all_data.phase_lock(tmp_path, 'public'):
        pass


def test_concurrent_summary_writers_retain_both_phase_results(tmp_path):
    from multiprocessing import get_context
    manifest = all_data.load_manifest()
    keys = ['P_MODIS', 'P_GULF']
    ctx = get_context('spawn')
    processes = [ctx.Process(target=_refresh_in_process, args=(tmp_path, manifest, key)) for key in keys]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join()
            pytest.fail('summary writers hung')
        assert process.exitcode == 0
    result = json.loads((tmp_path / 'coverage.json').read_text())
    assert result['tasks']['Q19']['numerical_inputs_complete']
    assert result['tasks']['Q13']['numerical_inputs_complete']
    assert not result['tasks']['Q24']['numerical_inputs_complete']
