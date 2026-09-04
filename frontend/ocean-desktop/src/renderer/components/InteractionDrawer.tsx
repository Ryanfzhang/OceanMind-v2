import {useEffect, useState} from 'react';
import {BookOpenCheck, Check, MessageSquare, SendHorizontal, X} from 'lucide-react';

import type {PendingInteraction} from '../pending-interaction.js';
import {useUiLanguage} from '../i18n.js';

type InteractionDrawerProps = {
  interaction: PendingInteraction;
  answer: string;
  onAnswer: (answer: string) => void;
  onSubmit: (answer?: string) => void;
};

export function InteractionDrawer({interaction, answer, onAnswer, onSubmit}: InteractionDrawerProps): React.JSX.Element {
  const {text} = useUiLanguage();
  const permission = interaction.kind === 'permission';
  const paperSelection = interaction.kind === 'paper_selection';
  const [selectedPaperIds, setSelectedPaperIds] = useState<string[]>([]);

  useEffect(() => setSelectedPaperIds([]), [interaction.interactionId]);

  if (paperSelection) {
    const allSelected = selectedPaperIds.length === interaction.options.length;
    const togglePaper = (paperId: string): void => {
      setSelectedPaperIds((current) => current.includes(paperId)
        ? current.filter((value) => value !== paperId)
        : [...current, paperId]);
    };
    const toggleAll = (): void => setSelectedPaperIds(allSelected
      ? []
      : interaction.options.map((option) => option.paperId));

    return <section className="interaction-drawer paper-selection-drawer" role="region" aria-labelledby="interaction-drawer-title" aria-live="polite">
      <header>
        <BookOpenCheck size={17} />
        <div>
          <strong id="interaction-drawer-title">{text('Choose papers', '选择论文')}</strong>
          <small>{text('Select the evidence OceanMind should use next.', '选择 OceanMind 下一步要使用的论文。')}</small>
        </div>
        <span className="paper-selection-count">{selectedPaperIds.length}/{interaction.options.length}</span>
      </header>
      <p className="interaction-question">{interaction.question}</p>
      <div className="paper-selection-table" role="table" aria-label={text('Candidate papers', '候选论文')}>
        <div className="paper-selection-row paper-selection-head" role="row">
          <span role="columnheader">{text('Paper', '论文')}</span>
          <label role="columnheader">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              aria-label={text('Select all papers', '选择全部论文')}
            />
            <span>{text('Select', '选择')}</span>
          </label>
        </div>
        <div className="paper-selection-body">
          {interaction.options.map((paper) => <label className="paper-selection-row" role="row" key={paper.paperId}>
            <span className="paper-selection-paper" role="cell">
              <strong>{paper.title}</strong>
            </span>
            <span className="paper-selection-check" role="cell">
              <input
                type="checkbox"
                checked={selectedPaperIds.includes(paper.paperId)}
                onChange={() => togglePaper(paper.paperId)}
                aria-label={text(`Select ${paper.title}`, `选择《${paper.title}》`)}
              />
            </span>
          </label>)}
        </div>
      </div>
      <div className="interaction-drawer-actions paper-selection-actions">
        <span>{selectedPaperIds.length
          ? text(`${selectedPaperIds.length} selected`, `已选择 ${selectedPaperIds.length} 篇`)
          : text('Select at least one paper', '请至少选择一篇论文')}</span>
        <button
          type="button"
          className="primary"
          disabled={!selectedPaperIds.length}
          onClick={() => onSubmit(JSON.stringify({selected_paper_ids: selectedPaperIds}))}
        >
          <Check size={14} />{text('Use selected papers and continue', '使用所选论文并继续')}
        </button>
      </div>
    </section>;
  }

  return <section className="interaction-drawer" role="region" aria-labelledby="interaction-drawer-title" aria-live="polite">
    <header>
      <MessageSquare size={16} />
      <div><strong id="interaction-drawer-title">{permission ? text('Allow analysis execution', '允许执行分析') : text('One detail needed', '需要补充一个细节')}</strong><small>{permission ? text('Review this action before OceanMind continues.', '请在 OceanMind 继续前检查此操作。') : text('Reply here to continue the same task.', '在这里回复以继续同一任务。')}</small></div>
    </header>
    <p className="interaction-question">{interaction.question}</p>
    {permission ? <div className="interaction-drawer-actions">
      <button type="button" onClick={() => onSubmit('deny')}><X size={14} />{text('Deny', '拒绝')}</button>
      <button type="button" className="primary" onClick={() => onSubmit('allow')}><Check size={14} />{text('Allow', '允许')}</button>
    </div> : <form onSubmit={(event) => {event.preventDefault(); onSubmit();}}>
      <div>
        <input
          id="interaction-answer"
          autoFocus
          value={answer}
          onChange={(event) => onAnswer(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onSubmit();
            }
          }}
          placeholder={text('Reply to OceanMind', '回复 OceanMind')}
          aria-label={text('Answer to OceanMind', '回复 OceanMind')}
        />
        <button type="submit" className="primary" disabled={!answer.trim()} title={text('Send reply', '发送回复')} aria-label={text('Send reply', '发送回复')}><SendHorizontal size={15} /></button>
      </div>
    </form>}
  </section>;
}
