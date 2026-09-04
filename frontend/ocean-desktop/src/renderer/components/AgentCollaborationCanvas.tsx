import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';

import type {TeamAgent, TeamAgentProfile, TeamSnapshot, TeamTodo} from '../types.js';
import {CoordinatorRoleIcon, ExpertRoleIcon} from './AgentRoleIcons.js';

const useClientLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

type Point = {x: number; y: number};
type PreviewAnchor = {pointerX: number; pointerY: number; left: number; top: number};
type CanvasAgent = TeamAgent & {
  displayRole: string;
  profile: TeamAgentProfile | null;
};

const DEFAULT_CANVAS_WIDTH = 620;
const NODE_WIDTH = 176;
const NODE_HEIGHT = 76;
const EXPERT_ACCENTS = ['#2f80b9', '#2b9a87', '#766fc1', '#bd7b38', '#c45f64', '#587e5e'];

function safeId(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]/g, '-');
}

function cleanText(value: string | null | undefined): string {
  return (value ?? '').replace(/^[#>\s]+/, '').replace(/[`*_]/g, '').replace(/\s+/g, ' ').trim();
}

function positionsFor(agents: CanvasAgent[], width: number): {positions: Map<string, Point>; height: number} {
  const coordinator = agents.find((item) => item.authority === 'coordinator');
  const members = agents.filter((item) => item.authority !== 'coordinator');
  const positions = new Map<string, Point>();
  if (!members.length) {
    if (coordinator) positions.set(coordinator.agent_id, {x: width / 2, y: 86});
    return {positions, height: 168};
  }
  if (coordinator) positions.set(coordinator.agent_id, {x: width / 2, y: 80});
  const columns = width >= 580 ? Math.min(3, members.length) : width >= 410 ? Math.min(2, members.length) : 1;
  const rows = Math.ceil(members.length / columns);
  const horizontalInset = Math.max(12, Math.min(28, width * .04));
  const availableWidth = width - horizontalInset * 2;
  const firstRowY = 210;
  const rowGap = 112;
  members.forEach((member, index) => {
    const row = Math.floor(index / columns);
    const firstInRow = row * columns;
    const rowCount = Math.min(columns, members.length - firstInRow);
    const column = index - firstInRow;
    const slotWidth = availableWidth / rowCount;
    positions.set(member.agent_id, {
      x: horizontalInset + slotWidth * (column + .5),
      y: firstRowY + row * rowGap,
    });
  });
  return {positions, height: firstRowY + (rows - 1) * rowGap + NODE_HEIGHT / 2 + 34};
}

function connectionPath(from: Point, to: Point): string {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    const direction = dx >= 0 ? 1 : -1;
    const startX = from.x + direction * NODE_WIDTH / 2;
    const endX = to.x - direction * NODE_WIDTH / 2;
    const bend = Math.max(30, Math.abs(endX - startX) * .5);
    return `M ${startX} ${from.y} C ${startX + direction * bend} ${from.y}, ${endX - direction * bend} ${to.y}, ${endX} ${to.y}`;
  }
  const direction = dy >= 0 ? 1 : -1;
  const startY = from.y + direction * NODE_HEIGHT / 2;
  const endY = to.y - direction * NODE_HEIGHT / 2;
  const bend = Math.max(26, Math.abs(endY - startY) * .5);
  return `M ${from.x} ${startY} C ${from.x} ${startY + direction * bend}, ${to.x} ${endY - direction * bend}, ${to.x} ${endY}`;
}

function connectionStart(from: Point, to: Point): Point {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    const direction = dx >= 0 ? 1 : -1;
    return {x: from.x + direction * NODE_WIDTH / 2, y: from.y};
  }
  const direction = dy >= 0 ? 1 : -1;
  return {x: from.x, y: from.y + direction * NODE_HEIGHT / 2};
}

function connectionEnd(from: Point, to: Point): Point {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    const direction = dx >= 0 ? 1 : -1;
    return {x: to.x - direction * NODE_WIDTH / 2, y: to.y};
  }
  const direction = dy >= 0 ? 1 : -1;
  return {x: to.x, y: to.y - direction * NODE_HEIGHT / 2};
}

function statusLabel(status: TeamAgent['status']): string {
  return status.replace('_', ' ').replace(/\b[a-z]/g, (char) => char.toUpperCase());
}

function authorityLabel(authority: TeamAgent['authority']): string {
  if (authority === 'coordinator') return 'Coordinator';
  if (authority === 'expert') return 'Expert';
  return 'Scientific Discussion Partner';
}

function stableAccent(agent: CanvasAgent, index: number): string {
  if (agent.authority === 'coordinator') return '#216f9f';
  return EXPERT_ACCENTS[Math.max(0, index - 1) % EXPERT_ACCENTS.length]!;
}

type NodeCopy = {
  objective: string;
  workingOn: string;
};

function todoStatus(todo: TeamTodo): TeamAgent['status'] {
  if (todo.state === 'working') return 'working';
  if (todo.state === 'result_returned') return 'completed';
  if (todo.state === 'stopped') return 'incomplete';
  if (todo.state === 'skipped') return 'skipped';
  return 'waiting';
}

function assignedTodos(snapshot: TeamSnapshot, agent: CanvasAgent): TeamTodo[] {
  const todos = snapshot.todos ?? [];
  if (agent.authority === 'coordinator') return todos;
  const exact = agent.work_order_id ? todos.filter((todo) => todo.work_order_id === agent.work_order_id) : [];
  if (exact.length) return exact;
  return agent.profile_id
    ? todos.filter((todo) => todo.profile_id === agent.profile_id
      && (todo.expert_key ?? null) === (agent.expert_key ?? null))
    : [];
}

function expertIdentity(profileId: string, expertKey?: string | null): string {
  return `expert:${profileId}:${expertKey || 'default'}`;
}

function nodeTaskSummary(snapshot: TeamSnapshot, agent: CanvasAgent): NodeCopy {
  const todos = snapshot.todos ?? [];
  if (agent.authority === 'coordinator') {
    return {
      objective: todos.length
        ? `Coordinate ${todos.length} scientific workstream${todos.length === 1 ? '' : 's'} and synthesize their evidence.`
        : 'Frame the research question and coordinate the evidence needed to answer it.',
      workingOn: cleanText(agent.activity) || 'Assessing the research request.',
    };
  }
  const assigned = assignedTodos(snapshot, agent);
  const current = assigned.find((todo) => todo.state === 'working' || todo.state === 'queued' || todo.state === 'pending')
    ?? assigned.at(-1);
  const terminalActivity: Partial<Record<TeamAgent['status'], string>> = {
    completed: 'Work complete; the result has been returned to the Coordinator.',
    incomplete: 'Returned the supported evidence and its unresolved limitation.',
    blocked: 'Reported the material blocker to the Coordinator.',
    failed: 'The workstream stopped before returning a reliable result.',
    skipped: 'This workstream was not needed in the current research path.',
  };
  return {
    objective: cleanText(current?.question ?? agent.task_goal) || 'Awaiting a bounded scientific assignment.',
    workingOn: terminalActivity[agent.status]
      ?? (cleanText(agent.activity)
        || (agent.status === 'waiting' ? 'Waiting for the next research step.' : 'Working through the assigned evidence.')),
  };
}

function activeRoster(snapshot: TeamSnapshot): CanvasAgent[] {
  const profiles = new Map((snapshot.role_pool ?? []).map((profile) => [profile.profile_id, profile]));
  const coordinator = snapshot.agents.find((agent) => agent.authority === 'coordinator') ?? {
    agent_id: 'coordinator', semantic_role: 'Coordinator', authority: 'coordinator' as const,
    status: 'working' as const, activity: 'Request received',
  };
  const complete: CanvasAgent[] = [{...coordinator, displayRole: 'Coordinator', profile: null}];
  const logicalAgents = new Map<string, TeamAgent>();
  const activeStatuses = new Set<TeamAgent['status']>(['planning', 'working', 'discussing']);
  for (const agent of snapshot.agents) {
    if (agent.authority === 'coordinator') continue;
    // profile_id is the capability type; expert_key is the concrete instance.
    // Repeated rounds of one instance fold together, while same-type siblings
    // remain separate nodes. Prefer the active round in legacy snapshots.
    const key = agent.profile_id
      ? expertIdentity(agent.profile_id, agent.expert_key)
      : agent.work_order_id ?? agent.agent_id;
    const current = logicalAgents.get(key);
    if (!current || activeStatuses.has(agent.status) || !activeStatuses.has(current.status)) {
      logicalAgents.set(key, agent);
    }
  }
  // Keep every task-planned Expert visible even before its session starts.
  for (const todo of snapshot.todos ?? []) {
    const key = expertIdentity(todo.profile_id, todo.expert_key);
    const alreadyRepresented = logicalAgents.has(key);
    if (alreadyRepresented) continue;
    const profile = profiles.get(todo.profile_id);
    logicalAgents.set(key, {
      agent_id: `planned-${safeId(key)}`,
      profile_id: todo.profile_id,
      expert_key: todo.expert_key,
      semantic_role: profile?.display_name ?? 'Planned Expert',
      authority: profile?.authority ?? 'expert',
      status: todoStatus(todo),
      activity: todo.state === 'pending' ? 'Waiting to Start' : todo.question,
      work_order_id: todo.work_order_id,
      task_goal: todo.question,
      result_summary: todo.result_summary,
    });
  }
  for (const agent of logicalAgents.values()) {
    const profile = agent.profile_id ? profiles.get(agent.profile_id) ?? null : null;
    complete.push({
      ...agent,
      displayRole: profile?.display_name ?? (agent.semantic_role || authorityLabel(agent.authority)),
      profile,
    });
  }
  return complete;
}

function previewAnchor(clientX: number, clientY: number): PreviewAnchor {
  const gap = 14;
  return {pointerX: clientX, pointerY: clientY, left: clientX + gap, top: clientY + gap};
}

function fittedPreviewAnchor(anchor: PreviewAnchor, width: number, height: number): Pick<PreviewAnchor, 'left' | 'top'> {
  const gap = 14;
  const padding = 12;
  if (typeof window === 'undefined') return {left: anchor.left, top: anchor.top};
  const availableRight = Math.max(padding, window.innerWidth - width - padding);
  const availableBottom = Math.max(padding, window.innerHeight - height - padding);
  const preferredLeft = anchor.pointerX + gap + width <= window.innerWidth - padding
    ? anchor.pointerX + gap
    : anchor.pointerX - width - gap;
  const preferredTop = anchor.pointerY + gap + height <= window.innerHeight - padding
    ? anchor.pointerY + gap
    : anchor.pointerY - height - gap;
  return {
    left: Math.min(Math.max(padding, preferredLeft), availableRight),
    top: Math.min(Math.max(padding, preferredTop), availableBottom),
  };
}

export function AgentCollaborationCanvas({
  snapshot,
}: {
  snapshot: TeamSnapshot;
}): React.JSX.Element {
  const canvasRef = useRef<HTMLElement>(null);
  const detailPopoverRef = useRef<HTMLElement>(null);
  const [canvasWidth, setCanvasWidth] = useState(DEFAULT_CANVAS_WIDTH);
  const [hoveredAgentId, setHoveredAgentId] = useState<string | null>(null);
  const [detailAnchor, setDetailAnchor] = useState<PreviewAnchor | null>(null);
  const normalized = snapshot;
  const roster = activeRoster(normalized);
  const {positions, height} = positionsFor(roster, canvasWidth);
  const agentById = new Map(roster.map((agent) => [agent.agent_id, agent]));
  const accentById = new Map(roster.map((agent, index) => [agent.agent_id, stableAccent(agent, index)]));
  const colorOf = (agentId: string) => accentById.get(agentId) ?? '#2f80b9';
  const edgeAccent = (fromAgentId: string, toAgentId: string) => {
    const from = agentById.get(fromAgentId);
    const memberId = from?.authority === 'coordinator' ? toAgentId : fromAgentId;
    return accentById.get(memberId) ?? '#2f80b9';
  };
  const gradientId = (kind: 'rel' | 'act', key: string) => `agent-edge-${kind}-${safeId(key)}`;
  const makeEdge = (fromAgentId: string, toAgentId: string, from: Point, to: Point) => ({
    fromAgentId,
    toAgentId,
    start: connectionStart(from, to),
    end: connectionEnd(from, to),
    path: connectionPath(from, to),
    fromColor: colorOf(fromAgentId),
    toColor: colorOf(toAgentId),
    accent: edgeAccent(fromAgentId, toAgentId),
  });
  type CanvasEdge = ReturnType<typeof makeEdge> & {key: string};
  const activePathKeys = new Set<string>();
  const activePaths: CanvasEdge[] = [];
  for (const interaction of normalized.interactions.filter((item) => item.state === 'active')) {
    const key = `${interaction.from_agent_id}:${interaction.to_agent_id}`;
    if (activePathKeys.has(key)) continue;
    const from = positions.get(interaction.from_agent_id);
    const to = positions.get(interaction.to_agent_id);
    if (!from || !to) continue;
    activePathKeys.add(key);
    activePaths.push({key, ...makeEdge(interaction.from_agent_id, interaction.to_agent_id, from, to)});
  }
  const relationshipKeys = new Set<string>();
  const relationshipPaths: CanvasEdge[] = [];
  for (const relation of [...normalized.dependencies, ...normalized.interactions.filter((item) => item.state === 'completed')]) {
    const key = `${relation.from_agent_id}:${relation.to_agent_id}`;
    if (relationshipKeys.has(key)) continue;
    const from = positions.get(relation.from_agent_id);
    const to = positions.get(relation.to_agent_id);
    if (!from || !to) continue;
    relationshipKeys.add(key);
    relationshipPaths.push({key, ...makeEdge(relation.from_agent_id, relation.to_agent_id, from, to)});
  }
  const coordinator = roster.find((agent) => agent.authority === 'coordinator');
  for (const agent of roster.filter((item) => item.authority !== 'coordinator')) {
    if (!coordinator) break;
    const key = `${coordinator.agent_id}:${agent.agent_id}`;
    if (activePathKeys.has(key) || relationshipKeys.has(key)) continue;
    const from = positions.get(coordinator.agent_id);
    const to = positions.get(agent.agent_id);
    if (!from || !to) continue;
    relationshipKeys.add(key);
    relationshipPaths.push({key, ...makeEdge(coordinator.agent_id, agent.agent_id, from, to)});
  }

  useEffect(() => {
    const element = canvasRef.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const update = () => setCanvasWidth(Math.max(360, Math.floor(element.clientWidth)));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const hoveredAgent = roster.find((agent) => agent.agent_id === hoveredAgentId) ?? null;
  const hoveredCopy = hoveredAgent ? nodeTaskSummary(snapshot, hoveredAgent) : null;
  const darkPopover = Boolean(canvasRef.current?.closest('.theme-dark'));
  useClientLayoutEffect(() => {
    const popover = detailPopoverRef.current;
    if (!popover || !detailAnchor) return;
    const bounds = popover.getBoundingClientRect();
    const fitted = fittedPreviewAnchor(detailAnchor, bounds.width, bounds.height);
    if (Math.abs(fitted.left - detailAnchor.left) < 1 && Math.abs(fitted.top - detailAnchor.top) < 1) return;
    setDetailAnchor((current) => current
      && current.pointerX === detailAnchor.pointerX
      && current.pointerY === detailAnchor.pointerY
      ? {...current, ...fitted}
      : current);
  }, [detailAnchor, hoveredAgentId, hoveredCopy?.objective, hoveredCopy?.workingOn]);
  const detailPopover = hoveredAgent && hoveredCopy && detailAnchor && typeof document !== 'undefined'
    ? createPortal(<aside
      ref={detailPopoverRef}
      className={`agent-node-popover${darkPopover ? ' theme-dark-popover' : ''}`}
      style={{left: detailAnchor.left, top: detailAnchor.top}}
      role="tooltip"
    >
      <header><strong>{hoveredAgent.displayRole}</strong></header>
      <dl>
        <div><dt>Objective</dt><dd>{hoveredCopy.objective}</dd></div>
        <div><dt>Working on</dt><dd>{hoveredCopy.workingOn}</dd></div>
      </dl>
    </aside>, document.body)
    : null;

  return <section className="agent-canvas" aria-label="Active OceanMind Team" ref={canvasRef}>
    <svg viewBox={`0 0 ${canvasWidth} ${height}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label="OceanMind professional team collaboration topology">
      <defs>
        {relationshipPaths.map((edge) => <linearGradient
          key={gradientId('rel', edge.key)}
          id={gradientId('rel', edge.key)}
          gradientUnits="userSpaceOnUse"
          x1={edge.start.x} y1={edge.start.y} x2={edge.end.x} y2={edge.end.y}
        >
          <stop offset="0%" stopColor={edge.fromColor} />
          <stop offset="100%" stopColor={edge.toColor} />
        </linearGradient>)}
        {activePaths.map((edge) => <linearGradient
          key={gradientId('act', edge.key)}
          id={gradientId('act', edge.key)}
          gradientUnits="userSpaceOnUse"
          x1={edge.start.x} y1={edge.start.y} x2={edge.end.x} y2={edge.end.y}
        >
          <stop offset="0%" stopColor={edge.fromColor} />
          <stop offset="100%" stopColor={edge.toColor} />
        </linearGradient>)}
      </defs>
      <g className="agent-edges">
        {relationshipPaths.map((edge) => <g className="agent-relationship" key={edge.key} style={{'--agent-accent': edge.accent} as React.CSSProperties}>
          <path className="agent-edge completed" d={edge.path} stroke={`url(#${gradientId('rel', edge.key)})`} />
          <circle className="agent-edge-endpoint" cx={edge.end.x} cy={edge.end.y} r="2.6" />
        </g>)}
        {activePaths.map((edge) => <g className="agent-active-exchange" key={edge.key} style={{'--agent-accent': edge.accent} as React.CSSProperties}>
          <path className="agent-edge active" d={edge.path} stroke={`url(#${gradientId('act', edge.key)})`} />
          <path className="agent-edge-flow" d={edge.path} pathLength={100} />
          <circle className="agent-edge-source" cx={edge.start.x} cy={edge.start.y} r="3" />
          <circle className="agent-edge-pulse" cx={edge.end.x} cy={edge.end.y} r="4" />
          <circle className="agent-edge-endpoint" cx={edge.end.x} cy={edge.end.y} r="3" />
          {['0s', '.87s', '1.73s'].map((begin) => <circle className="agent-flow-particle" r="2.4" key={`${edge.key}-${begin}`}>
            <animateMotion dur="2.6s" begin={begin} repeatCount="indefinite" path={edge.path} />
          </circle>)}
        </g>)}
      </g>
      <g className="agent-nodes">
        {roster.map((agent, index) => {
          const point = positions.get(agent.agent_id) ?? {x: canvasWidth / 2, y: height / 2};
          const role = agent.displayRole;
          const task = nodeTaskSummary(snapshot, agent);
          const accent = accentById.get(agent.agent_id) ?? '#2f80b9';
          return <g
            key={agent.agent_id}
            className={`agent-node authority-${agent.authority} status-${agent.status}`}
            transform={`translate(${point.x}, ${point.y})`}
            style={{animationDelay: `${index * 55}ms`, '--agent-accent': accent} as React.CSSProperties}
            tabIndex={0}
            aria-label={`${role}. Objective: ${task.objective}. Working on: ${task.workingOn}.`}
            onMouseEnter={(event) => {
              setHoveredAgentId(agent.agent_id);
              setDetailAnchor(previewAnchor(event.clientX, event.clientY));
            }}
            onMouseLeave={() => {
              setHoveredAgentId((current) => current === agent.agent_id ? null : current);
              setDetailAnchor(null);
            }}
            onFocus={(event) => {
              const bounds = event.currentTarget.getBoundingClientRect();
              setHoveredAgentId(agent.agent_id);
              setDetailAnchor(previewAnchor(bounds.right, bounds.top));
            }}
            onBlur={() => {
              setHoveredAgentId((current) => current === agent.agent_id ? null : current);
              setDetailAnchor(null);
            }}
          >
            <rect className="agent-node-card" x={-NODE_WIDTH / 2} y={-NODE_HEIGHT / 2} width={NODE_WIDTH} height={NODE_HEIGHT} rx="13" />
            <foreignObject className="agent-node-copy-object" x={-NODE_WIDTH / 2 + 10} y={-NODE_HEIGHT / 2 + 9} width={NODE_WIDTH - 20} height={NODE_HEIGHT - 18}>
              <div className="agent-node-copy">
                <i className="agent-node-avatar">
                  {agent.authority === 'coordinator' ? <CoordinatorRoleIcon /> : <ExpertRoleIcon />}
                  <em />
                </i>
                <div className="agent-node-identity">
                  <strong>{role}</strong>
                  <small>{statusLabel(agent.status)}</small>
                </div>
              </div>
            </foreignObject>
          </g>;
        })}
      </g>
    </svg>
    {detailPopover}
  </section>;
}

export function timerClock(milliseconds: number): string {
  const totalSeconds = Math.floor(milliseconds / 1_000);
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const hours = Math.floor(totalSeconds / 3_600);
  return hours
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${String(Math.floor(totalSeconds / 60)).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function timerDuration(milliseconds: number): string {
  const totalSeconds = Math.floor(milliseconds / 1_000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}
