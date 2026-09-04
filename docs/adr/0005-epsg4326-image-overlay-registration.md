# ADR 0005: EPSG:4326 Image Overlay Registration

- Status: Superseded by ADR 0019; historical only
- Date: 2026-07-11
- Decision IDs: OD-11

## Context

A raster that is visually plausible can still be scientifically wrong when its
rows are inverted, its bounds describe cell centers instead of outer edges, or
its antimeridian parts are joined implicitly.  The map library needs a complete
registration record rather than a filename plus bounding box.

## Decision

The first production SpatialLayerArtifact supports only regular
longitude/latitude scalar rasters in EPSG:4326.  Its validated registration
contains at least:

```text
crs = "EPSG:4326"
field URI + variable + latitude/longitude coordinate names
axis_order = "longitude_latitude"
source_coordinate_registration = "center" | "edge"
pixel_registration = "outer_edges"
row_order = "north_to_south"
column_order = "west_to_east"
longitude_domain = "-180_180"
width + height
parts[]: image URI, width, height, west/east/south/north outer edges
nodata.alpha = 0
resampling = "nearest" | "bilinear"
units + value range + colormap + levels
```

`outer_edges` is the only accepted `pixel_registration` value.  An agent that
starts with cell centers must compute the outer bounds explicitly.  Raster row
zero is northernmost and column zero is westernmost.  The frontend registers a
part with corners `[west,north]`, `[east,north]`, `[east,south]`,
`[west,south]`, matching MapLibre's documented top-left clockwise image-source
order ([ImageSource](https://maplibre.org/maplibre-gl-js/docs/API/classes/ImageSource/)).

Every part must have `west_edge < east_edge`.  A field that crosses the
antimeridian is split into ordered non-crossing parts such as 170E--180E and
180W--170W; `west > east` never means "wrap".  No-data pixels use transparent
alpha zero.  Resampling is declared and checked, never inferred by the UI.

Curvilinear, tripolar, staggered, and non-EPSG:4326 grids are unsupported by
this first renderer.  They require an explicit remapping declaration with
source/target grids and error/coverage checks before they can become a layer.

The current publisher accepts a regular NetCDF field output.  It verifies that
the named numeric field has the declared two-dimensional shape and value range.
For center-registered data it verifies a descending latitude axis, an
increasing longitude axis within each image part, and outer edges inferred from
the coordinate centers.  An antimeridian split may wrap only between parts.
For edge-registered data, v1 accepts a single non-wrapping part; a split edge
grid fails closed until its duplicated boundary representation has its own
typed contract.  PNGs must be bounded 8-bit RGBA images with alpha exactly
zero for no-data or 255 for data.

## Walking-Skeleton Evidence

The synthetic run generates an RGBA checkerboard with known geographic
orientation, outer edges, and a known transparent no-data block.  Its temporary
`overlay.json` records the frozen contract fields and two separately cropped
antimeridian images.  Independent verification checks source PNG corners,
transparent alpha, registration fields, and part dimensions.

The browser probe registers a normal field image and two dateline image sources
using an offline MapLibre style.  Desktop and mobile screenshots are checked
for the four alpha-composited fixture colors and their expected northwest,
northeast, southwest, southeast centroid order.  The desktop world view also
checks the distinct west/east dateline colors.  This detects blank images, row
inversion, left/right mirroring, and a missing dateline part.

The spike's `antimeridian_parts` key is temporary.  The production schema uses
the typed `parts[]` representation above; the coordinate and pixel semantics
are the decision, not the temporary key name.

## Consequences

- Plot Studio renders only backend-validated registrations.  A map client must
  not guess whether a bounding box describes centers, edges, or a wrapped
  longitude range.
- A transparent PNG is not enough by itself: the artifact also records source
  grid facts, units, render choices, and the producing run/checks.
- Tile, hover, vector-field, and time/depth-slider work remains out of scope
  for this contract and must preserve these same registration facts later.

## Verification

- `tests/test_ocean_partner/test_spatial_layer_publishing.py`
- `tests/test_ocean_partner/test_plot_studio_browser.py`
- `npm run visual-check`
