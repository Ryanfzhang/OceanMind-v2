# Desktop Lifecycle Validation Matrix

This matrix is the target-machine evidence required before F6 can claim a
supported desktop release. It supplements local unit/Electron/package checks;
it does not replace them. Run it only for exact signed candidate artifacts and
retain the resulting record beside that candidate's release metadata.

## Preconditions

- Candidate A and newer candidate B have the same approved update channel,
  expected platform/architecture, and frozen sidecar architecture.
- macOS candidates are Developer ID signed and notarized; Windows candidates
  are Authenticode signed. Record the verification output before installation.
- The update configuration inside A is enabled, names the reviewed HTTPS
  manifest/feed hosts, and contains the approved Ed25519 public keys.
- Each machine starts with no project workspace, no Python/Node development
  runtime on `PATH`, and no previous Ocean Research Partner installation.
- Use a disposable local workspace for lifecycle tests. Do not send a real
  research workspace or model credential to a release test machine.

## Matrix

| Scenario | macOS 13+ arm64/x64 | Windows 11 x64 | Required evidence |
| --- | --- | --- | --- |
| First install | Install signed dmg, launch once | Install signed NSIS package, launch once | Installer identity, Gatekeeper/SmartScreen outcome, app version, frozen-sidecar `doctor`, Protocol ready and one task creation |
| No development runtime | Reopen with Python/Node paths removed | Reopen with Python/Node paths removed | Packaged app starts its bundled sidecar only; no system interpreter fallback |
| Normal update | Install A, explicitly prepare and install B | Install A, explicitly prepare and install B | Signed manifest/URL/version record, B version after restart, backend schema/sidecar fingerprint, preserved workspace task and map state |
| Interrupted download | Stop network or terminate during prepare | Stop network or terminate during prepare | A remains runnable; no installer starts; staging recovery discards the partial archive |
| Interrupted install | Terminate only at a documented installer-safe point | Terminate only at a documented installer-safe point | Native installer result, current app version, workspace preservation, and whether manual repair is required |
| Failed first launch | Force B's test-only backend readiness failure | Force B's test-only backend readiness failure | Failure is observable, previous version/recovery policy is followed, no unbounded restart loop |
| Rollback | Apply the approved platform recovery procedure | Apply the approved platform recovery procedure | Restored app/sidecar version, preserved workspace, exact operator steps, and reason automatic rollback was or was not available |
| Uninstall/reinstall | Remove app, retain workspace, reinstall | Remove app, retain workspace, reinstall | Workspace retention matches installer policy; no stale sidecar or update cache makes the app appear upgraded |
| Sleep/wake | Sleep during idle sidecar and active task boundary | Sleep during idle sidecar and active task boundary | Backend recovery/health state, no duplicate task mutation, no orphan process tree |
| Accessibility/GPU | Screen reader, 200% scaling, forced colors, WebGL loss | Screen reader, 200% scaling, forced colors, WebGL loss | Manual assistive-technology notes, map fallback and LinkedPlot/task availability |

## Recording Rules

For every matrix cell, record the candidate SHA-256, platform/architecture,
installer signature result, app version before/after, backend schema, frozen
runtime fingerprint, timestamp, operator, and pass/fail rationale. Redact
workspace paths, tokens, research data, complete logs, and model content.

An interrupted install or rollback result is not allowed to be inferred from a
unit test. It must be reproduced with the actual target installer. If the
installer cannot automatically retain/restore a previous app version, document
the manual recovery procedure and keep the automatic rollback gate open.
