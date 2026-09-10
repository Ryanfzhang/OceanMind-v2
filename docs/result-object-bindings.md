# Result object bindings

An immutable result can expose named plotted objects. The calculation declares their geometry;
the Coordinator cites their IDs; the Workbench uses the same record for labels and selection.
No object position or ID is reconstructed from narrative text.

## Authoring

```python
from oceanx.scientific_view import ScientificFigure

figure = ScientificFigure(plot_kind="spatial_map", title="Classified regions")
panel = figure.panel(x=longitude, y=latitude)
panel.field2d(classes, units="1", field_kind="categorical",
              category_labels={1: "Group A", 2: "Group B"})
figure.add_feature(id="region_a", label="Region A", mask=classes == 1)
figure.save("regions.nc")
```

`add_feature` takes exactly one selector:

- `mask`: nonempty Boolean y-by-x mask. Monotonic coordinate centers determine cell edges.
  Selected row runs become polygons, preserving excluded cells and disconnected components.
- `point=(x, y)`: actual data position.
- `bounds=(xmin, ymin, xmax, ymax)`: plotted region or time/value interval.
- `layer_id`: ID explicitly assigned with `panel.line(layer_id=...)` or `panel.scatter(layer_id=...)`.
  Gapped curves must be split into finite geometries.
- `geometry`: Point, MultiPoint, LineString, Polygon or MultiPolygon in panel data coordinates.

Multi-panel figures require the relevant `panel_id`. IDs are unique across the figure.
Time-axis coordinates accept ISO dates and are stored as epoch milliseconds.
Map longitudes normalize to [-180, 180]; antimeridian-crossing lines/polygons require explicit splitting.
Categorical fields use nearest-neighbor rendering and a discrete legend. Supplied category labels
must cover every finite code; omitted labels display neutral “Class N” names, not inferred meanings.

## Citations and rendering

Expert: `[[output:outputs/regions.nc#region_a|Region A]]`.
Published: `[[result:task_id/result_id@v1#region_a|Region A]]`.

The full feature definition remains in the saved NetCDF `ocean_view` attribute; publication indexes
its ID and label in result metadata. Backend validation rejects invalid geometry or unknown panels.
Output aliases resolve to immutable result versions. Object labels come from the saved registry.
Unknown or ambiguous objects display an explicit unavailable message instead of opening the whole figure.
Adjacent standalone aliases for the same target are deduplicated; different objects remain separate.

Opening a citation selects and focuses the object. A single-row selector switches objects or returns
to the full view; complete labels appear only in selected-object details. Small numbered markers
identify objects without putting paragraphs on the map. Polygon masks use light translucent fills
and a white under-stroke beneath a colored boundary, visible even over similar-colored fields.
Before rendering, polygon pieces are unioned within each object in data coordinates, including
legacy row-run geometries. Only the merged boundaries (including holes and disconnected islands)
are outlined: internal rectangle seams disappear without simplifying or expanding the mask.
Map and chart renderers share this preparation and styling; prepared geometry is memoized across
selection and hover changes. Separate object IDs are never merged, and saved geometry is untouched.
Click a mask or its marker to focus it; unselected masks fade. Continuous fields have a numeric color
scale separate from mask colors. These are renderer defaults, not task-specific analysis rules, and
do not change stored geometry or require resaving existing results with feature metadata.
The same mechanism applies to arbitrary computed regions, points, curves and intervals, without
domain names, variable names or task-specific branches in the renderer.

## Compatibility and scope

Existing whole-result citations remain readable. Old immutable results without feature metadata
do not acquire guessed objects; create a new result version with declared objects to add bindings.
This feature does not validate the scientific interpretation of a claim, rerun an analysis, alter
historical result files, or repair third-party basemap authentication.

Coverage: Python builder/hydration/publication/citation tests; frontend rendering tests; isolated,
offline Electron tests for chart and map object links, selection and reopening after reload.
