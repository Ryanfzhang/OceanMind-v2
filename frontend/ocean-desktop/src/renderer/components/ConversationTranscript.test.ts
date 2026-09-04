import {expect, it} from 'vitest';

import type {TaskResultRecord} from '../types.js';
import {taskResultKeys} from './ConversationTranscript.js';

it('resolves durable task result references with or without a version', () => {
  const result = {
    result_ref: {task_id: 'task_1', result_id: 'result_2', version: 1},
    content: {output_path: 'outputs/temperature_section.nc'},
  } as unknown as TaskResultRecord;

  expect(taskResultKeys(result)).toEqual(expect.arrayContaining([
    'task_1/result_2@v1',
    'task_1/result_2@v0001',
    'task_1/result_2',
    'result_2@v1',
    'result_2',
    'outputs/temperature_section.nc',
  ]));
});
