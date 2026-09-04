import {describe, expect, it} from 'vitest';

import {pendingInteractionFromPayload} from './pending-interaction.js';

describe('pendingInteractionFromPayload', () => {
  it('normalizes a typed paper-selection interaction', () => {
    expect(pendingInteractionFromPayload({
      interaction_id: 'int_papers',
      kind: 'paper_selection',
      question: 'Choose papers',
      options: [{
        paper_id: 'paper_1',
        title: 'A paper',
        topic: 'Shelf circulation',
        evidence_scope: 'abstract',
        evidence_summary: 'The abstract reports a shelf-exchange diagnostic.',
        validation_target: 'Compare the reported direction with the task data.',
        citation: 'Author et al. (2025)',
      }],
      state: 'pending',
    })).toEqual({
      interactionId: 'int_papers',
      kind: 'paper_selection',
      question: 'Choose papers',
      options: [{
        paperId: 'paper_1',
        title: 'A paper',
        topic: 'Shelf circulation',
        evidenceScope: 'abstract',
        evidenceSummary: 'The abstract reports a shelf-exchange diagnostic.',
        validationTarget: 'Compare the reported direction with the task data.',
        citation: 'Author et al. (2025)',
      }],
    });
  });

  it('rejects a paper-selection interaction with a malformed shortlist', () => {
    expect(pendingInteractionFromPayload({
      interaction_id: 'int_bad_papers',
      kind: 'paper_selection',
      question: 'Choose papers',
      options: [{paper_id: 'paper_1', title: 'Missing topic'}],
      state: 'pending',
    })).toBeNull();
  });
});
