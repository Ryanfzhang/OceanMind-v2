#!/usr/bin/env python3
"""Two-phase, fixed-manifest acquisition of ALL non-CMOMS numerical benchmark inputs."""
import argparse
import copy
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path

import download_data as erddap
import download_services as services
import ncei_oisst

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "public_manifest.json"


def load_manifest():
    manifest = json.loads(MANIFEST.read_text())
    if set(manifest["tasks"]) != {f"Q{i:02}" for i in [*range(13, 25), 28, 29, 30]}:
        raise ValueError("Manifest must cover all 15 non-CMOMS tasks")
    for task, groups in manifest["tasks"].items():
        if not groups or any(group not in manifest["groups"] for group in groups):
            raise ValueError(f"Undefined input group: {task}")
        canonical = json.loads((HERE.parent / "tasks" / task / "task_info.json").read_text())
        if canonical["data_groups"] != groups or canonical["catalog_version"] != manifest["version"]:
            raise ValueError(f"Catalogue/manifest mismatch: {task}")
    return manifest


def group_plan(group, metadata=erddap.get_json):
    if group["adapter"] == "erddap":
        config = erddap.load_config(HERE / "download_config.json")
        job = copy.deepcopy(next(p for p in config["products"] if p["id"] == group["product"]))
        job.update({key: group[key] for key in ["variables", "data_type", "start", "end", "bbox"]})
        result = erddap.plan_product(job, metadata)
        if result["missing_periods"]:
            raise erddap.DownloadError(f"Provider is missing required months: {result['missing_periods']}; no gap-filling or incomplete success")
        return result["chunks"]
    if group["adapter"] == "ncei":
        return ncei_oisst.plan(group)
    chunks = []
    for variable in group["variables"]:
        # Surface-height products have no depth axis, unlike 3-D hydrography.
        depth = group.get("depth") if variable != "zos" else None
        selected = services.build_plan(group["adapter"], [variable], group["years"], group["months"],
                                       group["bbox"], group.get("dataset"), group.get("dataset_version"), depth)
        for chunk in selected:
            chunk["data_type"] = group["data_type"]
            chunk["expected_grid_step"] = 1 / 12 if group["adapter"] == "cmems" else 0.25
            chunk["request_sha256"] = erddap.fingerprint({k: v for k, v in chunk.items() if k not in {"request_sha256", "relative_path"}})
            chunk["relative_path"] = erddap.archive_path({"id": "cmems_daily" if group["adapter"] == "cmems" else "era5_hourly", "data_type": group["data_type"]}, variable, chunk["period"], chunk["request_sha256"])
        chunks.extend(selected)
    return chunks


def verify_existing(chunks, root):
    """Never trust filenames alone. Recheck both request identity and file SHA256."""
    def no_network(url, destination, timeout):
        raise erddap.DownloadError(f"Missing file: {destination}")
    for chunk in chunks:
        erddap.transfer(chunk, root, runner=no_network)


def coverage(manifest, reports):
    ready = {key: bool(report.get("complete") and report.get("group_sha256") == erddap.fingerprint(manifest["groups"][key]))
             for key, report in reports.items() if key in manifest["groups"]}
    tasks = {task: {"numerical_inputs_complete": all(ready.get(g, False) for g in groups),
                     "missing_groups": [g for g in groups if not ready.get(g, False)]}
             for task, groups in manifest["tasks"].items()}
    return {"catalogue": manifest["version"], "all_numerical_inputs_complete": all(t["numerical_inputs_complete"] for t in tasks.values()),
            "tasks": tasks, "scope": "Numerical inputs only. This is not validated reference answers, scientific result correctness, or staged idea-paper full texts."}


def bindings(manifest):
    return {task: {"datasets": list(dict.fromkeys(f"{manifest['groups'][g]['data_type']}/{folder}"
                         for g in groups for folder in manifest["groups"][g]["folders"]))}
            for task, groups in manifest["tasks"].items()}


@contextmanager
def phase_lock(root, phase):
    """Different download phases share the archive; verify/legacy tools exclude both."""
    import fcntl
    with ExitStack() as stack:
        archive = stack.enter_context((root / ".download.lock").open("a"))
        try:
            mode = fcntl.LOCK_EX if phase == "verify" else fcntl.LOCK_SH
            fcntl.flock(archive, mode | fcntl.LOCK_NB)
            if phase != "verify":
                lock = stack.enter_context((root / f".download.{phase}.lock").open("a"))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise erddap.DownloadError(
                f"Download lock busy for {phase}: same phase, verification, or a legacy downloader is running. "
                "Restart legacy downloads with the updated script before running public and services together."
            ) from exc
        yield


def refresh_summary(control, manifest):
    """Serialize shared outputs and rebuild from current reports, never a stale snapshot."""
    import fcntl
    with (control / ".summary.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reports = {}
        for key in manifest["groups"]:
            path = control / f"{key}.report.json"
            if path.exists():
                reports[key] = json.loads(path.read_text())
        result = coverage(manifest, reports)
        erddap.write_json(control / "coverage.json", result)
        erddap.write_json(control / "data_bindings.json", bindings(manifest))
        erddap.write_json(control / "masks.json", manifest["masks"])
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["public", "services", "verify"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Download; otherwise show the fixed scope without network calls")
    parser.add_argument("--workers", type=services.positive_workers, default=2,
                        help="Concurrent CMEMS/ERA5 chunks (default: 2; 1 restores serial). Public/verify stay serial.")
    args = parser.parse_args(argv)
    manifest = load_manifest()
    root = args.output.expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("Choose a dedicated data directory")
    selected = {k: g for k, g in manifest["groups"].items() if args.phase == "verify" or g["phase"] == args.phase}
    if not args.execute and args.phase != "verify":
        for key, group in selected.items():
            print(key, json.dumps(group))
        print("Offline preview only. Add --execute. No data or credentials checked.")
        return 0
    root.mkdir(parents=True, exist_ok=True)
    with phase_lock(root, args.phase):
        control = erddap.safe_destination(root, "_download_all")
        control.mkdir(exist_ok=True)
        failed = False
        for key, group in selected.items():
            report = {"group_sha256": erddap.fingerprint(group), "complete": False, "completed_files": 0,
                      "workers": args.workers if args.phase == "services" else 1, "failed_files": []}
            report_path = control / f"{key}.report.json"
            plan_path = control / f"{key}.plan.json"
            erddap.write_json(report_path, report)
            refresh_summary(control, manifest)
            print(f"{key}: checking/downloading ...", flush=True)
            try:
                if args.phase == "verify":
                    saved = json.loads(plan_path.read_text())
                    if saved["group_sha256"] != report["group_sha256"]:
                        raise ValueError("Saved request plan is from a different manifest")
                    chunks = saved["chunks"]
                    if not chunks or saved["plan_sha256"] != erddap.fingerprint(chunks):
                        raise ValueError("Saved request plan is empty or corrupted")
                else:
                    chunks = group_plan(group)
                    erddap.write_json(plan_path, {"group_sha256": report["group_sha256"], "plan_sha256": erddap.fingerprint(chunks), "chunks": chunks})
                report["expected_files"] = len(chunks)
                def record(result, report=report, key=key, chunks=chunks, report_path=report_path):
                    report["completed_files"] += 1
                    print(f"{key}: {report['completed_files']}/{len(chunks)} {result['path']}", flush=True)
                    erddap.write_json(report_path, report)
                if args.phase == "verify":
                    verify_existing(chunks, root)
                    report["completed_files"] = len(chunks)
                elif group["adapter"] == "ncei":
                    ncei_oisst.execute(chunks, root, record)
                elif group["adapter"] in {"cmems", "era5"}:
                    for result in services.execute_chunks(chunks, root, args.workers):
                        if "error_type" in result:
                            report["failed_files"].append(result)
                            erddap.write_json(report_path, report)
                            print(f"{key}: chunk FAILED ({result['error_type']}) {result['path']}", flush=True)
                        else:
                            record(result)
                    if report["failed_files"]:
                        raise erddap.DownloadError("Some service chunks failed; verified files retained")
                else:
                    for chunk in chunks:
                        record(erddap.transfer(chunk, root))
                report["complete"] = report["completed_files"] == report["expected_files"] and bool(chunks)
            except Exception as exc:
                # Provider exceptions can contain credential-bearing URLs. Do not persist them.
                report["error_type"] = type(exc).__name__
                if group["phase"] == "public":
                    report["error"] = str(exc)
                print(f"{key}: FAILED ({type(exc).__name__}). Check connectivity/account/product availability and rerun; verified files are retained.", flush=True)
                failed = True
            erddap.write_json(report_path, report)
            refresh_summary(control, manifest)
        result = refresh_summary(control, manifest)
        count = sum(t["numerical_inputs_complete"] for t in result["tasks"].values())
        print(f"Numerical input coverage: {count}/15 tasks. Report: {control / 'coverage.json'}")
        return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, erddap.DownloadError) as exc:
        raise SystemExit(f"ERROR: {exc}")
    except KeyboardInterrupt:
        raise SystemExit("Interrupted. Verified files retained; rerun the same command.")
