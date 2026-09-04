import {describe, expect, it} from 'vitest';

import {
  optimisticUserTranscriptItem,
  mergeSnapshotTranscript,
  reconcileTranscriptItem,
  removeOptimisticTranscriptItem,
  restoreSubmittedPrompt,
  submissionTransportFailureItem,
  revisionFromRequestFailure,
} from './request-recovery.js';

describe('request failure recovery', () => {
  it('restores a submitted prompt after failure without overwriting new input', () => {
    expect(restoreSubmittedPrompt('', 'continue the analysis', false)).toBe('continue the analysis');
    expect(restoreSubmittedPrompt('new draft', 'old request', false)).toBe('new draft');
    expect(restoreSubmittedPrompt('', 'cancelled request', true)).toBe('');
  });

  it('extracts only a valid authoritative workspace conflict revision', () => {
    expect(revisionFromRequestFailure({
      code: 'workspace_revision_conflict',
      details: {current_workspace_revision: 7},
    })).toBe(7);
    expect(revisionFromRequestFailure({
      code: 'model_error',
      details: {current_workspace_revision: 8},
    })).toBeNull();
    expect(revisionFromRequestFailure({
      code: 'workspace_revision_conflict',
      details: {current_workspace_revision: -1},
    })).toBeNull();
  });

  it('shows a submitted query immediately and replaces it with the durable item', () => {
    const optimistic = optimisticUserTranscriptItem('req_1', 'Inspect this dataset');
    expect(optimistic).toMatchObject({
      item_id: 'optimistic:req_1',
      request_id: 'req_1',
      role: 'user',
      text: 'Inspect this dataset',
    });

    const authoritative = {
      item_id: 'item_user_1',
      request_id: 'req_1',
      role: 'user' as const,
      text: 'Inspect this dataset',
    };
    expect(reconcileTranscriptItem([optimistic], authoritative)).toEqual([authoritative]);
    expect(reconcileTranscriptItem([authoritative], authoritative)).toEqual([authoritative]);
  });

  it('removes only an unsent optimistic query', () => {
    const optimistic = optimisticUserTranscriptItem('req_1', 'Inspect this dataset');
    const durable = {item_id: 'old', role: 'assistant' as const, text: 'Previous answer'};
    expect(removeOptimisticTranscriptItem([durable, optimistic], 'req_1')).toEqual([durable]);
  });

  it('keeps a transport failure in the same visible exchange', () => {
    expect(submissionTransportFailureItem('req_1', 'Request was not accepted')).toMatchObject({
      item_id: 'transport-failure:req_1',
      request_id: 'req_1',
      role: 'system',
      text: 'Request was not accepted',
    });
  });

  it('preserves an optimistic query across a racing task snapshot', () => {
    const optimistic = optimisticUserTranscriptItem('req_1', 'Inspect this dataset');
    const submitted = new Map([['req_1', {text: optimistic.text, taskId: 'task_1'}]]);
    expect(mergeSnapshotTranscript([optimistic], [], submitted, 'task_1')).toEqual([optimistic]);
    expect(mergeSnapshotTranscript([optimistic], [{
      item_id: 'user_1', role: 'user', text: optimistic.text, request_id: 'req_1',
    }], submitted, 'task_1')).toEqual([{
      item_id: 'user_1', role: 'user', text: optimistic.text, request_id: 'req_1',
    }]);
    expect(mergeSnapshotTranscript([optimistic], [], submitted, 'task_2')).toEqual([]);
  });
});
