# Desktop Workspace Interaction Contract

This document freezes the v1 interaction shape for Ocean Research Partner. It
describes the product surface that the renderer may refine, but must not
silently rearrange or replace with a generic dashboard.

## Wide Workspace

The normal workspace is a single task-first workbench at window widths of
1200 CSS pixels and above.

```text
+------------------+--------------------------+----------------------+
| Project and task | Current research task    | Persistent map       |
| sidebar           | transcript and composer | and linked views     |
|                  |                          |                      |
| project picker   | task title and controls  | map controls         |
| task create      | output shelf             | spatial field        |
| task search      | evidence inspector       | point/transect       |
| sources          | conversation             | linked plot dock     |
| task history     | composer                 |                      |
+------------------+--------------------------+----------------------+
```

- The sidebar is 252 px. It owns project selection, task lifecycle, sources,
  and task history; it does not become a second artifact dashboard.
- The conversation pane is the primary task surface and remains at least
  420 px wide. Its output shelf is task-scoped, not a global gallery.
- The spatial pane remains mounted while a task is active. It is at least
  360 px wide and contains the map, map-local controls, and the linked-view
  dock. Spatial fields overlay the map; point, region, and transect selections
  open their linked visualization in the dock.
- Conversation and spatial focus modes may temporarily expand one pane. They
  do not discard the active task, map scene, selected feature, or linked plot.

## Compact Workspace

At widths below 1200 CSS pixels, the sidebar is 210 px and the remaining area
shows exactly one primary pane: Task or Map. A fixed, semantic two-button
switcher changes between them. It must remain visible, within the viewport,
and keyboard-operable at the supported minimum window size of 1024 x 680 and
at 200% browser zoom.

The compact state is a presentation choice only. Switching panes does not
restart the backend, clear a selection, reload a map scene, or re-request a
linked plot. If WebGL becomes unavailable, the map explicitly enters its
rendering-unavailable state; task context and the linked dock remain usable.

## Keyboard And Focus Contract

The application favors standard browser focus order over hidden global
shortcuts. A keyboard action must use the same typed UI intent as its pointer
equivalent.

| Action | Contract |
| --- | --- |
| Command palette | `Cmd+K` on macOS and `Ctrl+K` elsewhere toggles the command palette. |
| Command action | `Tab` moves through its enabled actions; `Enter` invokes the focused typed action and closes the palette. |
| Task composer | `Enter` submits a non-empty request; `Shift+Enter` inserts a line break. |
| New task | `Enter` in the New research task field creates the task. |
| Rename | `Enter` commits the title; `Escape` abandons the draft. |
| Compact view | Task and Map controls are ordinary semantic buttons; `Space` and `Enter` invoke them. |
| Dialogs | Opening focuses the topmost dialog. `Tab` and `Shift+Tab` stay inside it; `Escape` invokes an enabled explicit close or cancel action only; normal dismissal restores the prior control. |

No keyboard action may directly mutate task, artifact, run, permission, or map
state outside the Protocol v2 request path. Command-palette entries are merely
discoverable names for existing typed actions.

## Stable State Boundaries

- A task switch during an active request requires the existing explicit
  Keep Current or Cancel and Switch choice.
- The renderer can render task transcript, map projections, and task-scoped
  artifact summaries, but never checkpoint messages, sidecar paths, or raw
  diagnostics.
- Map selections enter a request only through the explicit Use in task action,
  as exact artifact references.
- Dialogs and focused panes are ephemeral view state. Task transcript,
  checkpoint, task map state, artifacts, and run evidence remain backend-owned
  durable state.

## Regression Evidence

The Electron accessibility contract verifies named controls, the map and linked
plot semantics, command-palette focus trapping/restoration, and keyboard
activation of the composer action. The compact workspace contract verifies the
Task/Map buttons by keyboard at the minimum size and 200% zoom. See
`frontend/ocean-desktop/tests/accessibility-contract.spec.ts` and
`frontend/ocean-desktop/tests/security-boundary.spec.ts`.
