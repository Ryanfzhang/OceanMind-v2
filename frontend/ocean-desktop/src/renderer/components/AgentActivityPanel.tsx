import {Paperclip} from 'lucide-react';

import {MessageMarkdown} from '../message-markdown.js';
import type {TeamAgent, TeamAgentProfile, TeamAgentTranscript, TeamAgentTranscriptBlock, TeamTodo} from '../types.js';

const TODO_STATE_LABELS: Record<TeamTodo['state'], string> = {
  pending: 'Waiting',
  queued: 'Queued',
  working: 'Working',
  result_returned: 'Result ready',
  stopped: 'Stopped',
  skipped: 'Skipped',
};

function displayRole(agent: TeamAgent, profiles: TeamAgentProfile[]): string {
  return profiles.find((profile) => profile.profile_id === agent.profile_id)?.display_name
    ?? agent.semantic_role;
}

function statusLabel(status: TeamAgent['status']): string {
  return status.replaceAll('_', ' ').replace(/\b[a-z]/g, (char) => char.toUpperCase());
}

/** The activity feed can carry markdown headings or result excerpts; the header shows one clean line. */
function activitySummary(activity: string): string {
  return activity.replace(/^[#>\s]+/, '').replace(/[#*_`]/g, '').replace(/\s+/g, ' ').trim();
}

function todosForAgent(agent: TeamAgent, todos: TeamTodo[]): TeamTodo[] {
  if (agent.authority === 'coordinator') return todos;
  const byWorkOrder = agent.work_order_id
    ? todos.filter((todo) => todo.work_order_id === agent.work_order_id)
    : [];
  if (byWorkOrder.length) return byWorkOrder;
  return agent.profile_id
    ? todos.filter((todo) => todo.profile_id === agent.profile_id
      && (todo.expert_key ?? null) === (agent.expert_key ?? null))
    : [];
}

function speakerLabel(
  role: TeamAgentTranscript['messages'][number]['role'],
  selectedRole: string,
  coordinatorSelected: boolean,
): string {
  if (coordinatorSelected) {
    if (role === 'user') return 'You → Coordinator';
    if (role === 'coordinator') return 'Coordinator → You';
  } else {
    if (role === 'coordinator' || role === 'user') return `Coordinator → ${selectedRole}`;
    if (role === 'expert') return `${selectedRole} → Coordinator`;
  }
  if (role === 'tool') return 'Technical activity';
  return role === 'system' ? 'System' : role;
}

function timeLabel(value?: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {hour: '2-digit', minute: '2-digit'}).format(parsed);
}

type JsonEnvelope = {
  prose: string;
  payload: Record<string, unknown>;
};

/**
 * Work-order prompts arrive as instruction prose followed by a JSON envelope.
 * Split the two so the payload can be rendered as a card instead of a raw dump.
 */
function parseJsonEnvelope(text: string): JsonEnvelope | null {
  const trimmed = text.trim();
  for (let start = trimmed.indexOf('{'); start !== -1; start = trimmed.indexOf('{', start + 1)) {
    try {
      const parsed: unknown = JSON.parse(trimmed.slice(start));
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return {prose: trimmed.slice(0, start).trim(), payload: parsed as Record<string, unknown>};
      }
    } catch {
      // This brace opens an incomplete structure; keep scanning.
    }
  }
  return null;
}

type Assignment = {
  goal: string;
  doneWhen?: string;
  contextSummary?: string;
  intents: string[];
  constraints: string[];
  sources: string[];
};

function stringField(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    .map((item) => item.trim());
}

function sourceList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>;
      const title = stringField(record, 'title') ?? stringField(record, 'handle');
      const kind = stringField(record, 'kind');
      if (title) return kind ? `${title} (${kind})` : title;
      return null;
    }
    return typeof item === 'string' && item.trim() ? item.trim() : null;
  }).filter((item): item is string => Boolean(item));
}

function asAssignment(payload: Record<string, unknown>): Assignment | null {
  const goal = stringField(payload, 'task_goal');
  if (!goal) return null;
  return {
    goal,
    doneWhen: stringField(payload, 'done_when'),
    contextSummary: stringField(payload, 'context_summary'),
    intents: stringList(payload.outcome_intents),
    constraints: stringList(payload.constraints),
    sources: sourceList(payload.sources),
  };
}

function AssignmentCard({assignment, prose}: {assignment: Assignment; prose: string}) {
  return <div className="agent-assignment-card">
    <dl>
      <div>
        <dt>Task</dt>
        <dd>{assignment.goal}</dd>
      </div>
      {assignment.doneWhen ? <div>
        <dt>Done when</dt>
        <dd>{assignment.doneWhen}</dd>
      </div> : null}
      {assignment.contextSummary ? <div>
        <dt>Context</dt>
        <dd>{assignment.contextSummary}</dd>
      </div> : null}
    </dl>
    {assignment.intents.length ? <ul className="agent-assignment-intents">
      {assignment.intents.map((intent) => <li key={intent}>{intent.replaceAll('_', ' ')}</li>)}
    </ul> : null}
    {assignment.sources.length ? <p className="agent-assignment-sources">Sources: {assignment.sources.join(' · ')}</p> : null}
    {assignment.constraints.length ? <ul className="agent-assignment-constraints">
      {assignment.constraints.map((constraint) => <li key={constraint}>{constraint}</li>)}
    </ul> : null}
    {prose ? <details className="agent-assignment-raw">
      <summary>Full instructions</summary>
      <pre>{prose}</pre>
    </details> : null}
  </div>;
}

function StructuredPayload({prose, payload}: {prose: string; payload: Record<string, unknown>}) {
  return <>
    {prose ? <MessageMarkdown content={prose} /> : null}
    <details className="agent-assignment-raw">
      <summary>Structured payload</summary>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </details>
  </>;
}

type HistoryEntry =
  | {
      kind: 'message';
      key: string;
      role: TeamAgentTranscript['messages'][number]['role'];
      text: string;
      createdAt?: string;
    }
  | {
      kind: 'technical';
      key: string;
      createdAt?: string;
      blocks: Array<{key: string; block: Exclude<TeamAgentTranscriptBlock, {type: 'text'}>}>;
    };

function historyEntries(messages: TeamAgentTranscript['messages']): HistoryEntry[] {
  const entries: HistoryEntry[] = [];
  let technical: Array<{key: string; block: Exclude<TeamAgentTranscriptBlock, {type: 'text'}>}> = [];
  let technicalCreatedAt: string | undefined;
  const flushTechnical = () => {
    if (!technical.length) return;
    entries.push({
      kind: 'technical',
      key: `technical-${technical[0]!.key}`,
      createdAt: technicalCreatedAt,
      blocks: technical,
    });
    technical = [];
    technicalCreatedAt = undefined;
  };

  for (const message of messages) {
    message.blocks.forEach((block, index) => {
      const key = `${message.message_id}-${index}`;
      if (block.type !== 'text') {
        technicalCreatedAt ??= message.created_at;
        technical.push({key, block});
        return;
      }
      flushTechnical();
      if (block.text.trim()) {
        entries.push({kind: 'message', key, role: message.role, text: block.text, createdAt: message.created_at});
      }
    });
  }
  flushTechnical();
  return entries;
}

export function AgentActivityPanel({
  agent,
  profiles,
  todos,
  transcript,
  loading,
  error,
}: {
  agent: TeamAgent | null;
  profiles: TeamAgentProfile[];
  todos: TeamTodo[];
  transcript: TeamAgentTranscript | null;
  loading: boolean;
  error: string | null;
}): React.JSX.Element {
  if (!agent) return <section className="agent-activity empty" aria-label="Agent activity">
    <div><small>Team activity</small><strong>Select an Expert workstream</strong></div>
    <p>Its assignment, progress, and conversation with the Coordinator will appear here.</p>
  </section>;

  const role = displayRole(agent, profiles);
  const activity = activitySummary(agent.activity);
  const selectedTodos = todosForAgent(agent, todos);
  const returned = selectedTodos.filter((todo) => todo.state === 'result_returned').length;
  const matchesSelection = transcript?.agent_id === agent.agent_id
    && (agent.authority === 'coordinator' || transcript.work_order_id === agent.work_order_id);
  const messages = matchesSelection ? transcript?.messages ?? [] : [];
  const entries = historyEntries(messages);
  const coordinatorSelected = agent.authority === 'coordinator';
  const conversationLabel = coordinatorSelected ? 'You ↔ Coordinator' : `Coordinator ↔ ${role}`;

  return <section className="agent-activity" aria-label={`${role} activity and conversation`}>
    <header className="agent-activity-header">
      <div>
        <small>{coordinatorSelected ? 'Team overview' : 'Selected agent'}</small>
        <strong>{role}</strong>
        {activity ? <p>{activity}</p> : null}
      </div>
      <span className={`agent-state-pill status-${agent.status}`}>{statusLabel(agent.status)}</span>
    </header>

    <section className="agent-task-strip" aria-label={`${role} assigned work`}>
      <header>
        <strong>{coordinatorSelected ? 'Research Tasks' : 'Assigned Work'}</strong>
        <small>{selectedTodos.length ? `${returned} of ${selectedTodos.length} Results Ready` : 'Planning'}</small>
      </header>
      {selectedTodos.length ? <ol>
        {selectedTodos.map((todo) => <li className={`state-${todo.state}`} key={todo.todo_id}>
          <i aria-hidden="true" />
          <span>{todo.question}</span>
          <small>{TODO_STATE_LABELS[todo.state]}</small>
        </li>)}
      </ol> : <p>{agent.task_goal ?? (coordinatorSelected
        ? 'The Coordinator is preparing the research tasks.'
        : 'No separate task has been recorded for this agent yet.')}</p>}
    </section>

    <section className="agent-activity-thread">
      <header><strong>Conversation</strong><small>{conversationLabel}</small></header>
      <div className="agent-activity-log" aria-live="polite">
        {loading ? <p className="agent-activity-state">Loading saved conversation…</p> : null}
        {error ? <p className="agent-activity-state error">{error}</p> : null}
        {!loading && !error && !entries.length ? <p className="agent-activity-state">No saved messages yet.</p> : null}
        {matchesSelection && transcript && transcript.compaction_generation > 0 ? <details className="agent-activity-memory">
          <summary>Long-session history preserved</summary>
          <p>Messages remain available across {transcript.compaction_generation} context-compaction boundary/boundaries.</p>
        </details> : null}
        {entries.map((entry) => {
          const timestamp = timeLabel(entry.createdAt);
          if (entry.kind === 'message') {
            const envelope = parseJsonEnvelope(entry.text);
            const assignment = envelope ? asAssignment(envelope.payload) : null;
            return <article className={`agent-timeline-message role-${entry.role}`} key={entry.key}>
              <i aria-hidden="true" />
              <div>
                <header><small>{speakerLabel(entry.role, role, coordinatorSelected)}</small>{timestamp ? <time>{timestamp}</time> : null}</header>
                {assignment && envelope ? <AssignmentCard assignment={assignment} prose={envelope.prose} />
                  : envelope ? <StructuredPayload prose={envelope.prose} payload={envelope.payload} />
                  : <MessageMarkdown content={entry.text} />}
              </div>
            </article>;
          }
          return <details className="agent-technical-activity" key={entry.key}>
            <summary>
              <span>Technical activity</span>
              <small>{entry.blocks.length} record{entry.blocks.length === 1 ? '' : 's'}{timestamp ? ` · ${timestamp}` : ''}</small>
            </summary>
            <div className="agent-technical-records">
              {entry.blocks.map(({key, block}) => {
                if (block.type === 'tool_call') return <article className="agent-tool-record" key={key}>
                  <strong>Used {block.tool_name}</strong>
                  <pre>{JSON.stringify(block.input, null, 2)}</pre>
                </article>;
                if (block.type === 'tool_result') return <article className={`agent-tool-record${block.is_error ? ' error' : ''}`} key={key}>
                  <strong>{block.is_error ? 'Tool error' : 'Tool result'}</strong>
                  <pre>{block.text}</pre>
                </article>;
                return <p className="agent-attachment" key={key}><Paperclip size={13} />{block.source_path || block.media_type}</p>;
              })}
            </div>
          </details>;
        })}
      </div>
    </section>
  </section>;
}
