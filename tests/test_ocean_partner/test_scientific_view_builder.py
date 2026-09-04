from __future__ import annotations

import json

import numpy as np
import pytest

from ocean_partner.expert_deliverables import (
    ExpertDeliverableService,
    hydrate_ocean_view_netcdf,
    hydrate_scientific_manifest,
    hydrate_spatial_manifest,
)
from ocean_partner.expert_execution import SCIENTIFIC_VIEW_API_CONTRACT, ExpertCodeExecutionService
from ocean_partner.scientific_view import ScientificFigure, ScientificMap, publish_report
from ocean_partner.tools import candidate_outputs_from_execution


def test_builder_owns_numpy_conversion_and_continuous_field_layout(tmp_path) -> None:
    figure = ScientificFigure(plot_kind="section", title="Temperature section")
    panel = figure.panel(
        x=np.asarray([20.0, 21.0, 22.0], dtype=np.float32),
        y=np.asarray([0.5, 50.0], dtype=np.float32),
        x_label="Latitude",
        y_label="Depth",
        y_units="m",
        y_reverse=True,
    )
    panel.heatmap(
        np.asarray([[27.0, np.nan, 26.0], [20.0, 19.0, 18.0]], dtype=np.float32),
        colorbar_label="Temperature (degC)",
    )

    output = figure.save(tmp_path / "section.nc")
    hydrated = hydrate_ocean_view_netcdf(output)
    payload = hydrated

    panel_payload = payload["panels"][0]
    z_field = panel_payload["layers"][0]["z"]
    assert len(payload["data"][z_field]) == 6
    assert hydrated["data"][z_field] == [27.0, None, 26.0, 20.0, 19.0, 18.0]
    assert panel_payload["axes"]["x"]["field"] in payload["data"]
    assert panel_payload["axes"]["y"]["reverse"] is True
    metadata = ExpertDeliverableService._validate_structured_data(hydrated, expected_kind="section")
    assert metadata["renderer_schema"] == "ocean-scientific-figure/v4"
    assert metadata["layer_types"] == ["field2d"]
    assert panel_payload["layers"][0]["render"] == "filled_contour"
    assert panel_payload["title"] == "Temperature (degC)"


def test_builder_records_generic_categorised_samples(tmp_path) -> None:
    figure = ScientificFigure(plot_kind="scatter", title="Categorised samples")
    panel = figure.panel(x=[34.9, 35.4, 36.6, 36.7], y=[5.0, 9.0, 20.0, 24.0])
    panel.categories(
        ["deep", "intermediate", "upper", "upper"],
        labels={"deep": "Deep water", "intermediate": "Intermediate water", "upper": "Upper water"},
    )

    output = figure.save(tmp_path / "categories.nc")
    payload = hydrate_ocean_view_netcdf(output)
    metadata = ExpertDeliverableService._validate_structured_data(payload, expected_kind="scatter")

    assert metadata["layer_types"] == ["categories"]
    assert payload["panels"][0]["layers"][0]["labels"]["deep"] == "Deep water"


def test_builder_extracts_contours_without_matplotlib_collections_api(tmp_path) -> None:
    x = np.linspace(34.0, 37.0, 12)
    y = np.linspace(5.0, 30.0, 10)
    salinity, temperature = np.meshgrid(x, y)
    sigma = salinity - 0.1 * temperature

    figure = ScientificFigure(plot_kind="ts_diagram", title="T-S structure")
    panel = figure.panel(x=x, y=y, x_label="Salinity", y_label="Temperature")
    panel.contour_grid(sigma, levels=[32.0, 33.0, 34.0])
    output = figure.save(tmp_path / "ts.nc")
    hydrated = hydrate_ocean_view_netcdf(output)
    assert hydrated["panels"][0]["layers"][0]["paths"]
    ExpertDeliverableService._validate_structured_data(hydrated, expected_kind="ts_diagram")


def test_builder_rejects_transposed_fields_instead_of_guessing(tmp_path) -> None:
    figure = ScientificFigure(plot_kind="hovmoller", title="Time-depth structure")
    panel = figure.panel(x=["2025-04", "2025-05", "2025-06"], y=[0.0, 50.0])

    with pytest.raises(ValueError, match=r"expected \(len\(y\), len\(x\)\)"):
        panel.heatmap(np.ones((3, 2)))


def test_builder_normalizes_datetime_and_matplotlib_cycle_colors(tmp_path) -> None:
    figure = ScientificFigure(plot_kind="time_series", title="Seasonal cycle")
    panel = figure.panel(
        x=np.asarray(["2025-04-01", "2025-05-01"], dtype="datetime64[ns]"),
        y=[27.0, 28.0],
    )
    panel.line(color="C0")
    output = figure.save(tmp_path / "seasonal.nc")
    payload = hydrate_ocean_view_netcdf(output)

    x_field = payload["panels"][0]["axes"]["x"]["field"]
    assert payload["data"][x_field] == ["2025-04-01T00:00:00.000", "2025-05-01T00:00:00.000"]
    assert payload["panels"][0]["axes"]["x"]["scale"] == "time"
    assert payload["panels"][0]["axes"]["x"]["tick_format"] == "date"
    assert payload["panels"][0]["layers"][0]["style"]["color"] == "#0072B2"


def test_builder_reuses_shared_vectors_across_panels() -> None:
    x = [34.8, 35.2, 36.1]
    y = [8.0, 16.0, 25.0]
    figure = ScientificFigure(plot_kind="ts_diagram", title="Shared samples")
    figure.panel(x=x, y=y).scatter()
    figure.panel(x=x, y=y).scatter()

    assert len(figure.data) == 2
    first, second = figure.panels
    assert first.payload["axes"]["x"]["field"] == second.payload["axes"]["x"]["field"]
    assert first.payload["axes"]["y"]["field"] == second.payload["axes"]["y"]["field"]


def test_renderer_accepts_publication_scale_classified_samples(tmp_path) -> None:
    size = 40_000
    figure = ScientificFigure(plot_kind="scatter", title="Classified water masses")
    figure.panel(
        x=np.linspace(33.0, 37.0, size),
        y=np.linspace(4.0, 30.0, size),
    ).categories([f"mass-{index % 4}" for index in range(size)])
    output = figure.save(tmp_path / "classified.nc")
    payload = hydrate_ocean_view_netcdf(output)

    metadata = ExpertDeliverableService._validate_structured_data(payload, expected_kind="scatter")
    assert metadata["point_count"] == size * 3


def test_builder_requires_numeric_scatter_colours_to_use_data_fields() -> None:
    figure = ScientificFigure(plot_kind="scatter", title="Density coloured samples")
    panel = figure.panel(x=[34.8, 35.2], y=[12.0, 20.0])

    with pytest.raises(ValueError, match="colorbar_label is required"):
        panel.scatter(color_values=[25.0, 26.0])
    with pytest.raises(TypeError, match="radius must be a real number"):
        panel.scatter(radius="large")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="color_domain must increase"):
        panel.scatter(
            color_values=[25.0, 26.0],
            color_domain=[30.0, 20.0],
            colorbar_label="Potential temperature (degC)",
        )

    panel.scatter(
        color_values=[25.0, 26.0],
        palette="viridis",
        radius=1.4,
        opacity=0.4,
        colorbar_label="Potential temperature (degC)",
    )
    layer = panel.layers[0]
    assert isinstance(layer["color"], str)
    assert layer["style"]["palette"] == "viridis"
    assert layer["style"]["radius"] == 1.4
    assert panel.payload["display"]["colorbar_label"] == "Potential temperature (degC)"


def test_expert_receives_scatter_and_field_api_from_runtime_signatures() -> None:
    layers = SCIENTIFIC_VIEW_API_CONTRACT["layers"]

    assert "color_values" in layers["scatter"]
    assert "colorbar_label" in layers["scatter"]
    assert "style" not in layers["scatter"]
    assert "width" in layers["contour_grid"]
    assert "style" not in layers["contour_grid"]
    assert "render" in layers["field2d"]
    assert "categories" in layers
    assert "# <-- MODIFY" in SCIENTIFIC_VIEW_API_CONTRACT["examples"]["ts_scatter"]


def test_save_registers_completed_view_with_hosting_runtime(tmp_path, monkeypatch) -> None:
    output_root = tmp_path / "outputs"
    manifest = tmp_path / "result-events.jsonl"
    monkeypatch.setenv("OCEAN_OUTPUT_DIR", str(output_root))
    monkeypatch.setenv("OCEAN_RESULT_MANIFEST", str(manifest))

    figure = ScientificFigure(
        plot_kind="profile",
        title="Observed profile",
        caption="A durable scientific result.",
        source_handle="source_1",
    )
    figure.panel(x=[34.8, 35.1], y=[0.0, 100.0]).line()
    output = figure.save("profile.nc")

    assert output == output_root / "profile.nc"
    events = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    event = events[0]
    assert event["schema_version"] == "ocean-result-event/v1"
    assert event["kind"] == "interactive_view"
    assert event["view_kind"] == "profile"
    assert event["title"] == "Observed profile"
    assert event["summary"] == "A durable scientific result."
    assert event["data_output"] == "profile.nc"
    assert event["source_handle"] == "source_1"
    assert event["view_type"] == "profile.line"
    assert "view_spec" not in event
    assert "data_schema" not in event
    assert hydrate_ocean_view_netcdf(output)["panels"][0]["layers"][0]["type"] == "line"

    candidates = candidate_outputs_from_execution(
        execution_id="codeexec_typed_profile",
        execution_result={
            "outputs": [
                {"name": "profile.nc", "bytes": 128, "sha256": "b" * 64},
            ],
            "discovered_results": ExpertCodeExecutionService._read_result_events(
                manifest, output_files=("profile.nc",)
            ),
        },
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.view_type == "profile.line"
    handoff = candidate.coordinator_payload()
    assert handoff == {
        "path": "outputs/profile.nc",
        "kind": "interactive_view",
        "title": "Observed profile",
        "summary": "A durable scientific result.",
        "sha256": "b" * 64,
        "view_type": "profile.line",
    }


def test_builder_inherits_spatial_context_from_the_framework_input_manifest(
    tmp_path, monkeypatch
) -> None:
    output_root = tmp_path / "outputs"
    manifest = tmp_path / "result-events.jsonl"
    input_manifest = tmp_path / "inputs.json"
    input_manifest.write_text(
        json.dumps(
            {
                "analysis_context": {
                    "sources": [
                        {
                            "handle": "source_1",
                            "spatial_context": {
                                "region_key": "dataset:gulf",
                                "bounds": [-98.5, 16.5, -77.0, 32.0],
                                "fit_policy": "region_change",
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OCEAN_OUTPUT_DIR", str(output_root))
    monkeypatch.setenv("OCEAN_RESULT_MANIFEST", str(manifest))
    monkeypatch.setenv("OCEAN_INPUT_MANIFEST", str(input_manifest))

    figure = ScientificFigure(
        plot_kind="profile",
        title="Area-mean profile",
        source_handle="source_1",
    )
    figure.panel(x=[27.0, 15.0], y=[0.0, 500.0], y_reverse=True).line()
    payload = hydrate_ocean_view_netcdf(figure.save("profile.nc"))

    assert payload["spatial_context"] == {
        "region_key": "dataset:gulf",
        "bounds": [-98.5, 16.5, -77.0, 32.0],
        "fit_policy": "region_change",
    }


def test_unified_figure_map_and_report_register_runtime_owned_results(
    tmp_path, monkeypatch
) -> None:
    output_root = tmp_path / "outputs"
    manifest = tmp_path / "result-events.jsonl"
    monkeypatch.setenv("OCEAN_OUTPUT_DIR", str(output_root))
    monkeypatch.setenv("OCEAN_RESULT_MANIFEST", str(manifest))

    figure = ScientificFigure(
        plot_kind="spatial_map",
        title="Sea-surface temperature",
        conclusions=("The western basin is warmer than the shelf.",),
        source_handle="source_1",
    )
    figure.panel(
        x=[-91.0, -90.0],
        y=[24.0, 25.0],
        x_label="Longitude",
        y_label="Latitude",
    ).field2d(
        [[27.0, 28.0], [26.0, 27.5]],
        variable="temperature",
        units="degC",
        colorbar_label="Temperature (degC)",
    )
    map_path = figure.save("sst.nc")
    report_path = output_root / "report.md"
    report_path.write_text("# Result\n", encoding="utf-8")
    publish_report(
        report_path,
        title="Analysis report",
        conclusions=("The mapped gradient is robust.",),
        attachments=("sst.nc",),
    )

    assert map_path.is_file()
    events = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert events[0]["data_output"] == "sst.nc"
    assert events[0]["variable"] == "temperature"
    assert events[0]["conclusions"] == ["The western basin is warmer than the shelf."]
    assert events[1]["report_output"] == "report.md"
    assert events[1]["attachment_outputs"] == ["sst.nc"]
    collected = ExpertCodeExecutionService._read_result_events(
        manifest, output_files=("sst.nc", "report.md")
    )
    assert len(collected) == 2
    assert collected[0]["data_output"] == "sst.nc"


def test_legacy_scientific_map_remains_read_compatible(tmp_path) -> None:
    path = ScientificMap(title="Legacy map").save(
        tmp_path / "legacy.nc",
        field=[[1.0, 2.0], [3.0, 4.0]],
        longitude=[120.0, 121.0],
        latitude=[20.0, 21.0],
        variable="temperature",
        units="degC",
    )

    assert path.is_file()


def test_spatial_materializer_keeps_grid_values_in_netcdf(tmp_path) -> None:
    source = tmp_path / "source.nc"
    import xarray as xr

    xr.Dataset(
        {
            "temperature": (
                ("latitude", "longitude"),
                np.asarray([[27.0, 28.0], [26.0, np.nan]]),
                {"units": "degC"},
            )
        },
        coords={"longitude": [-91.0, -90.0], "latitude": [24.0, 25.0]},
    ).to_netcdf(source, engine="h5netcdf")
    staging = tmp_path / "staging"
    staging.mkdir()

    _, manifest_path, dataset_path, _ = ExpertDeliverableService._prepare_spatial_view(
        field_path=source,
        staging=staging,
        variable="temperature",
        longitude_coordinate="longitude",
        latitude_coordinate="latitude",
        units="degC",
        colormap="viridis",
        colorbar_label="Temperature",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "ocean-interactive-spatial/v2"
    assert "values" not in manifest
    assert manifest["shape"] == [2, 2]
    hydrated = hydrate_spatial_manifest(manifest, dataset_path)
    assert hydrated["values"] == [[26.0, None], [27.0, 28.0]]


def test_publish_report_writes_a_framework_summary_when_the_file_is_missing(
    tmp_path, monkeypatch
) -> None:
    output_root = tmp_path / "outputs"
    monkeypatch.setenv("OCEAN_OUTPUT_DIR", str(output_root))

    report = publish_report(
        "summary.md",
        title="Temperature–salinity result",
        summary="Annual-mean structure.",
        conclusions=("The thermocline is shallow.",),
        checks=("Units verified.",),
        limitations=("Annual mean only.",),
    )

    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "# Temperature–salinity result" in text
    assert "## Conclusions" in text
    assert "- Units verified." in text
