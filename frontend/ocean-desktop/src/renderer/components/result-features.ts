import type {Geometry} from 'geojson';
import type {LayerSpecification} from 'maplibre-gl';
import polygonClipping, {type MultiPolygon} from 'polygon-clipping';

export type ResultFeature = {id: string; label: string; panel_id?: string; geometry: Geometry};
export const FEATURE_COLORS = ['#d55e00', '#0072b2', '#009e73', '#cc79a7', '#e69f00', '#56b4e9'];

export const MASK_STYLE = {fill: .10, selectedFill: .18, halo: 3.5, selectedHalo: 4.5, edge: 1.5, selectedEdge: 2.5};

/** Union only within an object, in data coordinates. Never mutate saved geometry,
 * bridge gaps, simplify away holes, or dissolve two distinct object identities.
 * Call at payload/memo boundaries, not on selection or pointer updates. */
export function prepareResultFeature(feature: ResultFeature): ResultFeature {
  if (feature.geometry.type !== 'MultiPolygon') return feature;
  return {...feature, geometry: {type: 'MultiPolygon', coordinates:
    polygonClipping.union(feature.geometry.coordinates as MultiPolygon)}};
}

/** Polygon input is dissolved by prepareResultFeature before reaching these layers. */
export function resultFeatureMapLayers(source: string, hasSelection: boolean): LayerSpecification[] {
  return [
    {id: 'ocean-feature-fill', source, type: 'fill', filter: ['==', '$type', 'Polygon'], paint: {
      'fill-color': ['get', 'color'],
      'fill-opacity': ['case', ['get', 'selected'], MASK_STYLE.selectedFill, hasSelection ? .03 : MASK_STYLE.fill],
      // Shared edges of legacy row-run polygons must not become visible seams.
      'fill-antialias': false,
    }},
    {id: 'ocean-feature-halo', source, type: 'line', filter: ['==', '$type', 'Polygon'], layout: {'line-join': 'round'}, paint: {
      'line-color': '#fff',
      'line-width': ['case', ['get', 'selected'], MASK_STYLE.selectedHalo, MASK_STYLE.halo],
      'line-opacity': ['case', ['get', 'selected'], 1, hasSelection ? .45 : 1],
    }},
    {id: 'ocean-feature-boundary', source, type: 'line', filter: ['==', '$type', 'Polygon'], layout: {'line-join': 'round'}, paint: {
      'line-color': ['get', 'color'],
      'line-width': ['case', ['get', 'selected'], MASK_STYLE.selectedEdge, MASK_STYLE.edge],
      'line-opacity': ['case', ['get', 'selected'], 1, hasSelection ? .45 : 1],
    }},
    {id: 'ocean-feature-line', source, type: 'line', filter: ['==', '$type', 'LineString'], paint: {
      'line-color': ['get', 'color'], 'line-width': ['case', ['get', 'selected'], 3, 1.5],
      'line-opacity': ['case', ['get', 'selected'], 1, hasSelection ? .25 : .7],
    }},
    {id: 'ocean-feature-point', source, type: 'circle', filter: ['==', '$type', 'Point'], paint: {
      'circle-color': ['get', 'color'], 'circle-radius': ['case', ['get', 'selected'], 6, 4],
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1,
    }},
  ];
}
export type ResultCategory = {value: number; label: string};
export function categoryColor(index: number): string {
  return FEATURE_COLORS[index % FEATURE_COLORS.length]!;
}

export function featurePoints(feature: ResultFeature): [number, number][] {
  const geometry = feature.geometry;
  if (geometry.type === 'GeometryCollection') return [];
  const points: [number, number][] = [];
  function visit(value: unknown): void {
    if (!Array.isArray(value)) return;
    if (value.length === 2 && value.every(v => typeof v === 'number' && Number.isFinite(v))) points.push(value as [number, number]);
    else value.forEach(visit);
  }
  visit(geometry.coordinates);
  return points;
}

export function featureBounds(feature: ResultFeature): [number, number, number, number] | null {
  const points = featurePoints(feature);
  if (!points.length) return null;
  return points.reduce<[number, number, number, number]>((b, [x, y]) => [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)], [Infinity, Infinity, -Infinity, -Infinity]);
}

/** A label must sit on the object, not in a hole between disconnected components. */
export function featureAnchor(feature: ResultFeature): [number, number] | null {
  const geometry = feature.geometry;
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates]
    : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  if (polygons.length) {
    const area = (ring: number[][]) => Math.abs(ring.slice(1).reduce((sum, p, i) => sum + ring[i]![0]!*p[1]!-p[0]!*ring[i]![1]!, 0));
    const polygon = polygons.reduce((best, p) => area(p[0]!) > area(best[0]!) ? p : best);
    const ring = polygon[0]!;
    const xs = [...new Set(ring.map(p => p[0]!))];
    const ys = [...new Set(ring.map(p => p[1]!))];
    if (polygon.length === 1 && ring.length === 5 && xs.length === 2 && ys.length === 2) {
      return [(xs[0]!+xs[1]!)/2, (ys[0]!+ys[1]!)/2];
    }
    return ring[0] as [number, number]; // Boundary point is valid even for concave regions or holes.
  }
  const points = featurePoints(feature);
  return points[Math.floor(points.length/2)] ?? null;
}

export function resultFeatures(value: unknown): ResultFeature[] {
  if (!value || typeof value !== 'object' || !('features' in value) || !Array.isArray(value.features)) return [];
  return value.features.filter((f): f is ResultFeature => !!f && typeof f.id === 'string' && typeof f.label === 'string' && !!f.geometry);
}
