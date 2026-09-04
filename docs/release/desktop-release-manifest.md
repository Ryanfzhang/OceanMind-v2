# Desktop Release Manifest

Run this after building a target-platform desktop bundle and before signing or
uploading it. The generator is local-only: it does not contact an update
service, sign a binary, or publish anything.

`npm run package:dir` now verifies the sidecar twice: once from the local
staging directory before Electron packaging, then again from the final app
bundle. Every target must complete a Desktop Protocol v2 handshake and emit a
machine-readable `doctor` report. On macOS, packaging additionally requires
the Seatbelt AnalysisRun capability and a model-free `sandbox-self-check`; the
self-check launches a constrained child from the exact sidecar binary and
proves that it can write only its declared output while an unmounted private
file remains unreadable. On Windows, the package spike instead requires
`doctor` to report no sandbox backend and `analysis_run_execution: false`.
That is an intentional fail-closed assertion until the Windows trusted-execution
broker exists; it is not a Windows release approval.

`package:dir` is intentionally the CI verification artifact, not the release
payload. The release commands first run the same build and sidecar checks, then
produce the install/update formats that an operator can inspect, sign, and bind
to the signed update manifest:

```bash
cd frontend/ocean-desktop
npm run build:sidecar
npm run package:release:mac
# Produces unsigned zip and dmg artifacts for the exact Node/Electron/sidecar architecture.

# Run on Windows.
npm run build:sidecar
npm run package:release:win
# Produces an unsigned NSIS installer and its win-unpacked verification bundle.
```

Protected release infrastructure uses `package:release:mac:signed` and
`package:release:win:signed` instead. Those commands require all relevant
certificate/notarization credentials, force `electron-builder` to fail when a
signing identity is absent, and verify the final delivery artifacts. See the
[Desktop signing runbook](desktop-signing-runbook.md); do not run a signed
command with credentials copied into a local shell.

`package:release:*` is deliberately native-only. Before Electron Builder runs,
it reads the frozen sidecar executable and requires its architecture to match
`process.arch`, then passes that exact architecture to Electron Builder. After
the regular frozen-sidecar verifier, it also launches the packaged Electron app
with inherited development-runtime paths removed and creates one task. This
prevents an Apple Silicon machine running an x64 Node process through Rosetta
from labelling an x64 frozen Python sidecar as arm64, or a release archive from
silently relying on a developer Python. Build macOS arm64 and x64 on separate
native Node/Python runners; build the Windows installer on native Windows.
Cross-target archives are not supported because the frozen sidecar is part of
the application ABI.

These commands do not bypass signing. The macOS archive/disk image still needs
Developer ID signing and notarization; the Windows NSIS installer still needs
Authenticode. The archive selected for a release manifest must be the reviewed,
signed final artifact, not an unsigned CI output.

```bash
make release-manifest \
  APP="frontend/ocean-desktop/dist/mac/Ocean Research Partner.app" \
  VERSION="0.1.5" \
  OUTPUT="release-metadata/macos-x64-0.1.5"
```

`OUTPUT` must not already exist. The generator writes a temporary sibling
directory and renames it only after all documents are complete:

- `checksums.json`: SHA-256 and byte count for every regular bundle file, plus
  a deterministic aggregate bundle digest and any contained symlinks.
- `sbom.cdx.json`: CycloneDX 1.5-style application/component inventory from
  the desktop npm lockfile and frozen sidecar Python metadata. The generator
  recognizes both macOS `Contents/Resources/sidecar` and Windows
  `resources/sidecar` layouts, and rejects an ambiguous or missing sidecar.
- `licenses.json`: normalized component/version/license view for review.

The command rejects a bundle entry whose symlink resolves outside the bundle.
It also refuses to overwrite a release metadata directory, so a failed or
partial regeneration cannot silently replace the evidence associated with an
already reviewed package.

The macOS and Windows package CI jobs run this generator against their actual
unsigned `dist` bundle after sidecar verification. That validates the target
directory layout and dependency discovery; it does not make either package
signed, notarized, or release-approved.

Each target job then reruns the packaged verifier with `--output` and uploads
the resulting `package-verification.json` beside the bundle metadata. Before
the sidecar starts, that verifier reads the final Electron executable and the
frozen sidecar binary, requiring both to agree with the current native target
architecture (Mach-O on macOS, PE on Windows). It also reads the bounded,
path-free `ocean-scientific-runtime.json` beside the final sidecar, invokes the
same final executable's `scientific-runtime` command, and rejects any canonical
JSON or SHA-256 mismatch. The attestation records only target
platform/architecture, required backend schema, reported sandbox/self-check
contract, and the scientific-runtime schema/fingerprint/dependency count; it
intentionally excludes the manifest itself, absolute build paths, workspace
paths, archive bytes and renderer-visible resource grants. Review the target
job's metadata and attestation together before treating a packaged-dir spike as
evidence.

These package-time frozen-sidecar probes allow a 120-second cold-start budget:
they load the complete scientific Python environment and are not part of the
interactive request latency budget. A timeout remains a fail-closed package
verification failure.

The same target job launches the packaged Electron executable with inherited
Python/Node development paths removed, starts the frozen sidecar through the
typed bridge, and creates one task in a fresh local workspace. Its
`packaged-launch.json` records only the target identity, reported backend schema
and successful packaged/task state. This is a package-contained-runtime smoke,
not a substitute for a clean physical machine, platform signing, or a Windows
trusted-execution approval.

This package verification does not prove an end-to-end updater. The desktop now
has signed-manifest admission and a constrained platform-updater handoff, but a
release still requires F6 target platform signing/notarization, checksum
publication, atomic replacement/rollback policy, and clean-machine validation
gates in `plan_frontend.md`.
