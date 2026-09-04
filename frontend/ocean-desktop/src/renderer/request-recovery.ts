import type {TranscriptItem} from './types.js';

export function restoreSubmittedPrompt(
  currentPrompt: string,
  submittedPrompt: string | null | undefined,
  cancelled: boolean,
): string {
  if (cancelled || currentPrompt.trim() || !submittedPrompt) return currentPrompt;
  return submittedPrompt;
}

/**
 * Keep the user's request visible while the backend is accepting and persisting it.
 * The authoritative transcript item replaces this optimistic item when it arrives.
 */
export function optimisticUserTranscriptItem(requestId: string, text: string): TranscriptItem {
  return {
    item_id: `optimistic:${requestId}`,
    role: 'user',
    text,
    request_id: requestId,
    created_at: new Date().toISOString(),
  };
}

/** A renderer-owned transport failure is shown in the exchange instead of a toast. */
export function submissionTransportFailureItem(requestId: string, message: string): TranscriptItem {
  return {
    item_id: `transport-failure:${requestId}`,
    role: 'system',
    text: message,
    request_id: requestId,
    created_at: new Date().toISOString(),
  };
}

export function reconcileTranscriptItem(
  current: TranscriptItem[],
  incoming: TranscriptItem,
): TranscriptItem[] {
  if (current.some((item) => item.item_id === incoming.item_id)) return current;
  if (incoming.role === 'user' && incoming.request_id) {
    const optimisticIndex = current.findIndex((item) => (
      item.item_id === `optimistic:${incoming.request_id}`
      && item.role === 'user'
    ));
    if (optimisticIndex >= 0) {
      return current.map((item, index) => index === optimisticIndex ? incoming : item);
    }
  }
  return [...current, incoming];
}

export function removeOptimisticTranscriptItem(
  current: TranscriptItem[],
  requestId: string,
): TranscriptItem[] {
  return current.filter((item) => item.item_id !== `optimistic:${requestId}`);
}

/**
 * An authoritative snapshot may race the durable transcript append for a request
 * that the renderer has already shown optimistically. Preserve that one local
 * query until the snapshot contains its authoritative user item.
 */
export function mergeSnapshotTranscript(
  current: TranscriptItem[],
  incoming: TranscriptItem[],
  submitted: Map<string, {text: string; taskId: string}>,
  taskId: string,
): TranscriptItem[] {
  const durableUserRequests = new Set(
    incoming
      .filter((item) => item.role === 'user' && item.request_id)
      .map((item) => item.request_id as string),
  );
  const pending = current.filter((item) => {
    if (!item.item_id.startsWith('optimistic:') || !item.request_id) return false;
    const request = submitted.get(item.request_id);
    return request?.taskId === taskId && !durableUserRequests.has(item.request_id);
  });
  return pending.length ? [...incoming, ...pending] : incoming;
}

export function revisionFromRequestFailure(error: unknown): number | null {
  if (error === null || typeof error !== 'object' || Array.isArray(error)) return null;
  const record = error as Record<string, unknown>;
  if (record.code !== 'workspace_revision_conflict') return null;
  const details = record.details;
  if (details === null || typeof details !== 'object' || Array.isArray(details)) return null;
  const revision = (details as Record<string, unknown>).current_workspace_revision;
  return typeof revision === 'number' && Number.isSafeInteger(revision) && revision >= 0
    ? revision
    : null;
}
