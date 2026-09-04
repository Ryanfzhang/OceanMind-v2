# Desktop Signing Runbook

`desktop-signed-release.yml` builds release candidates only from an existing,
immutable `v<package-version>` tag. It does not create a public GitHub Release
or publish an update manifest. The protected `desktop-release` environment is
the only workflow environment that may expose signing secrets.

## Required GitHub Environment Secrets

macOS requires all of the following:

- `MAC_CSC_LINK`: base64-encoded Developer ID Application `.p12`.
- `MAC_CSC_KEY_PASSWORD`: `.p12` password.
- `APPLE_ID`: notarization Apple ID.
- `APPLE_APP_SPECIFIC_PASSWORD`: Apple app-specific password.
- `APPLE_TEAM_ID`: Apple Developer Team ID.

Windows requires:

- `WIN_CSC_LINK`: base64-encoded Authenticode `.pfx`.
- `WIN_CSC_KEY_PASSWORD`: `.pfx` password.

Set these only in the protected GitHub environment, never as repository,
workflow, issue, or local shell values. `electron-builder` reads the values
directly; the repository does not decode, log, or write a certificate.

## Candidate Build Contract

The workflow runs x64 macOS on `macos-15-intel`, arm64 macOS on `macos-14`,
and Windows x64 on `windows-latest`. Each target uses native Python and Node
to build the frozen sidecar. The target check rejects an Electron/sidecar ABI
mismatch before signing.

`package:release:*:signed` requires credentials before starting its build and
passes `forceCodeSigning=true` to `electron-builder`. This prevents the normal
unsigned fallback from producing a release artifact. On macOS, the Apple
notarization credentials cause `electron-builder` to notarize and staple the
app/DMG. On Windows, the dedicated Authenticode certificate signs the unpacked
app executable, frozen sidecar executable, and NSIS installer.

The final verifier requires strict macOS `codesign`, Developer ID authority,
stapler validation and Gatekeeper assessment, or `Valid` Authenticode status
and signer identity on all three Windows executable surfaces. Both targets
also run the existing frozen-sidecar doctor, sandbox contract, protocol
handshake, and packaged Electron launch without inherited Python/Node paths.

The workflow uploads signed delivery files and path-free release metadata as
private GitHub Actions artifacts. A release owner must inspect those artifacts
and retain the exact metadata next to the eventual public delivery files.

## Before Publishing

Do not publish a signed candidate until all of these are recorded separately:

- Target-machine installation and first launch with no development runtime.
- macOS Gatekeeper/notarization and Windows SmartScreen/Authenticode review.
- Upgrade, interrupted update, rollback, uninstall/reinstall and sleep/wake
  results for the exact signed artifacts, using the [desktop lifecycle
  validation matrix](desktop-lifecycle-validation.md).
- Windows trusted-execution broker status. It remains fail closed and does not
  become trusted merely because the containing sidecar is signed.

The signed update manifest and installer replacement/rollback mechanism remain
separate from this workflow; see `desktop-update-contract.md`.
