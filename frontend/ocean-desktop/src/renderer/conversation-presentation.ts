import type {TranscriptItem} from './types.js';

const HIDDEN_SOURCE_CONTEXT_MARKER = '[Task-owned immutable sources available to this turn.';
const INTERNAL_SYSTEM_PREFIXES = [
  'Revision refreshed before execution; no external side effect was replayed.',
] as const;

function visibleItem(item: TranscriptItem): TranscriptItem | null {
  if (item.role === 'system' && INTERNAL_SYSTEM_PREFIXES.some((prefix) => item.text.startsWith(prefix))) {
    return null;
  }
  if (item.role !== 'user') return item;
  const marker = item.text.indexOf(HIDDEN_SOURCE_CONTEXT_MARKER);
  if (marker < 0) return item;
  return {...item, text: item.text.slice(0, marker).trimEnd()};
}

export type PresentedTranscriptGroup = {
  key: string;
  requestId: string | null;
  userItems: TranscriptItem[];
  processItems: TranscriptItem[];
  finalItem: TranscriptItem | null;
  active: boolean;
};

/**
 * Present one model request as one conversation exchange.
 *
 * OceanMind persists every assistant tool-use turn for recovery and audit.
 * Those turns are useful process evidence, but they are not separate answers.
 * The final assistant/system item remains visible; earlier items become the
 * request's collapsible Thinking history.
 */
export function presentTranscript(
  transcript: TranscriptItem[],
  activeRequestId: string | null,
): PresentedTranscriptGroup[] {
  const grouped: Array<{key: string; requestId: string | null; items: TranscriptItem[]}> = [];
  const byRequest = new Map<string, number>();

  transcript.forEach((rawItem, index) => {
    // Old task transcripts may contain routing context written by an earlier
    // backend.  Sanitize at presentation time as well as fixing new writes so
    // reopening an existing task cannot expose internal model instructions.
    const item = visibleItem(rawItem);
    if (item === null) return;
    const requestId = item.request_id ?? null;
    if (requestId === null) {
      grouped.push({key: `item:${item.item_id}:${index}`, requestId: null, items: [item]});
      return;
    }
    const existing = byRequest.get(requestId);
    if (existing !== undefined) {
      grouped[existing]?.items.push(item);
      return;
    }
    byRequest.set(requestId, grouped.length);
    grouped.push({key: `request:${requestId}`, requestId, items: [item]});
  });

  return grouped.map((group) => {
    const active = group.requestId !== null && group.requestId === activeRequestId;
    const userItems = group.items.filter((item) => item.role === 'user');
    const responseItems = group.items.filter((item) => item.role !== 'user');
    let finalIndex = -1;
    if (!active) {
      for (let index = responseItems.length - 1; index >= 0; index -= 1) {
        const role = responseItems[index]?.role;
        if (role === 'assistant' || role === 'system') {
          finalIndex = index;
          break;
        }
      }
    }
    return {
      key: group.key,
      requestId: group.requestId,
      userItems,
      processItems: responseItems.filter((_item, index) => index !== finalIndex),
      finalItem: finalIndex >= 0 ? responseItems[finalIndex] ?? null : null,
      active,
    };
  });
}
