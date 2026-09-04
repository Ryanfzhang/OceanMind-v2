import {useEffect, useRef, useState} from 'react';
import {BarChart3, BookOpen, ChevronDown, ChevronRight, MessageSquare, Sparkles, Timer} from 'lucide-react';

import {presentTranscript, type PresentedTranscriptGroup} from '../conversation-presentation.js';
import {MessageMarkdown, referencedResultKeys, type MarkdownResultLink} from '../message-markdown.js';
import type {PendingInteraction} from '../pending-interaction.js';
import {resultsForRequest, taskResultRefKey, taskResultsForRequest} from '../types.js';
import type {DeliveryManifest, ResearchTask, TaskOutput, TaskResultRecord, TeamSnapshot, TranscriptItem} from '../types.js';
import {useUiLanguage} from '../i18n.js';
import {AgentCollaborationCanvas, timerClock} from './AgentCollaborationCanvas.js';
import {InteractionDrawer} from './InteractionDrawer.js';

function text(item: TranscriptItem): string {
  return `${item.text}${item.interrupted ? ' (interrupted)' : ''}`;
}

function compactResearchResult(value: string, limit = 360): string {
  const cleaned = value
    .replace(/^[#>\s]+/gm, '')
    .replace(/[`*_]/g, '')
    .replace(/\[\[(?:result|output):[^\]]+\]\]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (cleaned.length <= limit) return cleaned;
  const head = cleaned.slice(0, limit + 1);
  const boundary = Math.max(
    head.lastIndexOf('。'),
    head.lastIndexOf('！'),
    head.lastIndexOf('？'),
    head.lastIndexOf('. '),
    head.lastIndexOf('; '),
  );
  return `${head.slice(0, boundary >= Math.floor(limit * .55) ? boundary + 1 : limit).trimEnd()}…`;
}

export function taskResultKeys(result: TaskResultRecord): string[] {
  const {task_id: taskId, result_id: resultId, version} = result.result_ref;
  const paddedVersion = String(version).padStart(4, '0');
  const outputPath = typeof result.content?.output_path === 'string' ? result.content.output_path : null;
  return [
    taskResultRefKey(result.result_ref),
    `${taskId}/${resultId}@v${paddedVersion}`,
    `${taskId}/${resultId}`,
    `${resultId}@v${version}`,
    `${resultId}@v${paddedVersion}`,
    resultId,
    ...(outputPath ? [outputPath] : []),
    ...(result.execution_output_names ?? []),
  ];
}

function timestamp(value?: string): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

function clockDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1_000));
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);
  return hours
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${String(totalMinutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function completedDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1_000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) return `${totalMinutes}m ${String(seconds).padStart(2, '0')}s`;
  const hours = Math.floor(totalMinutes / 60);
  return `${hours}h ${String(totalMinutes % 60).padStart(2, '0')}m`;
}

function requestTimeBounds(group: PresentedTranscriptGroup): {startedAt: number | null; endedAt: number | null; finalAt: number | null} {
  const candidates = [...group.userItems, ...group.processItems, ...(group.finalItem ? [group.finalItem] : [])]
    .map((item) => timestamp(item.created_at))
    .filter((value): value is number => value !== null);
  const userCandidates = group.userItems
    .map((item) => timestamp(item.created_at))
    .filter((value): value is number => value !== null);
  const startedAt = userCandidates.length ? Math.min(...userCandidates) : (candidates.length ? Math.min(...candidates) : null);
  const finalAt = timestamp(group.finalItem?.created_at);
  const stoppedAt = !group.active && candidates.length ? Math.max(...candidates) : null;
  return {startedAt, endedAt: finalAt ?? stoppedAt, finalAt};
}

function RequestElapsedTime({group}: {group: PresentedTranscriptGroup}): React.JSX.Element | null {
  const {text: uiText} = useUiLanguage();
  const {startedAt, endedAt, finalAt} = requestTimeBounds(group);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!group.active || startedAt === null) return;
    setNow(Date.now());
    const interval = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [group.active, startedAt]);

  if (startedAt === null) return null;
  const elapsed = Math.max(0, (group.active ? now : endedAt ?? startedAt) - startedAt);
  const label = group.active
    ? `${uiText('Running', '运行中')} · ${clockDuration(elapsed)}`
    : finalAt !== null
      ? `${uiText('Completed in', '完成于')} ${completedDuration(elapsed)}`
      : `${uiText('Stopped after', '停止于')} ${completedDuration(elapsed)}`;
  return <p className={`request-elapsed-time${group.active ? ' running' : ''}`}>{label}</p>;
}

function Working({
  group,
  streaming,
  team,
  hiddenProcessItemId = null,
}: {
  group: PresentedTranscriptGroup;
  streaming: string;
  team: TeamSnapshot | null;
  hiddenProcessItemId?: string | null;
}): React.JSX.Element | null {
  const {text: uiText} = useUiLanguage();
  const traceRef = useRef<HTMLDivElement>(null);
  const [canvasCollapsed, setCanvasCollapsed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const process = group.processItems.filter((item) =>
    (item.role === 'assistant' || item.role === 'system') && item.item_id !== hiddenProcessItemId,
  );
  const live = group.active && streaming && text(process.at(-1) ?? {item_id: '', role: 'assistant', text: ''}) !== streaming ? streaming : '';
  const matchingTeam = team && (team.request_id === group.requestId || (group.active && !team.request_id)) ? team : null;
  const expertResults = group.active
    ? (matchingTeam?.agents ?? []).flatMap((agent) => {
      if (agent.authority === 'coordinator' || !agent.result_summary?.trim()) return [];
      const summary = compactResearchResult(agent.result_summary);
      return summary ? [{agent, summary}] : [];
    })
    : [];
  const hasLiveTrace = group.active && Boolean(process.length || live || expertResults.length);
  const timing = requestTimeBounds(group);
  useEffect(() => {
    setCanvasCollapsed(false);
  }, [group.active, group.key]);
  useEffect(() => {
    if (traceRef.current) traceRef.current.scrollTop = traceRef.current.scrollHeight;
  }, [process.length, live, expertResults.length]);
  useEffect(() => {
    if (!group.active || timing.startedAt === null) return;
    setNow(Date.now());
    const interval = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [group.active, timing.startedAt]);
  if (!group.active && !matchingTeam) return null;
  const status = group.active ? 'working' : matchingTeam?.status ?? 'completed';
  const elapsed = timing.startedAt === null ? null : Math.max(0, (group.active ? now : timing.endedAt ?? timing.startedAt) - timing.startedAt);
  const timerLabel = group.active && elapsed !== null ? timerClock(elapsed) : null;
  return <section className={`working-stream ${group.active ? 'active' : 'completed'} status-${status}${matchingTeam ? ' has-team' : ''}${canvasCollapsed ? ' details-collapsed' : ''}${hasLiveTrace ? ' has-trace' : ''}`}>
    <header className="working-stream-header"><i /><span>{uiText('Team', '团队')}</span>{timerLabel ? <small className="working-stream-timer"><Timer size={12} />{timerLabel}</small> : null}
      {matchingTeam ? <button
        className="working-stream-toggle"
        type="button"
        aria-expanded={!canvasCollapsed}
        aria-label={canvasCollapsed ? uiText('View team', '查看团队') : uiText('Hide team', '收起团队')}
        title={canvasCollapsed ? uiText('View team', '查看团队') : uiText('Hide team', '收起团队')}
        onClick={() => setCanvasCollapsed((value) => !value)}
      >
        {canvasCollapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
      </button> : null}
    </header>
    <div className="working-stream-content">
      {matchingTeam && !canvasCollapsed ? <AgentCollaborationCanvas snapshot={matchingTeam} /> : null}
      {hasLiveTrace ? <section className="live-research-log" aria-label={uiText('Live research log', '实时研究记录')}>
        <header><strong>{uiText('Live research log', '实时研究记录')}</strong><small>{uiText('Temporary', '临时')}</small></header>
        <div className="live-research-log-body" ref={traceRef} aria-live="polite">
          {process.map((item) => <article className={`research-log-entry ${item.role === 'system' ? 'system' : 'coordinator'}`} key={item.item_id}>
            <strong>{item.role === 'system' ? 'OceanMind' : 'Coordinator'}</strong>
            <div><MessageMarkdown content={text(item)} /></div>
          </article>)}
          {expertResults.map(({agent, summary}) => <article className="research-log-entry expert-result" key={`result:${agent.agent_id}:${summary}`}>
            <strong>{agent.semantic_role}</strong><p>{summary}</p>
          </article>)}
          {live ? <article className="research-log-entry coordinator streaming"><strong>Coordinator</strong><div><MessageMarkdown content={live} /></div></article> : null}
        </div>
      </section> : null}
    </div>
  </section>;
}

function InlinePaperSelection({
  interaction,
  summary,
  answer,
  onAnswer,
  onSubmit,
}: {
  interaction: PendingInteraction;
  summary: TranscriptItem | null;
  answer: string;
  onAnswer: (answer: string) => void;
  onSubmit: (answer?: string) => void;
}): React.JSX.Element {
  const interactionRef = useRef<HTMLElement>(null);

  useEffect(() => {
    interactionRef.current?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }, [interaction.interactionId]);

  return <article className="message assistant coordinator-checkpoint" ref={interactionRef}>
    {summary ? <MessageMarkdown content={text(summary)} /> : null}
    <InteractionDrawer
      interaction={interaction}
      answer={answer}
      onAnswer={onAnswer}
      onSubmit={onSubmit}
    />
  </article>;
}

export function ConversationTranscript({
  loading,
  transcript,
  streaming,
  activeRequestId,
  task,
  workspacePath,
  outputs,
  manifests,
  taskResults,
  team,
  teamSnapshots = {},
  pendingInteraction = null,
  interactionAnswer = '',
  onInteractionAnswer = () => undefined,
  onInteractionSubmit = () => undefined,
  onOpenResult,
  onOpenTaskResult,
  onOpenTaskResultFile = () => undefined,
}: {
  loading: boolean;
  transcript: TranscriptItem[];
  streaming: string;
  activeRequestId: string | null;
  task: ResearchTask | null;
  workspacePath: string | null;
  outputs: TaskOutput[];
  manifests: DeliveryManifest[];
  taskResults: TaskResultRecord[];
  team: TeamSnapshot | null;
  teamSnapshots?: Readonly<Record<string, TeamSnapshot>>;
  pendingInteraction?: PendingInteraction | null;
  interactionAnswer?: string;
  onInteractionAnswer?: (answer: string) => void;
  onInteractionSubmit?: (answer?: string) => void;
  onOpenResult: (output: TaskOutput) => void;
  onOpenTaskResult: (result: TaskResultRecord) => void;
  onOpenTaskResultFile?: (result: TaskResultRecord, file: TaskResultRecord['files'][number]) => void;
}): React.JSX.Element {
  const {text: uiText} = useUiLanguage();
  if (loading) return <div className="empty-state" role="status"><p>{uiText('Loading task…', '正在加载任务…')}</p></div>;
  const groups = presentTranscript(transcript, activeRequestId);
  const hasActive = groups.some((group) => group.active);
  const taskScopedResults = taskResults
    .filter((result) => result.kind === 'interactive_view' || result.kind === 'report');
  return <>
    {groups.map((group) => {
      const historicalTeam = group.requestId ? teamSnapshots[group.requestId] : null;
      const groupTeam = historicalTeam
        ?? (team && (team.request_id === group.requestId || (group.active && !team.request_id)) ? team : null);
      const groupHasTeam = Boolean(groupTeam);
      const paperSelection = group.active && pendingInteraction?.kind === 'paper_selection'
        ? pendingInteraction
        : null;
      const checkpointSummary = paperSelection
        ? [...group.processItems].reverse().find((item) => item.role === 'assistant' && text(item).trim()) ?? null
        : null;
      const requestResults = taskResultsForRequest(taskResults, group.requestId);
      const directResults = requestResults
        .filter((result) => result.kind === 'interactive_view' || result.kind === 'report');
      const supplementaryNotebooks = requestResults.flatMap((result) => {
        if (result.kind !== 'file' || result.content.role !== 'supplementary_figure_notebook') return [];
        const declared = typeof result.content.file === 'string' ? result.content.file : null;
        const file = result.files.find((item) => declared && item.path === declared)
          ?? result.files.find((item) => item.path.endsWith('.ipynb'));
        return file ? [{result, file}] : [];
      });
      const skillUpdates = requestResults.filter((result) => result.content.role === 'skill_update');
      const legacyResults = directResults.length ? [] : resultsForRequest(outputs, manifests, group.requestId);
      const final = group.finalItem ? text(group.finalItem) : '';
      const referencedKeys = referencedResultKeys(final);
      // Result references are durable at task scope.  A follow-up report may
      // cite a view produced by an earlier request, so link resolution must
      // not be artificially restricted to the current request group.
      const resultLinks: MarkdownResultLink[] = taskScopedResults.map((result) => ({
        keys: taskResultKeys(result),
        label: result.title,
        summary: result.summary,
        kind: result.kind,
        onOpen: () => onOpenTaskResult(result),
      }));
      const unreferencedResults = directResults.filter((result) =>
        !taskResultKeys(result).some((key) => referencedKeys.has(key)),
      );
      const links = legacyResults.map(({entry, output}) => ({
        ref: entry.open_ref,
        label: entry.kind === 'interactive_view' ? `Interactive View — ${entry.title}` : `Research Report — ${entry.title}`,
        onOpen: () => onOpenResult(output),
      }));
      return <section className="conversation-exchange" key={group.key}>
        {group.userItems.map((item) => <article className="message user" key={item.item_id}><MessageMarkdown content={text(item)} /></article>)}
        {!group.active || !groupHasTeam ? <RequestElapsedTime group={group} /> : null}
        <Working
          group={group}
          streaming={streaming}
          team={groupTeam}
          hiddenProcessItemId={checkpointSummary?.item_id ?? null}
        />
        {paperSelection ? <InlinePaperSelection
          interaction={paperSelection}
          summary={checkpointSummary}
          answer={interactionAnswer}
          onAnswer={onInteractionAnswer}
          onSubmit={onInteractionSubmit}
        /> : null}
        {group.finalItem ? <article className="message assistant"><MessageMarkdown content={final} artifactLinks={links} resultLinks={resultLinks} />
          {skillUpdates.length ? <section className="skill-updates" aria-label="OceanMind learned from this task">
            <header><Sparkles size={18} /><div><h3>{uiText('OceanMind learned from this task', 'OceanMind 从本次任务中学习了经验')}</h3><p>{uiText('A stronger reviewer approved these reusable workspace practices.', '更强的审核模型批准了这些可复用的工作区实践。')}</p></div></header>
            {skillUpdates.map((result) => <article key={taskResultRefKey(result.result_ref)}>
              <strong>{typeof result.content.skill_name === 'string' ? result.content.skill_name : result.title}</strong>
              <small>{typeof result.content.operation === 'string' && result.content.operation === 'update' ? uiText('Updated', '已更新') : uiText('Created', '已创建')} · {uiText('version', '版本')} {typeof result.content.version === 'number' ? result.content.version : 1}</small>
              <p>{result.summary}</p>
            </article>)}
          </section> : null}
          {supplementaryNotebooks.length ? <section className="supplementary-materials" aria-label={uiText('Supplementary Materials', '补充材料')}>
            <strong>{uiText('Supplementary Materials:', '补充材料：')}</strong>
            <ul>{supplementaryNotebooks.map(({result, file}) => <li key={taskResultRefKey(result.result_ref)}>
              <button className="supplementary-material-link" type="button" onClick={() => onOpenTaskResultFile(result, file)} title={uiText('Open notebook', '打开 notebook')}>
                <strong>{file.path.split(/[\\/]/).filter(Boolean).at(-1) ?? file.path}</strong>
              </button>
            </li>)}</ul>
          </section> : null}
          {unreferencedResults.length || legacyResults.length ? <details className="additional-results">
            <summary>{uiText('Additional Results', '其他结果')} ({unreferencedResults.length + legacyResults.length})</summary>
            <div className="message-artifact-actions">
              {unreferencedResults.map((result) => <button key={taskResultRefKey(result.result_ref)} className={`result-${result.kind}`} onClick={() => onOpenTaskResult(result)}>
                {result.kind === 'interactive_view' ? <BarChart3 size={16} /> : <BookOpen size={16} />}
                <span><strong>{result.kind === 'interactive_view' ? uiText('Interactive View', '交互视图') : uiText('Research Report', '研究报告')}</strong><small>{result.title}</small></span>
              </button>)}
              {legacyResults.map(({entry, output}) => <button key={entry.entry_id} className={`result-${entry.kind}`} onClick={() => onOpenResult(output)}>
                {entry.kind === 'interactive_view' ? <BarChart3 size={16} /> : <BookOpen size={16} />}
                <span><strong>{entry.kind === 'interactive_view' ? uiText('Interactive View', '交互视图') : uiText('Research Report', '研究报告')}</strong><small>{entry.title}</small></span>
              </button>)}
            </div>
          </details> : null}
        </article> : null}
      </section>;
    })}
    {activeRequestId && !hasActive ? <>
      <Working group={{key: activeRequestId, requestId: activeRequestId, userItems: [], processItems: [], finalItem: null, active: true}} streaming={streaming} team={team} />
    </> : null}
    {!task && !workspacePath ? <div className="empty-state"><MessageSquare size={34} /><p>{uiText('Open a project to begin.', '打开一个项目以开始。')}</p></div> : null}
    {!task && workspacePath ? <div className="empty-state"><MessageSquare size={34} /><p>{uiText('Create or select a research task.', '创建或选择一个研究任务。')}</p></div> : null}
  </>;
}
