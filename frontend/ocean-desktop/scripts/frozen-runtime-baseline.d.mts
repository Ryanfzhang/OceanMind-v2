export const frozenScientificRuntimeFilename: 'ocean-scientific-runtime.json';
export const frozenScientificRuntimeSchema: 'ocean-frozen-scientific-runtime/v1';

export function canonicalJson(value: unknown): string;

export function frozenRuntimeAttestation(payload: unknown): {
  schema_version: 'ocean-frozen-scientific-runtime/v1';
  fingerprint_sha256: string;
  dependency_count: number;
};
