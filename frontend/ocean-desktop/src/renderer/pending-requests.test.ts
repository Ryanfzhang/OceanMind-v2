import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

import {PendingRequestHandlers} from './pending-requests.js';

describe('PendingRequestHandlers', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('resolves a handler taken before the timeout and cancels its expiry timer', () => {
    const expired: string[] = [];
    const handlers = new PendingRequestHandlers(1_000, (id) => expired.push(id));
    const seen: unknown[] = [];
    handlers.set('req_a', (result) => seen.push(result));
    const handler = handlers.get('req_a');
    handlers.delete('req_a');
    handler?.({ok: true});
    vi.advanceTimersByTime(5_000);
    expect(seen).toEqual([{ok: true}]);
    expect(expired).toEqual([]);
    expect(handlers.size).toBe(0);
  });

  it('expires a handler after the TTL so the caller can reset busy state', () => {
    const expired: string[] = [];
    const handlers = new PendingRequestHandlers(1_000, (id) => expired.push(id));
    const seen: unknown[] = [];
    handlers.set('req_b', (result) => seen.push(result));
    vi.advanceTimersByTime(1_000);
    expect(expired).toEqual(['req_b']);
    expect(handlers.get('req_b')).toBeUndefined();
    expect(handlers.size).toBe(0);
    expect(seen).toEqual([]);
  });

  it('restarts the TTL when the same request id is registered again', () => {
    const expired: string[] = [];
    const handlers = new PendingRequestHandlers(1_000, (id) => expired.push(id));
    handlers.set('req_c', () => {});
    vi.advanceTimersByTime(900);
    handlers.set('req_c', () => {});
    vi.advanceTimersByTime(900);
    expect(expired).toEqual([]);
    vi.advanceTimersByTime(100);
    expect(expired).toEqual(['req_c']);
  });

  it('expireAll fails every pending handler exactly once (sequence-gap resync)', () => {
    const expired: string[] = [];
    const handlers = new PendingRequestHandlers(60_000, (id) => expired.push(id));
    handlers.set('req_1', () => {});
    handlers.set('req_2', () => {});
    handlers.set('req_3', () => {});
    handlers.expireAll();
    expect(expired).toEqual(['req_1', 'req_2', 'req_3']);
    expect(handlers.size).toBe(0);
    vi.advanceTimersByTime(120_000);
    expect(expired).toEqual(['req_1', 'req_2', 'req_3']);
  });

  it('clear drops pending handlers without reporting expiry (backend exit)', () => {
    const expired: string[] = [];
    const handlers = new PendingRequestHandlers(1_000, (id) => expired.push(id));
    handlers.set('req_d', () => {});
    handlers.clear();
    vi.advanceTimersByTime(5_000);
    expect(expired).toEqual([]);
    expect(handlers.size).toBe(0);
  });
});
