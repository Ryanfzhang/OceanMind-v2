export type PaperSelectionOption = {
  paperId: string;
  title: string;
  topic: string;
  evidenceScope?: 'metadata_only' | 'abstract' | 'public_excerpt';
  evidenceSummary?: string;
  validationTarget?: string;
  citation?: string;
  url?: string;
};

export type PendingInteraction = {
  interactionId: string;
  question: string;
  kind: 'question' | 'permission' | 'paper_selection';
  options: PaperSelectionOption[];
};

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Normalize an active interaction event or a pending task-snapshot record. */
export function pendingInteractionFromPayload(value: unknown): PendingInteraction | null {
  if (!isRecord(value)) return null;
  const interactionId = value.interaction_id;
  const question = value.question;
  const kind = value.kind;
  if (
    typeof interactionId !== 'string'
    || typeof question !== 'string'
    || !question.trim()
    || (kind !== 'question' && kind !== 'permission' && kind !== 'paper_selection')
    || ('state' in value && value.state !== 'pending')
  ) {
    return null;
  }
  const rawOptions = value.options;
  const options = Array.isArray(rawOptions) ? rawOptions.flatMap((option): PaperSelectionOption[] => {
    if (!isRecord(option)) return [];
    const paperId = option.paper_id;
    const title = option.title;
    const topic = option.topic;
    const evidenceScope = option.evidence_scope;
    if (typeof paperId !== 'string' || typeof title !== 'string' || typeof topic !== 'string') return [];
    return [{
      paperId,
      title,
      topic,
      ...(evidenceScope === 'metadata_only' || evidenceScope === 'abstract' || evidenceScope === 'public_excerpt'
        ? {evidenceScope}
        : {}),
      ...(typeof option.evidence_summary === 'string' ? {evidenceSummary: option.evidence_summary} : {}),
      ...(typeof option.validation_target === 'string' ? {validationTarget: option.validation_target} : {}),
      ...(typeof option.citation === 'string' ? {citation: option.citation} : {}),
      ...(typeof option.url === 'string' ? {url: option.url} : {}),
    }];
  }) : [];
  if (kind === 'paper_selection' && (!Array.isArray(rawOptions) || !options.length || options.length !== rawOptions.length)) return null;
  if (kind !== 'paper_selection' && options.length) return null;
  return {interactionId, question, kind, options};
}
