"""Shared archive tests: no credentials, live providers, downloads or LLM calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import sys

import download_data as down
import download_services as services
import prepare_queries as prepare
from test_download_data import job, provider


class ArchiveTests(unittest.TestCase):
    def test_variable_year_layout_and_multiple_variables(self):
        j = job()
        j.update(data_type="MODIS_Aqua", variable_folders={"chlor_a": "chlorophyll"})
        chunk = down.plan_product(j, provider())["chunks"][0]
        self.assertTrue(chunk["relative_path"].startswith("MODIS_Aqua/chlorophyll/2011/test_2011-01_"))
        j["variables"] = ["chlor_a", "mask"]
        def fetch(url):
            result = provider()(url)
            if "/info/" in url:
                result["table"]["rows"].append(["variable", "mask", "", "byte", "time, latitude, longitude"])
            return result
        plan = down.plan_product(j, fetch)
        self.assertEqual(plan["requested_files"], 6)
        self.assertEqual(plan["available_periods"], 3)
        self.assertEqual(len(plan["chunks"]), 6)
        self.assertEqual(plan["chunks"][1]["variables"], ["mask"])
        self.assertIn("/mask/2011/", plan["chunks"][1]["relative_path"])

    def test_output_path_escape_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in ["../escape.nc", "/tmp/escape.nc"]:
                with self.assertRaises(down.DownloadError):
                    down.safe_destination(root, value)
            (root / "link").symlink_to(root, target_is_directory=True)
            with self.assertRaises(down.DownloadError):
                down.safe_destination(root, "link/data.nc")
        with self.assertRaises(down.DownloadError):
            down.archive_path({"id": "test", "data_type": "../../x"}, "u", "2020-01", "abcd")

    def test_same_variable_different_subset_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c = down.plan_product(job(), provider())["chunks"][0]
            down.ensure_collection(root / c["relative_path"], c)
            j = job()
            j["bbox"] = [104, 110, 1, 25]
            other = down.plan_product(j, provider())["chunks"][0]
            with self.assertRaises(down.DownloadError):
                down.ensure_collection(root / other["relative_path"], other)


class ServiceTests(unittest.TestCase):
    def test_official_sdk_dispatch_uses_local_staging(self):
        for source in ["era5", "cmems"]:
            c = services.build_plan(source, ["ssr"] if source == "era5" else ["thetao"],
                                    [2023], [6], [120, 128, 25, 34], "product", "version")[0]
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "request.nc.part"
                sdk = Mock()
                if source == "era5":
                    def retrieve(chunk, target):
                        Path(target).write_bytes(b"test SDK response")
                    sdk.fetch.side_effect = retrieve
                else:
                    def subset(**kwargs):
                        (Path(kwargs["output_directory"]) / kwargs["output_filename"]).write_bytes(b"test SDK response")
                    sdk.subset.side_effect = subset
                module = "era5_google" if source == "era5" else "copernicusmarine"
                with patch.dict(sys.modules, {module: sdk}):
                    services.fetch_service(c, destination)
                self.assertEqual(destination.read_bytes(), b"test SDK response")
                self.assertEqual(list(Path(directory).iterdir()), [destination])
                if source == "era5":
                    self.assertEqual(sdk.fetch.call_args.args, (c, destination))
                else:
                    for key, value in c["request"].items():
                        self.assertEqual(sdk.subset.call_args.kwargs[key], value)

    def test_era5_preserves_hourly_accumulations_and_leap_year(self):
        chunks = services.build_plan("era5", ["ssr", "slhf"], [2020], [2], [120, 128, 25, 34])
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["expected_samples"], 29*24)
        self.assertEqual(chunks[0]["request"]["area"], [34, 120, 25, 128])
        self.assertEqual(chunks[0]["request"]["variable"], ["surface_net_solar_radiation"])
        self.assertTrue(chunks[0]["relative_path"].startswith("ERA5/ssr/2020/"))
        self.assertNotIn("key", json.dumps(chunks))

    def test_cmems_requires_explicit_version_and_preserves_full_depth(self):
        with self.assertRaises(down.DownloadError):
            services.build_plan("cmems", ["thetao"], [2023], [6], [120, 128, 25, 34])
        c = services.build_plan("cmems", ["thetao"], [2023], [6], [120, 128, 25, 34], "test_product", "test_version")[0]
        self.assertNotIn("maximum_depth", c["request"])
        self.assertEqual(c["expected_samples"], 30)
        self.assertTrue(c["relative_path"].startswith("CMEMS/thetao/2023/"))
        self.assertNotIn("password", c["request"])

    def test_real_netcdf_missing_hours_rejected(self):
        import netCDF4
        import numpy as np
        c = services.build_plan("era5", ["ssr"], [2020], [2], [120, 128, 25, 34])[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.nc"
            with netCDF4.Dataset(path, "w") as ds:
                for name, vals in [("valid_time", np.arange(29*24)), ("latitude", [25, 34]), ("longitude", [120, 128])]:
                    ds.createDimension(name, len(vals))
                    v = ds.createVariable(name, "f8", (name,))
                    v[:] = vals
                    v.units = "hours since 2020-02-01" if name == "valid_time" else "degrees"
                v = ds.createVariable("ssr", "f4", ("valid_time", "latitude", "longitude"))
                v.units = "J m-2"
                v[:] = np.ones((29*24, 2, 2))
            self.assertEqual(services.verify_service_file(path, c), {"ssr": 29*24*4})
            c["expected_grid_step"] = 0.25
            with self.assertRaises(down.DownloadError):
                services.verify_service_file(path, c)  # two corner pixels are not a complete 0.25-degree grid
            c.pop("expected_grid_step")
            with netCDF4.Dataset(path, "a") as ds:
                ds.variables["valid_time"][1] = 0
            with self.assertRaises(down.DownloadError):
                services.verify_service_file(path, c)


class BindingTests(unittest.TestCase):
    def test_directory_reference_keeps_query_and_does_not_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            data = root / "CMOMS/temperature/2011"
            data.mkdir(parents=True)
            source = data / "original.nc"
            source.write_bytes(b"path-only fixture, not validated science data")
            cases = prepare.build_cases(["Q01"], root, datasets=["CMOMS/temperature/2011"])
            task = json.loads((prepare.TASKS / "Q01/task_info.json").read_text())
            self.assertEqual(cases[0]["query"], task["query"])
            self.assertEqual(cases[0]["datasets"], [str(data.resolve())])
            self.assertEqual(len(list(root.rglob("*.nc"))), 1)
            from oceanx.batch import QueryCase
            QueryCase.model_validate(cases[0])

    def test_missing_binding_empty_directory_and_references_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty").mkdir()
            for kwargs in [{"bindings": {}}, {"datasets": ["empty"]}, {"datasets": ["../escape"]}]:
                with self.assertRaises((ValueError, FileNotFoundError)):
                    prepare.build_cases(["Q01"], root, **kwargs)
            (root / "checklist.json").write_text("{}")
            with self.assertRaises(ValueError):
                prepare.build_cases(["Q01"], root, datasets=["checklist.json"])


if __name__ == "__main__":
    unittest.main()
