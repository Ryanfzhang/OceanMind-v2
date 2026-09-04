from __future__ import annotations

import xarray as xr

from ocean_partner.analysis_probe import inspect_source


def test_analysis_probe_builds_metadata_without_loading_scientific_result(tmp_path) -> None:
    path = tmp_path / "ocean.nc"
    xr.Dataset(
        {
            "temperature": (
                ("time", "depth", "latitude", "longitude"),
                [[[[27.0, 28.0], [26.0, 27.5]]]],
                {"units": "degC", "standard_name": "sea_water_temperature"},
            )
        },
        coords={
            "time": ("time", [0], {"axis": "T"}),
            "depth": ("depth", [10.0], {"axis": "Z", "units": "m", "positive": "down"}),
            "latitude": ("latitude", [24.0, 25.0], {"standard_name": "latitude"}),
            "longitude": ("longitude", [-91.0, -90.0], {"standard_name": "longitude"}),
        },
    ).to_netcdf(path)

    context = inspect_source(
        {
            "handle": "source_1",
            "kind": "dataset",
            "title": "Ocean fixture",
            "path": str(path),
            "format": "netcdf",
        }
    )

    assert context["inspection"] == "ready"
    assert context["dimensions"] == {
        "time": 1,
        "depth": 1,
        "latitude": 2,
        "longitude": 2,
    }
    assert context["data_variables"][0]["name"] == "temperature"
    assert {item["coordinate_role"] for item in context["coordinates"]} == {
        "time",
        "depth",
        "latitude",
        "longitude",
    }
    assert context["spatial_context"] == {
        "region_key": "dataset:-91.000000,24.000000,-90.000000,25.000000",
        "bounds": [-91.0, 24.0, -90.0, 25.0],
        "fit_policy": "region_change",
    }


def test_analysis_probe_inspects_a_dataset_collection_without_opening_the_root_as_zarr(
    tmp_path,
) -> None:
    root = tmp_path / "cmems"
    root.mkdir()
    for name, variable in (("temperature", "thetao"), ("salinity", "so")):
        xr.Dataset(
            {
                variable: (
                    ("time", "depth", "latitude", "longitude"),
                    [[[[27.0, 28.0], [26.0, 27.5]]]],
                    {"units": "degC" if variable == "thetao" else "1e-3"},
                )
            },
            coords={
                "time": ("time", [0], {"axis": "T"}),
                "depth": ("depth", [10.0], {"axis": "Z", "units": "m"}),
                "latitude": ("latitude", [24.0, 25.0], {"axis": "Y"}),
                "longitude": ("longitude", [-91.0, -90.0], {"axis": "X"}),
            },
        ).to_zarr(root / f"{name}.zarr", mode="w")

    context = inspect_source(
        {
            "handle": "source_1",
            "kind": "dataset",
            "title": "CMEMS collection",
            "path": str(root),
            "format": "directory",
        }
    )

    assert context["inspection"] == "ready"
    assert context["dataset_layout"] == "collection"
    assert context["member_count"] == 2
    assert {member["data_variables"][0]["name"] for member in context["members"]} == {
        "thetao",
        "so",
    }
    assert context["spatial_context"]["bounds"] == [-91.0, 24.0, -90.0, 25.0]
