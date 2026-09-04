import {describe, expect, it} from 'vitest';

import {paperAcquisitionStorageKey, parsePaperAcquisitionCommand, readPaperAcquisitionMode} from './paper-acquisition-mode.js';

describe('paper acquisition slash commands', () => {
  it('parses the three supported modes without treating other prompts as commands', () => {
    expect(parsePaperAcquisitionCommand('/papers ask')).toEqual({kind: 'set', mode: 'ask_before_download'});
    expect(parsePaperAcquisitionCommand('/papers AUTO')).toEqual({kind: 'set', mode: 'auto_download_open_access'});
    expect(parsePaperAcquisitionCommand('/papers search-only')).toEqual({kind: 'set', mode: 'search_only'});
    expect(parsePaperAcquisitionCommand('/papers')).toEqual({kind: 'show'});
    expect(parsePaperAcquisitionCommand('/papers download-everything')).toEqual({kind: 'invalid'});
    expect(parsePaperAcquisitionCommand('compare these papers')).toBeNull();
  });

  it('scopes the preference to one project task and falls back safely', () => {
    const key = paperAcquisitionStorageKey('/project/a', 'task_1');
    expect(key).toContain('task_1');
    expect(readPaperAcquisitionMode({getItem: () => 'search_only'}, key)).toBe('search_only');
    expect(readPaperAcquisitionMode({getItem: () => 'invalid'}, key)).toBe('ask_before_download');
    expect(readPaperAcquisitionMode({getItem: () => null}, null)).toBe('ask_before_download');
  });
});
