"""NOAA original daily OISST route, independent of gaps in ERDDAP mirrors."""
import datetime as dt
import json
from pathlib import Path
import tempfile

from download_data import DownloadError, archive_path, curl, fingerprint, transfer

BASE = "https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr"


def plan(group):
    day = dt.date.fromisoformat(group["start"])
    end = dt.date.fromisoformat(group["end"])
    chunks = []
    while day <= end:
        period = day.isoformat()
        url = f"{BASE}/{day:%Y%m}/oisst-avhrr-v02r01.{day:%Y%m%d}.nc"
        for variable in group["variables"]:
            chunk = {"dataset": "NOAA-OISST-v2.1-original", "version_label": "v02r01-final",
                     "url": url, "collection_provider": BASE, "period": period,
                     "variables": [variable], "bbox": group["bbox"], "adapter": "ncei"}
            chunk["request_sha256"] = fingerprint(chunk)
            chunk["relative_path"] = archive_path({"id": "oisst_daily", "data_type": group["data_type"]}, variable, period, chunk["request_sha256"])
            chunks.append(chunk)
        day += dt.timedelta(days=1)
    return chunks


def crop(source, destination, chunk):
    """Lossless spatial subset; preserve native packed values, dimensions and units."""
    import netCDF4
    import numpy as np
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(destination, "w") as dst:
        indices = {}
        for axis, bounds in [("lon", chunk["bbox"][:2]), ("lat", chunk["bbox"][2:])]:
            values = src.variables[axis][:]
            if np.ma.getmaskarray(values).any():
                raise DownloadError("Masked OISST coordinate")
            compare = (values + 180) % 360 - 180 if axis == "lon" else values
            chosen = np.flatnonzero((compare >= bounds[0]) & (compare <= bounds[1]))
            if not chosen.size or (chosen.size > 1 and np.any(np.diff(chosen) != 1)):
                raise DownloadError("Empty/discontiguous OISST subset")
            indices[axis] = slice(int(chosen[0]), int(chosen[-1])+1)
        variable = src.variables[chunk["variables"][0]]
        names = list(dict.fromkeys([*variable.dimensions, *chunk["variables"]]))
        for name in variable.dimensions:
            size = len(src.dimensions[name])
            selected = indices.get(name, slice(0, size))
            dst.createDimension(name, selected.stop - selected.start)
        dst.setncatts({name: src.getncattr(name) for name in src.ncattrs() if name != "_NCProperties"})
        dst.setncattr("oceanx_source_url", chunk["url"])
        dst.setncattr("oceanx_subset_bbox_wesn", json.dumps(chunk["bbox"]))
        for axis, label in [("lon", "lon"), ("lat", "lat")]:
            chosen = src.variables[axis][indices[axis]]
            dst.setncattr(f"geospatial_{label}_min", float(chosen.min()))
            dst.setncattr(f"geospatial_{label}_max", float(chosen.max()))
        for name in names:
            v = src.variables[name]
            out = dst.createVariable(name, v.dtype, v.dimensions, zlib=True,
                                     fill_value=getattr(v, "_FillValue", False))
            out.setncatts({a: v.getncattr(a) for a in v.ncattrs() if a != "_FillValue"})
            v.set_auto_maskandscale(False)
            out.set_auto_maskandscale(False)
            out[:] = v[tuple(indices.get(d, slice(None)) for d in v.dimensions)]


def verify(path, chunk):
    import netCDF4
    import numpy as np
    with netCDF4.Dataset(path) as ds:
        time = ds.variables["time"]
        dates = netCDF4.num2date(time[:], time.units)
        if len(dates) != 1 or dates[0].strftime("%Y-%m-%d") != chunk["period"]:
            raise DownloadError("Wrong OISST day")
        for axis, bounds in [("lon", chunk["bbox"][:2]), ("lat", chunk["bbox"][2:])]:
            values = ds.variables[axis][:]
            values = (values + 180) % 360 - 180 if axis == "lon" else values
            if (not values.size or np.ma.getmaskarray(values).any() or not np.isfinite(values).all()
                    or values.min() < bounds[0] or values.max() > bounds[1]):
                raise DownloadError("Wrong OISST grid")
            expected = np.arange(-179.875 if axis == "lon" else -89.875,
                                 180 if axis == "lon" else 90, 0.25)
            expected = expected[(expected >= bounds[0]) & (expected <= bounds[1])]
            if values.shape != expected.shape or not np.allclose(values, expected, atol=1e-6):
                raise DownloadError("Incomplete OISST grid")
        result = {}
        for name in chunk["variables"]:
            v = ds.variables[name]
            if not getattr(v, "units", "") or not {"time", "lat", "lon"} <= set(v.dimensions):
                raise DownloadError("Invalid OISST science variable")
            values = v[:]
            result[name] = int(np.ma.filled(np.isfinite(values) & ~np.ma.getmaskarray(values), False).sum())
        return result


def execute(chunks, root, record):
    """One original global file per day, cropped to both fields; temp file removed after use."""
    dates = list(dict.fromkeys(c["period"] for c in chunks))
    by_date = {day: [] for day in dates}
    for chunk in chunks:
        by_date[chunk["period"]].append(chunk)
    for day in dates:
        with tempfile.TemporaryDirectory(prefix="oisst-", dir=root) as folder:
            native = Path(folder) / "original.nc"
            for chunk in by_date[day]:
                def runner(url, destination, timeout):
                    if not native.exists():
                        curl(url, native, timeout=600)
                    crop(native, destination, chunk)
                record(transfer(chunk, root, runner=runner, verifier=verify))
