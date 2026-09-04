import {randomUUID} from 'node:crypto';
import {lstat, mkdir, open, readFile, rename, rm} from 'node:fs/promises';
import {dirname, join} from 'node:path';

import type {UpdatePackage, UpdateRuntime, VerifiedUpdate} from './update-manifest.js';
import {parseStrictJsonBytes} from './strict-json.js';

export const UPDATE_HANDOFF_SCHEMA = 'ocean-desktop-update-handoff/v1';

const journalName = 'handoff.json';
const maximumJournalBytes = 32 * 1024;
const semanticVersion = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

type UpdateHandoff = {
  requestedAt: string;
  previousRuntime: UpdateRuntime;
  target: VerifiedUpdate;
};

type HandoffJournal = {
  schema_version: typeof UPDATE_HANDOFF_SCHEMA;
  requested_at: string;
  previous_runtime: {
    version: string;
    protocol_version: number;
    backend_schema: string;
    platform: UpdateRuntime['platform'];
    architecture: UpdateRuntime['architecture'];
  };
  target: {
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

export type UpdateHandoffOutcome =
  | {state: 'none'}
  | {state: 'applied'; version: string}
  | {state: 'previous_runtime_resumed'; version: string}
  | {state: 'unexpected_runtime'; expectedVersion: string; observedVersion: string}
  | {state: 'discarded'};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  return actual.length === sortedExpected.length && actual.every((key, index) => key === sortedExpected[index]);
}

function validSemver(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 128 && semanticVersion.test(value);
}

function validBackendSchema(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 128 && /^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(value);
}

function validTimestamp(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)) return false;
  const parsed = new Date(value);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString() === value;
}

function validUrl(value: unknown): value is string {
  if (typeof value !== 'string' || !value || value.length > 2_048) return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password && !parsed.hash;
  } catch {
    return false;
  }
}

function validRuntime(value: unknown): value is UpdateRuntime {
  const candidate = record(value);
  if (!candidate || !exactKeys(candidate, ['architecture', 'backendSchema', 'platform', 'protocolVersion', 'version'])) return false;
  return validSemver(candidate.version)
    && typeof candidate.protocolVersion === 'number'
    && Number.isSafeInteger(candidate.protocolVersion)
    && candidate.protocolVersion > 0
    && validBackendSchema(candidate.backendSchema)
    && (candidate.platform === 'darwin' || candidate.platform === 'win32')
    && (candidate.architecture === 'arm64' || candidate.architecture === 'x64');
}

function validPackage(value: unknown): value is UpdatePackage {
  const candidate = record(value);
  if (!candidate || !exactKeys(candidate, ['architecture', 'platform', 'sha256', 'sizeBytes', 'url'])) return false;
  return (candidate.platform === 'darwin' || candidate.platform === 'win32')
    && (candidate.architecture === 'arm64' || candidate.architecture === 'x64')
    && validUrl(candidate.url)
    && typeof candidate.sha256 === 'string'
    && /^[a-f0-9]{64}$/.test(candidate.sha256)
    && typeof candidate.sizeBytes === 'number'
    && Number.isSafeInteger(candidate.sizeBytes)
    && candidate.sizeBytes > 0;
}

function validVerifiedUpdate(value: unknown): value is VerifiedUpdate {
  const candidate = record(value);
  if (!candidate || !exactKeys(candidate, ['backendSchema', 'package', 'protocolVersion', 'version'])) return false;
  return validSemver(candidate.version)
    && typeof candidate.protocolVersion === 'number'
    && Number.isSafeInteger(candidate.protocolVersion)
    && candidate.protocolVersion > 0
    && validBackendSchema(candidate.backendSchema)
    && validPackage(candidate.package);
}

function parseVersion(value: string): {major: number; minor: number; patch: number; prerelease: string[] | null} {
  const match = semanticVersion.exec(value);
  if (!match) throw new Error('Update handoff version is invalid.');
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    prerelease: match[4] ? match[4].split('.') : null,
  };
}

function compareVersions(left: string, right: string): number {
  const first = parseVersion(left);
  const second = parseVersion(right);
  for (const key of ['major', 'minor', 'patch'] as const) {
    if (first[key] !== second[key]) return first[key] < second[key] ? -1 : 1;
  }
  if (first.prerelease === null || second.prerelease === null) {
    if (first.prerelease === second.prerelease) return 0;
    return first.prerelease === null ? 1 : -1;
  }
  const maximum = Math.max(first.prerelease.length, second.prerelease.length);
  for (let index = 0; index < maximum; index += 1) {
    const firstPart = first.prerelease[index];
    const secondPart = second.prerelease[index];
    if (firstPart === secondPart) continue;
    if (firstPart === undefined) return -1;
    if (secondPart === undefined) return 1;
    const firstNumeric = /^\d+$/.test(firstPart);
    const secondNumeric = /^\d+$/.test(secondPart);
    if (firstNumeric && secondNumeric) return Number(firstPart) < Number(secondPart) ? -1 : 1;
    if (firstNumeric !== secondNumeric) return firstNumeric ? -1 : 1;
    return firstPart < secondPart ? -1 : 1;
  }
  return 0;
}

function sameRuntime(left: UpdateRuntime, right: UpdateRuntime): boolean {
  return left.version === right.version
    && left.protocolVersion === right.protocolVersion
    && left.backendSchema === right.backendSchema
    && left.platform === right.platform
    && left.architecture === right.architecture;
}

function targetRuntime(target: VerifiedUpdate): UpdateRuntime {
  return {
    version: target.version,
    protocolVersion: target.protocolVersion,
    backendSchema: target.backendSchema,
    platform: target.package.platform,
    architecture: target.package.architecture,
  };
}

function assertCompatible(previousRuntime: UpdateRuntime, target: VerifiedUpdate): void {
  if (!validRuntime(previousRuntime) || !validVerifiedUpdate(target)) {
    throw new Error('Update handoff requires exact runtime and verified update records.');
  }
  const expected = targetRuntime(target);
  if (
    previousRuntime.protocolVersion !== expected.protocolVersion
    || previousRuntime.backendSchema !== expected.backendSchema
    || previousRuntime.platform !== expected.platform
    || previousRuntime.architecture !== expected.architecture
    || compareVersions(previousRuntime.version, expected.version) >= 0
  ) {
    throw new Error('Update handoff target is incompatible with the installed runtime.');
  }
}

function journalFor(previousRuntime: UpdateRuntime, target: VerifiedUpdate, requestedAt: string): HandoffJournal {
  return {
    schema_version: UPDATE_HANDOFF_SCHEMA,
    requested_at: requestedAt,
    previous_runtime: {
      version: previousRuntime.version,
      protocol_version: previousRuntime.protocolVersion,
      backend_schema: previousRuntime.backendSchema,
      platform: previousRuntime.platform,
      architecture: previousRuntime.architecture,
    },
    target: {
      version: target.version,
      protocol_version: target.protocolVersion,
      backend_schema: target.backendSchema,
      package: {
        platform: target.package.platform,
        architecture: target.package.architecture,
        url: target.package.url,
        sha256: target.package.sha256,
        size_bytes: target.package.sizeBytes,
      },
    },
  };
}

function parseJournal(value: unknown): UpdateHandoff | null {
  const journal = record(value);
  if (!journal || !exactKeys(journal, ['previous_runtime', 'requested_at', 'schema_version', 'target'])) return null;
  if (journal.schema_version !== UPDATE_HANDOFF_SCHEMA || !validTimestamp(journal.requested_at)) return null;
  const previous = record(journal.previous_runtime);
  const target = record(journal.target);
  if (!previous || !target || !exactKeys(previous, ['architecture', 'backend_schema', 'platform', 'protocol_version', 'version']) || !exactKeys(target, ['backend_schema', 'package', 'protocol_version', 'version'])) return null;
  const packageRecord = record(target.package);
  if (!packageRecord || !exactKeys(packageRecord, ['architecture', 'platform', 'sha256', 'size_bytes', 'url'])) return null;
  const previousRuntime: UpdateRuntime = {
    version: previous.version as string,
    protocolVersion: previous.protocol_version as number,
    backendSchema: previous.backend_schema as string,
    platform: previous.platform as UpdateRuntime['platform'],
    architecture: previous.architecture as UpdateRuntime['architecture'],
  };
  const verifiedTarget: VerifiedUpdate = {
    version: target.version as string,
    protocolVersion: target.protocol_version as number,
    backendSchema: target.backend_schema as string,
    package: {
      platform: packageRecord.platform as UpdatePackage['platform'],
      architecture: packageRecord.architecture as UpdatePackage['architecture'],
      url: packageRecord.url as string,
      sha256: packageRecord.sha256 as string,
      sizeBytes: packageRecord.size_bytes as number,
    },
  };
  try {
    assertCompatible(previousRuntime, verifiedTarget);
  } catch {
    return null;
  }
  return {requestedAt: journal.requested_at, previousRuntime, target: verifiedTarget};
}

async function syncParent(path: string): Promise<void> {
  if (process.platform === 'win32') return;
  const directory = await open(dirname(path), 'r');
  try {
    await directory.sync();
  } finally {
    await directory.close();
  }
}

async function writeJournal(path: string, journal: HandoffJournal): Promise<void> {
  const temporary = `${path}.${randomUUID()}.tmp`;
  const handle = await open(temporary, 'wx', 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(journal)}\n`, 'utf8');
    await handle.sync();
  } finally {
    await handle.close();
  }
  await rename(temporary, path);
  await syncParent(path);
}

export class UpdateHandoffStore {
  constructor(private readonly root: string) {}

  async recordInstallIntent(previousRuntime: UpdateRuntime, target: VerifiedUpdate): Promise<void> {
    assertCompatible(previousRuntime, target);
    await mkdir(this.root, {recursive: true, mode: 0o700});
    await writeJournal(join(this.root, journalName), journalFor(previousRuntime, target, new Date().toISOString()));
  }

  async clear(): Promise<void> {
    await rm(join(this.root, journalName), {force: true});
  }

  async observeLaunch(currentRuntime: UpdateRuntime): Promise<UpdateHandoffOutcome> {
    if (!validRuntime(currentRuntime)) throw new Error('Update handoff launch runtime is invalid.');
    const path = join(this.root, journalName);
    let details: Awaited<ReturnType<typeof lstat>>;
    try {
      details = await lstat(path);
    } catch {
      return {state: 'none'};
    }
    if (!details.isFile() || details.isSymbolicLink() || details.size > maximumJournalBytes) {
      await this.clear();
      return {state: 'discarded'};
    }
    let handoff: UpdateHandoff | null = null;
    try {
      handoff = parseJournal(parseStrictJsonBytes(await readFile(path)));
    } catch {
      handoff = null;
    }
    if (!handoff) {
      await this.clear();
      return {state: 'discarded'};
    }
    await this.clear();
    if (sameRuntime(currentRuntime, targetRuntime(handoff.target))) {
      return {state: 'applied', version: handoff.target.version};
    }
    if (sameRuntime(currentRuntime, handoff.previousRuntime)) {
      return {state: 'previous_runtime_resumed', version: handoff.previousRuntime.version};
    }
    return {
      state: 'unexpected_runtime',
      expectedVersion: handoff.target.version,
      observedVersion: currentRuntime.version,
    };
  }
}
