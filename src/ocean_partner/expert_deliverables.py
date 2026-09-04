"""Publish user-facing results directly from Expert-owned code executions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from ocean_partner.artifacts.models import ArtifactRef
from ocean_partner.backend.store import RequestStore
from ocean_partner.task_results import TaskResultError, TaskResultRecord, TaskResultStore
from ocean_partner.task_workspace import TaskWorkspaceProjector

MAX_RENDERED_SCIENTIFIC_VALUES = 500_000


class ExpertDeliverableError(RuntimeError):
    """An Expert result cannot be safely materialized as a user-facing deliverable."""


def interactive_view_cache(
    *,
    record: TaskResultRecord,
    results: TaskResultStore,
    hydrated_payload: dict[str, Any] | None = None,
) -> tuple[Path, bytes]:
    """Return one immutable renderer cache outside the user-facing task files.

    The Expert already hydrates and validates a scientific manifest before it
    becomes a result. Passing that payload here avoids repeating NetCDF I/O
    when the user first opens the view. Older results are hydrated lazily once.
    """

    data_file = record.content.get("data_file")
    if not isinstance(data_file, str):
        raise ExpertDeliverableError("Interactive result manifest is missing")
    manifest_path = results.file_path(ref=record.ref, relative_path=data_file)
    if manifest_path.suffix.lower() == ".json" and manifest_path.stat().st_size > 5 * 1024 * 1024:
        raise ExpertDeliverableError("Interactive result manifest exceeds its bounded size")
    dataset_file = record.content.get("dataset_file")
    manifest_record = next((item for item in record.files if item.path == data_file), None)
    dataset_record = next(
        (
            item
            for item in record.files
            if isinstance(dataset_file, str) and item.path == dataset_file
        ),
        None,
    )
    identity = hashlib.sha256(
        "\x1f".join(
            (
                "interactive-view-cache/v2",
                record.ref.key,
                manifest_record.sha256 if manifest_record is not None else "",
                dataset_record.sha256 if dataset_record is not None else "",
            )
        ).encode("utf-8")
    ).hexdigest()
    cache_root = results.task_workspaces.paths.cache / "interactive-views"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / f"{identity}.json"
    if not cache_path.exists():
        payload = hydrated_payload
        if payload is None:
            if manifest_path.suffix.lower() == ".nc":
                payload = hydrate_ocean_view_netcdf(manifest_path)
            else:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                schema = payload.get("schema_version") if isinstance(payload, dict) else None
                if schema in {
                    "ocean-scientific-figure/v3",
                    "ocean-scientific-figure/v4",
                    "ocean-interactive-spatial/v2",
                }:
                    if not isinstance(dataset_file, str):
                        raise ExpertDeliverableError("Interactive result NetCDF data is missing")
                    dataset_path = results.file_path(ref=record.ref, relative_path=dataset_file)
                    payload = (
                        hydrate_scientific_manifest(payload, dataset_path)
                        if schema in {"ocean-scientific-figure/v3", "ocean-scientific-figure/v4"}
                        else hydrate_spatial_manifest(payload, dataset_path)
                    )
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > 25 * 1024 * 1024:
            raise ExpertDeliverableError("Interactive result exceeds the renderer resource limit")
        temporary = cache_path.with_name(f".{cache_path.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(encoded)
        os.chmod(temporary, 0o600)
        os.replace(temporary, cache_path)
    raw = cache_path.read_bytes()
    if len(raw) > 25 * 1024 * 1024:
        raise ExpertDeliverableError("Interactive result exceeds the renderer resource limit")
    return cache_path, raw


def hydrate_scientific_manifest(payload: Any, dataset_path: Path) -> dict[str, Any]:
    """Resolve one small figure manifest against its immutable NetCDF arrays.

    The persisted result keeps scientific arrays in NetCDF.  Hydration is only
    a viewer boundary: it returns the bounded row-major values expected by the
    renderer without creating another result file or asking an Expert to
    transcribe shapes into JSON.
    """

    if not isinstance(payload, dict) or payload.get("schema_version") not in {
        "ocean-scientific-figure/v3",
        "ocean-scientific-figure/v4",
    }:
        raise ExpertDeliverableError("Scientific figure manifest is incompatible")
    descriptors = payload.get("data")
    if not isinstance(descriptors, dict) or not descriptors:
        raise ExpertDeliverableError("Scientific figure manifest requires NetCDF variables")
    for panel in payload.get("panels", ()):
        if not isinstance(panel, dict):
            continue
        for layer in panel.get("layers", ()):
            if not isinstance(layer, dict) or layer.get("type") not in {
                "heatmap",
                "field2d",
            }:
                continue
            try:
                x_spec = descriptors[layer["x"]]
                y_spec = descriptors[layer["y"]]
                z_spec = descriptors[layer["z"]]
                expected_shape = [y_spec["shape"][0], x_spec["shape"][0]]
                expected_dims = [y_spec["dims"][0], x_spec["dims"][0]]
            except (KeyError, IndexError, TypeError) as exc:
                raise ExpertDeliverableError(
                    "Scientific figure field2d references invalid NetCDF dimensions"
                ) from exc
            if z_spec.get("shape") != expected_shape or z_spec.get("dims") != expected_dims:
                raise ExpertDeliverableError("Scientific figure field2d must declare z as (y, x)")
    try:
        import numpy as np
        import xarray as xr
    except ImportError as exc:
        raise ExpertDeliverableError(
            "Scientific figure hydration requires the installed Ocean runtime"
        ) from exc
    try:
        source = xr.open_dataset(dataset_path, decode_cf=True, mask_and_scale=True)
    except Exception as exc:
        raise ExpertDeliverableError("Scientific figure NetCDF data is unreadable") from exc
    try:
        resolved: dict[str, list[Any]] = {}
        total = 0
        for field, descriptor in descriptors.items():
            if (
                not isinstance(field, str)
                or not isinstance(descriptor, dict)
                or descriptor.get("variable") != field
            ):
                raise ExpertDeliverableError("Scientific figure variable descriptor is invalid")
            variable = source.get(field)
            if variable is None:
                raise ExpertDeliverableError(
                    f"Scientific figure NetCDF is missing variable {field!r}"
                )
            declared_dims = descriptor.get("dims")
            declared_shape = descriptor.get("shape")
            if list(variable.dims) != declared_dims or list(variable.shape) != declared_shape:
                raise ExpertDeliverableError(
                    f"Scientific figure variable {field!r} does not match its declared dims and shape"
                )
            values = np.asarray(variable.values).reshape(-1)
            total += int(values.size)
            # The renderer receives these arrays through a granted cache file,
            # not through the 1 MiB Protocol V2 event frame.  Keep a real bound
            # for browser memory while allowing publication-scale regular grids
            # (for example a 600 x 660 T-S density field).
            if total > MAX_RENDERED_SCIENTIFIC_VALUES:
                raise ExpertDeliverableError(
                    "Scientific figure exceeds the rendered point allowance"
                )
            if np.issubdtype(values.dtype, np.datetime64):
                resolved[field] = [
                    None if np.isnat(value) else str(np.datetime_as_string(value, unit="ms"))
                    for value in values
                ]
            elif np.issubdtype(values.dtype, np.number):
                resolved[field] = [float(value) if np.isfinite(value) else None for value in values]
            else:
                resolved[field] = [
                    None if value is None else str(value) for value in values.tolist()
                ]
        hydrated = dict(payload)
        hydrated["data"] = resolved
        panels: list[Any] = []
        for panel in hydrated.get("panels", ()):
            if not isinstance(panel, dict):
                panels.append(panel)
                continue
            layers: list[Any] = []
            for layer in panel.get("layers", ()):
                if not isinstance(layer, dict) or layer.get("type") != "contour":
                    layers.append(layer)
                    continue
                path_data = layer.get("path_data")
                if not isinstance(path_data, dict):
                    layers.append(layer)
                    continue
                try:
                    x_values = resolved[path_data["x"]]
                    y_values = resolved[path_data["y"]]
                    starts = resolved[path_data["start"]]
                    counts = resolved[path_data["count"]]
                    levels = resolved[path_data["level"]]
                    labels = resolved[path_data["label"]]
                except (KeyError, TypeError) as exc:
                    raise ExpertDeliverableError("Contour path data is incomplete") from exc
                paths: list[dict[str, Any]] = []
                for start, count, level, label in zip(starts, counts, levels, labels):
                    offset = int(start)
                    length = int(count)
                    path = {
                        "level": float(level),
                        "points": [
                            [float(x), float(y)]
                            for x, y in zip(
                                x_values[offset : offset + length],
                                y_values[offset : offset + length],
                            )
                        ],
                    }
                    if label:
                        path["label"] = str(label)
                    paths.append(path)
                layers.append({key: value for key, value in layer.items() if key != "path_data"} | {"paths": paths})
            panels.append({**panel, "layers": layers})
        hydrated["panels"] = panels
        return hydrated
    finally:
        source.close()


def hydrate_spatial_manifest(payload: Any, dataset_path: Path) -> dict[str, Any]:
    """Resolve a compact spatial manifest against its regular-grid NetCDF field."""

    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "ocean-interactive-spatial/v2"
    ):
        raise ExpertDeliverableError("Spatial view manifest is incompatible")
    variable_name = payload.get("variable")
    longitude_name = payload.get("longitude_coordinate")
    latitude_name = payload.get("latitude_coordinate")
    if not all(
        isinstance(name, str) and name for name in (variable_name, longitude_name, latitude_name)
    ):
        raise ExpertDeliverableError("Spatial view manifest coordinates are invalid")
    try:
        import numpy as np
        import xarray as xr

        source = xr.open_dataset(dataset_path, decode_cf=True, mask_and_scale=True)
    except Exception as exc:
        raise ExpertDeliverableError("Spatial view NetCDF data is unreadable") from exc
    try:
        if (
            variable_name not in source
            or longitude_name not in source.coords
            or latitude_name not in source.coords
        ):
            raise ExpertDeliverableError("Spatial view NetCDF variables are incomplete")
        field = source[variable_name].transpose(latitude_name, longitude_name)
        values = np.asarray(field.values, dtype=float)
        if list(values.shape) != payload.get("shape"):
            raise ExpertDeliverableError("Spatial view field does not match its declared shape")
        if values.size > 200_000:
            raise ExpertDeliverableError("Spatial view exceeds the rendered cell allowance")
        hydrated = dict(payload)
        hydrated.update(
            {
                "schema_version": "ocean-interactive-spatial/v1",
                "longitude": [float(value) for value in source.coords[longitude_name].values],
                "latitude": [float(value) for value in source.coords[latitude_name].values],
                "values": [
                    [float(value) if np.isfinite(value) else None for value in row]
                    for row in values
                ],
            }
        )
        return hydrated
    finally:
        source.close()


def hydrate_ocean_view_netcdf(dataset_path: Path) -> dict[str, Any]:
    """Load the renderer contract and arrays from one self-describing NetCDF."""

    try:
        import xarray as xr

        source = xr.open_dataset(dataset_path, decode_cf=False, mask_and_scale=False)
    except Exception as exc:
        raise ExpertDeliverableError("Interactive view NetCDF is unreadable") from exc
    try:
        if source.attrs.get("ocean_view_schema") != "ocean-view-netcdf/v1":
            raise ExpertDeliverableError("NetCDF does not declare an OceanMind view")
        raw = source.attrs.get("ocean_view")
        if not isinstance(raw, str):
            raise ExpertDeliverableError("NetCDF is missing its OceanMind view contract")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExpertDeliverableError("NetCDF OceanMind view contract is invalid") from exc
    finally:
        source.close()
    schema = payload.get("schema_version") if isinstance(payload, dict) else None
    if schema in {"ocean-scientific-figure/v3", "ocean-scientific-figure/v4"}:
        return hydrate_scientific_manifest(payload, dataset_path)
    if schema == "ocean-interactive-spatial/v2":
        return hydrate_spatial_manifest(payload, dataset_path)
    raise ExpertDeliverableError("NetCDF OceanMind view contract is unsupported")


class ExpertDeliverableService:
    """Materialize checked Expert outputs as an interactive view or report."""

    def __init__(
        self,
        *,
        store: RequestStore,
        task_workspaces: TaskWorkspaceProjector,
        task_results: TaskResultStore,
    ) -> None:
        self.store = store
        self.task_workspaces = task_workspaces
        self.results = task_results

    async def materialize_spatial_view(
        self,
        *,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        execution_id: str,
        title: str,
        summary: str,
        field_output: str,
        dataset_ref: ArtifactRef,
        variable: str,
        longitude_coordinate: str,
        latitude_coordinate: str,
        units: str | None,
        colormap: str,
        colorbar_label: str | None,
        field_kind: str,
        origin_request_id: str | None,
        execution_output_names: tuple[str, ...],
        presentation: dict[str, Any] | None = None,
        materialization_key: str | None = None,
    ) -> dict[str, Any]:
        field_path = self._execution_output(
            workspace_id=workspace_id,
            task_id=task_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            output_name=field_output,
        )
        if field_path.suffix.lower() != ".nc":
            raise ExpertDeliverableError("Interactive view field must be a NetCDF output")
        self._require_assigned_dataset(
            workspace_id=workspace_id,
            work_order_id=work_order_id,
            dataset_ref=dataset_ref,
        )

        # Current views are self-describing: publication indexes the existing
        # NetCDF instead of generating a JSON manifest and a second NetCDF.
        try:
            hydrated_payload = hydrate_ocean_view_netcdf(field_path)
        except ExpertDeliverableError:
            hydrated_payload = None
        if hydrated_payload is not None:
            if hydrated_payload.get("schema_version") != "ocean-interactive-spatial/v1":
                raise ExpertDeliverableError("Spatial NetCDF declares a non-map view")
            files: dict[str, Path | bytes] = {"data.nc": field_path}
            record = self.results.put(
                workspace_id=workspace_id,
                task_id=task_id,
                kind="interactive_view",
                title=title,
                summary=summary,
                content={
                    "view_kind": "spatial_map",
                    "output_path": f"outputs/{field_output}",
                    "data_file": "data.nc",
                    "dataset_file": "data.nc",
                    "preview_file": None,
                    "render_status": "interactive",
                    "render_message": None,
                    "interaction": {
                        "hover_value": True,
                        "pan_zoom": True,
                        "coordinate_readout": True,
                    },
                    "metadata": {
                        "variable": hydrated_payload.get("variable"),
                        "units": hydrated_payload.get("units"),
                        "bounds": hydrated_payload.get("bounds"),
                    },
                    "presentation": presentation or {},
                },
                files=files,
                source_refs=(dataset_ref.model_dump(mode="json"),),
                origin_request_id=origin_request_id,
                work_order_id=work_order_id,
                execution_id=execution_id,
                execution_output_names=execution_output_names,
                materialization_key=materialization_key,
            )
            try:
                interactive_view_cache(
                    record=record,
                    results=self.results,
                    hydrated_payload=hydrated_payload,
                )
            except (OSError, ValueError, TaskResultError, ExpertDeliverableError):
                pass
            return {
                "kind": "interactive_view",
                "render_status": "interactive",
                "render_message": None,
                "result_ref": record.ref.model_dump(mode="json"),
                "result": record.as_payload(),
            }

        with ExitStack() as stack:
            staging = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="interactive-view-",
                        dir=field_path.parent,
                    )
                )
            )
            metadata, view_data, view_dataset, overlay = self._prepare_spatial_view(
                field_path=field_path,
                staging=staging,
                variable=variable,
                longitude_coordinate=longitude_coordinate,
                latitude_coordinate=latitude_coordinate,
                units=units,
                colormap=colormap,
                colorbar_label=colorbar_label,
                field_kind=field_kind,
            )
            hydrated_payload = hydrate_spatial_manifest(
                json.loads(view_data.read_text(encoding="utf-8")), view_dataset
            )
            view_files: dict[str, Path] = {
                "view.json": view_data,
                "data.nc": view_dataset,
                "preview.png": overlay,
            }
            record = self.results.put(
                workspace_id=workspace_id,
                task_id=task_id,
                kind="interactive_view",
                title=title,
                summary=summary,
                content={
                    "view_kind": "spatial_map",
                    "data_file": "view.json",
                    "dataset_file": "data.nc",
                    "preview_file": "preview.png",
                    "interaction": {
                        "hover_value": True,
                        "pan_zoom": True,
                        "coordinate_readout": True,
                    },
                    "metadata": metadata,
                    "presentation": presentation or {},
                },
                files=view_files,
                source_refs=(dataset_ref.model_dump(mode="json"),),
                origin_request_id=origin_request_id,
                work_order_id=work_order_id,
                execution_id=execution_id,
                execution_output_names=execution_output_names,
                materialization_key=materialization_key,
            )
            try:
                interactive_view_cache(
                    record=record,
                    results=self.results,
                    hydrated_payload=hydrated_payload,
                )
            except (OSError, ValueError, TaskResultError, ExpertDeliverableError):
                # The immutable result is already valid. Cache prewarming is a
                # performance optimisation; the read endpoint can retry it.
                pass

        return {
            "kind": "interactive_view",
            "result_ref": record.ref.model_dump(mode="json"),
            "result": record.as_payload(),
        }

    async def materialize_structured_view(
        self,
        *,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        execution_id: str,
        title: str,
        summary: str,
        data_output: str,
        dataset_output: str | None = None,
        preview_output: str | None,
        dataset_ref: ArtifactRef,
        view_kind: str,
        interaction: dict[str, Any],
        origin_request_id: str | None,
        execution_output_names: tuple[str, ...],
        presentation: dict[str, Any] | None = None,
        materialization_key: str | None = None,
    ) -> dict[str, Any]:
        """Persist a non-map result and select its best available presentation.

        The execution output is the result.  Renderer validation only decides
        whether the Workbench can open it interactively; it must never erase a
        successfully generated file or force the Expert to run the science
        again.  A valid PNG becomes the first fallback, followed by the
        original downloadable file.
        """

        self._require_assigned_dataset(
            workspace_id=workspace_id,
            work_order_id=work_order_id,
            dataset_ref=dataset_ref,
        )
        data_path = self._execution_output(
            workspace_id=workspace_id,
            task_id=task_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            output_name=data_output,
        )
        render_error: str | None = None
        metadata: dict[str, Any] = {}
        hydrated_payload: dict[str, Any] | None = None
        dataset_path: Path | None = None
        if data_path.suffix.lower() == ".nc":
            try:
                hydrated_payload = hydrate_ocean_view_netcdf(data_path)
                metadata = self._validate_structured_data(
                    hydrated_payload, expected_kind=view_kind
                )
                metadata["renderer_schema"] = hydrated_payload["schema_version"]
                dataset_path = data_path
            except ExpertDeliverableError as exc:
                render_error = str(exc) or "NetCDF interactive view is not renderable"
        elif data_path.suffix.lower() != ".json":
            render_error = "Interactive view must be a self-describing NetCDF file"
        elif data_path.stat().st_size > 5 * 1024 * 1024:
            render_error = "Structured interactive view exceeds 5 MiB"
        else:
            try:
                payload = json.loads(data_path.read_text(encoding="utf-8"))
                if payload.get("schema_version") in {
                    "ocean-scientific-figure/v3",
                    "ocean-scientific-figure/v4",
                }:
                    if dataset_output is None:
                        raise ExpertDeliverableError(
                            "Scientific figure manifest requires its NetCDF data output"
                        )
                    dataset_path = self._execution_output(
                        workspace_id=workspace_id,
                        task_id=task_id,
                        work_order_id=work_order_id,
                        execution_id=execution_id,
                        output_name=dataset_output,
                    )
                    if dataset_path.suffix.lower() != ".nc":
                        raise ExpertDeliverableError("Scientific figure data output must be NetCDF")
                    hydrated = hydrate_scientific_manifest(payload, dataset_path)
                    hydrated_payload = hydrated
                    metadata = self._validate_structured_data(hydrated, expected_kind=view_kind)
                    metadata["renderer_schema"] = payload["schema_version"]
                else:
                    metadata = self._validate_structured_data(payload, expected_kind=view_kind)
                    hydrated_payload = payload
            except (OSError, json.JSONDecodeError, ExpertDeliverableError) as exc:
                render_error = str(exc) or "Structured interactive view is not renderable"

        if data_path.suffix.lower() == ".nc" and render_error is not None:
            # Current results have one canonical representation. Publishing an
            # unreadable self-describing file would only create a blank link in
            # the report, so fail acceptance while retaining the Expert file.
            raise ExpertDeliverableError(render_error)

        preview_path: Path | None = None
        preview_error: str | None = None
        if preview_output is not None:
            try:
                candidate = self._execution_output(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    work_order_id=work_order_id,
                    execution_id=execution_id,
                    output_name=preview_output,
                )
                if (
                    candidate.suffix.lower() != ".png"
                    or candidate.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"
                ):
                    preview_error = "Interactive view preview must be PNG"
                else:
                    preview_path = candidate
            except ExpertDeliverableError as exc:
                preview_error = str(exc)

        single_netcdf = data_path.suffix.lower() == ".nc"
        is_manifest = dataset_path is not None and not single_netcdf
        data_file = (
            "data.nc"
            if single_netcdf
            else "view.json"
            if is_manifest
            else (
                "data.json"
                if data_path.suffix.lower() == ".json"
                else ("result" + (data_path.suffix.lower() or ".bin"))
            )
        )
        render_status = (
            "interactive"
            if render_error is None
            else ("preview" if preview_path is not None else "file")
        )
        files = {data_file: data_path}
        if dataset_path is not None and dataset_path != data_path:
            files["data.nc"] = dataset_path
        if preview_path is not None:
            files["preview.png"] = preview_path
        render_messages = [message for message in (render_error, preview_error) if message]
        record = self.results.put(
            workspace_id=workspace_id,
            task_id=task_id,
            kind="interactive_view",
            title=title,
            summary=summary,
            content={
                "view_kind": view_kind,
                "output_path": f"outputs/{data_output}",
                "data_file": data_file,
                "dataset_file": "data.nc" if dataset_path is not None else None,
                "preview_file": "preview.png" if preview_path is not None else None,
                "render_status": render_status,
                "render_message": "; ".join(render_messages) if render_messages else None,
                "interaction": interaction,
                "metadata": {
                    **metadata,
                    "payload_size_bytes": data_path.stat().st_size,
                },
                "presentation": presentation or {},
            },
            files=files,
            source_refs=(dataset_ref.model_dump(mode="json"),),
            origin_request_id=origin_request_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            execution_output_names=execution_output_names,
            materialization_key=materialization_key,
        )
        if render_status == "interactive" and hydrated_payload is not None:
            try:
                interactive_view_cache(
                    record=record,
                    results=self.results,
                    hydrated_payload=hydrated_payload,
                )
            except (OSError, ValueError, TaskResultError, ExpertDeliverableError):
                pass
        return {
            "kind": "interactive_view",
            "render_status": render_status,
            "render_message": "; ".join(render_messages) if render_messages else None,
            "result_ref": record.ref.model_dump(mode="json"),
            "result": record.as_payload(),
        }

    async def materialize_report(
        self,
        *,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        execution_id: str,
        title: str,
        summary: str,
        report_output: str,
        attachment_outputs: tuple[str, ...],
        evidence_refs: tuple[ArtifactRef, ...],
        data_sources_summary: str,
        calculation_summary: str,
        parameters_summary: str,
        checks: tuple[str, ...],
        limitations: tuple[str, ...],
        conclusion_export_allowed: bool,
        fully_reproducible: bool,
        origin_request_id: str | None,
        execution_output_names: tuple[str, ...],
        presentation: dict[str, Any] | None = None,
        materialization_key: str | None = None,
    ) -> dict[str, Any]:
        if not evidence_refs:
            raise ExpertDeliverableError("Report requires at least one immutable evidence ref")
        evidence: list[dict[str, Any]] = []
        for ref in evidence_refs:
            artifact = self.store.get_artifact(workspace_id=workspace_id, ref=ref)
            if artifact is None:
                raise ExpertDeliverableError(f"Report evidence is unavailable: {ref.key}")
            evidence.append(
                {
                    "ref": ref.model_dump(mode="json"),
                    "artifact_type": artifact.artifact_type,
                    "conclusion_eligible": conclusion_export_allowed,
                    "grounding": "grounded",
                }
            )
        report_path = self._execution_output(
            workspace_id=workspace_id,
            task_id=task_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            output_name=report_output,
        )
        if report_path.suffix.lower() not in {".md", ".markdown"}:
            raise ExpertDeliverableError("Report primary output must be Markdown")
        report_text = report_path.read_text(encoding="utf-8")
        execution_root = report_path.parent.parent
        code_path = execution_root / "code" / "analysis.py"
        requirements_path = execution_root / "code" / "requirements.txt"
        input_manifest_path = execution_root / "inputs.json"
        required_files = {
            "analysis.py": code_path,
            "requirements.txt": requirements_path,
            "inputs.json": input_manifest_path,
        }
        unavailable = [name for name, path in required_files.items() if not path.is_file()]
        if unavailable:
            raise ExpertDeliverableError(
                "Reproducibility package is incomplete: " + ", ".join(unavailable)
            )
        execution = self.store.get_code_execution(execution_id)
        if (
            execution is None
            or execution.workspace_id != workspace_id
            or execution.task_id != task_id
        ):
            raise ExpertDeliverableError("Report execution evidence is unavailable")

        def checksum(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        reproducibility = {
            "schema_version": "ocean-report-reproducibility/v1",
            "data_sources": data_sources_summary,
            "calculation": calculation_summary,
            "parameters": parameters_summary,
            "checks": list(checks),
            "limitations": list(limitations),
            "input_artifact_refs": [ref.model_dump(mode="json") for ref in evidence_refs],
            "execution_id": execution_id,
            "publisher_work_order_id": work_order_id,
            "source_work_order_id": execution.work_order_id,
            "execution_state": execution.state,
            "execution_result": execution.result,
            "package_files": {
                name: {"sha256": checksum(path), "bytes": path.stat().st_size}
                for name, path in required_files.items()
            },
        }
        appendix = (
            "\n\n## Reproducibility\n\n"
            "### Data sources\n\n"
            + data_sources_summary.strip()
            + "\n\n### Calculation\n\n"
            + calculation_summary.strip()
            + "\n\n### Parameters\n\n"
            + parameters_summary.strip()
            + "\n\n### Checks\n\n"
            + "\n".join(f"- {item}" for item in checks)
            + "\n\n### Limitations\n\n"
            + "\n".join(f"- {item}" for item in limitations)
            + "\n\nThe attached Python source, environment requirements, and immutable input "
            "manifest document this report's execution. The task-level supplementary "
            "analysis.ipynb is the single editable notebook for the complete analysis.\n"
        )
        files: dict[str, bytes | Path] = {
            "report.md": (report_text.rstrip() + appendix).encode("utf-8"),
            **required_files,
            "reproducibility.json": json.dumps(
                reproducibility, ensure_ascii=False, indent=2
            ).encode("utf-8"),
        }
        for index, output in enumerate(attachment_outputs, start=1):
            path = self._execution_output(
                workspace_id=workspace_id,
                task_id=task_id,
                work_order_id=work_order_id,
                execution_id=execution_id,
                output_name=output,
            )
            files[f"attachment_{index:03d}{path.suffix.lower()}"] = path
        attachment_files = [name for name in files if name.startswith("attachment_")]
        record = self.results.put(
            workspace_id=workspace_id,
            task_id=task_id,
            kind="report",
            title=title,
            summary=summary,
            content={
                "markdown_file": "report.md",
                "code_file": "analysis.py",
                "environment_file": "requirements.txt",
                "input_manifest_file": "inputs.json",
                "reproducibility_manifest_file": "reproducibility.json",
                "attachment_files": attachment_files,
                "evidence": evidence,
                "conclusion_export_allowed": conclusion_export_allowed,
                "fully_reproducible": fully_reproducible,
                "presentation": presentation or {},
            },
            files=files,
            source_refs=tuple(ref.model_dump(mode="json") for ref in evidence_refs),
            origin_request_id=origin_request_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            execution_output_names=execution_output_names,
            materialization_key=materialization_key,
        )
        return {
            "kind": "report",
            "result_ref": record.ref.model_dump(mode="json"),
            "result": record.as_payload(),
        }

    def _require_assigned_dataset(
        self,
        *,
        workspace_id: str,
        work_order_id: str,
        dataset_ref: ArtifactRef,
    ) -> None:
        artifact = self.store.get_artifact(workspace_id=workspace_id, ref=dataset_ref)
        if artifact is None or artifact.artifact_type != "dataset":
            raise ExpertDeliverableError("Interactive view source is not an available dataset")
        record = self.store.get_team_work(work_order_id)
        allowed = (
            {
                evidence.ref
                for evidence in record.work_order.input_refs
                if evidence.kind == "dataset"
            }
            if record is not None
            else set()
        )
        if dataset_ref.key not in allowed:
            raise ExpertDeliverableError(
                "Interactive view must cite a dataset frozen in this Expert assignment"
            )

    @staticmethod
    def _prepare_spatial_view(
        *,
        field_path: Path,
        staging: Path,
        variable: str,
        longitude_coordinate: str,
        latitude_coordinate: str,
        units: str | None,
        colormap: str,
        colorbar_label: str | None,
        field_kind: str = "continuous",
    ) -> tuple[dict[str, Any], Path, Path, Path]:
        """Normalize a regular geographic field and derive renderer files server-side."""

        try:
            import numpy as np
            import xarray as xr
            from matplotlib import colormaps
            from matplotlib.colors import Normalize
            from PIL import Image
        except ImportError as exc:
            raise ExpertDeliverableError(
                "Interactive views require the installed Ocean scientific runtime"
            ) from exc
        try:
            source = xr.open_dataset(field_path, decode_cf=False, mask_and_scale=False)
        except Exception as exc:
            raise ExpertDeliverableError(
                "Interactive view field is not a readable NetCDF dataset"
            ) from exc
        try:
            if variable not in source.data_vars:
                raise ExpertDeliverableError(
                    f"Interactive view field does not contain variable {variable!r}"
                )
            if (
                longitude_coordinate not in source.coords
                or latitude_coordinate not in source.coords
            ):
                raise ExpertDeliverableError(
                    "Interactive view field is missing the supplied longitude or latitude coordinate"
                )
            field = source[variable]
            longitude = source.coords[longitude_coordinate]
            latitude = source.coords[latitude_coordinate]
            if field.ndim != 2 or longitude.ndim != 1 or latitude.ndim != 1:
                raise ExpertDeliverableError(
                    "Interactive view requires one two-dimensional field and one-dimensional regular axes"
                )
            if longitude.dims[0] not in field.dims or latitude.dims[0] not in field.dims:
                raise ExpertDeliverableError(
                    "Interactive view coordinates must index the supplied field"
                )
            longitude_values = np.asarray(longitude.values, dtype=float)
            latitude_values = np.asarray(latitude.values, dtype=float)
            if longitude_values.size < 2 or latitude_values.size < 2:
                raise ExpertDeliverableError(
                    "Interactive view coordinates require at least two values per axis"
                )
            if not np.isfinite(longitude_values).all() or not np.isfinite(latitude_values).all():
                raise ExpertDeliverableError("Interactive view coordinates must be finite")

            normalized_longitude = ((longitude_values + 180.0) % 360.0) - 180.0
            longitude_order = np.argsort(normalized_longitude)
            latitude_order = np.argsort(latitude_values)[::-1]
            normalized_longitude = normalized_longitude[longitude_order]
            normalized_latitude = latitude_values[latitude_order]
            longitude_steps = np.diff(normalized_longitude)
            latitude_steps = -np.diff(normalized_latitude)
            longitude_spacing = float(np.median(longitude_steps))
            latitude_spacing = float(np.median(latitude_steps))
            if longitude_spacing <= 0 or latitude_spacing <= 0:
                raise ExpertDeliverableError(
                    "Interactive view coordinates must be strictly monotonic"
                )
            if not np.allclose(
                longitude_steps,
                longitude_spacing,
                rtol=1e-6,
                atol=max(1e-10, longitude_spacing * 1e-6),
            ) or not np.allclose(
                latitude_steps,
                latitude_spacing,
                rtol=1e-6,
                atol=max(1e-10, latitude_spacing * 1e-6),
            ):
                raise ExpertDeliverableError(
                    "Interactive view currently requires a regular geographic grid"
                )

            field = field.transpose(latitude.dims[0], longitude.dims[0])
            values = np.asarray(field.values)[np.ix_(latitude_order, longitude_order)]
            if not np.issubdtype(values.dtype, np.number):
                raise ExpertDeliverableError("Interactive view field must contain numeric values")
            values = np.asarray(values, dtype=float)
            finite = values[np.isfinite(values)]
            if finite.size == 0:
                raise ExpertDeliverableError("Interactive view field has no finite values")
            minimum = float(finite.min())
            maximum = float(finite.max())
            declared_units = (units or str(field.attrs.get("units", ""))).strip()
            if not declared_units:
                raise ExpertDeliverableError(
                    "Interactive view requires explicit units or units on the field variable"
                )

            west = float(normalized_longitude[0] - longitude_spacing / 2)
            east = float(normalized_longitude[-1] + longitude_spacing / 2)
            south = float(normalized_latitude[-1] - latitude_spacing / 2)
            north = float(normalized_latitude[0] + latitude_spacing / 2)
            tolerance = 1e-8
            if (
                west < -180 - tolerance
                or east > 180 + tolerance
                or south < -90 - tolerance
                or north > 90 + tolerance
            ):
                raise ExpertDeliverableError(
                    "Interactive view outer edges exceed the EPSG:4326 geographic domain"
                )
            west, east = max(-180.0, west), min(180.0, east)
            south, north = max(-90.0, south), min(90.0, north)

            try:
                color_map = colormaps[colormap]
            except KeyError as exc:
                raise ExpertDeliverableError(f"Unknown matplotlib colormap: {colormap}") from exc
            if minimum == maximum:
                epsilon = max(1e-12, abs(minimum) * 1e-9)
                scale_min, scale_max = minimum - epsilon, maximum + epsilon
            else:
                scale_min, scale_max = minimum, maximum
            if field_kind not in {"continuous", "categorical"}:
                raise ExpertDeliverableError("Spatial field kind is unsupported")
            rgba = color_map(Normalize(vmin=scale_min, vmax=scale_max)(values), bytes=True)
            rgba[..., 3] = np.where(np.isfinite(values), 255, 0).astype(np.uint8)
            overlay = staging / "overlay.png"
            Image.fromarray(rgba, mode="RGBA").save(overlay)
            levels = [float(value) for value in np.linspace(scale_min, scale_max, 9)]
            metadata = {
                "variable": variable,
                "longitude_coordinate": longitude_coordinate,
                "latitude_coordinate": latitude_coordinate,
                "crs": "EPSG:4326",
                "bounds": [west, south, east, north],
                "width": int(values.shape[1]),
                "height": int(values.shape[0]),
                "units": declared_units,
                "value_range": [minimum, maximum],
                "colorbar": {
                    "colormap": colormap,
                    "levels": levels,
                    "label": colorbar_label or declared_units,
                },
                "field_kind": field_kind,
                "spatial_context": {
                    "region_key": "bounds:"
                    + ",".join(f"{value:.6f}" for value in (west, south, east, north)),
                    "bounds": [west, south, east, north],
                    "fit_policy": "region_change",
                },
            }
            view_dataset = staging / "data.nc"
            xr.Dataset(
                {
                    variable: (
                        (latitude_coordinate, longitude_coordinate),
                        values,
                        {"units": declared_units},
                    )
                },
                coords={
                    longitude_coordinate: normalized_longitude,
                    latitude_coordinate: normalized_latitude,
                },
            ).to_netcdf(view_dataset, engine="h5netcdf")
            view_data = staging / "view.json"
            view_data.write_text(
                json.dumps(
                    {
                        "schema_version": "ocean-interactive-spatial/v2",
                        "view_kind": "spatial_map",
                        "dataset_file": "data.nc",
                        "variable": variable,
                        "units": declared_units,
                        "longitude_coordinate": longitude_coordinate,
                        "latitude_coordinate": latitude_coordinate,
                        "shape": [int(values.shape[0]), int(values.shape[1])],
                        "bounds": [west, south, east, north],
                        "colorbar": metadata["colorbar"],
                        "rendering": {
                            "kind": field_kind,
                            "interpolation": (
                                "linear" if field_kind == "continuous" else "nearest"
                            ),
                        },
                        "spatial_context": metadata["spatial_context"],
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            return metadata, view_data, view_dataset, overlay
        finally:
            source.close()

    @staticmethod
    def _validate_structured_data(
        payload: Any,
        *,
        expected_kind: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ExpertDeliverableError("Structured interactive-view data must be an object")
        if payload.get("plot_kind") != expected_kind:
            raise ExpertDeliverableError(
                "Interactive view kind does not match its structured data payload"
            )
        if payload.get("schema_version") in {
            "ocean-scientific-view/v1",
            "ocean-scientific-figure/v2",
            "ocean-scientific-figure/v3",
            "ocean-scientific-figure/v4",
        }:
            return ExpertDeliverableService._validate_scientific_figure(payload)
        axes = payload.get("axes")
        series = payload.get("series")
        if not isinstance(axes, list) or not axes or not isinstance(series, list) or not series:
            raise ExpertDeliverableError(
                "Structured interactive-view data requires non-empty axes and series"
            )
        point_count = 0
        variable_names: list[str] = []
        for column in [*axes, *series]:
            if not isinstance(column, dict) or not isinstance(column.get("name"), str):
                raise ExpertDeliverableError("Interactive-view columns require names")
            values = column.get("values")
            if not isinstance(values, list):
                raise ExpertDeliverableError("Interactive-view columns require value arrays")
            variable_names.append(column["name"])
            point_count += len(values)
        if point_count > 50_000:
            raise ExpertDeliverableError("Structured interactive view exceeds 50,000 points")
        if len(variable_names) != len(set(variable_names)):
            raise ExpertDeliverableError("Interactive-view column names must be unique")
        axis_lengths = [len(column["values"]) for column in axes]
        series_lengths = [len(column["values"]) for column in series]
        if expected_kind in {"time_series", "profile"}:
            if len(axes) != 1 or any(length != axis_lengths[0] for length in series_lengths):
                raise ExpertDeliverableError(
                    f"{expected_kind} requires one axis and equally sized series"
                )
        elif expected_kind in {"scatter", "ts_diagram"}:
            if (
                len(axes) != 2
                or axis_lengths[0] != axis_lengths[1]
                or any(length != axis_lengths[0] for length in series_lengths)
            ):
                raise ExpertDeliverableError(
                    f"{expected_kind} requires two equally sized axes and aligned series"
                )
        elif expected_kind in {"section", "hovmoller"}:
            cell_count = axis_lengths[0] * axis_lengths[1] if len(axes) == 2 else -1
            if len(axes) != 2 or any(length != cell_count for length in series_lengths):
                raise ExpertDeliverableError(
                    f"{expected_kind} requires two axes and row-major x_count*y_count series"
                )
        spatial_context = ExpertDeliverableService._validate_spatial_context(
            payload.get("spatial_context")
        )
        return {
            "variables": variable_names,
            "point_count": point_count,
            "time_encoding": payload.get("time_encoding"),
            "spatial_context": spatial_context,
            "renderer_schema": "legacy-columns/v1",
        }

    @staticmethod
    def _validate_scientific_figure(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate one generic, bounded Figure -> Panel -> Layer contract."""

        schema = payload.get("schema_version")
        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            raise ExpertDeliverableError("Scientific figure requires a non-empty data object")

        point_count = 0
        for name, values in data.items():
            if not isinstance(name, str) or not name or len(name) > 128:
                raise ExpertDeliverableError("Scientific-figure data fields require bounded names")
            if not isinstance(values, list):
                raise ExpertDeliverableError("Scientific-figure data fields require value arrays")
            if any(
                value is not None and not isinstance(value, (int, float, str)) for value in values
            ):
                raise ExpertDeliverableError(
                    "Scientific-figure values must be numbers, strings, or null"
                )
            point_count += len(values)
        if point_count > MAX_RENDERED_SCIENTIFIC_VALUES:
            raise ExpertDeliverableError("Scientific figure exceeds the rendered point allowance")

        if schema == "ocean-scientific-view/v1":
            panels: Any = [
                {
                    "id": "main",
                    "axes": payload.get("axes"),
                    "layers": payload.get("layers"),
                    "display": payload.get("display"),
                }
            ]
        else:
            panels = payload.get("panels")
            layout = payload.get("layout")
            if layout is not None and (
                not isinstance(layout, dict) or layout.get("columns", 1) not in {1, 2, 3}
            ):
                raise ExpertDeliverableError("Scientific-figure layout is invalid")
        if not isinstance(panels, list) or not panels or len(panels) > 6:
            raise ExpertDeliverableError("Scientific figure requires one to six panels")

        panel_ids: set[str] = set()
        layer_types: list[str] = []
        layer_count = 0

        def validate_axis(axis: Any, axis_name: str) -> None:
            if not isinstance(axis, dict):
                raise ExpertDeliverableError("Scientific-figure axes must be objects")
            field = axis.get("field")
            if not isinstance(field, str) or field not in data:
                raise ExpertDeliverableError(
                    f"Scientific-figure {axis_name} axis references an unknown field"
                )
            if axis.get("scale", "linear") not in {"linear", "log", "time", "category"}:
                raise ExpertDeliverableError("Scientific-figure axis scale is unsupported")
            if axis.get("tick_format", "auto") not in {
                "auto",
                "number",
                "date",
                "month",
                "longitude",
                "latitude",
            }:
                raise ExpertDeliverableError("Scientific-figure tick format is unsupported")
            axis_range = axis.get("range")
            if axis_range is not None and (
                not isinstance(axis_range, list)
                or len(axis_range) != 2
                or not all(isinstance(value, (int, float)) for value in axis_range)
            ):
                raise ExpertDeliverableError(
                    "Scientific-figure axis range must contain two numbers"
                )
            if (
                axis.get("scale") == "log"
                and axis_range is not None
                and any(value <= 0 for value in axis_range)
            ):
                raise ExpertDeliverableError(
                    "Scientific-figure log axis range must remain positive"
                )

        def require_fields(layer: dict[str, Any], names: tuple[str, ...]) -> list[list[Any]]:
            arrays: list[list[Any]] = []
            for name in names:
                field = layer.get(name)
                if not isinstance(field, str) or field not in data:
                    raise ExpertDeliverableError(
                        f"Scientific-figure {layer.get('type')} layer references unknown {name} data"
                    )
                arrays.append(data[field])
            return arrays

        for panel_index, panel in enumerate(panels):
            if not isinstance(panel, dict):
                raise ExpertDeliverableError("Scientific-figure panels must be objects")
            panel_id = panel.get("id", f"panel-{panel_index + 1}")
            if (
                not isinstance(panel_id, str)
                or not panel_id.strip()
                or len(panel_id) > 128
                or panel_id in panel_ids
            ):
                raise ExpertDeliverableError("Scientific-figure panel ids must be unique")
            panel_ids.add(panel_id)
            grid = panel.get("grid")
            if grid is not None and (
                not isinstance(grid, dict)
                or any(
                    key not in {"column", "row", "column_span", "row_span"}
                    or not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 1
                    or value > 6
                    for key, value in grid.items()
                )
            ):
                raise ExpertDeliverableError("Scientific-figure panel grid is invalid")
            display = panel.get("display")
            if display is not None and not isinstance(display, dict):
                raise ExpertDeliverableError("Scientific-figure panel display is invalid")
            if isinstance(display, dict) and (
                display.get("legend_position", "bottom") not in {"top", "bottom", "inside"}
                or (
                    "aspect_ratio" in display
                    and (
                        not isinstance(display["aspect_ratio"], (int, float))
                        or isinstance(display["aspect_ratio"], bool)
                        or not 0.5 <= display["aspect_ratio"] <= 3.0
                    )
                )
            ):
                raise ExpertDeliverableError("Scientific-figure panel display is invalid")
            axes = panel.get("axes")
            if not isinstance(axes, dict) or set(axes) != {"x", "y"}:
                raise ExpertDeliverableError(
                    "Scientific-figure panels require x and y axis definitions"
                )
            validate_axis(axes["x"], "x")
            validate_axis(axes["y"], "y")
            layers = panel.get("layers")
            if not isinstance(layers, list) or not layers or len(layers) > 24:
                raise ExpertDeliverableError(
                    "Scientific-figure panels require one to twenty-four layers"
                )
            layer_count += len(layers)

            for layer in layers:
                if not isinstance(layer, dict):
                    raise ExpertDeliverableError("Scientific-figure layers must be objects")
                layer_type = layer.get("type")
                if layer_type not in {
                    "scatter",
                    "line",
                    "band",
                    "heatmap",
                    "field2d",
                    "categories",
                    "contour",
                    "annotation",
                    "reference",
                    "vector",
                }:
                    raise ExpertDeliverableError("Scientific-figure layer type is unsupported")
                layer_types.append(str(layer_type))
                style = layer.get("style")
                if style is not None and not isinstance(style, dict):
                    raise ExpertDeliverableError("Scientific-figure layer style must be an object")
                color_domain = layer.get("color_domain")
                if color_domain is not None and (
                    not isinstance(color_domain, list)
                    or len(color_domain) != 2
                    or not all(isinstance(value, (int, float)) for value in color_domain)
                ):
                    raise ExpertDeliverableError(
                        "Scientific-figure color domain must contain two numbers"
                    )
                if layer.get("color_scale", "linear") not in {"linear", "log"}:
                    raise ExpertDeliverableError("Scientific-figure color scale is unsupported")
                if (
                    layer.get("color_scale") == "log"
                    and color_domain is not None
                    and any(value <= 0 for value in color_domain)
                ):
                    raise ExpertDeliverableError(
                        "Scientific-figure log color domain must remain positive"
                    )

                if layer_type in {"scatter", "line"}:
                    arrays = require_fields(layer, ("x", "y"))
                    if len(arrays[0]) != len(arrays[1]):
                        raise ExpertDeliverableError(
                            f"Scientific-figure {layer_type} coordinates must be aligned"
                        )
                    color_field = layer.get("color")
                    if color_field is not None:
                        if not isinstance(color_field, str) or color_field not in data:
                            raise ExpertDeliverableError("Scientific-figure color field is unknown")
                        if len(data[color_field]) != len(arrays[0]):
                            raise ExpertDeliverableError(
                                "Scientific-figure color values must be aligned"
                            )
                elif layer_type == "band":
                    arrays = require_fields(layer, ("x", "y0", "y1"))
                    if len({len(values) for values in arrays}) != 1:
                        raise ExpertDeliverableError(
                            "Scientific-figure band values must be aligned"
                        )
                elif layer_type in {"heatmap", "field2d"}:
                    x_values, y_values, z_values = require_fields(layer, ("x", "y", "z"))
                    if len(z_values) != len(x_values) * len(y_values):
                        raise ExpertDeliverableError(
                            "Scientific-figure field2d z values must be row-major x_count*y_count"
                        )
                    if layer_type == "field2d":
                        if layer.get("render", "filled_contour") not in {
                            "filled_contour",
                            "smooth",
                            "cells",
                        }:
                            raise ExpertDeliverableError(
                                "Scientific-figure field2d render mode is unsupported"
                            )
                        if layer.get("interpolation", "linear") not in {"linear", "nearest"}:
                            raise ExpertDeliverableError(
                                "Scientific-figure field2d interpolation is unsupported"
                            )
                        levels = layer.get("levels", 14)
                        if not (
                            isinstance(levels, int)
                            and not isinstance(levels, bool)
                            and 3 <= levels <= 32
                        ) and not (
                            isinstance(levels, list)
                            and 2 <= len(levels) <= 32
                            and all(isinstance(value, (int, float)) for value in levels)
                        ):
                            raise ExpertDeliverableError(
                                "Scientific-figure field2d levels are invalid"
                            )
                elif layer_type == "categories":
                    arrays = require_fields(layer, ("x", "y", "category"))
                    if len({len(values) for values in arrays}) != 1:
                        raise ExpertDeliverableError(
                            "Scientific-figure category values must align with x and y"
                        )
                    labels = layer.get("labels")
                    if labels is not None and (
                        not isinstance(labels, dict)
                        or len(labels) > 64
                        or any(
                            not isinstance(key, str)
                            or not isinstance(value, str)
                            or not value
                            or len(value) > 120
                            for key, value in labels.items()
                        )
                    ):
                        raise ExpertDeliverableError(
                            "Scientific-figure category labels are invalid"
                        )
                elif layer_type == "vector":
                    arrays = require_fields(layer, ("x", "y", "u", "v"))
                    if len({len(values) for values in arrays}) != 1:
                        raise ExpertDeliverableError(
                            "Scientific-figure vector values must be aligned"
                        )
                    if "scale" in layer and (
                        not isinstance(layer["scale"], (int, float))
                        or isinstance(layer["scale"], bool)
                        or layer["scale"] <= 0
                    ):
                        raise ExpertDeliverableError(
                            "Scientific-figure vector scale must be positive"
                        )
                elif layer_type == "contour":
                    paths = layer.get("paths")
                    if not isinstance(paths, list) or not paths or len(paths) > 128:
                        raise ExpertDeliverableError(
                            "Scientific-figure contour requires bounded paths"
                        )
                    for path in paths:
                        if not isinstance(path, dict) or not isinstance(
                            path.get("level"), (int, float)
                        ):
                            raise ExpertDeliverableError(
                                "Scientific-figure contour path requires a level"
                            )
                        points = path.get("points")
                        if not isinstance(points, list) or len(points) < 2:
                            raise ExpertDeliverableError(
                                "Scientific-figure contour path requires points"
                            )
                        if any(
                            not isinstance(point, list)
                            or len(point) != 2
                            or not all(isinstance(value, (int, float)) for value in point)
                            for point in points
                        ):
                            raise ExpertDeliverableError(
                                "Scientific-figure contour points must be [x,y]"
                            )
                        point_count += len(points)
                elif layer_type == "annotation":
                    items = layer.get("items")
                    if not isinstance(items, list) or not items or len(items) > 64:
                        raise ExpertDeliverableError(
                            "Scientific-figure annotation requires bounded items"
                        )
                    for item in items:
                        if (
                            not isinstance(item, dict)
                            or not isinstance(item.get("x"), (int, float))
                            or not isinstance(item.get("y"), (int, float))
                            or not isinstance(item.get("text"), str)
                            or not item["text"]
                            or len(item["text"]) > 160
                        ):
                            raise ExpertDeliverableError(
                                "Scientific-figure annotation item is invalid"
                            )
                elif layer_type == "reference" and (
                    layer.get("axis") not in {"x", "y"}
                    or not isinstance(layer.get("value"), (int, float))
                ):
                    raise ExpertDeliverableError(
                        "Scientific-figure reference requires axis and value"
                    )

        if layer_count > 48:
            raise ExpertDeliverableError("Scientific figure exceeds forty-eight total layers")
        if point_count > MAX_RENDERED_SCIENTIFIC_VALUES:
            raise ExpertDeliverableError("Scientific figure exceeds the rendered point allowance")
        spatial_context = ExpertDeliverableService._validate_spatial_context(
            payload.get("spatial_context")
        )
        return {
            "variables": list(data),
            "point_count": point_count,
            "time_encoding": payload.get("time_encoding"),
            "spatial_context": spatial_context,
            "renderer_schema": schema,
            "panel_count": len(panels),
            "layer_types": layer_types,
        }

    @staticmethod
    def _validate_spatial_context(value: Any) -> dict[str, Any] | None:
        """Validate optional geographic context shared by every plot kind.

        This is deliberately independent of scientific variables and regions:
        it describes where a result belongs, not how that result was computed.
        """

        if value is None:
            return None
        if not isinstance(value, dict):
            raise ExpertDeliverableError("spatial_context must be an object")
        region_key = value.get("region_key")
        if not isinstance(region_key, str) or not region_key.strip() or len(region_key) > 200:
            raise ExpertDeliverableError("spatial_context requires a bounded region_key")
        fit_policy = value.get("fit_policy", "region_change")
        if fit_policy not in {"region_change", "always", "never"}:
            raise ExpertDeliverableError("spatial_context fit_policy is invalid")
        normalized: dict[str, Any] = {
            "region_key": region_key.strip(),
            "fit_policy": fit_policy,
        }
        bounds = value.get("bounds")
        if bounds is not None:
            if (
                not isinstance(bounds, list)
                or len(bounds) != 4
                or not all(isinstance(item, (int, float)) for item in bounds)
            ):
                raise ExpertDeliverableError(
                    "spatial_context bounds must be [west,south,east,north]"
                )
            west, south, east, north = (float(item) for item in bounds)
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ExpertDeliverableError("spatial_context bounds exceed EPSG:4326")
            normalized["bounds"] = [west, south, east, north]
        features = value.get("features")
        if features is not None:
            if not isinstance(features, dict) or features.get("type") != "FeatureCollection":
                raise ExpertDeliverableError(
                    "spatial_context features must be GeoJSON FeatureCollection"
                )
            entries = features.get("features")
            if not isinstance(entries, list) or len(entries) > 128:
                raise ExpertDeliverableError("spatial_context features are invalid or too numerous")
            allowed = {
                "Point",
                "MultiPoint",
                "LineString",
                "MultiLineString",
                "Polygon",
                "MultiPolygon",
            }
            for feature in entries:
                if not isinstance(feature, dict) or feature.get("type") != "Feature":
                    raise ExpertDeliverableError(
                        "spatial_context contains an invalid GeoJSON feature"
                    )
                geometry = feature.get("geometry")
                if not isinstance(geometry, dict) or geometry.get("type") not in allowed:
                    raise ExpertDeliverableError("spatial_context contains unsupported geometry")
                if "coordinates" not in geometry:
                    raise ExpertDeliverableError("spatial_context geometry requires coordinates")
            normalized["features"] = features
        if "bounds" not in normalized and "features" not in normalized:
            raise ExpertDeliverableError("spatial_context requires bounds or features")
        return normalized

    def _execution_output(
        self,
        *,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        execution_id: str,
        output_name: str,
    ) -> Path:
        record = self.store.get_code_execution(execution_id)
        if record is None or record.workspace_id != workspace_id or record.task_id != task_id:
            raise ExpertDeliverableError("CodeExecution is unavailable to this task")
        if record.work_order_id != work_order_id:
            current_work = self.store.get_team_work(work_order_id)
            origin_work = self.store.get_team_work(record.work_order_id)
            same_task = (
                current_work is not None
                and origin_work is not None
                and current_work.work_order.task_id is not None
                and current_work.work_order.task_id == origin_work.work_order.task_id == task_id
            )
            same_legacy_agent_job = (
                current_work is not None
                and origin_work is not None
                and current_work.work_order.task_id is None
                and current_work.work_order.job_key is not None
                and current_work.work_order.job_key == origin_work.work_order.job_key
            )
            same_legacy_request = (
                current_work is not None
                and origin_work is not None
                and (
                    current_work.work_order.task_id is None
                    or origin_work.work_order.task_id is None
                )
                and current_work.work_order.parent_request_id
                == origin_work.work_order.parent_request_id
            )
            if (
                current_work is None
                or origin_work is None
                or current_work.workspace_id != workspace_id
                or origin_work.workspace_id != workspace_id
                or not (same_task or same_legacy_agent_job or same_legacy_request)
            ):
                raise ExpertDeliverableError("CodeExecution is not available to this task")
        if record.state == "running" or record.result is None:
            raise ExpertDeliverableError("CodeExecution outputs are not yet available")
        declared = record.result.get("output_files", [])
        if not isinstance(declared, list):
            raise ExpertDeliverableError("CodeExecution output manifest is invalid")
        requested = PurePosixPath(output_name)
        if requested.is_absolute() or any(part in {"", ".", ".."} for part in requested.parts):
            raise ExpertDeliverableError("Deliverable output path is unsafe")

        # OCEAN_OUTPUT_DIR is already the execution's ``outputs`` directory.
        # Treat ``figure.json`` and ``outputs/figure.json`` as the same
        # declaration spelling when the manifest has one unambiguous match.
        # This removes a transport naming trap without weakening file
        # ownership or path-safety checks.
        def without_output_root(value: str) -> str:
            parts = list(PurePosixPath(value).parts)
            while len(parts) > 1 and parts[0] == "outputs":
                parts.pop(0)
            return PurePosixPath(*parts).as_posix()

        safe_declared = [
            value
            for value in declared
            if isinstance(value, str)
            and value
            and not PurePosixPath(value).is_absolute()
            and all(part not in {"", ".", ".."} for part in PurePosixPath(value).parts)
        ]
        if output_name in safe_declared:
            resolved_output_name = output_name
        else:
            aliases = [
                value
                for value in safe_declared
                if without_output_root(value) == without_output_root(output_name)
            ]
            if len(aliases) != 1:
                raise ExpertDeliverableError(
                    "Requested deliverable was not an enumerated code output"
                )
            resolved_output_name = aliases[0]
        relative = PurePosixPath(resolved_output_name)
        persisted_work_root = record.result.get("work_root")
        if isinstance(persisted_work_root, str) and persisted_work_root:
            work_root = Path(persisted_work_root).expanduser().resolve()
        else:
            raise ExpertDeliverableError(
                "CodeExecution does not belong to the current Expert Session protocol"
            )
        task_root = self.task_workspaces.ensure_task_root(task_id).resolve()
        try:
            work_root.relative_to(task_root)
        except ValueError as exc:
            raise ExpertDeliverableError(
                "CodeExecution work root is outside the task workspace"
            ) from exc
        output_root = (work_root / "executions" / execution_id / "outputs").resolve()
        candidate = output_root.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(output_root)
        except (OSError, ValueError) as exc:
            raise ExpertDeliverableError("Deliverable output is unavailable") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise ExpertDeliverableError("Deliverable output must be a regular file")
        return resolved

    def resolve_execution_output_name(
        self,
        *,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        execution_id: str,
        output_name: str,
    ) -> str:
        """Return the manifest spelling for one safely resolved output alias."""

        path = self._execution_output(
            workspace_id=workspace_id,
            task_id=task_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            output_name=output_name,
        )
        record = self.store.get_code_execution(execution_id)
        if record is None or record.result is None:
            raise ExpertDeliverableError("CodeExecution is unavailable to this task")
        persisted_work_root = record.result.get("work_root")
        if not isinstance(persisted_work_root, str) or not persisted_work_root:
            raise ExpertDeliverableError(
                "CodeExecution does not belong to the current Expert Session protocol"
            )
        output_root = (
            Path(persisted_work_root).expanduser().resolve()
            / "executions"
            / execution_id
            / "outputs"
        )
        try:
            return path.relative_to(output_root).as_posix()
        except ValueError as exc:
            raise ExpertDeliverableError("Deliverable output is unavailable") from exc


__all__ = [
    "ExpertDeliverableError",
    "ExpertDeliverableService",
    "hydrate_ocean_view_netcdf",
    "hydrate_scientific_manifest",
    "hydrate_spatial_manifest",
]
