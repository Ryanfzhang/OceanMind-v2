"""Offline regression tests; run with python -m unittest discover -s benchmarking/download."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("download_data", Path(__file__).with_name("download_data.py"))
down = importlib.util.module_from_spec(spec)
spec.loader.exec_module(down)


def job():
    return {"id": "test", "server": "https://example.test/erddap", "dataset": "test_data",
            "version_label": "test-version", "variables": ["chlor_a"], "start": "2011-01",
            "end": "2011-03", "cadence": "month", "bbox": [104, 120, 1, 25]}


def provider(extra=False, gap=False):
    axes = {"time": ["2011-01-16T12:00:00Z", "2011-02-16T12:00:00Z", "2011-03-16T12:00:00Z"],
            "latitude": [30, 25, 20, 10, 1, 0], "longitude": [100, 104, 110, 120, 125]}
    if gap:
        axes["time"].pop(1)
    dims = ["time", "latitude", "longitude"]
    if extra:
        dims.insert(1, "zlev")
        axes["zlev"] = [10.0]

    def fetch(url):
        if "/info/" in url:
            return {"table": {"rows": [["variable", "chlor_a", "", "float", ", ".join(dims)]]}}
        name = url.split("?")[-1]
        return {"table": {"columnNames": [name], "columnUnits": ["UTC" if name == "time" else "degrees"],
                          "rows": [[v] for v in axes[name]]}}
    return fetch


class PlanTests(unittest.TestCase):
    def test_calendar_range_and_leap_day(self):
        self.assertEqual(down.period_keys("2020-02-28", "2020-03-01", "day"),
                         ["2020-02-28", "2020-02-29", "2020-03-01"])
        self.assertEqual(down.period_keys("2011-12", "2012-02", "month"), ["2011-12", "2012-01", "2012-02"])

    def test_descending_latitude_and_real_month_timestamps(self):
        plan = down.plan_product(job(), fetch=provider())
        self.assertEqual(len(plan["chunks"]), 3)
        first = plan["chunks"][0]
        self.assertEqual(first["expected"]["latitude"], {"count": 4, "first": 25, "last": 1})
        self.assertEqual(first["expected"]["time"]["first"], "2011-01-16T12:00:00Z")
        self.assertIn("%5B0:1:0%5D%5B1:1:4%5D%5B1:1:3%5D", first["url"])

    def test_missing_month_is_not_nearest_neighbor_or_fill(self):
        plan = down.plan_product(job(), fetch=provider(gap=True))
        self.assertEqual(plan["missing_periods"], ["2011-02"])
        self.assertEqual([c["period"] for c in plan["chunks"]], ["2011-01", "2011-03"])

    def test_extra_axis_must_be_explicit(self):
        with self.assertRaises(down.DownloadError):
            down.plan_product(job(), fetch=provider(extra=True))
        j = job()
        j["axis_values"] = {"zlev": 10}
        self.assertEqual(down.plan_product(j, fetch=provider(extra=True))["chunks"][0]["dimensions"],
                         ["time", "zlev", "latitude", "longitude"])

    def test_absent_variable_is_not_substituted(self):
        j = job()
        j["variables"] = ["sst"]
        with self.assertRaises(down.DownloadError):
            down.plan_product(j, fetch=provider())

    def test_longitude_360_normalization(self):
        self.assertEqual(down.axis_slice([260, 265, 270, 275, 280], -95, -85, longitude=True), (1, 3))
        with self.assertRaises(down.DownloadError):
            down.axis_slice([0, 90, 180, 270, 359], -5, 5, longitude=True)

    def test_missing_invalid_and_empty_bbox(self):
        for bbox in (None, [120, 104, 1, 25], [104, 120, -91, 25], [104, float('nan'), 1, 25]):
            with self.assertRaises(down.DownloadError):
                down.check_bbox(bbox)
        with self.assertRaises(down.DownloadError):
            down.axis_slice([0, 1], 5, 10)

    def test_scope_and_version_change_output_identity(self):
        first = down.plan_product(job(), fetch=provider())["chunks"][0]
        j = job()
        j["bbox"] = [104, 110, 1, 25]
        second = down.plan_product(j, fetch=provider())["chunks"][0]
        j = job()
        j["version_label"] = "different-release"
        third = down.plan_product(j, fetch=provider())["chunks"][0]
        self.assertEqual(len({c["relative_path"] for c in [first, second, third]}), 3)

    def test_pending_products_and_bad_ids_fail_before_network(self):
        with self.assertRaises(down.DownloadError):
            down.main(["--output", "/tmp/not-used", "--products", "gulf_reanalysis"])
        config = {"schema_version": 1, "products": [job()]}
        config["products"][0]["id"] = "../../outside"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config))
            with self.assertRaises(down.DownloadError):
                down.load_config(path)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.chunk = down.plan_product(job(), fetch=provider())["chunks"][0]

    @staticmethod
    def fake_curl(url, destination, timeout):
        destination.write_bytes(b"synthetic-payload")

    @staticmethod
    def fake_verify(path, chunk):
        if path.read_bytes() != b"synthetic-payload":
            raise down.DownloadError("invalid")
        return {"chlor_a": 0}

    def test_resume_skips_hash_verified_files(self):
        first = down.transfer(self.chunk, self.output, runner=self.fake_curl, verifier=self.fake_verify)
        self.assertEqual(first["state"], "downloaded")
        def forbidden(*args, **kwargs):
            self.fail("Already verified data should not be downloaded again")
        second = down.transfer(self.chunk, self.output, runner=forbidden)
        self.assertEqual(second["state"], "verified_existing")
        self.assertEqual(second["valid_counts"], {"chlor_a": 0})

    def test_partial_and_interrupted_file_restarts_only_that_slice(self):
        final = self.output / self.chunk["relative_path"]
        final.parent.mkdir(parents=True)
        final.with_suffix(".nc.part").write_bytes(b"interrupted")
        result = down.transfer(self.chunk, self.output, runner=self.fake_curl, verifier=self.fake_verify)
        self.assertEqual(result["state"], "downloaded")
        self.assertFalse(final.with_suffix(".nc.part").exists())

    def test_html_not_promoted_to_final_netcdf(self):
        def html(url, destination, timeout):
            destination.write_text("<html>login</html>")
        with self.assertRaises(down.DownloadError):
            down.transfer(self.chunk, self.output, runner=html, verifier=self.fake_verify)
        self.assertFalse((self.output / self.chunk["relative_path"]).exists())

    def test_corrupt_or_unmanaged_existing_data_is_not_overwritten(self):
        final = self.output / self.chunk["relative_path"]
        final.parent.mkdir(parents=True)
        final.write_bytes(b"user-file")
        with self.assertRaises(down.DownloadError):
            down.transfer(self.chunk, self.output, runner=self.fake_curl, verifier=self.fake_verify)
        self.assertEqual(final.read_bytes(), b"user-file")
        final.unlink()
        down.transfer(self.chunk, self.output, runner=self.fake_curl, verifier=self.fake_verify)
        final.write_bytes(b"damaged")
        with self.assertRaises(down.DownloadError):
            down.transfer(self.chunk, self.output, runner=self.fake_curl, verifier=self.fake_verify)

    def test_real_netcdf_validation_rejects_wrong_time(self):
        import netCDF4
        import numpy as np
        path = self.output / "fixture.nc"
        with netCDF4.Dataset(path, "w") as ds:
            for dim, expected in self.chunk["expected"].items():
                ds.createDimension(dim, expected["count"])
                variable = ds.createVariable(dim, "f8", (dim,))
                if dim == "time":
                    variable.units = "seconds since 1970-01-01"
                    variable[:] = netCDF4.date2num([down.date_value(expected["first"])], variable.units)
                else:
                    variable[:] = np.linspace(expected["first"], expected["last"], expected["count"])
            ds.createVariable("chlor_a", "f4", tuple(self.chunk["dimensions"]), fill_value=-999)[:] = 1
        counts = down.verify_netcdf(path, self.chunk)
        self.assertEqual(counts["chlor_a"], 12)
        wrong = copy.deepcopy(self.chunk)
        wrong["expected"]["time"]["first"] = "2011-01-01T00:00:00Z"
        with self.assertRaises(down.DownloadError):
            down.verify_netcdf(path, wrong)


class PreflightTests(unittest.TestCase):
    def run_missing(self, allow):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            j = job()
            j["enabled"] = True
            config.write_text(json.dumps({"schema_version": 1, "products": [j]}))
            plan = down.plan_product(j, fetch=provider(gap=True))
            argv = ["--config", str(config), "--output", str(root / "data"), "--execute"]
            if allow:
                argv.append("--allow-missing")
            with patch.object(down, "plan_product", return_value=plan), patch.object(
                down, "transfer", return_value={"state": "downloaded"}
            ) as transfer:
                code = down.main(argv)
                calls = transfer.call_count
            report = json.loads(next((root / "data" / "_runs").glob("*/report.json")).read_text())
            return code, calls, report

    def test_unacknowledged_gaps_block_all_data_transfers(self):
        code, calls, report = self.run_missing(False)
        self.assertEqual((code, calls), (2, 0))
        self.assertFalse(report["complete"])
        self.assertEqual(report["products"][0]["state"], "blocked_before_transfer")

    def test_allow_missing_never_claims_complete_coverage(self):
        code, calls, report = self.run_missing(True)
        self.assertEqual((code, calls), (2, 2))
        self.assertFalse(report["complete"])
        self.assertFalse(report["products"][0]["complete"])
        self.assertEqual(report["products"][0]["missing_periods"], ["2011-02"])


if __name__ == "__main__":
    unittest.main()
