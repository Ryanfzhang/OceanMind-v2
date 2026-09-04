import {describe, expect, it} from 'vitest';

import {forgetProject, mergeRememberedProjects, parseProjectCatalog, rememberProject, upsertTaskPreservingOrder} from './project-catalog.js';
import type {ResearchTask} from './types.js';

const task = (id: string, status: ResearchTask['status'] = 'active'): ResearchTask => ({
  task_id: id,
  workspace_id: 'ws_desktop',
  title: `Task ${id}`,
  status,
  task_revision: 1,
  conversation_generation: 0,
});

describe('project catalog', () => {
  it('updates an opened task without moving it to the top', () => {
    const tasks = [task('newest'), task('middle'), task('oldest')];
    const updated = {...tasks[1]!, title: 'Updated middle task'};

    expect(upsertTaskPreservingOrder(tasks, updated).map((entry) => entry.task_id)).toEqual([
      'newest',
      'middle',
      'oldest',
    ]);
    expect(upsertTaskPreservingOrder(tasks, updated)[1]?.title).toBe('Updated middle task');
  });

  it('keeps recent projects with their visible tasks', () => {
    const catalog = rememberProject([], '/research/gulf', [task('one'), task('old', 'archived')], 10);
    const next = rememberProject(catalog, '/research/atlantic', [task('two')], 20);

    expect(next.map((entry) => entry.name)).toEqual(['atlantic', 'gulf']);
    expect(next[1]?.tasks.map((entry) => entry.task_id)).toEqual(['one']);
  });

  it('updates a selected project without moving its sidebar position', () => {
    const catalog = [
      {path: '/research/atlantic', name: 'atlantic', tasks: [task('two')], lastOpenedAt: 20},
      {path: '/research/gulf', name: 'gulf', tasks: [task('one')], lastOpenedAt: 10},
    ];

    const next = rememberProject(catalog, '/research/gulf', [task('one')], 30);

    expect(next.map((entry) => entry.name)).toEqual(['atlantic', 'gulf']);
    expect(next[1]?.lastOpenedAt).toBe(30);
  });

  it('forgets one project without changing the remaining entries', () => {
    const catalog = rememberProject(
      rememberProject([], '/research/gulf', [task('one')], 10),
      '/research/atlantic',
      [task('two')],
      20,
    );

    expect(forgetProject(catalog, '/research/atlantic')).toEqual([catalog[1]]);
  });

  it('rejects malformed persisted catalog entries', () => {
    const parsed = parseProjectCatalog(JSON.stringify([
      {path: '/research/gulf', tasks: [task('one')], lastOpenedAt: 2},
      {path: '', tasks: []},
      {name: 'missing path'},
    ]));

    expect(parsed).toHaveLength(1);
    expect(parsed[0]?.name).toBe('gulf');
  });

  it('preserves persisted project order instead of sorting by last selection', () => {
    const parsed = parseProjectCatalog(JSON.stringify([
      {path: '/research/atlantic', name: 'atlantic', tasks: [], lastOpenedAt: 1},
      {path: '/research/gulf', name: 'gulf', tasks: [], lastOpenedAt: 99},
    ]));

    expect(parsed.map((entry) => entry.name)).toEqual(['atlantic', 'gulf']);
  });

  it('merges every host-remembered project without discarding cached tasks', () => {
    const catalog = rememberProject([], '/research/gulf', [task('one')], 10);
    const merged = mergeRememberedProjects(catalog, [
      {path: '/research/gulf', name: 'gulf', lastOpenedAt: 12},
      {path: '/research/pacific', name: 'pacific', lastOpenedAt: 20},
    ]);

    expect(merged.map((entry) => entry.name)).toEqual(['gulf', 'pacific']);
    expect(merged[0]?.tasks.map((entry) => entry.task_id)).toEqual(['one']);
  });
});
