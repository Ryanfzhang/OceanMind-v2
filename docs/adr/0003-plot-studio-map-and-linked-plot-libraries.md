# ADR 0003: Plot Studio Map And Linked Plot Libraries

- Status: Superseded by ADR 0019; historical only
- Date: 2026-07-11
- Decision IDs: OD-01

## Context

The first graphical Ocean path must work offline, render a georeferenced image
overlay, retain ordinary GeoJSON Point/Polygon/LineString features, and open a
linked chart without making a public basemap or a static PNG the source of
truth.  The library decision needs an executable packaging and payload probe,
not a feature-list comparison alone.

## Decision

1. Use MapLibre GL JS 5.24.0 for the local map surface.  Plot Studio starts
   with an in-memory empty style, bundled graticule/coastline assets, and no
   external tile provider.  It uses MapLibre image sources for raster parts and
   GeoJSON sources/layers for Point, Polygon, and LineString scene features.
2. Use uPlot 1.6.32 for vector linked plots: time series, profiles, scatter,
   and T-S diagrams. Plot Studio uses a small native Canvas matrix renderer
   for section and Hovmoller payloads, keeping dense field interaction local
   and dependency-free. This is a rendering/data-shape decision, not an
   implementation of any ocean-analysis method or a substitute for Phase 5
   reference evals.
3. The frontend accepts a single linked-plot JSON payload only when it is at
   most 5 MiB and at most 50,000 points.  Larger results require an explicit
   downsampled interaction artifact and a reference to full data.  Dense plots
   suppress individual point glyphs above 2,000 points, while preserving the
   series line.
4. The Phase 0.5 packaged `dist/` budget is 400 KiB gzip for all browser
   assets, including the offline demo artifacts.  This is a guardrail for the
   walking skeleton, not a permanent production performance SLO.

MapLibre's image source accepts four geographic corner coordinates in
top-left, clockwise order, which matches the overlay contract selected in ADR
0005.  Its GeoJSON and image-source APIs are documented by MapLibre
([ImageSource](https://maplibre.org/maplibre-gl-js/docs/API/classes/ImageSource/),
[GeoJSONSource](https://maplibre.org/maplibre-gl-js/docs/API/classes/GeoJSONSource/)).
uPlot documents its Canvas time-series focus and supported temporal/numeric
axes in its [upstream README](https://github.com/leeoniya/uPlot).

## Evidence

The isolated browser probe in `spikes/ocean_walking_skeleton/plot_studio/`
uses an offline synthetic output from a trusted sandbox run.  It has passed:

- a field image source with a known north/south checkerboard orientation;
- two separately registered dateline image parts, plus Point/Polygon/LineString
  GeoJSON layers;
- marker-to-time-series interaction on desktop and mobile screenshots;
- Point -> time series, Polygon -> profile, and LineString -> section
  interaction through actual MapLibre hit-testing in the packaged browser;
- bounded `profile`, `ts_diagram`/`scatter`, `section`, and `hovmoller`
  payload validation. Matrix series use a flattened `(vertical, horizontal)`
  shape after a horizontal axis followed by a vertical axis;
- a real 5,242,880-byte JSON payload containing 50,000 time-series points,
  parsed and rendered into a uPlot canvas in 13,448 ms on the current
  headless-Chrome development host;
- a built `dist/` measurement of 385,574 gzip bytes against the 409,600-byte
  budget.
- an Evidence/Code inspector backed by safe extracts from the same sandbox
  manifests, including a real `source_changed_during_run` attempt whose
  publish gate is blocked. Raw logs are intentionally absent from those browser
  assets.

The 5 MiB timing is a reproducible probe measurement, not a cross-device
latency promise.  It deliberately sets a payload boundary that production
transport and artifact validators must enforce.

## Consequences

- MapLibre is a view library only.  It does not infer CRS, pixel registration,
  row order, or antimeridian semantics; the backend validates those facts
  before the UI receives an overlay registration.
- Plot Studio cannot rely on network map tiles for scientific context.  A
  later optional tile provider must keep the same artifact contract and cannot
  change provenance.
- The linked-plot payload contract is deliberately generic: it records axes,
  units, shapes, values, and rendering geometry, while model-authored code
  remains responsible for the underlying calculation. A successful renderer
  does not certify a profile, section, or T-S result scientifically.
- The bundle-budget and visual probes remain executable checks.  A dependency
  or rendering change that exceeds the budget, loses the canvas, or changes
  checkerboard orientation blocks the graphical path until reviewed.
- This decision does not implement the Protocol v2 graphical transport,
  asset server, token handshake, or persistent MapScene state; those remain
  Phase 1 and later work.

## Verification

- `npm run check`
- `npm run build`
- `npm run bundle-budget`
- `npm run visual-check`
- `python scripts/verify-screenshots.py <screenshot-directory>`
