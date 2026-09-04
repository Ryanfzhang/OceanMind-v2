import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';

import {ReportWorkbench} from './components/ReportWorkbench.js';
import type {ResultDocument} from './types.js';

describe('ReportWorkbench', () => {
  it('renders generated report figures as visual evidence', () => {
    const document = {
      key: 'task_1/report_fixture@v1',
      title: 'Seasonal chlorophyll analysis',
      summary: 'Checked report with figures',
      content: {},
    } as ResultDocument;
    const html = renderToStaticMarkup(<ReportWorkbench
      document={document}
      loading={false}
      markdown="# Result"
      error={null}
      resources={[
        {name: 'attachment_001.png', label: 'Figure 1', url: 'ocean-artifact://resource/res_figure', mimeType: 'image/png'},
        {name: 'analysis.ipynb', label: 'Analysis notebook', url: 'ocean-artifact://resource/res_notebook', mimeType: 'application/x-ipynb+json'},
      ]}
      onClose={() => undefined}
    />);

    expect(html).toContain('aria-label="Report figures"');
    expect(html).toContain('<img src="ocean-artifact://resource/res_figure" alt="Figure 1"');
    expect(html).toContain('Analysis notebook');
    expect(html).toContain('<button type="button"');
    expect(html).not.toContain('href="ocean-artifact://resource/res_notebook"');
  });
});
