"""Build one task-level notebook that re-renders accepted interactive results.

The scientific calculation has already finished when this module runs.  A
supplementary notebook therefore reads the immutable self-describing NetCDF
payloads in place and renders them with fixed templates; it neither duplicates
those payloads nor repeats an Expert's data preparation program.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FigureReproductionSource:
    result_ref: dict[str, Any]
    title: str
    summary: str
    view_kind: str
    data_reference: str


_FIXED_RENDERER_SOURCE = '''from pathlib import Path
import json
import math

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr


# Publication defaults. These are presentation settings only; the accepted data,
# axes and scientific colour domains remain unchanged.
FIGURE_WIDTH = 7.2          # <-- MODIFY: journal figure width in inches
BASE_FONT_SIZE = 9          # <-- MODIFY: readable at two-column publication width
SCATTER_SIZE_MIN = 10.0     # <-- MODIFY: minimum marker area (points squared)
CONTOUR_LEVELS = 24         # <-- MODIFY: smooth filled contours without inventing data

NATURE_COLORS = [
    "#0F4D92",  # deep blue
    "#42949E",  # teal
    "#8BCF8B",  # soft green
    "#9A4D8E",  # violet
    "#B64342",  # restrained red
    "#767676",  # neutral
]

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "Liberation Sans"],
    "font.size": BASE_FONT_SIZE,
    "axes.labelsize": BASE_FONT_SIZE,
    "axes.titlesize": BASE_FONT_SIZE + 1,
    "axes.titleweight": "bold",
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.prop_cycle": plt.cycler(color=NATURE_COLORS),
    "xtick.labelsize": BASE_FONT_SIZE - 1,
    "ytick.labelsize": BASE_FONT_SIZE - 1,
    "xtick.direction": "out",
    "ytick.direction": "out",
    "xtick.major.width": 0.7,
    "ytick.major.width": 0.7,
    "legend.frameon": False,
    "legend.fontsize": BASE_FONT_SIZE - 1,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "savefig.dpi": 300,
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
})


def save_publication_figure(fig, stem, formats=("svg", "pdf", "png"), dpi=300):
    """Save editable vector originals plus a high-resolution preview."""
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    saved = []
    for extension in formats:
        target = stem.with_suffix(f".{extension}")
        fig.savefig(target, dpi=dpi, bbox_inches="tight", facecolor="white")
        saved.append(target)
    return saved


def _style_axis(ax):
    ax.set_axisbelow(True)
    ax.tick_params(length=3.5, color="#4D4D4D", labelcolor="#272727")
    ax.spines["left"].set_color("#4D4D4D")
    ax.spines["bottom"].set_color("#4D4D4D")
    return ax


def _add_colorbar(fig, artist, ax, label):
    colorbar = fig.colorbar(artist, ax=ax, fraction=0.046, pad=0.025, aspect=28)
    colorbar.outline.set_visible(False)
    colorbar.ax.tick_params(length=2.5, width=0.6, labelsize=BASE_FONT_SIZE - 1)
    if label:
        colorbar.set_label(label, labelpad=6)
    return colorbar


def resolve_result_file(relative_path):
    """Resolve a result path when launched from the task or supplementary folder."""
    relative_path = Path(relative_path)
    candidates = (Path.cwd() / relative_path, Path.cwd() / "supplementary" / relative_path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Cannot find preserved result data: {relative_path}. "
        "Open this notebook from its OceanMind task directory."
    )


def _view_spec(dataset):
    raw = dataset.attrs.get("ocean_view")
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    raise ValueError("The NetCDF file does not contain an OceanMind view specification")


def _values(dataset, spec, field):
    descriptor = spec.get("data", {}).get(field, {})
    variable = descriptor.get("variable", field) if isinstance(descriptor, dict) else field
    if variable not in dataset:
        raise KeyError(f"View field {field!r} points to missing variable {variable!r}")
    return np.asarray(dataset[variable].values)


def _coordinates(dataset, spec, field, dimension):
    values = _values(dataset, spec, field)
    axis = spec.get("_axes", {}).get(dimension, {})
    if axis.get("scale") == "time":
        return mdates.date2num(values.astype("datetime64[ms]"))
    if axis.get("scale") == "category":
        labels = list(dict.fromkeys(str(value) for value in _values(dataset, spec, axis["field"])))
        return np.asarray([labels.index(str(value)) for value in values])
    return values


def _colormap(palette):
    palettes = {
        "depth": ["#f3edc9", "#cdddc8", "#93c4bd", "#5a9ea5", "#477992", "#455777", "#66516d"],
        "thermal": ["#243c62", "#47759a", "#82afb5", "#e8e2cd", "#da9a70", "#a94b42"],
        "haline": ["#f4f0e5", "#c6dcd5", "#83b9b2", "#47888e", "#27536d"],
        "diverging": ["#315f91", "#8cb5ca", "#f4f3ed", "#dda17d", "#9f443f"],
        "chlorophyll": ["#f1edc8", "#c8dda7", "#83b984", "#4b8c6c", "#285c56"],
        "default": ["#edf0d6", "#bdd7c1", "#7db5ad", "#518d98", "#4b6282", "#70566f"],
        "categorical": ["#154f70", "#bd6840", "#4a8e92", "#7775a7", "#9d7b36", "#5d7e68", "#a64f68", "#52719b"],
    }
    palettes["balance"] = palettes["diverging"]
    if isinstance(palette, str):
        palette = palettes.get(palette, palette)
    if isinstance(palette, (list, tuple)):
        return mpl.colors.LinearSegmentedColormap.from_list("oceanmind", palette)
    return mpl.colormaps[palette]


def _normalization(layer):
    domain = layer.get("color_domain")
    limits = {"vmin": float(domain[0]), "vmax": float(domain[1])} if domain else {}
    norm_type = mpl.colors.LogNorm if layer.get("color_scale") == "log" else mpl.colors.Normalize
    return norm_type(**limits)


def _linestyle(style):
    dash = style.get("dash")
    if not dash:
        return "-"
    if dash in {"solid", "dashed", "dotted", "dashdot", "-", "--", ":", "-."}:
        return dash
    return (0, tuple(float(value) for value in dash.replace(",", " ").split()))


def _axis_label(axis):
    label = str(axis.get("label") or "")
    units = str(axis.get("units") or "")
    return f"{label} ({units})" if label and units else label or units


def _configure_axis(ax, axis, *, dimension, values=None):
    label = _axis_label(axis)
    if dimension == "x":
        ax.set_xlabel(label)
    else:
        ax.set_ylabel(label)
    scale = axis.get("scale", "linear")
    if scale not in {"linear", "log", "time", "category"}:
        raise ValueError(f"Unsupported axis scale: {scale}")
    (ax.set_xscale if dimension == "x" else ax.set_yscale)("log" if scale == "log" else "linear")
    if scale == "time":
        (ax.xaxis_date if dimension == "x" else ax.yaxis_date)()
    if scale == "category" and values is not None:
        labels = list(dict.fromkeys(str(value) for value in values))
        (ax.set_xticks if dimension == "x" else ax.set_yticks)(range(len(labels)), labels)
    limits = axis.get("range")
    if isinstance(limits, list) and len(limits) == 2:
        setter = ax.set_xlim if dimension == "x" else ax.set_ylim
        if scale == "time":
            setter(*(np.datetime64(int(value), "ms") for value in limits))
        else:
            setter(float(limits[0]), float(limits[1]))
    inverted = ax.xaxis_inverted if dimension == "x" else ax.yaxis_inverted
    if axis.get("reverse") and not inverted():
        (ax.invert_xaxis if dimension == "x" else ax.invert_yaxis)()
    if axis.get("grid"):
        ax.grid(True, color="#D9E2E8", linewidth=0.55, alpha=0.65)


def _render_spatial_map(dataset, spec):
    variable = spec["variable"]
    longitude = np.asarray(dataset[spec["longitude_coordinate"]].values)
    latitude = np.asarray(dataset[spec["latitude_coordinate"]].values)
    field = np.asarray(dataset[variable].values)
    colorbar = spec.get("colorbar", {})
    levels = colorbar.get("levels")
    palette = _colormap(colorbar.get("colormap", "cividis"))

    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH, FIGURE_WIDTH * 0.62),
        constrained_layout=True,
    )
    if isinstance(levels, list) and len(levels) >= 3:
        artist = ax.contourf(
            longitude,
            latitude,
            field,
            levels=levels,
            cmap=palette,
            extend="both",
            antialiased=True,
        )
    else:
        artist = ax.pcolormesh(
            longitude,
            latitude,
            field,
            cmap=palette,
            shading="auto",
            rasterized=True,
        )
    _add_colorbar(fig, artist, ax, colorbar.get("label") or spec.get("units", ""))
    _style_axis(ax)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    finite_latitude = latitude[np.isfinite(latitude)]
    if finite_latitude.size:
        cosine = np.cos(np.deg2rad(float(np.nanmean(finite_latitude))))
        ax.set_aspect(1.0 / max(abs(cosine), 0.2), adjustable="box")
    ax.set_title(
        spec.get("title") or variable.replace("_", " ").title(),
        loc="left",
        pad=8,
    )
    return fig


def _render_layer(ax, dataset, spec, layer):
    layer_type = layer.get("type")
    style = layer.get("style") or {}
    if layer_type == "line":
        return ax.plot(
            _coordinates(dataset, spec, layer["x"], "x"),
            _coordinates(dataset, spec, layer["y"], "y"),
            color=style.get("color"),
            linewidth=float(style.get("width", 1.6)),
            solid_capstyle="round",
            label=layer.get("label"),
            alpha=float(style.get("opacity", 1.0)),
            linestyle=_linestyle(style),
        )[0]
    if layer_type == "scatter":
        color_field = layer.get("color")
        colors = _values(dataset, spec, color_field) if color_field else style.get("color")
        radius = float(style.get("radius", 2.0))
        scatter_kwargs = {
            "s": max(SCATTER_SIZE_MIN, radius * radius * 7.0),
            "alpha": float(style.get("opacity", 0.62)),
            "linewidths": 0,
            "rasterized": True,
            "marker": {"circle": "o", "square": "s", "triangle": "^", "diamond": "D"}.get(style.get("marker", "circle"), "o"),
            "label": layer.get("label"),
        }
        if color_field:
            scatter_kwargs.update({
                "c": colors,
                "cmap": _colormap(style.get("palette", "cividis")),
                "norm": _normalization(layer),
            })
        else:
            scatter_kwargs["color"] = style.get("color", NATURE_COLORS[0])
        return ax.scatter(
            _coordinates(dataset, spec, layer["x"], "x"),
            _coordinates(dataset, spec, layer["y"], "y"),
            **scatter_kwargs,
        )
    if layer_type in {"field2d", "heatmap"}:
        x = _coordinates(dataset, spec, layer["x"], "x")
        y = _coordinates(dataset, spec, layer["y"], "y")
        z = _values(dataset, spec, layer["z"])
        palette = _colormap(style.get("palette", "cividis"))
        limits = {"norm": _normalization(layer)}
        if layer.get("render") in {"filled_contour", "contourf"}:
            return ax.contourf(
                x,
                y,
                z,
                levels=layer.get("levels", CONTOUR_LEVELS),
                cmap=palette,
                extend="both",
                antialiased=True,
                **limits,
            )
        return ax.pcolormesh(
            x,
            y,
            z,
            cmap=palette,
            shading="auto",
            rasterized=True,
            **limits,
        )
    if layer_type == "band":
        return ax.fill_between(
            _coordinates(dataset, spec, layer["x"], "x"),
            _values(dataset, spec, layer["y0"]), _values(dataset, spec, layer["y1"]),
            color=style.get("fill") or style.get("color", NATURE_COLORS[0]),
            alpha=float(style.get("fill_opacity", style.get("opacity", 0.18))),
            label=layer.get("label"), linewidth=0,
        )
    if layer_type == "vector":
        u = _values(dataset, spec, layer["u"])
        v = _values(dataset, spec, layer["v"])
        # The interactive contract expresses vector scale as screen length per unit,
        # not as the reciprocal scale used by Matplotlib quiver.
        factor = layer.get("scale", 34.0 / max(float(np.nanmax(np.hypot(u, v))), 1e-12))
        return ax.quiver(
            _coordinates(dataset, spec, layer["x"], "x"),
            _coordinates(dataset, spec, layer["y"], "y"),
            u, -v if spec.get("_axes", {}).get("y", {}).get("reverse") else v,
            angles="uv", scale_units="dots", scale=1.0 / float(factor),
            color=style.get("color", NATURE_COLORS[0]),
            alpha=float(style.get("opacity", 1.0)), label=layer.get("label"),
        )
    if layer_type == "categories":
        categories = _values(dataset, spec, layer["category"])
        x = _coordinates(dataset, spec, layer["x"], "x")
        y = _coordinates(dataset, spec, layer["y"], "y")
        labels = layer.get("labels", {})
        palette = style.get("palette", "categorical")
        colors = palette if isinstance(palette, list) else [_colormap(palette)(i / 7) for i in range(8)]
        artists = []
        for index, category in enumerate(dict.fromkeys(str(value) for value in categories if value is not None and str(value) not in {"", "nan"})):
            mask = np.asarray([str(value) == category for value in categories])
            artists.append(ax.scatter(x[mask], y[mask], color=colors[index % len(colors)],
                s=max(SCATTER_SIZE_MIN, float(style.get("radius", 2)) ** 2 * 7),
                alpha=float(style.get("opacity", 1)), label=labels.get(category, category)))
            if layer.get("show_labels"):
                ax.annotate(labels.get(category, category),
                    (np.mean(x[mask]), np.mean(y[mask])), fontsize=BASE_FONT_SIZE)
        return artists
    if layer_type == "contour":
        paths = layer.get("paths", [])
        if layer.get("path_data"):
            fields = {key: _values(dataset, spec, field) for key, field in layer["path_data"].items()}
            paths = []
            for index, (start, count) in enumerate(zip(fields["start"], fields["count"])):
                section = slice(int(start), int(start + count))
                paths.append({"points": list(zip(fields["x"][section], fields["y"][section])),
                    "label": (str(fields["label"][index]) if "label" in fields else "") or str(fields["level"][index])})
        artists = []
        for path in paths:
            points = np.asarray(path["points"])
            line = ax.plot(points[:, 0], points[:, 1], color=style.get("color", "#7b858a"),
                linewidth=float(style.get("width", 1.0)), alpha=float(style.get("opacity", 1.0)),
                linestyle=_linestyle(style))[0]
            artists.append(line)
            if path.get("label"):
                middle = points[len(points) // 2]
                ax.annotate(str(path["label"]), middle, fontsize=BASE_FONT_SIZE - 1,
                    color=style.get("color", "#7b858a"))
        return artists
    if layer_type == "reference":
        value = float(layer["value"])
        color = style.get("color", "#767676")
        if layer.get("axis") == "x":
            return ax.axvline(value, color=color, linestyle="--", linewidth=0.9, alpha=0.8, label=layer.get("label"))
        return ax.axhline(value, color=color, linestyle="--", linewidth=0.9, alpha=0.8, label=layer.get("label"))
    if layer_type == "annotation":
        for item in layer.get("items", []):
            ax.annotate(
                str(item.get("text", "")),
                (float(item["x"]), float(item["y"])),
                fontsize=BASE_FONT_SIZE,
                fontweight="bold",
                color="#272727",
            )
        return None
    raise ValueError(f"Unsupported OceanMind layer: {layer_type!r}; figure rendering stopped")


def _render_scientific_figure(dataset, spec):
    panels = spec.get("panels") or []
    columns = max(1, min(int((spec.get("layout") or {}).get("columns", 1)), len(panels)))
    rows = math.ceil(len(panels) / columns)
    width = max(5.4, min(FIGURE_WIDTH, 3.5 * columns))
    height = max(3.8, 3.0 * rows)
    fig, axes = plt.subplots(
        rows,
        columns,
        figsize=(width, height),
        squeeze=False,
        constrained_layout=True,
    )
    flat_axes = list(axes.flat)

    for panel_index, (ax, panel) in enumerate(zip(flat_axes, panels)):
        axis_spec = panel.get("axes") or {}
        panel_spec = {**spec, "_axes": axis_spec}
        last_mappable = None
        has_label = False
        for layer in panel.get("layers") or []:
            artist = _render_layer(ax, dataset, panel_spec, layer)
            if (layer.get("type") in {"field2d", "heatmap"} or layer.get("type") == "scatter" and layer.get("color")) and artist is not None:
                last_mappable = artist
            has_label = has_label or bool(layer.get("label")) or layer.get("type") == "categories"
        for dimension in ("x", "y"):
            axis = axis_spec.get(dimension) or {}
            values = _values(dataset, spec, axis["field"]) if axis.get("field") else None
            _configure_axis(ax, axis, dimension=dimension, values=values)
        _style_axis(ax)
        if panel.get("title"):
            ax.set_title(panel["title"], loc="left", pad=7)
        ax.text(
            -0.08,
            1.04,
            chr(ord("a") + panel_index),
            transform=ax.transAxes,
            fontsize=BASE_FONT_SIZE + 1,
            fontweight="bold",
            ha="left",
            va="bottom",
        )
        if has_label:
            ax.legend(loc="best", handlelength=1.8, borderaxespad=0.4)
        display = panel.get("display") or {}
        if last_mappable is not None and display.get("colorbar_label"):
            _add_colorbar(fig, last_mappable, ax, display["colorbar_label"])

    for ax in flat_axes[len(panels):]:
        ax.set_visible(False)
    figure_title = str(spec.get("title") or "")
    if figure_title:
        fig.suptitle(
            figure_title,
            x=0.01,
            ha="left",
            fontsize=BASE_FONT_SIZE + 2,
            fontweight="bold",
            color="#272727",
        )
    fig.align_labels()
    return fig


def render_oceanmind_view(path):
    """Render one preserved OceanMind result without recomputing its data."""
    path = Path(path)
    with xr.open_dataset(path) as opened:
        dataset = opened.load()
    spec = _view_spec(dataset)
    if spec.get("view_kind") == "spatial_map":
        return _render_spatial_map(dataset, spec)
    return _render_scientific_figure(dataset, spec)
'''


def _cell_id(request_id: str, label: str) -> str:
    return hashlib.sha256(f"{request_id}:{label}".encode()).hexdigest()[:16]


def build_figure_reproduction_notebook(
    *,
    request_id: str,
    sources: tuple[FigureReproductionSource, ...],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return one task-level notebook and an index of referenced result data."""

    cells: list[dict[str, Any]] = [
        {
            "cell_type": "markdown",
            "id": _cell_id(request_id, "introduction"),
            "metadata": {},
            "source": [
                "# OceanMind analysis notebook\n",
                "\n",
                (
                    "This notebook re-renders every accepted interactive result directly "
                    "from the accepted NetCDF results already stored in this task. It does "
                    "**not** duplicate those datasets or recompute the scientific analysis. "
                    "You can edit the marked presentation constants below to change the "
                    "visualisation, and use `save_publication_figure(...)` for editable SVG, "
                    "PDF and high-resolution PNG exports.\n"
                ),
            ],
        },
    ]
    data_index: list[dict[str, Any]] = []
    result_data: dict[str, str] = {}
    for index, source in enumerate(sources, start=1):
        relative_path = source.data_reference
        data_key = f"figure_{index:02d}"
        result_data[data_key] = relative_path
        data_index.append(
            {
                "result_ref": source.result_ref,
                "title": source.title,
                "view_kind": source.view_kind,
                "key": data_key,
                "file": relative_path,
            }
        )
        cells.extend(
            [
                {
                    "cell_type": "markdown",
                    "id": _cell_id(request_id, f"figure-{index}-description"),
                    "metadata": {},
                    "source": [
                        f"## Figure {index}. {source.title}\n",
                        "\n",
                        f"{source.summary or 'Accepted OceanMind interactive result.'}\n",
                    ],
                },
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "id": _cell_id(request_id, f"figure-{index}-render"),
                    "metadata": {},
                    "outputs": [],
                    "source": [
                        f"render_oceanmind_view(resolve_result_file(RESULT_DATA[{data_key!r}]))\n"
                    ],
                },
            ]
        )

    renderer_source = (
        _FIXED_RENDERER_SOURCE
        + "\n\n# Accepted result files used by this analysis; no data are duplicated here.\n"
        + "RESULT_DATA = "
        + repr(result_data)
        + "\n"
    )
    cells.insert(
        1,
        {
            "cell_type": "code",
            "execution_count": None,
            "id": _cell_id(request_id, "fixed-renderer"),
            "metadata": {},
            "outputs": [],
            "source": renderer_source.splitlines(keepends=True),
        },
    )

    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
            "oceanmind": {
                "schema_version": "ocean-supplementary-analysis-notebook/v1",
                "request_id": request_id,
                "data_files": data_index,
                "renderer": "nature-python-templates/v3",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return notebook, data_index


__all__ = ["FigureReproductionSource", "build_figure_reproduction_notebook"]
