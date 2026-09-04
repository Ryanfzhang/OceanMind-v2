export type BackendSequenceDecision =
  | {kind: 'accept'; lastSequence: number}
  | {kind: 'duplicate'; lastSequence: number | null}
  | {kind: 'gap'; expectedSequence: number; lastSequence: number};

export function isBackendSequence(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 1;
}

/**
 * Sequence values belong to one sidecar transport connection. A new
 * `system.ready` event starts a new tracker rather than continuing this one.
 */
export function assessBackendSequence(
  previousSequence: number | null,
  sequence: unknown,
): BackendSequenceDecision {
  if (!isBackendSequence(sequence)) {
    return {kind: 'duplicate', lastSequence: previousSequence};
  }
  if (previousSequence === null || sequence === previousSequence + 1) {
    return {kind: 'accept', lastSequence: sequence};
  }
  if (sequence <= previousSequence) {
    return {kind: 'duplicate', lastSequence: previousSequence};
  }
  return {kind: 'gap', expectedSequence: previousSequence + 1, lastSequence: sequence};
}
