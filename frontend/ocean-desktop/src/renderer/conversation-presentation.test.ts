import {describe, expect, it} from 'vitest';

import {presentTranscript} from './conversation-presentation.js';
import type {TranscriptItem} from './types.js';

const item = (
  itemId: string,
  role: TranscriptItem['role'],
  text: string,
  requestId = 'req_1',
): TranscriptItem => ({item_id: itemId, role, text, request_id: requestId});

describe('presentTranscript', () => {
  it('keeps only the last assistant turn visible after a request completes', () => {
    const [group] = presentTranscript([
      item('user', 'user', 'Make a map'),
      item('step_1', 'assistant', 'Inspecting the dataset'),
      item('step_2', 'assistant', 'Running the analysis'),
      item('answer', 'assistant', 'The map is ready'),
    ], null);

    expect(group?.userItems.map((entry) => entry.text)).toEqual(['Make a map']);
    expect(group?.processItems.map((entry) => entry.text)).toEqual([
      'Inspecting the dataset',
      'Running the analysis',
    ]);
    expect(group?.finalItem?.text).toBe('The map is ready');
  });

  it('treats every response item as Thinking while the request is active', () => {
    const [group] = presentTranscript([
      item('user', 'user', 'Make a map'),
      item('step', 'assistant', 'Inspecting the dataset'),
    ], 'req_1');

    expect(group?.active).toBe(true);
    expect(group?.finalItem).toBeNull();
    expect(group?.processItems.map((entry) => entry.text)).toEqual(['Inspecting the dataset']);
  });

  it('surfaces a terminal system error and folds prior assistant progress away', () => {
    const [group] = presentTranscript([
      item('user', 'user', 'Make a map'),
      item('step', 'assistant', 'Running the analysis'),
      item('error', 'system', 'The request timed out'),
    ], null);

    expect(group?.processItems.map((entry) => entry.text)).toEqual(['Running the analysis']);
    expect(group?.finalItem).toMatchObject({role: 'system', text: 'The request timed out'});
  });

  it('never presents server-owned source routing context as user text', () => {
    const [group] = presentTranscript([
      item('user', 'user', 'What data do I have?\n\n[Task-owned immutable sources available to this turn. hidden]\n- source_1: dataset_a@v1'),
    ], 'req_1');

    expect(group?.userItems.map((entry) => entry.text)).toEqual(['What data do I have?']);
  });

  it('omits internal revision bookkeeping from the conversation', () => {
    const [group] = presentTranscript([
      item('user', 'user', 'Inspect the source'),
      item('revision', 'system', 'Revision refreshed before execution; no external side effect was replayed.'),
      item('answer', 'assistant', 'Inspection complete'),
    ], null);

    expect(group?.processItems).toEqual([]);
    expect(group?.finalItem?.text).toBe('Inspection complete');
  });
});
