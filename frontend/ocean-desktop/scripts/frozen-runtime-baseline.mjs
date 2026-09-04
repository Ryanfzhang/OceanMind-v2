import {createHash} from 'node:crypto';

export const frozenScientificRuntimeFilename = 'ocean-scientific-runtime.json';
export const frozenScientificRuntimeSchema = 'ocean-frozen-scientific-runtime/v1';

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value, keys) {
  return isRecord(value)
    && Object.keys(value).length === keys.length
    && keys.every((key) => Object.hasOwn(value, key));
}

function sortedUniqueStrings(value) {
  return Array.isArray(value)
    && value.every((item) => typeof item === 'string' && item.length > 0)
    && value.every((item, index) => index === 0 || value[index - 1] < item);
}

function validDistribution(value) {
  if (!isRecord(value) || typeof value.name !== 'string' || !value.name || typeof value.version !== 'string' || !value.version) return false;
  const keys = Object.keys(value).sort();
  if (keys.join(',') !== 'name,version' && keys.join(',') !== 'direct_url,editable,name,version') return false;
  return value.direct_url === undefined
    || (typeof value.direct_url === 'string' && value.direct_url.length > 0 && typeof value.editable === 'boolean');
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(',')}]`;
  if (isRecord(value)) {
    return `{${Object.keys(value).sort().map((key) => `${jsonAscii(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return jsonAscii(value);
}

function jsonAscii(value) {
  const serialized = JSON.stringify(value);
  if (serialized === undefined) throw new Error('Frozen scientific runtime manifest contains an unsupported value.');
  return serialized.replace(/[\u0080-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

export function frozenRuntimeAttestation(payload) {
  if (!hasExactKeys(payload, ['schema_version', 'interpreter', 'platform', 'required_modules', 'distributions', 'native_runtime'])) {
    throw new Error('Frozen scientific runtime manifest has incompatible fields.');
  }
  if (payload.schema_version !== frozenScientificRuntimeSchema) {
    throw new Error('Frozen scientific runtime manifest schema is incompatible.');
  }
  if (!hasExactKeys(payload.interpreter, ['implementation', 'version'])
    || typeof payload.interpreter.implementation !== 'string'
    || !payload.interpreter.implementation
    || !Array.isArray(payload.interpreter.version)
    || payload.interpreter.version.length !== 3
    || payload.interpreter.version.some((value) => !Number.isInteger(value) || value < 0)) {
    throw new Error('Frozen scientific runtime interpreter record is invalid.');
  }
  if (!hasExactKeys(payload.platform, ['system', 'machine'])
    || typeof payload.platform.system !== 'string'
    || !payload.platform.system
    || typeof payload.platform.machine !== 'string'
    || !payload.platform.machine) {
    throw new Error('Frozen scientific runtime platform record is invalid.');
  }
  if (!sortedUniqueStrings(payload.required_modules)) {
    throw new Error('Frozen scientific runtime module list is invalid.');
  }
  if (!Array.isArray(payload.distributions) || !payload.distributions.every(validDistribution)) {
    throw new Error('Frozen scientific runtime distributions are invalid.');
  }
  if (!payload.distributions.every((item, index) => index === 0
    || `${payload.distributions[index - 1].name}\u0000${payload.distributions[index - 1].version}`
      <= `${item.name}\u0000${item.version}`)) {
    throw new Error('Frozen scientific runtime distributions are not ordered.');
  }
  if (!isRecord(payload.native_runtime)
    || Object.entries(payload.native_runtime).some(([key, value]) => !key || typeof value !== 'string')) {
    throw new Error('Frozen scientific native runtime record is invalid.');
  }
  return {
    schema_version: frozenScientificRuntimeSchema,
    fingerprint_sha256: createHash('sha256').update(canonicalJson(payload), 'utf8').digest('hex'),
    dependency_count: payload.distributions.length,
  };
}
