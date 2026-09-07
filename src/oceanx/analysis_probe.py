"""Deterministic, metadata-only dataset inspection for task AnalysisContext.

This module runs in the same scientific Python environment as Expert code.  It
opens coordinates and variable metadata without loading full scientific arrays.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ARRAY_FORMATS = {".nc", ".nc4", ".cdf", ".netcdf"}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value[:64]]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in list(value.items())[:64]}
    return str(value)


def _role(name: str, attrs: dict[str, Any]) -> str | None:
    lowered = name.lower()
    standard_name = str(attrs.get("standard_name", "")).lower()
    axis = str(attrs.get("axis", "")).upper()
    units = str(attrs.get("units", "")).lower()
    if axis == "X" or standard_name == "longitude" or lowered in {"lon", "longitude", "xlon"}:
        return "longitude"
    if axis == "Y" or standard_name == "latitude" or lowered in {"lat", "latitude", "ylat"}:
        return "latitude"
    if axis == "T" or standard_name == "time" or lowered in {"time", "date", "datetime"}:
        return "time"
    if (
        axis == "Z"
        or "depth" in standard_name
        or lowered in {"depth", "deptht", "lev", "level", "z"}
        or ("positive" in attrs and units in {"m", "meter", "meters"})
    ):
        return "depth"
    return None


def _coordinate_extent(variable: Any) -> list[Any] | None:
    """Read only a bounded coordinate vector, never a scientific data field."""

    if int(getattr(variable, "size", 0)) > 100_000:
        return None
    try:
        import numpy as np

        values = np.asarray(variable.values).reshape(-1)
        if values.size == 0:
            return None
        if np.issubdtype(values.dtype, np.datetime64):
            valid = values[~np.isnat(values)]
            if valid.size:
                return [str(valid.min()), str(valid.max())]
            return None
        if np.issubdtype(values.dtype, np.number):
            numeric = values.astype(float, copy=False)
            valid = numeric[np.isfinite(numeric)]
            if valid.size:
                return [float(valid.min()), float(valid.max())]
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _array_metadata(name: str, variable: Any, *, coordinate: bool = False) -> dict[str, Any]:
    attrs = {str(key): _json_value(value) for key, value in list(variable.attrs.items())[:16]}
    result: dict[str, Any] = {
        "name": name,
        "dims": list(variable.dims),
        "shape": list(variable.shape),
        "dtype": str(variable.dtype),
        "attrs": attrs,
    }
    chunks = getattr(variable, "chunks", None)
    if chunks:
        result["chunks"] = [list(chunk) for chunk in chunks]
    role = _role(name, attrs)
    if role:
        result["coordinate_role"] = role
    if coordinate:
        extent = _coordinate_extent(variable)
        if extent is not None:
            result["extent"] = extent
    return result


def _inspect_xarray(path: Path, format_hint: str) -> dict[str, Any]:
    import xarray as xr

    normalized = format_hint.lower()
    if normalized == "zarr" or path.is_dir():
        dataset = xr.open_zarr(path, consolidated=None, chunks=None, decode_times=True)
    else:
        dataset = xr.open_dataset(path, chunks=None, decode_times=True)
    try:
        coordinates = [
            _array_metadata(str(name), variable, coordinate=True)
            for name, variable in list(dataset.coords.items())[:32]
        ]
        variables = [
            _array_metadata(str(name), variable)
            for name, variable in list(dataset.data_vars.items())[:96]
        ]
        return {
            "dimensions": {str(name): int(size) for name, size in dataset.sizes.items()},
            "coordinates": coordinates,
            "data_variables": variables,
            "attrs": {
                str(key): _json_value(value)
                for key, value in list(dataset.attrs.items())[:24]
            },
        }
    finally:
        dataset.close()


def _collection_candidates(path: Path) -> list[tuple[Path, str]]:
    zarr = sorted(
        child for child in path.iterdir()
        if child.is_dir()
        and (
            child.suffix.lower() == ".zarr"
            or (child / ".zgroup").is_file()
            or (child / "zarr.json").is_file()
        )
    )
    # Equivalent Zarr stores are the preferred task input; avoid describing the
    # same variables a second time from large NetCDF siblings.
    if zarr:
        return [(child, "zarr") for child in zarr[:24]]
    arrays = sorted(
        child for child in path.iterdir()
        if child.is_file() and child.suffix.lower() in _ARRAY_FORMATS
    )
    return [(child, child.suffix.lstrip(".") or "netcdf") for child in arrays[:24]]


def _spatial_context(coordinates: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_role = {
        item.get("coordinate_role"): item.get("extent")
        for item in coordinates
        if isinstance(item.get("extent"), list) and len(item["extent"]) == 2
    }
    longitude = by_role.get("longitude")
    latitude = by_role.get("latitude")
    if not longitude or not latitude or not all(
        isinstance(value, (int, float))
        for value in (*longitude, *latitude)
    ):
        return None
    west, east = float(longitude[0]), float(longitude[1])
    south, north = float(latitude[0]), float(latitude[1])
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None
    bounds = [west, south, east, north]
    return {
        "region_key": "dataset:" + ",".join(f"{value:.6f}" for value in bounds),
        "bounds": bounds,
        "fit_policy": "region_change",
    }


def _inspect_collection(path: Path) -> dict[str, Any]:
    candidates = _collection_candidates(path)
    if not candidates:
        raise ValueError("Dataset directory contains no supported Zarr or NetCDF members")

    members: list[dict[str, Any]] = []
    shared_coordinates: list[dict[str, Any]] | None = None
    shared_dimensions: dict[str, int] | None = None
    for member_path, format_hint in candidates:
        try:
            metadata = _inspect_xarray(member_path, format_hint)
        except Exception as exc:  # noqa: BLE001 - normalize individual engine failures
            members.append(
                {
                    "path": str(member_path),
                    "format": format_hint,
                    "inspection": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        coordinates = metadata.pop("coordinates")
        dimensions = metadata.pop("dimensions")
        if shared_coordinates is None:
            shared_coordinates = coordinates
            shared_dimensions = dimensions
        elif coordinates != shared_coordinates:
            metadata["coordinates"] = coordinates
        if dimensions != shared_dimensions:
            metadata["dimensions"] = dimensions
        members.append(
            {
                "path": str(member_path),
                "format": format_hint,
                "inspection": "ready",
                **metadata,
            }
        )
    if shared_coordinates is None or not any(
        member.get("inspection") == "ready" for member in members
    ):
        raise ValueError("No supported dataset member could be inspected")
    result: dict[str, Any] = {
        "dataset_layout": "collection",
        "member_count": len(members),
        "dimensions": shared_dimensions or {},
        "coordinates": shared_coordinates,
        "members": members,
    }
    spatial = _spatial_context(shared_coordinates)
    if spatial:
        result["spatial_context"] = spatial
    time = next(
        (
            item.get("extent")
            for item in shared_coordinates
            if item.get("coordinate_role") == "time"
        ),
        None,
    )
    if isinstance(time, list) and len(time) == 2:
        result["time_range"] = time
    return result


def _inspect_table(path: Path, format_hint: str) -> dict[str, Any]:
    import pandas as pd

    normalized = format_hint.lower()
    if normalized in {"csv", "txt"}:
        frame = pd.read_csv(path, nrows=8)
    elif normalized in {"parquet", "pq"}:
        frame = pd.read_parquet(path).head(8)
    else:
        raise ValueError(f"Unsupported tabular format: {format_hint}")
    return {
        "dimensions": {"rows": None, "columns": len(frame.columns)},
        "coordinates": [],
        "data_variables": [
            {"name": str(name), "dims": ["rows"], "shape": [None], "dtype": str(dtype)}
            for name, dtype in frame.dtypes.items()
        ],
        "attrs": {},
    }


def inspect_source(source: dict[str, Any]) -> dict[str, Any]:
    result = {
        "handle": source.get("handle"),
        "kind": source.get("kind"),
        "title": source.get("title"),
        "path": source.get("path"),
        "format": source.get("format"),
    }
    if source.get("kind") != "dataset" or not source.get("path"):
        result["inspection"] = "not_a_dataset"
        return result
    path = Path(str(source["path"]))
    format_hint = str(source.get("format") or path.suffix.lstrip("."))
    try:
        if path.is_dir() and not (
            (path / ".zgroup").is_file() or (path / "zarr.json").is_file()
        ):
            metadata = _inspect_collection(path)
        elif format_hint.lower() in {"csv", "txt", "parquet", "pq"}:
            metadata = _inspect_table(path, format_hint)
        else:
            metadata = _inspect_xarray(path, format_hint)
            spatial = _spatial_context(metadata.get("coordinates", []))
            if spatial:
                metadata["spatial_context"] = spatial
        result.update(metadata)
        result["inspection"] = "ready"
    # Dataset engines expose many third-party exception types.  This process is
    # the deliberate error-normalization boundary and never retries the probe.
    except Exception as exc:  # noqa: BLE001
        result["inspection"] = "unavailable"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> None:
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    output = {
        "schema_version": "ocean-analysis-context/v1",
        "sources": [inspect_source(item) for item in payload.get("sources", [])],
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
