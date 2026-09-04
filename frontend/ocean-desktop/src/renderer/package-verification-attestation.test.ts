import {mkdtemp, readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

import {describe, expect, it} from 'vitest';

import {
  packageVerificationAttestation,
  writePackageVerificationAttestation,
} from '../../scripts/package-verification-attestation.mjs';

describe('package verification attestation', () => {
  it('writes a compact, path-free target verification record exactly once', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-package-attestation-'));
    const report = packageVerificationAttestation({
      platform: 'win32',
      architecture: 'x64',
      backendSchema: 'ocean-desktop-backend/v1',
      sandbox: {available: false, backend: null, reason: 'trusted execution broker is not ready'},
      sandboxSelfCheck: null,
      scientificRuntime: {
        schema_version: 'ocean-frozen-scientific-runtime/v1',
        fingerprint_sha256: 'a'.repeat(64),
        dependency_count: 12,
      },
    });

    const output = writePackageVerificationAttestation('evidence/package-verification.json', report, root);
    expect(output).toBe(join(root, 'evidence', 'package-verification.json'));
    expect(JSON.parse(await readFile(output, 'utf8'))).toEqual(report);
    expect(JSON.stringify(report)).not.toContain(root);
    expect(() => writePackageVerificationAttestation('evidence/package-verification.json', report, root)).toThrow('already exists');
  });
});
