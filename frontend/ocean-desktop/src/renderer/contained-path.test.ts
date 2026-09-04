import {describe, expect, it} from 'vitest';

import {artifactPathSegments, isSafeContainedRelativePath} from '../shared/contained-path.js';

describe('contained desktop paths', () => {
  it('accepts ordinary nested project and artifact paths', () => {
    expect(isSafeContainedRelativePath('inputs/cruise_2010/station-01.nc')).toBe(true);
    expect(artifactPathSegments('/spatial_layer/sst_2010/v0001/part_001.png')).toEqual([
      'spatial_layer', 'sst_2010', 'v0001', 'part_001.png',
    ]);
  });

  it.each([
    '../outside.nc',
    '..\\outside.nc',
    'C:\\outside.nc',
    '\\\\server\\share\\outside.nc',
    'report.md:alternate-stream',
    'NUL',
    'aux.json',
    'records/station. ',
    'records/station.',
    'records/line\u0000break.nc',
  ])('rejects a cross-platform alias or escape: %s', (path) => {
    expect(isSafeContainedRelativePath(path)).toBe(false);
  });

  it.each([
    '/report/example/v0001/report.md:alternate-stream',
    '/report/example/v0001/NUL',
    '/report/example/v0001/report.md%3Aalternate-stream',
    '/report/example/v0001/part%2F001.png',
    '/report/example/v0001/%2e%2e',
    '/report//example/v0001/report.md',
    '/report/example/v0001/%E0%A4%A',
  ])('rejects unsafe or non-canonical artifact URL paths: %s', (path) => {
    expect(artifactPathSegments(path)).toBeNull();
  });
});
