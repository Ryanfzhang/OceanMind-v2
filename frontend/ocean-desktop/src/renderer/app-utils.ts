import type {ArtifactRef, DesktopRuntimeCapabilities, EventPayload} from './types.js';

export function asRecord(value: unknown): EventPayload {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as EventPayload : {};
}

export function projectNameFromPath(path: string | null | undefined): string {
  if (!path) return 'Open project';
  const normalized = path.replace(/[\\/]+$/, '');
  return normalized.split(/[\\/]/).at(-1) || path;
}

export function sourceTitleFromPath(path: string): string {
  const filename = path.split('/').at(-1) ?? path;
  return filename.replace(/\.[^.]+$/, '') || filename;
}

export function artifactFileName(file: {uri?: string}): string | null {
  const name = file.uri?.split('/').at(-1) ?? '';
  return /^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$/.test(name) ? name : null;
}

export function sameRef(left: ArtifactRef | null | undefined, right: ArtifactRef | null | undefined): boolean {
  return Boolean(left && right && left.artifact_id === right.artifact_id && left.version === right.version);
}

/** Only the foreground research request owns the task-level completion refresh. */
export function isActiveForegroundRequest(
  requestId: string | null | undefined,
  activeRequestId: string | null | undefined,
): boolean {
  return Boolean(requestId && activeRequestId && requestId === activeRequestId);
}

/** Ignore a late task.open snapshot after the user has selected another task. */
export function shouldApplyTaskSnapshot(
  selectedTaskId: string | null,
  snapshotTaskId: string,
): boolean {
  return selectedTaskId === null || selectedTaskId === snapshotTaskId;
}

/** Background result commits refresh data, never change the active conversation. */
export function changedResultsTaskId(
  selectedTaskId: string | null,
  event: {type?: string; task_id?: string | null},
): string | null {
  return event.type === 'task.results.changed' && event.task_id && event.task_id === selectedTaskId
    ? event.task_id : null;
}

/** A ready backend context is not enough: task writes require an opened workspace. */
export function isTaskCreationReady(
  targetProjectPath: string,
  activeProjectPath: string | null,
  hasBackendContext: boolean,
  hasOpenWorkspace: boolean,
): boolean {
  return Boolean(
    targetProjectPath
    && targetProjectPath === activeProjectPath
    && hasBackendContext
    && hasOpenWorkspace
  );
}

export function parseDesktopRuntimeCapabilities(value: unknown): DesktopRuntimeCapabilities | null {
  const payload = asRecord(value);
  if (payload.schema_version !== 'ocean-desktop-runtime-capabilities/v1'
    && payload.schema_version !== 'ocean-desktop-runtime-capabilities/v2') return null;
  const connections = Array.isArray(payload.connections) ? payload.connections.flatMap((raw) => {
    const item = asRecord(raw);
    return typeof item.id === 'string' && typeof item.label === 'string' && typeof item.available === 'boolean'
      ? [{id: item.id, label: item.label, available: item.available}]
      : [];
  }) : [];
  const skills = Array.isArray(payload.skills) ? payload.skills.flatMap((raw) => {
    const item = asRecord(raw);
    return typeof item.name === 'string' && typeof item.description === 'string' && typeof item.version === 'string'
      ? [{name: item.name, description: item.description, version: item.version}]
      : [];
  }) : [];
  return {connections, skills};
}

export function researchSkillLabel(name: string): string {
  return name.split('-').filter(Boolean).map((part) => part[0]?.toUpperCase() + part.slice(1)).join(' ');
}
