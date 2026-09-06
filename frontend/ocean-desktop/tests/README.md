# Desktop interaction regression tests

Run from `frontend/ocean-desktop`:

```sh
npm run e2e:research
```

Install repository Python dependencies and Desktop npm dependencies first. The suite uses
the checkout `.venv` (`OCEAN_PYTHON` can select another interpreter), the built Electron app,
and the actual Python host, protocol router, task database, orchestration and result services.
No separately downloaded Playwright browser is required: Electron provides Chromium.
Linux needs a graphical session, for example `xvfb-run -a npm run e2e:research`.

The test-only backend replaces Coordinator/model execution and Expert participant execution
with deterministic runtime doubles. It does **not** replace the renderer, request routing,
paper-selection futures, cancellation, persistence, Curator commit or resource authorization.
These tests validate product integration, not real-model reasoning, provider adapters,
research quality, or whether a generated scientific notebook executes correctly.

Coverage:

- Project/Task creation through Enter and the arrow, project-level `+`, hover visibility,
  stable ordering, task switching and persisted records.
- Inline two-column paper selection, multiselect, double-click protection, continuation
  of the same request and stable Expert identity.
- Cancellation during generation and during paper selection; immediate follow-up.
- Historical Canvas/answer/supplementary ordering after reopening, authorized notebook
  opening and preservation of a user's edits across later analysis rounds.
- Curator result-change notifications on an already-open task and persistence on reopening.
- English and Chinese task-creation copy.

Each test uses a new Electron profile and temporary project marked `.e2e-project`. Inherited
model credentials are removed, fixture Python outbound connections fail closed, and renderer
HTTP requests are blocked. Native folder selection is stubbed; notebook opening records the
authorized OS path instead of launching the user's notebook editor. Backend fixtures refuse
unmarked projects and are available only through the existing unpackaged Desktop test hook.

Assertions poll real state; there are no fixed UI sleeps or automatic test retries. On failure,
Playwright retains a trace, screenshot, renderer errors and backend diagnostics in `test-results/`.
The isolated project path is attached to the result for local investigation. Temporary test
projects are retained (never reuse an actual research directory). CI uploads failure artifacts.
`npm run e2e:check` checks the tests' TypeScript contracts without launching Electron.
