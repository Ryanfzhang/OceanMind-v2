import {describe, expect, it} from 'vitest';

import {
  assertReleaseTarget,
  parseMachOArchitectures,
  parseWindowsPeArchitecture,
} from '../../scripts/release-target.mjs';

function peBinary(machine: number) {
  const binary = Buffer.alloc(0x86);
  binary.writeUInt16LE(0x5a4d, 0);
  binary.writeUInt32LE(0x80, 0x3c);
  binary.write('PE\0\0', 0x80, 'ascii');
  binary.writeUInt16LE(machine, 0x84);
  return binary;
}

describe('desktop release target guard', () => {
  it('reads thin and universal Mach-O descriptions', () => {
    expect([...parseMachOArchitectures('Mach-O 64-bit executable x86_64')]).toEqual(['x64']);
    expect([...parseMachOArchitectures('Mach-O universal binary with 2 architectures: [x86_64] [arm64]')].sort()).toEqual(['arm64', 'x64']);
  });

  it('reads Windows PE target machines without trusting the filename', () => {
    expect(parseWindowsPeArchitecture(peBinary(0x8664))).toBe('x64');
    expect(parseWindowsPeArchitecture(peBinary(0xaa64))).toBe('arm64');
    expect(() => parseWindowsPeArchitecture(Buffer.from('not a PE'))).toThrow('valid PE');
  });

  it('rejects a release whose frozen sidecar cannot run with the target Electron', () => {
    expect(assertReleaseTarget({platform: 'darwin', nodeArchitecture: 'x64', sidecarArchitectures: new Set(['x64'])})).toEqual({platform: 'darwin', architecture: 'x64'});
    expect(() => assertReleaseTarget({platform: 'darwin', nodeArchitecture: 'arm64', sidecarArchitectures: new Set(['x64'])})).toThrow('does not match');
  });
});
