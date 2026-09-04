import {Component, useRef, useState, type ReactNode} from 'react';
import {BarChart3, Image as ImageIcon, LoaderCircle, Maximize2, Minus, Plus, X} from 'lucide-react';
import type {FeatureCollection, Geometry} from 'geojson';

import {asRecord} from '../app-utils.js';
import type {ArtifactVersion, EventPayload} from '../types.js';
import {
  isScientificPayload,
  ScientificView,
  type ScientificLayer,
  type ScientificPayload,
  type ScientificScalar,
} from './ScientificView.js';

export type SpatialContext = {
  region_key: string;
  bounds?: [number, number, number, number];
  fit_policy?: 'region_change' | 'always' | 'never';
  features?: FeatureCollection<Geometry>;
};

export type SpatialPayload = {
  schema_version: 'ocean-interactive-spatial/v1';
  view_kind: 'spatial_map';
  variable: string;
  units: string;
  longitude: number[];
  latitude: number[];
  values: Array<Array<number | null>>;
  bounds: [number, number, number, number];
  colorbar?: EventPayload;
  rendering?: {
    kind?: 'continuous' | 'categorical';
    interpolation?: 'linear' | 'nearest';
  };
  spatial_context?: SpatialContext;
};

type LegacyColumn = {
  name: string;
  units?: string;
  values: ScientificScalar[];
  role?: 'estimate' | 'lower_bound' | 'upper_bound' | 'reference';
  group?: string;
  label?: string;
};

export type LegacyStructuredPayload = {
  plot_kind: string;
  axes: LegacyColumn[];
  series: LegacyColumn[];
  shape?: [number, number];
  spatial_context?: SpatialContext;
};

export type StructuredPayload = ScientificPayload | LegacyStructuredPayload;

type ResultRenderBoundaryProps = {children: ReactNode; resetKey?: string};
type ResultRenderBoundaryState = {failed: boolean};

/** Keep one malformed or unexpectedly large result from unmounting the whole
 * desktop renderer. Selecting another result resets the isolated surface.
 */
export class ResultRenderBoundary extends Component<ResultRenderBoundaryProps, ResultRenderBoundaryState> {
  state: ResultRenderBoundaryState = {failed: false};

  static getDerivedStateFromError(): ResultRenderBoundaryState {
    return {failed: true};
  }

  componentDidUpdate(previous: ResultRenderBoundaryProps): void {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) this.setState({failed: false});
  }

  render(): ReactNode {
    if (this.state.failed) return <div className="workbench-error" role="alert"><strong>View could not be rendered</strong><p>The saved result is still available; close this view or open another result.</p></div>;
    return this.props.children;
  }
}

export function isSpatial(value: unknown): value is SpatialPayload {
  const payload = asRecord(value);
  return payload.schema_version === 'ocean-interactive-spatial/v1'
    && payload.view_kind === 'spatial_map'
    && Array.isArray(payload.longitude)
    && Array.isArray(payload.latitude)
    && Array.isArray(payload.values);
}

export function isStructured(value: unknown): value is StructuredPayload {
  if (isScientificPayload(value)) return true;
  const payload = asRecord(value);
  return typeof payload.plot_kind === 'string'
    && Array.isArray(payload.axes)
    && Array.isArray(payload.series);
}

export function structuredViewKind(payload: StructuredPayload): string {
  if (!isScientificPayload(payload)) return payload.plot_kind;
  const layers = payload.schema_version === 'ocean-scientific-view/v1'
    ? payload.layers
    : payload.panels.flatMap((panel) => panel.layers);
  if (layers.some((layer) => layer.type === 'categories')) return 'classified_samples';
  if (payload.plot_kind === 'scatter' && layers.some((layer) => layer.type === 'field2d' || layer.type === 'heatmap')) return 'density_field';
  return payload.plot_kind;
}

const VIEW_LABELS: Record<string, string> = {
  spatial_map: 'Map', time_series: 'Time series', profile: 'Vertical profile',
  scatter: 'Scatter plot', ts_diagram: 'T–S diagram', section: 'Section',
  hovmoller: 'Hovmöller diagram', density_field: 'Density field',
  classified_samples: 'Classified samples',
};

export function viewLabel(kind: string | undefined): string {
  return kind ? VIEW_LABELS[kind] ?? kind.replaceAll('_', ' ') : 'Interactive result';
}

function formatNumber(value: number): string {
  if (Math.abs(value) >= 1_000 || (Math.abs(value) > 0 && Math.abs(value) < .01)) return value.toExponential(3);
  return value.toFixed(3).replace(/\.0+$|(?<=\.[0-9]*?)0+$/g, '');
}

function SpatialView({payload, previewUrl}: {payload: SpatialPayload; previewUrl: string | null}): React.JSX.Element {
  const imageRef = useRef<HTMLImageElement>(null);
  const [hover, setHover] = useState<{lon: number; lat: number; value: number | null} | null>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({x: 0, y: 0});
  const dragRef = useRef<{x: number; y: number; startX: number; startY: number} | null>(null);
  const [west, south, east, north] = payload.bounds;
  const updateHover = (clientX: number, clientY: number) => {
    const image = imageRef.current;
    if (!image) return;
    const rect = image.getBoundingClientRect();
    const x = (clientX - rect.left) / rect.width;
    const y = (clientY - rect.top) / rect.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return setHover(null);
    const column = Math.min(payload.longitude.length - 1, Math.max(0, Math.floor(x * payload.longitude.length)));
    const row = Math.min(payload.latitude.length - 1, Math.max(0, Math.floor(y * payload.latitude.length)));
    setHover({
      lon: payload.longitude[column] ?? west + (east - west) * x,
      lat: payload.latitude[row] ?? north - (north - south) * y,
      value: payload.values[row]?.[column] ?? null,
    });
  };
  return <div className="spatial-view-stage"
    onPointerMove={(event) => {
      if (dragRef.current) {
        setOffset({x: dragRef.current.startX + event.clientX - dragRef.current.x, y: dragRef.current.startY + event.clientY - dragRef.current.y});
        setHover(null);
      } else updateHover(event.clientX, event.clientY);
    }}
    onPointerUp={() => {dragRef.current = null;}}
    onPointerLeave={() => {dragRef.current = null; setHover(null);}}
  >
    <div className="view-controls" role="group" aria-label="Interactive view controls">
      <button onClick={() => setScale((value) => Math.min(5, value + .35))} title="Zoom in"><Plus size={15} /></button>
      <button onClick={() => setScale((value) => Math.max(1, value - .35))} title="Zoom out"><Minus size={15} /></button>
      <button onClick={() => {setScale(1); setOffset({x: 0, y: 0});}} title="Reset view"><Maximize2 size={15} /></button>
    </div>
    {previewUrl ? <img ref={imageRef} src={previewUrl} alt={`${payload.variable} interactive spatial field`} draggable={false}
      style={{transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`}}
      onPointerDown={(event) => {
        if (scale <= 1) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current = {x: event.clientX, y: event.clientY, startX: offset.x, startY: offset.y};
      }}
    /> : <div className="view-preview-missing"><ImageIcon size={30} /><p>Preview is unavailable, but the value grid is loaded.</p></div>}
    <div className="coordinate-corners"><span>{north.toFixed(2)}°N</span><span>{south.toFixed(2)}°N</span></div>
    {hover ? <output className="spatial-hover-readout"><strong>{payload.variable}</strong><span>{formatNumber(hover.lon)}° · {formatNumber(hover.lat)}°</span><b>{hover.value === null ? 'No data' : `${formatNumber(hover.value)} ${payload.units}`}</b></output> : null}
  </div>;
}

function logicalGroup(column: LegacyColumn): string {
  return column.group?.trim() || column.name
    .replace(/(?:ci|confidence)[\s_.-]*(?:lo|low|hi|high)/ig, ' ')
    .replace(/(?:^|[\s_.-])(?:lower|upper)(?=$|[\s_.-])/ig, ' ')
    .replace(/[\s_.-]+$/g, '').replace(/\s+/g, ' ').trim() || column.name;
}

function legacyToScientific(payload: LegacyStructuredPayload): ScientificPayload {
  const data: Record<string, ScientificScalar[]> = {};
  [...payload.axes, ...payload.series].forEach((column) => {data[column.name] = column.values;});
  const firstAxis = payload.axes[0]!;
  const secondAxis = payload.axes[1];
  const firstSeries = payload.series[0];
  const colors = ['#155795', '#d66a2f', '#398f97', '#8d5b93', '#3f6f56', '#aa8430'];

  if (payload.plot_kind === 'section' || payload.plot_kind === 'hovmoller') {
    return {
      schema_version: 'ocean-scientific-view/v1', plot_kind: payload.plot_kind, data,
      axes: {
        x: {field: firstAxis.name, label: firstAxis.name, units: firstAxis.units, scale: typeof firstAxis.values[0] === 'number' ? 'linear' : 'category'},
        y: {field: secondAxis?.name ?? firstAxis.name, label: secondAxis?.name, units: secondAxis?.units, reverse: /depth|pressure/i.test(secondAxis?.name ?? '')},
      },
      layers: firstSeries && secondAxis ? [{type: 'field2d', x: firstAxis.name, y: secondAxis.name, z: firstSeries.name, render: 'filled_contour', interpolation: 'linear', levels: 14, label: firstSeries.label ?? firstSeries.name, style: {palette: 'thermal'}}] : [],
      display: {colorbar_label: firstSeries ? `${firstSeries.label ?? firstSeries.name}${firstSeries.units ? ` (${firstSeries.units})` : ''}` : undefined},
      spatial_context: payload.spatial_context,
    };
  }

  if (payload.plot_kind === 'scatter' || payload.plot_kind === 'ts_diagram') {
    const colorSeries = firstSeries?.values.every((value) => value === null || typeof value === 'number') ? firstSeries : undefined;
    return {
      schema_version: 'ocean-scientific-view/v1', plot_kind: payload.plot_kind, data,
      axes: {
        x: {field: firstAxis.name, label: firstAxis.name, units: firstAxis.units},
        y: {field: secondAxis?.name ?? firstSeries?.name ?? firstAxis.name, label: secondAxis?.name ?? firstSeries?.name, units: secondAxis?.units ?? firstSeries?.units},
      },
      layers: secondAxis ? [{type: 'scatter', x: firstAxis.name, y: secondAxis.name, color: colorSeries?.name, color_scale: /depth/i.test(colorSeries?.name ?? '') ? 'log' : 'linear', label: payload.plot_kind === 'ts_diagram' ? 'Water-column samples' : 'Samples', style: {palette: /depth/i.test(colorSeries?.name ?? '') ? 'depth' : 'default', radius: 2.2, opacity: .42}}] : [],
      display: {colorbar_label: colorSeries ? `${colorSeries.label ?? colorSeries.name}${colorSeries.units ? ` (${colorSeries.units})` : ''}` : undefined},
      spatial_context: payload.spatial_context,
    };
  }

  const isProfile = payload.plot_kind === 'profile';
  const estimates = payload.series.filter((column) => !column.role || column.role === 'estimate' || column.role === 'reference');
  const groups = new Map<string, {estimate?: LegacyColumn; lower?: LegacyColumn; upper?: LegacyColumn}>();
  payload.series.forEach((column) => {
    const group = logicalGroup(column); const entry = groups.get(group) ?? {};
    if (column.role === 'lower_bound') entry.lower = column;
    else if (column.role === 'upper_bound') entry.upper = column;
    else entry.estimate = column;
    groups.set(group, entry);
  });
  const layers: ScientificLayer[] = [];
  [...groups.entries()].forEach(([group, columns], index) => {
    const estimate = columns.estimate; const color = colors[index % colors.length]!;
    if (!isProfile && columns.lower && columns.upper) layers.push({type: 'band', x: firstAxis.name, y0: columns.lower.name, y1: columns.upper.name, label: `${group} interval`, style: {fill: color, fill_opacity: .15}});
    if (estimate) layers.push({type: 'line', x: isProfile ? estimate.name : firstAxis.name, y: isProfile ? firstAxis.name : estimate.name, label: estimate.label ?? group, style: {color, width: estimate.role === 'reference' ? 1.5 : 2.4, dash: estimate.role === 'reference' ? '5 4' : undefined, radius: 2.2}});
  });
  const primary = estimates[0] ?? firstSeries ?? firstAxis;
  return {
    schema_version: 'ocean-scientific-view/v1', plot_kind: payload.plot_kind, data,
    axes: isProfile
      ? {x: {field: primary.name, label: primary.label ?? primary.name, units: primary.units}, y: {field: firstAxis.name, label: firstAxis.name, units: firstAxis.units, reverse: true}}
      : {x: {field: firstAxis.name, label: firstAxis.name, units: firstAxis.units, scale: typeof firstAxis.values[0] === 'number' ? 'linear' : 'category'}, y: {field: primary.name, label: payload.series.length === 1 ? primary.label ?? primary.name : 'Value', units: payload.series.length === 1 ? primary.units : undefined}},
    layers, spatial_context: payload.spatial_context,
  };
}

export function StructuredView({payload, compactHeader = false}: {payload: StructuredPayload; compactHeader?: boolean}): React.JSX.Element {
  return <ScientificView payload={isScientificPayload(payload) ? payload : legacyToScientific(payload)} compactHeader={compactHeader} />;
}

export function InteractiveViewWorkbench({artifact, loading, data, previewUrl, error, onClose}: {
  artifact: ArtifactVersion | null;
  loading: boolean;
  data: unknown;
  previewUrl: string | null;
  error: string | null;
  onClose: () => void;
}): React.JSX.Element {
  const kind = isSpatial(data) ? data.view_kind : isStructured(data) ? data.plot_kind : typeof artifact?.content?.view_kind === 'string' ? artifact.content.view_kind : undefined;
  return <aside className="result-workbench" aria-label="Result Workbench">
    <header><div><BarChart3 size={18} /><span><strong>Result Workbench</strong><small>{artifact ? `${viewLabel(kind)} · ${artifact.title}` : 'Select an Interactive Result'}</small></span></div>{artifact ? <button onClick={onClose} title="Close Workbench"><X size={17} /></button> : null}</header>
    <div className="result-workbench-body">
      {!artifact ? <div className="workbench-empty"><BarChart3 size={38} /><h2>Explore research results</h2><p>Open a map or scientific chart from an OceanMind answer.</p></div> : loading ? <div className="workbench-empty"><LoaderCircle className="spin" size={30} /><p>Loading the immutable result…</p></div> : error ? <div className="workbench-error"><strong>View unavailable</strong><p>{error}</p></div> : isSpatial(data) ? <SpatialView payload={data} previewUrl={previewUrl} /> : isStructured(data) ? <ResultRenderBoundary key={`${artifact.ref.artifact_id}@${artifact.ref.version}`} resetKey={`${artifact.ref.artifact_id}@${artifact.ref.version}`}><StructuredView payload={data} /></ResultRenderBoundary> : <div className="workbench-error"><strong>Unsupported result payload</strong><p>The saved result does not match the current scientific view contract.</p></div>}
      {artifact ? <footer><span>{artifact.summary}</span><code>{artifact.ref.artifact_id}@v{artifact.ref.version}</code></footer> : null}
    </div>
  </aside>;
}
