import {describe, expect, it} from 'vitest';

import {editableModelId, isConcreteModelId, isModelProviderReady} from './model-provider.js';

describe('model provider readiness', () => {
  const status = (model: string, configured: boolean) => {
    const role = {profile: 'role-api', label: 'Role API', provider: 'openai', model, configured};
    return {coordinator: role, expert: role, skillCurator: role, configured};
  };
  it('does not treat the inherited default model as callable', () => {
    expect(isConcreteModelId('default')).toBe(false);
    expect(editableModelId(' default ')).toBe('');
    expect(isModelProviderReady(status('default', true))).toBe(false);
  });

  it('requires both an available credential and a concrete model', () => {
    expect(isConcreteModelId('deepseek-chat')).toBe(true);
    expect(isModelProviderReady(status('deepseek-chat', true))).toBe(true);
    expect(isModelProviderReady(status('deepseek-chat', false))).toBe(false);
  });
});
