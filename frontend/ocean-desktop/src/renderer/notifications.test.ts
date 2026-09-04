import {describe, expect, it} from 'vitest';

import {
  diagnosticLevelFor,
  dismissNotification,
  NOTIFICATION_DISMISS_MS,
  NOTIFICATION_STACK_LIMIT,
  pushNotification,
  type AppNotification,
} from '../../../packages/ocean-ui/src/index.js';

function note(id: number, message: string, level: AppNotification['level'] = 'info'): AppNotification {
  return {id, level, message};
}

describe('notification stack', () => {
  it('appends notifications in arrival order', () => {
    const stack = pushNotification(pushNotification([], note(1, 'first')), note(2, 'second'));
    expect(stack.map((item) => item.id)).toEqual([1, 2]);
  });

  it('drops the oldest notification beyond the stack limit', () => {
    let stack: AppNotification[] = [];
    for (let id = 1; id <= NOTIFICATION_STACK_LIMIT + 2; id += 1) {
      stack = pushNotification(stack, note(id, 'message ' + id));
    }
    expect(stack).toHaveLength(NOTIFICATION_STACK_LIMIT);
    expect(stack[0]!.id).toBe(3);
  });

  it('ignores an exact repeat of the newest notification', () => {
    const stack = pushNotification([note(1, 'same')], note(2, 'same'));
    expect(stack.map((item) => item.id)).toEqual([1]);
  });

  it('keeps a repeated message when the level changed', () => {
    const stack = pushNotification([note(1, 'same', 'info')], note(2, 'same', 'error'));
    expect(stack).toHaveLength(2);
  });

  it('dismisses a notification by id', () => {
    const stack = dismissNotification([note(1, 'a'), note(2, 'b')], 1);
    expect(stack.map((item) => item.id)).toEqual([2]);
  });

  it('auto-dismisses info and warning but never errors', () => {
    expect(NOTIFICATION_DISMISS_MS.info).toBeGreaterThan(0);
    expect(NOTIFICATION_DISMISS_MS.warning).toBeGreaterThan(NOTIFICATION_DISMISS_MS.info!);
    expect(NOTIFICATION_DISMISS_MS.error).toBeNull();
  });
});

describe('diagnostic level classification', () => {
  it('marks failure wording as errors', () => {
    expect(diagnosticLevelFor('Could not reach the Ocean backend.')).toBe('error');
    expect(diagnosticLevelFor('Spatial layer contract is invalid.')).toBe('error');
    expect(diagnosticLevelFor('Backend exited (1).')).toBe('error');
    expect(diagnosticLevelFor('Request did not complete.')).toBe('error');
  });

  it('marks recovery wording as warnings', () => {
    expect(diagnosticLevelFor('Recovered an active request from the local task journal.')).toBe('warning');
    expect(diagnosticLevelFor('A backend request did not finish in time; the interface was reset.')).toBe('warning');
  });

  it('marks progress notes as info', () => {
    expect(diagnosticLevelFor('Dataset snapshot imported.')).toBe('info');
    expect(diagnosticLevelFor('Report artifact created from pinned task evidence.')).toBe('info');
  });
});
