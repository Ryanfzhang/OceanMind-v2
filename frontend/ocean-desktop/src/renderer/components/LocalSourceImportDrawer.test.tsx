import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';

import {LocalSourceImportDrawer} from './LocalSourceImportDrawer.js';

describe('LocalSourceImportDrawer', () => {
  it('renders dataset confirmation as a non-modal composer drawer', () => {
    const markup = renderToStaticMarkup(<LocalSourceImportDrawer
      pending={{kind: 'dataset', relativePath: 'imports/CMEMS_oceanmind'}}
      title="CMEMS_oceanmind"
      authors=""
      year=""
      doi=""
      busy={false}
      onTitle={() => {}}
      onAuthors={() => {}}
      onYear={() => {}}
      onDoi={() => {}}
      onCancel={() => {}}
      onConfirm={() => {}}
    />);

    expect(markup).toContain('class="local-source-drawer"');
    expect(markup).toContain('data-kind="dataset"');
    expect(markup).toContain('aria-label="Import local dataset"');
    expect(markup).not.toContain('aria-modal');
    expect(markup).not.toContain('import-dialog-backdrop');
    expect(markup).toContain('CMEMS_oceanmind');
    expect(markup).not.toContain('Local dataset folder');
    expect(markup).toContain('aria-label="Display name"');
    expect(markup).not.toContain('imports/CMEMS_oceanmind</strong>');
    expect(markup).toContain('class="local-source-confirm"');
  });

  it('keeps paper metadata fields inside the same drawer', () => {
    const markup = renderToStaticMarkup(<LocalSourceImportDrawer
      pending={{kind: 'paper', relativePath: 'papers/study.pdf'}}
      title="Study"
      authors="Ocean Scientist"
      year="2026"
      doi="10.1000/example"
      busy={false}
      onTitle={() => {}}
      onAuthors={() => {}}
      onYear={() => {}}
      onDoi={() => {}}
      onCancel={() => {}}
      onConfirm={() => {}}
    />);

    expect(markup).not.toContain('Local paper');
    expect(markup).toContain('Authors');
    expect(markup).toContain('Year');
    expect(markup).toContain('DOI');
  });
});
