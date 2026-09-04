import {createHash} from 'node:crypto';
import {lstat, mkdtemp, readFile, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

import {describe, expect, it} from 'vitest';

import {UpdateHandoffStore} from '../shared/update-handoff.js';
import type {UpdateRuntime, VerifiedUpdate} from '../shared/update-manifest.js';

const archive = Buffer.from('signed update archive');

function runtime(version = '0.1.0'): UpdateRuntime {
  return {
    version,
    protocolVersion: 2,
    backendSchema: 'ocean-desktop-backend/v1',
    platform: 'darwin',
    architecture: 'arm64',
  };
}

function update(version = '0.1.1'): VerifiedUpdate {
  return {
    version,
    protocolVersion: 2,
    backendSchema: 'ocean-desktop-backend/v1',
    package: {
      platform: 'darwin',
      architecture: 'arm64',
      url: `https://updates.example/releases/${version}/Ocean-Partner-arm64.zip`,
      sha256: createHash('sha256').update(archive).digest('hex'),
      sizeBytes: archive.byteLength,
    },
  };
}

describe('desktop update handoff journal', () => {
  it('records a private exact handoff and confirms the expected target runtime on next launch', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-handoff-'));
    const store = new UpdateHandoffStore(root);
    await store.recordInstallIntent(runtime(), update());

    const journalPath = join(root, 'handoff.json');
    expect((await lstat(journalPath)).isFile()).toBe(true);
    expect(await readFile(journalPath, 'utf8')).toContain('"schema_version":"ocean-desktop-update-handoff/v1"');
    await expect(store.observeLaunch(runtime('0.1.1'))).resolves.toEqual({state: 'applied', version: '0.1.1'});
    await expect(store.observeLaunch(runtime('0.1.1'))).resolves.toEqual({state: 'none'});
  });

  it('records when the previous exact runtime resumes without calling it a successful upgrade', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-handoff-'));
    const store = new UpdateHandoffStore(root);
    await store.recordInstallIntent(runtime(), update());

    await expect(store.observeLaunch(runtime())).resolves.toEqual({state: 'previous_runtime_resumed', version: '0.1.0'});
  });

  it('rejects incompatible handoffs and discards malformed or unexpected launch records', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-handoff-'));
    const store = new UpdateHandoffStore(root);
    await expect(store.recordInstallIntent(runtime(), update('0.1.0'))).rejects.toThrow('incompatible');
    await store.recordInstallIntent(runtime(), update());
    await expect(store.observeLaunch(runtime('0.2.0'))).resolves.toEqual({
      state: 'unexpected_runtime',
      expectedVersion: '0.1.1',
      observedVersion: '0.2.0',
    });

    await store.recordInstallIntent(runtime(), update());
    const journalPath = join(root, 'handoff.json');
    const journal = await readFile(journalPath, 'utf8');
    await writeFile(journalPath, journal.replace('"schema_version"', '"schema_version":"tampered","schema_version"'));
    await expect(store.observeLaunch(runtime())).resolves.toEqual({state: 'discarded'});
  });
});
