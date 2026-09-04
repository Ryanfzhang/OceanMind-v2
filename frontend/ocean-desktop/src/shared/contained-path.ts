import {isAbsolute} from 'node:path';

const windowsDeviceName = /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$/i;

function safeSegment(segment: string): boolean {
  return Boolean(segment)
    && segment !== '.'
    && segment !== '..'
    && !/[\0-\x1f]/.test(segment)
    && !segment.includes(':')
    && !segment.endsWith('.')
    && !segment.endsWith(' ')
    && !windowsDeviceName.test(segment);
}

/**
 * Keep path identifiers valid on both POSIX and Windows, even when tests run on
 * only one host. In particular, reject Windows ADS/device aliases before any fs
 * resolution can reinterpret them.
 */
export function isSafeContainedRelativePath(path: string): boolean {
  if (!path || isAbsolute(path)) return false;
  const segments = path.split(/[\\/]/);
  return segments.length > 0 && segments.every(safeSegment);
}

/** Converts a URL pathname into canonical artifact path segments, or rejects it. */
export function artifactPathSegments(pathname: string): string[] | null {
  if (!pathname.startsWith('/')) return null;
  const raw = pathname.slice(1).split('/');
  if (!raw.length || raw.some((segment) => !segment)) return null;
  let segments: string[];
  try {
    segments = raw.map((segment) => decodeURIComponent(segment));
  } catch {
    return null;
  }
  return segments.every((segment) => !/[\\/]/.test(segment) && safeSegment(segment)) ? segments : null;
}
