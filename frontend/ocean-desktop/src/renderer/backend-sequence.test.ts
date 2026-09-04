import {describe, expect, it} from 'vitest';

import {assessBackendSequence} from '../../../packages/ocean-client/src/backend-sequence.js';

describe('backend event sequence', () => {
  it('accepts the first event and contiguous events for one sidecar connection', () => {
    expect(assessBackendSequence(null, 1)).toEqual({kind: 'accept', lastSequence: 1});
    expect(assessBackendSequence(1, 2)).toEqual({kind: 'accept', lastSequence: 2});
  });

  it('ignores replayed, malformed, and unsequenced frames', () => {
    expect(assessBackendSequence(5, 5)).toEqual({kind: 'duplicate', lastSequence: 5});
    expect(assessBackendSequence(5, 4)).toEqual({kind: 'duplicate', lastSequence: 5});
    expect(assessBackendSequence(5, '6')).toEqual({kind: 'duplicate', lastSequence: 5});
    expect(assessBackendSequence(null, 0)).toEqual({kind: 'duplicate', lastSequence: null});
  });

  it('reports a gap without applying the later incremental event', () => {
    expect(assessBackendSequence(7, 10)).toEqual({
      kind: 'gap',
      expectedSequence: 8,
      lastSequence: 10,
    });
  });
});
