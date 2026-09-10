import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';
import {featureAnchor, featureBounds, prepareResultFeature, resultFeatureMapLayers, resultFeatures, type ResultFeature} from './result-features.js';
import {scientificAxisTicks, ScientificView} from './ScientificView.js';
import {spatialCategories, sampleSpatialGrid} from './SpatialWorkbench.js';
import type {SpatialPayload} from './InteractiveViewWorkbench.js';

describe('generic result objects', () => {
  it('keeps tick labels distinct when focusing a small region', () => {
    const ticks = scientificAxisTicks([21.98, 22.02], {field: 'y'});
    expect(new Set(ticks.map(tick => tick.label)).size).toBe(ticks.length);
  });
  const region: ResultFeature = {id: 'region', label: 'Computed region', panel_id: 'p', geometry: {type: 'Polygon', coordinates: [[[1, 2], [3, 2], [3, 4], [1, 4], [1, 2]]]}};
  it('derives bounds from geometry and keeps old figures feature-free', () => {
    expect(featureBounds(region)).toEqual([1, 2, 3, 4]);
    expect(resultFeatures({features: [region]})).toEqual([region]);
    expect(resultFeatures({})).toEqual([]);
  });
  it('places a disconnected-region label inside a component instead of in the gap', () => {
    const disconnected: ResultFeature = {...region, geometry: {type: 'MultiPolygon', coordinates: [
      [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
      [[[10, 0], [11, 0], [11, 1], [10, 1], [10, 0]]],
    ]}};
    expect(featureAnchor(disconnected)).toEqual([.5, .5]);
  });
  it('labels actual chart geometry and exposes selection without a task-specific branch', () => {
    const markup = renderToStaticMarkup(<ScientificView featureId="region" payload={{
      schema_version: 'ocean-scientific-figure/v4', plot_kind: 'scatter', title: 'Generic example',
      data: {x: [1, 2, 3], y: [2, 3, 4]}, features: [region],
      panels: [{id: 'p', axes: {x: {field: 'x'}, y: {field: 'y'}}, layers: [{type: 'scatter', x: 'x', y: 'y'}]}],
    }} />);
    expect(markup).toContain('Computed region');
    expect(markup).toContain('data-feature-id="region"');
    expect(markup).toContain('data-selected="true"');
    expect(markup).toContain('stroke="none"');
    expect(markup).not.toMatch(/<text[^>]*>Computed region/);
  });
  it('draws contrasting boundaries above fills in all-object and selected modes', () => {
    for (const hasSelection of [false, true]) {
      const layers = resultFeatureMapLayers('test-source', hasSelection);
      const fill = layers.find(layer => layer.type === 'fill')!;
      expect(fill.paint?.['fill-antialias']).toBe(false);
      expect(fill.paint).not.toHaveProperty('fill-outline-color');
      const halo = layers.find(layer => layer.id === 'ocean-feature-halo')!;
      const boundary = layers.find(layer => layer.id === 'ocean-feature-boundary')!;
      expect(halo.type === 'line' && halo.paint?.['line-color']).toBe('#fff');
      expect(boundary.type === 'line' && boundary.filter).toEqual(['==', '$type', 'Polygon']);
      expect(layers.indexOf(halo)).toBeGreaterThan(layers.indexOf(fill));
      expect(layers.indexOf(boundary)).toBeGreaterThan(layers.indexOf(halo));
      expect(boundary.type === 'line' && boundary.paint?.['line-opacity']).toEqual(['case', ['get', 'selected'], 1, hasSelection ? .45 : 1]);
      const line = layers.find(layer => layer.id === 'ocean-feature-line')!;
      expect(line.type === 'line' && line.filter).toEqual(['==', '$type', 'LineString']);
    }
  });
  const rectangle = (x: number, y: number, w = 1, h = 1): number[][][] => [[[x,y], [x+w,y], [x+w,y+h], [x,y+h], [x,y]]];
  const mask = (coordinates: number[][][][]): ResultFeature => ({...region, geometry: {type: 'MultiPolygon', coordinates}});
  it('dissolves shared and partially shared row edges without changing stored geometry', () => {
    const source = mask([rectangle(0,0,3), rectangle(1,1)]);
    const original = JSON.stringify(source);
    const merged = prepareResultFeature(source);
    expect(JSON.stringify(source)).toBe(original);
    expect(merged.id).toBe(source.id);
    expect(merged.label).toBe(source.label);
    expect(merged.geometry).toEqual({type: 'MultiPolygon', coordinates: [[[[0,0],[3,0],[3,1],[2,1],[2,2],[1,2],[1,1],[0,1],[0,0]]]]});
  });
  it('preserves an excluded hole and disconnected component while removing internal seams', () => {
    const merged = prepareResultFeature(mask([
      rectangle(0,0,3), rectangle(0,1), rectangle(2,1), rectangle(0,2,3), rectangle(10,10),
    ]));
    if (merged.geometry.type !== 'MultiPolygon') throw new Error('Expected mask');
    expect(merged.geometry.coordinates).toHaveLength(2);
    expect(merged.geometry.coordinates[0]).toHaveLength(2); // Exterior plus hole.
    expect(merged.geometry.coordinates[0]![0]).toHaveLength(5); // No row seams.
    expect(merged.geometry.coordinates[0]![1]).toEqual([[1,1],[1,2],[2,2],[2,1],[1,1]]);
    expect(featureBounds(merged)).toEqual([0,0,11,11]);
  });
  it('unions overlapping pieces instead of double tinting them or using an XOR fill', () => {
    expect(prepareResultFeature(mask([rectangle(0,0,2), rectangle(1,0,2)])).geometry)
      .toEqual({type: 'MultiPolygon', coordinates: [rectangle(0,0,3)]});
  });
  it('does not bridge diagonal components or merge different object identities', () => {
    const source = mask([rectangle(0,0), rectangle(1,1)]);
    expect(prepareResultFeature(source).geometry).toEqual(source.geometry);
    const separate = [mask([rectangle(0,0)]), {...mask([rectangle(1,0)]), id: 'other'}].map(prepareResultFeature);
    expect(separate.map(f => f.id)).toEqual(['region', 'other']);
    expect(separate.map(featureBounds)).toEqual([[0,0,1,1], [1,0,2,1]]);
  });
  it('uses the same dissolved outline for chart fills and both boundary strokes', () => {
    const markup = renderToStaticMarkup(<ScientificView payload={{
      schema_version: 'ocean-scientific-figure/v4', plot_kind: 'scatter', title: 'Mask chart',
      data: {x: [0,3], y: [0,3]}, features: [mask([rectangle(0,0,2), rectangle(0,1,2)])],
      panels: [{id: 'p', axes: {x: {field: 'x'}, y: {field: 'y'}}, layers: [{type: 'scatter', x: 'x', y: 'y'}]}],
    }} />);
    const paths = [...markup.matchAll(/data-mask-part="(fill|halo|boundary)" d="([^"]+)"/g)];
    expect(paths.map(p => p[1])).toEqual(['fill', 'halo', 'boundary']);
    expect(new Set(paths.map(p => p[2])).size).toBe(1);
    expect(paths[0]![2]!.match(/M/g)).toHaveLength(1);
    expect(markup).toContain('stroke="#d55e00"');
  });
  it('uses discrete labels for nonconsecutive codes and excludes missing cells', () => {
    const payload: SpatialPayload = {schema_version: 'ocean-interactive-spatial/v1', view_kind: 'spatial_map', variable: 'class', units: '1', longitude: [1, 2], latitude: [3, 4], values: [[4, null], [8, 4]], bounds: [1, 3, 2, 4], rendering: {kind: 'categorical'}, categories: [{value: 4, label: 'First group'}, {value: 8, label: 'Second group'}]};
    expect(spatialCategories(payload).map(({value, label}) => ({value, label}))).toEqual(payload.categories);
    expect(sampleSpatialGrid(payload, 1.8, 3.8, 'nearest')).toBe(4);
    expect(sampleSpatialGrid(payload, 2, 3, 'nearest')).toBeNull();
  });
});
