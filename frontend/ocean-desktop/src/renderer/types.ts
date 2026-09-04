import type {RequestContext as ProtocolRequestContext} from '../../../packages/ocean-client/src/generated/protocol-v2.js';

export type EventPayload = Record<string, unknown>;
export type RequestContext = ProtocolRequestContext & {workspace_id: string};
export type ArtifactRef = {artifact_id: string; version: number};
export type ArtifactSummary = {
  ref: ArtifactRef;
  artifact_type: string;
  title: string;
  summary?: string;
};
export type ArtifactFile = {
  uri?: string;
  mime_type?: string;
  size_bytes?: number;
  sha256?: string;
};
export type ArtifactVersion = ArtifactSummary & {
  content: EventPayload;
  provenance?: EventPayload;
  files?: ArtifactFile[];
};
export type DisclosurePolicy = {
  provider_id: string;
  metadata: string;
  aggregate_statistics: string;
  raw_bounded_sample: string;
  document_text: string;
  diagnostic_excerpt: string;
  confirmed: boolean;
};
export type Workspace = {
  workspace_id: string;
  path: string | null;
  revision: number;
  artifacts: ArtifactSummary[];
  disclosure_policy?: DisclosurePolicy | null;
};
export type TaskWorkflow = {
  request_id: string;
  state: 'planning' | 'working' | 'completed' | 'incomplete' | 'failed' | 'cancelled';
  activity: string;
};
export type ResearchTask = {
  task_id: string;
  workspace_id: string;
  title: string;
  status: 'active' | 'completed' | 'archived';
  task_revision: number;
  active_request_id?: string | null;
  conversation_generation: number;
  updated_at?: string;
  workflow?: TaskWorkflow | null;
};
export type TaskSource = {
  artifact: ArtifactSummary;
  relation: 'source';
  origin_request_id?: string | null;
  origin_request_ids?: string[];
  linked_at?: string;
};
export type TranscriptItem = {
  item_id: string;
  role: 'user' | 'assistant' | 'tool' | 'system';
  text: string;
  sequence?: number;
  request_id?: string | null;
  turn_id?: string | null;
  tool_call_id?: string | null;
  interrupted?: boolean;
  created_at?: string;
};
export type TaskOutput = {
  artifact: ArtifactSummary;
  relation: string;
  origin_request_id?: string | null;
  origin_request_ids?: string[];
  linked_at?: string;
};
export type DeliveryEntry = {
  entry_id: string;
  kind: 'interactive_view' | 'report';
  title: string;
  open_ref: ArtifactRef;
  artifact_type: 'interactive_view' | 'report';
  placement: 'inline' | 'end';
  capabilities: string[];
  openable: boolean;
};
export type DeliveryManifest = {request_id: string; entries: DeliveryEntry[]};
export type LegacyTaskResultEntry = {entry: DeliveryEntry; output: TaskOutput};
export type TaskResultRef = {task_id: string; result_id: string; version: number};
export type TaskResultFile = {
  path: string;
  mime_type: string;
  size: number;
  sha256: string;
};
export type TaskResultRecord = {
  result_ref: TaskResultRef;
  workspace_id: string;
  kind: 'interactive_view' | 'report' | 'file' | 'table';
  title: string;
  summary: string;
  created_at: string;
  origin_request_id?: string | null;
  work_order_id?: string | null;
  execution_id?: string | null;
  execution_output_names?: string[];
  source_refs?: EventPayload[];
  content: EventPayload;
  files: TaskResultFile[];
};
export type ResultDocument = {
  key: string;
  title: string;
  summary?: string;
  content: EventPayload;
};
export type TeamAgent = {
  agent_id: string;
  profile_id?: string | null;
  expert_key?: string | null;
  semantic_role: string;
  authority: 'coordinator' | 'expert' | 'discussion';
  status: 'planning' | 'working' | 'waiting' | 'discussing' | 'recovering' | 'completed' | 'incomplete' | 'blocked' | 'failed' | 'skipped';
  activity: string;
  work_order_id?: string | null;
  task_goal?: string | null;
  result_summary?: string | null;
  limitations?: string[];
};
export type TeamAgentProfile = {
  profile_id: string;
  display_name: string;
  authority: 'expert' | 'discussion';
  category: 'framing' | 'data' | 'science' | 'methods' | 'evidence' | 'visualization' | 'discussion';
  summary: string;
};
export type TeamTodo = {
  todo_id: string;
  question: string;
  depends_on: string[];
  profile_id: string;
  expert_key?: string | null;
  expected_outputs: string[];
  state: 'pending' | 'queued' | 'working' | 'result_returned' | 'stopped' | 'skipped';
  work_order_id?: string | null;
  session_round?: number | null;
  result_summary?: string | null;
};
export type TeamAgentTranscriptBlock =
  | {type: 'text'; text: string}
  | {type: 'tool_call'; tool_call_id: string; tool_name: string; input: EventPayload}
  | {type: 'tool_result'; tool_call_id: string; text: string; is_error: boolean}
  | {type: 'attachment'; media_type: string; source_path: string};
export type TeamAgentTranscriptMessage = {
  message_id: string;
  role: 'user' | 'coordinator' | 'expert' | 'tool' | 'system';
  blocks: TeamAgentTranscriptBlock[];
  created_at?: string;
  interrupted?: boolean;
};
export type TeamAgentTranscript = {
  agent_id: string;
  work_order_id?: string | null;
  messages: TeamAgentTranscriptMessage[];
  compaction_generation: number;
  updated_at?: string | null;
};
export type TeamSnapshot = {
  request_id?: string;
  revision: number;
  status: 'working' | 'completed' | 'incomplete' | 'blocked' | 'failed';
  strategy: 'direct' | 'single_delegate' | 'parallel_team' | 'team_with_discussion';
  role_pool?: TeamAgentProfile[];
  agents: TeamAgent[];
  todos?: TeamTodo[];
  dependencies: Array<{
    from_agent_id: string;
    to_agent_id: string;
    kind: 'delegation' | 'dependency' | 'discussion' | 'feedback';
  }>;
  interactions: Array<{
    interaction_id: string;
    from_agent_id: string;
    to_agent_id: string;
    kind: 'delegation' | 'handoff' | 'discussion' | 'feedback' | 'continuation';
    summary: string;
    state: 'active' | 'completed';
  }>;
};
export type RuntimeConnection = {id: string; label: string; available: boolean};
export type ResearchSkill = {name: string; description: string; version: string};
export type DesktopRuntimeCapabilities = {connections: RuntimeConnection[]; skills: ResearchSkill[]};
export type DisplayDensity = 'comfortable' | 'compact';
export type AppearanceTheme = 'system' | 'light' | 'dark';
export type ResolvedAppearanceTheme = 'light' | 'dark';
export type OceanEvent = {
  event_id?: string;
  type?: string;
  request_id?: string | null;
  task_id?: string | null;
  sequence?: number;
  timestamp?: string;
  workspace_revision?: number;
  payload?: EventPayload;
};

export function refKey(ref: ArtifactRef): string {
  return `${ref.artifact_id}@v${ref.version}`;
}

export function manifestForRequest(
  manifests: DeliveryManifest[],
  requestId: string | null | undefined,
): DeliveryManifest | null {
  if (!requestId) return null;
  return manifests.find((manifest) => manifest.request_id === requestId) ?? null;
}

export function resultsForRequest(
  outputs: TaskOutput[],
  manifests: DeliveryManifest[],
  requestId: string | null | undefined,
): LegacyTaskResultEntry[] {
  const manifest = manifestForRequest(manifests, requestId);
  if (!manifest) return [];
  const byRef = new Map(outputs.map((output) => [refKey(output.artifact.ref), output]));
  return manifest.entries.flatMap((entry) => {
    const output = byRef.get(refKey(entry.open_ref));
    return output && entry.openable ? [{entry, output}] : [];
  });
}

export function taskResultRefKey(ref: TaskResultRef): string {
  return `${ref.task_id}/${ref.result_id}@v${ref.version}`;
}

export function taskResultsForRequest(
  results: TaskResultRecord[],
  requestId: string | null | undefined,
): TaskResultRecord[] {
  if (!requestId) return [];
  return results.filter((result) => result.origin_request_id === requestId);
}
