export type ScientificScalar = number | string | null;

export type ScientificAxis = {
  field: string;
  label?: string;
  units?: string;
  scale?: 'linear' | 'log' | 'time' | 'category';
  range?: [number, number];
  reverse?: boolean;
  tick_count?: number;
  tick_format?: 'auto' | 'number' | 'date' | 'month' | 'longitude' | 'latitude';
  precision?: number;
  grid?: boolean;
};

export type ScientificLayerStyle = {
  color?: string;
  opacity?: number;
  width?: number;
  dash?: string;
  radius?: number;
  fill?: string;
  fill_opacity?: number;
  palette?: string | string[];
  marker?: 'circle' | 'square' | 'triangle' | 'diamond';
};

export type XYLayer = {
  id?: string;
  type: 'scatter' | 'line';
  x: string;
  y: string;
  color?: string;
  color_scale?: 'linear' | 'log';
  color_domain?: [number, number];
  label?: string;
  style?: ScientificLayerStyle;
};

export type BandLayer = {
  id?: string;
  type: 'band';
  x: string;
  y0: string;
  y1: string;
  label?: string;
  style?: ScientificLayerStyle;
};

export type HeatmapLayer = {
  id?: string;
  type: 'heatmap';
  x: string;
  y: string;
  z: string;
  color_scale?: 'linear' | 'log';
  color_domain?: [number, number];
  label?: string;
  style?: ScientificLayerStyle;
};

export type Field2DLayer = {
  id?: string;
  type: 'field2d';
  x: string;
  y: string;
  z: string;
  render?: 'filled_contour' | 'smooth' | 'cells';
  interpolation?: 'linear' | 'nearest';
  levels?: number | number[];
  color_scale?: 'linear' | 'log';
  color_domain?: [number, number];
  label?: string;
  style?: ScientificLayerStyle;
};

export type CategoriesLayer = {
  id?: string;
  type: 'categories';
  x: string;
  y: string;
  category: string;
  labels?: Record<string, string>;
  show_labels?: boolean;
  label?: string;
  style?: ScientificLayerStyle;
};

export type ContourPath = {level: number; points: Array<[number, number]>; label?: string};
export type ContourLayer = {
  id?: string;
  type: 'contour';
  paths: ContourPath[];
  label?: string;
  style?: ScientificLayerStyle;
};

export type AnnotationLayer = {
  id?: string;
  type: 'annotation';
  items: Array<{
    x: number;
    y: number;
    text: string;
    dx?: number;
    dy?: number;
    arrow?: boolean;
    align?: 'start' | 'middle' | 'end';
  }>;
  style?: ScientificLayerStyle;
};

export type ReferenceLayer = {
  id?: string;
  type: 'reference';
  axis: 'x' | 'y';
  value: number;
  label?: string;
  style?: ScientificLayerStyle;
};

export type VectorLayer = {
  id?: string;
  type: 'vector';
  x: string;
  y: string;
  u: string;
  v: string;
  scale?: number;
  label?: string;
  style?: ScientificLayerStyle;
};

export type ScientificLayer =
  | XYLayer
  | BandLayer
  | HeatmapLayer
  | Field2DLayer
  | CategoriesLayer
  | ContourLayer
  | AnnotationLayer
  | ReferenceLayer
  | VectorLayer;

export type ScientificPanelDisplay = {
  legend?: boolean;
  legend_position?: 'top' | 'bottom' | 'inside';
  colorbar?: boolean;
  colorbar_label?: string;
  aspect_ratio?: number;
};

export type ScientificPanel = {
  id: string;
  label?: string;
  title?: string;
  subtitle?: string;
  axes: {x: ScientificAxis; y: ScientificAxis};
  layers: ScientificLayer[];
  grid?: {column?: number; row?: number; column_span?: number; row_span?: number};
  display?: ScientificPanelDisplay;
};

export type ScientificTheme = {
  ink?: string;
  muted?: string;
  grid?: string;
  paper?: string;
  series?: string[];
};

export type ScientificFigurePayload = {
  schema_version: 'ocean-scientific-figure/v2' | 'ocean-scientific-figure/v3' | 'ocean-scientific-figure/v4';
  plot_kind: string;
  title?: string;
  subtitle?: string;
  caption?: string;
  data: Record<string, ScientificScalar[]>;
  layout?: {columns?: 1 | 2 | 3; gap?: number};
  panels: ScientificPanel[];
  theme?: ScientificTheme;
  spatial_context?: unknown;
};

export type ScientificViewPayloadV1 = {
  schema_version: 'ocean-scientific-view/v1';
  plot_kind: string;
  title?: string;
  subtitle?: string;
  data: Record<string, ScientificScalar[]>;
  axes: {x: ScientificAxis; y: ScientificAxis};
  layers: ScientificLayer[];
  display?: ScientificPanelDisplay;
  spatial_context?: unknown;
};

export type ScientificPayload = ScientificFigurePayload | ScientificViewPayloadV1;

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

export function isScientificPayload(value: unknown): value is ScientificPayload {
  if (!isRecord(value) || typeof value.plot_kind !== 'string' || !isRecord(value.data)) return false;
  if (value.schema_version === 'ocean-scientific-view/v1') {
    return isRecord(value.axes) && Array.isArray(value.layers);
  }
  if (value.schema_version === 'ocean-scientific-figure/v2' || value.schema_version === 'ocean-scientific-figure/v3' || value.schema_version === 'ocean-scientific-figure/v4') {
    return Array.isArray(value.panels) && value.panels.length > 0;
  }
  return false;
}

const ISO_TIME = /^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?$/;

function inferredAxis(axis: ScientificAxis, data: Record<string, ScientificScalar[]>): ScientificAxis {
  if (axis.scale === 'time' || axis.scale === 'category' || axis.scale === 'log') return axis;
  const values = (data[axis.field] ?? []).filter((value): value is string => typeof value === 'string');
  if (values.length < 2 || !values.every((value) => ISO_TIME.test(value) && Number.isFinite(Date.parse(value)))) return axis;
  return {...axis, scale: 'time', tick_format: axis.tick_format ?? 'date'};
}

export function normalizeScientificFigure(payload: ScientificPayload): ScientificFigurePayload {
  const data = {...payload.data};
  const normalizeLayers = (layers: ScientificLayer[], panelIndex: number): ScientificLayer[] => layers.map((original, layerIndex) => {
    let layer: ScientificLayer = original.type === 'heatmap' ? {
      ...original,
      type: 'field2d',
      render: 'filled_contour',
      interpolation: 'linear',
      levels: 18,
    } : original;

    // Repair old builder output that put a numeric colour vector in style.color.
    // New output is rejected at save time and must use scatter(color_values=...).
    if (layer.type === 'scatter' && Array.isArray((layer.style as {color?: unknown} | undefined)?.color)) {
      const values = (layer.style as unknown as {color: unknown[]}).color;
      const field = `compat_panel_${panelIndex + 1}_layer_${layerIndex + 1}_color`;
      data[field] = values.map((value) => typeof value === 'number' && Number.isFinite(value) ? value : null);
      const {color: _invalidColor, ...style} = layer.style as ScientificLayerStyle & {color: unknown};
      layer = {...layer, color: field, style};
    }

    // A short ordered vertical profile is a continuous sampled curve.  Treating
    // it as an unconnected cloud loses the scientific relationship.  Large or
    // colour-coded profile samples remain scatter by design.
    if (
      payload.plot_kind === 'profile'
      && layer.type === 'scatter'
      && (data[layer.x]?.length ?? 0) >= 2
      && (data[layer.x]?.length ?? 0) <= 1_000
    ) {
      const {radius: _radius, marker: _marker, ...style} = layer.style ?? {};
      return {
        ...layer,
        type: 'line',
        style: {...style, width: layer.style?.width ?? 2.25},
      };
    }
    return layer;
  });
  if (payload.schema_version === 'ocean-scientific-figure/v2' || payload.schema_version === 'ocean-scientific-figure/v3' || payload.schema_version === 'ocean-scientific-figure/v4') {
    return {
      ...payload,
      schema_version: 'ocean-scientific-figure/v4',
      data,
      panels: payload.panels.map((panel, panelIndex) => ({
        ...panel,
        axes: {x: inferredAxis(panel.axes.x, data), y: inferredAxis(panel.axes.y, data)},
        layers: normalizeLayers(panel.layers, panelIndex),
      })),
    };
  }
  const legacy = payload as ScientificViewPayloadV1;
  return {
    schema_version: 'ocean-scientific-figure/v4',
    plot_kind: payload.plot_kind,
    title: payload.title,
    subtitle: payload.subtitle,
    data,
    layout: {columns: 1},
    panels: [{
      id: 'main',
      axes: {x: inferredAxis(legacy.axes.x, data), y: inferredAxis(legacy.axes.y, data)},
      layers: normalizeLayers(legacy.layers, 0),
      display: legacy.display,
    }],
    spatial_context: payload.spatial_context,
  };
}
