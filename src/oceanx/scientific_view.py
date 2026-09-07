"""Framework-owned builder for OceanMind interactive scientific figures.

Expert code supplies scientific arrays and meaning.  This module owns the
renderer schema and NumPy/xarray conversion.  One interactive view is persisted
as one NetCDF file: arrays are variables and the small renderer contract lives
in the ``ocean_view`` dataset attribute.  Expert code never constructs renderer
JSON or keeps a second data path in sync.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from numbers import Real
from pathlib import Path
from typing import Any, Literal

Scalar = int | float | str | None
AxisScale = Literal["linear", "log", "time", "category"]
FieldRender = Literal["filled_contour", "smooth", "cells"]
FieldInterpolation = Literal["linear", "nearest"]


def _finite_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _palette(value: str | Sequence[str]) -> str | list[str]:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("scatter palette cannot be empty")
        return value
    if not isinstance(value, Sequence) or not value:
        raise TypeError("scatter palette must be a name or a non-empty sequence of colours")
    colors = list(value)
    if not all(isinstance(color, str) and color.strip() for color in colors):
        raise TypeError("scatter palette colours must be non-empty strings")
    return colors


def _analysis_context_for(source_handle: str | None) -> dict[str, Any] | None:
    """Resolve framework-prepared source context without model-authored glue."""

    manifest_name = os.environ.get("OCEAN_INPUT_MANIFEST")
    if not manifest_name:
        return None
    try:
        payload = json.loads(Path(manifest_name).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    context = payload.get("analysis_context")
    if not isinstance(context, Mapping):
        return None
    sources = [source for source in context.get("sources", ()) if isinstance(source, Mapping)]
    if source_handle is None and len(sources) == 1:
        return dict(sources[0])
    for source in sources:
        if source.get("handle") == source_handle:
            return dict(source)
    return None


def _source_spatial_context(source_handle: str | None) -> dict[str, Any] | None:
    source = _analysis_context_for(source_handle)
    spatial = source.get("spatial_context") if source else None
    return dict(spatial) if isinstance(spatial, Mapping) else None


def _result_path(output: str | os.PathLike[str]) -> Path:
    path = Path(output)
    output_root = os.environ.get("OCEAN_OUTPUT_DIR")
    if not path.is_absolute() and output_root:
        path = Path(output_root) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _result_relative_path(path: Path) -> str:
    output_root_name = os.environ.get("OCEAN_OUTPUT_DIR")
    if not output_root_name:
        return path.name
    output_root = Path(output_root_name).resolve()
    try:
        return path.resolve().relative_to(output_root).as_posix()
    except ValueError as exc:
        raise ValueError("Scientific results must be saved inside OCEAN_OUTPUT_DIR") from exc


def _append_result_event(path: Path, event: Mapping[str, Any]) -> None:
    """Atomically register a framework-created result with the host runtime."""

    manifest_name = os.environ.get("OCEAN_RESULT_MANIFEST")
    output_root_name = os.environ.get("OCEAN_OUTPUT_DIR")
    if not manifest_name or not output_root_name:
        return
    output_root = Path(output_root_name).resolve()
    try:
        output_name = path.resolve().relative_to(output_root).as_posix()
    except ValueError as exc:
        raise ValueError("Scientific results must be saved inside OCEAN_OUTPUT_DIR") from exc
    payload = {
        "schema_version": "ocean-result-event/v1",
        **dict(event),
    }
    primary_field = "report_output" if payload.get("kind") == "report" else "data_output"
    payload[primary_field] = output_name
    manifest = Path(manifest_name)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")


def _scalar(value: Any) -> Scalar:
    if value is None:
        return None
    if getattr(getattr(value, "dtype", None), "kind", None) == "M":
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"Unsupported scientific value: {type(value).__name__}") from exc
    return numeric if math.isfinite(numeric) else None


def _python_values(values: Any) -> Any:
    raw = getattr(values, "values", values)
    if getattr(getattr(raw, "dtype", None), "kind", None) == "M":
        raw = raw.astype("datetime64[ms]").astype(str)
    tolist = getattr(raw, "tolist", None)
    if callable(tolist):
        raw = tolist()
    return raw


def _vector(values: Any, *, name: str) -> list[Scalar]:
    raw = _python_values(values)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise TypeError(f"{name} must be a one-dimensional array")
    if any(isinstance(item, Sequence) and not isinstance(item, (str, bytes)) for item in raw):
        raise ValueError(f"{name} must be one-dimensional")
    return [_scalar(item) for item in raw]


def _flat(values: Any) -> list[Scalar]:
    raw = _python_values(values)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return [_scalar(raw)]
    result: list[Scalar] = []
    for item in raw:
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            result.extend(_flat(item))
        else:
            result.append(_scalar(item))
    return result


def _bounded_style(style: Mapping[str, Any] | None, **defaults: Any) -> dict[str, Any]:
    allowed = {
        "color",
        "opacity",
        "width",
        "dash",
        "radius",
        "fill",
        "fill_opacity",
        "palette",
        "marker",
    }
    merged = {key: value for key, value in defaults.items() if value is not None}
    if style:
        unknown = set(style) - allowed
        if unknown:
            raise ValueError("Unsupported scientific style fields: " + ", ".join(sorted(unknown)))
        merged.update(style)
    cycle = {
        f"C{index}": color
        for index, color in enumerate(
            (
                "#0072B2",
                "#D55E00",
                "#009E73",
                "#CC79A7",
                "#E69F00",
                "#56B4E9",
                "#F0E442",
                "#000000",
                "#6B7280",
                "#8B5CF6",
            )
        )
    }
    for key in ("color", "fill"):
        value = merged.get(key)
        if isinstance(value, str) and value in cycle:
            merged[key] = cycle[value]
        elif isinstance(value, str):
            try:
                from matplotlib.colors import to_hex

                merged[key] = to_hex(value, keep_alpha=True)
            except (ImportError, ValueError) as exc:
                raise ValueError(f"Unsupported browser color: {value}") from exc
        elif value is not None:
            raise ValueError(
                f"{key} is a single browser color; pass numeric arrays through "
                "scatter(color_values=...)"
            )
    return merged


def _axis_values(values: Any, *, name: str, scale: AxisScale) -> list[Scalar]:
    vector = _vector(values, name=name)
    if scale == "time" and any(
        value is not None and not isinstance(value, str) for value in vector
    ):
        raise ValueError(
            f"{name} time coordinates must be datetime64, datetime, or ISO strings; "
            "integer epoch values are ambiguous"
        )
    return vector


_ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?$")


def _looks_like_time(values: Sequence[Scalar]) -> bool:
    present = [value for value in values if value is not None]
    return len(present) >= 2 and all(
        isinstance(value, str) and _ISO_TIME.fullmatch(value) for value in present
    )


class ScientificPanel:
    """One panel whose layers receive arrays directly rather than field names."""

    def __init__(
        self,
        figure: ScientificFigure,
        *,
        panel_id: str,
        x: Any,
        y: Any,
        title: str = "",
        subtitle: str = "",
        label: str = "",
        x_label: str = "",
        y_label: str = "",
        x_units: str = "",
        y_units: str = "",
        x_scale: AxisScale = "linear",
        y_scale: AxisScale = "linear",
        x_range: Sequence[float] | None = None,
        y_range: Sequence[float] | None = None,
        x_reverse: bool = False,
        y_reverse: bool = False,
        x_grid: bool = False,
        y_grid: bool = True,
        display: Mapping[str, Any] | None = None,
    ) -> None:
        self.figure = figure
        self.panel_id = panel_id
        x_values = _axis_values(x, name="x", scale=x_scale)
        y_values = _axis_values(y, name="y", scale=y_scale)
        effective_x_scale: AxisScale = (
            "time" if x_scale == "linear" and _looks_like_time(x_values) else x_scale
        )
        effective_y_scale: AxisScale = (
            "time" if y_scale == "linear" and _looks_like_time(y_values) else y_scale
        )
        self.x = figure._add_data(f"{panel_id}_x", x_values)
        self.y = figure._add_data(f"{panel_id}_y", y_values)
        self.layers: list[dict[str, Any]] = []
        self.payload: dict[str, Any] = {
            "id": panel_id,
            "axes": {
                "x": self._axis(
                    self.x, x_label, x_units, effective_x_scale, x_range, x_reverse, x_grid
                ),
                "y": self._axis(
                    self.y, y_label, y_units, effective_y_scale, y_range, y_reverse, y_grid
                ),
            },
            "layers": self.layers,
        }
        for key, value in (("title", title), ("subtitle", subtitle), ("label", label)):
            if value:
                self.payload[key] = value
        if display:
            self.payload["display"] = dict(display)

    @staticmethod
    def _axis(
        field: str,
        label: str,
        units: str,
        scale: AxisScale,
        axis_range: Sequence[float] | None,
        reverse: bool,
        grid: bool,
    ) -> dict[str, Any]:
        axis: dict[str, Any] = {"field": field, "scale": scale, "grid": grid}
        if scale == "time":
            axis["tick_format"] = "date"
        if label:
            axis["label"] = label
        if units:
            axis["units"] = units
        if axis_range is not None:
            if len(axis_range) != 2:
                raise ValueError("axis range must contain two values")
            axis["range"] = [float(axis_range[0]), float(axis_range[1])]
        if reverse:
            axis["reverse"] = True
        return axis

    def _xy(self, x: Any | None, y: Any | None, *, prefix: str) -> tuple[str, str]:
        x_field = (
            self.x
            if x is None
            else self.figure._add_data(f"{self.panel_id}_{prefix}_x", _vector(x, name="x"))
        )
        y_field = (
            self.y
            if y is None
            else self.figure._add_data(f"{self.panel_id}_{prefix}_y", _vector(y, name="y"))
        )
        if len(self.figure.data[x_field]) != len(self.figure.data[y_field]):
            raise ValueError(f"{prefix} x and y arrays must be aligned")
        return x_field, y_field

    def line(
        self,
        *,
        x: Any | None = None,
        y: Any | None = None,
        label: str = "",
        color: str | None = None,
        style: Mapping[str, Any] | None = None,
    ) -> ScientificPanel:
        index = len(self.layers) + 1
        x_field, y_field = self._xy(x, y, prefix=f"line{index}")
        if (
            sum(
                x_value is not None and y_value is not None
                for x_value, y_value in zip(self.figure.data[x_field], self.figure.data[y_field])
            )
            < 2
        ):
            raise ValueError("line requires at least two finite coordinate pairs")
        layer: dict[str, Any] = {"type": "line", "x": x_field, "y": y_field}
        if label:
            layer["label"] = label
        normalized_style = _bounded_style(style, color=color)
        if normalized_style:
            layer["style"] = normalized_style
        self.layers.append(layer)
        return self

    def scatter(
        self,
        *,
        x: Any | None = None,
        y: Any | None = None,
        color_values: Any | None = None,
        label: str = "",
        color: str | None = None,
        color_scale: Literal["linear", "log"] = "linear",
        color_domain: Sequence[float] | None = None,
        palette: str | Sequence[str] = "viridis",
        radius: float = 1.2,
        opacity: float = 0.25,
        marker: Literal["circle", "square", "triangle", "diamond"] = "circle",
        colorbar_label: str = "",
    ) -> ScientificPanel:
        """Add samples with explicit, runtime-validated visual parameters.

        ``colorbar_label`` is scientifically required whenever ``color_values``
        carries a third variable.  Keeping it on the scatter call makes the
        relationship visible in the generated API contract and avoids asking
        an Expert to guess a nested ``panel.display`` field.
        """

        if not isinstance(colorbar_label, str):
            raise TypeError("scatter colorbar_label must be a string")
        if color_scale not in {"linear", "log"}:
            raise ValueError("scatter color_scale must be 'linear' or 'log'")
        normalized_radius = _finite_number(radius, name="scatter radius")
        normalized_opacity = _finite_number(opacity, name="scatter opacity")
        normalized_palette = _palette(palette)
        if normalized_radius <= 0:
            raise ValueError("scatter radius must be positive")
        if not 0 <= normalized_opacity <= 1:
            raise ValueError("scatter opacity must be between 0 and 1")
        if marker not in {"circle", "square", "triangle", "diamond"}:
            raise ValueError("unsupported scatter marker")
        index = len(self.layers) + 1
        x_field, y_field = self._xy(x, y, prefix=f"scatter{index}")
        if not any(
            x_value is not None and y_value is not None
            for x_value, y_value in zip(self.figure.data[x_field], self.figure.data[y_field])
        ):
            raise ValueError("scatter requires at least one finite coordinate pair")
        layer: dict[str, Any] = {"type": "scatter", "x": x_field, "y": y_field}
        if color_values is not None:
            if not colorbar_label.strip():
                raise ValueError(
                    "scatter colorbar_label is required when color_values are provided"
                )
            values = _vector(color_values, name="color_values")
            if len(values) != len(self.figure.data[x_field]):
                raise ValueError("scatter color values must align with x and y")
            layer["color"] = self.figure._add_data(f"{self.panel_id}_scatter{index}_color", values)
            layer["color_scale"] = color_scale
            if color_domain is not None:
                if len(color_domain) != 2:
                    raise ValueError("scatter color_domain must contain two values")
                lower = _finite_number(color_domain[0], name="scatter color_domain lower bound")
                upper = _finite_number(color_domain[1], name="scatter color_domain upper bound")
                if lower >= upper:
                    raise ValueError("scatter color_domain must increase from lower to upper")
                layer["color_domain"] = [lower, upper]
            self.payload.setdefault("display", {})["colorbar_label"] = colorbar_label.strip()
        elif colorbar_label:
            raise ValueError("scatter colorbar_label requires color_values")
        if label:
            layer["label"] = label
        normalized_style = _bounded_style(
            {
                **({"palette": normalized_palette} if color_values is not None else {}),
                "radius": normalized_radius,
                "opacity": normalized_opacity,
                "marker": marker,
            },
            color=color,
        )
        if normalized_style:
            layer["style"] = normalized_style
        self.layers.append(layer)
        return self

    def heatmap(
        self,
        z: Any,
        *,
        label: str = "",
        palette: str | Sequence[str] = "viridis",
        color_scale: Literal["linear", "log"] = "linear",
        color_domain: Sequence[float] | None = None,
        colorbar_label: str = "",
        style: Mapping[str, Any] | None = None,
    ) -> ScientificPanel:
        """Compatibility alias for a continuous two-dimensional field.

        Older Expert notebooks called every matrix a heatmap.  The persisted
        grammar now records the scientific meaning instead: continuous fields
        render as filled contours by default.  Call :meth:`field2d` directly
        for categorical cells or an explicitly smooth surface.
        """

        return self.field2d(
            z,
            label=label,
            palette=palette,
            color_scale=color_scale,
            color_domain=color_domain,
            colorbar_label=colorbar_label,
            render="filled_contour",
            interpolation="linear",
            style=style,
        )

    def field2d(
        self,
        z: Any,
        *,
        label: str = "",
        variable: str = "",
        units: str = "",
        field_kind: Literal["continuous", "categorical"] = "continuous",
        palette: str | Sequence[str] = "viridis",
        color_scale: Literal["linear", "log"] = "linear",
        color_domain: Sequence[float] | None = None,
        colorbar_label: str = "",
        render: FieldRender = "filled_contour",
        interpolation: FieldInterpolation = "linear",
        levels: int | Sequence[float] = 14,
        style: Mapping[str, Any] | None = None,
    ) -> ScientificPanel:
        """Add one regular two-dimensional scientific field.

        The layer describes data semantics rather than a plotting-library
        primitive.  The Workbench owns interpolation, responsive rendering,
        colorbars, and interaction.
        """

        import numpy as np

        matrix = np.asarray(_python_values(z))
        expected_shape = (
            len(self.figure.data[self.y]),
            len(self.figure.data[self.x]),
        )
        if matrix.shape != expected_shape:
            raise ValueError(
                f"field2d z has shape {matrix.shape}; expected (len(y), len(x)) = {expected_shape}"
            )
        values = _flat(matrix)
        if not any(isinstance(value, (int, float)) for value in values):
            raise ValueError("field2d requires at least one finite value")
        if render not in {"filled_contour", "smooth", "cells"}:
            raise ValueError(f"Unsupported field2d render mode: {render}")
        if interpolation not in {"linear", "nearest"}:
            raise ValueError(f"Unsupported field2d interpolation: {interpolation}")
        if field_kind not in {"continuous", "categorical"}:
            raise ValueError(f"Unsupported field2d field kind: {field_kind}")
        if isinstance(levels, int):
            if levels < 3 or levels > 32:
                raise ValueError("field2d levels must be between 3 and 32")
            normalized_levels: int | list[float] = levels
        else:
            normalized_levels = [float(value) for value in levels]
            if len(normalized_levels) < 2 or len(normalized_levels) > 32:
                raise ValueError("field2d levels must contain two to thirty-two values")
        index = len(self.layers) + 1
        variable_name = re.sub(r"[^A-Za-z0-9_]", "_", variable.strip())
        if variable_name and variable_name[0].isdigit():
            variable_name = "value_" + variable_name
        z_field = self.figure._add_data(
            variable_name or f"{self.panel_id}_field{index}_z",
            values,
            shape=expected_shape,
            dims=(
                self.figure.data_specs[self.y]["dims"][0],
                self.figure.data_specs[self.x]["dims"][0],
            ),
        )
        self.figure.field_metadata[z_field] = {
            "variable": variable_name or z_field,
            "units": units.strip(),
            "field_kind": field_kind,
        }
        layer: dict[str, Any] = {
            "type": "field2d",
            "x": self.x,
            "y": self.y,
            "z": z_field,
            "color_scale": color_scale,
            "render": render,
            "interpolation": interpolation,
            "levels": normalized_levels,
            "style": _bounded_style(style, palette=palette),
        }
        if label:
            layer["label"] = label
        if color_domain is not None:
            layer["color_domain"] = [float(color_domain[0]), float(color_domain[1])]
        if colorbar_label:
            self.payload.setdefault("display", {})["colorbar_label"] = colorbar_label
        if not self.payload.get("title") and (label or colorbar_label):
            self.payload["title"] = label or colorbar_label
        self.layers.append(layer)
        return self

    def categories(
        self,
        category_values: Any,
        *,
        x: Any | None = None,
        y: Any | None = None,
        labels: Mapping[str | int | float, str] | None = None,
        label: str = "Categories",
        show_labels: bool = True,
        style: Mapping[str, Any] | None = None,
    ) -> ScientificPanel:
        """Add categorised samples without domain-specific plot branches."""

        index = len(self.layers) + 1
        x_field, y_field = self._xy(x, y, prefix=f"categories{index}")
        values = _vector(category_values, name="category_values")
        if len(values) != len(self.figure.data[x_field]):
            raise ValueError("category values must align with x and y")
        present = {str(value) for value in values if value is not None}
        if not present:
            raise ValueError("categories requires at least one non-null category")
        normalized_labels = {
            str(key): str(value).strip()
            for key, value in (labels or {}).items()
            if str(value).strip()
        }
        layer: dict[str, Any] = {
            "type": "categories",
            "x": x_field,
            "y": y_field,
            "category": self.figure._add_data(f"{self.panel_id}_categories{index}_value", values),
            "label": label,
            "show_labels": bool(show_labels),
            "style": _bounded_style(style, palette="categorical"),
        }
        if normalized_labels:
            layer["labels"] = normalized_labels
        self.layers.append(layer)
        return self

    def band(
        self,
        lower: Any,
        upper: Any,
        *,
        x: Any | None = None,
        label: str = "",
        color: str | None = None,
        style: Mapping[str, Any] | None = None,
    ) -> ScientificPanel:
        index = len(self.layers) + 1
        x_field = (
            self.x
            if x is None
            else self.figure._add_data(f"{self.panel_id}_band{index}_x", _vector(x, name="x"))
        )
        low = _vector(lower, name="lower")
        high = _vector(upper, name="upper")
        if len({len(self.figure.data[x_field]), len(low), len(high)}) != 1:
            raise ValueError("band arrays must be aligned")
        self.layers.append(
            {
                "type": "band",
                "x": x_field,
                "y0": self.figure._add_data(f"{self.panel_id}_band{index}_low", low),
                "y1": self.figure._add_data(f"{self.panel_id}_band{index}_high", high),
                **({"label": label} if label else {}),
                "style": _bounded_style(style, color=color, fill=color),
            }
        )
        return self

    def vector(
        self,
        u: Any,
        v: Any,
        *,
        x: Any | None = None,
        y: Any | None = None,
        label: str = "",
        color: str | None = None,
        scale: float | None = None,
        style: Mapping[str, Any] | None = None,
    ) -> ScientificPanel:
        index = len(self.layers) + 1
        x_field, y_field = self._xy(x, y, prefix=f"vector{index}")
        u_values = _vector(u, name="u")
        v_values = _vector(v, name="v")
        if len({len(self.figure.data[x_field]), len(u_values), len(v_values)}) != 1:
            raise ValueError("vector x, y, u, and v arrays must be aligned")
        layer: dict[str, Any] = {
            "type": "vector",
            "x": x_field,
            "y": y_field,
            "u": self.figure._add_data(f"{self.panel_id}_vector{index}_u", u_values),
            "v": self.figure._add_data(f"{self.panel_id}_vector{index}_v", v_values),
        }
        if label:
            layer["label"] = label
        if scale is not None:
            layer["scale"] = float(scale)
        normalized_style = _bounded_style(style, color=color)
        if normalized_style:
            layer["style"] = normalized_style
        self.layers.append(layer)
        return self

    def contour_paths(
        self,
        paths: Iterable[Mapping[str, Any]],
        *,
        label: str = "",
        color: str = "#7b858a",
        width: float = 1.0,
        opacity: float = 1.0,
        dash: str | None = None,
    ) -> ScientificPanel:
        normalized_width = _finite_number(width, name="contour width")
        normalized_opacity = _finite_number(opacity, name="contour opacity")
        if dash is not None and not isinstance(dash, str):
            raise TypeError("contour dash must be a string or None")
        if normalized_width <= 0:
            raise ValueError("contour width must be positive")
        if not 0 <= normalized_opacity <= 1:
            raise ValueError("contour opacity must be between 0 and 1")
        normalized: list[dict[str, Any]] = []
        for path in paths:
            points = [[float(x), float(y)] for x, y in path["points"]]
            if len(points) < 2:
                continue
            if len(points) > 1_000:
                stride = math.ceil(len(points) / 1_000)
                points = points[::stride]
            normalized.append(
                {
                    "level": float(path["level"]),
                    "points": points,
                    **({"label": str(path["label"])} if path.get("label") else {}),
                }
            )
            if len(normalized) == 128:
                break
        if not normalized:
            raise ValueError("contour_paths requires at least one path")
        # Contour coordinates are scientific arrays, not renderer metadata.
        # Store a compact ragged-array description in the figure contract and
        # let the NetCDF hydrator rebuild the renderer's ``paths`` objects.
        index = len(self.layers) + 1
        prefix = f"{self.panel_id}_contour{index}"
        flat_x: list[float] = []
        flat_y: list[float] = []
        starts: list[int] = []
        counts: list[int] = []
        levels: list[float] = []
        labels: list[str] = []
        for path in normalized:
            starts.append(len(flat_x))
            points = path["points"]
            counts.append(len(points))
            flat_x.extend(point[0] for point in points)
            flat_y.extend(point[1] for point in points)
            levels.append(float(path["level"]))
            labels.append(str(path.get("label", "")))
        point_dim = f"dim_{prefix}_point"
        path_dim = f"dim_{prefix}_path"
        self.layers.append(
            {
                "type": "contour",
                "path_data": {
                    "x": self.figure._add_data(f"{prefix}_x", flat_x, dims=(point_dim,)),
                    "y": self.figure._add_data(f"{prefix}_y", flat_y, dims=(point_dim,)),
                    "start": self.figure._add_data(
                        f"{prefix}_start", starts, dims=(path_dim,)
                    ),
                    "count": self.figure._add_data(
                        f"{prefix}_count", counts, dims=(path_dim,)
                    ),
                    "level": self.figure._add_data(
                        f"{prefix}_level", levels, dims=(path_dim,)
                    ),
                    "label": self.figure._add_data(
                        f"{prefix}_label", labels, dims=(path_dim,)
                    ),
                },
                **({"label": label} if label else {}),
                "style": _bounded_style(
                    {
                        "width": normalized_width,
                        "opacity": normalized_opacity,
                        **({"dash": dash} if dash else {}),
                    },
                    color=color,
                ),
            }
        )
        return self

    def contour_grid(
        self,
        z: Any,
        *,
        levels: Sequence[float],
        label: str = "",
        color: str = "#7b858a",
        width: float = 1.0,
        opacity: float = 1.0,
        dash: str | None = None,
    ) -> ScientificPanel:
        """Compute portable contour paths using Matplotlib's stable ``allsegs`` API."""

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        x_values = [float(value) for value in self.figure.data[self.x] if value is not None]
        y_values = [float(value) for value in self.figure.data[self.y] if value is not None]
        matrix = np.asarray(_python_values(z), dtype=float)
        if matrix.shape != (len(y_values), len(x_values)):
            raise ValueError("contour grid must have shape (len(y), len(x))")
        figure, axis = plt.subplots(figsize=(2, 2))
        try:
            contour_set = axis.contour(x_values, y_values, matrix, levels=levels)
            paths = [
                {"level": float(level), "points": segment.tolist()}
                for level, segments in zip(contour_set.levels, contour_set.allsegs)
                for segment in segments
                if len(segment) >= 2
            ]
        finally:
            plt.close(figure)
        return self.contour_paths(
            paths,
            label=label,
            color=color,
            width=width,
            opacity=opacity,
            dash=dash,
        )

    def annotations(
        self,
        items: Iterable[Mapping[str, Any]],
        *,
        color: str = "#273238",
        style: Mapping[str, Any] | None = None,
    ) -> ScientificPanel:
        normalized = []
        for item in items:
            normalized.append(
                {
                    key: value
                    for key, value in {
                        "x": float(item["x"]),
                        "y": float(item["y"]),
                        "text": str(item["text"]),
                        "dx": item.get("dx"),
                        "dy": item.get("dy"),
                        "arrow": item.get("arrow"),
                        "align": item.get("align"),
                    }.items()
                    if value is not None
                }
            )
        if not normalized:
            raise ValueError("annotations requires at least one item")
        self.layers.append(
            {"type": "annotation", "items": normalized, "style": _bounded_style(style, color=color)}
        )
        return self

    def reference(
        self,
        *,
        axis: Literal["x", "y"],
        value: float,
        label: str = "",
        color: str = "#7b858a",
        style: Mapping[str, Any] | None = None,
    ) -> ScientificPanel:
        self.layers.append(
            {
                "type": "reference",
                "axis": axis,
                "value": float(value),
                **({"label": label} if label else {}),
                "style": _bounded_style(style, color=color),
            }
        )
        return self


class ScientificFigure:
    """Generic Nature-leaning interactive figure composed from scientific arrays."""

    def __init__(
        self,
        *,
        plot_kind: str,
        title: str,
        subtitle: str = "",
        caption: str = "",
        columns: Literal[1, 2, 3] = 1,
        spatial_context: Mapping[str, Any] | None = None,
        source_handle: str | None = None,
        conclusions: Sequence[str] = (),
    ) -> None:
        self.plot_kind = plot_kind
        self.title = title
        self.subtitle = subtitle
        self.caption = caption
        self.columns = columns
        self.spatial_context = (
            dict(spatial_context) if spatial_context else _source_spatial_context(source_handle)
        )
        self.source_handle = source_handle
        self.conclusions = tuple(str(item).strip() for item in conclusions if str(item).strip())
        self.data: dict[str, list[Scalar]] = {}
        self.data_specs: dict[str, dict[str, Any]] = {}
        self.field_metadata: dict[str, dict[str, Any]] = {}
        self.panels: list[ScientificPanel] = []

    def _add_data(
        self,
        preferred: str,
        values: list[Scalar],
        *,
        shape: Sequence[int] | None = None,
        dims: Sequence[str] | None = None,
    ) -> str:
        normalized_shape = tuple(int(value) for value in (shape or (len(values),)))
        if math.prod(normalized_shape) != len(values):
            raise ValueError(f"{preferred} shape does not match its values")

        # Numeric arrays belong in the dataset once.  Reusing a vector across
        # panels is common for T-S samples, profiles and shared coordinates;
        # duplicating it bloats NetCDF, hydration time and browser memory.
        if shape is None and dims is None:
            for existing, existing_values in self.data.items():
                if existing_values == values and self.data_specs.get(existing, {}).get(
                    "shape"
                ) == list(normalized_shape):
                    return existing

        normalized_preferred = re.sub(r"[^A-Za-z0-9_]", "_", preferred)
        if not normalized_preferred or normalized_preferred[0].isdigit():
            normalized_preferred = "value_" + normalized_preferred
        name = normalized_preferred
        suffix = 2
        while name in self.data:
            name = f"{normalized_preferred}_{suffix}"
            suffix += 1
        self.data[name] = values
        normalized_dims = tuple(dims or (f"dim_{name}",))
        if len(normalized_dims) != len(normalized_shape):
            raise ValueError(f"{name} dims do not match its shape")
        self.data_specs[name] = {
            "variable": name,
            "dims": list(normalized_dims),
            "shape": list(normalized_shape),
        }
        return name

    def panel(
        self, *, x: Any, y: Any, panel_id: str | None = None, **kwargs: Any
    ) -> ScientificPanel:
        identifier = panel_id or f"panel_{len(self.panels) + 1}"
        if any(panel.panel_id == identifier for panel in self.panels):
            raise ValueError(f"Duplicate scientific panel id: {identifier}")
        panel = ScientificPanel(self, panel_id=identifier, x=x, y=y, **kwargs)
        self.panels.append(panel)
        return panel

    def payload(self, *, dataset_file: str = "data.nc") -> dict[str, Any]:
        if not self.panels or any(not panel.layers for panel in self.panels):
            raise ValueError("Every scientific figure panel requires at least one layer")
        payload: dict[str, Any] = {
            "schema_version": "ocean-scientific-figure/v4",
            "producer": "oceanmind-scientific-view-builder/v3",
            "plot_kind": self.plot_kind,
            "title": self.title,
            "dataset_file": dataset_file,
            "data": self.data_specs,
            "layout": {"columns": self.columns},
            "panels": [panel.payload for panel in self.panels],
        }
        if self.subtitle:
            payload["subtitle"] = self.subtitle
        if self.caption:
            payload["caption"] = self.caption
        if self.spatial_context:
            payload["spatial_context"] = self.spatial_context
        return payload

    def _view_type(self) -> str:
        """Return the declared scientific rendering type without inspecting values.

        ``plot_kind`` describes the scientific coordinate system (profile,
        section, T-S diagram, ...), while the primary layer describes the
        renderer (line, scatter, contourf, ...).  Keeping both prevents the
        Coordinator or Workbench from guessing a chart from array shape.
        """

        visual_layers = [
            layer
            for panel in self.panels
            for layer in panel.layers
            if layer.get("type") not in {"annotation", "reference", "contour"}
        ]
        layer_types = {str(layer.get("type")) for layer in visual_layers}
        if len(layer_types) != 1:
            renderer = "composite"
        else:
            layer = visual_layers[0]
            renderer = str(layer["type"])
            if renderer == "field2d":
                renderer = {
                    "filled_contour": "contourf",
                    "smooth": "surface",
                    "cells": "cells",
                }.get(str(layer.get("render")), "field2d")
        kind = {
            "spatial_map": "map",
            "ts_diagram": "ts",
            "hovmoller": "time_depth",
        }.get(self.plot_kind, self.plot_kind)
        return f"{kind}.{renderer}"

    def _data_schema(self, *, dataset_file: str) -> dict[str, Any]:
        """Describe external NetCDF arrays and their semantic rendering roles."""

        roles: dict[str, set[str]] = {name: set() for name in self.data_specs}
        units: dict[str, str] = {
            name: str(metadata.get("units") or "") for name, metadata in self.field_metadata.items()
        }
        for panel in self.panels:
            axes = panel.payload.get("axes", {})
            for channel in ("x", "y"):
                axis = axes.get(channel, {})
                field = axis.get("field")
                if isinstance(field, str) and field in roles:
                    roles[field].add(channel)
                    if axis.get("units"):
                        units.setdefault(field, str(axis["units"]))
            for layer in panel.layers:
                for channel in ("x", "y", "z", "color", "category", "u", "v", "y0", "y1"):
                    field = layer.get(channel)
                    if isinstance(field, str) and field in roles:
                        roles[field].add(channel)
        return {
            "schema_version": "ocean-view-data/v1",
            "format": "netcdf",
            "dataset_output": dataset_file,
            "variables": [
                {
                    "name": name,
                    "dims": list(spec["dims"]),
                    "shape": list(spec["shape"]),
                    "units": units.get(name, ""),
                    "roles": sorted(roles.get(name, ())),
                }
                for name, spec in self.data_specs.items()
            ],
        }

    def view_spec(self, *, dataset_file: str = "data.nc") -> dict[str, Any]:
        """Return compact renderer semantics; numeric arrays stay in NetCDF."""

        payload = self.payload(dataset_file=dataset_file)
        return {
            "schema_version": "ocean-interactive-view/v1",
            "type": self._view_type(),
            "plot_kind": self.plot_kind,
            "figure_schema": payload["schema_version"],
            "data": payload["data"],
            "layout": payload["layout"],
            "panels": payload["panels"],
            "interaction": {"tooltip": True, "zoom": True},
            **(
                {"spatial_context": payload["spatial_context"]}
                if "spatial_context" in payload
                else {}
            ),
        }

    def save(self, output: str | os.PathLike[str]) -> Path:
        if self.plot_kind == "spatial_map":
            return self._save_spatial_map(output)
        path = _result_path(output)
        if path.suffix.lower() != ".nc":
            path = path.with_suffix(".nc")
        dataset = self._dataset()
        figure_payload = self.payload(dataset_file=path.name)
        dataset.attrs.update(
            {
                "ocean_view_schema": "ocean-view-netcdf/v1",
                "ocean_view_type": self._view_type(),
                "ocean_view": json.dumps(
                    figure_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            dataset.to_netcdf(temporary, engine="h5netcdf")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        self._register_saved_result(path)
        return path

    def _save_spatial_map(self, output: str | os.PathLike[str]) -> Path:
        """Persist one regular geographic field through the unified figure API."""

        import numpy as np
        import xarray as xr

        if len(self.panels) != 1:
            raise ValueError("spatial_map requires exactly one panel")
        panel = self.panels[0]
        field_layers = [layer for layer in panel.layers if layer.get("type") == "field2d"]
        if len(panel.layers) != 1 or len(field_layers) != 1:
            raise ValueError("spatial_map requires exactly one field2d layer")
        layer = field_layers[0]
        longitude = np.asarray(self.data[str(layer["x"])], dtype=float)
        latitude = np.asarray(self.data[str(layer["y"])], dtype=float)
        z_field = str(layer["z"])
        field = np.asarray(
            [np.nan if value is None else float(value) for value in self.data[z_field]],
            dtype=float,
        ).reshape((latitude.size, longitude.size))
        normalized_longitude = ((longitude + 180.0) % 360.0) - 180.0
        longitude_order = np.argsort(normalized_longitude)
        latitude_order = np.argsort(latitude)[::-1]
        longitude = normalized_longitude[longitude_order]
        latitude = latitude[latitude_order]
        field = field[np.ix_(latitude_order, longitude_order)]
        if longitude.size < 2 or latitude.size < 2:
            raise ValueError("spatial_map requires at least two coordinates per axis")
        longitude_spacing = float(np.median(np.diff(longitude)))
        latitude_spacing = float(np.median(-np.diff(latitude)))
        if longitude_spacing <= 0 or latitude_spacing <= 0:
            raise ValueError("spatial_map coordinates must be strictly monotonic")
        metadata = self.field_metadata.get(z_field, {})
        variable = str(metadata.get("variable") or "field")
        units = str(metadata.get("units") or "").strip()
        if not units:
            raise ValueError("spatial_map field2d requires explicit units")
        path = _result_path(output)
        if path.suffix.lower() != ".nc":
            path = path.with_suffix(".nc")
        dataset = xr.DataArray(
            field,
            dims=("latitude", "longitude"),
            coords={"longitude": longitude, "latitude": latitude},
            name=variable,
            attrs={"units": units},
        ).to_dataset()
        palette = (layer.get("style") or {}).get("palette", "viridis")
        finite = field[np.isfinite(field)]
        if finite.size == 0:
            raise ValueError("spatial_map field requires at least one finite value")
        scale_min = float(finite.min())
        scale_max = float(finite.max())
        if scale_min == scale_max:
            epsilon = max(1e-12, abs(scale_min) * 1e-9)
            scale_min, scale_max = scale_min - epsilon, scale_max + epsilon
        bounds = [
            max(-180.0, float(longitude[0] - longitude_spacing / 2)),
            max(-90.0, float(latitude[-1] - latitude_spacing / 2)),
            min(180.0, float(longitude[-1] + longitude_spacing / 2)),
            min(90.0, float(latitude[0] + latitude_spacing / 2)),
        ]
        spatial_payload = {
            "schema_version": "ocean-interactive-spatial/v2",
            "view_kind": "spatial_map",
            "dataset_file": path.name,
            "variable": variable,
            "units": units,
            "longitude_coordinate": "longitude",
            "latitude_coordinate": "latitude",
            "shape": [int(latitude.size), int(longitude.size)],
            "bounds": bounds,
            "colorbar": {
                "label": panel.payload.get("display", {}).get("colorbar_label") or units,
                "colormap": palette if isinstance(palette, str) else "viridis",
                "levels": [
                    float(value) for value in np.linspace(scale_min, scale_max, 9)
                ],
            },
            "rendering": {
                "kind": metadata.get("field_kind", "continuous"),
                "interpolation": (
                    "nearest"
                    if metadata.get("field_kind") == "categorical"
                    else "linear"
                ),
            },
            "spatial_context": self.spatial_context
            or {
                "region_key": self.title,
                "bounds": bounds,
                "fit_policy": "region_change",
            },
        }
        dataset.attrs.update(
            {
                "ocean_view_schema": "ocean-view-netcdf/v1",
                "ocean_view_type": "map.field2d",
                "ocean_view": json.dumps(
                    spatial_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            dataset.to_netcdf(temporary, engine="h5netcdf")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        event: dict[str, Any] = {
            "kind": "interactive_view",
            "view_kind": "spatial_map",
            "title": self.title,
            "summary": self.caption or self.subtitle,
            "variable": variable,
            "longitude_coordinate": "longitude",
            "latitude_coordinate": "latitude",
            "units": units,
            "colormap": palette if isinstance(palette, str) else "viridis",
            "colorbar_label": (panel.payload.get("display", {}).get("colorbar_label") or units),
            "field_kind": metadata.get("field_kind", "continuous"),
            "view_type": "map.field2d",
        }
        if self.conclusions:
            event["conclusions"] = list(self.conclusions)
        if self.source_handle:
            event["source_handle"] = self.source_handle
        _append_result_event(path, event)
        return path

    def _dataset(self) -> Any:
        import numpy as np
        import xarray as xr

        variables: dict[str, Any] = {}
        for name, values in self.data.items():
            spec = self.data_specs[name]
            non_null = [value for value in values if value is not None]
            if all(isinstance(value, (int, float)) for value in non_null):
                array = np.asarray(
                    [np.nan if value is None else float(value) for value in values],
                    dtype=np.float64,
                )
            elif all(isinstance(value, str) for value in non_null):
                array = np.asarray(["" if value is None else value for value in values], dtype=str)
            else:
                raise ValueError(f"{name} mixes incompatible scientific value types")
            array = array.reshape(tuple(spec["shape"]))
            variables[name] = (tuple(spec["dims"]), array)
        return xr.Dataset(variables)

    def _register_saved_result(self, path: Path) -> None:
        """Tell the hosting runtime that a complete view now exists.

        The registration file is framework-owned and intentionally separate from
        the renderer payload.  In an OceanMind execution this makes ``save`` the
        atomic delivery boundary: a later exception or model/provider failure can
        no longer erase a figure that was already written successfully.  Outside
        that runtime (for example in a notebook or unit test) ``save`` remains an
        ordinary file write.
        """

        event: dict[str, Any] = {
            "kind": "interactive_view",
            "view_kind": self.plot_kind,
            "title": self.title,
            "summary": self.caption or self.subtitle,
            "view_type": self._view_type(),
        }
        if self.conclusions:
            event["conclusions"] = list(self.conclusions)
        if self.source_handle:
            event["source_handle"] = self.source_handle
        _append_result_event(path, event)


class ScientificMap:
    """Deprecated compatibility wrapper for pre-unified Expert programs.

    New analysis should use ``ScientificFigure(plot_kind="spatial_map")`` so
    every interactive result follows the same panel/layer API.
    """

    def __init__(
        self,
        *,
        title: str,
        summary: str = "",
        conclusions: Sequence[str] = (),
        source_handle: str | None = None,
        colormap: str = "viridis",
        colorbar_label: str = "",
        field_kind: Literal["continuous", "categorical"] = "continuous",
    ) -> None:
        self.title = title
        self.summary = summary
        self.conclusions = tuple(str(item).strip() for item in conclusions if str(item).strip())
        self.source_handle = source_handle
        self.colormap = colormap
        self.colorbar_label = colorbar_label
        self.field_kind = field_kind

    def save(
        self,
        output: str | os.PathLike[str],
        *,
        field: Any,
        longitude: Any,
        latitude: Any,
        variable: str,
        units: str = "",
    ) -> Path:
        import numpy as np
        import xarray as xr

        longitude_values = np.asarray(_python_values(longitude))
        latitude_values = np.asarray(_python_values(latitude))
        field_values = np.asarray(_python_values(field))
        if longitude_values.ndim != 1 or latitude_values.ndim != 1:
            raise ValueError("ScientificMap longitude and latitude must be one-dimensional")
        expected = (latitude_values.size, longitude_values.size)
        if field_values.shape != expected:
            transposed = (longitude_values.size, latitude_values.size)
            if field_values.shape == transposed and transposed != expected:
                field_values = field_values.T
            else:
                raise ValueError(
                    f"ScientificMap field has shape {field_values.shape}; expected {expected}"
                )
        variable_name = re.sub(r"[^A-Za-z0-9_]", "_", variable.strip())
        if not variable_name or variable_name[0].isdigit():
            variable_name = "value_" + variable_name
        data = xr.DataArray(
            field_values,
            dims=("latitude", "longitude"),
            coords={"longitude": longitude_values, "latitude": latitude_values},
            name=variable_name,
            attrs={"units": units},
        )
        path = _result_path(output)
        if path.suffix.lower() != ".nc":
            path = path.with_suffix(".nc")
        data.to_dataset().to_netcdf(path)
        event: dict[str, Any] = {
            "kind": "interactive_view",
            "view_kind": "spatial_map",
            "title": self.title,
            "summary": self.summary,
            "variable": variable_name,
            "longitude_coordinate": "longitude",
            "latitude_coordinate": "latitude",
            "units": units or None,
            "colormap": self.colormap,
            "colorbar_label": self.colorbar_label or units or None,
            "field_kind": self.field_kind,
            "view_spec": {
                "schema_version": "ocean-interactive-view/v1",
                "type": "map.contourf" if self.field_kind == "continuous" else "map.cells",
                "plot_kind": "spatial_map",
                "figure_schema": "ocean-scientific-figure/v4",
                "data": {
                    "longitude": {
                        "variable": "longitude",
                        "dims": ["longitude"],
                        "shape": [int(longitude_values.size)],
                    },
                    "latitude": {
                        "variable": "latitude",
                        "dims": ["latitude"],
                        "shape": [int(latitude_values.size)],
                    },
                    variable_name: {
                        "variable": variable_name,
                        "dims": ["latitude", "longitude"],
                        "shape": [int(latitude_values.size), int(longitude_values.size)],
                    },
                },
                "layout": {"columns": 1},
                "panels": [
                    {
                        "id": "panel_1",
                        "axes": {
                            "x": {"field": "longitude", "label": "Longitude"},
                            "y": {"field": "latitude", "label": "Latitude"},
                        },
                        "layers": [
                            {
                                "type": "field2d",
                                "x": "longitude",
                                "y": "latitude",
                                "z": variable_name,
                                "render": (
                                    "filled_contour" if self.field_kind == "continuous" else "cells"
                                ),
                                "interpolation": "linear",
                                "levels": 14,
                                "style": {"palette": self.colormap},
                            }
                        ],
                    }
                ],
                "interaction": {"tooltip": True, "zoom": True},
            },
            "data_schema": {
                "schema_version": "ocean-view-data/v1",
                "format": "netcdf",
                "dataset_output": _result_relative_path(path),
                "variables": [
                    {
                        "name": variable_name,
                        "dims": ["latitude", "longitude"],
                        "shape": [int(latitude_values.size), int(longitude_values.size)],
                        "units": units,
                        "roles": ["z"],
                    },
                    {
                        "name": "longitude",
                        "dims": ["longitude"],
                        "shape": [int(longitude_values.size)],
                        "units": "degrees_east",
                        "roles": ["x"],
                    },
                    {
                        "name": "latitude",
                        "dims": ["latitude"],
                        "shape": [int(latitude_values.size)],
                        "units": "degrees_north",
                        "roles": ["y"],
                    },
                ],
            },
        }
        if self.conclusions:
            event["conclusions"] = list(self.conclusions)
        if self.source_handle:
            event["source_handle"] = self.source_handle
        _append_result_event(path, event)
        return path


def publish_report(
    output: str | os.PathLike[str],
    *,
    title: str,
    summary: str = "",
    conclusions: Sequence[str] = (),
    attachments: Sequence[str] = (),
    checks: Sequence[str] = (),
    limitations: Sequence[str] = (),
) -> Path:
    """Read-compatible legacy report writer; Expert runtimes do not expose it."""

    path = _result_path(output)
    if not path.is_file():
        sections = [f"# {title}"]
        if summary.strip():
            sections.extend(("", summary.strip()))
        for heading, values in (
            ("Conclusions", conclusions),
            ("Checks performed", checks),
            ("Limitations", limitations),
        ):
            items = [str(item).strip() for item in values if str(item).strip()]
            if items:
                sections.extend(("", f"## {heading}", "", *(f"- {item}" for item in items)))
        path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    event: dict[str, Any] = {
        "kind": "report",
        "title": title,
        "summary": summary,
        "attachment_outputs": [str(item) for item in attachments],
        "report_checks": [str(item) for item in checks if str(item).strip()],
        "report_limitations": [str(item) for item in limitations if str(item).strip()],
    }
    normalized_conclusions = [str(item) for item in conclusions if str(item).strip()]
    if normalized_conclusions:
        event["conclusions"] = normalized_conclusions
    _append_result_event(path, event)
    return path


# ``publish_report`` remains importable only so historical executions and
# persisted result fixtures can still be read. New Expert sandboxes export the
# figure builders alone; Coordinator finalization owns report creation.
__all__ = ["ScientificFigure", "ScientificMap", "ScientificPanel"]
