import {createHash} from 'node:crypto';
import {mkdtemp, mkdir, readdir, readFile, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Readable} from 'node:stream';

import {describe, expect, it} from 'vitest';

import {UpdateStagingStore} from '../shared/update-staging.js';
import type {VerifiedUpdate} from '../shared/update-manifest.js';

function verifiedUpdate(bytes: Buffer): VerifiedUpdate {
  return {
    version: '0.1.1',
    protocolVersion: 2,
    backendSchema: 'ocean-desktop-backend/v1',
    package: {
      platform: 'darwin',
      architecture: 'arm64',
      url: 'https://updates.example/releases/0.1.1/Ocean-Partner-arm64.zip',
      sha256: createHash('sha256').update(bytes).digest('hex'),
      sizeBytes: bytes.byteLength,
    },
  };
}

describe('verified desktop update staging', () => {
  it('journals a complete verified archive and recovers it after a restart', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-stage-'));
    const bytes = Buffer.from('complete desktop update archive');
    const store = new UpdateStagingStore(root);
    const staged = await store.stage(verifiedUpdate(bytes), Readable.from([bytes.subarray(0, 9), bytes.subarray(9)]));

    expect(await readFile(staged.archivePath)).toEqual(bytes);
    const recovered = await new UpdateStagingStore(root).recover();
    expect(recovered.discardedDirectories).toEqual([]);
    expect(recovered.staged).toHaveLength(1);
    expect(recovered.staged[0]?.update).toEqual(verifiedUpdate(bytes));
  });

  it('keeps the staged archive and journal as ordinary private files after durable rename', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-stage-'));
    const bytes = Buffer.from('durable staged archive');
    const staged = await new UpdateStagingStore(root).stage(verifiedUpdate(bytes), Readable.from([bytes]));

    const entries = (await readdir(staged.directory)).sort();
    expect(entries).toEqual(['package', 'transaction.json']);
    expect(await readFile(staged.archivePath)).toEqual(bytes);
  });

  it('removes an interrupted or integrity-failed download instead of making it recoverable', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-stage-'));
    const expected = Buffer.from('expected update archive');
    const store = new UpdateStagingStore(root);
    await expect(store.stage(verifiedUpdate(expected), Readable.from([Buffer.from('tampered archive')]))).rejects.toThrow('verification');

    const interrupted = join(root, 'update-stage-interrupted');
    await mkdir(interrupted);
    await writeFile(join(interrupted, 'package.part'), expected);
    const recovery = await store.recover();
    expect(recovery.staged).toEqual([]);
    expect(recovery.discardedDirectories).toEqual([interrupted]);
    expect(await readdir(root)).toEqual([]);
  });

  it('refuses caller-supplied records that do not retain the verified update shape', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-stage-'));
    const bytes = Buffer.from('untrusted caller record');
    const update = verifiedUpdate(bytes);
    const malformed = {
      ...update,
      package: {...update.package, url: 'http://updates.example/unsigned.zip'},
    } as unknown as VerifiedUpdate;

    await expect(new UpdateStagingStore(root).stage(malformed, Readable.from([bytes])))
      .rejects.toThrow('exact verified update record');
    expect(await readdir(root)).toEqual([]);
  });

  it('discards a staged directory whose archive no longer matches its journal', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-stage-'));
    const bytes = Buffer.from('verified archive before tampering');
    const store = new UpdateStagingStore(root);
    const staged = await store.stage(verifiedUpdate(bytes), Readable.from([bytes]));
    await writeFile(staged.archivePath, 'changed after validation');

    const recovery = await store.recover();
    expect(recovery.staged).toEqual([]);
    expect(recovery.discardedDirectories).toEqual([staged.directory]);
  });

  it('discards journals with unsafe metadata and stage directories with extra contents', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-stage-'));
    const bytes = Buffer.from('strict recovery metadata');
    const store = new UpdateStagingStore(root);
    const journalTampered = await store.stage(verifiedUpdate(bytes), Readable.from([bytes]));
    const journal = JSON.parse(await readFile(join(journalTampered.directory, 'transaction.json'), 'utf8'));
    journal.update.package.url = 'http://updates.example/unsigned.zip';
    await writeFile(join(journalTampered.directory, 'transaction.json'), JSON.stringify(journal));

    const firstRecovery = await store.recover();
    expect(firstRecovery.staged).toEqual([]);
    expect(firstRecovery.discardedDirectories).toEqual([journalTampered.directory]);

    const extraFile = await store.stage(verifiedUpdate(bytes), Readable.from([bytes]));
    await writeFile(join(extraFile.directory, 'unexpected.txt'), 'not part of the stage contract');
    const secondRecovery = await store.recover();
    expect(secondRecovery.staged).toEqual([]);
    expect(secondRecovery.discardedDirectories).toEqual([extraFile.directory]);

    const nonCanonicalTime = await store.stage(verifiedUpdate(bytes), Readable.from([bytes]));
    const timestampJournal = JSON.parse(await readFile(join(nonCanonicalTime.directory, 'transaction.json'), 'utf8'));
    timestampJournal.staged_at = '2026-07-16T06:36:12+00:00';
    await writeFile(join(nonCanonicalTime.directory, 'transaction.json'), JSON.stringify(timestampJournal));
    const thirdRecovery = await store.recover();
    expect(thirdRecovery.staged).toEqual([]);
    expect(thirdRecovery.discardedDirectories).toEqual([nonCanonicalTime.directory]);
  });

  it('discards a journal with duplicate JSON keys rather than accepting parser-dependent metadata', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-update-stage-'));
    const bytes = Buffer.from('duplicate journal key');
    const store = new UpdateStagingStore(root);
    const staged = await store.stage(verifiedUpdate(bytes), Readable.from([bytes]));
    const journalPath = join(staged.directory, 'transaction.json');
    const journal = await readFile(journalPath, 'utf8');
    await writeFile(journalPath, journal.replace('"schema_version"', '"schema_version":"tampered","schema_version"'));

    const recovery = await store.recover();
    expect(recovery.staged).toEqual([]);
    expect(recovery.discardedDirectories).toEqual([staged.directory]);
  });
});
