import React, {useEffect, useState} from 'react';
import {Box, Text, useApp, useInput} from 'ink';
import TextInput from 'ink-text-input';

import {
	useOceanBackend,
	type ActiveToolCall,
	type MultiAgentRole,
	type MultiAgentStatus,
	type OisstSubsetProposal,
	type OisstSubsetSelection,
	type ReportPreview,
	type RunInspector,
	type TranscriptEntry,
	type VersionInspector,
	} from './hooks/useOceanBackend.js';
import {
	asArtifactRef,
	artifactLinkDetails,
	artifactMetadataDetails,
	formatArtifactRef as formatRef,
	type InspectorDetailTone,
	verificationColor,
} from './inspectorDetails.js';
import type {OceanTerminalConfig} from './types.js';
import type {ArtifactProjection, ArtifactRef, PaperCitation} from '../../packages/ocean-client/src/generated/protocol-v2.js';

type ReviewState = 'pending' | 'approved' | 'changes_requested' | 'rejected';
type ArtifactSummary = {
	ref: ArtifactRef;
	title: string;
	projection: ArtifactProjection;
};
type RunSummary = {
	run_id: string;
	state: string;
	attempt_count: number;
	last_terminal_state?: string | null;
};
type PendingPaperImport = {
	relativePath: string;
	citation: PaperCitation;
};
type ReportCreateCommand = {
	title: string;
	artifactRefs: ArtifactRef[];
};
type MultiAgentStartCommand = {
	prompt: string;
	roles: MultiAgentRole[];
};
type MultiAgentProposalRejection = {
	proposalId: string;
	reason: string;
};

export function App({config}: {config: OceanTerminalConfig}): React.JSX.Element {
	const {exit} = useApp();
	const [input, setInput] = useState('');
	const [exitRequested, setExitRequested] = useState(false);
	const [pendingDatasetImport, setPendingDatasetImport] = useState<string | null>(null);
	const [pendingPaperImport, setPendingPaperImport] = useState<PendingPaperImport | null>(null);
	const [pendingOisstProposal, setPendingOisstProposal] = useState<OisstSubsetProposal | null>(null);
	const [pendingDisclosureProvider, setPendingDisclosureProvider] = useState<string | null>(null);
	const backend = useOceanBackend(config);

	const requestExit = (): void => {
		setExitRequested(true);
		backend.shutdown();
	};

	useEffect(() => {
		if (exitRequested && backend.shutdownComplete) {
			exit();
		}
	}, [backend.shutdownComplete, exit, exitRequested]);

	const submit = (value: string): void => {
		const command = value.trim();
		if (command === 'quit') {
			requestExit();
		} else if (pendingDatasetImport) {
			if (command === 'yes') {
				backend.importDataset(pendingDatasetImport);
				setPendingDatasetImport(null);
			} else if (command === 'no' || command === 'cancel') {
				setPendingDatasetImport(null);
			}
		} else if (pendingPaperImport) {
			if (command === 'yes') {
				backend.importPaper(pendingPaperImport.relativePath, pendingPaperImport.citation);
				setPendingPaperImport(null);
			} else if (command === 'no' || command === 'cancel') {
				setPendingPaperImport(null);
			}
		} else if (pendingOisstProposal) {
			if (command === 'yes') {
				backend.fetchOisstSubset(pendingOisstProposal);
				setPendingOisstProposal(null);
			} else if (command === 'no' || command === 'cancel') {
				setPendingOisstProposal(null);
			}
		} else if (pendingDisclosureProvider) {
			if (command === 'yes') {
				backend.confirmDisclosurePolicy(pendingDisclosureProvider);
				setPendingDisclosureProvider(null);
			} else if (command === 'no' || command === 'cancel') {
				setPendingDisclosureProvider(null);
			}
		} else if (command === 'plot') {
			backend.openPlotStudio();
		} else if (command === 'snapshot') {
			backend.requestSnapshot();
		} else if (command === 'tasks') {
			backend.requestTaskList();
		} else if (command.startsWith('task ')) {
			backend.createTask(command.slice('task '.length));
		} else if (command.startsWith('use ')) {
			const taskId = command.slice('use '.length).trim();
			if (isTaskId(taskId)) {
				backend.openTask(taskId);
			}
		} else if (command.startsWith('open ')) {
			backend.openWorkspace(command.slice('open '.length).trim());
		} else if (command.startsWith('import ')) {
			const relativePath = command.slice('import '.length).trim();
			if (relativePath) {
				setPendingDatasetImport(relativePath);
			}
		} else if (command.startsWith('paper ')) {
			const paper = parsePaperImport(command.slice('paper '.length));
			if (paper) {
				setPendingPaperImport(paper);
			}
		} else if (command.startsWith('citation ')) {
			const title = command.slice('citation '.length).trim();
			if (title) {
				backend.registerPaper({title});
			}
		} else if (command.startsWith('fetch oisst ')) {
			const selection = parseOisstSelection(command.slice('fetch oisst '.length));
			if (selection) {
				backend.previewOisstSubset(selection, setPendingOisstProposal);
			}
		} else if (command.startsWith('request ')) {
			const [refText, ...goalParts] = command.slice('request '.length).trim().split(/\s+/);
			const ref = parseRef(refText ?? '');
			const goal = goalParts.join(' ').trim();
			if (ref && goal) {
				backend.createFigureRequest(ref, goal);
			}
		} else if (command.startsWith('report-preview ')) {
			const refs = parseRefs(command.slice('report-preview '.length));
			if (refs) {
				backend.previewReport(refs);
			}
		} else if (command.startsWith('report ')) {
			const report = parseReportCreate(command.slice('report '.length));
			if (report) {
				backend.createReport(report.title, report.artifactRefs);
			}
		} else if (command === 'agents') {
			backend.getMultiAgentStatus();
		} else if (command.startsWith('multi ')) {
			const multiAgent = parseMultiAgentStart(command.slice('multi '.length));
			if (multiAgent) {
				backend.startMultiAgent(multiAgent.prompt, multiAgent.roles);
			}
		} else if (command.startsWith('cancel-agent ')) {
			const taskId = command.slice('cancel-agent '.length).trim();
			if (isTaskId(taskId)) {
				backend.cancelMultiAgentTask(taskId);
			}
		} else if (command.startsWith('accept-proposal ')) {
			const proposalId = command.slice('accept-proposal '.length).trim();
			if (isProposalId(proposalId)) {
				backend.acceptMultiAgentProposal(proposalId);
			}
		} else if (command.startsWith('reject-proposal ')) {
			const rejection = parseMultiAgentProposalRejection(command.slice('reject-proposal '.length));
			if (rejection) {
				backend.rejectMultiAgentProposal(rejection.proposalId, rejection.reason);
			}
		} else if (command === 'disclosure') {
			backend.getDisclosurePolicy();
		} else if (command.startsWith('disclosure ')) {
			const providerId = command.slice('disclosure '.length).trim();
			if (providerId) {
				setPendingDisclosureProvider(providerId);
			}
		} else if (command.startsWith('inspect ')) {
			const ref = parseRef(command.slice('inspect '.length));
			if (ref) {
				backend.inspectArtifact(ref);
			}
		} else if (command.startsWith('versions ')) {
			const artifactId = command.slice('versions '.length).trim();
			if (/^[a-z][a-z0-9_]{2,127}$/.test(artifactId)) {
				backend.inspectVersions(artifactId);
			}
		} else if (command.startsWith('inspect-run ')) {
			backend.inspectRun(command.slice('inspect-run '.length).trim());
		} else if (command.startsWith('activate ')) {
			const ref = parseRef(command.slice('activate '.length));
			if (ref) {
				backend.activateHypothesis(ref);
			}
		} else if (command.startsWith('review ')) {
			const [, refText, stateText, ...commentParts] = command.split(/\s+/);
			const ref = parseRef(refText ?? '');
			if (ref && isReviewState(stateText)) {
				backend.submitReview(ref, stateText, commentParts.join(' '));
			}
		} else if (command.startsWith('run ')) {
			backend.startAnalysisAttempt(command.slice('run '.length).trim());
		} else if (command.startsWith('cancel-run ')) {
			backend.cancelAnalysisAttempt(command.slice('cancel-run '.length).trim());
		} else if (command.startsWith('abandon ')) {
			backend.abandonAnalysisRun(command.slice('abandon '.length).trim());
		} else if (command === 'cancel') {
			backend.cancelActiveAgent();
		} else if (command) {
			backend.submitAgent(command);
		}
		setInput('');
	};

	useInput((character, key) => {
		if (key.ctrl && character === 'c') {
			requestExit();
		}
	});

	return (
		<Box flexDirection="column" paddingX={1}>
			<Box borderStyle="single" borderColor="cyan" paddingX={1}>
				<Text bold color="cyan">Ocean Research Partner</Text>
				<Text> </Text>
				<Text color={backend.session ? 'green' : 'yellow'}>
					{backend.session ? 'connected' : 'connecting'}
				</Text>
			</Box>
			<Box marginTop={1} flexDirection="row" gap={1}>
				<Box borderStyle="single" borderColor="gray" flexDirection="column" paddingX={1} width={42}>
					<Text bold>Workspace {backend.workspace?.workspace_id ?? config.workspaceId}</Text>
					<Text color="gray">{backend.workspace?.path ?? config.workspacePath}</Text>
					<Text>Revision {backend.workspace?.revision ?? 0}</Text>
					<Text color="cyan">Research tasks</Text>
					{backend.tasks.slice(0, 8).map((task) => (
						<Text key={task.task_id} color={task.task_id === backend.activeTask?.task_id ? 'green' : 'gray'}>
							{truncate(task.title, 24)} <Text color="gray">{task.status}</Text>
						</Text>
					))}
					<Text color="cyan">Model data</Text>
					{backend.disclosurePolicy ? (
						<Text color="gray">
							{truncate(backend.disclosurePolicy.provider_id, 19)} v{backend.disclosurePolicy.policy_version}
							{' '}raw {backend.disclosurePolicy.raw_bounded_sample}{' '}
							{backend.disclosurePolicy.confirmed ? 'confirmed' : 'unconfirmed'}
						</Text>
					) : <Text color="yellow">policy pending</Text>}
					<Text color="cyan">Artifacts</Text>
					{((backend.workspace?.artifacts ?? []) as ArtifactSummary[]).slice(0, 10).map((artifact) => (
						<Text key={`${artifact.ref.artifact_id}-${artifact.ref.version}`}>
							{formatRef(artifact.ref)} {truncate(artifact.title, 19)} <Text color="gray">{artifact.projection.impact_state}</Text>
						</Text>
					))}
					{backend.workspace?.active_refs?.active_hypothesis ? (
						<Text color="cyan">Active hypothesis {formatRef(backend.workspace.active_refs.active_hypothesis)}</Text>
					) : null}
					<Text color="cyan">Runs</Text>
					{((backend.workspace?.runs ?? []) as RunSummary[]).slice(0, 8).map((run) => (
						<Text key={run.run_id}>
							{truncate(run.run_id, 18)} <Text color={run.state === 'checks_passed' ? 'green' : run.state === 'checks_failed' ? 'yellow' : 'gray'}>{run.state}</Text> <Text color="gray">{run.attempt_count}</Text>
						</Text>
					))}
					{backend.busy ? <Text color="yellow">request active</Text> : null}
					{backend.plotStudioReady ? <Text color="green">Plot Studio opened</Text> : null}
				</Box>
				<Box borderStyle="single" borderColor="gray" flexDirection="column" paddingX={1} flexGrow={1}>
					<Conversation
						taskTitle={backend.activeTask?.title ?? null}
						transcript={backend.transcript}
						streamingAssistant={backend.streamingAssistant}
						activeToolCalls={backend.activeToolCalls}
					/>
					<Box marginTop={1}>
					{backend.runInspector ? <RunEvidenceInspector inspector={backend.runInspector} /> : null}
					{!backend.runInspector && backend.versionInspector ? <VersionInspectorView inspector={backend.versionInspector} /> : null}
					{!backend.runInspector && !backend.versionInspector && backend.reportPreview ? <ReportPreviewView preview={backend.reportPreview} /> : null}
					{!backend.runInspector && !backend.versionInspector && !backend.reportPreview && backend.multiAgentStatus ? <MultiAgentStatusView status={backend.multiAgentStatus} /> : null}
					{!backend.runInspector && !backend.versionInspector && !backend.reportPreview && !backend.multiAgentStatus ? (
						backend.inspector ? <ArtifactInspector inspector={backend.inspector} /> : <Text color="gray">Artifact Inspector</Text>
					) : null}
					</Box>
				</Box>
			</Box>
			{backend.diagnostics.slice(-3).map((line, index) => (
				<Text key={`${index}-${line}`} color="gray">{line}</Text>
			))}
			{pendingDatasetImport ? (
				<Text color="yellow">
					Create a private immutable snapshot of {truncate(pendingDatasetImport, 64)} for reproducible reruns? [yes/no]
				</Text>
			) : null}
			{pendingPaperImport ? (
				<Text color="yellow">
					Create a private immutable PDF snapshot of {truncate(pendingPaperImport.relativePath, 48)} for {truncate(pendingPaperImport.citation.title, 48)}? Document text remains local. [yes/no]
				</Text>
			) : null}
			{pendingOisstProposal ? (
				<Text color="yellow">
					Download {pendingOisstProposal.provider.provider_id} SST for {pendingOisstProposal.selection.year}, {formatCoordinate(pendingOisstProposal.selection.west)}E-{formatCoordinate(pendingOisstProposal.selection.east)}E / {formatCoordinate(pendingOisstProposal.selection.south)}N-{formatCoordinate(pendingOisstProposal.selection.north)}N? {formatBytes(pendingOisstProposal.estimated_uncompressed_bytes)} raw-grid estimate; {formatBytes(pendingOisstProposal.maximum_download_bytes)} network limit; {pendingOisstProposal.license_snapshot.use_constraint} Target {pendingOisstProposal.target_storage}. [yes/no]
				</Text>
			) : null}
			{pendingDisclosureProvider ? (
				<Text color="yellow">
					Use {truncate(pendingDisclosureProvider, 48)} with metadata and aggregate statistics only? Raw samples, document text, and diagnostic excerpts remain local. [yes/no]
				</Text>
			) : null}
			<Box marginTop={1}>
				<Text color="cyan">ocean&gt; </Text>
				<TextInput value={input} onChange={setInput} onSubmit={submit} />
			</Box>
		</Box>
	);
}

function parseOisstSelection(value: string): OisstSubsetSelection | null {
	const parts = value.trim().split(/\s+/);
	if (parts.length !== 5) {
		return null;
	}
	const [yearText, westText, eastText, southText, northText] = parts;
	const year = Number(yearText);
	const west = Number(westText);
	const east = Number(eastText);
	const south = Number(southText);
	const north = Number(northText);
	if (![year, west, east, south, north].every(Number.isFinite) || !Number.isInteger(year)) {
		return null;
	}
	return {year, west, east, south, north};
}

function parsePaperImport(value: string): PendingPaperImport | null {
	const separator = value.indexOf('::');
	if (separator < 1) {
		return null;
	}
	const relativePath = value.slice(0, separator).trim();
	const title = value.slice(separator + 2).trim();
	return relativePath && title ? {relativePath, citation: {title}} : null;
}

function parseRefs(value: string): ArtifactRef[] | null {
	const refs = value.trim().split(/\s+/).map(parseRef);
	if (refs.length === 0 || refs.some((ref) => ref === null)) {
		return null;
	}
	const exactRefs = refs as ArtifactRef[];
	return new Set(exactRefs.map((ref) => formatRef(ref))).size === exactRefs.length ? exactRefs : null;
}

function parseReportCreate(value: string): ReportCreateCommand | null {
	const separator = value.indexOf('::');
	if (separator < 1) {
		return null;
	}
	const title = value.slice(0, separator).trim();
	const artifactRefs = parseRefs(value.slice(separator + 2));
	return title && artifactRefs ? {title, artifactRefs} : null;
}

function parseMultiAgentStart(value: string): MultiAgentStartCommand | null {
	const separator = value.indexOf('::');
	if (separator < 1) {
		return null;
	}
	const roles = value.slice(0, separator).split(',').map((role) => role.trim());
	const prompt = value.slice(separator + 2).trim();
	if (!prompt || roles.length === 0 || roles.length > 4 || new Set(roles).size !== roles.length || !roles.every(isMultiAgentRole)) {
		return null;
	}
	return {prompt, roles};
}

function parseMultiAgentProposalRejection(value: string): MultiAgentProposalRejection | null {
	const separator = value.indexOf('::');
	if (separator < 1) {
		return null;
	}
	const proposalId = value.slice(0, separator).trim();
	const reason = value.slice(separator + 2).trim();
	return isProposalId(proposalId) && reason ? {proposalId, reason} : null;
}

function formatBytes(value: number): string {
	if (value < 1024 * 1024) {
		return `${Math.ceil(value / 1024)} KiB`;
	}
	return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function formatCoordinate(value: number): string {
	return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}

function Conversation({
	taskTitle,
	transcript,
	streamingAssistant,
	activeToolCalls,
}: {
	taskTitle: string | null;
	transcript: TranscriptEntry[];
	streamingAssistant: string;
	activeToolCalls: ActiveToolCall[];
}): React.JSX.Element {
	return (
		<>
			<Text bold>{taskTitle ? `Conversation: ${truncate(taskTitle, 56)}` : 'Conversation'}</Text>
			{transcript.slice(-8).map((item) => (
				<Text key={item.itemId} color={transcriptColor(item.role)}>
					{transcriptLabel(item.role)} {truncate(item.text.replaceAll('\n', ' '), 88)}{item.interrupted ? ' (interrupted)' : ''}
				</Text>
			))}
			{activeToolCalls.map((call) => (
				<Text key={call.toolCallId} color="yellow">tool {truncate(call.toolName, 56)} running</Text>
			))}
			{streamingAssistant ? <Text color="cyan">Ocean {truncate(streamingAssistant.replaceAll('\n', ' '), 88)}</Text> : null}
		</>
	);
}

function ArtifactInspector({inspector}: {inspector: NonNullable<ReturnType<typeof useOceanBackend>['inspector']>}): React.JSX.Element {
	const {artifact, projection, links, reviews, verifications} = inspector;
	const content = artifact.content;
	const figureSpec = artifact.artifact_type === 'figure_spec' ? content : null;
	const figureRequest = artifact.artifact_type === 'figure_request' ? content : null;
	const paper = artifact.artifact_type === 'paper' ? content : null;
	const hypothesis = artifact.artifact_type === 'hypothesis' ? content : null;
	const report = artifact.artifact_type === 'report' ? content : null;
	const details = artifactMetadataDetails(artifact.artifact_type, content);
	const related = artifactLinkDetails(links);
	return (
		<>
			<Text bold>{artifact.title}</Text>
			<Text>{formatRef(artifact.ref)} {artifact.artifact_type}</Text>
			<Text color="gray">{artifact.summary || 'No summary'}</Text>
			<Text>Lifecycle {projection.lifecycle_state}  Review {projection.review_state}  Check {projection.verification_state}</Text>
			<Text>Impact {projection.impact_state}  Links {links.incoming.length}/{links.outgoing.length}  Reviews {reviews.length}</Text>
			{details.map((detail) => (
				<Text key={detail.key} color={inspectorDetailColor(detail.tone)}>{truncate(detail.text, 104)}</Text>
			))}
			{paper ? <PaperSummary content={paper} /> : null}
			{hypothesis ? <HypothesisSummary content={hypothesis} /> : null}
			{report ? <ReportSummary content={report} /> : null}
			{figureRequest ? <FigureRequestSummary content={figureRequest} /> : null}
			{figureSpec ? <FigureSpecSummary content={figureSpec} /> : null}
			{related.map((detail) => (
				<Text key={detail.key} color={inspectorDetailColor(detail.tone)}>{detail.text}</Text>
			))}
			{(projection.impact_reasons ?? []).slice(0, 2).map((reason, index) => (
				<Text key={`${reason.source.artifact_id}-${index}`} color="yellow">{reason.trigger} {formatRef(reason.source)}</Text>
			))}
			{reviews.slice(0, 2).map((review) => (
				<Text key={review.review_id} color={review.state === 'approved' ? 'green' : review.state === 'rejected' ? 'red' : 'yellow'}>
					{review.state} {truncate(review.comment || review.review_id, 48)}
				</Text>
			))}
			{verifications.slice(0, 3).map((verification) => (
				<Text key={verification.verification_id} color={verificationColor(verification.state)}>
					verify {verification.state} <Text color="gray">{verification.origin}/{verification.independence}</Text>
				</Text>
			))}
		</>
	);
}

function ReportPreviewView({preview}: {preview: ReportPreview}): React.JSX.Element {
	return (
		<>
			<Text bold>Report Preview</Text>
			<Text color={preview.conclusion_export_allowed ? 'green' : 'yellow'}>
				{preview.conclusion_export_allowed ? 'conclusion-ready' : 'draft only'}  {preview.fully_reproducible ? 'reproducible inputs' : 'rerun limitation'}
			</Text>
			{preview.items.slice(0, 6).map((item) => (
				<Text key={`${item.ref.artifact_id}-${item.ref.version}`} color={item.conclusion_eligible ? 'gray' : 'yellow'}>
					{formatRef(item.ref)} {item.conclusion_eligible ? 'ready' : truncate(item.limitation ?? 'blocked', 46)}
				</Text>
			))}
		</>
	);
}

function MultiAgentStatusView({status}: {status: MultiAgentStatus}): React.JSX.Element {
	const activeWorkers = status.tasks.filter((task) => task.state === 'queued' || task.state === 'running').length;
	return (
		<>
			<Text bold>Research Workers</Text>
			<Text color={status.feature.enabled ? activeWorkers > 0 ? 'yellow' : 'green' : 'gray'}>
				{status.feature.enabled ? `${activeWorkers}/${status.feature.max_workers} active` : 'feature disabled'}
			</Text>
			{status.tasks.map((task) => (
				<Box key={task.task_id} flexDirection="column" marginTop={1}>
					<Text color={multiAgentStateColor(task.state)}>{task.role} {task.state}</Text>
					<Text color="gray">{task.task_id}</Text>
					{task.error ? <Text color="red">{truncate(task.error, 72)}</Text> : null}
				</Box>
			))}
			{status.proposals.map((proposal) => (
				<Box key={proposal.proposal_id} flexDirection="column" marginTop={1}>
					<Text color={multiAgentStateColor(proposal.state)}>{proposal.role} {proposal.proposal_kind} {proposal.state}</Text>
					<Text color="gray">{proposal.proposal_id}</Text>
					<Text>{truncate(proposal.title, 72)}</Text>
					<Text color="gray">{truncate(proposal.summary, 72)}</Text>
					{proposal.accepted_artifact_ref ? <Text color="green">{formatRef(proposal.accepted_artifact_ref)}</Text> : null}
					{proposal.rejection_reason ? <Text color="yellow">{truncate(proposal.rejection_reason, 72)}</Text> : null}
				</Box>
			))}
		</>
	);
}

function PaperSummary({content}: {content: Record<string, unknown>}): React.JSX.Element {
	const citation = record(content.citation);
	const authors = Array.isArray(citation.authors) ? citation.authors.filter((author): author is string => typeof author === 'string') : [];
	const publicationYear = typeof citation.publication_year === 'number' ? String(citation.publication_year) : '';
	const sourceKind = typeof content.source_kind === 'string' ? content.source_kind : '';
	return (
		<>
			<Text color="cyan">{[authors.join(', '), publicationYear].filter(Boolean).join('  ') || 'Citation metadata'}</Text>
			<Text color="gray">{sourceKind || 'metadata_only'}  document text local</Text>
		</>
	);
}

function HypothesisSummary({content}: {content: Record<string, unknown>}): React.JSX.Element {
	const statement = typeof content.statement === 'string' ? content.statement : '';
	const predictions = Array.isArray(content.predictions) ? content.predictions.length : 0;
	const falsifiers = Array.isArray(content.falsification_criteria) ? content.falsification_criteria.length : 0;
	return (
		<>
			{statement ? <Text color="cyan">{truncate(statement, 78)}</Text> : null}
			<Text color="gray">{predictions} predictions  {falsifiers} falsification criteria</Text>
		</>
	);
}

function ReportSummary({content}: {content: Record<string, unknown>}): React.JSX.Element {
	const conclusionReady = content.conclusion_export_allowed === true;
	const reproducible = content.fully_reproducible === true;
	const refs = Array.isArray(content.artifact_refs)
		? content.artifact_refs.map(asArtifactRef).filter((ref): ref is ArtifactRef => ref !== null)
		: [];
	const diagnostics = new Map(
		(Array.isArray(content.diagnostics) ? content.diagnostics : [])
			.map((value) => record(value))
			.map((diagnostic) => {
				const ref = asArtifactRef(diagnostic.ref);
				return ref ? [formatRef(ref), diagnostic] as const : null;
			})
			.filter((entry): entry is readonly [string, Record<string, unknown>] => entry !== null),
	);
	return (
		<>
			<Text color={conclusionReady ? 'green' : 'yellow'}>
				{conclusionReady ? 'conclusion-ready' : 'draft only'}  {refs.length} pinned refs  {reproducible ? 'reproducible inputs' : 'rerun limitation'}
			</Text>
			{refs.map((ref) => {
				const diagnostic = diagnostics.get(formatRef(ref));
				const eligible = diagnostic?.conclusion_eligible === true;
				const limitation = typeof diagnostic?.limitation === 'string' ? diagnostic.limitation : '';
				const grounding = typeof diagnostic?.grounding === 'string' ? diagnostic.grounding : '';
				return (
					<Text key={formatRef(ref)} color={eligible ? 'gray' : 'yellow'}>
						{formatRef(ref)} {eligible ? 'eligible' : truncate(limitation || 'blocked', 52)}{grounding ? `  ${grounding}` : ''}
					</Text>
				);
			})}
		</>
	);
}

function FigureRequestSummary({content}: {content: Record<string, unknown>}): React.JSX.Element {
	const goal = typeof content.user_goal === 'string' ? content.user_goal : '';
	const datasets = Array.isArray(content.dataset_refs) ? content.dataset_refs.length : 0;
	const outputs = Array.isArray(content.requested_outputs) ? content.requested_outputs.join(', ') : '';
	return (
		<>
			{goal ? <Text color="cyan">{truncate(goal, 78)}</Text> : null}
			<Text color="gray">{datasets} datasets  {outputs}</Text>
		</>
	);
}

function FigureSpecSummary({content}: {content: Record<string, unknown>}): React.JSX.Element {
	const intent = record(content.intent);
	const figure = record(content.figure);
	const data = record(content.data);
	const output = record(content.output);
	const goal = typeof intent.scientific_question === 'string' ? intent.scientific_question : '';
	const type = typeof figure.figure_type === 'string' ? figure.figure_type : '';
	const formats = Array.isArray(output.formats) ? output.formats.join(', ') : '';
	const sources = Array.isArray(data.source_refs) ? data.source_refs.length : 0;
	return (
		<>
			{goal ? <Text color="cyan">{truncate(goal, 78)}</Text> : null}
			<Text color="gray">{type}  {sources} sources  {formats}</Text>
		</>
	);
}

function VersionInspectorView({inspector}: {inspector: VersionInspector}): React.JSX.Element {
	return (
		<>
			<Text bold>{inspector.artifactId}</Text>
			{inspector.versions.map((version) => (
				<Text key={`${version.ref.artifact_id}-${version.ref.version}`}>
					{formatRef(version.ref)} {truncate(version.title, 32)} <Text color="gray">{version.projection.lifecycle_state}/{version.projection.impact_state}</Text>
				</Text>
			))}
		</>
	);
}

function RunEvidenceInspector({inspector}: {inspector: RunInspector}): React.JSX.Element {
	const selected = inspector.attempts.find((attempt) => attempt.attempt.attempt_id === inspector.run.selected_attempt_id)
		?? inspector.attempts.at(-1)
		?? null;
	const analysis = selected?.code.find((file) => file.name === 'analysis.py') ?? selected?.code[0] ?? null;
	return (
		<>
			<Text bold>{inspector.run.run_id}</Text>
			<Text>{inspector.run.state}  {selected?.attempt.state ?? 'no attempts'}  {selected?.attempt.execution_trust ?? ''}</Text>
			{inspector.run.inputs.map((input) => (
				<Text key={input.label} color="gray">{input.label} {input.materialization_level}</Text>
			))}
			{(selected?.attempt.checks ?? []).slice(0, 5).map((check) => (
				<Text key={check.check_id} color={check.state === 'pass' ? 'green' : check.state === 'warning' ? 'yellow' : 'red'}>
					{check.state} {check.check_id} <Text color="gray">{check.origin}/{check.independence}</Text>
				</Text>
			))}
			{analysis ? <>
				<Text color="cyan">{analysis.name} {analysis.integrity} {analysis.sha256.slice(0, 12)}</Text>
				{(analysis.content ?? 'Code snapshot unavailable.').split('\n').slice(0, 10).map((line, index) => <Text key={`${index}-${line}`} color="gray">{line || ' '}</Text>)}
			</> : null}
		</>
	);
}

function parseRef(value: string): ArtifactRef | null {
	const match = /^([a-z][a-z0-9_]{2,127})@v([1-9][0-9]*)$/.exec(value.trim());
	return match ? {artifact_id: match[1], version: Number(match[2])} : null;
}

function isReviewState(value: string | undefined): value is ReviewState {
	return value === 'pending' || value === 'approved' || value === 'changes_requested' || value === 'rejected';
}

function isMultiAgentRole(value: string): value is MultiAgentRole {
	return value === 'literature_scout'
		|| value === 'data_analyst'
		|| value === 'figure_reviewer'
		|| value === 'research_writer';
}

function isTaskId(value: string): boolean {
	return /^task_[A-Za-z0-9_-]{1,123}$/.test(value);
}

function isProposalId(value: string): boolean {
	return /^proposal_[A-Za-z0-9_-]{1,119}$/.test(value);
}

function multiAgentStateColor(state: string): 'cyan' | 'green' | 'yellow' | 'red' | 'gray' {
	if (state === 'completed' || state === 'accepted') {
		return 'green';
	}
	if (state === 'queued' || state === 'running' || state === 'pending') {
		return 'yellow';
	}
	if (state === 'failed' || state === 'rejected' || state === 'cancelled' || state === 'interrupted') {
		return 'red';
	}
	return 'gray';
}

function inspectorDetailColor(tone: InspectorDetailTone): 'cyan' | 'yellow' | 'gray' {
	return tone === 'info' ? 'cyan' : tone === 'warning' ? 'yellow' : 'gray';
}

function transcriptLabel(role: TranscriptEntry['role']): string {
	return role === 'user' ? 'You:' : role === 'assistant' ? 'Ocean:' : role === 'tool' ? 'Tool:' : 'System:';
}

function transcriptColor(role: TranscriptEntry['role']): 'cyan' | 'green' | 'yellow' | 'gray' {
	return role === 'user' ? 'cyan' : role === 'assistant' ? 'green' : role === 'tool' ? 'yellow' : 'gray';
}

function truncate(value: string, length: number): string {
	return value.length > length ? `${value.slice(0, Math.max(1, length - 1))}…` : value;
}

function record(value: unknown): Record<string, unknown> {
	return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
