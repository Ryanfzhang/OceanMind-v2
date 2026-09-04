import type {ModelProviderStatus} from '../shared/bridge.js';

/** A provider needs an explicit model identifier; the settings default is not callable. */
export function isConcreteModelId(value: string | null | undefined): boolean {
  const model = value?.trim();
  return Boolean(model && model.toLowerCase() !== 'default');
}

export function editableModelId(value: string | null | undefined): string {
  return isConcreteModelId(value) ? value!.trim() : '';
}

export function isModelProviderReady(status: ModelProviderStatus | null | undefined): boolean {
  return Boolean(
    status?.configured
    && isConcreteModelId(status.coordinator.model)
    && isConcreteModelId(status.expert.model)
    && isConcreteModelId(status.skillCurator.model),
  );
}
