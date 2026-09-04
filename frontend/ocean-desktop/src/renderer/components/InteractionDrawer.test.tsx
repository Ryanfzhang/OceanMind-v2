import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';

import {InteractionDrawer} from './InteractionDrawer.js';

describe('InteractionDrawer', () => {
  it('renders a question as a non-modal composer drawer', () => {
    const markup = renderToStaticMarkup(<InteractionDrawer
      interaction={{interactionId: 'interaction_1', kind: 'question', question: 'Which region should be downloaded?', options: []}}
      answer="Gulf of Mexico"
      onAnswer={() => {}}
      onSubmit={() => {}}
    />);

    expect(markup).toContain('class="interaction-drawer"');
    expect(markup).toContain('role="region"');
    expect(markup).not.toContain('aria-modal');
    expect(markup).toContain('One detail needed');
    expect(markup).toContain('Which region should be downloaded?');
    expect(markup).toContain('aria-label="Answer to OceanMind"');
  });

  it('renders permission choices without a free-form answer', () => {
    const markup = renderToStaticMarkup(<InteractionDrawer
      interaction={{interactionId: 'interaction_2', kind: 'permission', question: 'Run the analysis now?', options: []}}
      answer=""
      onAnswer={() => {}}
      onSubmit={() => {}}
    />);

    expect(markup).toContain('Allow analysis execution');
    expect(markup).toContain('Deny');
    expect(markup).toContain('Allow');
    expect(markup).not.toContain('aria-label="Answer to OceanMind"');
  });

  it('renders a compact title-and-checkbox paper shortlist', () => {
    const markup = renderToStaticMarkup(<InteractionDrawer
      interaction={{
        interactionId: 'interaction_3',
        kind: 'paper_selection',
        question: 'Which papers should be reviewed in full?',
        options: [
          {
            paperId: 'paper_loop_current',
            title: 'Loop Current variability in the Gulf of Mexico',
            topic: 'Upper-ocean heat transport',
            evidenceScope: 'abstract',
            evidenceSummary: 'The abstract reports a seasonal heat-transport comparison.',
            validationTarget: 'Compare the reported seasonal contrast with the local temperature field.',
            citation: 'Example et al. (2025)',
          },
          {
            paperId: 'paper_deep_water',
            title: 'Deep-water renewal pathways',
            topic: 'Water-mass structure',
            evidenceScope: 'public_excerpt',
            evidenceSummary: 'A public results excerpt identifies the renewal-depth range.',
            validationTarget: 'Test whether the same depth range appears in the local profiles.',
          },
        ],
      }}
      answer=""
      onAnswer={() => {}}
      onSubmit={() => {}}
    />);

    expect(markup).toContain('Choose papers');
    expect(markup).toContain('Paper');
    expect(markup).toContain('Select');
    expect(markup).toContain('Loop Current variability in the Gulf of Mexico');
    expect(markup).not.toContain('Candidate evidence');
    expect(markup).not.toContain('Abstract reviewed');
    expect(markup).not.toContain('Compare the reported seasonal contrast');
    expect(markup).toContain('Use selected papers and continue');
  });
});
