import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';

import {MessageMarkdown, safeExternalHref} from './message-markdown.js';

describe('MessageMarkdown', () => {
  it('renders research Markdown into structured, safe reading content', () => {
    const markup = renderToStaticMarkup(
      <MessageMarkdown content={'## Research note\n\n- **Verified** coordinate metadata\n- Compared two monthly means\n\n| Check | State |\n| --- | --- |\n| Units | ready |\n\n```python\nmean_sst = sst.mean("time")\n```\n\n$T = T_0 + T\'$,\n\n<script>alert("not rendered")</script>'} />,
    );

    expect(markup).toContain('<h2>Research note</h2>');
    expect(markup).toContain('<strong>Verified</strong>');
    expect(markup).toContain('<table>');
    expect(markup).toContain('mean_sst');
    expect(markup).toContain('katex');
    expect(markup).not.toContain('<script>');
  });

  it('only allows http(s) links', () => {
    expect(safeExternalHref('https://example.org/data')).toBe('https://example.org/data');
    expect(safeExternalHref('javascript:alert(1)')).toBeNull();
    expect(safeExternalHref('/relative-path')).toBeNull();
  });

  it('turns exact delivered refs into inline workbench links', () => {
    const markup = renderToStaticMarkup(<MessageMarkdown
      content={'Compare `scene_temperature@v1` with the evidence below.'}
      artifactLinks={[{
        ref: {artifact_id: 'scene_temperature', version: 1},
        label: 'Interactive view',
        onOpen: () => {},
      }]}
    />);

    expect(markup).toContain('markdown-artifact-link');
    expect(markup).toContain('Interactive view');
    expect(markup).not.toContain('<code>scene_temperature@v1</code>');
  });

  it('accepts canonical zero-padded artifact keys from backend diagnostics', () => {
    const markup = renderToStaticMarkup(<MessageMarkdown
      content={'Explore `scene_temperature@v0001`.'}
      artifactLinks={[{
        ref: {artifact_id: 'scene_temperature', version: 1},
        label: 'Interactive view',
        onOpen: () => {},
      }]}
    />);

    expect(markup).toContain('markdown-artifact-link');
    expect(markup).not.toContain('<code>scene_temperature@v0001</code>');
  });

  it('renders supporting interactive views as a compact labeled itemized list', () => {
    const markup = renderToStaticMarkup(<MessageMarkdown
      content={'物理判读：结论正文。\n\n[[result:task_1/result_theta@v1]]（θ 表层年均） · [[result:task_1/result_salt@v1]]（S 表层年均）'}
      resultLinks={[
        {
          keys: ['task_1/result_theta@v1'],
          label: 'θ 表层年均水平分布（CMEMS1）',
          summary: '365 日等权平均。',
          kind: 'interactive_view',
          onOpen: () => {},
        },
        {
          keys: ['task_1/result_salt@v1'],
          label: 'S 表层年均水平分布（CMEMS1）',
          kind: 'interactive_view',
          onOpen: () => {},
        },
      ]}
    />);

    expect(markup).toContain('<ul>');
    expect(markup).toContain('>θ 表层年均</button>');
    expect(markup).toContain('>S 表层年均</button>');
    expect(markup).not.toContain('markdown-result-link__kind');
    expect(markup).not.toContain('ocean-result-');
  });

  it('never exposes internal result tokens when an old supporting result is unavailable', () => {
    const markup = renderToStaticMarkup(<MessageMarkdown
      content={'- [[result:task_1/missing@v1]]（温盐剖面）'}
      resultLinks={[]}
    />);

    expect(markup).toContain('markdown-result-unavailable');
    expect(markup).toContain('温盐剖面');
    expect(markup).not.toContain('ocean-result-');
    expect(markup).not.toContain('task_1/missing@v1');
  });

  it('resolves an Expert output path to the published interactive result', () => {
    const markup = renderToStaticMarkup(<MessageMarkdown
      content={'结论由 [[output:outputs/temperature_section.nc|温度断面]] 支持。'}
      resultLinks={[{
        keys: ['outputs/temperature_section.nc'],
        label: 'Temperature section',
        kind: 'interactive_view',
        onOpen: () => {},
      }]}
    />);

    expect(markup).toContain('markdown-result-link');
    expect(markup).toContain('>温度断面</button>');
    expect(markup).not.toContain('outputs/temperature_section.nc');
  });
});
