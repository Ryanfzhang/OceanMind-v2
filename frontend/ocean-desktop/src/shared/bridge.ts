export type DesktopBackendLaunch = {
  workspacePath: string;
};

export type WorkspaceSourceKind = 'dataset' | 'paper';
export type WorkspaceSourceSelection = {relativePath?: string; localPath?: string};

/**
 * A renderer-to-host setup request. The API key is deliberately absent from
 * every backend Protocol v2 envelope and is never returned to the renderer.
 */
export type ModelRoleProviderSetup = {
  provider: 'openai' | 'anthropic';
  model: string;
  baseUrl?: string;
  apiKey?: string;
};

export type ModelProviderSetup = {
  coordinator: ModelRoleProviderSetup;
  expert: ModelRoleProviderSetup;
  skillCurator: ModelRoleProviderSetup;
};

export type ModelRoleProviderStatus = {
  profile: string;
  label: string;
  provider: string;
  model: string;
  baseUrl?: string;
  configured: boolean;
};

export type ModelProviderStatus = {
  coordinator: ModelRoleProviderStatus;
  expert: ModelRoleProviderStatus;
  skillCurator: ModelRoleProviderStatus;
  configured: boolean;
};

export type DesktopBackendStatus = {
  running: boolean;
  workspacePath: string | null;
  detail?: string;
  readyEvent?: Record<string, unknown> | null;
  activeTaskId?: string | null;
};

export type DesktopUpdateStatus = {
  configured: boolean;
  state: 'unavailable' | 'idle' | 'checking' | 'prepared' | 'installing' | 'failed';
  version?: string;
  lastInstall?: {
    state: 'applied' | 'previous_runtime_resumed' | 'unexpected_runtime' | 'discarded';
    version?: string;
  };
};

export type DesktopProjectEntry = {
  path: string;
  name: string;
  lastOpenedAt: number;
};

export type OpenArtifactResourceResult = {
  opened: boolean;
  revealed: boolean;
  message?: string;
};

export type BackendFrame =
  | {kind: 'event'; payload: Record<string, unknown>}
  | {kind: 'diagnostic'; message: string; source?: 'backend_log' | 'runtime'}
  | {kind: 'exit'; code: number | null};

export type OceanDesktopBridge = {
  chooseWorkspace(): Promise<string | null>;
  listProjects(): Promise<DesktopProjectEntry[]>;
  forgetProject(projectPath: string): Promise<DesktopProjectEntry[]>;
  chooseWorkspaceSource(kind: WorkspaceSourceKind): Promise<WorkspaceSourceSelection | null>;
  startBackend(options: DesktopBackendLaunch): Promise<DesktopBackendStatus>;
  stopBackend(): Promise<void>;
  getBackendStatus(): Promise<DesktopBackendStatus>;
  getModelProviderStatus(): Promise<ModelProviderStatus>;
  configureModelProvider(setup: ModelProviderSetup): Promise<ModelProviderStatus>;
  sendBackendFrame(frame: Record<string, unknown>): Promise<void>;
  openArtifactResource(url: string): Promise<OpenArtifactResourceResult>;
  revealPortableExport(exportId: string): Promise<void>;
  getUpdateStatus(): Promise<DesktopUpdateStatus>;
  checkForUpdate(): Promise<DesktopUpdateStatus>;
  installPreparedUpdate(): Promise<DesktopUpdateStatus>;
  onBackendFrame(listener: (frame: BackendFrame) => void): () => void;
};
