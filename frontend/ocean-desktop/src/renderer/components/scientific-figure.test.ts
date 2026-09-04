import {describe, expect, it} from 'vitest';

import {structuredViewKind} from './InteractiveViewWorkbench.js';
import {normalizeScientificFigure, type ScientificFigurePayload} from './scientific-figure.js';

describe('scientific figure normalization', () => {
  it('repairs ISO date axes that were incorrectly declared linear', () => {
    const payload: ScientificFigurePayload = {
      schema_version: 'ocean-scientific-figure/v4',
      plot_kind: 'hovmoller',
      data: {
        date: ['2025-04-01T00:00:00.000', '2025-05-01T00:00:00.000'],
        depth: [0, 50],
        value: [28, 27, 20, 19],
      },
      panels: [{
        id: 'main',
        axes: {x: {field: 'date', scale: 'linear'}, y: {field: 'depth', scale: 'linear'}},
        layers: [{type: 'field2d', x: 'date', y: 'depth', z: 'value'}],
      }],
    };

    const normalized = normalizeScientificFigure(payload);
    expect(normalized.panels[0]?.axes.x).toMatchObject({scale: 'time', tick_format: 'date'});
  });

  it('labels a field layer by its visual semantics instead of a stale scatter kind', () => {
    const payload: ScientificFigurePayload = {
      schema_version: 'ocean-scientific-figure/v4',
      plot_kind: 'scatter',
      data: {x: [1, 2], y: [1, 2], z: [1, 2, 3, 4]},
      panels: [{
        id: 'main',
        axes: {x: {field: 'x'}, y: {field: 'y'}},
        layers: [{type: 'field2d', x: 'x', y: 'y', z: 'z'}],
      }],
    };

    expect(structuredViewKind(payload)).toBe('density_field');
  });
});
