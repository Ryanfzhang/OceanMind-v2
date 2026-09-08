#!/usr/bin/env python3
"""Standalone, GET-only ERDDAP subset downloader. No OceanMind runtime imports."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from urllib.parse import quote, urlsplit


class DownloadError(Exception):
    pass


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def path_component(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise DownloadError(f"Unsafe archive path component: {value!r}")
    return value


def archive_path(job, variable, period, request_hash):
    """One scientific variable per file, native cadence grouped by calendar year."""
    source = path_component(job.get("data_type", job["id"]))
    folder = path_component(job.get("variable_folders", {}).get(variable, variable))
    product = path_component(job["id"])
    return f"{source}/{folder}/{period[:4]}/{product}_{period}_{request_hash[:16]}.nc"


def safe_destination(output, relative_path):
    root = output.resolve()
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise DownloadError("Archive paths must be relative and contained")
    destination = root / rel
    for part in [destination, *destination.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise DownloadError(f"Symlink in output path: {part}")
    if not destination.resolve().is_relative_to(root):
        raise DownloadError("Archive path escapes output root")
    return destination


def ensure_collection(final, chunk):
    """A variable directory is one product/grid/release, not a mixture of subsets."""
    folder = final.parent.parent
    descriptor = folder / "_collection.json"
    if descriptor.is_symlink() or descriptor.with_name(descriptor.name + ".tmp").is_symlink():
        raise DownloadError("Symlink in collection descriptor")
    request = {k: v for k, v in chunk.get("request", {}).items()
               if k not in {"start_datetime", "end_datetime", "year", "month", "day", "time"}}
    identity = {"dataset": chunk["dataset"], "version": chunk["version_label"],
                "processing_version": chunk.get("provider_processing_version"),
                "variables": chunk["variables"], "provider": chunk.get("collection_provider", chunk["url"].split("?")[0]),
                "request": request, "bbox": chunk.get("bbox"),
                "grid": {k: v for k, v in chunk.get("expected", {}).items() if k != "time"}}
    if descriptor.exists():
        if json.loads(descriptor.read_text()).get("identity") != identity:
            raise DownloadError(f"Different product/version/grid already occupies {folder}; choose a separate archive root")
    else:
        if folder.exists() and any(folder.glob("*/*.nc")):
            raise DownloadError(f"Unmanaged NetCDF files already occupy {folder}; use an empty archive location")
        write_json(descriptor, {"identity": identity, "layout": "type/variable/year/native-period.nc"})
    # Index shifts at the provider must not create two files for the same period.
    prefix = final.name.rsplit("_", 1)[0]
    if any(p != final for p in final.parent.glob(prefix + "_*.nc")):
        raise DownloadError(f"Another request already occupies period {chunk['period']}; inspect it before downloading")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def curl(url, destination=None, timeout=180):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise DownloadError("Only credential-free HTTPS source URLs are accepted")
    command = ["curl", "--disable", "--fail", "--silent", "--show-error", "--location",
               "--globoff", "--proto", "=https", "--proto-redir", "=https",
               "--connect-timeout", "20", "--max-time", str(timeout),
               "--retry", "3", "--retry-delay", "2", "--retry-connrefused"]
    if destination is not None:
        command += ["--output", str(destination)]
    result = subprocess.run(command + [url], capture_output=True)
    if result.returncode:
        raise DownloadError(f"curl exited {result.returncode}: {result.stderr.decode(errors='replace')[-1200:]}")
    return result.stdout


def get_json(url):
    try:
        return json.loads(curl(url))
    except (ValueError, UnicodeError) as exc:
        raise DownloadError("Provider returned non-JSON metadata (possibly an error/login page)") from exc


def query_url(server, dataset, extension, query):
    return f"{server.rstrip('/')}/griddap/{dataset}.{extension}?{quote(query, safe=',:')}"


def date_value(value):
    # ERDDAP .json time coordinates use ISO timestamps with columnUnits='UTC'.
    if not isinstance(value, str):
        raise DownloadError("Expected an ISO UTC time axis; refusing to guess numeric time units")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def period_keys(start, end, cadence):
    fmt = "%Y-%m" if cadence == "month" else "%Y-%m-%d"
    first, last = dt.datetime.strptime(start, fmt), dt.datetime.strptime(end, fmt)
    if first.strftime(fmt) != start or last.strftime(fmt) != end or first > last:
        raise DownloadError("Invalid or reversed start/end dates")
    result = []
    while first <= last:
        result.append(first.strftime(fmt))
        if cadence == "month":
            first = dt.datetime(first.year + (first.month == 12), first.month % 12 + 1, 1)
        else:
            first += dt.timedelta(days=1)
    return result


def check_bbox(bbox):
    if bbox is None or len(bbox) != 4:
        raise DownloadError("Explicit bbox required: WEST EAST SOUTH NORTH (MODIS: --modis-bbox)")
    west, east, south, north = map(float, bbox)
    if not all(math.isfinite(v) for v in (west, east, south, north)):
        raise DownloadError("Non-finite bbox")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise DownloadError("Use a non-dateline-crossing bbox in -180..180 / -90..90")
    return west, east, south, north


def axis_slice(values, low, high, longitude=False):
    actual = [float(v) for v in values]
    comparable = [((v + 180) % 360 - 180) for v in actual] if longitude else actual
    indices = [i for i, v in enumerate(comparable) if low <= v <= high]
    if not indices:
        raise DownloadError("No coordinate centres inside requested bbox; enlarge it")
    if indices != list(range(indices[0], indices[-1] + 1)):
        raise DownloadError("Selection crosses a longitude seam; split the request explicitly")
    return indices[0], indices[-1]


def plan_product(job, fetch=get_json):
    west, east, south, north = check_bbox(job.get("bbox"))
    requested = period_keys(job["start"], job["end"], job["cadence"])
    server, dataset = job["server"], job["dataset"]
    info = fetch(f"{server}/info/{dataset}/index.json")
    variables = {r[1]: [d.strip() for d in r[4].split(",")]
                 for r in info["table"]["rows"] if r[0] == "variable"}
    attributes = {r[2]: r[4] for r in info["table"]["rows"]
                  if r[0] == "attribute" and r[1] == "NC_GLOBAL"}
    dims = None
    for name in job["variables"]:
        if name not in variables:
            raise DownloadError(f"Provider lacks requested variable {name}")
        if dims is not None and dims != variables[name]:
            raise DownloadError("Requested variables have different dimension orders")
        dims = variables[name]
    if not dims or not {"time", "latitude", "longitude"}.issubset(dims):
        raise DownloadError("This downloader requires time/latitude/longitude dimensions")
    axes = {}
    selections = {}
    for dim in dims:
        table = fetch(query_url(server, dataset, "json", dim))["table"]
        if table["columnNames"] != [dim]:
            raise DownloadError(f"Unexpected coordinate response for {dim}")
        axes[dim] = [r[0] for r in table["rows"]]
        if dim == "time":
            if table["columnUnits"] != ["UTC"]:
                raise DownloadError("Unsupported time-axis units")
        elif dim == "latitude":
            selections[dim] = axis_slice(axes[dim], south, north)
        elif dim == "longitude":
            selections[dim] = axis_slice(axes[dim], west, east, longitude=True)
        else:
            target = job.get("axis_values", {}).get(dim)
            if target is None:
                raise DownloadError(f"Explicit axis_values required for extra dimension {dim}")
            matches = [i for i, v in enumerate(axes[dim]) if math.isclose(float(v), float(target), abs_tol=1e-6)]
            if len(matches) != 1:
                raise DownloadError(f"Extra axis value absent/ambiguous: {dim}={target}")
            selections[dim] = (matches[0], matches[0])
    fmt = "%Y-%m" if job["cadence"] == "month" else "%Y-%m-%d"
    available = {}
    for i, value in enumerate(axes["time"]):
        key = date_value(value).strftime(fmt)
        if key in requested:
            if key in available:
                raise DownloadError(f"Multiple observations in {key}; wrong cadence or ambiguous product")
            available[key] = i
    chunks = []
    for key in requested:
        if key not in available:
            continue
        selected = dict(selections, time=(available[key], available[key]))
        suffix = "".join(f"[{selected[d][0]}:1:{selected[d][1]}]" for d in dims)
        expected = {d: {"count": b-a+1, "first": axes[d][a], "last": axes[d][b]}
                    for d, (a, b) in selected.items()}
        for name in job["variables"]:
            path_component(name)
            chunk = {"period": key, "url": query_url(server, dataset, "nc", name + suffix),
                     "variables": [name], "dimensions": dims, "expected": expected,
                     "dataset": dataset, "version_label": job["version_label"],
                     "provider_processing_version": attributes.get("processing_version", attributes.get("product_version"))}
            chunk["request_sha256"] = fingerprint(chunk)
            chunk["relative_path"] = archive_path(job, name, key, chunk["request_sha256"])
            chunks.append(chunk)
    return {"product": job["id"], "job": job, "provider_metadata": info,
            "coordinate_snapshot": axes, "chunks": chunks,
            "missing_periods": [key for key in requested if key not in available],
            "requested_periods": len(requested),
            "available_periods": len(available),
            "requested_files": len(requested) * len(job["variables"])}


def verify_netcdf(path, chunk):
    import netCDF4
    import numpy as np

    counts = {}
    try:
        with netCDF4.Dataset(path) as ds:
            for dim, expected in chunk["expected"].items():
                if dim not in ds.variables or len(ds.dimensions[dim]) != expected["count"]:
                    raise DownloadError(f"Coordinate missing/wrong length: {dim}")
                coordinate = ds.variables[dim]
                endpoints = coordinate[[0, -1]]
                if dim == "time":
                    times = netCDF4.num2date(endpoints, coordinate.units,
                                            calendar=getattr(coordinate, "calendar", "standard"))
                    actual = [t.strftime("%Y-%m-%dT%H:%M:%S") for t in times]
                    wanted = [date_value(expected[k]).isoformat() for k in ("first", "last")]
                    if actual != wanted:
                        raise DownloadError("Downloaded time does not match requested observation")
                elif not np.allclose(endpoints, [expected["first"], expected["last"]], atol=1e-5, rtol=1e-6):
                    raise DownloadError(f"Downloaded coordinate endpoints differ: {dim}")
            for name in chunk["variables"]:
                if name not in ds.variables or list(ds.variables[name].dimensions) != chunk["dimensions"]:
                    raise DownloadError(f"Missing variable or wrong dimensions: {name}")
                values = ds.variables[name][:]  # One time slice only; forces full payload/decompression validation.
                valid = np.isfinite(values) & ~np.ma.getmaskarray(values)
                counts[name] = int(np.ma.filled(valid, False).sum())
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as exc:
        raise DownloadError(f"Not a valid expected NetCDF subset: {exc}") from exc
    return counts  # Zero-valid ocean-colour cells are recorded, not filled or mistaken for network failure.


def transfer(chunk, output, timeout=600, runner=curl, verifier=verify_netcdf):
    final = safe_destination(output, chunk["relative_path"])
    ensure_collection(final, chunk)
    receipt = final.with_suffix(".receipt.json")
    if receipt.is_symlink() or final.with_suffix(".nc.part").is_symlink() or receipt.with_name(receipt.name + ".tmp").is_symlink():
        raise DownloadError("Symlink in transfer sidecar path")
    if final.exists():
        if not receipt.exists():
            raise DownloadError(f"Unmanaged file exists; inspect it instead of overwriting: {final}")
        saved = json.loads(receipt.read_text())
        if saved.get("request_sha256") != chunk["request_sha256"] or file_hash(final) != saved.get("sha256"):
            raise DownloadError(f"Existing file/receipt mismatch; inspect or move it before retrying: {final}")
        return {"path": str(final), "state": "verified_existing", **saved}
    final.parent.mkdir(parents=True, exist_ok=True)
    partial = final.with_suffix(".nc.part")
    # ERDDAP dynamically generates files and may not support byte ranges. Restart only this unfinished slice.
    runner(chunk["url"], destination=partial, timeout=timeout)
    counts = verifier(partial, chunk)
    saved = {"request_sha256": chunk["request_sha256"], "sha256": file_hash(partial),
             "bytes": partial.stat().st_size, "valid_counts": counts, "source_url": chunk["url"],
             "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    # Receipt first: a crash before rename remains safely retryable; final only appears after validation.
    write_json(receipt, saved)
    partial.replace(final)
    return {"path": str(final), "state": "downloaded", **saved}


def load_config(path):
    config = json.loads(path.read_text())
    if config.get("schema_version") != 1:
        raise DownloadError("Unsupported configuration schema")
    ids = set()
    for job in config["products"]:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", job["id"]) or job["id"] in ids:
            raise DownloadError("Unsafe/duplicate product ID")
        ids.add(job["id"])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", job["dataset"]):
            raise DownloadError("Unsafe dataset ID")
        if job["cadence"] not in ("day", "month") or not job["variables"]:
            raise DownloadError("Invalid cadence or empty variables")
        path_component(job.get("data_type", job["id"]))
        if len(set(job["variables"])) != len(job["variables"]):
            raise DownloadError("Duplicate variables")
        for variable in job["variables"]:
            path_component(variable)
            path_component(job.get("variable_folders", {}).get(variable, variable))
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("download_config.json"))
    parser.add_argument("--list", action="store_true", help="List automatic and unresolved products without network access")
    parser.add_argument("--output", type=Path, help="Data directory on the server")
    parser.add_argument("--products", nargs="+", help="Default: enabled products only, NOT all pending sources")
    parser.add_argument("--modis-bbox", nargs=4, type=float, metavar=("W", "E", "S", "N"))
    parser.add_argument("--bbox", nargs=4, type=float, help="Override bbox for ONE explicitly selected product")
    parser.add_argument("--start", help="Override start month (YYYY-MM); day products use YYYY-MM-DD")
    parser.add_argument("--end", help="Override end month/date; use together with --start")
    parser.add_argument("--execute", action="store_true", help="Download data; otherwise metadata-only preview")
    parser.add_argument("--allow-missing", action="store_true", help="Download available slices despite catalog gaps; exit 2 still reports incomplete coverage")
    parser.add_argument("--limit", type=int, help="At most this many periods per product, including every variable (smoke test only)")
    parser.add_argument("--timeout", type=int, default=600, help="Per-file curl timeout seconds, excluding retries")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.list:
        for job in config["products"]:
            print(f"AUTO {job['id']}: {job['dataset']} {job['start']}..{job['end']}; bbox={job['bbox']}")
        for item in config.get("pending", []):
            print(f"PENDING {item['id']}: {item['reason']}")
        return 0
    if not args.output or not shutil.which("curl"):
        raise DownloadError("Provide --output and install curl")
    if bool(args.start) != bool(args.end) or (args.limit is not None and args.limit < 1) or args.timeout < 1:
        raise DownloadError("Provide both --start/--end; limit and timeout must be positive")
    selected = args.products or [j["id"] for j in config["products"] if j.get("enabled")]
    known = {j["id"]: j for j in config["products"]}
    if not selected or len(set(selected)) != len(selected) or set(selected) - known.keys():
        raise DownloadError("Unknown, pending or duplicate product; see --list")
    if args.bbox and (not args.products or len(selected) != 1):
        raise DownloadError("--bbox requires one explicit --products selection")
    jobs = []
    for id in selected:
        job = copy.deepcopy(known[id])
        if id == "modis_monthly" and args.modis_bbox:
            job["bbox"] = args.modis_bbox
        if args.bbox:
            job["bbox"] = args.bbox
        if args.start:
            job.update(start=args.start, end=args.end)
        check_bbox(job.get("bbox"))
        period_keys(job["start"], job["end"], job["cadence"])
        jobs.append(job)
    if args.execute:
        try:
            import netCDF4  # noqa: F401
        except ImportError as exc:
            raise DownloadError("Install NetCDF validation dependency: python -m pip install netCDF4") from exc
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    import fcntl  # Linux/macOS; this script is deliberately independent of the desktop app.
    with (output / ".download.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DownloadError("Another downloader is using this output directory") from exc
        run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        run_dir = output / "_runs" / run_id
        report = {"mode": "execute" if args.execute else "preview", "products": [],
                  "pending_products": config.get("pending", []), "limited": args.limit is not None,
                  "scope": "selected automatic products only; not all benchmark data", "complete": False}
        plans = []
        errors = False
        missing = False
        for job in jobs:
            print(f"Inspecting {job['id']} ...", flush=True)
            try:
                plan = plan_product(job)
                write_json(run_dir / f"{job['id']}.plan.json", plan)
                plans.append(plan)
                missing |= bool(plan["missing_periods"])
                print(f"  {plan['available_periods']}/{plan['requested_periods']} periods, {len(plan['chunks'])} variable files; missing={plan['missing_periods']}", flush=True)
            except (DownloadError, KeyError, ValueError) as exc:
                print(f"  ERROR {job['id']}: {exc}", file=sys.stderr, flush=True)
                report["products"].append({"product": job["id"], "error": str(exc)})
                errors = True
        may_download = args.execute and not errors and (not missing or args.allow_missing)
        if args.execute and not may_download:
            print("Data transfer blocked by preflight errors or unacknowledged catalog gaps.", flush=True)
        for plan in plans:
            entry = {"product": plan["product"], "requested_periods": plan["requested_periods"],
                     "available_periods": plan["available_periods"], "requested_files": plan["requested_files"], "missing_periods": plan["missing_periods"],
                     "files": [], "complete": False}
            report["products"].append(entry)
            if may_download:
                periods = list(dict.fromkeys(c["period"] for c in plan["chunks"]))
                chosen_periods = set(periods[:args.limit] if args.limit else periods)
                chosen = [c for c in plan["chunks"] if c["period"] in chosen_periods]
                for n, chunk in enumerate(chosen, 1):
                    print(f"[{plan['product']}] {n}/{len(chosen)} {chunk['period']}", flush=True)
                    try:
                        entry["files"].append(transfer(chunk, output, args.timeout))
                    except (DownloadError, OSError, ValueError) as exc:
                        print(f"  ERROR {chunk['period']}: {exc}", file=sys.stderr, flush=True)
                        entry["files"].append({"period": chunk["period"], "state": "failed", "error": str(exc)})
                        errors = True
                    write_json(run_dir / "report.json", report)
                entry["complete"] = (not plan["missing_periods"] and len(chosen) == plan["requested_files"]
                                     and all(f["state"] != "failed" for f in entry["files"]))
            else:
                entry["state"] = "preview" if not args.execute else "blocked_before_transfer"
        report["complete"] = bool(args.execute and not errors and not missing and not args.limit)
        write_json(run_dir / "report.json", report)
        print(f"Report: {run_dir / 'report.json'}", flush=True)
        if errors:
            return 1
        if missing:
            print("Catalog coverage incomplete. No gap filling. Use --allow-missing to download available slices.")
            return 2
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DownloadError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted. Verified files are retained; rerun to continue.", file=sys.stderr)
        sys.exit(130)
