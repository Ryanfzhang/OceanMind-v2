import {execFile as execFileCallback} from 'node:child_process';
import {createHash, generateKeyPairSync} from 'node:crypto';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';

import {describe, expect, it} from 'vitest';

import {selectSignedUpdate} from '../shared/update-manifest.js';

const execFile = promisify(execFileCallback);
const rendererRoot = dirname(fileURLToPath(import.meta.url));
const desktopRoot = resolve(rendererRoot, '..', '..');
const signer = join(desktopRoot, 'scripts', 'sign-update-manifest.mjs');

describe('desktop update-manifest signing CLI', () => {
  it('writes a new Ed25519-signed manifest accepted by the desktop admission contract', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'ocean-update-manifest-'));
    try {
      const keys = generateKeyPairSync('ed25519');
      const input = join(directory, 'release-input.json');
      const privateKey = join(directory, 'release-key.pem');
      const output = join(directory, 'manifest.json');
      const archive = join(directory, 'Ocean-Partner-arm64.zip');
      const archiveBytes = Buffer.from('signed Ocean Research Partner archive', 'utf8');
      await writeFile(archive, archiveBytes);
      await writeFile(input, JSON.stringify({
        schema_version: 'ocean-desktop-update/v1',
        key_id: 'release-2026-a',
        release: {
          version: '0.1.1',
          protocol_version: 2,
          backend_schema: 'ocean-desktop-backend/v1',
          packages: [{
            platform: 'darwin',
            architecture: 'arm64',
            url: 'https://updates.oceanpartner.example/releases/0.1.1/Ocean-Partner-arm64.zip',
            artifact_path: 'Ocean-Partner-arm64.zip',
          }],
        },
      }, null, 2));
      await writeFile(privateKey, keys.privateKey.export({type: 'pkcs8', format: 'pem'}));

      await execFile(process.execPath, [signer, '--input', input, '--private-key', privateKey, '--output', output]);
      const manifest = JSON.parse(await readFile(output, 'utf8'));
      const update = selectSignedUpdate(manifest, {
        version: '0.1.0',
        protocolVersion: 2,
        backendSchema: 'ocean-desktop-backend/v1',
        platform: 'darwin',
        architecture: 'arm64',
      }, {'release-2026-a': keys.publicKey});
      expect(update.version).toBe('0.1.1');
      expect(update.package.sizeBytes).toBe(archiveBytes.byteLength);
      expect(update.package.sha256).toBe(createHash('sha256').update(archiveBytes).digest('hex'));

      await expect(execFile(process.execPath, [signer, '--input', input, '--private-key', privateKey, '--output', output])).rejects.toThrow(/EEXIST/);
    } finally {
      await rm(directory, {recursive: true, force: true});
    }
  });
});
