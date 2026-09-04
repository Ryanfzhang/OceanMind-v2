export const PENDING_REQUEST_TIMEOUT_MS = 180_000;

export type PendingRequestHandler = (result: Record<string, unknown>) => void;

/**
 * Tracks in-flight backend request handlers with a TTL. A lost terminal event
 * (backend crash, sequence gap) must not wedge the UI: expiring a handler
 * reports the request id so the matching busy state can reset.
 */
export class PendingRequestHandlers {
  private readonly entries = new Map<string, {handler: PendingRequestHandler; timer: ReturnType<typeof setTimeout>}>();

  constructor(
    private readonly timeoutMs: number,
    private readonly onExpire: (id: string) => void,
  ) {}

  set(id: string, handler: PendingRequestHandler): this {
    this.delete(id);
    const timer = setTimeout(() => {
      if (this.entries.delete(id)) this.onExpire(id);
    }, this.timeoutMs);
    this.entries.set(id, {handler, timer});
    return this;
  }

  get(id: string): PendingRequestHandler | undefined {
    return this.entries.get(id)?.handler;
  }

  delete(id: string): boolean {
    const entry = this.entries.get(id);
    if (!entry) return false;
    clearTimeout(entry.timer);
    this.entries.delete(id);
    return true;
  }

  clear(): void {
    for (const entry of this.entries.values()) clearTimeout(entry.timer);
    this.entries.clear();
  }

  /** Expire every pending handler (sequence-gap resync) so busy state resets instead of hanging. */
  expireAll(): void {
    for (const id of [...this.entries.keys()]) {
      if (this.delete(id)) this.onExpire(id);
    }
  }

  get size(): number {
    return this.entries.size;
  }
}
