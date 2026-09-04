export type PaperAcquisitionMode = 'ask_before_download' | 'auto_download_open_access' | 'search_only';

export type PaperAcquisitionCommand =
  | {kind: 'show'}
  | {kind: 'set'; mode: PaperAcquisitionMode}
  | {kind: 'invalid'};

export const DEFAULT_PAPER_ACQUISITION_MODE: PaperAcquisitionMode = 'ask_before_download';

const MODE_BY_COMMAND: Record<string, PaperAcquisitionMode> = {
  ask: 'ask_before_download',
  auto: 'auto_download_open_access',
  'search-only': 'search_only',
};

export function parsePaperAcquisitionCommand(value: string): PaperAcquisitionCommand | null {
  const parts = value.trim().toLowerCase().split(/\s+/);
  if (parts[0] !== '/papers') return null;
  if (parts.length === 1) return {kind: 'show'};
  if (parts.length !== 2 || !MODE_BY_COMMAND[parts[1]]) return {kind: 'invalid'};
  return {kind: 'set', mode: MODE_BY_COMMAND[parts[1]]};
}

export function paperAcquisitionStorageKey(projectPath: string | null, taskId: string | null): string | null {
  if (!projectPath || !taskId) return null;
  return `oceanmind.paper-acquisition.v1:${encodeURIComponent(projectPath)}:${taskId}`;
}

export function readPaperAcquisitionMode(storage: Pick<Storage, 'getItem'>, key: string | null): PaperAcquisitionMode {
  if (!key) return DEFAULT_PAPER_ACQUISITION_MODE;
  const value = storage.getItem(key);
  return value === 'auto_download_open_access' || value === 'search_only' || value === 'ask_before_download'
    ? value
    : DEFAULT_PAPER_ACQUISITION_MODE;
}

