import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';

const supportedArchitectures = new Set(['x64', 'arm64']);

export function assertSupportedArchitecture(architecture) {
  if (!supportedArchitectures.has(architecture)) {
    throw new Error(`Ocean Desktop release packaging supports x64 and arm64 only; received ${architecture}.`);
  }
  return architecture;
}

export function parseMachOArchitectures(description) {
  const normalized = String(description).toLowerCase();
  const architectures = new Set();
  if (/x86_64|x86-64/.test(normalized)) architectures.add('x64');
  if (/arm64|aarch64/.test(normalized)) architectures.add('arm64');
  if (!architectures.size) {
    throw new Error(`Could not identify a supported macOS sidecar architecture from: ${description.trim() || 'empty file output'}.`);
  }
  return architectures;
}

export function parseWindowsPeArchitecture(binary) {
  if (!Buffer.isBuffer(binary) || binary.length < 0x40 || binary.readUInt16LE(0) !== 0x5a4d) {
    throw new Error('Windows sidecar is not a valid PE executable.');
  }
  const peOffset = binary.readUInt32LE(0x3c);
  if (peOffset + 6 > binary.length || binary.toString('ascii', peOffset, peOffset + 4) !== 'PE\0\0') {
    throw new Error('Windows sidecar is missing its PE header.');
  }
  const machine = binary.readUInt16LE(peOffset + 4);
  if (machine === 0x8664) return 'x64';
  if (machine === 0xaa64) return 'arm64';
  throw new Error(`Windows sidecar has an unsupported PE machine type: 0x${machine.toString(16)}.`);
}

export function sidecarArchitectures(platform, executable, dependencies = {}) {
  if (platform === 'darwin') {
    const file = dependencies.file ?? ((path) => execFileSync('file', ['--brief', path], {encoding: 'utf8'}));
    return parseMachOArchitectures(file(executable));
  }
  if (platform === 'win32') {
    const readFile = dependencies.readFile ?? readFileSync;
    return new Set([parseWindowsPeArchitecture(readFile(executable))]);
  }
  throw new Error(`Ocean Desktop release packaging is supported only on macOS or Windows; received ${platform}.`);
}

export function assertReleaseTarget({platform, nodeArchitecture, sidecarArchitectures: architectures}) {
  const architecture = assertSupportedArchitecture(nodeArchitecture);
  if (!(architectures instanceof Set) || !architectures.has(architecture)) {
    const actual = architectures instanceof Set ? [...architectures].sort().join(', ') : 'unknown';
    throw new Error(
      `Frozen sidecar architecture (${actual}) does not match the Node/Electron release target (${architecture}). `
      + 'Rebuild the sidecar with a native Python of the release target architecture.',
    );
  }
  return {platform, architecture};
}
