---
name: ocean-map-and-figure-review
description: Create and review truthful, coherent, publication-quality OceanMind maps, charts, multi-panel figures, and interactive scientific views.
metadata:
  origin: oceanmind
  roles:
    - data_reproducibility_expert
    - ocean_process_expert
    - statistical_inference_expert
    - visualization_communication_expert
---

# Ocean Scientific Visualization

Use this Manual when a WorkOrder asks for a map, plot, interactive view, visual analysis, or figure-bearing report. It defines a decision process and quality bar, not a mandatory checklist of plot types.

## Start from the scientific message

Before plotting, write one sentence stating the conclusion the view should support and identify the exact computed evidence behind it. If the evidence does not support that sentence, revise the claim or analysis before styling the figure.

Give every panel one question. Use a dominant panel for the main result and supporting panels only when they add different evidence. Do not publish separate artifacts for panels that are merely facets of one scientific comparison; keep them in one coherent interactive result when the renderer can represent them. Use separate views only for scientifically distinct results.

## Choose the smallest faithful representation

- Use a spatial map for geographic fields and selections.
- Use time series, profiles, sections, Hovmöller diagrams, scatter plots, or T-S diagrams when those coordinates directly express the evidence.
- Prefer an interactive view when hover values, coordinates, selections, comparison, or zoom materially help inspection.
- Always keep a readable static preview for export, history, and environments where interaction is unavailable.
- Put supporting provenance, sources, computation, notebook, checks, and limitations in the report rather than crowding the figure.

## Figure and panel contract

- Keep typography, units, symbols, axis direction, panel labels, spacing, and visual hierarchy consistent at final display size.
- Share axes, legends, and color scales only when the data are genuinely comparable. Otherwise make differences explicit.
- Prefer colorblind-safe sequential or diverging palettes with a scientifically meaningful midpoint. Avoid rainbow palettes and decorative color variation.
- Label every axis and colorbar with quantity and unit. State transformations, normalization, aggregation, and sampling when they affect interpretation.
- Show sample size, uncertainty, missingness, masks, or detection limits when material to the claim.
- Keep annotations sparse and evidence-linked; remove redundant titles, legends, and duplicated panels.
- Treat continuous matrices, unclassified samples, and classified samples as different evidence. Use
  `field2d` for a continuous matrix, `scatter` for samples, and `categories` for an actual grouping.
  Several colourings of the same coordinates are supporting diagnostics, not several identified groups.
- When a conclusion names classes or clusters, the saved view must carry the computed category values
  and visible category labels. If no assignment or defensible range was computed, revise the claim rather
  than asking the renderer to imply the categories.

## Ocean and map integrity

Verify the selected time/depth/region, coordinate names, longitude convention, latitude order, CRS or projection, grid registration, cell edges, masks, coastlines, and depth-axis direction. A visually plausible image is not evidence that coordinates are registered correctly.

For vectors, contours, transects, regions, or overlays, check that density and interpolation do not hide scale or manufacture structure. Do not compare panels with different ranges or masks as though they used one common scale.

## Interactive delivery contract

The view data must come from a succeeded durable code execution or an immutable accepted input, never from prose or truncated stdout. Append every scientifically distinct view and the optional report exactly once to the current task's ResultBundle. Each append returns a task-local TaskResultRef. A retry reuses the saved execution and existing TaskResultRefs; it must not recompute or create duplicate views.

Each interactive result needs a clear title, concise scientific summary, source dataset reference, units, bounded renderer-safe data, meaningful interaction metadata, and a static preview when available. Return the ResultBundle and TaskResultRefs directly to the Coordinator. The final Coordinator answer should cite those refs where the results are discussed; the task snapshot makes them openable in the Workbench without output Artifact registration, a DeliveryManifest, republishing, or synchronization. Raw IDs are not a user-facing delivery. Renderer validation selects interactive, preview, or file presentation after persistence. A preview/file fallback is a delivered result with limited interactivity, not a reason to rerun or re-register the computation.

## Final QA

Before submission, inspect the rendered result at its actual UI or export size and confirm:

1. the intended conclusion is visually supported without overclaiming;
2. values, units, ranges, masks, coordinates, and panel comparisons are truthful;
3. labels, legends, hover content, and colorbars are readable and non-overlapping;
4. interaction and static fallback show the same scientific selection;
5. every view is present in the current task ResultBundle, and the reproducible report closes with sources, code/notebook, checks, and limitations when a report was requested.

Stop and report a limitation rather than inventing missing units, coordinates, uncertainty, or unsupported scientific structure.
