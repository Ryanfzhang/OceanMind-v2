# Desktop Update Admission Contract

This document defines the fail-closed admission and transport boundary for the
desktop update path. It is intentionally separate from hosting, signing-key
custody, notarization, and target-machine evidence. An updater may prepare an
installation package only after this contract accepts it.

The implementation spans `src/shared/update-manifest.ts`,
`src/shared/update-transport.ts`, `src/shared/update-service.ts`, and
`src/shared/update-config.ts`. Its test suites use fresh Ed25519 keys and
exercise the same verification path that a release integration must use.

After release metadata and the target installer/archive have been reviewed,
the manifest can be produced locally without contacting an update service:

```bash
cd frontend/ocean-desktop
npm run sign:update-manifest -- \
  --input ../release-input.json \
  --private-key /secure/release-keys/ocean-desktop-ed25519.pem \
  --output ../release-manifest-0.1.1.json
```

The input uses the signed-manifest shape below but omits `signature`; each
package instead supplies a local `artifact_path` rather than `sha256` and
`size_bytes`. The tool requires an Ed25519 PKCS#8 private key, streams the
regular archive file to calculate those two fields itself, canonicalizes the
exact payload, refuses unknown fields or unsafe package metadata, and writes
a new output only. It will not overwrite an existing reviewed manifest.

## Signed Manifest

The release service publishes a JSON envelope with exactly these fields:

```json
{
  "schema_version": "ocean-desktop-update/v1",
  "key_id": "release-2026-a",
  "release": {
    "version": "0.1.1",
    "protocol_version": 2,
    "backend_schema": "ocean-desktop-backend/v1",
    "packages": [
      {
        "platform": "darwin",
        "architecture": "arm64",
        "url": "https://updates.example/releases/0.1.1/Ocean-Partner-arm64.zip",
        "sha256": "<64 lower-case hexadecimal characters>",
        "size_bytes": 123456
      }
    ]
  },
  "signature": "<base64 Ed25519 signature>"
}
```

The signed bytes are canonical JSON for the envelope without `signature`, with
object keys sorted recursively and no insignificant whitespace. The manifest
parser rejects unknown or missing fields rather than ignoring a release field.
This prevents a release publisher and client from interpreting the same signed
document differently. Before schema/signature validation, the bounded JSON
decoder also rejects duplicate object keys, non-finite numeric values and more
than 128 nested arrays/objects. This avoids accepting a manifest whose meaning
depends on a JSON library's duplicate-key behavior. JSON bytes must also be
strict UTF-8; malformed byte sequences are not silently replaced before
signature verification.

## Admission Rules

Before any download, the desktop updater must:

1. Look up `key_id` in its shipped trusted public-key set and verify the
   Ed25519 signature.
2. Require the candidate semantic version to be strictly greater than the
   installed version. Equal versions and downgrades are rejected.
3. Require exact equality for `protocol_version` and `backend_schema` with the
   installed desktop/sidecar pair. The packaged backend reports the latter as
   `backend_schema` in both its `system.ready` frame and `doctor` output; it is
   currently `ocean-desktop-backend/v1`.
4. Select exactly one package matching the current `darwin` or `win32` target
   and `arm64` or `x64` architecture. No cross-target fallback is allowed. A
   release package is built only when that Electron architecture agrees with
   the frozen sidecar architecture; the package command rejects a Rosetta or
   cross-build mismatch before signing can begin.
5. Require an HTTPS package URL without embedded credentials or fragments.
6. Verify both `size_bytes` and SHA-256 against the complete downloaded bytes
   before staging.

The package is not eligible for an install merely because a response has a
valid TLS connection or a matching filename.

## Transport Reconciliation

`src/shared/update-transport.ts` is the boundary between this signed manifest
and Electron Builder's platform updater. It is deliberately narrower than a
generic download API:

1. The main process first admits one `VerifiedUpdate` through the signed
   manifest above.
2. It disables automatic download, automatic install-on-quit, prerelease,
   downgrade, web-installer, and differential-download behavior on the platform
   updater.
3. It requires the updater's ordinary release feed to name the exact same
   version, one exact package URL, and the signed package byte count. A release
   feed cannot select a different installer just because it is served from the
   same HTTPS host.
4. After the platform updater downloads its installer/archive, the main process
   rejects a symbolic link, unexpected length, or SHA-256 mismatch against the
   signed manifest. It repeats that check immediately before `quitAndInstall`.
5. Only then can the updater hand off the application shutdown and replacement
   to its native macOS or NSIS mechanism. The renderer never receives a package
   path, updater object, or arbitrary feed URL.

`src/shared/update-service.ts` makes the outer sequence injectable: fetch a
signed manifest, compare it with the installed version/protocol/backend schema,
then ask the reconciled transport to prepare it. It does not check on startup,
download in the background, or install without an explicit caller. The default
packaged `resources/update-config.json` is explicitly disabled, so normal builds
have no update endpoint or trusted key set and Settings hides update controls.
When release engineering ships an enabled configuration, the typed renderer
bridge can only inspect status, check/prepare, or install an already prepared
package; it cannot supply an arbitrary URL, key, or filesystem path.

An enabled configuration has exactly this shape and must be placed in the
packaged `update-config.json` resource before signing the application:

```json
{
  "schema_version": "ocean-desktop-update-config/v1",
  "enabled": true,
  "manifest_url": "https://updates.example/manifests/stable.json",
  "feed_url": "https://updates.example/releases/",
  "trusted_keys": {
    "release-2026-a": "-----BEGIN PUBLIC KEY-----\\n...Ed25519 SPKI PEM...\\n-----END PUBLIC KEY-----\\n"
  }
}
```

The parser rejects unknown fields, clear-text or credential-bearing URLs,
non-Ed25519 keys, and a configuration with no keys. A production release must
change this file under the same signing controls as the application bundle.
`npm run verify:package` verifies that every macOS/Windows package contains a
valid configuration before its sidecar attestation is accepted.

Electron Builder's supported auto-update targets match this packaging plan:
macOS needs the zip alongside the user-facing dmg, and Windows uses NSIS. The
application uses `electron-updater` only for this platform-specific handoff; it
does not treat Electron's feed metadata as an authority for research-runtime
compatibility. See [Electron Builder's auto-update target guidance](https://www.electron.build/docs/features/auto-update/).

## Remaining Release Work

The following F6 gates remain open:

- Sign and notarize the macOS installer; Authenticode-sign the Windows
  installer.
- Ship an immutable update configuration containing the reviewed HTTPS manifest
  URL, release-feed URL and trusted public-key set; keep key rotation and
  offline behavior auditable.
- Exercise `electron-updater` against signed macOS zip and Windows NSIS
  packages, preserve a recoverable previous app/sidecar pair where the target
  installer supports it, and make failed startup/rollback observable.
- Provide the installed runtime identity used above from the final packaged
  app and frozen sidecar, then test upgrade, rollback, partial-download and
  crash recovery on clean macOS and Windows machines.

The current tests cover invalid signatures, unknown keys, downgrade/equal
versions, protocol and backend-schema mismatches, ambiguous targets, and
partial or hash-mismatched package bytes. They do not claim a hosted updater
or a signed installer exists.

## Local Staging Journal

`src/shared/update-staging.ts` implements the first local transaction boundary
for a future updater. It accepts only an exact runtime `VerifiedUpdate` record
that retains the admission contract's semantic version, backend schema, HTTPS
package URL, platform/architecture, SHA-256 and byte-count constraints. It
writes to a private per-user staging directory, enforces the signed byte count
while streaming, syncs a temporary archive, verifies the SHA-256, renames it to
its final staging name, and only then atomically writes a compact journal. On
macOS, it also fsyncs the staging directory after each rename so a power loss
cannot leave a journal referring to an archive rename that was never made
durable. On startup, `recover()` accepts only a bounded canonical journal and
exactly the two expected regular files (`package` and `transaction.json`);
their archive bytes and immutable metadata must still agree. Partial,
unjournaled, oversized, non-canonical, extra-content, or subsequently modified
staging directories are discarded. The Electron main process runs this cleanup
after creating the workbench window; it does not send staging paths or archive
bytes across the renderer bridge.

This makes a crash during download or staging recoverable without treating a
partial archive as an update. The staging journal is independent from the
platform updater's installer cache; it remains useful for an explicit local
download workflow, but it is not treated as proof that an installed app has
been replaced. Atomic replacement, previous-version retention, rollback and
clean-machine recovery remain open until they are demonstrated with signed
target packages.
