"""Run the actual notebook renderer, including scientific layers beyond simple maps."""

import matplotlib

matplotlib.use("Agg")
import pytest
import xarray as xr

from oceanx.figure_reproduction import build_figure_reproduction_notebook


@pytest.fixture
def renderer():
    notebook, _ = build_figure_reproduction_notebook(request_id="req_test", sources=())
    namespace = {}
    exec(compile("".join(notebook["cells"][1]["source"]), "analysis.ipynb", "exec"), namespace)  # noqa: S102 - generated notebook under test
    yield namespace
    namespace["plt"].close("all")


@pytest.fixture
def data():
    return xr.Dataset(
        {
            "x": ("point", [1.0, 2.0, 3.0]),
            "y": ("point", [2.0, 3.0, 4.0]),
            "low": ("point", [1.0, 2.0, 3.0]),
            "high": ("point", [3.0, 4.0, 5.0]),
            "u": ("point", [1.0, 0.0, -1.0]),
            "v": ("point", [0.0, 1.0, 0.0]),
            "color": ("point", [100.0, 200.0, 300.0]),
            "category": ("point", ["A", "B", "A"]),
            "start": ("path", [0]),
            "count": ("path", [3]),
            "level": ("path", [27.0]),
        }
    )


@pytest.mark.parametrize(
    "layer",
    [
        {"type": "band", "x": "x", "y0": "low", "y1": "high"},
        {"type": "vector", "x": "x", "y": "y", "u": "u", "v": "v"},
        {"type": "categories", "x": "x", "y": "y", "category": "category", "show_labels": True},
        {
            "type": "contour",
            "path_data": {"x": "x", "y": "y", "start": "start", "count": "count", "level": "level"},
        },
    ],
)
def test_notebook_renders_every_scientific_layer(renderer, data, layer, tmp_path):
    fig, ax = renderer["plt"].subplots()
    before = len(ax.get_children())
    assert renderer["_render_layer"](ax, data, {}, layer) is not None
    assert len(ax.get_children()) > before
    fig.savefig(tmp_path / f"{layer['type']}.svg")


def test_notebook_preserves_axes_color_domain_and_custom_palette(renderer, data):
    _, ax = renderer["plt"].subplots()
    renderer["_configure_axis"](ax, {"scale": "log", "reverse": True}, dimension="x")
    assert ax.get_xscale() == "log"
    assert ax.xaxis_inverted()
    layer = {
        "type": "scatter",
        "x": "x",
        "y": "y",
        "color": "color",
        "color_domain": [10.0, 1000.0],
        "color_scale": "log",
        "style": {"palette": ["#000000", "#ffffff"], "marker": "square"},
    }
    artist = renderer["_render_layer"](ax, data, {}, layer)
    assert artist.get_clim() == (10.0, 1000.0)
    assert isinstance(artist.norm, matplotlib.colors.LogNorm)
    assert len(artist.get_offsets()) == 3


def test_notebook_renders_iso_dates_as_time_not_categories(renderer, data):
    data = data.assign(time=("point", ["2025-01-01", "2025-02-01", "2025-06-01"]))
    spec = {"_axes": {"x": {"field": "time", "scale": "time"}}}
    _, ax = renderer["plt"].subplots()
    line = renderer["_render_layer"](ax, data, spec, {"type": "line", "x": "time", "y": "y"})
    renderer["_configure_axis"](ax, spec["_axes"]["x"], dimension="x")
    dates = line.get_xdata(orig=False)
    assert dates[1] - dates[0] == 31
    assert dates[2] - dates[1] == 120


def test_notebook_rejects_unknown_layers_instead_of_silent_omission(renderer, data):
    _, ax = renderer["plt"].subplots()
    with pytest.raises(ValueError, match="Unsupported OceanMind layer"):
        renderer["_render_layer"](ax, data, {}, {"type": "unknown"})


def test_notebook_run_all_preserves_contour_and_band_from_netcdf(renderer, data, tmp_path):
    import json

    spec = {
        "view_kind": "scientific_figure",
        "title": "Test evidence",
        "data": {name: {"variable": name} for name in data},
        "panels": [
            {
                "axes": {"x": {"field": "x", "scale": "log"}, "y": {"field": "y", "reverse": True}},
                "layers": [
                    {"type": "line", "x": "x", "y": "y"},
                    {"type": "band", "x": "x", "y0": "low", "y1": "high"},
                    {
                        "type": "contour",
                        "path_data": {
                            "x": "x",
                            "y": "y",
                            "start": "start",
                            "count": "count",
                            "level": "level",
                        },
                    },
                ],
            }
        ],
    }
    data.attrs["ocean_view"] = json.dumps(spec)
    path = tmp_path / "data.nc"
    data.to_netcdf(path)
    fig = renderer["render_oceanmind_view"](path)
    ax = fig.axes[0]
    assert len(ax.lines) == 2
    assert len(ax.collections) == 1
    assert ax.get_xscale() == "log"
    assert ax.yaxis_inverted()
    renderer["save_publication_figure"](fig, tmp_path / "evidence")
    assert (tmp_path / "evidence.svg").stat().st_size > 0
