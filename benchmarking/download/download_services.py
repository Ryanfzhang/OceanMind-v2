#!/usr/bin/env python3
"""Download CMEMS subsets or ERA5 hourly fields into the shared variable/year archive.

Explicit product/version/region selections only. No OceanX runtime or LLM calls.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
from pathlib import Path
import tempfile

from download_data import DownloadError, archive_path, check_bbox, fingerprint, transfer, write_json


ERA5_VARIABLES = {
    "ssr": "surface_net_solar_radiation",
    "str": "surface_net_thermal_radiation",
    "sshf": "surface_sensible_heat_flux",
    "slhf": "surface_latent_heat_flux",
}


def build_plan(source, variables, years, months, bbox, dataset_id=None, version=None, depth=None):
    west, east, south, north = check_bbox(bbox)
    if source not in {"cmems", "era5"}:
        raise DownloadError("Unknown service")
    if not years or not months or not variables or len(set(variables)) != len(variables):
        raise DownloadError("Provide years, months and unique variables")
    if any(y < 1900 or y > 2200 for y in years) or any(m not in range(1, 13) for m in months):
        raise DownloadError("Invalid calendar year/month")
    if source == "cmems" and (not dataset_id or not version):
        raise DownloadError("CMEMS requires an explicit dataset ID and frozen dataset version from its catalogue")
    if depth and (source != "cmems" or not 0 <= depth[0] <= depth[1]):
        raise DownloadError("Depth must be an ordered nonnegative CMEMS range")
    if source == "era5" and set(variables) - ERA5_VARIABLES.keys():
        raise DownloadError("ERA5 supports the four benchmark flux fields: ssr str sshf slhf")
    chunks = []
    for year in sorted(set(years)):
        for month in sorted(set(months)):
            days = calendar.monthrange(year, month)[1]
            period = f"{year:04}-{month:02}"
            for variable in variables:
                if source == "cmems":
                    request = dict(dataset_id=dataset_id, dataset_version=version, variables=[variable],
                                   minimum_longitude=west, maximum_longitude=east,
                                   minimum_latitude=south, maximum_latitude=north,
                                   start_datetime=f"{period}-01T00:00:00",
                                   end_datetime=f"{period}-{days:02}T23:59:59",
                                   coordinates_selection_method="inside", file_format="netcdf",
                                   raise_if_updating=True)
                    if depth:
                        request.update(minimum_depth=depth[0], maximum_depth=depth[1])
                    dataset = dataset_id
                else:
                    request = {"product_type": ["reanalysis"], "variable": [ERA5_VARIABLES[variable]],
                               "year": [str(year)], "month": [f"{month:02}"],
                               "day": [f"{d:02}" for d in range(1, days + 1)],
                               "time": [f"{h:02}:00" for h in range(24)],
                               "area": [north, west, south, east], "data_format": "netcdf", "download_format": "unarchived"}
                    dataset = "reanalysis-era5-single-levels"
                chunk = {"service": source, "dataset": dataset, "request": request,
                         "period": period, "variables": [variable], "bbox": bbox,
                         "url": f"{source}:{dataset}", "version_label": version if source == "cmems" else "ERA5 reanalysis, retrieval timestamp in receipt",
                         "expected_samples": days * (24 if source == "era5" else 1)}
                chunk["request_sha256"] = fingerprint(chunk)
                job = {"id": "cmems_daily" if source == "cmems" else "era5_hourly",
                       "data_type": "CMEMS" if source == "cmems" else "ERA5"}
                chunk["relative_path"] = archive_path(job, variable, period, chunk["request_sha256"])
                chunks.append(chunk)
    return chunks


def verify_service_file(path, chunk):
    import netCDF4
    import numpy as np
    try:
        with netCDF4.Dataset(path) as ds:
            time_name = "time" if "time" in ds.variables else "valid_time"
            time = ds.variables[time_name]
            values = time[:]
            if values.ndim != 1 or np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
                raise DownloadError("Invalid time coordinate")
            if len(values) != chunk["expected_samples"] or np.any(np.diff(values) <= 0):
                raise DownloadError("Missing/duplicate/nonmonotonic daily or hourly samples")
            dates = netCDF4.num2date(values, time.units, calendar=getattr(time, "calendar", "standard"))
            if any(d.strftime("%Y-%m") != chunk["period"] for d in dates):
                raise DownloadError("Wrong month returned")
            seconds = 3600 if chunk["service"] == "era5" else 86400
            if any((b-a).total_seconds() != seconds for a, b in zip(dates, dates[1:])):
                raise DownloadError("Irregular time coverage")
            if chunk["service"] == "era5" and (dates[0].day != 1 or dates[0].hour != 0):
                raise DownloadError("ERA5 must contain all UTC hours")
            for coord, bounds in [("longitude", chunk["bbox"][:2]), ("latitude", chunk["bbox"][2:])]:
                raw = ds.variables[coord][:]
                if np.ma.getmaskarray(raw).any():
                    raise DownloadError(f"Masked spatial coordinate: {coord}")
                x = np.asarray(raw)
                if coord == "longitude":
                    x = (x + 180) % 360 - 180
                if x.size == 0 or not np.isfinite(x).all() or x.min() < bounds[0]-1e-5 or x.max() > bounds[1]+1e-5:
                    raise DownloadError(f"Wrong spatial coverage: {coord}")
                if "expected_grid_step" in chunk:
                    step = chunk["expected_grid_step"]
                    ordered = np.sort(x)
                    if (x.ndim != 1 or x.min() - bounds[0] > step + 2e-5
                            or bounds[1] - x.max() > step + 2e-5
                            or (x.size > 1 and not np.allclose(np.diff(ordered), step, atol=2e-5))):
                        raise DownloadError(f"Incomplete or unexpected native grid: {coord}")
            if "minimum_depth" in chunk["request"]:
                depth = ds.variables["depth"][:]
                if (not depth.size or np.ma.getmaskarray(depth).any() or not np.isfinite(depth).all()
                        or depth.min() < chunk["request"]["minimum_depth"] - 1e-5
                        or depth.max() > chunk["request"]["maximum_depth"] + 1e-5):
                    raise DownloadError("Wrong depth coverage")
            counts = {}
            for name in chunk["variables"]:
                variable = ds.variables[name]
                if time_name not in variable.dimensions or not getattr(variable, "units", ""):
                    raise DownloadError(f"Variable lacks time dimension/units: {name}")
                count = 0
                # Read one time slice at a time; do not load a year of 3-D fields into RAM.
                axis = variable.dimensions.index(time_name)
                for index in range(len(values)):
                    selection = [slice(None)] * variable.ndim
                    selection[axis] = index
                    data = variable[tuple(selection)]
                    count += int(np.ma.filled(np.isfinite(data) & ~np.ma.getmaskarray(data), False).sum())
                counts[name] = count
            return counts
    except (KeyError, ValueError, OSError, RuntimeError, AttributeError) as exc:
        raise DownloadError(f"Invalid service NetCDF: {exc}") from exc


def fetch_service(chunk, destination):
    # Keep credentials in provider-managed files/environment, never in the plan.
    with tempfile.TemporaryDirectory(prefix="ocean-download-", dir=destination.parent) as staging:
        staged = Path(staging) / "payload.nc"
        if chunk["service"] == "cmems":
            import copernicusmarine
            copernicusmarine.subset(**chunk["request"], output_directory=staging, output_filename=staged.name)
        else:
            import cdsapi
            cdsapi.Client().retrieve(chunk["dataset"], chunk["request"], str(staged))
        if not staged.is_file():
            raise DownloadError("Provider did not produce the requested NetCDF file")
        staged.replace(destination)


def positive_workers(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("workers must be at least 1")
    return value


def _transfer_service(job):
    chunk, root = job
    try:
        return transfer(chunk, root,
                        runner=lambda url, destination, timeout: fetch_service(chunk, destination),
                        verifier=verify_service_file)
    except Exception as exc:  # noqa: BLE001 - isolate provider failures and redact exception details.
        # Provider exceptions may contain credentials; return only their type.
        return {"path": str(root / chunk["relative_path"]), "error_type": type(exc).__name__}


def execute_chunks(chunks, root, workers=2):
    """Bound in-flight monthly requests; emit results to one parent report writer.

    Separate spawned processes keep NetCDF/HDF5 and provider-client state isolated.
    Existing file receipts are still verified by transfer before any API submission.
    """
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    from itertools import islice
    from multiprocessing import get_context

    from download_data import ensure_collection, safe_destination

    if workers < 1:
        raise ValueError("workers must be at least 1")
    paths = [chunk["relative_path"] for chunk in chunks]
    if len(paths) != len(set(paths)):
        raise DownloadError("Duplicate destinations in service plan")
    if workers == 1 or len(chunks) < 2:
        for chunk in chunks:
            yield _transfer_service((chunk, root))
        return
    # Shared collection descriptors must exist before concurrent workers start.
    for chunk in chunks:
        ensure_collection(safe_destination(root, chunk["relative_path"]), chunk)
    jobs = iter((chunk, root) for chunk in chunks)
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        pending = {pool.submit(_transfer_service, job) for job in islice(jobs, workers)}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
                job = next(jobs, None)
                if job is not None:
                    pending.add(pool.submit(_transfer_service, job))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["cmems", "era5"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variables", nargs="+", required=True)
    parser.add_argument("--years", nargs="+", type=int, required=True)
    parser.add_argument("--months", nargs="+", type=int, default=list(range(1, 13)))
    parser.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "E", "S", "N"))
    parser.add_argument("--dataset-id")
    parser.add_argument("--dataset-version")
    parser.add_argument("--depth", nargs=2, type=float, help="CMEMS min/max depth; omit to retain full depth")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--workers", type=positive_workers, default=2,
                        help="Concurrent service chunks (default: 2; use 1 for serial)")
    args = parser.parse_args(argv)
    chunks = build_plan(args.source, args.variables, args.years, args.months, args.bbox,
                        args.dataset_id, args.dataset_version, args.depth)
    root = args.output.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (root / ".download.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DownloadError("Another downloader is using this archive") from exc
        with tempfile.TemporaryDirectory(prefix="plan-", dir=root) as temporary:
            # A unique permanent run directory; only JSON metadata is stored here.
            run = root / "_runs" / Path(temporary).name
            write_json(run / "plan.json", chunks)
        report = {"mode": "execute" if args.execute else "offline_preview", "complete": False,
                  "scope": "requested service fields only, not all benchmark inputs", "files": []}
        report["workers"] = args.workers
        report["errors"] = []
        if args.execute:
            for record in execute_chunks(chunks, root, args.workers):
                report["errors" if "error_type" in record else "files"].append(record)
                state = record.get("error_type", record.get("state", "completed"))
                print(f"{len(report['files'])}/{len(chunks)} {state}: {record['path']}", flush=True)
                write_json(run / "report.json", report)
        else:
            for chunk in chunks:
                print(chunk["relative_path"], flush=True)
        report["complete"] = bool(args.execute) and not report["errors"]
        write_json(run / "report.json", report)
        print(f"Plan/report: {run}. Preview does not verify remote availability. No averaging or flux conversion.")
        if report["errors"]:
            raise DownloadError(f"Some service chunks failed; verified files retained. Report: {run}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DownloadError, ValueError, OSError) as exc:
        raise SystemExit(f"ERROR: {exc}")
