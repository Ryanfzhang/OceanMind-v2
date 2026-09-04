import {createHash, verify, type KeyObject} from 'node:crypto';

export const UPDATE_MANIFEST_SCHEMA = 'ocean-desktop-update/v1';

export type UpdatePlatform = 'darwin' | 'win32';
export type UpdateArchitecture = 'arm64' | 'x64';
export type TrustedUpdateKeys = Readonly<Record<string, KeyObject | string>>;

export type UpdateRuntime = {
  version: string;
  protocolVersion: number;
  backendSchema: string;
  platform: UpdatePlatform;
  architecture: UpdateArchitecture;
};

export type UpdatePackage = {
  platform: UpdatePlatform;
  architecture: UpdateArchitecture;
  url: string;
  sha256: string;
  sizeBytes: number;
};

export type VerifiedUpdate = {
  version: string;
  protocolVersion: number;
  backendSchema: string;
  package: UpdatePackage;
};

type JsonRecord = Record<string, unknown>;
type ParsedEnvelope = {
  keyId: string;
  signature: Buffer;
  release: {
    version: string;
    protocolVersion: number;
    backendSchema: string;
    packages: UpdatePackage[];
  };
  signedPayload: JsonRecord;
};

export class UpdateManifestError extends Error {
  constructor(
    readonly code:
      | 'malformed_manifest'
      | 'unknown_signing_key'
      | 'invalid_signature'
      | 'version_not_newer'
      | 'protocol_mismatch'
      | 'backend_schema_mismatch'
      | 'unsupported_target'
      | 'package_integrity',
    message: string,
  ) {
    super(message);
    this.name = 'UpdateManifestError';
  }
}

function fail(code: UpdateManifestError['code'], message: string): never {
  throw new UpdateManifestError(code, message);
}

function record(value: unknown, path: string): JsonRecord {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    fail('malformed_manifest', `${path} must be an object.`);
  }
  return value as JsonRecord;
}

function exactKeys(value: JsonRecord, keys: readonly string[], path: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    fail('malformed_manifest', `${path} has unsupported or missing fields.`);
  }
}

function string(value: unknown, path: string): string {
  if (typeof value !== 'string' || !value) fail('malformed_manifest', `${path} must be a non-empty string.`);
  return value;
}

function integer(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    fail('malformed_manifest', `${path} must be a safe integer.`);
  }
  return value;
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) fail('malformed_manifest', 'Signed payload contains a non-finite number.');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(',')}]`;
  const item = record(value, 'signed payload');
  return `{${Object.keys(item).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(item[key])}`).join(',')}}`;
}

function strictSemver(value: string, path: string): {major: number; minor: number; patch: number; prerelease: string[] | null} {
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.exec(value);
  if (!match) fail('malformed_manifest', `${path} must be a semantic version.`);
  const [major, minor, patch] = [match[1], match[2], match[3]].map((part) => Number(part));
  if ([major, minor, patch].some((part) => !Number.isSafeInteger(part))) {
    fail('malformed_manifest', `${path} is outside the supported semantic-version range.`);
  }
  return {major, minor, patch, prerelease: match[4] ? match[4].split('.') : null};
}

function compareSemver(leftValue: string, rightValue: string): number {
  const left = strictSemver(leftValue, 'version');
  const right = strictSemver(rightValue, 'version');
  for (const part of ['major', 'minor', 'patch'] as const) {
    if (left[part] !== right[part]) return left[part] > right[part] ? 1 : -1;
  }
  if (!left.prerelease && !right.prerelease) return 0;
  if (!left.prerelease) return 1;
  if (!right.prerelease) return -1;
  const length = Math.max(left.prerelease.length, right.prerelease.length);
  for (let index = 0; index < length; index += 1) {
    const leftPart = left.prerelease[index];
    const rightPart = right.prerelease[index];
    if (leftPart === undefined) return -1;
    if (rightPart === undefined) return 1;
    if (leftPart === rightPart) continue;
    const leftNumeric = /^\d+$/.test(leftPart);
    const rightNumeric = /^\d+$/.test(rightPart);
    if (leftNumeric && rightNumeric) return Number(leftPart) > Number(rightPart) ? 1 : -1;
    if (leftNumeric) return -1;
    if (rightNumeric) return 1;
    return leftPart > rightPart ? 1 : -1;
  }
  return 0;
}

function parsePackage(value: unknown, index: number): UpdatePackage {
  const item = record(value, `release.packages[${index}]`);
  exactKeys(item, ['architecture', 'platform', 'sha256', 'size_bytes', 'url'], `release.packages[${index}]`);
  const platform = string(item.platform, `release.packages[${index}].platform`);
  const architecture = string(item.architecture, `release.packages[${index}].architecture`);
  if (platform !== 'darwin' && platform !== 'win32') fail('malformed_manifest', `release.packages[${index}].platform is unsupported.`);
  if (architecture !== 'arm64' && architecture !== 'x64') fail('malformed_manifest', `release.packages[${index}].architecture is unsupported.`);
  const url = string(item.url, `release.packages[${index}].url`);
  let parsedUrl: URL;
  try {
    parsedUrl = new URL(url);
  } catch {
    fail('malformed_manifest', `release.packages[${index}].url is invalid.`);
  }
  if (parsedUrl.protocol !== 'https:' || parsedUrl.username || parsedUrl.password || parsedUrl.hash) {
    fail('malformed_manifest', `release.packages[${index}].url must be an HTTPS URL without credentials or a fragment.`);
  }
  const sha256 = string(item.sha256, `release.packages[${index}].sha256`);
  if (!/^[a-f0-9]{64}$/.test(sha256)) fail('malformed_manifest', `release.packages[${index}].sha256 must be lower-case SHA-256.`);
  const sizeBytes = integer(item.size_bytes, `release.packages[${index}].size_bytes`);
  if (sizeBytes <= 0) fail('malformed_manifest', `release.packages[${index}].size_bytes must be positive.`);
  return {platform, architecture, url, sha256, sizeBytes};
}

function parseEnvelope(value: unknown): ParsedEnvelope {
  const envelope = record(value, 'update manifest');
  exactKeys(envelope, ['key_id', 'release', 'schema_version', 'signature'], 'update manifest');
  if (envelope.schema_version !== UPDATE_MANIFEST_SCHEMA) fail('malformed_manifest', 'Update manifest schema version is unsupported.');
  const keyId = string(envelope.key_id, 'update manifest.key_id');
  const signatureValue = string(envelope.signature, 'update manifest.signature');
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(signatureValue)) fail('malformed_manifest', 'Update manifest.signature is not base64.');
  const signature = Buffer.from(signatureValue, 'base64');
  if (!signature.length) fail('malformed_manifest', 'Update manifest.signature is empty.');
  const release = record(envelope.release, 'release');
  exactKeys(release, ['backend_schema', 'packages', 'protocol_version', 'version'], 'release');
  const version = string(release.version, 'release.version');
  strictSemver(version, 'release.version');
  const protocolVersion = integer(release.protocol_version, 'release.protocol_version');
  if (protocolVersion < 1) fail('malformed_manifest', 'release.protocol_version must be positive.');
  const backendSchema = string(release.backend_schema, 'release.backend_schema');
  if (!Array.isArray(release.packages) || !release.packages.length) fail('malformed_manifest', 'release.packages must be a non-empty array.');
  const packages = release.packages.map((item, index) => parsePackage(item, index));
  const signedPayload = {
    schema_version: envelope.schema_version,
    key_id: keyId,
    release: {
      version,
      protocol_version: protocolVersion,
      backend_schema: backendSchema,
      packages: packages.map((item) => ({
        platform: item.platform,
        architecture: item.architecture,
        url: item.url,
        sha256: item.sha256,
        size_bytes: item.sizeBytes,
      })),
    },
  };
  return {keyId, signature, release: {version, protocolVersion, backendSchema, packages}, signedPayload};
}

export function selectSignedUpdate(manifest: unknown, runtime: UpdateRuntime, trustedKeys: TrustedUpdateKeys): VerifiedUpdate {
  strictSemver(runtime.version, 'installed version');
  if (!Number.isSafeInteger(runtime.protocolVersion) || runtime.protocolVersion < 1 || !runtime.backendSchema) {
    fail('malformed_manifest', 'Installed update runtime identity is invalid.');
  }
  const envelope = parseEnvelope(manifest);
  const trustedKey = trustedKeys[envelope.keyId];
  if (!trustedKey) fail('unknown_signing_key', 'Update manifest was signed by an unknown key.');
  let signatureValid = false;
  try {
    signatureValid = verify(null, Buffer.from(canonicalJson(envelope.signedPayload), 'utf8'), trustedKey, envelope.signature);
  } catch {
    signatureValid = false;
  }
  if (!signatureValid) fail('invalid_signature', 'Update manifest signature verification failed.');
  if (compareSemver(envelope.release.version, runtime.version) <= 0) {
    fail('version_not_newer', 'Update manifest does not advance the installed version.');
  }
  if (envelope.release.protocolVersion !== runtime.protocolVersion) {
    fail('protocol_mismatch', 'Update manifest requires a different Protocol version.');
  }
  if (envelope.release.backendSchema !== runtime.backendSchema) {
    fail('backend_schema_mismatch', 'Update manifest requires a different backend schema.');
  }
  const packages = envelope.release.packages.filter((item) => item.platform === runtime.platform && item.architecture === runtime.architecture);
  if (packages.length !== 1) fail('unsupported_target', 'Update manifest does not contain one exact package for this platform and architecture.');
  return {
    version: envelope.release.version,
    protocolVersion: envelope.release.protocolVersion,
    backendSchema: envelope.release.backendSchema,
    package: packages[0]!,
  };
}

export function assertDownloadedPackageIntegrity(updatePackage: UpdatePackage, bytes: Uint8Array): void {
  if (bytes.byteLength !== updatePackage.sizeBytes) {
    fail('package_integrity', 'Downloaded update package has an unexpected size.');
  }
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  if (sha256 !== updatePackage.sha256) {
    fail('package_integrity', 'Downloaded update package SHA-256 verification failed.');
  }
}

export function canonicalUpdatePayloadForSigning(payload: {
  keyId: string;
  version: string;
  protocolVersion: number;
  backendSchema: string;
  packages: UpdatePackage[];
}): Buffer {
  return Buffer.from(canonicalJson({
    schema_version: UPDATE_MANIFEST_SCHEMA,
    key_id: payload.keyId,
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
  }), 'utf8');
}
