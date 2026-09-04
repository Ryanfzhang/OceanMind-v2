import {artifactPathSegments} from './contained-path.js';

export type ArtifactResourceGrant = {
  token: string;
  resourceUri: string;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
};

/** Resource grants remain valid while the renderer refreshes the same task. */
export function shouldResetTaskResourceGrants(
  activeTaskId: string | null,
  nextTaskId: string,
): boolean {
  return activeTaskId !== nextTaskId;
}

const MAX_RESOURCE_BYTES = 25 * 1024 * 1024;
const SAFE_MIME_TYPES = new Set([
  'application/json',
  'application/x-netcdf',
  'application/octet-stream',
  'application/pdf',
  'application/x-ipynb+json',
  'image/jpeg',
  'image/png',
  'image/svg+xml',
  'image/webp',
  'text/csv',
  'text/markdown',
  'text/plain',
  'text/tab-separated-values',
  'text/x-python',
]);

function isCanonicalResourceUri(value: string): boolean {
  try {
    const uri = new URL(value);
    if (uri.search || uri.hash || uri.username || uri.password) return false;
    if (uri.protocol === 'file:') return uri.hostname === '' && Boolean(uri.pathname);
    return uri.protocol === 'ocean:'
      && uri.hostname === 'artifacts'
      && artifactPathSegments(uri.pathname) !== null;
  } catch {
    return false;
  }
}

export function parseArtifactResourceGrant(value: Record<string, unknown>): ArtifactResourceGrant | null {
  const token = typeof value.resource_token === 'string' ? value.resource_token : null;
  const resourceUri = typeof value.resource_uri === 'string' ? value.resource_uri : null;
  const mimeType = typeof value.mime_type === 'string' && SAFE_MIME_TYPES.has(value.mime_type)
    ? value.mime_type
    : null;
  const sizeBytes = typeof value.size_bytes === 'number' && Number.isSafeInteger(value.size_bytes)
    ? value.size_bytes
    : null;
  const sha256 = typeof value.sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.sha256)
    ? value.sha256
    : null;
  if (!token || !/^res_[A-Za-z0-9]+$/.test(token) || !resourceUri || !isCanonicalResourceUri(resourceUri) || !mimeType || sizeBytes === null || sizeBytes < 0 || sizeBytes > MAX_RESOURCE_BYTES || !sha256) {
    return null;
  }
  return {token, resourceUri, mimeType, sizeBytes, sha256};
}
