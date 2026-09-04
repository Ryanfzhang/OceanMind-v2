# Desktop Support Policy

## Current Scope

Ocean Research Partner Desktop is a local-first research workbench under active
development. A macOS packaged-dir build and frozen-sidecar handshake are
verified in this repository. The Windows package CI spike builds the target
bundle, includes the native `ocean-sandbox-broker.exe` next to the frozen
sidecar, and requires both the sidecar and the broker to report execution as
unavailable. CI is configured to execute its native broker contract on a
Windows runner, covering the current AppContainer/Job probes and fail-closed
handle-path guard; its retained target-runner result still needs review. That
verifies fail-closed packaging and a narrow native foundation only, not Windows
trusted execution. Code signing/notarization candidate automation is
available through the protected release workflow, but no signed target artifact
has yet been reviewed. Automatic updates and rollback are not yet release-ready.

The application must fail closed when its packaged sidecar, sandbox capability,
or required scientific runtime is unavailable. It must not substitute a system
Python interpreter or unsandboxed execution for a packaged analysis run.
The package verifier records only the frozen scientific runtime's schema,
fingerprint and dependency count; its path-free baseline document remains inside
the sidecar bundle.

## Supported Evidence

When reporting an issue, include only non-sensitive information:

- Desktop application version and target platform/architecture.
- `ocean doctor` capability summary with secrets removed.
- Whether the failure occurs before backend ready, during task interaction, or
  during a bounded AnalysisRun.
- The relevant opaque request, run, attempt, artifact, or export ID.
- A redacted diagnostic excerpt only when its disclosure policy explicitly
  permits it.

Do not attach API keys, workspace databases, raw NetCDF/PDF files, complete
model transcripts, unredacted execution logs, generated source code containing
research-sensitive material, or packaged release credentials.

## Recovery Expectations

Project state is local. Before upgrading a future signed release, keep a
private copy of the workspace's `.oceanmind` state and immutable
artifact files. The current release tooling provides checksums, SBOM, license
metadata, signed-manifest admission, and a constrained handoff to the native
macOS/NSIS updater. The default package has updates explicitly disabled, and
there is no reviewed hosted feed, signed update run, previous-version retention,
or rollback evidence yet. Treat updates and rollback as unavailable until the
release owner has completed the target-machine matrix.

If a foreground request, sidecar, or renderer exits unexpectedly, reopen the
same workspace and inspect its task snapshot and terminal state. The backend
should recover interrupted requests without replaying a completed mutation.
Report a duplicate artifact, run, or terminal event as a correctness issue.

If the platform loses WebGL/GPU rendering, the Spatial Workbench deliberately
stops its map renderer and layer controls rather than presenting a stale map.
The linked visualization dock and task context remain available; reopening the
application is the supported recovery path for the current session.

## Release Boundaries

Only a package built and verified on its target platform may be described as a
candidate for that platform. A macOS build does not prove Windows support, and
a successful frozen-sidecar handshake does not prove signing, notarization,
clean-machine installation, update, rollback, or a researcher walkthrough.

The release owner must retain the generated release metadata alongside the
exact bundle submitted for signing. See the [Desktop signing
runbook](desktop-signing-runbook.md), [Desktop release
metadata](desktop-release-manifest.md), and F5/F6 in `plan_frontend.md` for
the remaining release gates.

## Development Performance Baseline

Run `npm --prefix frontend/ocean-desktop run benchmark:smoke` on a target
platform to record renderer-ready, sidecar-ready, task-snapshot, and map-canvas
timings without a model call or external dataset. Pass `-- --output PATH` to
retain the JSON result. CI or a release operator can set
`OCEAN_BENCHMARK_MAX_RENDERER_READY_MS`,
`OCEAN_BENCHMARK_MAX_BACKEND_READY_MS`,
`OCEAN_BENCHMARK_MAX_TASK_SNAPSHOT_MS`, and
`OCEAN_BENCHMARK_MAX_MAP_CANVAS_READY_MS` to turn an agreed baseline into a
failing regression gate.

Run `npm --prefix frontend/ocean-desktop run benchmark:linked-plot` to exercise
the full desktop `MapScene` selection -> `ocean-artifact://` broker -> JSON
parse -> uPlot path with exactly 50,000 time-series points. The result includes
the point count, payload bytes, click-to-canvas-ready timing, and sampled
non-transparent canvas pixels; `OCEAN_BENCHMARK_MAX_LINKED_PLOT_READY_MS` can
turn the timing into an agreed target-platform regression gate. The fixture is
local-only and bounded to the renderer's normal 5 MiB artifact limit. The
2026-07-15 macOS x64 development baseline was 98 ms for a 1,144,730-byte
payload; it is informational, not a cross-platform release threshold.

Run `npm --prefix frontend/ocean-desktop run benchmark:spatial-rasters` to
exercise four real `2048x2048` MapLibre image sources through the same bounded
artifact broker. It records source-ready time, brokered bytes, a screenshot
pixel check for the rendered raster colors, an estimated decoded RGBA texture
working set, and Chromium's non-GPU JS heap fields when available. Set
`OCEAN_BENCHMARK_MAX_SPATIAL_RASTER_READY_MS` only after reviewing a target
platform baseline. The 2026-07-15 macOS x64 development baseline was 247 ms
for an estimated 64 MiB decoded raster workload; it is informational and not a
measurement of GPU memory.

## Visual QA

Run `npm --prefix frontend/ocean-desktop run capture:fixture -- --output PATH`
to capture the deterministic checkpoint fixture at a fixed 1440 x 920,
100%-zoom wide workspace. It exercises the real Electron window, sidecar,
Protocol v2 task restore, MapLibre surface, layer controls, and composer without
a model call or external dataset. Review this screenshot whenever shared layout,
map controls, task readability, or focus styling changes; it is visual evidence,
not a pixel-perfect cross-platform release baseline.

The 2026-07-16 local visual review confirmed that the restored fixture keeps its
spatial field visible on the actual MapLibre canvas while the task, evidence
output shelf, composer, and map-linked dock remain legible. The associated
TypeScript check, 19 Electron scenarios, and 50 sandbox/runtime/spatial
publication tests passed. This is engineering evidence only; it does not
replace a signed-package, target-GPU, or assistive-technology review.

The Electron suite also compares that same fixed 1440 x 920 macOS workbench
fixture against a reviewed screenshot baseline after it confirms the real
MapLibre raster is visible. This guards task/sidebar/map/dock/composer visual
regressions in CI; the Windows visual baseline remains an explicit release
gate rather than an inferred cross-platform equivalence claim.

These benchmarks do not replace target-platform GPU-memory profiling or an
assistive-technology audit. Those remain separate release gates.

The Desktop Electron suite also enforces a structural accessibility baseline:
visible controls must have an accessible name, the persistent map and bounded
LinkedPlot canvas expose concise non-data descriptions, and modal dialogs keep
Tab/Shift+Tab inside their focus scope, dismiss only through their enabled
close/cancel semantics, and restore focus after close. This is not a substitute
for screen-reader or target-platform scaling review. The automated renderer
contract also verifies system reduced-motion and forced-colors preferences, but
manual high-contrast and assistive-technology review remain release gates.
