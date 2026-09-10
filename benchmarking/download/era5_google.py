"""Anonymous ARCO-ERA5 reads; preserve hourly values and checkpoint regional subsets.

Read the documented Zarr v2 layout directly over HTTPS, without Google credentials
or loading the global time series. A global chunk is discarded after each hour.
"""
from __future__ import annotations

import datetime as dt
import json

from download_data import DownloadError

STORE = "https://storage.googleapis.com/gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"


class Store:
    def __init__(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        self.session = requests.Session()
        retry = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET"], backoff_max=30)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.metadata = self.get(".zmetadata").json()["metadata"]

    def get(self, key):
        response = self.session.get(f"{STORE}/{key}", timeout=(15, 60))
        response.raise_for_status()
        return response

    def array(self, name, key):
        import numcodecs
        import numpy as np

        spec = self.metadata[f"{name}/.zarray"]
        if spec["zarr_format"] != 2 or spec["order"] != "C" or spec.get("filters"):
            raise DownloadError("Unsupported Google Zarr encoding")
        payload = self.get(f"{name}/{key}").content
        if spec.get("compressor"):
            payload = numcodecs.get_codec(spec["compressor"]).decode(payload)
        return np.frombuffer(payload, dtype=spec["dtype"]).reshape(spec["chunks"])

    def close(self):
        self.session.close()


def fetch(chunk, destination, store_factory=Store):
    import netCDF4
    import numpy as np
    from download_services import ERA5_VARIABLES

    store = store_factory()
    try:
        meta = store.metadata
        short = chunk["variables"][0]
        name = ERA5_VARIABLES[short]
        attrs = meta[f"{name}/.zattrs"]
        lat = store.array("latitude", "0")
        lon = store.array("longitude", "0")
        spec = meta[f"{name}/.zarray"]
        if (attrs.get("units") != "J m**-2"
                or attrs.get("_ARRAY_DIMENSIONS") != ["time", "latitude", "longitude"]
                or spec["chunks"] != [1, len(lat), len(lon)]):
            raise DownloadError("Google flux units/dimensions/chunk layout changed")
        if (meta["time/.zattrs"].get("units") != "hours since 1900-01-01 00:00:00"
                or meta["time/.zattrs"].get("calendar") != "proleptic_gregorian"):
            raise DownloadError("Google time encoding changed")
        start = dt.datetime.strptime(chunk["period"], "%Y-%m")
        hours = chunk["expected_samples"]
        stop = start + dt.timedelta(hours=hours - 1)
        global_attrs = meta[".zattrs"]
        if (start.date() < dt.date.fromisoformat(global_attrs["valid_time_start"][:10])
                or stop.date() > dt.date.fromisoformat(global_attrs["valid_time_stop"][:10])):
            raise DownloadError("Requested month is outside finalized Google ERA5 coverage")
        west, east, south, north = chunk["bbox"]
        normalized = (lon + 180) % 360 - 180
        yi = np.flatnonzero((lat >= south) & (lat <= north))
        xi = np.flatnonzero((normalized >= west) & (normalized <= east))
        xi = xi[np.argsort(normalized[xi])]
        yi = yi[np.argsort(-lat[yi])]
        if not len(yi) or not len(xi):
            raise DownloadError("No Google grid points inside requested area")
        times = int((start - dt.datetime(1900, 1, 1)).total_seconds() / 3600) + np.arange(hours)
        origin = int(store.array("time", "0")[0])
        indices = times - origin
        # Verify every requested timestamp, including Zarr coordinate chunk boundaries.
        size = meta["time/.zarray"]["chunks"][0]
        for block in np.unique(indices // size):
            mask = indices // size == block
            actual = store.array("time", str(block))[indices[mask] % size]
            if not np.array_equal(actual, times[mask]):
                raise DownloadError("Google time index does not match requested UTC hours")
        checkpoint = destination.with_name(destination.name + ".google")
        if checkpoint.is_symlink():
            raise DownloadError("Symlink in Google checkpoint path")
        exists = checkpoint.exists()
        with netCDF4.Dataset(checkpoint, "a" if exists else "w") as ds:
            if exists:
                if (getattr(ds, "request_sha256", None) != chunk["request_sha256"]
                        or getattr(ds, "source_url", None) != STORE
                        or not np.array_equal(ds["latitude"][:], lat[yi])
                        or not np.array_equal(ds["longitude"][:], normalized[xi])):
                    raise DownloadError("Google checkpoint does not match requested subset")
            else:
                ds.request_sha256 = chunk["request_sha256"]
                ds.source_url = STORE
                ds.download_provider = "Google ARCO-ERA5"
                ds.source_metadata = json.dumps(global_attrs, sort_keys=True)
                ds.processing = "Spatial subset only; original hourly J m-2; no averaging, interpolation or sign conversion"
                ds.completed_hours = 0
                for coord, data in [("latitude", lat[yi]), ("longitude", normalized[xi])]:
                    ds.createDimension(coord, len(data))
                    v = ds.createVariable(coord, "f4", (coord,))
                    v[:] = data
                    v.units = "degrees_north" if coord == "latitude" else "degrees_east"
                ds.createDimension("valid_time", hours)
                v = ds.createVariable("valid_time", "i8", ("valid_time",))
                v.units = "hours since 1900-01-01 00:00:00"
                v.calendar = "proleptic_gregorian"
                v = ds.createVariable(short, "f4", ("valid_time", "latitude", "longitude"),
                                      zlib=True, fletcher32=True, chunksizes=(1, len(yi), len(xi)),
                                      fill_value=np.nan)
                v.setncatts({k: val for k, val in attrs.items() if not k.startswith("_")})
                ds.sync()
            done = int(ds.completed_hours)
            if not 0 <= done <= hours:
                raise DownloadError("Invalid Google checkpoint progress")
            for h in range(done, hours):
                data = store.array(name, f"{indices[h]}.0.0")[0][np.ix_(yi, xi)]
                if not np.isfinite(data).all():
                    raise DownloadError("Missing values in Google surface heat-flux subset")
                ds[short][h] = data
                ds["valid_time"][h] = times[h]
                ds.sync()
                ds.completed_hours = h + 1
                ds.sync()
                if (h + 1) % 24 == 0 or h + 1 == hours:
                    print(f"ERA5 Google {short} {chunk['period']}: {h + 1}/{hours} hours", flush=True)
        checkpoint.replace(destination)
    finally:
        store.close()


def provenance(path):
    import netCDF4

    with netCDF4.Dataset(path) as ds:
        if getattr(ds, "download_provider", "") != "Google ARCO-ERA5":
            return {}  # A complete legacy CDS partial still has its original provenance.
        return {"source_url": ds.source_url, "download_provider": ds.download_provider,
                "source_metadata": json.loads(ds.source_metadata), "processing": ds.processing}
