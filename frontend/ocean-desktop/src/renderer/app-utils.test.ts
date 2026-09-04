import {describe, expect, it} from 'vitest';

import {isActiveForegroundRequest, isTaskCreationReady, shouldApplyTaskSnapshot} from './app-utils.js';

describe('foreground request completion', () => {
  it('refreshes the task only for the active research request', () => {
    expect(isActiveForegroundRequest('session_submit_1', 'session_submit_1')).toBe(true);
    expect(isActiveForegroundRequest('task_result_resource_grant_1', 'session_submit_1')).toBe(false);
    expect(isActiveForegroundRequest('task_open_1', null)).toBe(false);
  });

  it('keeps task navigation on the last task selected by the user', () => {
    expect(shouldApplyTaskSnapshot('task_8', 'task_8')).toBe(true);
    expect(shouldApplyTaskSnapshot('task_8', 'task_7')).toBe(false);
    expect(shouldApplyTaskSnapshot(null, 'task_8')).toBe(true);
  });

  it('waits for workspace.open before creating a task', () => {
    expect(isTaskCreationReady('/research/gulf', '/research/gulf', true, false)).toBe(false);
    expect(isTaskCreationReady('/research/gulf', '/research/gulf', false, true)).toBe(false);
    expect(isTaskCreationReady('/research/gulf', '/research/pacific', true, true)).toBe(false);
    expect(isTaskCreationReady('/research/gulf', '/research/gulf', true, true)).toBe(true);
  });
});
