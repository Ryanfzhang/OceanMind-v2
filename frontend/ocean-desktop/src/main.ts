import {app, BrowserWindow, dialog, ipcMain, powerMonitor, protocol, session, shell, type IpcMainInvokeEvent, type Session} from 'electron';
import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {createInterface} from 'node:readline';
import {dirname, extname, isAbsolute, join, relative, resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath, pathToFileURL} from 'node:url';

import {parseArtifactResourceGrant, shouldResetTaskResourceGrants} from './shared/artifact-resource-grant.js';
import {shouldPublishBackendExit} from './shared/backend-lifecycle.js';
import {artifactPathSegments, isSafeContainedRelativePath} from './shared/contained-path.js';
import type {BackendFrame, DesktopBackendLaunch, DesktopBackendStatus, DesktopProjectEntry, DesktopUpdateStatus, ModelProviderSetup, ModelProviderStatus, ModelRoleProviderSetup, ModelRoleProviderStatus, OpenArtifactResourceResult} from './shared/bridge.js';
import {parseDesktopUpdateConfig} from './shared/update-config.js';
import {loadElectronPlatformUpdater} from './shared/electron-updater-adapter.js';
import {parseStrictJsonBytes} from './shared/strict-json.js';
import {developmentPython} from './shared/python-environment.mjs';
import {UpdateHandoffStore, type UpdateHandoffOutcome} from './shared/update-handoff.js';
import {DesktopUpdateService} from './shared/update-service.js';
import {UpdateStagingStore} from './shared/update-staging.js';

const moduleDirectory = dirname(fileURLToPath(import.meta.url));
const protocolPrefix = 'OHJSON:';
const maxFrameBytes = 1024 * 1024;
const resourceGrantLifetimeMs = 10 * 60 * 1_000;
const artifactResourceTypes = new Map([
  ['.json', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.webp', 'image/webp'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.md', 'text/markdown; charset=utf-8'],
  ['.py', 'text/x-python; charset=utf-8'],
  ['.ipynb', 'application/x-ipynb+json; charset=utf-8'],
  ['.txt', 'text/plain; charset=utf-8'],
]);
// Large scientific arrays never travel in Protocol V2 frames.  They are read
// through short-lived, immutable grants and remain bounded by the shared
// 25 MiB resource policy.
const maxArtifactResourceBytes = 25 * 1024 * 1024;
const maxUpdateManifestBytes = 256 * 1024;
const desktopProtocolVersion = 2;
const desktopBackendSchema = 'ocean-desktop-backend/v1';

function configureIsolatedTestProfile(): string | null {
  if (app.isPackaged || process.env.OCEAN_DESKTOP_TEST_ISOLATED_PROFILE !== '1') return null;
  const profile = mkdtempSync(join(tmpdir(), 'ocean-desktop-e2e-'));
  app.setPath('userData', profile);
  return profile;
}

const isolatedTestProfile = configureIsolatedTestProfile();

let desktopUpdateService: DesktopUpdateService | null = null;
let desktopUpdatesReady: Promise<void> = Promise.resolve();
let desktopUpdateStatus: DesktopUpdateStatus = {configured: false, state: 'unavailable'};
let updateHandoffStore: UpdateHandoffStore | null = null;
let latestUpdateHandoff: UpdateHandoffOutcome = {state: 'none'};

type GrantedArtifactResource = {
  path: string;
  resourceUri: string;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
  expiresAt: number;
  webContentsId: number;
};

function projectCatalogPath(): string {
  return join(app.getPath('userData'), 'oceanmind-projects.json');
}

function readDesktopProjects(): DesktopProjectEntry[] {
  try {
    const parsed = JSON.parse(readFileSync(projectCatalogPath(), 'utf8')) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((value) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
      const entry = value as Partial<DesktopProjectEntry>;
      if (typeof entry.path !== 'string' || !isAbsolute(entry.path)) return [];
      return [{
        path: resolve(entry.path),
        name: typeof entry.name === 'string' && entry.name.trim() ? entry.name : entry.path.split(/[\\/]/).filter(Boolean).at(-1) ?? entry.path,
        lastOpenedAt: typeof entry.lastOpenedAt === 'number' ? entry.lastOpenedAt : 0,
      }];
    });
  } catch {
    return [];
  }
}

function rememberDesktopProject(projectPath: string): DesktopProjectEntry[] {
  const canonical = resolve(projectPath);
  const projects = readDesktopProjects();
  const entry: DesktopProjectEntry = {
    path: canonical,
    name: canonical.split(/[\\/]/).filter(Boolean).at(-1) ?? canonical,
    lastOpenedAt: Date.now(),
  };
  const existing = projects.find((project) => project.path === canonical);
  const next = existing
    ? projects.map((project) => project.path === canonical ? entry : project)
    : [entry, ...projects];
  mkdirSync(dirname(projectCatalogPath()), {recursive: true});
  writeFileSync(projectCatalogPath(), `${JSON.stringify(next, null, 2)}\n`, 'utf8');
  return next;
}

function forgetDesktopProject(projectPath: string): DesktopProjectEntry[] {
  const canonical = resolve(projectPath);
  const next = readDesktopProjects().filter((project) => project.path !== canonical);
  mkdirSync(dirname(projectCatalogPath()), {recursive: true});
  writeFileSync(projectCatalogPath(), `${JSON.stringify(next, null, 2)}\n`, 'utf8');
  return next;
}

function sha256File(path: string): string | null {
  try {
    return createHash('sha256').update(readFileSync(path)).digest('hex');
  } catch {
    return null;
  }
}

function updateConfigPath(): string {
  return app.isPackaged
    ? join(process.resourcesPath, 'update-config.json')
    : join(moduleDirectory, '..', 'resources', 'update-config.json');
}

function updateRuntime() {
  if ((process.platform !== 'darwin' && process.platform !== 'win32') || (process.arch !== 'arm64' && process.arch !== 'x64')) {
    throw new Error(`Desktop updates are unsupported for ${process.platform}/${process.arch}.`);
  }
  return {
    version: app.getVersion(),
    protocolVersion: desktopProtocolVersion,
    backendSchema: desktopBackendSchema,
    platform: process.platform,
    architecture: process.arch,
  };
}

async function fetchSignedUpdateManifest(url: string): Promise<unknown> {
  const response = await fetch(url, {
    headers: {accept: 'application/json'},
    redirect: 'error',
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok || !response.body) {
    throw new Error(`Update manifest request failed with status ${response.status}.`);
  }
  const announcedLength = response.headers.get('content-length');
  if (announcedLength && (!/^\d+$/.test(announcedLength) || Number(announcedLength) > maxUpdateManifestBytes)) {
    throw new Error('Update manifest exceeds the maximum response size.');
  }
  const reader = response.body.getReader();
  const chunks: Buffer[] = [];
  let bytes = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      if (!value) continue;
      bytes += value.byteLength;
      if (bytes > maxUpdateManifestBytes) {
        throw new Error('Update manifest exceeds the maximum response size.');
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  return parseStrictJsonBytes(Buffer.concat(chunks, bytes));
}

async function configureDesktopUpdates(): Promise<void> {
  desktopUpdateService = null;
  desktopUpdateStatus = withUpdateHandoff({configured: false, state: 'unavailable'});
  let parsed: unknown;
  try {
    parsed = parseStrictJsonBytes(readFileSync(updateConfigPath()));
  } catch (error) {
    console.warn('OceanMind desktop update configuration could not be read:', error);
    return;
  }
  try {
    const config = parseDesktopUpdateConfig(parsed);
    if (!config.enabled) return;
    desktopUpdateService = new DesktopUpdateService({
      manifestUrl: config.manifestUrl,
      feedBaseUrl: config.feedBaseUrl,
      runtime: updateRuntime(),
      trustedKeys: config.trustedKeys,
      updater: await loadElectronPlatformUpdater(),
      fetchManifest: fetchSignedUpdateManifest,
    });
    desktopUpdateStatus = withUpdateHandoff({configured: true, state: 'idle'});
  } catch (error) {
    console.error('OceanMind desktop update configuration was rejected:', error);
  }
}

function withUpdateHandoff(status: DesktopUpdateStatus): DesktopUpdateStatus {
  if (latestUpdateHandoff.state === 'none') return status;
  const version = latestUpdateHandoff.state === 'unexpected_runtime'
    ? latestUpdateHandoff.observedVersion
    : latestUpdateHandoff.state === 'discarded'
      ? undefined
      : latestUpdateHandoff.version;
  return {
    ...status,
    lastInstall: {state: latestUpdateHandoff.state, ...(version ? {version} : {})},
  };
}

async function recoverUpdateHandoff(): Promise<void> {
  if (!updateHandoffStore) return;
  try {
    latestUpdateHandoff = await updateHandoffStore.observeLaunch(updateRuntime());
    if (latestUpdateHandoff.state === 'previous_runtime_resumed') {
      console.warn(`OceanMind update handoff returned to previous runtime ${latestUpdateHandoff.version}.`);
    } else if (latestUpdateHandoff.state === 'unexpected_runtime') {
      console.error(`OceanMind update handoff expected ${latestUpdateHandoff.expectedVersion} but launched ${latestUpdateHandoff.observedVersion}.`);
    } else if (latestUpdateHandoff.state === 'discarded') {
      console.warn('OceanMind discarded an invalid pending update handoff record.');
    }
  } catch (error) {
    console.error('OceanMind update handoff recovery did not complete:', error);
  }
}

async function checkForDesktopUpdate(): Promise<DesktopUpdateStatus> {
  if (!desktopUpdateService || desktopUpdateStatus.state === 'checking' || desktopUpdateStatus.state === 'installing') {
    return desktopUpdateStatus;
  }
  desktopUpdateStatus = withUpdateHandoff({configured: true, state: 'checking'});
  try {
    const prepared = await desktopUpdateService.checkAndPrepare();
    desktopUpdateStatus = withUpdateHandoff({configured: true, state: 'prepared', version: prepared.update.version});
  } catch (error) {
    console.error('OceanMind desktop update check failed:', error);
    desktopUpdateStatus = withUpdateHandoff({configured: true, state: 'failed'});
  }
  return desktopUpdateStatus;
}

async function installPreparedDesktopUpdate(): Promise<DesktopUpdateStatus> {
  if (!desktopUpdateService || desktopUpdateStatus.state !== 'prepared') return desktopUpdateStatus;
  desktopUpdateStatus = withUpdateHandoff({...desktopUpdateStatus, state: 'installing'});
  try {
    const target = desktopUpdateService.preparedUpdate();
    if (!target || !updateHandoffStore) throw new Error('Verified update handoff is unavailable.');
    await updateHandoffStore.recordInstallIntent(updateRuntime(), target);
    await desktopUpdateService.installPrepared();
  } catch (error) {
    console.error('OceanMind desktop update installation could not start:', error);
    await updateHandoffStore?.clear().catch(() => undefined);
    desktopUpdateStatus = withUpdateHandoff({configured: true, state: 'failed'});
  }
  return desktopUpdateStatus;
}

protocol.registerSchemesAsPrivileged([
  {scheme: 'ocean-artifact', privileges: {secure: true, standard: true, supportFetchAPI: true, corsEnabled: true}},
]);

function defaultPythonExecutable(): string {
  if (app.isPackaged) {
    const executable = join(
      process.resourcesPath,
      'sidecar',
      'ocean-backend',
      process.platform === 'win32' ? 'ocean-backend.exe' : 'ocean-backend',
    );
    if (!existsSync(executable)) {
      throw new Error('Packaged OceanMind Python sidecar is missing. Reinstall the complete application.');
    }
    return executable;
  }
  return developmentPython();
}

function parseDesktopBackendLaunch(value: unknown): DesktopBackendLaunch {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Desktop backend launch options are invalid.');
  }
  const options = value as Record<string, unknown>;
  if (Object.keys(options).some((key) => key !== 'workspacePath')) {
    throw new Error('Desktop backend launch accepts only a workspace path.');
  }
  if (typeof options.workspacePath !== 'string' || !options.workspacePath.trim()) {
    throw new Error('Desktop backend launch requires a workspace path.');
  }
  return {workspacePath: options.workspacePath};
}

function parseModelProviderSetup(value: unknown): ModelProviderSetup {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Model provider setup is invalid.');
  }
  const setup = value as Record<string, unknown>;
  if (Object.keys(setup).some((key) => !['coordinator', 'expert', 'skillCurator'].includes(key))) {
    throw new Error('Model provider setup contains unsupported fields.');
  }
  const parseRole = (role: string): ModelRoleProviderSetup => {
    const raw = setup[role];
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error(`${role} model setup is invalid.`);
    }
    const item = raw as Record<string, unknown>;
    if (Object.keys(item).some((key) => !['provider', 'model', 'baseUrl', 'apiKey'].includes(key))) {
      throw new Error(`${role} model setup contains unsupported fields.`);
    }
    if (item.provider !== 'openai' && item.provider !== 'anthropic') {
    throw new Error('Choose an OpenAI-compatible or Anthropic-compatible provider.');
    }
    if (typeof item.model !== 'string' || !item.model.trim() || item.model.trim().toLowerCase() === 'default' || item.model.trim().length > 256) {
      throw new Error(`${role} model must be a concrete provider model ID.`);
    }
    const result: ModelRoleProviderSetup = {provider: item.provider, model: item.model.trim()};
    if (item.baseUrl !== undefined) {
      if (typeof item.baseUrl !== 'string' || item.baseUrl.trim().length > 2048) {
      throw new Error('Model endpoint must be a URL no longer than 2048 characters.');
      }
      const endpoint = item.baseUrl.trim();
      if (endpoint) {
        let parsed: URL;
        try { parsed = new URL(endpoint); }
        catch { throw new Error('Model endpoint must be a valid HTTP(S) URL.'); }
        if ((parsed.protocol !== 'https:' && parsed.protocol !== 'http:') || parsed.username || parsed.password) {
          throw new Error('Model endpoint must be an HTTP(S) URL without embedded credentials.');
        }
        result.baseUrl = endpoint;
      }
    }
    if (item.apiKey !== undefined) {
      if (typeof item.apiKey !== 'string' || !item.apiKey.trim() || item.apiKey.length > 4096) {
        throw new Error('API key must be between 1 and 4096 characters.');
      }
      result.apiKey = item.apiKey;
    }
    return result;
  };
  return {coordinator: parseRole('coordinator'), expert: parseRole('expert'), skillCurator: parseRole('skillCurator')};
}

function parseModelProviderStatus(value: unknown): ModelProviderStatus {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Model provider helper returned an invalid response.');
  }
  const status = value as Record<string, unknown>;
  const roles = status.roles;
  if (roles === null || typeof roles !== 'object' || Array.isArray(roles)) {
    throw new Error('Model provider helper returned an invalid role response.');
  }
  const roleMap = roles as Record<string, unknown>;
  const parseRoleStatus = (key: string): ModelRoleProviderStatus => {
    const raw = roleMap[key];
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Model provider role is invalid.');
    const item = raw as Record<string, unknown>;
    const roleString = (field: string, maximum: number) => {
      const candidate = item[field];
      if (typeof candidate !== 'string' || !candidate || candidate.length > maximum) throw new Error('Model provider role is invalid.');
      return candidate;
    };
    if (typeof item.configured !== 'boolean') throw new Error('Model provider role is invalid.');
    const result: ModelRoleProviderStatus = {
      profile: roleString('profile', 128), label: roleString('label', 256), provider: roleString('provider', 128), model: roleString('model', 256), configured: item.configured,
    };
    if (item.base_url !== undefined && item.base_url !== null) {
      if (typeof item.base_url !== 'string' || item.base_url.length > 2048) throw new Error('Model provider role endpoint is invalid.');
      result.baseUrl = item.base_url;
    }
    return result;
  };
  const coordinator = parseRoleStatus('coordinator');
  const expert = parseRoleStatus('expert');
  const skillCurator = parseRoleStatus('skill_curator');
  return {
    coordinator, expert, skillCurator,
    configured: coordinator.configured && expert.configured && skillCurator.configured,
  };
}

function desktopTestBackendScript(): string | null {
  if (app.isPackaged || process.env.OCEAN_DESKTOP_TEST_MODE !== '1') return null;
  const script = process.env.OCEAN_DESKTOP_TEST_BACKEND_SCRIPT;
  if (!script) return null;
  if (!isAbsolute(script) || !existsSync(script)) {
    throw new Error('Desktop E2E backend fixture must be an existing absolute script path.');
  }
  return script;
}

class OceanBackendSidecar {
  private child: ChildProcessWithoutNullStreams | null = null;
  private workspacePath: string | null = null;
  private stateDirectory: string | null = null;
  private launchOptions: DesktopBackendLaunch | null = null;
  private restartTimer: NodeJS.Timeout | null = null;
  private restartAttempts = 0;
  private stopping = false;
  private readyEvent: Record<string, unknown> | null = null;
  private activeTaskId: string | null = null;
  private readonly grantedResources = new Map<string, GrantedArtifactResource>();

  constructor(
    private readonly publish: (frame: BackendFrame) => void,
    private readonly activeRendererId: () => number | null,
  ) {}

  async start(options: DesktopBackendLaunch): Promise<DesktopBackendStatus> {
    await this.stop();
    this.stopping = false;
    this.restartAttempts = 0;
    this.launchOptions = options;
    this.readyEvent = null;
    this.activeTaskId = null;
    this.clearGrantedResources();
    rememberDesktopProject(options.workspacePath);
    this.launch(options);
    return {running: true, workspacePath: resolve(options.workspacePath)};
  }

  async modelProviderStatus(): Promise<ModelProviderStatus> {
    if (desktopTestBackendScript()) {
      const fixture = {
        profile: 'desktop-fixture',
        label: 'Desktop test model',
        provider: 'desktop_checkpoint_fixture',
        model: 'fixture',
        configured: true,
      };
      return {coordinator: fixture, expert: fixture, skillCurator: fixture, configured: true};
    }
    return this.runProviderHelper('desktop-provider-status');
  }

  async configureModelProvider(setup: ModelProviderSetup): Promise<ModelProviderStatus> {
    return this.runProviderHelper('desktop-provider-config', setup);
  }

  private runProviderHelper(command: 'desktop-provider-status' | 'desktop-provider-config', setup?: ModelProviderSetup): Promise<ModelProviderStatus> {
    const executable = defaultPythonExecutable();
    const argumentsForHelper = app.isPackaged
      ? [command]
      : ['-m', 'oceanx', command];
    const helperEnvironment = process.env.OCEAN_DESKTOP_TEST_ISOLATED_PROFILE === '1'
      ? {...process.env, OCEANMIND_CONFIG_DIR: join(app.getPath('userData'), 'oceanmind')}
      : process.env;
    return new Promise((resolveHelper, rejectHelper) => {
      const child = spawn(executable, argumentsForHelper, {
        cwd: this.workspacePath ?? app.getPath('home'),
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        env: helperEnvironment,
      });
      let stdout = '';
      let stderr = '';
      let settled = false;
      const settle = (callback: () => void) => {
        if (settled) return;
        settled = true;
        callback();
      };
      child.stdout.setEncoding('utf8');
      child.stderr.setEncoding('utf8');
      child.stdout.on('data', (chunk: string) => { stdout = (stdout + chunk).slice(0, 16_384); });
      child.stderr.on('data', (chunk: string) => { stderr = (stderr + chunk).slice(0, 2_000); });
      child.on('error', () => settle(() => rejectHelper(new Error('Could not start the local model setup helper.'))));
      child.on('exit', (code) => settle(() => {
        if (code !== 0) {
          rejectHelper(new Error(stderr.trim() || 'Model setup did not complete.'));
          return;
        }
        try {
          resolveHelper(parseModelProviderStatus(JSON.parse(stdout)));
        } catch {
          rejectHelper(new Error('Model setup returned an invalid response.'));
        }
      }));
      // Secrets travel through a short-lived stdin pipe, never argv, a Protocol v2 frame, or a diagnostic.
      child.stdin.end(setup ? JSON.stringify({
        roles: {
          coordinator: {provider: setup.coordinator.provider, model: setup.coordinator.model, base_url: setup.coordinator.baseUrl ?? null, api_key: setup.coordinator.apiKey ?? null},
          expert: {provider: setup.expert.provider, model: setup.expert.model, base_url: setup.expert.baseUrl ?? null, api_key: setup.expert.apiKey ?? null},
          skill_curator: {provider: setup.skillCurator.provider, model: setup.skillCurator.model, base_url: setup.skillCurator.baseUrl ?? null, api_key: setup.skillCurator.apiKey ?? null},
        },
      }) : '');
    });
  }

  private launch(options: DesktopBackendLaunch): void {
    this.readyEvent = null;
    const workspacePath = resolve(options.workspacePath);
    const stateDirectory = join(workspacePath, '.oceanmind');
    const executable = defaultPythonExecutable();
    const usesFrozenSidecar = app.isPackaged;
    const testBackendScript = desktopTestBackendScript();
    const argumentsForBackend = testBackendScript
      ? [testBackendScript, '--state-dir', stateDirectory, '--client-kind', 'desktop']
      : usesFrozenSidecar
        ? ['backend', '--state-dir', stateDirectory, '--client-kind', 'desktop']
        : ['-m', 'oceanx', 'backend', '--state-dir', stateDirectory, '--client-kind', 'desktop'];
    const child = spawn(
      executable,
      argumentsForBackend,
      {
        cwd: workspacePath,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      },
    );
    this.child = child;
    this.workspacePath = workspacePath;
    this.stateDirectory = stateDirectory;
    const stdout = createInterface({input: child.stdout});
    const stderr = createInterface({input: child.stderr});
    stdout.on('line', (line) => this.onStdout(line));
    // Structured tool failures already return to the owning Agent and its Task trace.
    // stderr is an internal backend log stream, not a user-facing notification channel.
    stderr.on('line', (line) => this.publish({kind: 'diagnostic', source: 'backend_log', message: line.slice(0, 16_000)}));
    child.on('error', (error) => this.publish({kind: 'diagnostic', message: error.message}));
    child.on('exit', (code) => {
      const childIsCurrent = this.child === child;
      if (childIsCurrent) {
        this.child = null;
      }
      if (!shouldPublishBackendExit(this.stopping, childIsCurrent)) return;
      this.publish({kind: 'exit', code});
      if (!this.stopping && this.child === null && this.launchOptions && this.restartAttempts < 1) {
        this.restartAttempts += 1;
        this.publish({kind: 'diagnostic', message: 'OceanMind backend stopped unexpectedly. Restarting.'});
        this.restartTimer = setTimeout(() => {
          this.restartTimer = null;
          if (!this.stopping && this.launchOptions && this.child === null) {
            this.launch(this.launchOptions);
          }
        }, 750);
      }
    });
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.launchOptions = null;
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    const child = this.child;
    this.child = null;
    this.workspacePath = null;
    this.stateDirectory = null;
    this.readyEvent = null;
    this.activeTaskId = null;
    this.grantedResources.clear();
    if (!child || child.exitCode !== null) {
      return;
    }
    child.kill('SIGTERM');
    await new Promise<void>((resolveStop) => {
      const timeout = setTimeout(() => {
        if (child.exitCode === null) {
          child.kill('SIGKILL');
        }
      }, 5_000);
      child.once('exit', () => {
        clearTimeout(timeout);
        resolveStop();
      });
    });
  }

  async send(payload: Record<string, unknown>): Promise<void> {
    if (!this.child || this.child.stdin.destroyed) {
      throw new Error('OceanMind backend is not running');
    }
    const line = JSON.stringify(payload);
    if (Buffer.byteLength(line, 'utf8') > maxFrameBytes) {
      throw new Error('Protocol request exceeds the desktop frame limit');
    }
    const type = payload.type;
    const requestPayload = payload.payload;
    if (
      type === 'task.open'
      && requestPayload
      && typeof requestPayload === 'object'
      && typeof (requestPayload as Record<string, unknown>).task_id === 'string'
    ) {
      const nextTaskId = (requestPayload as Record<string, string>).task_id;
      if (shouldResetTaskResourceGrants(this.activeTaskId, nextTaskId)) {
        this.clearGrantedResources();
      }
      this.activeTaskId = nextTaskId;
    }
    if (
      type === 'task.archive'
      && requestPayload
      && typeof requestPayload === 'object'
      && (requestPayload as Record<string, unknown>).task_id === this.activeTaskId
    ) {
      this.activeTaskId = null;
      this.clearGrantedResources();
    }
    const child = this.child;
    const stdin = child.stdin;
    await new Promise<void>((resolveWrite, rejectWrite) => {
      let settled = false;
      const finish = (error?: Error | null) => {
        if (settled) return;
        settled = true;
        stdin.off('error', onError);
        child.off('exit', onExit);
        if (error) rejectWrite(error);
        else resolveWrite();
      };
      const onError = (error: Error) => finish(error);
      const onExit = () => finish(new Error('OceanMind backend exited before receiving the request bytes'));
      stdin.once('error', onError);
      child.once('exit', onExit);
      stdin.write(`${line}\n`, 'utf8', (error) => finish(error));
    });
  }

  status(): DesktopBackendStatus {
    return {
      running: this.child !== null && this.child.exitCode === null,
      workspacePath: this.workspacePath,
      readyEvent: this.readyEvent,
      activeTaskId: this.activeTaskId,
    };
  }

  clearGrantedResources(): void {
    this.grantedResources.clear();
  }

  isGrantedArtifactRequestAuthorized(requestUrl: string, webContentsId: number | undefined): boolean {
    if (webContentsId === undefined) return false;
    let url: URL;
    try {
      url = new URL(requestUrl);
    } catch {
      return false;
    }
    const token = this.grantedResourceToken(url);
    if (!token) return false;
    const grant = this.grantedResources.get(token);
    if (!grant || grant.expiresAt <= Date.now()) {
      if (grant) this.grantedResources.delete(token);
      return false;
    }
    return grant.webContentsId === webContentsId;
  }

  resumeAfterSystemWake(): void {
    if (this.stopping || !this.launchOptions) return;
    if (this.child !== null && this.child.exitCode === null) return;

    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    this.publish({kind: 'diagnostic', message: 'System resumed. Recovering OceanMind backend.'});
    this.launch(this.launchOptions);
  }

  async artifactResponse(requestUrl: string): Promise<Response> {
    if (!this.stateDirectory) {
      return new Response('OceanMind backend is not running', {status: 503});
    }
    let url: URL;
    try {
      url = new URL(requestUrl);
    } catch {
      return new Response('Invalid artifact URL', {status: 400});
    }
    if (url.search || url.hash || !url.hostname) {
      return new Response('Artifact URL is not permitted', {status: 403});
    }
    if (url.hostname === 'resource') {
      return this.grantedArtifactResponse(url);
    }
    if (url.hostname !== 'artifacts') {
      return new Response('Artifact URL is not permitted', {status: 403});
    }
    const segments = artifactPathSegments(url.pathname);
    if (segments === null) {
      return new Response('Artifact URL traverses a parent path', {status: 403});
    }
    const artifactRoot = resolve(this.stateDirectory, 'artifacts');
    const candidate = resolve(artifactRoot, ...segments);
    if (relative(artifactRoot, candidate).startsWith('..')) {
      return new Response('Artifact URL escapes the artifact store', {status: 403});
    }
    if (!existsSync(candidate)) {
      return new Response('Artifact resource is not found', {status: 404});
    }
    let realArtifactRoot: string;
    let realCandidate: string;
    try {
      realArtifactRoot = realpathSync(artifactRoot);
      realCandidate = realpathSync(candidate);
    } catch {
      return new Response('Artifact resource could not be resolved safely', {status: 403});
    }
    const candidateRelativePath = relative(realArtifactRoot, realCandidate);
    if (!isSafeContainedRelativePath(candidateRelativePath)) {
      return new Response('Artifact URL escapes the artifact store', {status: 403});
    }
    const mimeType = artifactResourceTypes.get(extname(realCandidate).toLowerCase());
    if (!mimeType) {
      return new Response('Artifact resource type is not permitted', {status: 415});
    }
    let resourceSize: number;
    try {
      const stat = statSync(realCandidate);
      if (!stat.isFile()) return new Response('Artifact resource is not a regular file', {status: 403});
      resourceSize = stat.size;
    } catch {
      return new Response('Artifact resource could not be inspected safely', {status: 403});
    }
    if (resourceSize > maxArtifactResourceBytes) {
      return new Response('Artifact resource exceeds the renderer byte limit', {status: 413});
    }
    try {
      return new Response(readFileSync(realCandidate), {
        headers: {
          'Cache-Control': 'no-store',
          'Content-Security-Policy': "default-src 'none'; sandbox",
          'Content-Type': mimeType,
          'X-Content-Type-Options': 'nosniff',
          'Access-Control-Allow-Origin': '*',
        },
      });
    } catch {
      return new Response('Artifact resource could not be read safely', {status: 404});
    }
  }

  async openGrantedArtifactResource(requestUrl: string, webContentsId: number): Promise<OpenArtifactResourceResult> {
    let url: URL;
    try {
      url = new URL(requestUrl);
    } catch {
      throw new Error('Supplementary material URL is invalid.');
    }
    const token = this.grantedResourceToken(url);
    const grant = token ? this.grantedResources.get(token) : undefined;
    if (!token || !grant || grant.webContentsId !== webContentsId || grant.expiresAt <= Date.now()) {
      throw new Error('Supplementary material access expired. Open it again from the task.');
    }
    if (this.resolveGrantedResource(grant.resourceUri) !== grant.path) {
      this.grantedResources.delete(token);
      throw new Error('Supplementary material no longer matches its saved result.');
    }
    const stat = statSync(grant.path);
    if (!stat.isFile() || stat.size !== grant.sizeBytes || sha256File(grant.path) !== grant.sha256) {
      this.grantedResources.delete(token);
      throw new Error('Supplementary material could not be verified.');
    }
    const message = await shell.openPath(grant.path);
    if (!message) return {opened: true, revealed: false};
    shell.showItemInFolder(grant.path);
    return {opened: false, revealed: true, message};
  }

  async chooseWorkspaceSource(kind: 'dataset' | 'paper'): Promise<{relativePath?: string; localPath?: string} | null> {
    if (!this.workspacePath) {
      throw new Error('Open a project before importing a local source.');
    }
    const result = await dialog.showOpenDialog(kind === 'dataset'
      ? {
          title: 'Choose local data',
          buttonLabel: 'Use selected path',
          message: 'Select a data file, or select the current folder to import the complete directory.',
          properties: ['openFile', 'openDirectory'],
        }
      : {
          title: 'Choose local PDF paper',
          buttonLabel: 'Import paper',
          properties: ['openFile'],
          filters: [{name: 'PDF', extensions: ['pdf']}],
        });
    if (result.canceled || !result.filePaths[0]) return null;
    let workspaceRoot: string;
    let selected: string;
    let sourceRelativePath: string;
    try {
      workspaceRoot = realpathSync(this.workspacePath);
      selected = realpathSync(resolve(result.filePaths[0]));
      sourceRelativePath = relative(workspaceRoot, selected);
      const selectedStat = statSync(selected);
      if (kind === 'paper') {
        if (
          !isSafeContainedRelativePath(sourceRelativePath)
          || !selectedStat.isFile()
          || extname(selected).toLowerCase() !== '.pdf'
        ) {
          throw new Error('unsafe project source');
        }
        return {relativePath: sourceRelativePath.split('\\').join('/')};
      }
      // A dataset remains at its user-owned location. The backend records one
      // Task-scoped read-only reference and mounts that path for Experts.
      if (!selectedStat.isFile() && !selectedStat.isDirectory()) throw new Error('unsafe data source');
      if (!isSafeContainedRelativePath(sourceRelativePath)) {
        return {localPath: selected};
      }
    } catch {
      throw new Error(kind === 'paper'
        ? 'The selected paper must remain inside the open project.'
        : 'The selected local data could not be referenced safely.');
    }
    return {relativePath: sourceRelativePath.split('\\').join('/')};
  }

  revealPortableExport(exportId: string): void {
    if (!this.stateDirectory) throw new Error('Open a project before revealing an export.');
    if (!/^export_[A-Za-z0-9]+$/.test(exportId)) throw new Error('Portable export identifier is not permitted.');
    const exportRoot = resolve(this.stateDirectory, 'exports');
    const candidate = resolve(exportRoot, exportId);
    if (relative(exportRoot, candidate).startsWith('..') || !existsSync(candidate)) {
      throw new Error('Portable export is unavailable.');
    }
    try {
      const realRoot = realpathSync(exportRoot);
      const realCandidate = realpathSync(candidate);
      const relation = relative(realRoot, realCandidate);
      if (!isSafeContainedRelativePath(relation) || !statSync(realCandidate).isDirectory()) {
        throw new Error('invalid export directory');
      }
      shell.showItemInFolder(realCandidate);
    } catch {
      throw new Error('Portable export is unavailable.');
    }
  }

  private onStdout(line: string): void {
    if (!line.startsWith(protocolPrefix)) {
      this.publish({kind: 'diagnostic', message: `Ignored backend stdout: ${line.slice(0, 2_000)}`});
      return;
    }
    const encodedFrame = line.slice(protocolPrefix.length);
    if (Buffer.byteLength(encodedFrame, 'utf8') > maxFrameBytes) {
      this.publish({kind: 'diagnostic', message: 'Backend emitted a Protocol v2 frame that exceeds the desktop byte limit.'});
      return;
    }
    try {
      const parsed = parseStrictJsonBytes(Buffer.from(encodedFrame, 'utf8'));
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Protocol v2 frame must be an object.');
      }
      const payload = parsed as Record<string, unknown>;
      this.captureGrantedResource(payload);
      if (payload.type === 'system.ready') {
        this.readyEvent = payload;
        // A ready sidecar proved the (re)start stable; a later crash may restart again.
        this.restartAttempts = 0;
      }
      this.publish({kind: 'event', payload});
    } catch {
      this.publish({kind: 'diagnostic', message: 'Backend emitted malformed Protocol v2 JSON.'});
    }
  }

  private captureGrantedResource(event: Record<string, unknown>): void {
    if (event.type !== 'request.completed' || (!this.stateDirectory && !this.workspacePath)) return;
    const payload = event.payload;
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) return;
    const result = (payload as Record<string, unknown>).result;
    if (result === null || typeof result !== 'object' || Array.isArray(result)) return;
    const grant = result as Record<string, unknown>;
    const resourceGrant = parseArtifactResourceGrant(grant);
    if (!resourceGrant) return;
    const webContentsId = this.activeRendererId();
    if (webContentsId === null) {
      this.publish({kind: 'diagnostic', message: 'Discarded an artifact resource grant without an active OceanMind window.'});
      delete grant.resource_token;
      delete grant.resource_uri;
      return;
    }
    const resourcePath = this.resolveGrantedResource(resourceGrant.resourceUri);
    if (resourcePath === null) {
      this.publish({kind: 'diagnostic', message: 'Rejected an invalid artifact resource grant from the backend.'});
      delete grant.resource_token;
      delete grant.resource_uri;
      return;
    }
    try {
      const stat = statSync(resourcePath);
      if (!stat.isFile() || stat.size !== resourceGrant.sizeBytes || sha256File(resourcePath) !== resourceGrant.sha256) {
        throw new Error('resource identity mismatch');
      }
    } catch {
      this.publish({kind: 'diagnostic', message: 'Rejected an unavailable artifact resource grant from the backend.'});
      delete grant.resource_token;
      delete grant.resource_uri;
      return;
    }
    this.grantedResources.set(resourceGrant.token, {
      path: resourcePath,
      resourceUri: resourceGrant.resourceUri,
      mimeType: resourceGrant.mimeType,
      sizeBytes: resourceGrant.sizeBytes,
      sha256: resourceGrant.sha256,
      expiresAt: Date.now() + resourceGrantLifetimeMs,
      webContentsId,
    });
    delete grant.resource_uri;
  }

  private resolveGrantedResource(uri: string): string | null {
    let parsed: URL;
    try {
      parsed = new URL(uri);
    } catch {
      return null;
    }
    if (parsed.protocol === 'file:') {
      if (!this.workspacePath || parsed.hostname || parsed.username || parsed.password || parsed.search || parsed.hash) return null;
      try {
        const realRoot = realpathSync(this.workspacePath);
        const realCandidate = realpathSync(fileURLToPath(parsed));
        const relation = relative(realRoot, realCandidate);
        return isSafeContainedRelativePath(relation) ? realCandidate : null;
      } catch {
        return null;
      }
    }
    if (!this.stateDirectory) return null;
    if (parsed.protocol !== 'ocean:' || parsed.hostname !== 'artifacts' || parsed.search || parsed.hash) return null;
    const segments = artifactPathSegments(parsed.pathname);
    if (segments === null) return null;
    const artifactRoot = resolve(this.stateDirectory, 'artifacts');
    const candidate = resolve(artifactRoot, ...segments);
    try {
      const realRoot = realpathSync(artifactRoot);
      const realCandidate = realpathSync(candidate);
      const relation = relative(realRoot, realCandidate);
      return isSafeContainedRelativePath(relation)
        ? realCandidate
        : null;
    } catch {
      return null;
    }
  }

  private async grantedArtifactResponse(url: URL): Promise<Response> {
    const token = this.grantedResourceToken(url);
    if (!token) {
      return new Response('Artifact resource token is not permitted', {status: 403});
    }
    const grant = this.grantedResources.get(token);
    if (!grant || grant.expiresAt <= Date.now()) {
      if (grant) this.grantedResources.delete(token);
      return new Response('Artifact resource grant expired or is unavailable', {status: 404});
    }
    if (this.resolveGrantedResource(grant.resourceUri) !== grant.path) {
      this.grantedResources.delete(token);
      return new Response('Artifact resource no longer matches its immutable grant', {status: 409});
    }
    try {
      const stat = statSync(grant.path);
      if (!stat.isFile() || stat.size !== grant.sizeBytes || sha256File(grant.path) !== grant.sha256) {
        this.grantedResources.delete(token);
        return new Response('Artifact resource no longer matches its immutable grant', {status: 409});
      }
    } catch {
      this.grantedResources.delete(token);
      return new Response('Artifact resource is unavailable', {status: 404});
    }
    try {
      return new Response(readFileSync(grant.path), {
        headers: {
          'Cache-Control': 'no-store',
          'Content-Security-Policy': "default-src 'none'; sandbox",
          'Content-Type': grant.mimeType,
          'X-Content-Type-Options': 'nosniff',
          'Access-Control-Allow-Origin': '*',
        },
      });
    } catch {
      this.grantedResources.delete(token);
      return new Response('Artifact resource is unavailable', {status: 404});
    }
  }

  private grantedResourceToken(url: URL): string | null {
    if (url.hostname !== 'resource' || url.search || url.hash) return null;
    const tokens = url.pathname.split('/').filter(Boolean);
    return tokens.length === 1 && /^res_[A-Za-z0-9]+$/.test(tokens[0]!) ? tokens[0]! : null;
  }
}

let mainWindow: BrowserWindow | null = null;
let desktopSession: Session | null = null;
let stoppingForQuit = false;
const sidecar = new OceanBackendSidecar((frame) => {
  mainWindow?.webContents.send('ocean:backend-frame', frame);
}, () => mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents.id : null);

function assertMainRenderer(event: IpcMainInvokeEvent): void {
  if (!mainWindow || mainWindow.isDestroyed() || event.sender.id !== mainWindow.webContents.id) {
    throw new Error('Desktop host IPC is available only to the active OceanMind window.');
  }
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
}

function createWindow(): void {
  if (!desktopSession) {
    throw new Error('OceanMind desktop session is unavailable.');
  }
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 680,
    backgroundColor: '#fafaf9',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      preload: join(moduleDirectory, 'preload.cjs'),
      session: desktopSession,
    },
  });
  const developmentUrl = process.env.ELECTRON_RENDERER_URL;
  const packagedPage = join(moduleDirectory, '..', 'dist', 'renderer', 'index.html');
  if (developmentUrl) {
    void mainWindow.loadURL(developmentUrl);
  } else {
    if (!existsSync(packagedPage)) {
      throw new Error('Desktop renderer is missing. Run npm run build in frontend/ocean-desktop.');
    }
    void mainWindow.loadFile(packagedPage);
  }
  mainWindow.webContents.setWindowOpenHandler(({url}) => {
    if (url.startsWith('https://')) void shell.openExternal(url);
    return {action: 'deny'};
  });
  mainWindow.webContents.on('will-attach-webview', (event) => event.preventDefault());
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowed = developmentUrl
      ? (() => {
        try {
          return new URL(url).origin === new URL(developmentUrl).origin;
        } catch {
          return false;
        }
      })()
      : url === pathToFileURL(packagedPage).toString();
    if (!allowed) event.preventDefault();
  });
  mainWindow.on('closed', () => {
    sidecar.clearGrantedResources();
    mainWindow = null;
  });
}

function recoverUpdateStaging(): void {
  const store = new UpdateStagingStore(join(app.getPath('userData'), 'update-staging'));
  void store.recover()
    .then(({discardedDirectories}) => {
      if (discardedDirectories.length) {
        const noun = discardedDirectories.length === 1 ? 'directory' : 'directories';
        console.warn(`Discarded ${discardedDirectories.length} incomplete OceanMind update staging ${noun}.`);
      }
    })
    .catch((error: unknown) => {
      console.error('OceanMind update staging recovery did not complete:', error);
    });
}

app.whenReady().then(() => {
  // Persistent partition keeps renderer localStorage (theme, density, pane width)
  // across restarts; isolated test profiles stay on a per-process in-memory partition.
  desktopSession = session.fromPartition(isolatedTestProfile ? `ocean-desktop-${process.pid}` : 'persist:ocean-desktop', {cache: false});
  updateHandoffStore = new UpdateHandoffStore(join(app.getPath('userData'), 'update-handoff'));
  desktopUpdatesReady = recoverUpdateHandoff().then(configureDesktopUpdates);
  desktopSession.protocol.handle('ocean-artifact', (request) => sidecar.artifactResponse(request.url));
  desktopSession.webRequest.onBeforeRequest({urls: ['ocean-artifact://resource/*']}, (details, callback) => {
    callback({cancel: !sidecar.isGrantedArtifactRequestAuthorized(details.url, details.webContentsId)});
  });
  powerMonitor.on('resume', () => sidecar.resumeAfterSystemWake());
  ipcMain.handle('ocean:choose-workspace', async (event) => {
    assertMainRenderer(event);
    const result = await dialog.showOpenDialog({properties: ['openDirectory', 'createDirectory']});
    const selected = result.canceled ? null : result.filePaths[0] ?? null;
    if (selected) rememberDesktopProject(selected);
    return selected;
  });
  ipcMain.handle('ocean:list-projects', (event) => {
    assertMainRenderer(event);
    return readDesktopProjects();
  });
  ipcMain.handle('ocean:forget-project', (event, projectPath: unknown) => {
    assertMainRenderer(event);
    if (typeof projectPath !== 'string' || !isAbsolute(projectPath)) {
      throw new Error('Project path must be absolute.');
    }
    return forgetDesktopProject(projectPath);
  });
  ipcMain.handle('ocean:choose-workspace-source', (event, kind: unknown) => {
    assertMainRenderer(event);
    if (kind !== 'dataset' && kind !== 'paper') {
      throw new Error('Unknown local source kind.');
    }
    return sidecar.chooseWorkspaceSource(kind);
  });
  ipcMain.handle('ocean:start-backend', (event, options: unknown) => {
    assertMainRenderer(event);
    return sidecar.start(parseDesktopBackendLaunch(options));
  });
  ipcMain.handle('ocean:stop-backend', (event) => {
    assertMainRenderer(event);
    return sidecar.stop();
  });
  ipcMain.handle('ocean:get-backend-status', (event) => {
    assertMainRenderer(event);
    return sidecar.status();
  });
  ipcMain.handle('ocean:get-model-provider-status', async (event) => {
    assertMainRenderer(event);
    return sidecar.modelProviderStatus();
  });
  ipcMain.handle('ocean:configure-model-provider', async (event, setup: unknown) => {
    assertMainRenderer(event);
    return sidecar.configureModelProvider(parseModelProviderSetup(setup));
  });
  ipcMain.handle('ocean:send-backend-frame', (event, frame: Record<string, unknown>) => {
    assertMainRenderer(event);
    return sidecar.send(frame);
  });
  ipcMain.handle('ocean:open-artifact-resource', (event, url: unknown) => {
    assertMainRenderer(event);
    if (typeof url !== 'string') throw new Error('Supplementary material URL must be a string.');
    return sidecar.openGrantedArtifactResource(url, event.sender.id);
  });
  ipcMain.handle('ocean:reveal-portable-export', (event, exportId: unknown) => {
    assertMainRenderer(event);
    if (typeof exportId !== 'string') throw new Error('Portable export identifier must be a string.');
    sidecar.revealPortableExport(exportId);
  });
  ipcMain.handle('ocean:get-update-status', async (event) => {
    assertMainRenderer(event);
    await desktopUpdatesReady;
    return desktopUpdateStatus;
  });
  ipcMain.handle('ocean:check-for-update', async (event) => {
    assertMainRenderer(event);
    await desktopUpdatesReady;
    return checkForDesktopUpdate();
  });
  ipcMain.handle('ocean:install-prepared-update', async (event) => {
    assertMainRenderer(event);
    await desktopUpdatesReady;
    return installPreparedDesktopUpdate();
  });
  createWindow();
  // Recovery is local housekeeping only; no update is applied without a later explicit installer contract.
  recoverUpdateStaging();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('second-instance', () => {
  if (!mainWindow) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.focus();
});

app.on('before-quit', (event) => {
  if (stoppingForQuit) return;
  event.preventDefault();
  stoppingForQuit = true;
  void sidecar.stop()
    .catch((error: unknown) => {
      console.error('OceanMind backend sidecar did not stop cleanly before quit:', error);
    })
    .finally(() => app.quit());
});
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => {
  if (!isolatedTestProfile) return;
  try {
    rmSync(isolatedTestProfile, {recursive: true, force: true});
  } catch (error) {
    console.warn('OceanMind test profile cleanup did not complete:', error);
  }
});
