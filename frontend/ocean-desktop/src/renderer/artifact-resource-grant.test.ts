import {describe, expect, it} from 'vitest';

import {parseArtifactResourceGrant, shouldResetTaskResourceGrants} from '../shared/artifact-resource-grant';

const validGrant = {
  resource_token: 'res_abc123',
  resource_uri: 'ocean://artifacts/report/example/v0001/report.md',
  mime_type: 'text/markdown',
  size_bytes: 256,
  sha256: 'a'.repeat(64),
};

describe('parseArtifactResourceGrant', () => {
  it('keeps grants across a refresh of the same task and clears them when task scope changes', () => {
    expect(shouldResetTaskResourceGrants('task_a', 'task_a')).toBe(false);
    expect(shouldResetTaskResourceGrants('task_a', 'task_b')).toBe(true);
    expect(shouldResetTaskResourceGrants(null, 'task_a')).toBe(true);
  });

  it('accepts an exact bounded document grant', () => {
    expect(parseArtifactResourceGrant(validGrant)).toEqual({
      token: 'res_abc123',
      resourceUri: 'ocean://artifacts/report/example/v0001/report.md',
      mimeType: 'text/markdown',
      sizeBytes: 256,
      sha256: 'a'.repeat(64),
    });
  });

  it('accepts a task-owned local result grant before the host containment check', () => {
    expect(parseArtifactResourceGrant({
      ...validGrant,
      resource_uri: 'file:///project/OceanMind%20Tasks/task/results/result/v0001/view.json',
      mime_type: 'application/json',
    })).toMatchObject({
      resourceUri: 'file:///project/OceanMind%20Tasks/task/results/result/v0001/view.json',
      mimeType: 'application/json',
    });
  });

  it.each([
    {...validGrant, resource_token: 'not-a-resource-token'},
    {...validGrant, sha256: 'not-a-digest'},
    {...validGrant, resource_uri: 'ocean://artifacts/report/example/v0001/report.md:alternate-stream'},
    {...validGrant, resource_uri: 'ocean://artifacts/report/example/v0001/report.md%3Aalternate-stream'},
    {...validGrant, mime_type: 'application/pdf', size_bytes: 25 * 1024 * 1024 + 1},
  ])('rejects a malformed or oversized viewer grant', (grant) => {
    expect(parseArtifactResourceGrant(grant)).toBeNull();
  });
});
