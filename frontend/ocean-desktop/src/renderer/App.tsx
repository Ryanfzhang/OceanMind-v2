import {useCallback, useEffect, useRef, useState} from 'react';
import {Check, Database, FileText, Plus, SendHorizontal, Square, Trash2, X} from 'lucide-react';

import type {BackendFrame, DesktopUpdateStatus, ModelProviderSetup, ModelProviderStatus} from '../shared/bridge.js';
import {artifactFileName, asRecord, isActiveForegroundRequest, isTaskCreationReady, parseDesktopRuntimeCapabilities, projectNameFromPath, shouldApplyTaskSnapshot, sourceTitleFromPath} from './app-utils.js';
import {ConversationTranscript, taskResultKeys} from './components/ConversationTranscript.js';
import {InteractionDrawer} from './components/InteractionDrawer.js';
import {LocalSourceImportDrawer} from './components/LocalSourceImportDrawer.js';
import {OceanMindWelcome} from './components/OceanMindWelcome.js';
import {ProjectTaskSidebar} from './components/ProjectTaskSidebar.js';
import {ReportWorkbench} from './components/ReportWorkbench.js';
import {ResultWorkbench} from './components/SpatialWorkbench.js';
import {DesktopSettingsDialog} from './components/dialogs/DesktopSettingsDialog.js';
import {ProjectRemoveDialog} from './components/dialogs/ProjectRemoveDialog.js';
import {TaskDeleteDialog} from './components/dialogs/TaskDeleteDialog.js';
import {useUiLanguage} from './i18n.js';
import {isModelProviderReady} from './model-provider.js';
import {pendingInteractionFromPayload, type PendingInteraction} from './pending-interaction.js';
import {paperAcquisitionStorageKey, parsePaperAcquisitionCommand, readPaperAcquisitionMode, type PaperAcquisitionMode} from './paper-acquisition-mode.js';
import {forgetProject, mergeRememberedProjects, parseProjectCatalog, rememberProject, upsertTaskPreservingOrder, type ProjectCatalogEntry} from './project-catalog.js';
import {requestId} from './request-id.js';
import {taskResultRefKey} from './types.js';
import type {AppearanceTheme, ArtifactSummary, ArtifactVersion, DeliveryManifest, DisplayDensity, EventPayload, OceanEvent, RequestContext, ResearchTask, ResolvedAppearanceTheme, ResultDocument, TaskOutput, TaskResultFile, TaskResultRecord, TeamSnapshot, TranscriptItem, Workspace} from './types.js';

type PendingImport = {kind: 'dataset' | 'paper'; relativePath?: string; localPath?: string};
type PendingTaskCreate = {projectPath: string; title: string};
type ResultSurface = {kind: 'interactive_view' | 'report'; document: ResultDocument} | null;
type RequestHandler = (result: EventPayload) => void;
type RequestErrorHandler = (message: string) => void;
type TaskResultGrantPurpose =
  | 'report_viewer'
  | 'report_notebook'
  | 'report_code'
  | 'report_environment'
  | 'report_inputs'
  | 'report_reproducibility'
  | 'report_image'
  | 'interactive_view_data'
  | 'interactive_view_preview'
  | 'result_file';
const TERMINAL_WORKFLOW_STATES = new Set(['completed', 'incomplete', 'failed', 'cancelled']);
const PROJECT_CATALOG_STORAGE_KEY = 'oceanmind.project-catalog.v1';

type DeleteCandidate = {task: ResearchTask; projectPath: string};

function optimisticUser(request: string, value: string): TranscriptItem {
  return {item_id: `optimistic_${request}`, request_id: request, role: 'user', text: value, created_at: new Date().toISOString()};
}

function reconcile(items: TranscriptItem[], incoming: TranscriptItem): TranscriptItem[] {
  const exact = items.findIndex((item) => item.item_id === incoming.item_id);
  if (exact >= 0) return items.map((item, index) => index === exact ? {...item, ...incoming, created_at: incoming.created_at ?? item.created_at} : item);
  if (incoming.role === 'user' && incoming.request_id) {
    const optimistic = items.findIndex((item) => item.item_id === `optimistic_${incoming.request_id}`);
    // Keep the optimistic send time: elapsed timing starts when the question is
    // handed to OceanMind, not when the backend echoes the transcript item.
    if (optimistic >= 0) return items.map((item, index) => index === optimistic ? {...incoming, created_at: item.created_at ?? incoming.created_at} : item);
  }
  return [...items, incoming].sort((a, b) => (a.sequence ?? Number.MAX_SAFE_INTEGER) - (b.sequence ?? Number.MAX_SAFE_INTEGER));
}

function appearance(): ResolvedAppearanceTheme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function resultFileName(path: string): string {
  return path.split('/').filter(Boolean).at(-1) ?? path;
}

function declaredResultFile(result: TaskResultRecord, field: string, fallback: (file: TaskResultFile) => boolean): TaskResultFile | undefined {
  const declared = typeof result.content[field] === 'string' ? result.content[field] as string : null;
  return result.files.find((file) => declared && (file.path === declared || resultFileName(file.path) === declared))
    ?? result.files.find(fallback);
}

function directResultDocument(result: TaskResultRecord): ResultDocument {
  return {key: taskResultRefKey(result.result_ref), title: result.title, summary: result.summary, content: result.content};
}

function legacyResultDocument(artifact: ArtifactVersion): ResultDocument {
  return {key: `${artifact.ref.artifact_id}@v${artifact.ref.version}`, title: artifact.title, summary: artifact.summary, content: artifact.content};
}

export function App(): React.JSX.Element {
  const {text} = useUiLanguage();
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [context, setContext] = useState<RequestContext | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [tasks, setTasks] = useState<ResearchTask[]>([]);
  const [activeTask, setActiveTask] = useState<ResearchTask | null>(null);
  const [taskLoading, setTaskLoading] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptItem[]>([]);
  const [outputs, setOutputs] = useState<TaskOutput[]>([]);
  const [manifests, setManifests] = useState<DeliveryManifest[]>([]);
  const [taskResults, setTaskResults] = useState<TaskResultRecord[]>([]);
  const [team, setTeam] = useState<TeamSnapshot | null>(null);
  const [teamSnapshots, setTeamSnapshots] = useState<Record<string, TeamSnapshot>>({});
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState('');
  const [prompt, setPrompt] = useState('');
  const [paperAcquisitionMode, setPaperAcquisitionMode] = useState<PaperAcquisitionMode>('ask_before_download');
  const [newTaskTitle, setNewTaskTitle] = useState('');
  const [projects, setProjects] = useState<ProjectCatalogEntry[]>(() => parseProjectCatalog(window.localStorage.getItem(PROJECT_CATALOG_STORAGE_KEY)));
  const [attachmentMenu, setAttachmentMenu] = useState(false);
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(null);
  const [importTitle, setImportTitle] = useState('');
  const [importAuthors, setImportAuthors] = useState('');
  const [importYear, setImportYear] = useState('');
  const [importDoi, setImportDoi] = useState('');
  const [importing, setImporting] = useState(false);
  const [pendingInteraction, setPendingInteraction] = useState<PendingInteraction | null>(null);
  const [interactionAnswer, setInteractionAnswer] = useState('');
  const [resultSurface, setResultSurface] = useState<ResultSurface>(null);
  const [resultLoading, setResultLoading] = useState(false);
  const [resultError, setResultError] = useState<string | null>(null);
  const [resultData, setResultData] = useState<unknown>(null);
  const [resultPreviewUrl, setResultPreviewUrl] = useState<string | null>(null);
  const [resultFileUrl, setResultFileUrl] = useState<string | null>(null);
  const [reportMarkdown, setReportMarkdown] = useState('');
  const [reportResources, setReportResources] = useState<Array<{name: string; label: string; url: string; mimeType: string}>>([]);
  const [inlineStatus, setInlineStatus] = useState<string | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<DeleteCandidate | null>(null);
  const [projectRemoveCandidate, setProjectRemoveCandidate] = useState<ProjectCatalogEntry | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelProvider, setModelProvider] = useState<ModelProviderStatus | null>(null);
  const [modelSaving, setModelSaving] = useState(false);
  const [runtime, setRuntime] = useState<ReturnType<typeof parseDesktopRuntimeCapabilities>>(null);
  const [density, setDensity] = useState<DisplayDensity>('comfortable');
  const [appearanceTheme, setAppearanceTheme] = useState<AppearanceTheme>('system');
  const [systemTheme, setSystemTheme] = useState<ResolvedAppearanceTheme>(appearance);
  const [update, setUpdate] = useState<DesktopUpdateStatus>({configured: false, state: 'unavailable'});

  const handlers = useRef(new Map<string, RequestHandler>());
  const errorHandlers = useRef(new Map<string, RequestErrorHandler>());
  const contextRef = useRef<RequestContext | null>(null);
  const taskRef = useRef<ResearchTask | null>(null);
  const activeRequestRef = useRef<string | null>(null);
  const workspaceRef = useRef<Workspace | null>(null);
  const workspacePathRef = useRef<string | null>(null);
  const projectsRef = useRef<ProjectCatalogEntry[]>(projects);
  const selectedTaskIdRef = useRef<string | null>(null);
  const seenEvents = useRef(new Set<string>());
  const backendCapabilities = useRef(new Set<string>());
  const taskResultDataCache = useRef(new Map<string, unknown>());
  const openResultKey = useRef<string | null>(null);
  const pendingTaskRestore = useRef<string | null>(null);
  const pendingTaskDelete = useRef<string | null>(null);
  const pendingTaskCreate = useRef<PendingTaskCreate | null>(null);
  const projectActivationGeneration = useRef(0);

  useEffect(() => {contextRef.current = context;}, [context]);
  useEffect(() => {taskRef.current = activeTask;}, [activeTask]);
  useEffect(() => {activeRequestRef.current = activeRequestId;}, [activeRequestId]);
  useEffect(() => {workspaceRef.current = workspace;}, [workspace]);
  useEffect(() => {workspacePathRef.current = workspacePath;}, [workspacePath]);
  useEffect(() => {projectsRef.current = projects;}, [projects]);
  useEffect(() => {window.localStorage.setItem(PROJECT_CATALOG_STORAGE_KEY, JSON.stringify(projects));}, [projects]);
  useEffect(() => {
    const key = paperAcquisitionStorageKey(workspacePath, activeTask?.task_id ?? null);
    setPaperAcquisitionMode(readPaperAcquisitionMode(window.localStorage, key));
  }, [activeTask?.task_id, workspacePath]);
  useEffect(() => {
    void window.oceanDesktop.listProjects()
      .then((remembered) => setProjects((current) => mergeRememberedProjects(current, remembered)))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const updateTheme = () => setSystemTheme(media.matches ? 'dark' : 'light');
    media.addEventListener('change', updateTheme);
    return () => media.removeEventListener('change', updateTheme);
  }, []);

  const resolvedTheme = appearanceTheme === 'system' ? systemTheme : appearanceTheme;
  const send = useCallback(async (frame: EventPayload): Promise<boolean> => {
    try {await window.oceanDesktop.sendBackendFrame(frame); return true;}
    catch (error) {setInlineStatus(error instanceof Error ? error.message : 'Backend request failed.'); return false;}
  }, []);
  const request = useCallback((type: string, payload: EventPayload, handler?: RequestHandler, task = false, revisions = false, errorHandler?: RequestErrorHandler): string | null => {
    const current = contextRef.current;
    if (!current) return null;
    const id = requestId(type.replaceAll('.', '_'));
    if (handler) handlers.current.set(id, handler);
    if (errorHandler) errorHandlers.current.set(id, errorHandler);
    const targetTask = taskRef.current;
    const frame: EventPayload = {
      protocol_version: 2,
      request_id: id,
      type,
      payload,
      context: task && targetTask ? {...current, task_id: targetTask.task_id} : current,
    };
    if (revisions) {
      frame.expected_workspace_revision = workspaceRef.current?.revision ?? 0;
      if (task && targetTask) frame.expected_task_revision = targetTask.task_revision;
    }
    void send(frame).then((sent) => {if (!sent) {handlers.current.delete(id); errorHandlers.current.delete(id);}});
    return id;
  }, [send]);
  const taskRequest = useCallback((type: string, payload: EventPayload, targetTask: ResearchTask, handler?: RequestHandler, revisions = false): string | null => {
    const current = contextRef.current;
    if (!current) return null;
    const id = requestId(type.replaceAll('.', '_'));
    if (handler) handlers.current.set(id, handler);
    const frame: EventPayload = {
      protocol_version: 2,
      request_id: id,
      type,
      payload,
      context: {...current, task_id: targetTask.task_id},
    };
    if (revisions) {
      frame.expected_workspace_revision = workspaceRef.current?.revision ?? 0;
      frame.expected_task_revision = targetTask.task_revision;
    }
    void send(frame).then((sent) => {if (!sent) handlers.current.delete(id);});
    return id;
  }, [send]);

  const refreshTasks = useCallback(() => {
    request('task.list', {include_archived: false, limit: 200}, (result) => {
      const next = Array.isArray(result.tasks) ? result.tasks as ResearchTask[] : [];
      setTasks(next);
      const path = workspacePathRef.current;
      if (path) setProjects((current) => rememberProject(current, path, next));
    });
  }, [request]);
  const resetTaskPresentation = useCallback(() => {
    openResultKey.current = null;
    setTranscript([]);
    setOutputs([]);
    setManifests([]);
    setTaskResults([]);
    setTeam(null);
    setTeamSnapshots({});
    setActiveRequestId(null);
    setStreaming('');
    setPendingInteraction(null);
    setResultSurface(null);
    setResultLoading(false);
    setResultError(null);
    setResultData(null);
    setResultPreviewUrl(null);
    setResultFileUrl(null);
    setReportMarkdown('');
    setReportResources([]);
  }, []);
  const openTask = useCallback((taskId: string, optimisticTask?: ResearchTask) => {
    const switching = taskRef.current?.task_id !== taskId;
    selectedTaskIdRef.current = taskId;
    if (switching) resetTaskPresentation();
    if (optimisticTask) {
      taskRef.current = optimisticTask;
      setActiveTask(optimisticTask);
    }
    setTaskLoading((current) => switching || current);
    setInlineStatus(null);
    const id = request(
      'task.open',
      {task_id: taskId},
      undefined,
      false,
      false,
      (message) => {
        if (selectedTaskIdRef.current !== taskId) return;
        setTaskLoading(false);
        setInlineStatus(message);
      },
    );
    if (!id && selectedTaskIdRef.current === taskId) setTaskLoading(false);
  }, [request, resetTaskPresentation]);
  const loadTaskResults = useCallback((taskId: string) => {
    request('task.output.list', {task_id: taskId, limit: 500}, (result) => {
      if (selectedTaskIdRef.current !== taskId) return;
      setOutputs(Array.isArray(result.outputs) ? result.outputs as TaskOutput[] : []);
      setManifests(Array.isArray(result.delivery_manifests) ? result.delivery_manifests as DeliveryManifest[] : []);
      setTaskResults(Array.isArray(result.task_results) ? result.task_results as TaskResultRecord[] : []);
    });
  }, [request]);
  const connectReady = useCallback((payload: EventPayload, path: string, restoreTaskId?: string | null) => {
    const clientId = typeof payload.client_id === 'string' ? payload.client_id : null;
    const sessionId = typeof payload.session_id === 'string' ? payload.session_id : null;
    if (!clientId || !sessionId) return;
    backendCapabilities.current = new Set(
      Array.isArray(payload.capabilities)
        ? payload.capabilities.filter((value): value is string => typeof value === 'string')
        : [],
    );
    setRuntime(parseDesktopRuntimeCapabilities(payload.runtime_capabilities));
    const next = {client_id: clientId, session_id: sessionId, workspace_id: 'ws_desktop'} as RequestContext;
    contextRef.current = next; setContext(next);
    const sessionIdRequest = requestId('session_open');
    void send({protocol_version: 2, request_id: sessionIdRequest, type: 'session.open', payload: {requested_workspace_id: 'ws_desktop'}, context: next});
    const workspaceOpenId = requestId('workspace_open');
    handlers.current.set(workspaceOpenId, (result) => {
      const openedWorkspace = result as unknown as Workspace;
      workspaceRef.current = openedWorkspace;
      setWorkspace(openedWorkspace);
      setProjects((current) => rememberProject(current, path));
      refreshTasks();
      if (restoreTaskId) openTask(restoreTaskId);
      pendingTaskRestore.current = null;
    });
    void send({protocol_version: 2, request_id: workspaceOpenId, type: 'workspace.open', payload: {path}, context: next});
  }, [openTask, refreshTasks, send]);

  const handleFrame = useCallback((frame: BackendFrame) => {
    if (frame.kind === 'diagnostic') {if (frame.source !== 'backend_log') setInlineStatus(frame.message); return;}
    if (frame.kind === 'exit') {setInlineStatus(`Backend exited (${frame.code ?? 0}).`); setContext(null); return;}
    const event = frame.payload as OceanEvent;
    if (event.event_id && seenEvents.current.has(event.event_id)) return;
    if (event.event_id) {seenEvents.current.add(event.event_id); if (seenEvents.current.size > 2_000) seenEvents.current.clear();}
    const payload = event.payload ?? {};
    if (event.type === 'system.ready') {
      const currentWorkspacePath = workspacePathRef.current;
      if (currentWorkspacePath) connectReady(payload, currentWorkspacePath, pendingTaskRestore.current);
      return;
    }
    if (event.type === 'request.completed') {
      const handler = event.request_id ? handlers.current.get(event.request_id) : undefined;
      if (event.request_id) {handlers.current.delete(event.request_id); errorHandlers.current.delete(event.request_id);}
      handler?.(asRecord(payload.result));
      if (isActiveForegroundRequest(event.request_id, activeRequestRef.current)) {
        setActiveRequestId(null); setStreaming('');
        const taskId = event.task_id ?? taskRef.current?.task_id;
        if (taskId) openTask(taskId);
        refreshTasks();
      }
      return;
    }
    if (event.type === 'request.failed' || event.type === 'request.cancelled' || event.type === 'system.error') {
      const error = asRecord(payload.error);
      const message = typeof error.message === 'string' ? error.message : typeof payload.reason === 'string' ? payload.reason : 'Request did not complete.';
      const errorHandler = event.request_id ? errorHandlers.current.get(event.request_id) : undefined;
      if (event.request_id) {handlers.current.delete(event.request_id); errorHandlers.current.delete(event.request_id);}
      errorHandler?.(message);
      setInlineStatus(message);
      if (isActiveForegroundRequest(event.request_id, activeRequestRef.current)) {setActiveRequestId(null); setStreaming(''); refreshTasks();}
      setTaskLoading(false);
      return;
    }
    if (event.type === 'request.accepted' && payload.request_type === 'session.submit' && event.request_id) {
      setActiveRequestId(event.request_id); refreshTasks(); return;
    }
    if (event.type === 'workspace.snapshot') {setWorkspace(payload as unknown as Workspace); refreshTasks(); return;}
    if (event.type === 'workspace.changed' && typeof payload.workspace_revision === 'number') setWorkspace((current) => current ? {...current, revision: payload.workspace_revision as number} : current);
    if ((event.type === 'artifact.created' || event.type === 'artifact.version.created') && payload.artifact) {
      const artifact = payload.artifact as ArtifactSummary;
      setWorkspace((current) => current ? {...current, artifacts: [artifact, ...current.artifacts.filter((item) => item.ref.artifact_id !== artifact.ref.artifact_id)]} : current);
    }
    if (event.type === 'task.snapshot') {
      const task = {...payload.task as ResearchTask, workflow: (payload.workflow as ResearchTask['workflow']) ?? null};
      if (!shouldApplyTaskSnapshot(selectedTaskIdRef.current, task.task_id)) return;
      const workflowIsTerminal = Boolean(task.workflow && TERMINAL_WORKFLOW_STATES.has(task.workflow.state));
      const snapshotRequestId = workflowIsTerminal ? null : task.active_request_id ?? null;
      taskRef.current = task;
      setTaskLoading(false); setActiveTask(task); setTranscript(Array.isArray(payload.transcript) ? payload.transcript as TranscriptItem[] : []);
      setOutputs(Array.isArray(payload.outputs) ? payload.outputs as TaskOutput[] : []);
      setManifests(Array.isArray(payload.delivery_manifests) ? payload.delivery_manifests as DeliveryManifest[] : []);
      setTaskResults([]);
      const snapshotTeam = payload.team_snapshot
        ? {...payload.team_snapshot as TeamSnapshot, request_id: (payload.team_snapshot as TeamSnapshot).request_id ?? snapshotRequestId ?? task.workflow?.request_id}
        : null;
      const historicalTeams = Array.isArray(payload.team_snapshots)
        ? payload.team_snapshots as TeamSnapshot[]
        : [];
      const teamsByRequest = Object.fromEntries(
        [...historicalTeams, ...(snapshotTeam ? [snapshotTeam] : [])]
          .flatMap((item) => item.request_id ? [[item.request_id, item] as const] : []),
      );
      setTeam(snapshotTeam);
      setTeamSnapshots(teamsByRequest);
      setPendingInteraction(Array.isArray(payload.interactions) ? payload.interactions.map(pendingInteractionFromPayload).find(Boolean) as PendingInteraction | undefined ?? null : null);
      setActiveRequestId(snapshotRequestId);
      if (workflowIsTerminal) setStreaming('');
      loadTaskResults(task.task_id);
      setTasks((current) => {
        const next = upsertTaskPreservingOrder(current, task).filter((item) => item.status !== 'archived');
        const path = workspacePathRef.current;
        if (path) setProjects((catalog) => rememberProject(catalog, path, next));
        return next;
      });
      if (pendingTaskDelete.current === task.task_id) {
        pendingTaskDelete.current = null;
        taskRequest('task.delete', {task_id: task.task_id}, task, () => {
          setActiveTask(null);
          resetTaskPresentation();
          refreshTasks();
        }, true);
      }
      return;
    }
    const visible = !event.task_id || event.task_id === taskRef.current?.task_id;
    if (!visible) {if (event.type === 'request.completed' || event.type === 'request.failed') refreshTasks(); return;}
    if (event.type === 'team.snapshot') {
      const snapshotTeam = {...payload as unknown as TeamSnapshot, request_id: event.request_id ?? (payload.request_id as string | undefined)};
      setTeam(snapshotTeam);
      if (snapshotTeam.request_id) {
        setTeamSnapshots((current) => ({...current, [snapshotTeam.request_id!]: snapshotTeam}));
      }
    }
    if (event.type === 'transcript.item.appended') setTranscript((current) => reconcile(current, {...payload as unknown as TranscriptItem, created_at: typeof payload.created_at === 'string' ? payload.created_at : event.timestamp, request_id: typeof payload.request_id === 'string' ? payload.request_id : event.request_id ?? null}));
    if (event.type === 'assistant.delta' && event.request_id === activeRequestRef.current && typeof payload.text === 'string') setStreaming((current) => (current + payload.text).slice(-32_000));
    if (event.type === 'assistant.turn.completed' && event.request_id === activeRequestRef.current) setStreaming('');
    if (event.type === 'interaction.requested') {const interaction = pendingInteractionFromPayload(payload); if (interaction) setPendingInteraction(interaction);}
  }, [connectReady, loadTaskResults, openTask, refreshTasks, resetTaskPresentation, taskRequest]);

  useEffect(() => window.oceanDesktop.onBackendFrame(handleFrame), [handleFrame]);
  useEffect(() => {
    void window.oceanDesktop.getModelProviderStatus().then(setModelProvider).catch(() => setModelProvider(null));
    void window.oceanDesktop.getUpdateStatus().then(setUpdate).catch(() => undefined);
    void window.oceanDesktop.getBackendStatus().then((status) => {
      if (!status.running || !status.workspacePath) return;
      workspacePathRef.current = status.workspacePath;
      setWorkspacePath(status.workspacePath);
      const ready = status.readyEvent as OceanEvent | null;
      if (ready?.type === 'system.ready') connectReady(ready.payload ?? {}, status.workspacePath);
    }).catch(() => undefined);
  }, [connectReady]);
  useEffect(() => {
    if (!workspacePath || context) return;
    void send({protocol_version: 2, request_id: requestId('handshake'), type: 'system.handshake', payload: {client_kind: 'desktop', client_version: '0.1.0', supported_protocol_versions: [2]}});
  }, [context, send, workspacePath]);

  const activateProject = async (path: string, restoreTaskId: string | null = null) => {
    const optimisticTask = restoreTaskId
      ? projectsRef.current.find((entry) => entry.path === path)?.tasks.find((task) => task.task_id === restoreTaskId)
      : undefined;
    if (path === workspacePathRef.current) {
      if (restoreTaskId && contextRef.current) openTask(restoreTaskId, optimisticTask);
      else if (restoreTaskId) {
        pendingTaskRestore.current = restoreTaskId;
        selectedTaskIdRef.current = restoreTaskId;
        taskRef.current = optimisticTask ?? null;
        setActiveTask(optimisticTask ?? null);
        setTaskLoading(true);
      } else {
        selectedTaskIdRef.current = null;
        taskRef.current = null;
        setActiveTask(null);
        resetTaskPresentation();
      }
      return;
    }
    const activationGeneration = projectActivationGeneration.current + 1;
    projectActivationGeneration.current = activationGeneration;
    pendingTaskRestore.current = restoreTaskId;
    resetTaskPresentation();
    taskResultDataCache.current.clear();
    handlers.current.clear();
    errorHandlers.current.clear();
    pendingTaskCreate.current = null;
    workspacePathRef.current = null;
    workspaceRef.current = null;
    selectedTaskIdRef.current = restoreTaskId;
    taskRef.current = optimisticTask ?? null;
    setProjects((current) => rememberProject(current, path));
    setWorkspacePath(null); setWorkspace(null); setTasks([]); setActiveTask(optimisticTask ?? null); setTaskLoading(Boolean(restoreTaskId)); contextRef.current = null; setContext(null);
    try {
      await window.oceanDesktop.startBackend({workspacePath: path});
    } catch (error) {
      if (projectActivationGeneration.current === activationGeneration) {
        setTaskLoading(false);
        setInlineStatus(error instanceof Error ? error.message : text('Could not start the project backend.', '无法启动项目后端。'));
      }
      return;
    }
    if (projectActivationGeneration.current !== activationGeneration) return;
    workspacePathRef.current = path;
    setWorkspacePath(path);
  };
  const chooseProject = async () => {
    const selected = await window.oceanDesktop.chooseWorkspace();
    if (!selected) return;
    await activateProject(selected);
  };
  const startNewResearch = (projectPath: string) => {
    setNewTaskTitle('');
    void activateProject(projectPath);
  };
  const dispatchTaskCreate = useCallback((pending: PendingTaskCreate): boolean => {
    if (!isTaskCreationReady(
      pending.projectPath,
      workspacePathRef.current,
      Boolean(contextRef.current),
      Boolean(workspaceRef.current),
    )) return false;
    const id = request(
      'task.create',
      {title: pending.title},
      (result) => {
        const task = result.task as ResearchTask | undefined;
        if (!task?.task_id) {
          setNewTaskTitle(pending.title);
          setInlineStatus(text('The backend did not return the created task.', '后端没有返回已创建的任务。'));
          return;
        }
        setTasks((current) => {
          const next = upsertTaskPreservingOrder(current, task);
          setProjects((catalog) => rememberProject(catalog, pending.projectPath, next));
          return next;
        });
        openTask(task.task_id, task);
      },
      false,
      false,
      (message) => {
        if (workspacePathRef.current === pending.projectPath) setNewTaskTitle(pending.title);
        setInlineStatus(message);
      },
    );
    return Boolean(id);
  }, [openTask, request, text]);
  const createTask = () => {
    const title = newTaskTitle.trim();
    const projectPath = workspacePathRef.current;
    if (!title || !projectPath) return;
    const pending = {projectPath, title};
    if (!dispatchTaskCreate(pending)) {
      // system.ready arrives before workspace.open completes. Keep the user's
      // title and submit once the workspace is actually ready for task writes.
      pendingTaskCreate.current = pending;
      return;
    }
    pendingTaskCreate.current = null;
    setNewTaskTitle('');
  };
  useEffect(() => {
    const pending = pendingTaskCreate.current;
    if (
      !context
      || !workspace
      || !pending
      || pending.projectPath !== workspacePath
      || !dispatchTaskCreate(pending)
    ) return;
    pendingTaskCreate.current = null;
    setNewTaskTitle((current) => current.trim() === pending.title ? '' : current);
  }, [context, dispatchTaskCreate, workspace, workspacePath]);
  const submit = () => {
    const userText = prompt.trim(); const task = taskRef.current;
    if (!userText || !task || task.status === 'archived' || task.active_request_id) return;
    const paperCommand = parsePaperAcquisitionCommand(userText);
    if (paperCommand) {
      if (paperCommand.kind === 'set') {
        const key = paperAcquisitionStorageKey(workspacePathRef.current, task.task_id);
        if (key) window.localStorage.setItem(key, paperCommand.mode);
        setPaperAcquisitionMode(paperCommand.mode);
        setInlineStatus(null);
      } else if (paperCommand.kind === 'show') {
        setPaperAcquisitionMode(readPaperAcquisitionMode(window.localStorage, paperAcquisitionStorageKey(workspacePathRef.current, task.task_id)));
        setInlineStatus(null);
      } else {
        setInlineStatus(text('Use /papers ask, /papers auto, or /papers search-only.', '请输入 /papers ask、/papers auto 或 /papers search-only。'));
      }
      setPrompt('');
      return;
    }
    if (!isModelProviderReady(modelProvider)) {setSettingsOpen(true); return;}
    const activePaperMode = readPaperAcquisitionMode(window.localStorage, paperAcquisitionStorageKey(workspacePathRef.current, task.task_id));
    const id = requestId('session_submit');
    setTranscript((current) => [...current, optimisticUser(id, userText)]); setPrompt(''); setStreaming(''); setTeam(null); setActiveRequestId(id); setInlineStatus(null);
    void send({protocol_version: 2, request_id: id, type: 'session.submit', payload: {text: userText, context_refs: [], literature_acquisition_mode: activePaperMode}, context: {...contextRef.current!, task_id: task.task_id}, expected_workspace_revision: workspaceRef.current?.revision ?? 0, expected_task_revision: task.task_revision});
  };
  const setPaperModeFromMenu = (mode: PaperAcquisitionMode) => {
    const task = taskRef.current;
    if (!task) return;
    const key = paperAcquisitionStorageKey(workspacePathRef.current, task.task_id);
    if (key) window.localStorage.setItem(key, mode);
    setPaperAcquisitionMode(mode);
    setPrompt('');
    setInlineStatus(null);
  };
  const cancel = () => {if (activeRequestId) request('request.cancel', {target_request_id: activeRequestId, reason: 'Cancelled from desktop'}, undefined, true);};
  const answer = (fixed?: string) => {
    const value = fixed ?? interactionAnswer.trim(); if (!pendingInteraction || !value) return;
    request('interaction.respond', {interaction_id: pendingInteraction.interactionId, answer: value}, undefined, true); setPendingInteraction(null); setInteractionAnswer('');
  };
  const chooseSource = async (kind: PendingImport['kind']) => {
    setAttachmentMenu(false); const selected = await window.oceanDesktop.chooseWorkspaceSource(kind); if (!selected) return;
    const selectedPath = selected.relativePath ?? selected.localPath; if (!selectedPath) return;
    setPendingImport({kind, relativePath: selected.relativePath, localPath: selected.localPath}); setImportTitle(sourceTitleFromPath(selectedPath)); setImportAuthors(''); setImportYear(''); setImportDoi('');
  };
  const commitImport = () => {
    if (!pendingImport || !importTitle.trim()) return;
    setImporting(true); const done = () => {setImporting(false); setPendingImport(null); refreshTasks(); if (taskRef.current) openTask(taskRef.current.task_id);};
    if (pendingImport.kind === 'dataset') {
      request('dataset.import', {relative_path: pendingImport.relativePath, local_path: pendingImport.localPath, materialization_level: 'local_reference', title: importTitle.trim()}, done, true, true);
    } else {
      const year = Number.parseInt(importYear, 10); const citation: EventPayload = {title: importTitle.trim(), authors: importAuthors.split(',').map((item) => item.trim()).filter(Boolean)};
      if (Number.isInteger(year)) citation.publication_year = year; if (importDoi.trim()) citation.doi = importDoi.trim();
      request('paper.import', {relative_path: pendingImport.relativePath, citation, materialization_acknowledged: true}, done, true, true);
    }
  };
  const cancelImport = () => {setPendingImport(null);};
  const grant = (artifact: ArtifactVersion, fileName: string, purpose: string): Promise<string> => new Promise((resolve, reject) => {
    const id = request('artifact.resource.grant', {artifact_ref: artifact.ref, file_name: fileName, purpose}, (result) => {
      const token = typeof result.resource_token === 'string' ? result.resource_token : null;
      if (token) resolve(`ocean-artifact://resource/${token}`);
      else reject(new Error('Resource grant was unavailable.'));
    });
    if (!id) reject(new Error('Backend is not connected.'));
  });
  const grantTaskResult = (result: TaskResultRecord, file: TaskResultFile, purpose: TaskResultGrantPurpose): Promise<string> => new Promise((resolve, reject) => {
    const id = request('task_result.resource.grant', {result_ref: result.result_ref, file_name: file.path, purpose}, (value) => {
      const token = typeof value.resource_token === 'string' ? value.resource_token : null;
      if (token) resolve(`ocean-artifact://resource/${token}`);
      else reject(new Error('Task result resource grant was unavailable.'));
    }, true);
    if (!id) reject(new Error('Backend is not connected.'));
  });
  const openTaskResultFile = (result: TaskResultRecord, file: TaskResultFile) => {
    void grantTaskResult(result, file, 'result_file').then((url) => {
      return window.oceanDesktop.openArtifactResource(url);
    }).then((opened) => {
      if (opened.revealed) setInlineStatus('No notebook application is registered, so OceanMind revealed the saved file instead.');
    }).catch((error) => {
      setInlineStatus(error instanceof Error ? error.message : 'Supplementary material is unavailable.');
    });
  };
  const openReportResource = (resource: {url: string}) => {
    void window.oceanDesktop.openArtifactResource(resource.url).then((opened) => {
      if (opened.revealed) setInlineStatus('No application is registered for this file, so OceanMind revealed it instead.');
    }).catch((error) => {
      setInlineStatus(error instanceof Error ? error.message : 'Report resource is unavailable.');
    });
  };
  const openTaskResult = (result: TaskResultRecord) => {
    if (result.kind !== 'interactive_view' && result.kind !== 'report') return;
    const kind = result.kind;
    const resultKey = taskResultRefKey(result.result_ref);
    openResultKey.current = resultKey;
    setResultSurface({kind, document: directResultDocument(result)});
    setResultLoading(true); setResultError(null); setResultData(null); setResultPreviewUrl(null); setResultFileUrl(null); setReportMarkdown(''); setReportResources([]);
    if (kind === 'interactive_view') {
      const dataFile = declaredResultFile(result, 'data_file', (file) => file.mime_type === 'application/json' || file.path.endsWith('.nc'));
      const datasetFile = declaredResultFile(result, 'dataset_file', (file) => file.path.endsWith('.nc'));
      const previewFile = declaredResultFile(result, 'preview_file', (file) => file.mime_type.startsWith('image/'));
      if (!dataFile) {setResultLoading(false); setResultError('Interactive data file is missing.'); return;}
      const downloadFile = datasetFile ?? dataFile;
      void grantTaskResult(result, downloadFile, 'result_file').then((url) => {
        if (openResultKey.current === resultKey) setResultFileUrl(url);
      }).catch(() => undefined);
      if (previewFile) void grantTaskResult(result, previewFile, 'interactive_view_preview').then((url) => {
        if (openResultKey.current === resultKey) setResultPreviewUrl(url);
      }).catch(() => undefined);
      // Hydration capability is authoritative.  Older saved results may have
      // been conservatively marked as file-only even though their canonical
      // view.json + data.nc pair is fully renderable by the current backend.
      if (backendCapabilities.current.has('task_result.interactive_view.get')) {
        const cached = taskResultDataCache.current.get(resultKey);
        if (cached) {
          setResultData(cached);
          setResultLoading(false);
          return;
        }
        const id = request('task_result.interactive_view.get', {result_ref: result.result_ref}, (value) => {
          const inline = value.data;
          const token = typeof value.resource_token === 'string' ? value.resource_token : null;
          const load = inline && typeof inline === 'object'
            ? Promise.resolve(inline)
            : token
              ? fetch(`ocean-artifact://resource/${token}`).then((response) => {
                  if (!response.ok) throw new Error(`Result data could not be read (${response.status}).`);
                  return response.json() as Promise<unknown>;
                })
              : Promise.reject(new Error('Interactive result data grant was unavailable.'));
          void load.then((data) => {
            taskResultDataCache.current.set(resultKey, data);
            if (taskResultDataCache.current.size > 24) {
              const oldest = taskResultDataCache.current.keys().next().value as string | undefined;
              if (oldest) taskResultDataCache.current.delete(oldest);
            }
            if (openResultKey.current === resultKey) {setResultData(data); setResultLoading(false);}
          }).catch((error) => {
            if (openResultKey.current === resultKey) {
              // A bad optional interactive representation must not hide a
              // valid saved preview or downloadable result.
              if (!previewFile && !downloadFile) setResultError(error instanceof Error ? error.message : 'Could not load interactive result.');
              setResultLoading(false);
            }
          });
        }, true, false, (message) => {
          if (openResultKey.current === resultKey) {
            if (!previewFile && !downloadFile) setResultError(message);
            setResultLoading(false);
          }
        });
        if (!id && openResultKey.current === resultKey) {
          if (!previewFile && !downloadFile) setResultError('Backend is not connected.');
          setResultLoading(false);
        }
      } else {
        setResultLoading(false);
        if (!previewFile && !downloadFile) setResultError('Restart OceanMind to load this interactive result with the matching backend.');
      }
      return;
    }
    const reportFile = declaredResultFile(result, 'markdown_file', (file) => file.mime_type === 'text/markdown');
    if (!reportFile) {setResultLoading(false); setResultError('Report file is missing.'); return;}
    const resources = result.files.filter((file) => file.path !== reportFile.path);
    void grantTaskResult(result, reportFile, 'report_viewer')
      .then((url) => fetch(url).then((response) => {
        if (!response.ok) throw new Error(`Report could not be read (${response.status}).`);
        return response.text();
      }))
      .then((value) => {
        if (openResultKey.current !== resultKey) return;
        setReportMarkdown(value);
        setResultLoading(false);
        return Promise.allSettled(resources.map((file) => grantTaskResult(result, file, 'result_file').then((url) => ({
          name: resultFileName(file.path), label: resultFileName(file.path), url, mimeType: file.mime_type,
        })))).then((settled) => {
          if (openResultKey.current !== resultKey) return;
          setReportResources(settled.flatMap((entry) => entry.status === 'fulfilled' ? [entry.value] : []));
        });
      })
      .catch((error) => {
        if (openResultKey.current === resultKey) {
          setResultError(error instanceof Error ? error.message : 'Could not load report.');
          setResultLoading(false);
        }
      });
  };
  const openResult = (output: TaskOutput) => {
    setResultLoading(true); setResultError(null); setResultData(null); setResultPreviewUrl(null); setResultFileUrl(null); setReportMarkdown(''); setReportResources([]);
    request('artifact.get', {ref: output.artifact.ref}, (result) => {
      const artifact = result.artifact as ArtifactVersion | undefined;
      if (!artifact) {setResultLoading(false); setResultError('Artifact was not found.'); return;}
      const kind = artifact.artifact_type === 'interactive_view' ? 'interactive_view' : 'report'; setResultSurface({kind, document: legacyResultDocument(artifact)});
      const files = artifact.files ?? [];
      if (kind === 'interactive_view') {
        const dataFile = files.find((file) => file.mime_type === 'application/json');
        const previewFile = files.find((file) => file.mime_type === 'image/png');
        if (!dataFile || !artifactFileName(dataFile)) {setResultLoading(false); setResultError('Interactive data file is missing.'); return;}
        Promise.all([
          grant(artifact, artifactFileName(dataFile)!, 'interactive_view_data').then((url) => fetch(url).then((response) => response.json())),
          previewFile && artifactFileName(previewFile) ? grant(artifact, artifactFileName(previewFile)!, 'interactive_view_preview') : Promise.resolve(null),
        ]).then(([data, preview]) => {setResultData(data); setResultPreviewUrl(preview); setResultLoading(false);}).catch((error) => {setResultError(error instanceof Error ? error.message : 'Could not load interactive view.'); setResultLoading(false);});
      } else {
        const reportFile = files.find((file) => file.mime_type === 'text/markdown');
        if (!reportFile || !artifactFileName(reportFile)) {setResultLoading(false); setResultError('Report file is missing.'); return;}
        const reportImages = files.filter((file) => file.mime_type === 'image/png' && artifactFileName(file));
        const resourceSpecs = [
          {name: 'analysis.ipynb', purpose: 'report_notebook', label: text('Analysis notebook', '分析 notebook'), mimeType: 'application/x-ipynb+json'},
          {name: 'analysis.py', purpose: 'report_code', label: text('Python source', 'Python 源代码'), mimeType: 'text/x-python'},
          {name: 'requirements.txt', purpose: 'report_environment', label: text('Environment requirements', '环境依赖'), mimeType: 'text/plain'},
          {name: 'inputs.json', purpose: 'report_inputs', label: text('Immutable inputs', '不可变输入'), mimeType: 'application/json'},
          {name: 'reproducibility.json', purpose: 'report_reproducibility', label: text('Method manifest', '方法清单'), mimeType: 'application/json'},
          ...reportImages.map((file, index) => ({name: artifactFileName(file)!, purpose: 'report_image', label: text(`Figure ${index + 1}`, `图 ${index + 1}`), mimeType: 'image/png'})),
        ];
        Promise.all([
          grant(artifact, artifactFileName(reportFile)!, 'report_viewer').then((url) => fetch(url).then((response) => response.text())),
          Promise.all(resourceSpecs.flatMap((spec) => files.some((file) => artifactFileName(file) === spec.name) ? [grant(artifact, spec.name, spec.purpose).then((url) => ({...spec, url}))] : [])),
        ]).then(([value, resources]) => {setReportMarkdown(value); setReportResources(resources); setResultLoading(false);}).catch((error) => {setResultError(error instanceof Error ? error.message : 'Could not load report.'); setResultLoading(false);});
      }
    });
  };
  const closeResult = () => {openResultKey.current = null; setResultSurface(null); setResultData(null); setResultPreviewUrl(null); setResultFileUrl(null); setReportMarkdown(''); setReportResources([]); setResultError(null);};
  const configureModel = (setup: ModelProviderSetup) => {setModelSaving(true); void window.oceanDesktop.configureModelProvider(setup).then((next) => {setModelProvider(next); setModelSaving(false); if (next.configured) setSettingsOpen(false);}).catch(() => setModelSaving(false));};
  const deleteTask = () => {
    if (!deleteCandidate) return;
    const {task, projectPath} = deleteCandidate;
    const currentTask = taskRef.current;
    if (projectPath !== workspacePathRef.current || !contextRef.current || currentTask?.task_id !== task.task_id) {
      pendingTaskDelete.current = task.task_id;
      setDeleteCandidate(null);
      void activateProject(projectPath, task.task_id);
      return;
    }
    taskRequest('task.delete', {task_id: currentTask.task_id}, currentTask, () => {
      setActiveTask(null);
      setDeleteCandidate(null);
      resetTaskPresentation();
      refreshTasks();
    }, true);
  };
  const removeProject = async () => {
    const project = projectRemoveCandidate;
    if (!project) return;
    try {
      await window.oceanDesktop.forgetProject(project.path);
      setProjects((current) => forgetProject(current, project.path));
      if (workspacePathRef.current === project.path) {
        await window.oceanDesktop.stopBackend();
        workspacePathRef.current = null;
        selectedTaskIdRef.current = null;
        taskRef.current = null;
        workspaceRef.current = null;
        contextRef.current = null;
        pendingTaskRestore.current = null;
        pendingTaskCreate.current = null;
        handlers.current.clear();
        errorHandlers.current.clear();
        setWorkspacePath(null);
        setContext(null);
        setWorkspace(null);
        setTasks([]);
        setActiveTask(null);
        setTaskLoading(false);
        setNewTaskTitle('');
        resetTaskPresentation();
        setInlineStatus(null);
      }
      setProjectRemoveCandidate(null);
    } catch (error) {
      setInlineStatus(error instanceof Error ? error.message : text('Could not remove project.', '无法移除项目。'));
    }
  };

  const reportResultLinks = taskResults.filter((result) => result.kind === 'interactive_view').map((result) => ({
    keys: taskResultKeys(result), label: result.title, summary: result.summary, kind: result.kind,
    onOpen: () => openTaskResult(result),
  }));
  const projectName = projectNameFromPath(workspace?.path ?? workspacePath);
  const activeArchived = activeTask?.status === 'archived';
  const status = context ? activeRequestId ? text('Working', '运行中') : text('Ready', '已就绪') : workspacePath ? text('Connecting', '连接中') : text('No project', '未选择项目');

  return <main className={`ocean-shell density-${density} theme-${resolvedTheme}${activeTask ? '' : ' welcome-mode'}`}>
    <ProjectTaskSidebar
      projects={projects}
      activeProjectPath={workspacePath}
      activeTaskId={activeTask?.task_id ?? null}
      onNewTask={startNewResearch}
      onChooseProject={() => void chooseProject()}
      onOpenProject={(path) => void activateProject(path)}
      onRemoveProject={setProjectRemoveCandidate}
      onOpenTask={(path, taskId) => void activateProject(path, taskId)}
      onDeleteTask={(path, taskId) => {
        const project = projects.find((entry) => entry.path === path);
        const task = activeTask?.task_id === taskId && workspacePath === path
          ? activeTask
          : project?.tasks.find((entry) => entry.task_id === taskId);
        if (task) setDeleteCandidate({task, projectPath: path});
      }}
      onOpenSettings={() => setSettingsOpen(true)}
    />
    <section className="conversation-pane">
      <header className="conversation-toolbar">{activeTask ? <div className="conversation-title"><strong>{activeTask.title}</strong></div> : <span />}{activeTask ? <div className="conversation-actions"><button onClick={() => setDeleteCandidate({task: activeTask, projectPath: workspacePath ?? ''})} title={text('Delete task', '删除任务')}><Trash2 size={16} /></button></div> : <span />}</header>
      <div className={`conversation-scroll${activeTask ? '' : ' welcome-scroll'}`}>{activeTask ? <ConversationTranscript loading={taskLoading} transcript={transcript} streaming={streaming} activeRequestId={activeRequestId} task={activeTask} workspacePath={workspacePath} outputs={outputs} manifests={manifests} taskResults={taskResults} team={team} teamSnapshots={teamSnapshots} pendingInteraction={pendingInteraction} interactionAnswer={interactionAnswer} onInteractionAnswer={setInteractionAnswer} onInteractionSubmit={answer} onOpenResult={openResult} onOpenTaskResult={openTaskResult} onOpenTaskResultFile={openTaskResultFile} /> : <OceanMindWelcome projectName={projectName} hasProject={Boolean(workspacePath)} title={newTaskTitle} onTitle={setNewTaskTitle} onCreateTask={createTask} onChooseProject={() => void chooseProject()} />}</div>
      {activeTask ? <div className="composer-wrap">
        {inlineStatus ? <div className="inline-status"><span>{inlineStatus}</span><button onClick={() => setInlineStatus(null)}><X size={14} /></button></div> : null}
        <div className={`composer-stack ${pendingImport ? 'source-drawer-open' : ''}`}>
          <LocalSourceImportDrawer pending={pendingImport} title={importTitle} authors={importAuthors} year={importYear} doi={importDoi} busy={importing} onTitle={setImportTitle} onAuthors={setImportAuthors} onYear={setImportYear} onDoi={setImportDoi} onCancel={cancelImport} onConfirm={commitImport} />
          {pendingInteraction && pendingInteraction.kind !== 'paper_selection' ? <InteractionDrawer interaction={pendingInteraction} answer={interactionAnswer} onAnswer={setInteractionAnswer} onSubmit={answer} /> : <div className="composer">{prompt.trimStart().toLowerCase().startsWith('/papers') ? <div className="composer-command-menu" aria-label={text('Paper search modes', '论文检索模式')}>
            {([
              ['ask_before_download', '/papers ask', text('Ask before download', '下载前询问')],
              ['auto_download_open_access', '/papers auto', text('Auto-download open access', '自动下载开放全文')],
              ['search_only', '/papers search-only', text('Search only', '仅搜索')],
            ] as Array<[PaperAcquisitionMode, string, string]>).map(([mode, command, label]) => <button key={mode} type="button" onClick={() => setPaperModeFromMenu(mode)}><span><code>{command}</code><strong>{label}</strong></span>{paperAcquisitionMode === mode ? <Check size={15} /> : null}</button>)}
          </div> : null}<textarea value={prompt} disabled={!activeTask || activeArchived || pendingInteraction?.kind === 'paper_selection'} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => {if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {event.preventDefault(); submit();}}} placeholder={pendingInteraction?.kind === 'paper_selection' ? text('Choose papers above to continue', '请在上方选择论文后继续') : activeTask ? text('Ask about this research task', '询问这个研究任务') : text('Select a research task', '选择研究任务')} />
            <div className="composer-actions"><div className="attachment-anchor"><button onClick={() => setAttachmentMenu((value) => !value)} disabled={!activeTask || pendingInteraction?.kind === 'paper_selection'} title={text('Add source', '添加来源')}><Plus size={19} /></button>{attachmentMenu ? <div className="attachment-menu"><button onClick={() => void chooseSource('dataset')}><Database size={16} />{text('Data', '数据')}</button><button onClick={() => void chooseSource('paper')}><FileText size={16} />{text('Paper', '论文')}</button></div> : null}</div>{activeRequestId ? <button className="send-button stop" onClick={cancel}><Square size={15} /></button> : <button className="send-button" onClick={submit} disabled={!prompt.trim() || !activeTask || activeArchived}><SendHorizontal size={17} /></button>}</div>
          </div>}
        </div>
      </div> : null}
    </section>
    {resultSurface?.kind === 'report' ? <ReportWorkbench document={resultSurface.document} loading={resultLoading} markdown={reportMarkdown} resources={reportResources} resultLinks={reportResultLinks} error={resultError} onOpenResource={openReportResource} onClose={closeResult} /> : <ResultWorkbench document={resultSurface?.kind === 'interactive_view' ? resultSurface.document : null} loading={resultLoading} data={resultData} previewUrl={resultPreviewUrl} downloadUrl={resultFileUrl} error={resultError} onClose={closeResult} />}
    <DesktopSettingsDialog open={settingsOpen} status={status} projectName={projectName} runtime={runtime} modelProvider={modelProvider} modelProviderSaving={modelSaving} displayDensity={density} onDisplayDensity={setDensity} appearanceTheme={appearanceTheme} onAppearanceTheme={setAppearanceTheme} onConfigureModelProvider={configureModel} update={update} onCheckForUpdate={() => void window.oceanDesktop.checkForUpdate().then(setUpdate)} onInstallUpdate={() => void window.oceanDesktop.installPreparedUpdate().then(setUpdate)} onClose={() => setSettingsOpen(false)} />
    <TaskDeleteDialog task={deleteCandidate?.task ?? null} onCancel={() => setDeleteCandidate(null)} onConfirm={deleteTask} />
    <ProjectRemoveDialog project={projectRemoveCandidate} onCancel={() => setProjectRemoveCandidate(null)} onConfirm={() => void removeProject()} />
  </main>;
}
