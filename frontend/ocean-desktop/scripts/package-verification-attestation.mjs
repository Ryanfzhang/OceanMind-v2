import {closeSync, existsSync, fsyncSync, mkdirSync, openSync, renameSync, unlinkSync, writeFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';

export function packageVerificationAttestation({platform, architecture, backendSchema, sandbox, sandboxSelfCheck, scientificRuntime}) {
  return {
    schema_version: 'ocean-desktop-package-verification/v1',
    platform,
    architecture,
    backend_schema: backendSchema,
    sandbox,
    sandbox_self_check: sandboxSelfCheck,
    scientific_runtime: scientificRuntime,
  };
}

export function writeEvidenceOnce(path, report, cwd = process.cwd()) {
  const output = resolve(cwd, path);
  if (existsSync(output)) throw new Error(`Package verification attestation already exists: ${output}`);
  mkdirSync(dirname(output), {recursive: true});
  const temporary = `${output}.${process.pid}.tmp`;
  const descriptor = openSync(temporary, 'wx', 0o600);
  try {
    writeFileSync(descriptor, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
  try {
    renameSync(temporary, output);
  } catch (error) {
    try { unlinkSync(temporary); } catch {}
    throw error;
  }
  return output;
}

export function writePackageVerificationAttestation(path, report, cwd = process.cwd()) {
  return writeEvidenceOnce(path, report, cwd);
}
