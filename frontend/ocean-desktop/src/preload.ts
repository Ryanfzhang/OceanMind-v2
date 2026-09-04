import {contextBridge, ipcRenderer} from 'electron';
import type {BackendFrame, DesktopBackendLaunch, ModelProviderSetup, OceanDesktopBridge, WorkspaceSourceKind} from './shared/bridge.js';

const bridge: OceanDesktopBridge = {
  chooseWorkspace: () => ipcRenderer.invoke('ocean:choose-workspace'),
  listProjects: () => ipcRenderer.invoke('ocean:list-projects'),
  forgetProject: (projectPath: string) => ipcRenderer.invoke('ocean:forget-project', projectPath),
  chooseWorkspaceSource: (kind: WorkspaceSourceKind) => ipcRenderer.invoke('ocean:choose-workspace-source', kind),
  startBackend: (options: DesktopBackendLaunch) => ipcRenderer.invoke('ocean:start-backend', options),
  stopBackend: () => ipcRenderer.invoke('ocean:stop-backend'),
  getBackendStatus: () => ipcRenderer.invoke('ocean:get-backend-status'),
  getModelProviderStatus: () => ipcRenderer.invoke('ocean:get-model-provider-status'),
  configureModelProvider: (setup: ModelProviderSetup) => ipcRenderer.invoke('ocean:configure-model-provider', setup),
  sendBackendFrame: (frame) => ipcRenderer.invoke('ocean:send-backend-frame', frame),
  openArtifactResource: (url) => ipcRenderer.invoke('ocean:open-artifact-resource', url),
  revealPortableExport: (exportId) => ipcRenderer.invoke('ocean:reveal-portable-export', exportId),
  getUpdateStatus: () => ipcRenderer.invoke('ocean:get-update-status'),
  checkForUpdate: () => ipcRenderer.invoke('ocean:check-for-update'),
  installPreparedUpdate: () => ipcRenderer.invoke('ocean:install-prepared-update'),
  onBackendFrame: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, frame: BackendFrame) => listener(frame);
    ipcRenderer.on('ocean:backend-frame', handler);
    return () => ipcRenderer.removeListener('ocean:backend-frame', handler);
  },
};

contextBridge.exposeInMainWorld('oceanDesktop', bridge);
