import {describe, expect, it} from 'vitest';

import {filterCommandActions} from '../../../packages/ocean-ui/src/index.js';

const actions = [
  {id: 'new-task', label: 'New research task'},
  {id: 'open-project', label: 'Open project'},
  {id: 'create-report', label: 'Create report from task outputs'},
];

describe('command palette filtering', () => {
  it('returns every action for a blank query', () => {
    expect(filterCommandActions(actions, '')).toEqual(actions);
    expect(filterCommandActions(actions, '   ')).toEqual(actions);
  });

  it('matches labels by case-insensitive substring', () => {
    expect(filterCommandActions(actions, 'TASK').map((action) => action.id)).toEqual(['new-task', 'create-report']);
    expect(filterCommandActions(actions, 'report').map((action) => action.id)).toEqual(['create-report']);
  });

  it('returns nothing when no label matches', () => {
    expect(filterCommandActions(actions, 'zzz')).toEqual([]);
  });
});
