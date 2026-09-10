import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest

from oceanx.artifacts.models import ArtifactRef
from oceanx.expert_deliverables import (
    ExpertDeliverableError,
    ExpertDeliverableService,
    InvalidResultObjectsError,
    hydrate_ocean_view_netcdf,
    hydrate_scientific_manifest,
)
from oceanx.expert_execution import SCIENTIFIC_VIEW_API_CONTRACT
from oceanx.result_citations import canonical_result_citations
from oceanx.scientific_view import ScientificFigure, validate_result_features
from oceanx.task_results import TaskResultRecord


def test_features_survive_netcdf_with_exact_mask_and_discrete_labels(tmp_path):
    figure = ScientificFigure(plot_kind="spatial_map", title="Computed regions")
    panel = figure.panel(x=[120, 121, 122], y=[20, 21, 22])
    values = np.array([[1, 1, 2], [1, 2, 1], [2, 2, 2]])
    panel.field2d(
        values, units="1", field_kind="categorical", category_labels={1: "Group A", 2: "Group B"}
    )
    figure.add_feature(id="region_a", label="Region A", mask=values == 1)
    figure.add_feature(id="station", label="Station", point=(121, 21))
    payload = hydrate_ocean_view_netcdf(figure.save(tmp_path / "regions.nc"))
    assert payload["features"] == figure.features
    rings = payload["features"][0]["geometry"]["coordinates"]
    assert rings == [
        [[[119.5, 19.5], [121.5, 19.5], [121.5, 20.5], [119.5, 20.5], [119.5, 19.5]]],
        [[[119.5, 20.5], [120.5, 20.5], [120.5, 21.5], [119.5, 21.5], [119.5, 20.5]]],
        [[[121.5, 20.5], [122.5, 20.5], [122.5, 21.5], [121.5, 21.5], [121.5, 20.5]]],
    ]
    assert payload["categories"] == [
        {"value": 1, "label": "Group A"},
        {"value": 2, "label": "Group B"},
    ]
    assert payload["rendering"]["interpolation"] == "nearest"


def test_time_curve_and_interval_have_numeric_renderer_coordinates(tmp_path):
    figure = ScientificFigure(plot_kind="time_series", title="Temporal events")
    panel = figure.panel(x=["2022-01-01", "2022-02-01"], y=[2, 5])
    panel.line(layer_id="observed")
    figure.add_feature(id="curve", label="Observed", layer_id="observed")
    figure.add_feature(id="interval", label="Window", bounds=("2022-01-01", 1, "2022-02-01", 6))
    payload = hydrate_ocean_view_netcdf(figure.save(tmp_path / "series.nc"))
    first = payload["features"][0]
    assert first["geometry"] == {
        "type": "LineString",
        "coordinates": [[1640995200000, 2], [1643673600000, 5]],
    }
    assert payload["features"][1]["geometry"]["type"] == "Polygon"
    assert "panel_id" in SCIENTIFIC_VIEW_API_CONTRACT["add_feature"]
    assert "panel_id" in SCIENTIFIC_VIEW_API_CONTRACT["panel"]
    with pytest.raises(ValueError, match="unique"):
        panel.scatter(layer_id="observed")


def test_invalid_or_ambiguous_features_fail_before_publication(tmp_path):
    figure = ScientificFigure(plot_kind="scatter", title="Samples")
    panel = figure.panel(x=[1, 2], y=[3, 4])
    panel.scatter()
    figure.add_feature(id="sample", label="Sample", point=(1, 3))
    with pytest.raises(ValueError, match="unique"):
        figure.add_feature(id="sample", label="Duplicate", point=(2, 4))
    with pytest.raises(ValueError, match="aligned"):
        figure.add_feature(id="region", label="Region", mask=[[1, 0]])
    with pytest.raises(ValueError, match="finite"):
        figure.add_feature(id="bad", label="Bad", point=(np.nan, 3))
    with pytest.raises(ValueError, match="existing panel"):
        figure.add_feature(id="bad", label="Bad", panel_id="missing", point=(1, 3))
    output = figure.save(tmp_path / "samples.nc")
    payload = figure.payload(dataset_file=output.name)
    payload["features"][0]["panel_id"] = "missing"
    with pytest.raises(ExpertDeliverableError, match="existing panel"):
        hydrate_scientific_manifest(payload, output)
    figure.panel(x=[1, 2], y=[4, 5]).line()
    with pytest.raises(ValueError, match="exactly one"):
        figure.add_feature(id="ambiguous", label="Ambiguous", point=(1, 4))


def test_no_features_is_backwards_compatible(tmp_path):
    figure = ScientificFigure(plot_kind="scatter", title="Legacy")
    figure.panel(x=[1, 2], y=[3, 4]).scatter()
    assert hydrate_ocean_view_netcdf(figure.save(tmp_path / "legacy.nc"))["features"] == []


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Polygon", "coordinates": [[[1, 2], [3, 4], [4, 5]]]},
        {"type": "LineString", "coordinates": [[1, 2]]},
        {"type": "Point", "coordinates": [1, float("inf")]},
        {"type": "MultiPolygon", "coordinates": []},
    ],
)
def test_malformed_geometry_is_rejected(geometry):
    with pytest.raises(ValueError):
        validate_result_features([{"id": "a", "label": "A", "geometry": geometry}])


def record(result_id="view", **kwargs):
    return TaskResultRecord(
        ref={"task_id": "task", "result_id": result_id, "version": 1},
        workspace_id="workspace",
        kind="interactive_view",
        title="Actual figure",
        created_at="2026-09-10",
        execution_output_names=("analysis.nc",),
        content={
            "output_path": "outputs/analysis.nc",
            "features": [{"id": "a", "label": "Region A"}, {"id": "b", "label": "Region B"}],
        },
        **kwargs,
    )


def test_aliases_canonicalize_dedup_and_keep_distinct_objects():
    text = "[[output:analysis.nc|Wrong title]]\n\n- [[result:task/view@v1|Other title]]\n\n[[output:analysis.nc#a|Made-up label]]\n[[result:view@v0001#b]]"
    result = canonical_result_citations(text, [record()])
    assert result.count("[[result:task/view@v1|Actual figure]]") == 1
    assert "[[result:task/view@v1#a|Region A]]" in result
    assert "[[result:task/view@v1#b|Region B]]" in result
    assert "Wrong" not in result and "Made-up" not in result


def test_unknown_or_ambiguous_object_never_falls_back_to_whole_figure():
    assert (
        canonical_result_citations("[[output:analysis.nc#missing]]", [record()])
        == "[Result object unavailable: missing]"
    )
    assert (
        canonical_result_citations("[[output:analysis.nc#a]]", [record(), record("other")])
        == "[Result object unavailable: a]"
    )
    assert (
        canonical_result_citations("[[result:task/view@v1#a]]", [record(), record("other")])
        == "[[result:task/view@v1#a|Region A]]"
    )


def test_reference_examples_in_code_remain_untouched():
    for fence in ("```", "~~~", "`"):
        text = (
            f"{fence}\n[[output:analysis.nc#a]]\n[[output:analysis.nc#a]]\n{fence}"
            if len(fence) > 1
            else "`[[output:analysis.nc#a]]`"
        )
        assert canonical_result_citations(text, [record()]) == text
    json.dumps(validate_result_features([]))


@pytest.mark.parametrize("is_map", [True, False])
def test_publication_indexes_the_same_object_registry(tmp_path, monkeypatch, is_map):
    from oceanx import expert_deliverables as module

    figure = ScientificFigure(plot_kind="spatial_map" if is_map else "scatter", title="Binding")
    panel = figure.panel(x=[120, 121], y=[20, 21])
    if is_map:
        panel.field2d([[1, 2], [3, 4]], units="1")
    else:
        panel.scatter()
    figure.add_feature(id="a", label="Actual object", point=(120, 20))
    output = figure.save(tmp_path / "view.nc")
    stored = []

    def put(**kwargs):
        kwargs.pop("files")
        result = TaskResultRecord(
            ref={"task_id": "task", "result_id": "view", "version": 1},
            created_at="2026-09-10",
            **kwargs,
        )
        stored.append(result)
        return result

    service = ExpertDeliverableService(
        store=None, task_workspaces=None, task_results=SimpleNamespace(put=put)
    )
    monkeypatch.setattr(service, "_execution_output", lambda **_: output)
    monkeypatch.setattr(service, "_require_assigned_dataset", lambda **_: None)
    monkeypatch.setattr(module, "interactive_view_cache", lambda **_: None)
    args = {
        "workspace_id": "workspace",
        "task_id": "task",
        "work_order_id": "work",
        "execution_id": "execution",
        "title": "Binding",
        "summary": "",
        "dataset_ref": ArtifactRef(artifact_id="source", version=1),
        "origin_request_id": "request",
        "execution_output_names": ("view.nc",),
    }

    async def publish():
        if is_map:
            return await service.materialize_spatial_view(
                **args,
                field_output="view.nc",
                variable="field",
                longitude_coordinate="longitude",
                latitude_coordinate="latitude",
                units="1",
                colormap="viridis",
                colorbar_label=None,
                field_kind="continuous",
            )
        return await service.materialize_structured_view(
            **args,
            data_output="view.nc",
            preview_output=None,
            dataset_output=None,
            view_kind="scatter",
            interaction={},
        )

    result = asyncio.run(publish())
    assert result["result"]["content"]["features"] == [{"id": "a", "label": "Actual object"}]
    assert (
        canonical_result_citations("[[output:view.nc#a|Wrong]]", stored)
        == "[[result:task/view@v1#a|Actual object]]"
    )

    def invalid(_path):
        raise InvalidResultObjectsError("Invalid result objects: broken geometry")

    monkeypatch.setattr(module, "hydrate_ocean_view_netcdf", invalid)
    with pytest.raises(ExpertDeliverableError, match="broken geometry"):
        asyncio.run(publish())
    assert len(stored) == 1  # Never republish a stripped, whole-figure fallback.


def test_map_longitudes_are_normalized_and_dateline_crossings_are_explicit():
    figure = ScientificFigure(plot_kind="spatial_map", title="Longitude handling")
    figure.panel(x=[200, 201], y=[20, 21]).field2d([[1, 2], [3, 4]], units="1")
    figure.add_feature(id="a", label="A", point=(200, 20))
    assert figure.features[0]["geometry"]["coordinates"] == [-160, 20]
    with pytest.raises(ValueError, match="antimeridian"):
        figure.add_feature(id="crossing", label="Crossing", bounds=(179, 20, 181, 21))
