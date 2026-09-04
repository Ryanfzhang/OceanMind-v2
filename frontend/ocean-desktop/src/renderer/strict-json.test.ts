import {describe, expect, it} from 'vitest';

import {StrictJsonError, parseStrictJson, parseStrictJsonBytes} from '../shared/strict-json.js';

describe('strict JSON parser', () => {
  it('parses the JSON subset used by update metadata without prototype mutation', () => {
    const parsed = parseStrictJson('{"manifest":{"version":"0.1.1","ready":true},"items":[null,-2.5e+3],"__proto__":{"polluted":true}}') as Record<string, unknown>;
    expect(parsed.manifest).toEqual({version: '0.1.1', ready: true});
    expect(parsed.items).toEqual([null, -2500]);
    expect(Object.getPrototypeOf(parsed)).toBeNull();
    expect(({} as {polluted?: boolean}).polluted).toBeUndefined();
  });

  it('rejects duplicate keys at every object depth', () => {
    expect(() => parseStrictJson('{"version":"0.1.0","version":"0.1.1"}')).toThrow(StrictJsonError);
    expect(() => parseStrictJson('{"release":{"packages":[],"packages":[]}}')).toThrow('Duplicate object key');
  });

  it('rejects malformed syntax, unbounded numeric values, and excessive nesting', () => {
    expect(() => parseStrictJson('{"ready":true,}')).toThrow(StrictJsonError);
    expect(() => parseStrictJson('1e10000')).toThrow('finite');
    expect(() => parseStrictJson(`${'['.repeat(130)}0${']'.repeat(130)}`)).toThrow('nesting');
  });

  it('rejects malformed UTF-8 bytes before any JSON semantics are considered', () => {
    expect(() => parseStrictJsonBytes(Uint8Array.from([0xc3, 0x28]))).toThrow('UTF-8');
    expect(parseStrictJsonBytes(Buffer.from('{"ready":true}', 'utf8'))).toEqual({ready: true});
  });
});
