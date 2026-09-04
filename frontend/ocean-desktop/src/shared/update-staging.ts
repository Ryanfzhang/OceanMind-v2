import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {lstat, mkdir, mkdtemp, open, readdir, readFile, rename, rm} from 'node:fs/promises';
import {dirname, join, resolve} from 'node:path';

import type {UpdatePackage, VerifiedUpdate} from './update-manifest.js';
import {parseStrictJsonBytes} from './strict-json.js';

export const UPDATE_STAGE_SCHEMA = 'ocean-desktop-update-stage/v1';
const journalName = 'transaction.json';
const archiveName = 'package';
const partialName = 'package.part';
const maxJournalBytes = 32 * 1024;
const maxVersionLength = 128;
const maxBackendSchemaLength = 128;
const maxPackageUrlLength = 2_048;

export type StagedUpdate = {
  schemaVersion: typeof UPDATE_STAGE_SCHEMA;
  stagedAt: string;
  update: VerifiedUpdate;
  directory: string;
  archivePath: string;
};

type StagingJournal = {
  schema_version: typeof UPDATE_STAGE_SCHEMA;
  staged_at: string;
  update: {
    version: string;
    protocol_version: number;
    backend_schema: string;
    package: {
      platform: UpdatePackage['platform'];
      architecture: UpdatePackage['architecture'];
      url: string;
      sha256: string;
      size_bytes: number;
    };
  };
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function string(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

function positiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

function boundedString(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum;
}

function validSemanticVersion(value: unknown): value is string {
  return boundedString(value, maxVersionLength)
    && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.test(value);
}

function validBackendSchema(value: unknown): value is string {
  return boundedString(value, maxBackendSchemaLength) && /^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(value);
}

function validStagedAt(value: unknown): value is string {
  if (!boundedString(value, 64) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)) return false;
  const parsed = new Date(value);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString() === value;
}

function validPackageUrl(value: unknown): value is string {
  if (!boundedString(value, maxPackageUrlLength)) return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password && !parsed.hash;
  } catch {
    return false;
  }
}

function validUpdatePackage(value: unknown): value is UpdatePackage {
  if (!isRecord(value) || !hasExactKeys(value, ['architecture', 'platform', 'sha256', 'sizeBytes', 'url'])) return false;
  return (value.platform === 'darwin' || value.platform === 'win32')
    && (value.architecture === 'arm64' || value.architecture === 'x64')
    && validPackageUrl(value.url)
    && typeof value.sha256 === 'string'
    && /^[a-f0-9]{64}$/.test(value.sha256)
    && positiveInteger(value.sizeBytes);
}

function validVerifiedUpdate(value: unknown): value is VerifiedUpdate {
  if (!isRecord(value) || !hasExactKeys(value, ['backendSchema', 'package', 'protocolVersion', 'version'])) return false;
  return validSemanticVersion(value.version)
    && positiveInteger(value.protocolVersion)
    && validBackendSchema(value.backendSchema)
    && validUpdatePackage(value.package);
}

function journalFor(update: VerifiedUpdate, stagedAt: string): StagingJournal {
  return {
    schema_version: UPDATE_STAGE_SCHEMA,
    staged_at: stagedAt,
    update: {
      version: update.version,
      protocol_version: update.protocolVersion,
      backend_schema: update.backendSchema,
      package: {
        platform: update.package.platform,
        architecture: update.package.architecture,
        url: update.package.url,
        sha256: update.package.sha256,
        size_bytes: update.package.sizeBytes,
      },
    },
  };
}

function parseJournal(value: unknown): Omit<StagedUpdate, 'directory' | 'archivePath'> | null {
  if (!isRecord(value) || !hasExactKeys(value, ['schema_version', 'staged_at', 'update'])) return null;
  const stagedAt = value.staged_at;
  if (value.schema_version !== UPDATE_STAGE_SCHEMA || !validStagedAt(stagedAt)) return null;
  if (!isRecord(value.update) || !hasExactKeys(value.update, ['backend_schema', 'package', 'protocol_version', 'version'])) return null;
  const {version, backend_schema: backendSchema, protocol_version: protocolVersion} = value.update;
  if (!validSemanticVersion(version) || !validBackendSchema(backendSchema) || !positiveInteger(protocolVersion)) return null;
  const item = value.update.package;
  if (!isRecord(item) || !hasExactKeys(item, ['architecture', 'platform', 'sha256', 'size_bytes', 'url'])) return null;
  const {platform, architecture, url, sha256, size_bytes: sizeBytes} = item;
  if (
    (platform !== 'darwin' && platform !== 'win32')
    || (architecture !== 'arm64' && architecture !== 'x64')
    || !validPackageUrl(url)
    || !string(sha256)
    || !/^[a-f0-9]{64}$/.test(sha256)
    || !positiveInteger(sizeBytes)
  ) return null;
  const parsed: Omit<StagedUpdate, 'directory' | 'archivePath'> = {
    schemaVersion: UPDATE_STAGE_SCHEMA,
    stagedAt,
    update: {
      version,
      protocolVersion,
      backendSchema,
      package: {
        platform,
        architecture,
        url,
        sha256,
        sizeBytes,
      },
    },
  };
  return validVerifiedUpdate(parsed.update) ? parsed : null;
}

async function writeJsonAtomically(path: string, value: StagingJournal): Promise<void> {
  const temporary = `${path}.tmp`;
  const handle = await open(temporary, 'wx', 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(value)}\n`, 'utf8');
    await handle.sync();
  } finally {
    await handle.close();
  }
  await rename(temporary, path);
  await syncParentDirectory(path);
}

async function syncParentDirectory(path: string): Promise<void> {
  // Directory fsync makes the preceding rename durable on the POSIX filesystems
  // used by macOS. Windows does not expose an equivalent directory handle through
  // this Node path, so its rename remains the platform atomicity primitive.
  if (process.platform === 'win32') return;
  const directory = await open(dirname(path), 'r');
  try {
    await directory.sync();
  } finally {
    await directory.close();
  }
}

async function verifyArchive(path: string, update: VerifiedUpdate): Promise<boolean> {
  let details: Awaited<ReturnType<typeof lstat>>;
  try {
    details = await lstat(path);
  } catch {
    return false;
  }
  if (!details.isFile() || details.isSymbolicLink() || details.size !== update.package.sizeBytes) return false;
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path)) digest.update(chunk);
  const hash = digest.digest('hex');
  return hash === update.package.sha256;
}

async function readJournal(path: string): Promise<Omit<StagedUpdate, 'directory' | 'archivePath'> | null> {
  let details: Awaited<ReturnType<typeof lstat>>;
  try {
    details = await lstat(path);
  } catch {
    return null;
  }
  if (!details.isFile() || details.isSymbolicLink() || details.size > maxJournalBytes) return null;
  try {
    return parseJournal(parseStrictJsonBytes(await readFile(path)));
  } catch {
    return null;
  }
}

async function hasOnlyStagingFiles(directory: string): Promise<boolean> {
  try {
    const entries = await readdir(directory, {withFileTypes: true});
    return entries.length === 2 && entries.every((entry) => (
      (entry.name === archiveName || entry.name === journalName)
      && entry.isFile()
      && !entry.isSymbolicLink()
    ));
  } catch {
    return false;
  }
}

export class UpdateStagingStore {
  constructor(private readonly root: string) {}

  async stage(update: VerifiedUpdate, chunks: AsyncIterable<Uint8Array>): Promise<StagedUpdate> {
    if (!validVerifiedUpdate(update)) {
      throw new Error('Update staging requires an exact verified update record.');
    }
    await mkdir(this.root, {recursive: true, mode: 0o700});
    const directory = await mkdtemp(join(this.root, 'update-stage-'));
    const partialPath = join(directory, partialName);
    const archivePath = join(directory, archiveName);
    try {
      const handle = await open(partialPath, 'wx', 0o600);
      const digest = createHash('sha256');
      let bytesWritten = 0;
      try {
        for await (const chunk of chunks) {
          if (!(chunk instanceof Uint8Array)) throw new Error('Update download yielded a non-byte chunk.');
          bytesWritten += chunk.byteLength;
          if (bytesWritten > update.package.sizeBytes) throw new Error('Update download exceeds its signed byte count.');
          digest.update(chunk);
          await handle.write(chunk);
        }
        await handle.sync();
      } finally {
        await handle.close();
      }
      if (bytesWritten !== update.package.sizeBytes || digest.digest('hex') !== update.package.sha256) {
        throw new Error('Update download failed its signed size or SHA-256 verification.');
      }
      await rename(partialPath, archivePath);
      await syncParentDirectory(archivePath);
      const stagedAt = new Date().toISOString();
      const journal = journalFor(update, stagedAt);
      await writeJsonAtomically(join(directory, journalName), journal);
      return {schemaVersion: UPDATE_STAGE_SCHEMA, stagedAt, update, directory, archivePath};
    } catch (error) {
      await rm(directory, {recursive: true, force: true});
      throw error;
    }
  }

  async recover(): Promise<{staged: StagedUpdate[]; discardedDirectories: string[]}> {
    try {
      await mkdir(this.root, {recursive: true, mode: 0o700});
    } catch {
      return {staged: [], discardedDirectories: []};
    }
    const staged: StagedUpdate[] = [];
    const discardedDirectories: string[] = [];
    for (const entry of await readdir(this.root, {withFileTypes: true})) {
      if (!entry.name.startsWith('update-stage-')) continue;
      const directory = resolve(this.root, entry.name);
      if (!entry.isDirectory() || !directory.startsWith(`${resolve(this.root)}${process.platform === 'win32' ? '\\' : '/'}`)) {
        await rm(directory, {recursive: true, force: true});
        discardedDirectories.push(directory);
        continue;
      }
      const parsed = await readJournal(join(directory, journalName));
      const archivePath = join(directory, archiveName);
      if (!parsed || !(await hasOnlyStagingFiles(directory)) || !(await verifyArchive(archivePath, parsed.update))) {
        await rm(directory, {recursive: true, force: true});
        discardedDirectories.push(directory);
        continue;
      }
      staged.push({...parsed, directory, archivePath});
    }
    staged.sort((left, right) => right.stagedAt.localeCompare(left.stagedAt));
    return {staged, discardedDirectories};
  }
}
