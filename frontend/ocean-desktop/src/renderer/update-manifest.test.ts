import {createHash, generateKeyPairSync, sign} from 'node:crypto';

import {describe, expect, it} from 'vitest';

import {
  UPDATE_MANIFEST_SCHEMA,
  UpdateManifestError,
  assertDownloadedPackageIntegrity,
  canonicalUpdatePayloadForSigning,
  selectSignedUpdate,
  type UpdatePackage,
} from '../shared/update-manifest.js';

const keys = generateKeyPairSync('ed25519');
const packageBytes = Buffer.from('ocean-partner-signed-release', 'utf8');
const updatePackage: UpdatePackage = {
  platform: 'darwin',
  architecture: 'arm64',
  url: 'https://updates.oceanpartner.example/releases/0.1.1/Ocean-Partner-arm64.zip',
  sha256: createHash('sha256').update(packageBytes).digest('hex'),
  sizeBytes: packageBytes.byteLength,
};
const runtime = {
  version: '0.1.0',
  protocolVersion: 2,
  backendSchema: 'ocean-desktop-backend/v1',
  platform: 'darwin' as const,
  architecture: 'arm64' as const,
};

function signedManifest(overrides: Partial<{
  version: string;
  protocolVersion: number;
  backendSchema: string;
  packages: UpdatePackage[];
  keyId: string;
}> = {}) {
  const keyId = overrides.keyId ?? 'release-2026-a';
  const payload = {
    keyId,
    version: overrides.version ?? '0.1.1',
    protocolVersion: overrides.protocolVersion ?? 2,
    backendSchema: overrides.backendSchema ?? 'ocean-desktop-backend/v1',
    packages: overrides.packages ?? [updatePackage],
  };
  return {
    schema_version: UPDATE_MANIFEST_SCHEMA,
    key_id: keyId,
    release: {
      version: payload.version,
      protocol_version: payload.protocolVersion,
      backend_schema: payload.backendSchema,
      packages: payload.packages.map((item) => ({
        platform: item.platform,
        architecture: item.architecture,
        url: item.url,
        sha256: item.sha256,
        size_bytes: item.sizeBytes,
      })),
    },
    signature: sign(null, canonicalUpdatePayloadForSigning(payload), keys.privateKey).toString('base64'),
  };
}

function expectUpdateError(action: () => unknown, code: UpdateManifestError['code']) {
  expect(action).toThrow(expect.objectContaining({name: 'UpdateManifestError', code}));
}

describe('signed desktop update manifest', () => {
  it('selects one strictly newer package for the exact runtime target', () => {
    const selected = selectSignedUpdate(signedManifest(), runtime, {'release-2026-a': keys.publicKey});
    expect(selected.version).toBe('0.1.1');
    expect(selected.package).toEqual(updatePackage);
  });

  it('rejects a manifest whose signed content was changed', () => {
    const manifest = signedManifest();
    manifest.release.version = '0.1.2';
    expectUpdateError(() => selectSignedUpdate(manifest, runtime, {'release-2026-a': keys.publicKey}), 'invalid_signature');
  });

  it('rejects unknown keys and non-incrementing versions', () => {
    expectUpdateError(() => selectSignedUpdate(signedManifest({keyId: 'retired-key'}), runtime, {'release-2026-a': keys.publicKey}), 'unknown_signing_key');
    expectUpdateError(() => selectSignedUpdate(signedManifest({version: '0.1.0'}), runtime, {'release-2026-a': keys.publicKey}), 'version_not_newer');
    expectUpdateError(() => selectSignedUpdate(signedManifest({version: '0.0.9'}), runtime, {'release-2026-a': keys.publicKey}), 'version_not_newer');
  });

  it('rejects a signed update that is incompatible with the installed protocol or backend schema', () => {
    expectUpdateError(() => selectSignedUpdate(signedManifest({protocolVersion: 3}), runtime, {'release-2026-a': keys.publicKey}), 'protocol_mismatch');
    expectUpdateError(() => selectSignedUpdate(signedManifest({backendSchema: 'ocean-desktop-backend/v2'}), runtime, {'release-2026-a': keys.publicKey}), 'backend_schema_mismatch');
  });

  it('requires exactly one package for the platform and architecture', () => {
    expectUpdateError(() => selectSignedUpdate(signedManifest({packages: [{...updatePackage, architecture: 'x64'}]}), runtime, {'release-2026-a': keys.publicKey}), 'unsupported_target');
    expectUpdateError(() => selectSignedUpdate(signedManifest({packages: [updatePackage, updatePackage]}), runtime, {'release-2026-a': keys.publicKey}), 'unsupported_target');
  });

  it('rejects partial and hash-mismatched package bytes before staging', () => {
    assertDownloadedPackageIntegrity(updatePackage, packageBytes);
    expectUpdateError(() => assertDownloadedPackageIntegrity(updatePackage, packageBytes.subarray(0, -1)), 'package_integrity');
    expectUpdateError(() => assertDownloadedPackageIntegrity({...updatePackage, sha256: '0'.repeat(64)}, packageBytes), 'package_integrity');
  });
});
