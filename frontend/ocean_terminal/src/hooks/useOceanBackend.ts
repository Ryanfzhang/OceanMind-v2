import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import readline from 'node:readline';
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';

import type {
	ArtifactProjection,
	ArtifactRef,
	DisclosurePolicySummary,
	OceanEvent,
	OceanRequest,
	PaperCitation,
	SystemReadyEvent,
	TaskSnapshotPayload,
	TaskSummaryPayload,
	WorkspaceSnapshotPayload,
} from '../../../packages/ocean-client/src/generated/protocol-v2.js';
import {TransportSequenceTracker} from '../../../packages/ocean-client/src/transport.js';
import type {OceanTerminalConfig} from '../types.js';
import {expectedWorkspaceOpenRevision} from '../../../packages/ocean-client/src/workspace-revision.js';

const PROTOCOL_PREFIX = 'OHJSON:';

type Session = {
	clientId: string;
	sessionId: string;
	capabilities: string[];
};

type Workspace = WorkspaceSnapshotPayload;
type ReviewState = 'pending' | 'approved' | 'changes_requested' | 'rejected';
export type OisstSubsetSelection = {
	year: number;
	west: number;
	east: number;
	south: number;
	north: number;
};
export type OisstSubsetProposal = {
	provider: {
		provider_id: string;
		transport: string;
		canonical_provider_id: string;
	};
	dataset: {
		dataset_id: string;
		canonical_doi: string;
		variable: string;
		units: string;
		grid_resolution_degrees: number;
	};
	selection: OisstSubsetSelection & {longitude_convention: string};
	query_urls: string[];
	request_count: number;
	estimated_grid_cells: number;
	estimated_uncompressed_bytes: number;
	maximum_download_bytes: number;
	materialization_level: 'cached_subset';
	target_storage: string;
	proposal_sha256: string;
	license_snapshot: {source: string; use_constraint: string; captured_for_contract: string};
	citation: {dataset: string; doi: string; access_transport: string};
};
type ArtifactVersion = {
	ref: ArtifactRef;
	artifact_type: string;
	title: string;
	summary: string;
	content: Record<string, unknown>;
	provenance: Record<string, unknown>;
	files: Array<{uri: string; mime_type: string; size_bytes: number; sha256: string}>;
};
type ReviewRecord = {
	review_id: string;
	state: ReviewState;
	comment: string;
};
type VerificationRecord = {
	verification_id: string;
	state: string;
	origin: string;
	independence: string;
};

export type ArtifactInspector = {
	artifact: ArtifactVersion;
	projection: ArtifactProjection;
	links: {
		incoming: Record<string, unknown>[];
		outgoing: Record<string, unknown>[];
	};
	reviews: ReviewRecord[];
	verifications: VerificationRecord[];
};

export type VersionInspector = {
	artifactId: string;
	versions: Array<{
		ref: ArtifactRef;
		artifact_type: string;
		title: string;
		summary: string;
		projection: ArtifactProjection;
	}>;
};

export type RunInspector = {
	run: {
		run_id: string;
		state: string;
		selected_attempt_id?: string | null;
		inputs: Array<{label: string; materialization_level: string}>;
	};
	attempts: Array<{
		attempt: {
			attempt_id: string;
			number: number;
			state: string;
			execution_trust: string;
			checks: Array<{
				check_id: string;
				state: string;
				origin: string;
				independence: string;
				evidence_level: string;
			}>;
		};
		code: Array<{
			name: string;
			sha256: string;
			integrity: string;
			content: string | null;
		}>;
	}>;
};

export type ReportPreview = {
	items: Array<{
		ref: ArtifactRef;
		artifact_type: string;
		title: string;
		projection: ArtifactProjection;
		conclusion_eligible: boolean;
		limitation: string | null;
		grounding: 'not_applicable' | 'grounded' | 'incomplete';
	}>;
	conclusion_export_allowed: boolean;
	fully_reproducible: boolean;
};

export type MultiAgentRole = 'literature_scout' | 'data_analyst' | 'figure_reviewer' | 'research_writer';
export type MultiAgentStatus = {
	feature: {
		enabled: boolean;
		max_workers: number;
	};
	tasks: Array<{
		task_id: string;
		role: MultiAgentRole;
		state: string;
		base_workspace_revision: number;
		error: string | null;
	}>;
	proposals: Array<{
		proposal_id: string;
		task_id: string;
		role: MultiAgentRole;
		state: string;
		proposal_kind: 'research_artifact' | 'recommendation';
		title: string;
		summary: string;
		base_workspace_revision: number;
		accepted_artifact_ref: ArtifactRef | null;
		rejection_reason: string | null;
	}>;
};

export type TranscriptEntry = {
	itemId: string;
	role: 'user' | 'assistant' | 'tool' | 'system';
	text: string;
	turnId?: string | null;
	toolCallId?: string | null;
	interrupted?: boolean;
};

export type ResearchTask = TaskSummaryPayload;

export type ActiveToolCall = {
	toolCallId: string;
	toolName: string;
	turnId: string;
};

const requestId = (prefix: string): string => `req_${prefix}_${crypto.randomUUID().replaceAll('-', '')}`;

export function useOceanBackend(config: OceanTerminalConfig) {
	const [session, setSession] = useState<Session | null>(null);
	const [workspace, setWorkspace] = useState<Workspace | null>(null);
	const [disclosurePolicy, setDisclosurePolicy] = useState<DisclosurePolicySummary | null>(null);
	const [inspector, setInspector] = useState<ArtifactInspector | null>(null);
	const [versionInspector, setVersionInspector] = useState<VersionInspector | null>(null);
	const [runInspector, setRunInspector] = useState<RunInspector | null>(null);
	const [reportPreview, setReportPreview] = useState<ReportPreview | null>(null);
	const [multiAgentStatus, setMultiAgentStatus] = useState<MultiAgentStatus | null>(null);
	const [busyRequests, setBusyRequests] = useState<Set<string>>(() => new Set());
	const [diagnostics, setDiagnostics] = useState<string[]>([]);
	const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
	const [tasks, setTasks] = useState<ResearchTask[]>([]);
	const [activeTask, setActiveTask] = useState<ResearchTask | null>(null);
	const [streamingAssistant, setStreamingAssistant] = useState('');
	const [activeAgentRequestId, setActiveAgentRequestId] = useState<string | null>(null);
	const [activeToolCalls, setActiveToolCalls] = useState<ActiveToolCall[]>([]);
	const [plotStudioReady, setPlotStudioReady] = useState(false);
	const [shutdownComplete, setShutdownComplete] = useState(false);
	const childRef = useRef<ChildProcessWithoutNullStreams | null>(null);
	const sessionRef = useRef<Session | null>(null);
	const workspaceRef = useRef<Workspace | null>(null);
	const activeTaskRef = useRef<ResearchTask | null>(null);
	const activeAgentRequestIdRef = useRef<string | null>(null);
	const sequenceTrackerRef = useRef(new TransportSequenceTracker());
	const resyncPendingRef = useRef(false);
	const pendingRequestHandlers = useRef(new Map<string, (result: Record<string, unknown>) => void>());

	useEffect(() => {
		workspaceRef.current = workspace;
	}, [workspace]);

	useEffect(() => {
		activeTaskRef.current = activeTask;
	}, [activeTask]);

	const activateAgentRequest = useCallback((requestId: string): void => {
		activeAgentRequestIdRef.current = requestId;
		setActiveAgentRequestId(requestId);
	}, []);

	const clearActiveAgentRequest = useCallback((requestId: string): void => {
		if (activeAgentRequestIdRef.current !== requestId) {
			return;
		}
		activeAgentRequestIdRef.current = null;
		setActiveAgentRequestId(null);
		setStreamingAssistant('');
		setActiveToolCalls([]);
	}, []);

	const appendTranscript = useCallback((entry: TranscriptEntry): void => {
		setTranscript((items) => {
			if (items.some((item) => item.itemId === entry.itemId)) {
				return items;
			}
			return [...items.slice(-79), entry];
		});
	}, []);

	const send = useCallback((request: OceanRequest): void => {
		const child = childRef.current;
		if (!child || child.stdin.destroyed) {
			setDiagnostics((items) => [...items.slice(-7), 'Backend connection is unavailable.']);
			return;
		}
		child.stdin.write(`${JSON.stringify(request)}\n`);
	}, []);

	const context = useCallback(() => {
		const current = sessionRef.current;
		if (!current) {
			return null;
		}
		return {
			client_id: current.clientId,
			session_id: current.sessionId,
			workspace_id: config.workspaceId,
		};
	}, [config.workspaceId]);

	const taskContext = useCallback(() => {
		const requestContext = context();
		const task = activeTaskRef.current;
		return requestContext && task ? {...requestContext, task_id: task.task_id} : null;
	}, [context]);

	const requestTaskList = useCallback((): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('task_list');
		pendingRequestHandlers.current.set(id, (result) => {
			const next = Array.isArray(result.tasks) ? result.tasks as ResearchTask[] : [];
			setTasks(next);
			setActiveTask((current) => current
				? next.find((task) => task.task_id === current.task_id) ?? current
				: current);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'task.list',
			payload: {include_archived: false, limit: 100},
			context: requestContext,
		});
	}, [context, send]);

	const openTask = useCallback((taskId: string): void => {
		const requestContext = context();
		if (!requestContext || !taskId) {
			return;
		}
		send({
			protocol_version: 2,
			request_id: requestId('task_open'),
			type: 'task.open',
			payload: {task_id: taskId},
			context: requestContext,
		});
	}, [context, send]);

	const createTask = useCallback((title: string): void => {
		const requestContext = context();
		const normalized = title.trim();
		if (!requestContext || !normalized) {
			return;
		}
		const id = requestId('task_create');
		pendingRequestHandlers.current.set(id, (result) => {
			const task = result.task as ResearchTask | undefined;
			if (task?.task_id) {
				setTasks((items) => [task, ...items.filter((item) => item.task_id !== task.task_id)]);
				openTask(task.task_id);
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'task.create',
			payload: {title: normalized},
			context: requestContext,
		});
	}, [context, openTask, send]);

	const getDisclosurePolicy = useCallback((): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('disclosure_policy_get');
		pendingRequestHandlers.current.set(id, (result) => {
			setDisclosurePolicy((result.policy as DisclosurePolicySummary | null | undefined) ?? null);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'disclosure.policy.get',
			payload: {},
			context: requestContext,
		});
	}, [context, send]);

	const confirmDisclosurePolicy = useCallback((providerId: string): void => {
		const requestContext = context();
		const provider = providerId.trim();
		if (!requestContext || !provider) {
			return;
		}
		const id = requestId('disclosure_policy_set');
		pendingRequestHandlers.current.set(id, (result) => {
			setDisclosurePolicy((result.policy as DisclosurePolicySummary | null | undefined) ?? null);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'disclosure.policy.set',
			payload: {
				provider_id: provider,
				metadata: 'allow',
				aggregate_statistics: 'allow',
				raw_bounded_sample: 'deny',
				document_text: 'deny',
				diagnostic_excerpt: 'deny',
				confirmed: true,
			},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, send]);

	const openWorkspace = useCallback((path: string): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const expectedRevision = expectedWorkspaceOpenRevision(
			workspaceRef.current,
			config.workspaceId,
		);
		const id = requestId('workspace_open');
		pendingRequestHandlers.current.set(id, (result) => {
			const snapshot = result as unknown as Workspace;
			if (typeof snapshot.workspace_id === 'string' && typeof snapshot.revision === 'number') {
				setWorkspace(snapshot);
				setDisclosurePolicy(snapshot.disclosure_policy ?? null);
				setTranscript([]);
				setTasks([]);
				setActiveTask(null);
				setStreamingAssistant('');
				setActiveToolCalls([]);
				setReportPreview(null);
				setMultiAgentStatus(null);
				activeAgentRequestIdRef.current = null;
				setActiveAgentRequestId(null);
				requestTaskList();
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'workspace.open',
			payload: {path},
			context: requestContext,
			...(expectedRevision === undefined
				? {}
				: {expected_workspace_revision: expectedRevision}),
		});
	}, [config.workspaceId, context, requestTaskList, send]);

	const requestSnapshot = useCallback((): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		send({
			protocol_version: 2,
			request_id: requestId('workspace_snapshot'),
			type: 'workspace.snapshot.get',
			payload: {},
			context: requestContext,
		});
	}, [context, send]);

	const openPlotStudio = useCallback((): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		send({
			protocol_version: 2,
			request_id: requestId('plot_studio'),
			type: 'plot_studio.open',
			payload: {open_browser: true},
			context: requestContext,
		});
	}, [context, send]);

	const inspectArtifact = useCallback((ref: ArtifactRef): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('artifact_get');
		setVersionInspector(null);
		setRunInspector(null);
		setReportPreview(null);
		pendingRequestHandlers.current.set(id, (result) => {
			const artifact = result.artifact as ArtifactVersion | undefined;
		const projection = result.projection as ArtifactProjection | undefined;
		const links = result.links as ArtifactInspector['links'] | undefined;
		const reviews = result.reviews as ReviewRecord[] | undefined;
		const verifications = Array.isArray(result.verifications)
			? result.verifications.filter(isVerificationRecord)
			: [];
		if (!artifact || !projection || !links || !reviews) {
				setDiagnostics((items) => [...items.slice(-7), 'Artifact inspector received an incomplete result.']);
				return;
			}
		setInspector({artifact, projection, links, reviews, verifications});
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'artifact.get',
			payload: {ref},
			context: requestContext,
		});
		}, [context, send]);

	const importDataset = useCallback((relativePath: string): void => {
		const requestContext = context();
		if (!requestContext || !relativePath) {
			return;
		}
		const id = requestId('dataset_import');
		pendingRequestHandlers.current.set(id, (result) => {
			const artifact = result.artifact as {ref?: ArtifactRef} | undefined;
			requestSnapshot();
			if (artifact?.ref) {
				inspectArtifact(artifact.ref);
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'dataset.import',
			payload: {relative_path: relativePath, materialization_acknowledged: true},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, inspectArtifact, requestSnapshot, send]);

	const importPaper = useCallback((relativePath: string, citation: PaperCitation): void => {
		const requestContext = context();
		const title = citation.title.trim();
		if (!requestContext || !relativePath || !title) {
			return;
		}
		const id = requestId('paper_import');
		pendingRequestHandlers.current.set(id, (result) => {
			const artifact = result.artifact as {ref?: ArtifactRef} | undefined;
			requestSnapshot();
			if (artifact?.ref) {
				inspectArtifact(artifact.ref);
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'paper.import',
			payload: {
				relative_path: relativePath,
				citation: {...citation, title},
				materialization_acknowledged: true,
			},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, inspectArtifact, requestSnapshot, send]);

	const registerPaper = useCallback((citation: PaperCitation): void => {
		const requestContext = context();
		const title = citation.title.trim();
		if (!requestContext || !title) {
			return;
		}
		const id = requestId('paper_register');
		pendingRequestHandlers.current.set(id, (result) => {
			const artifact = result.artifact as {ref?: ArtifactRef} | undefined;
			requestSnapshot();
			if (artifact?.ref) {
				inspectArtifact(artifact.ref);
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'paper.register',
			payload: {citation: {...citation, title}},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, inspectArtifact, requestSnapshot, send]);

	const activateHypothesis = useCallback((hypothesisRef: ArtifactRef): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('hypothesis_activate');
		pendingRequestHandlers.current.set(id, () => {
			requestSnapshot();
			inspectArtifact(hypothesisRef);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'hypothesis.activate',
			payload: {hypothesis_ref: hypothesisRef},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, inspectArtifact, requestSnapshot, send]);

	const previewOisstSubset = useCallback((
		selection: OisstSubsetSelection,
		onPreview: (proposal: OisstSubsetProposal) => void,
	): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('oisst_preview');
		pendingRequestHandlers.current.set(id, (result) => {
			const proposal = result.proposal;
			if (!isOisstSubsetProposal(proposal)) {
				setDiagnostics((items) => [...items.slice(-7), 'OISST preview received an incomplete source contract.']);
				return;
			}
			onPreview(proposal);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'dataset.remote.oisst.preview',
			payload: selection,
			context: requestContext,
		});
	}, [context, send]);

	const fetchOisstSubset = useCallback((proposal: OisstSubsetProposal): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('oisst_fetch');
		pendingRequestHandlers.current.set(id, (result) => {
			const artifact = result.artifact as {ref?: ArtifactRef} | undefined;
			requestSnapshot();
			if (artifact?.ref) {
				inspectArtifact(artifact.ref);
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'dataset.remote.oisst.fetch',
			payload: {
				year: proposal.selection.year,
				west: proposal.selection.west,
				east: proposal.selection.east,
				south: proposal.selection.south,
				north: proposal.selection.north,
				proposal_sha256: proposal.proposal_sha256,
				download_acknowledged: true,
			},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, inspectArtifact, requestSnapshot, send]);

	const createFigureRequest = useCallback((datasetRef: ArtifactRef, userGoal: string): void => {
		const requestContext = context();
		if (!requestContext || !userGoal.trim()) {
			return;
		}
		const id = requestId('figure_request');
		pendingRequestHandlers.current.set(id, (result) => {
			const artifact = result.artifact as {ref?: ArtifactRef} | undefined;
			requestSnapshot();
			if (artifact?.ref) {
				inspectArtifact(artifact.ref);
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'figure.request.create',
			payload: {
				user_goal: userGoal.trim(),
				dataset_refs: [datasetRef],
				requested_outputs: ['interactive', 'png'],
			},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, inspectArtifact, requestSnapshot, send]);

	const previewReport = useCallback((artifactRefs: ArtifactRef[]): void => {
		const requestContext = context();
		if (!requestContext || artifactRefs.length === 0) {
			return;
		}
		const id = requestId('report_preview');
		pendingRequestHandlers.current.set(id, (result) => {
			if (!isReportPreview(result.preview)) {
				setDiagnostics((items) => [...items.slice(-7), 'Report preview received an incomplete evidence result.']);
				return;
			}
			setInspector(null);
			setVersionInspector(null);
			setRunInspector(null);
			setReportPreview(result.preview);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'report.preview',
			payload: {artifact_refs: artifactRefs},
			context: requestContext,
		});
	}, [context, send]);

	const createReport = useCallback((title: string, artifactRefs: ArtifactRef[]): void => {
		const requestContext = context();
		const normalizedTitle = title.trim();
		if (!requestContext || !normalizedTitle || artifactRefs.length === 0) {
			return;
		}
		const id = requestId('report_create');
		pendingRequestHandlers.current.set(id, (result) => {
			const artifact = result.artifact as {ref?: ArtifactRef} | undefined;
			requestSnapshot();
			if (artifact?.ref) {
				inspectArtifact(artifact.ref);
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'report.create',
			payload: {title: normalizedTitle, artifact_refs: artifactRefs},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, inspectArtifact, requestSnapshot, send]);

	const getMultiAgentStatus = useCallback((): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('multi_agent_status');
		pendingRequestHandlers.current.set(id, (result) => {
			if (!isMultiAgentStatus(result)) {
				setDiagnostics((items) => [...items.slice(-7), 'Multi-agent status received an incomplete result.']);
				return;
			}
			setMultiAgentStatus(result);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'multi_agent.status.get',
			payload: {},
			context: requestContext,
		});
	}, [context, send]);

	useEffect(() => {
		const hasActiveWorkers = multiAgentStatus?.tasks.some(
			(task) => task.state === 'queued' || task.state === 'running',
		);
		if (!hasActiveWorkers) {
			return;
		}
		const timer = setTimeout(getMultiAgentStatus, 500);
		return () => clearTimeout(timer);
	}, [getMultiAgentStatus, multiAgentStatus]);

	const startMultiAgent = useCallback((prompt: string, roles: MultiAgentRole[]): void => {
		const requestContext = context();
		const normalizedPrompt = prompt.trim();
		if (!requestContext || !normalizedPrompt || roles.length === 0) {
			return;
		}
		const id = requestId('multi_agent_start');
		pendingRequestHandlers.current.set(id, (result) => {
			const feature = result.feature !== null
				&& typeof result.feature === 'object'
				&& !Array.isArray(result.feature)
				? result.feature as Record<string, unknown>
				: {};
			const tasks = Array.isArray(result.tasks) ? result.tasks : [];
			if (typeof feature.enabled !== 'boolean' || !Array.isArray(tasks)) {
				setDiagnostics((items) => [...items.slice(-7), 'Multi-agent start received an incomplete result.']);
				return;
			}
			setMultiAgentStatus({
				feature: {
					enabled: feature.enabled,
					max_workers: typeof feature.max_workers === 'number' ? feature.max_workers : 0,
				},
				tasks: tasks as MultiAgentStatus['tasks'],
				proposals: [],
			});
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'multi_agent.start',
			payload: {prompt: normalizedPrompt, roles},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, getMultiAgentStatus, send]);

	const cancelMultiAgentTask = useCallback((taskId: string): void => {
		const requestContext = context();
		if (!requestContext || !taskId) {
			return;
		}
		const id = requestId('multi_agent_cancel');
		pendingRequestHandlers.current.set(id, () => getMultiAgentStatus());
		send({
			protocol_version: 2,
			request_id: id,
			type: 'multi_agent.task.cancel',
			payload: {task_id: taskId},
			context: requestContext,
		});
	}, [context, getMultiAgentStatus, send]);

	const acceptMultiAgentProposal = useCallback((proposalId: string): void => {
		const requestContext = context();
		if (!requestContext || !proposalId) {
			return;
		}
		const id = requestId('multi_agent_accept');
		pendingRequestHandlers.current.set(id, (result) => {
			const artifact = result.artifact as {ref?: ArtifactRef} | undefined;
			requestSnapshot();
			getMultiAgentStatus();
			if (artifact?.ref) {
				inspectArtifact(artifact.ref);
			}
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'multi_agent.proposal.accept',
			payload: {proposal_id: proposalId},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, getMultiAgentStatus, inspectArtifact, requestSnapshot, send]);

	const rejectMultiAgentProposal = useCallback((proposalId: string, reason: string): void => {
		const requestContext = context();
		const normalizedReason = reason.trim();
		if (!requestContext || !proposalId || !normalizedReason) {
			return;
		}
		const id = requestId('multi_agent_reject');
		pendingRequestHandlers.current.set(id, () => getMultiAgentStatus());
		send({
			protocol_version: 2,
			request_id: id,
			type: 'multi_agent.proposal.reject',
			payload: {proposal_id: proposalId, reason: normalizedReason},
			context: requestContext,
		});
	}, [context, getMultiAgentStatus, send]);

	const inspectVersions = useCallback((artifactId: string): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('artifact_versions');
		setInspector(null);
		setRunInspector(null);
		setReportPreview(null);
		pendingRequestHandlers.current.set(id, (result) => {
			const versions = result.versions;
			if (!Array.isArray(versions)) {
				setDiagnostics((items) => [...items.slice(-7), 'Artifact versions received an incomplete result.']);
				return;
			}
			setVersionInspector({artifactId, versions: versions as VersionInspector['versions']});
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'artifact.versions.get',
			payload: {artifact_id: artifactId},
			context: requestContext,
		});
	}, [context, send]);

	const inspectRun = useCallback((runId: string): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('analysis_run_get');
		setInspector(null);
		setVersionInspector(null);
		setReportPreview(null);
		pendingRequestHandlers.current.set(id, (result) => {
			if (!result.run || !Array.isArray(result.attempts)) {
				setDiagnostics((items) => [...items.slice(-7), 'Analysis run inspector received an incomplete result.']);
				return;
			}
			setRunInspector(result as unknown as RunInspector);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'analysis.run.get',
			payload: {run_id: runId},
			context: requestContext,
		});
	}, [context, send]);

	const submitReview = useCallback((ref: ArtifactRef, state: ReviewState, comment = ''): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		const id = requestId('review_submit');
		pendingRequestHandlers.current.set(id, () => {
			requestSnapshot();
			inspectArtifact(ref);
		});
		send({
			protocol_version: 2,
			request_id: id,
			type: 'review.submit',
			payload: {artifact_ref: ref, state, comment},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, inspectArtifact, requestSnapshot, send]);

	const startAnalysisAttempt = useCallback((runId: string): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		send({
			protocol_version: 2,
			request_id: requestId('analysis_start'),
			type: 'analysis.attempt.start',
			payload: {run_id: runId},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, send]);

	const cancelAnalysisAttempt = useCallback((runId: string): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		send({
			protocol_version: 2,
			request_id: requestId('analysis_cancel'),
			type: 'analysis.attempt.cancel',
			payload: {run_id: runId},
			context: requestContext,
		});
	}, [context, send]);

	const abandonAnalysisRun = useCallback((runId: string): void => {
		const requestContext = context();
		if (!requestContext) {
			return;
		}
		send({
			protocol_version: 2,
			request_id: requestId('analysis_abandon'),
			type: 'analysis.run.abandon',
			payload: {run_id: runId},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
		});
	}, [context, send]);

	const submitAgent = useCallback((text: string): void => {
		const requestContext = taskContext();
		const task = activeTaskRef.current;
		const prompt = text.trim();
		if (!requestContext || !task || !prompt) {
			setDiagnostics((items) => [...items.slice(-7), 'Create or open a research task before submitting.']);
			return;
		}
		if (activeAgentRequestIdRef.current) {
			setDiagnostics((items) => [...items.slice(-7), 'Wait for the active Ocean request or cancel it first.']);
			return;
		}
		const id = requestId('session_submit');
		activateAgentRequest(id);
		setStreamingAssistant('');
		setActiveToolCalls([]);
		send({
			protocol_version: 2,
			request_id: id,
			type: 'session.submit',
			payload: {text: prompt},
			context: requestContext,
			expected_workspace_revision: workspaceRef.current?.revision ?? 0,
			expected_task_revision: task.task_revision,
		});
	}, [activateAgentRequest, taskContext, send]);

	const cancelActiveAgent = useCallback((): void => {
		const requestContext = taskContext() ?? context();
		const targetRequestId = activeAgentRequestIdRef.current;
		if (!requestContext || !targetRequestId) {
			return;
		}
		send({
			protocol_version: 2,
			request_id: requestId('session_cancel'),
			type: 'request.cancel',
			payload: {
				target_request_id: targetRequestId,
				reason: 'Cancelled from Ocean terminal',
			},
			context: requestContext,
		});
	}, [context, send, taskContext]);

	const shutdown = useCallback((): void => {
		const requestContext = context();
		if (!requestContext) {
			setShutdownComplete(true);
			return;
		}
		send({
			protocol_version: 2,
			request_id: requestId('shutdown'),
			type: 'system.shutdown',
			payload: {reason: 'Ocean terminal closed'},
			context: requestContext,
		});
	}, [context, send]);

	useEffect(() => {
		const [command, ...arguments_] = config.backendCommand;
		if (!command) {
			setDiagnostics(['Ocean backend command is missing.']);
			return;
		}
		const child = spawn(command, arguments_, {
			stdio: ['pipe', 'pipe', 'pipe'],
			windowsHide: true,
		});
		childRef.current = child;
		const stdout = readline.createInterface({input: child.stdout});
		const stderr = readline.createInterface({input: child.stderr});
		const handleEvent = (event: OceanEvent): void => {
			if (sequenceTrackerRef.current.observe(event) && !resyncPendingRef.current) {
				resyncPendingRef.current = true;
				requestSnapshot();
			}
			switch (event.type) {
				case 'system.ready': {
					const ready = event as SystemReadyEvent;
					const nextSession = {
						clientId: ready.payload.client_id,
						sessionId: ready.payload.session_id,
						capabilities: ready.payload.capabilities,
					};
					sessionRef.current = nextSession;
					setSession(nextSession);
					send({
						protocol_version: 2,
						request_id: requestId('session_open'),
						type: 'session.open',
						payload: {requested_workspace_id: config.workspaceId},
						context: {
							client_id: nextSession.clientId,
							session_id: nextSession.sessionId,
							workspace_id: config.workspaceId,
						},
					});
					openWorkspace(config.workspacePath);
					return;
				}
				case 'request.accepted':
					if (event.request_id) {
						setBusyRequests((requests) => new Set([...requests, event.request_id!]));
						if (event.payload.request_type === 'session.submit') {
							activateAgentRequest(event.request_id);
						}
					}
					return;
				case 'request.completed':
					const completedTask = event.request_id === activeAgentRequestIdRef.current
						? activeTaskRef.current
						: null;
					if (event.request_id) {
						const handler = pendingRequestHandlers.current.get(event.request_id);
						if (handler) {
							pendingRequestHandlers.current.delete(event.request_id);
							handler(event.payload.result);
						}
					}
					if (event.request_id) {
						setBusyRequests((requests) => {
							const next = new Set(requests);
							next.delete(event.request_id!);
							return next;
						});
						clearActiveAgentRequest(event.request_id);
						if (completedTask) {
							openTask(completedTask.task_id);
						}
					}
					return;
				case 'request.failed':
					if (event.request_id) {
						pendingRequestHandlers.current.delete(event.request_id);
						setBusyRequests((requests) => {
							const next = new Set(requests);
							next.delete(event.request_id!);
							return next;
						});
						clearActiveAgentRequest(event.request_id);
					}
					setDiagnostics((items) => [...items.slice(-7), event.payload.error.message]);
					return;
				case 'request.cancelled':
					if (event.request_id) {
						pendingRequestHandlers.current.delete(event.request_id);
						setBusyRequests((requests) => {
							const next = new Set(requests);
							next.delete(event.request_id!);
							return next;
						});
						clearActiveAgentRequest(event.request_id);
					}
					setDiagnostics((items) => [...items.slice(-7), event.payload.reason ?? 'Request cancelled.']);
					return;
				case 'transcript.item.appended':
					appendTranscript({
						itemId: event.payload.item_id,
						role: event.payload.role,
						text: event.payload.text,
						turnId: event.payload.turn_id,
						toolCallId: event.payload.tool_call_id,
					});
					return;
				case 'task.snapshot': {
					const snapshot = event.payload as TaskSnapshotPayload;
					setActiveTask(snapshot.task);
					setTasks((items) => [
						snapshot.task,
						...items.filter((task) => task.task_id !== snapshot.task.task_id),
					]);
					setTranscript((snapshot.transcript ?? []).map((item) => ({
						itemId: item.item_id,
						role: item.role,
						text: item.text,
						turnId: item.turn_id,
						toolCallId: item.tool_call_id,
						interrupted: item.interrupted,
					})));
					setStreamingAssistant('');
					setActiveToolCalls([]);
					return;
				}
				case 'assistant.delta':
					if (event.request_id === activeAgentRequestIdRef.current) {
						setStreamingAssistant((current) => `${current}${event.payload.text}`.slice(-16_000));
					}
					return;
				case 'assistant.turn.completed':
					if (event.request_id === activeAgentRequestIdRef.current) {
						setStreamingAssistant('');
					}
					return;
				case 'tool.call.started':
					if (event.request_id === activeAgentRequestIdRef.current) {
						setActiveToolCalls((calls) => [
							...calls.filter((call) => call.toolCallId !== event.payload.tool_call_id),
							{
								toolCallId: event.payload.tool_call_id,
								toolName: event.payload.tool_name,
								turnId: event.payload.turn_id,
							},
						]);
					}
					return;
				case 'tool.call.completed':
					if (event.request_id === activeAgentRequestIdRef.current) {
						setActiveToolCalls((calls) => calls.filter((call) => call.toolCallId !== event.payload.tool_call_id));
						requestSnapshot();
					}
					return;
				case 'context.compaction.progress':
					if (event.payload.message) {
						setDiagnostics((items) => [...items.slice(-7), event.payload.message!]);
					}
					return;
				case 'workspace.snapshot':
					resyncPendingRef.current = false;
					setWorkspace(event.payload);
					setDisclosurePolicy(event.payload.disclosure_policy ?? null);
					return;
				case 'workspace.changed':
					setWorkspace((current) => current
						? {...current, revision: event.payload.workspace_revision}
						: {
							workspace_id: event.workspace_id ?? config.workspaceId,
							path: config.workspacePath,
							revision: event.payload.workspace_revision,
							artifacts: [],
						});
					return;
				case 'disclosure.policy.updated':
					setDisclosurePolicy(event.payload.policy);
					setWorkspace((current) => current
						? {...current, revision: event.payload.workspace_revision, disclosure_policy: event.payload.policy}
						: current);
					return;
				case 'artifact.created':
				case 'artifact.version.created':
				case 'review.submitted':
				case 'analysis.run.ready':
				case 'analysis.run.checking':
				case 'analysis.run.checks_passed':
				case 'analysis.run.checks_failed':
				case 'analysis.run.abandoned':
				case 'analysis.attempt.queued':
				case 'analysis.attempt.succeeded':
				case 'analysis.attempt.rejected':
				case 'analysis.attempt.failed':
				case 'analysis.attempt.timed_out':
				case 'analysis.attempt.resource_limited':
				case 'analysis.attempt.cancelled':
				case 'analysis.attempt.interrupted':
				case 'analysis.attempt.source_changed':
					requestSnapshot();
					return;
				case 'plot_studio.ready':
					setPlotStudioReady(true);
					openLocalBrowser(event.payload.url);
					return;
				case 'system.error':
					setDiagnostics((items) => [...items.slice(-7), event.payload.error.message]);
					return;
				case 'system.shutdown':
					setShutdownComplete(true);
					return;
				default:
					return;
			}
		};
		stdout.on('line', (line) => {
			if (!line.startsWith(PROTOCOL_PREFIX)) {
				setDiagnostics((items) => [...items.slice(-7), `Ignored backend stdout: ${line}`]);
				return;
			}
			try {
				handleEvent(JSON.parse(line.slice(PROTOCOL_PREFIX.length)) as OceanEvent);
			} catch {
				setDiagnostics((items) => [...items.slice(-7), 'Received malformed backend protocol frame.']);
			}
		});
		stderr.on('line', (line) => {
			setDiagnostics((items) => [...items.slice(-7), `Backend: ${line}`]);
		});
		child.on('error', (error) => setDiagnostics((items) => [...items.slice(-7), error.message]));
		child.on('exit', (code) => {
			setDiagnostics((items) => [...items.slice(-7), `Backend exited (${code ?? 0}).`]);
			setShutdownComplete(true);
		});
		child.stdin.write(`${JSON.stringify({
			protocol_version: 2,
			request_id: requestId('handshake'),
			type: 'system.handshake',
			payload: {
				client_kind: 'tui',
				client_version: '0.1.0',
				supported_protocol_versions: [2],
			},
		})}\n`);
		return () => {
			stdout.close();
			stderr.close();
			if (!child.killed) {
				child.kill('SIGTERM');
			}
		};
	}, [
		activateAgentRequest,
		appendTranscript,
		clearActiveAgentRequest,
		config.backendCommand,
		config.workspaceId,
		config.workspacePath,
		openWorkspace,
		openTask,
		requestSnapshot,
		send,
	]);

	return useMemo(
		() => ({
			session,
			workspace,
			disclosurePolicy,
			inspector,
			versionInspector,
			runInspector,
			reportPreview,
			multiAgentStatus,
			busy: busyRequests.size > 0,
			diagnostics,
			transcript,
			tasks,
			activeTask,
			streamingAssistant,
			activeAgentRequestId,
			activeToolCalls,
			plotStudioReady,
			shutdownComplete,
			openWorkspace,
			requestTaskList,
			createTask,
			openTask,
			getDisclosurePolicy,
			confirmDisclosurePolicy,
			requestSnapshot,
			openPlotStudio,
			inspectArtifact,
			importDataset,
			importPaper,
			registerPaper,
			activateHypothesis,
			previewOisstSubset,
			fetchOisstSubset,
			createFigureRequest,
			previewReport,
			createReport,
			getMultiAgentStatus,
			startMultiAgent,
			cancelMultiAgentTask,
			acceptMultiAgentProposal,
			rejectMultiAgentProposal,
			inspectVersions,
			inspectRun,
			submitReview,
			startAnalysisAttempt,
			cancelAnalysisAttempt,
			abandonAnalysisRun,
			submitAgent,
			cancelActiveAgent,
			shutdown,
		}),
		[
			session,
			workspace,
			disclosurePolicy,
			inspector,
			versionInspector,
			runInspector,
			reportPreview,
			multiAgentStatus,
			busyRequests,
			diagnostics,
			transcript,
			tasks,
			activeTask,
			streamingAssistant,
			activeAgentRequestId,
			activeToolCalls,
			plotStudioReady,
			shutdownComplete,
			openWorkspace,
			requestTaskList,
			createTask,
			openTask,
			getDisclosurePolicy,
			confirmDisclosurePolicy,
			requestSnapshot,
			openPlotStudio,
			inspectArtifact,
			importDataset,
			importPaper,
			registerPaper,
			activateHypothesis,
			previewOisstSubset,
			fetchOisstSubset,
			createFigureRequest,
			previewReport,
			createReport,
			getMultiAgentStatus,
			startMultiAgent,
			cancelMultiAgentTask,
			acceptMultiAgentProposal,
			rejectMultiAgentProposal,
			inspectVersions,
			inspectRun,
			submitReview,
			startAnalysisAttempt,
			cancelAnalysisAttempt,
			abandonAnalysisRun,
			submitAgent,
			cancelActiveAgent,
			shutdown,
		],
	);
}

function isMultiAgentStatus(value: unknown): value is MultiAgentStatus {
	if (!value || typeof value !== 'object') {
		return false;
	}
	const status = value as Partial<MultiAgentStatus>;
	return !!status.feature
		&& typeof status.feature.enabled === 'boolean'
		&& Array.isArray(status.tasks)
		&& Array.isArray(status.proposals);
}

function isReportPreview(value: unknown): value is ReportPreview {
	if (!value || typeof value !== 'object') {
		return false;
	}
	const preview = value as Partial<ReportPreview>;
	return Array.isArray(preview.items)
		&& typeof preview.conclusion_export_allowed === 'boolean'
		&& typeof preview.fully_reproducible === 'boolean';
}

function isVerificationRecord(value: unknown): value is VerificationRecord {
	if (!value || typeof value !== 'object' || Array.isArray(value)) {
		return false;
	}
	const record = value as Partial<VerificationRecord>;
	return typeof record.verification_id === 'string'
		&& typeof record.state === 'string'
		&& typeof record.origin === 'string'
		&& typeof record.independence === 'string';
}

function isOisstSubsetProposal(value: unknown): value is OisstSubsetProposal {
	if (!value || typeof value !== 'object') {
		return false;
	}
	const proposal = value as Partial<OisstSubsetProposal>;
	return typeof proposal.proposal_sha256 === 'string'
		&& typeof proposal.estimated_uncompressed_bytes === 'number'
		&& typeof proposal.maximum_download_bytes === 'number'
		&& typeof proposal.target_storage === 'string'
		&& !!proposal.selection
		&& typeof proposal.selection.year === 'number'
		&& typeof proposal.selection.west === 'number'
		&& typeof proposal.selection.east === 'number'
		&& typeof proposal.selection.south === 'number'
		&& typeof proposal.selection.north === 'number'
		&& !!proposal.provider
		&& typeof proposal.provider.provider_id === 'string'
		&& !!proposal.dataset
		&& typeof proposal.dataset.canonical_doi === 'string'
		&& !!proposal.license_snapshot
		&& typeof proposal.license_snapshot.use_constraint === 'string';
}

function openLocalBrowser(url: string): void {
	const [command, arguments_] = process.platform === 'darwin'
		? ['open', [url]]
		: process.platform === 'win32'
			? ['cmd', ['/c', 'start', '', url]]
			: ['xdg-open', [url]];
	const browser = spawn(command, arguments_, {detached: true, stdio: 'ignore'});
	browser.on('error', () => {});
	browser.unref();
}
