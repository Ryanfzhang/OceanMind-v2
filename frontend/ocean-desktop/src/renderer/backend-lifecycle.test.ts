import {describe, expect, it} from 'vitest';

import {shouldPublishBackendExit} from '../shared/backend-lifecycle.js';

describe('backend lifecycle notifications', () => {
  it('hides the normal exit from an intentional project switch', () => {
    expect(shouldPublishBackendExit(true, false)).toBe(false);
    expect(shouldPublishBackendExit(false, false)).toBe(false);
  });

  it('reports an unexpected exit from the active backend', () => {
    expect(shouldPublishBackendExit(false, true)).toBe(true);
  });
});
