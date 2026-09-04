import {useEffect, useId, useMemo, useRef, useState} from 'react';
import {Focus, ZoomIn, ZoomOut} from 'lucide-react';

import {
  isScientificPayload,
  normalizeScientificFigure,
  type ScientificAxis,
  type Field2DLayer,
  type ScientificFigurePayload,
  type ScientificLayer,
  type ScientificPanel,
  type ScientificPayload,
  type ScientificScalar,
} from './scientific-figure.js';

export {isScientificPayload};
export type {ScientificFigurePayload, ScientificLayer, ScientificPayload, ScientificScalar};

const WIDTH = 680;
const HEIGHT = 430;
const MARGIN = {left: 70, right: 78, top: 24, bottom: 58};
const PLOT_WIDTH = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = HEIGHT - MARGIN.top - MARGIN.bottom;

const DEFAULT_SERIES = ['#154f70', '#4a8e92', '#bd6840', '#7775a7', '#5d7e68', '#9d7b36'];
const PALETTES: Record<string, string[]> = {
  depth: ['#f3edc9', '#cdddc8', '#93c4bd', '#5a9ea5', '#477992', '#455777', '#66516d'],
  thermal: ['#243c62', '#47759a', '#82afb5', '#e8e2cd', '#da9a70', '#a94b42'],
  haline: ['#f4f0e5', '#c6dcd5', '#83b9b2', '#47888e', '#27536d'],
  diverging: ['#315f91', '#8cb5ca', '#f4f3ed', '#dda17d', '#9f443f'],
  chlorophyll: ['#f1edc8', '#c8dda7', '#83b984', '#4b8c6c', '#285c56'],
  default: ['#edf0d6', '#bdd7c1', '#7db5ad', '#518d98', '#4b6282', '#70566f'],
  categorical: ['#154f70', '#bd6840', '#4a8e92', '#7775a7', '#9d7b36', '#5d7e68', '#a64f68', '#52719b'],
};

type Viewport = {x?: [number, number]; y?: [number, number]};
type Hover = {x: number; y: number; title: string; value: string};
type HoverPointRef = {x: number; y: number; layerIndex: number; pointIndex: number};

function finiteNumbers(values: ScientificScalar[]): number[] {
  return values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
}

function fieldNumbers(data: Record<string, ScientificScalar[]>, field: string): number[] {
  return finiteNumbers(data[field] ?? []);
}

function rawExtent(values: number[]): [number, number] {
  if (!values.length) return [0, 1];
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  values.forEach((value) => {
    if (!Number.isFinite(value)) return;
    minimum = Math.min(minimum, value);
    maximum = Math.max(maximum, value);
  });
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return [0, 1];
  return minimum === maximum ? [minimum - .5, maximum + .5] : [minimum, maximum];
}

function paddedExtent(values: number[]): [number, number] {
  const [minimum, maximum] = rawExtent(values);
  const padding = (maximum - minimum) * .045;
  return [minimum - padding, maximum + padding];
}

function isDepthAxis(axis: ScientificAxis): boolean {
  const text = `${axis.field} ${axis.label ?? ''} ${axis.units ?? ''}`.toLowerCase();
  return /\b(depth|pressure|z level|z-level)\b/.test(text);
}

export function scientificAxisExtent(values: number[], axis: ScientificAxis): [number, number] {
  const [minimum, maximum] = rawExtent(values);
  if (isDepthAxis(axis) && minimum >= 0) {
    const span = maximum - Math.min(0, minimum);
    return [0, maximum + Math.max(span * .015, 1e-9)];
  }
  return paddedExtent(values);
}

function logExtent(values: number[]): [number, number] {
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  values.forEach((value) => {
    if (!Number.isFinite(value) || value <= 0) return;
    minimum = Math.min(minimum, value);
    maximum = Math.max(maximum, value);
  });
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return [1, 10];
  if (minimum === maximum) return [minimum / 1.2, maximum * 1.2];
  return [minimum / 1.08, maximum * 1.08];
}

function cellExtent(values: number[]): [number, number] {
  const sorted = [...new Set(values)].sort((left, right) => left - right);
  if (sorted.length < 2) return paddedExtent(sorted);
  return [
    sorted[0]! - (sorted[1]! - sorted[0]!) / 2,
    sorted.at(-1)! + (sorted.at(-1)! - sorted.at(-2)!) / 2,
  ];
}

function categories(values: ScientificScalar[]): Map<string, number> {
  const result = new Map<string, number>();
  values.forEach((value) => {
    const key = String(value);
    if (!result.has(key)) result.set(key, result.size);
  });
  return result;
}

function axisNumber(value: ScientificScalar, axis: ScientificAxis, categoryValues: Map<string, number> | null): number | null {
  if (axis.scale === 'category' || categoryValues) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    return categoryValues?.get(String(value)) ?? null;
  }
  if (axis.scale === 'time' && typeof value === 'string') {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function decimalText(value: number, precision?: number): string {
  if (Math.abs(value) >= 10_000 || (Math.abs(value) > 0 && Math.abs(value) < .01)) return value.toExponential(2);
  const digits = precision ?? (Math.abs(value) < 10 ? 2 : 1);
  return value.toFixed(digits).replace(/\.0+$|(?<=\.[0-9]*?)0+$/g, '');
}

function axisText(value: number, axis: ScientificAxis): string {
  if (axis.tick_format === 'date' || (axis.scale === 'time' && axis.tick_format !== 'month')) {
    return new Intl.DateTimeFormat('en', {month: 'short', year: '2-digit'}).format(new Date(value));
  }
  if (axis.tick_format === 'month') return new Intl.DateTimeFormat('en', {month: 'short'}).format(new Date(value));
  if (axis.tick_format === 'longitude') return `${decimalText(Math.abs(value), axis.precision ?? 1)}°${value < 0 ? 'W' : value > 0 ? 'E' : ''}`;
  if (axis.tick_format === 'latitude') return `${decimalText(Math.abs(value), axis.precision ?? 1)}°${value < 0 ? 'S' : value > 0 ? 'N' : ''}`;
  return decimalText(value, axis.precision);
}

function tickValues(domain: [number, number], count: number): number[] {
  if (count <= 1) return [(domain[0] + domain[1]) / 2];
  return Array.from({length: count}, (_, index) => domain[0] + (domain[1] - domain[0]) * index / (count - 1));
}

function axisTickValues(domain: [number, number], axis: ScientificAxis, count: number): number[] {
  if (axis.scale !== 'log') return tickValues(domain, count);
  const low = Math.log10(Math.max(domain[0], Number.MIN_VALUE));
  const high = Math.log10(Math.max(domain[1], Number.MIN_VALUE));
  return tickValues([low, high], count).map((value) => 10 ** value);
}

function lerpColor(leftColor: string, rightColor: string, ratio: number): string {
  const parse = (value: string) => [1, 3, 5].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16));
  const left = parse(leftColor);
  const right = parse(rightColor);
  return `#${left.map((value, index) => Math.round(value + (right[index]! - value) * ratio).toString(16).padStart(2, '0')).join('')}`;
}

function paletteColor(palette: string[], ratio: number): string {
  const bounded = Math.max(0, Math.min(1, ratio));
  const position = bounded * (palette.length - 1);
  const index = Math.min(palette.length - 2, Math.floor(position));
  return lerpColor(palette[index]!, palette[index + 1]!, position - index);
}

function paletteFor(layer: ScientificLayer | undefined): string[] {
  const palette = layer?.style?.palette;
  if (Array.isArray(palette)) {
    const valid = palette.filter((color) => /^#[0-9a-f]{6}$/i.test(color));
    if (valid.length >= 2) return valid;
  }
  return PALETTES[typeof palette === 'string' ? palette : 'default'] ?? PALETTES.default!;
}

function numericCoordinate(
  values: ScientificScalar[],
  axis: ScientificAxis,
): number[] {
  const categoryValues = axis.scale === 'category' ? categories(values) : null;
  return values.map((value) => axisNumber(value, axis, categoryValues) ?? Number.NaN);
}

type Bracket = {lower: number; upper: number; ratio: number};

function coordinateBracket(values: number[], target: number, nearest: boolean): Bracket | null {
  if (!values.length || !Number.isFinite(target)) return null;
  const ascending = values.at(-1)! >= values[0]!;
  const compare = (value: number) => ascending ? value : -value;
  const sought = ascending ? target : -target;
  if (sought <= compare(values[0]!)) return {lower: 0, upper: 0, ratio: 0};
  if (sought >= compare(values.at(-1)!)) {
    const index = values.length - 1;
    return {lower: index, upper: index, ratio: 0};
  }
  let low = 0;
  let high = values.length - 1;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (compare(values[middle]!) <= sought) low = middle;
    else high = middle;
  }
  const span = values[high]! - values[low]!;
  const ratio = span ? (target - values[low]!) / span : 0;
  if (!nearest) return {lower: low, upper: high, ratio};
  const index = ratio < .5 ? low : high;
  return {lower: index, upper: index, ratio: 0};
}

function rgb(color: string): [number, number, number] {
  return [1, 3, 5].map((offset) => Number.parseInt(color.slice(offset, offset + 2), 16)) as [number, number, number];
}

function fieldImageDataUrl({
  layer, data, xAxis, yAxis, xDomain, yDomain, palette, colorDomain,
}: {
  layer: Field2DLayer;
  data: Record<string, ScientificScalar[]>;
  xAxis: ScientificAxis;
  yAxis: ScientificAxis;
  xDomain: [number, number];
  yDomain: [number, number];
  palette: string[];
  colorDomain: [number, number];
}): string | null {
  if (typeof document === 'undefined') return null;
  const xs = numericCoordinate(data[layer.x] ?? [], xAxis);
  const ys = numericCoordinate(data[layer.y] ?? [], yAxis);
  const values = data[layer.z] ?? [];
  if (xs.length < 2 || ys.length < 2 || values.length !== xs.length * ys.length) return null;
  // Render the regular field at a publication-like raster density.  The SVG is
  // often displayed wider than its logical 520-unit viewBox; a 520 x 340 canvas
  // visibly blurred contours on high-DPI screens.
  const width = 1040;
  const height = 680;
  const nearest = layer.interpolation === 'nearest' || layer.render === 'cells';
  const xBrackets = Array.from({length: width}, (_, column) => {
    const screenRatio = column / Math.max(1, width - 1);
    const valueRatio = xAxis.reverse ? 1 - screenRatio : screenRatio;
    return coordinateBracket(xs, xDomain[0] + (xDomain[1] - xDomain[0]) * valueRatio, nearest);
  });
  const yBrackets = Array.from({length: height}, (_, row) => {
    const screenRatio = row / Math.max(1, height - 1);
    const valueRatio = yAxis.reverse ? screenRatio : 1 - screenRatio;
    return coordinateBracket(ys, yDomain[0] + (yDomain[1] - yDomain[0]) * valueRatio, nearest);
  });
  const colorRatio = (value: number) => layer.color_scale === 'log'
    ? (Math.log10(Math.max(value, Number.MIN_VALUE)) - Math.log10(Math.max(colorDomain[0], Number.MIN_VALUE)))
      / (Math.log10(Math.max(colorDomain[1], Number.MIN_VALUE)) - Math.log10(Math.max(colorDomain[0], Number.MIN_VALUE)) || 1)
    : (value - colorDomain[0]) / (colorDomain[1] - colorDomain[0] || 1);
  const explicitLevels = Array.isArray(layer.levels) ? layer.levels : null;
  const bandCount = typeof layer.levels === 'number' ? layer.levels : 14;
  const renderedRatio = (value: number) => {
    const ratio = Math.max(0, Math.min(1, colorRatio(value)));
    if (layer.render !== 'filled_contour') return ratio;
    if (explicitLevels?.length) {
      const index = explicitLevels.findIndex((level) => value < level);
      const band = index < 0 ? explicitLevels.length : index;
      return band / explicitLevels.length;
    }
    return (Math.floor(ratio * bandCount) + .5) / bandCount;
  };
  const colorLookup = Array.from({length: 256}, (_, index) => rgb(paletteColor(palette, index / 255)));
  const sample = (x: Bracket, y: Bracket): number | null => {
    const nearestRow = y.ratio < .5 ? y.lower : y.upper;
    const nearestColumn = x.ratio < .5 ? x.lower : x.upper;
    const nearestValue = values[nearestRow * xs.length + nearestColumn];
    // Missing cells describe land, topography, or an unresolved part of the
    // scientific domain. The nearest source cell owns that mask, preventing
    // bilinear smoothing from inventing data across a gap.
    if (typeof nearestValue !== 'number' || !Number.isFinite(nearestValue)) return null;
    const v00 = values[y.lower * xs.length + x.lower];
    const v01 = values[y.lower * xs.length + x.upper];
    const v10 = values[y.upper * xs.length + x.lower];
    const v11 = values[y.upper * xs.length + x.upper];
    const w00 = (1 - x.ratio) * (1 - y.ratio);
    const w01 = x.ratio * (1 - y.ratio);
    const w10 = (1 - x.ratio) * y.ratio;
    const w11 = x.ratio * y.ratio;
    let total = 0;
    let weight = 0;
    if (typeof v00 === 'number' && Number.isFinite(v00)) {total += v00 * w00; weight += w00;}
    if (typeof v01 === 'number' && Number.isFinite(v01)) {total += v01 * w01; weight += w01;}
    if (typeof v10 === 'number' && Number.isFinite(v10)) {total += v10 * w10; weight += w10;}
    if (typeof v11 === 'number' && Number.isFinite(v11)) {total += v11 * w11; weight += w11;}
    return weight > 0 ? total / weight : nearestValue;
  };
  try {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) return null;
    const image = context.createImageData(width, height);
    for (let row = 0; row < height; row += 1) {
      const y = yBrackets[row];
      for (let column = 0; column < width; column += 1) {
        const offset = (row * width + column) * 4;
        const x = xBrackets[column];
        const value = x && y ? sample(x, y) : null;
        if (value === null) {
          image.data[offset + 3] = 0;
          continue;
        }
        const [red, green, blue] = colorLookup[Math.max(0, Math.min(255, Math.round(renderedRatio(value) * 255)))]!;
        image.data[offset] = red;
        image.data[offset + 1] = green;
        image.data[offset + 2] = blue;
        image.data[offset + 3] = 255;
      }
    }
    context.putImageData(image, 0, 0);
    return canvas.toDataURL('image/png');
  } catch {
    return null;
  }
}

function layerLabel(layer: ScientificLayer): string | undefined {
  return 'label' in layer ? layer.label : undefined;
}

function layerId(layer: ScientificLayer, index: number): string {
  return layer.id?.trim() || `${layer.type}-${index}`;
}

function linePath(
  xs: ScientificScalar[],
  ys: ScientificScalar[],
  px: (value: ScientificScalar) => number,
  py: (value: ScientificScalar) => number,
): string {
  let path = '';
  let open = false;
  xs.forEach((x, index) => {
    const screenX = px(x);
    const screenY = py(ys[index] ?? null);
    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) {
      open = false;
      return;
    }
    path += `${open ? 'L' : 'M'}${screenX.toFixed(2)},${screenY.toFixed(2)} `;
    open = true;
  });
  return path.trim();
}

function markerShape(marker: NonNullable<ScientificLayer['style']>['marker'], x: number, y: number, radius: number, color: string, opacity: number, key: number): React.JSX.Element {
  if (marker === 'square') return <rect key={key} x={x - radius} y={y - radius} width={radius * 2} height={radius * 2} fill={color} fillOpacity={opacity} />;
  if (marker === 'triangle') return <path key={key} d={`M${x},${y - radius * 1.2} L${x + radius},${y + radius} L${x - radius},${y + radius} Z`} fill={color} fillOpacity={opacity} />;
  if (marker === 'diamond') return <path key={key} d={`M${x},${y - radius * 1.25} L${x + radius},${y} L${x},${y + radius * 1.25} L${x - radius},${y} Z`} fill={color} fillOpacity={opacity} />;
  return <circle key={key} cx={x} cy={y} r={radius} fill={color} fillOpacity={opacity} />;
}

function drawCanvasMarker(context: CanvasRenderingContext2D, marker: NonNullable<ScientificLayer['style']>['marker'], x: number, y: number, radius: number, color: string): void {
  context.fillStyle = color;
  // Dense sub-pixel circles are perceptually identical to tiny squares after
  // rasterization, while fillRect preserves per-sample alpha and is much faster.
  if ((!marker || marker === 'circle') && radius <= 1.5) {
    context.fillRect(x - radius, y - radius, radius * 2, radius * 2);
    return;
  }
  context.beginPath();
  if (marker === 'square') {
    context.rect(x - radius, y - radius, radius * 2, radius * 2);
  } else if (marker === 'triangle') {
    context.moveTo(x, y - radius * 1.2);
    context.lineTo(x + radius, y + radius);
    context.lineTo(x - radius, y + radius);
    context.closePath();
  } else if (marker === 'diamond') {
    context.moveTo(x, y - radius * 1.25);
    context.lineTo(x + radius, y);
    context.lineTo(x, y + radius * 1.25);
    context.lineTo(x - radius, y);
    context.closePath();
  } else {
    context.arc(x, y, radius, 0, Math.PI * 2);
  }
  context.fill();
}

function ScientificPanelView({figure, panel, index}: {figure: ScientificFigurePayload; panel: ScientificPanel; index: number}): React.JSX.Element {
  const instanceId = useId().replaceAll(':', '');
  const clipId = `scientific-clip-${instanceId}`;
  const fieldClipId = `scientific-field-clip-${instanceId}`;
  const gradientId = `scientific-gradient-${instanceId}`;
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [hover, setHover] = useState<Hover | null>(null);
  const [viewport, setViewport] = useState<Viewport>({});
  const drag = useRef<{x: number; y: number; viewport: Required<Viewport>} | null>(null);
  const scatterCanvas = useRef<HTMLCanvasElement | null>(null);
  const xAxis = panel.axes.x;
  const yAxis = panel.axes.y;
  const data = figure.data;
  const seriesColors = figure.theme?.series?.length ? figure.theme.series : DEFAULT_SERIES;

  const geometry = useMemo(() => {
    const xSource = data[xAxis.field] ?? [];
    const ySource = data[yAxis.field] ?? [];
    const xCategories = xAxis.scale === 'category' ? categories(xSource) : null;
    const yCategories = yAxis.scale === 'category' ? categories(ySource) : null;
    const xValues: number[] = [];
    const yValues: number[] = [];
    const collect = (value: ScientificScalar, axis: ScientificAxis, target: number[], map: Map<string, number> | null) => {
      const numeric = axisNumber(value, axis, map);
      if (numeric !== null) target.push(numeric);
    };
    panel.layers.forEach((layer) => {
      if (layer.type === 'scatter' || layer.type === 'line' || layer.type === 'vector' || layer.type === 'categories') {
        (data[layer.x] ?? []).forEach((value) => collect(value, xAxis, xValues, xCategories));
        (data[layer.y] ?? []).forEach((value) => collect(value, yAxis, yValues, yCategories));
      } else if (layer.type === 'band') {
        (data[layer.x] ?? []).forEach((value) => collect(value, xAxis, xValues, xCategories));
        (data[layer.y0] ?? []).forEach((value) => collect(value, yAxis, yValues, yCategories));
        (data[layer.y1] ?? []).forEach((value) => collect(value, yAxis, yValues, yCategories));
      } else if (layer.type === 'heatmap' || layer.type === 'field2d') {
        (data[layer.x] ?? []).forEach((value) => collect(value, xAxis, xValues, xCategories));
        (data[layer.y] ?? []).forEach((value) => collect(value, yAxis, yValues, yCategories));
      } else if (layer.type === 'contour') {
        layer.paths.forEach((path) => path.points.forEach(([x, y]) => {xValues.push(x); yValues.push(y);}));
      } else if (layer.type === 'annotation') {
        layer.items.forEach((item) => {xValues.push(item.x); yValues.push(item.y);});
      } else if (layer.type === 'reference') {
        (layer.axis === 'x' ? xValues : yValues).push(layer.value);
      }
    });
    const field = panel.layers.find((layer) => layer.type === 'heatmap' || layer.type === 'field2d');
    const axisFieldValues = (field: string, axis: ScientificAxis, map: Map<string, number> | null) => (data[field] ?? []).flatMap((value) => {
      const numeric = axisNumber(value, axis, map);
      return numeric === null ? [] : [numeric];
    });
    const baseX: [number, number] = xAxis.range ?? (xCategories
      ? [-.5, Math.max(.5, xCategories.size - .5)]
      : field ? (isDepthAxis(xAxis) ? scientificAxisExtent(axisFieldValues(field.x, xAxis, xCategories), xAxis) : cellExtent(axisFieldValues(field.x, xAxis, xCategories)))
        : xAxis.scale === 'log' ? logExtent(xValues) : scientificAxisExtent(xValues, xAxis));
    const baseY: [number, number] = yAxis.range ?? (yCategories
      ? [-.5, Math.max(.5, yCategories.size - .5)]
      : field ? (isDepthAxis(yAxis) ? scientificAxisExtent(axisFieldValues(field.y, yAxis, yCategories), yAxis) : cellExtent(axisFieldValues(field.y, yAxis, yCategories)))
        : yAxis.scale === 'log' ? logExtent(yValues) : scientificAxisExtent(yValues, yAxis));
    const xDomain = viewport.x ?? baseX;
    const yDomain = viewport.y ?? baseY;
    const transform = (value: number, domain: [number, number], scale: ScientificAxis['scale']) => {
      if (scale !== 'log') return (value - domain[0]) / (domain[1] - domain[0] || 1);
      const safeValue = Math.max(value, Number.MIN_VALUE);
      const low = Math.log10(Math.max(domain[0], Number.MIN_VALUE));
      const high = Math.log10(Math.max(domain[1], Number.MIN_VALUE));
      return (Math.log10(safeValue) - low) / (high - low || 1);
    };
    const px = (value: ScientificScalar) => {
      const numeric = axisNumber(value, xAxis, xCategories);
      if (numeric === null) return Number.NaN;
      const ratio = transform(numeric, xDomain, xAxis.scale);
      return MARGIN.left + (xAxis.reverse ? 1 - ratio : ratio) * PLOT_WIDTH;
    };
    const py = (value: ScientificScalar) => {
      const numeric = axisNumber(value, yAxis, yCategories);
      if (numeric === null) return Number.NaN;
      const ratio = transform(numeric, yDomain, yAxis.scale);
      return MARGIN.top + (yAxis.reverse ? ratio : 1 - ratio) * PLOT_HEIGHT;
    };
    const xTicks = xCategories
      ? [...xCategories.entries()].filter((_, tickIndex, all) => tickIndex % Math.max(1, Math.ceil(all.length / 7)) === 0).map(([label, value]) => ({label, value}))
      : axisTickValues(xDomain, xAxis, Math.max(3, Math.min(6, xAxis.tick_count ?? 5))).map((value) => ({label: axisText(value, xAxis), value}));
    const yTicks = yCategories
      ? [...yCategories.entries()].filter((_, tickIndex, all) => tickIndex % Math.max(1, Math.ceil(all.length / 6)) === 0).map(([label, value]) => ({label, value}))
      : axisTickValues(yDomain, yAxis, Math.max(3, Math.min(6, yAxis.tick_count ?? 5))).map((value) => ({label: axisText(value, yAxis), value}));
    return {baseX, baseY, xDomain, yDomain, px, py, xTicks, yTicks};
  }, [data, panel.layers, viewport, xAxis, yAxis]);

  const colorLayer = panel.layers.find((layer) => layer.type === 'field2d' || layer.type === 'heatmap' || ((layer.type === 'scatter' || layer.type === 'line') && !!layer.color));
  const colorValues = colorLayer?.type === 'field2d' || colorLayer?.type === 'heatmap'
    ? fieldNumbers(data, colorLayer.z)
    : (colorLayer?.type === 'scatter' || colorLayer?.type === 'line') && colorLayer.color ? fieldNumbers(data, colorLayer.color) : [];
  const colorMode = colorLayer && 'color_scale' in colorLayer ? colorLayer.color_scale ?? 'linear' : 'linear';
  const colorDomain = colorLayer && 'color_domain' in colorLayer && colorLayer.color_domain
    ? colorLayer.color_domain
    : colorMode === 'log' ? logExtent(colorValues) : rawExtent(colorValues);
  const colorPalette = paletteFor(colorLayer);
  const colorFor = (value: number) => {
    const ratio = colorMode === 'log'
      ? (Math.log10(Math.max(value, Number.MIN_VALUE)) - Math.log10(Math.max(colorDomain[0], Number.MIN_VALUE)))
        / (Math.log10(Math.max(colorDomain[1], Number.MIN_VALUE)) - Math.log10(Math.max(colorDomain[0], Number.MIN_VALUE)) || 1)
      : (value - colorDomain[0]) / (colorDomain[1] - colorDomain[0] || 1);
    return paletteColor(colorPalette, ratio);
  };
  const fieldImages = useMemo(() => new Map(panel.layers.flatMap((layer, layerIndex) => {
    if (layer.type !== 'field2d') return [];
    const url = fieldImageDataUrl({
      layer,
      data,
      xAxis,
      yAxis,
      xDomain: geometry.xDomain,
      yDomain: geometry.yDomain,
      palette: paletteFor(layer),
      colorDomain: layer.color_domain ?? (layer.color_scale === 'log' ? logExtent(fieldNumbers(data, layer.z)) : rawExtent(fieldNumbers(data, layer.z))),
    });
    return [[layerId(layer, layerIndex), url] as const];
  })), [data, geometry.xDomain, geometry.yDomain, panel.layers, xAxis, yAxis]);

  const rasterPointCount = useMemo(() => panel.layers.reduce((total, layer, layerIndex) => {
    if ((layer.type !== 'scatter' && layer.type !== 'categories') || hidden.has(layerId(layer, layerIndex))) return total;
    const xs = data[layer.x] ?? [];
    const ys = data[layer.y] ?? [];
    return total + Math.min(xs.length, ys.length);
  }, 0), [data, hidden, panel.layers]);

  useEffect(() => {
    const canvas = scatterCanvas.current;
    const container = canvas?.parentElement;
    if (!canvas || !container) return;

    const draw = () => {
      const bounds = container.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2.5);
      const width = Math.max(1, Math.round(bounds.width * pixelRatio));
      const height = Math.max(1, Math.round(bounds.height * pixelRatio));
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) return;
      context.setTransform(width / WIDTH, 0, 0, height / HEIGHT, 0, 0);
      context.clearRect(0, 0, WIDTH, HEIGHT);
      context.save();
      context.beginPath();
      context.rect(MARGIN.left, MARGIN.top, PLOT_WIDTH, PLOT_HEIGHT);
      context.clip();

      panel.layers.forEach((layer, layerIndex) => {
        const id = layerId(layer, layerIndex);
        if ((layer.type !== 'scatter' && layer.type !== 'categories') || hidden.has(id)) return;
        const xs = data[layer.x] ?? [];
        const ys = data[layer.y] ?? [];
        const length = Math.min(xs.length, ys.length);
        const style = layer.style ?? {};
        // A sub-pixel authored radius is legitimate for exported vector figures,
        // but disappears on an interactive display. Keep every point and enforce
        // only a visibility floor; density remains encoded by alpha accumulation.
        const radius = Math.max(style.radius ?? 1.25, .9);
        const opacity = style.opacity ?? (layer.type === 'scatter' ? .5 : .48);
        const addPoint = (color: string, pointIndex: number) => {
          const x = geometry.px(xs[pointIndex] ?? null);
          const y = geometry.py(ys[pointIndex] ?? null);
          if (!Number.isFinite(x) || !Number.isFinite(y)) return;
          drawCanvasMarker(context, style.marker, x, y, radius, color);
        };
        context.globalAlpha = opacity;

        if (layer.type === 'scatter') {
          const baseColor = style.color ?? seriesColors[layerIndex % seriesColors.length]!;
          const values = layer.color ? data[layer.color] ?? [] : [];
          const numericValues = finiteNumbers(values);
          const mode = layer.color_scale ?? 'linear';
          const domain = layer.color_domain ?? (mode === 'log' ? logExtent(numericValues) : rawExtent(numericValues));
          const palette = paletteFor(layer);
          const pointColor = (value: ScientificScalar) => {
            if (typeof value !== 'number' || !Number.isFinite(value)) return baseColor;
            const ratio = mode === 'log'
              ? (Math.log10(Math.max(value, Number.MIN_VALUE)) - Math.log10(Math.max(domain[0], Number.MIN_VALUE)))
                / (Math.log10(Math.max(domain[1], Number.MIN_VALUE)) - Math.log10(Math.max(domain[0], Number.MIN_VALUE)) || 1)
              : (value - domain[0]) / (domain[1] - domain[0] || 1);
            // Quantizing only the palette avoids expensive unique color strings;
            // every sample is still drawn individually and retains alpha density.
            const bucket = Math.round(Math.max(0, Math.min(1, ratio)) * 95) / 95;
            return paletteColor(palette, bucket);
          };
          for (let pointIndex = 0; pointIndex < length; pointIndex += 1) {
            addPoint(layer.color ? pointColor(values[pointIndex] ?? null) : baseColor, pointIndex);
          }
        } else if (layer.type === 'categories') {
          const categoryValues = data[layer.category] ?? [];
          const unique = [...new Set(categoryValues.filter((value) => value !== null).map(String))];
          const palette = paletteFor(layer);
          const colorByCategory = new Map(unique.map((value, categoryIndex) => [value, palette[categoryIndex % palette.length]!]));
          for (let pointIndex = 0; pointIndex < length; pointIndex += 1) {
            const category = categoryValues[pointIndex];
            if (category === null || category === undefined) continue;
            const key = String(category);
            if (hidden.has(`${id}::${key}`)) continue;
            addPoint(colorByCategory.get(key) ?? seriesColors[layerIndex % seriesColors.length]!, pointIndex);
          }
        }

      });
      context.restore();
      context.globalAlpha = 1;
    };

    draw();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(draw);
    observer?.observe(container);
    return () => observer?.disconnect();
  }, [data, geometry, hidden, panel.layers, seriesColors]);

  const hoverIndex = useMemo(() => {
    const cellSize = 18;
    const buckets = new Map<string, HoverPointRef[]>();
    panel.layers.forEach((layer, layerIndex) => {
      if ((layer.type !== 'scatter' && layer.type !== 'line' && layer.type !== 'categories') || hidden.has(layerId(layer, layerIndex))) return;
      const xs = data[layer.x] ?? [];
      const ys = data[layer.y] ?? [];
      const length = Math.min(xs.length, ys.length);
      for (let pointIndex = 0; pointIndex < length; pointIndex += 1) {
        if (layer.type === 'categories') {
          const category = data[layer.category]?.[pointIndex];
          if (category === null || category === undefined || hidden.has(`${layerId(layer, layerIndex)}::${String(category)}`)) continue;
        }
        const x = xs[pointIndex] ?? null;
        const y = ys[pointIndex] ?? null;
        const screenX = geometry.px(x);
        const screenY = geometry.py(y);
        if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) continue;
        if (screenX < MARGIN.left || screenX > MARGIN.left + PLOT_WIDTH || screenY < MARGIN.top || screenY > MARGIN.top + PLOT_HEIGHT) continue;
        const key = `${Math.floor(screenX / cellSize)}:${Math.floor(screenY / cellSize)}`;
        const points = buckets.get(key) ?? [];
        points.push({x: screenX, y: screenY, layerIndex, pointIndex});
        buckets.set(key, points);
      }
    });
    return {buckets, cellSize};
  }, [data, geometry, hidden, panel.layers]);

  const nearestHoverPoint = (x: number, y: number): Hover | null => {
    const cellX = Math.floor(x / hoverIndex.cellSize);
    const cellY = Math.floor(y / hoverIndex.cellSize);
    const radius = Math.ceil(24 / hoverIndex.cellSize);
    let nearest: HoverPointRef | null = null;
    let distanceSquared = 24 * 24;
    for (let column = cellX - radius; column <= cellX + radius; column += 1) {
      for (let row = cellY - radius; row <= cellY + radius; row += 1) {
        for (const point of hoverIndex.buckets.get(`${column}:${row}`) ?? []) {
          const distance = (point.x - x) ** 2 + (point.y - y) ** 2;
          if (distance < distanceSquared) {
            nearest = point;
            distanceSquared = distance;
          }
        }
      }
    }
    if (!nearest) return null;
    const layer = panel.layers[nearest.layerIndex];
    if (!layer || (layer.type !== 'scatter' && layer.type !== 'line' && layer.type !== 'categories')) return null;
    const rawX = data[layer.x]?.[nearest.pointIndex] ?? null;
    const rawY = data[layer.y]?.[nearest.pointIndex] ?? null;
    const xValue = typeof rawX === 'number' ? axisText(rawX, xAxis) : String(rawX);
    const yValue = typeof rawY === 'number' ? axisText(rawY, yAxis) : String(rawY);
    const category = layer.type === 'categories' ? data[layer.category]?.[nearest.pointIndex] : null;
    const title = layer.type === 'categories' && category !== null && category !== undefined
      ? layer.labels?.[String(category)] ?? String(category)
      : layerLabel(layer) ?? layer.type;
    return {
      x: nearest.x,
      y: nearest.y,
      title,
      value: `${xAxis.label ?? xAxis.field} ${xValue}${xAxis.units ? ` ${xAxis.units}` : ''} · ${yAxis.label ?? yAxis.field} ${yValue}${yAxis.units ? ` ${yAxis.units}` : ''}`,
    };
  };

  const fieldHover = (screenX: number, screenY: number): Hover | null => {
    const entry = panel.layers.find((layer): layer is Field2DLayer => layer.type === 'field2d');
    if (!entry || screenX < MARGIN.left || screenX > MARGIN.left + PLOT_WIDTH || screenY < MARGIN.top || screenY > MARGIN.top + PLOT_HEIGHT) return null;
    const xs = numericCoordinate(data[entry.x] ?? [], xAxis);
    const ys = numericCoordinate(data[entry.y] ?? [], yAxis);
    const xRatio = (screenX - MARGIN.left) / PLOT_WIDTH;
    const yRatio = (screenY - MARGIN.top) / PLOT_HEIGHT;
    const xValue = geometry.xDomain[0] + (geometry.xDomain[1] - geometry.xDomain[0]) * (xAxis.reverse ? 1 - xRatio : xRatio);
    const yValue = geometry.yDomain[0] + (geometry.yDomain[1] - geometry.yDomain[0]) * (yAxis.reverse ? yRatio : 1 - yRatio);
    const x = coordinateBracket(xs, xValue, true);
    const y = coordinateBracket(ys, yValue, true);
    if (!x || !y) return null;
    const value = data[entry.z]?.[y.lower * xs.length + x.lower];
    if (typeof value !== 'number' || !Number.isFinite(value)) return null;
    return {
      x: geometry.px(data[entry.x]?.[x.lower] ?? null),
      y: geometry.py(data[entry.y]?.[y.lower] ?? null),
      title: entry.label ?? panel.display?.colorbar_label ?? 'Field value',
      value: `${axisText(xs[x.lower]!, xAxis)} · ${axisText(ys[y.lower]!, yAxis)} · ${decimalText(value)}`,
    };
  };

  const legendLayers = panel.layers
    .map((layer, layerIndex) => ({layer, id: layerId(layer, layerIndex), index: layerIndex}))
    .filter(({layer}) => layer.type !== 'annotation' && layer.type !== 'heatmap' && layer.type !== 'field2d' && layer.type !== 'categories' && !!layerLabel(layer));
  const categoryLegend = panel.layers.flatMap((layer, layerIndex) => {
    if (layer.type !== 'categories') return [];
    const values = [...new Set((data[layer.category] ?? []).filter((value) => value !== null).map(String))];
    const palette = paletteFor(layer);
    return values.map((value, categoryIndex) => ({
      id: `${layerId(layer, layerIndex)}::${value}`,
      label: layer.labels?.[value] ?? value,
      color: palette[categoryIndex % palette.length]!,
    }));
  });

  const zoom = (factor: number, anchorX = .5, anchorY = .5) => {
    const scaled = (domain: [number, number], anchor: number): [number, number] => {
      const center = domain[0] + (domain[1] - domain[0]) * anchor;
      return [center + (domain[0] - center) * factor, center + (domain[1] - center) * factor];
    };
    setViewport({x: scaled(geometry.xDomain, anchorX), y: scaled(geometry.yDomain, anchorY)});
  };

  const legendPosition = panel.display?.legend_position ?? 'top';
  const inferredTitle = panel.title ?? panel.display?.colorbar_label ?? (figure.panels.length > 1
    ? (figure.plot_kind === 'profile' ? xAxis.label : (panel.layers[0] ? layerLabel(panel.layers[0]) : undefined))
    : undefined);
  const showLegend = (legendLayers.length > 1 || categoryLegend.length > 0) && panel.display?.legend !== false;
  const legend = showLegend ? <div className="scientific-panel-legend" aria-label={`${inferredTitle ?? panel.id} series`}>
    {legendLayers.map(({layer, id, index: layerIndex}) => <button type="button" aria-pressed={!hidden.has(id)} className={hidden.has(id) ? 'is-muted' : ''} key={id} onClick={() => setHidden((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    })}><i style={{background: layer.style?.color ?? seriesColors[layerIndex % seriesColors.length]}} />{layerLabel(layer)}</button>)}
    {categoryLegend.map((entry) => <button type="button" aria-pressed={!hidden.has(entry.id)} className={hidden.has(entry.id) ? 'is-muted' : ''} key={entry.id} onClick={() => setHidden((current) => {
      const next = new Set(current);
      if (next.has(entry.id)) next.delete(entry.id); else next.add(entry.id);
      return next;
    })}><i style={{background: entry.color}} />{entry.label}</button>)}
  </div> : null;

  return <article className="scientific-panel" style={{aspectRatio: panel.display?.aspect_ratio}}>
    {(panel.label || inferredTitle || panel.subtitle || figure.panels.length > 1) ? <header className="scientific-panel-heading">
      <span className="scientific-panel-letter">{panel.label ?? String.fromCharCode(97 + index)}</span>
      <div>{inferredTitle ? <strong>{inferredTitle}</strong> : null}{panel.subtitle ? <small>{panel.subtitle}</small> : null}</div>
    </header> : null}
    {legendPosition === 'top' ? legend : null}
    <div className="scientific-panel-canvas">
      <svg className="scientific-field-layer" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" focusable="false">
        <defs><clipPath id={fieldClipId}><rect x={MARGIN.left} y={MARGIN.top} width={PLOT_WIDTH} height={PLOT_HEIGHT} /></clipPath></defs>
        <g clipPath={`url(#${fieldClipId})`}>
          {panel.layers.map((layer, layerIndex) => {
            if (layer.type !== 'field2d') return null;
            const id = layerId(layer, layerIndex);
            if (hidden.has(id)) return null;
            const image = fieldImages.get(id);
            return image
              ? <image key={id} className="scientific-field" href={image} x={MARGIN.left} y={MARGIN.top} width={PLOT_WIDTH} height={PLOT_HEIGHT} preserveAspectRatio="none" opacity={layer.style?.opacity ?? 1} />
              : <rect key={id} className="scientific-field scientific-field-fallback" x={MARGIN.left} y={MARGIN.top} width={PLOT_WIDTH} height={PLOT_HEIGHT} />;
          })}
        </g>
      </svg>
      <canvas
        ref={scatterCanvas}
        className="scientific-scatter-canvas"
        data-point-count={rasterPointCount}
        aria-hidden="true"
      />
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={panel.title ?? `${figure.plot_kind} panel ${index + 1}`}
        onPointerDown={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const x = (event.clientX - rect.left) / rect.width * WIDTH;
          const y = (event.clientY - rect.top) / rect.height * HEIGHT;
          if (x < MARGIN.left || x > MARGIN.left + PLOT_WIDTH || y < MARGIN.top || y > MARGIN.top + PLOT_HEIGHT) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = {x: event.clientX, y: event.clientY, viewport: {x: geometry.xDomain, y: geometry.yDomain}};
        }}
        onPointerUp={() => {drag.current = null;}}
        onPointerMove={(event) => {
          if (drag.current) {
            const rect = event.currentTarget.getBoundingClientRect();
            const dx = (event.clientX - drag.current.x) / rect.width * WIDTH;
            const dy = (event.clientY - drag.current.y) / rect.height * HEIGHT;
            const xSpan = drag.current.viewport.x[1] - drag.current.viewport.x[0];
            const ySpan = drag.current.viewport.y[1] - drag.current.viewport.y[0];
            const xShift = dx / PLOT_WIDTH * xSpan * (xAxis.reverse ? 1 : -1);
            const yShift = dy / PLOT_HEIGHT * ySpan * (yAxis.reverse ? -1 : 1);
            setViewport({
              x: [drag.current.viewport.x[0] + xShift, drag.current.viewport.x[1] + xShift],
              y: [drag.current.viewport.y[0] + yShift, drag.current.viewport.y[1] + yShift],
            });
            setHover(null);
            return;
          }
          const rect = event.currentTarget.getBoundingClientRect();
          const x = (event.clientX - rect.left) / rect.width * WIDTH;
          const y = (event.clientY - rect.top) / rect.height * HEIGHT;
          setHover(nearestHoverPoint(x, y) ?? fieldHover(x, y));
        }}
        onPointerLeave={() => {drag.current = null; setHover(null);}}
        onWheel={(event) => {
          // Ordinary wheel gestures should scroll the report. Pinch zoom (which
          // Chromium exposes with ctrl/meta) and the explicit controls zoom the
          // chart, preventing an unnoticed stale crop while reading.
          if (!event.ctrlKey && !event.metaKey) return;
          const rect = event.currentTarget.getBoundingClientRect();
          const x = (event.clientX - rect.left) / rect.width * WIDTH;
          const y = (event.clientY - rect.top) / rect.height * HEIGHT;
          if (x < MARGIN.left || x > MARGIN.left + PLOT_WIDTH || y < MARGIN.top || y > MARGIN.top + PLOT_HEIGHT) return;
          event.preventDefault();
          zoom(event.deltaY < 0 ? .82 : 1.22, (x - MARGIN.left) / PLOT_WIDTH, 1 - (y - MARGIN.top) / PLOT_HEIGHT);
        }}>
        <defs>
          <clipPath id={clipId}><rect x={MARGIN.left} y={MARGIN.top} width={PLOT_WIDTH} height={PLOT_HEIGHT} /></clipPath>
          <linearGradient id={gradientId} x1="0" x2="0" y1="1" y2="0">
            {colorPalette.map((color, colorIndex) => <stop key={`${color}-${colorIndex}`} offset={`${colorIndex / (colorPalette.length - 1) * 100}%`} stopColor={color} />)}
          </linearGradient>
        </defs>
        {geometry.yTicks.map((tick) => <g key={`y-${tick.label}`}>
          {yAxis.grid === true ? <line x1={MARGIN.left} y1={geometry.py(tick.value)} x2={MARGIN.left + PLOT_WIDTH} y2={geometry.py(tick.value)} className="scientific-grid" /> : null}
          <line x1={MARGIN.left - 5} y1={geometry.py(tick.value)} x2={MARGIN.left} y2={geometry.py(tick.value)} className="scientific-tick-mark" />
          <text x={MARGIN.left - 10} y={geometry.py(tick.value) + 4} textAnchor="end" className="scientific-tick">{tick.label}</text>
        </g>)}
        {geometry.xTicks.map((tick) => <g key={`x-${tick.label}`}>
          {xAxis.grid ? <line x1={geometry.px(tick.value)} y1={MARGIN.top} x2={geometry.px(tick.value)} y2={MARGIN.top + PLOT_HEIGHT} className="scientific-grid" /> : null}
          <line x1={geometry.px(tick.value)} y1={MARGIN.top + PLOT_HEIGHT} x2={geometry.px(tick.value)} y2={MARGIN.top + PLOT_HEIGHT + 5} className="scientific-tick-mark" />
          <text x={geometry.px(tick.value)} y={MARGIN.top + PLOT_HEIGHT + 20} textAnchor="middle" className="scientific-tick">{tick.label}</text>
        </g>)}
        <path d={`M${MARGIN.left},${MARGIN.top} V${MARGIN.top + PLOT_HEIGHT} H${MARGIN.left + PLOT_WIDTH}`} className="scientific-axis" />
        <g clipPath={`url(#${clipId})`}>
          {panel.layers.map((layer, layerIndex) => {
            const id = layerId(layer, layerIndex);
            if (hidden.has(id)) return null;
            const style = layer.style ?? {};
            const baseColor = style.color ?? seriesColors[layerIndex % seriesColors.length]!;
            if (layer.type === 'field2d') {
              return null;
            }
            if (layer.type === 'band') {
              const xs = data[layer.x] ?? [];
              const lower = data[layer.y0] ?? [];
              const upper = data[layer.y1] ?? [];
              const upperPoints = xs.flatMap((x, itemIndex) => Number.isFinite(geometry.px(x)) && Number.isFinite(geometry.py(upper[itemIndex] ?? null)) ? [`${geometry.px(x)},${geometry.py(upper[itemIndex] ?? null)}`] : []);
              const lowerPoints = xs.flatMap((x, itemIndex) => Number.isFinite(geometry.px(x)) && Number.isFinite(geometry.py(lower[itemIndex] ?? null)) ? [`${geometry.px(x)},${geometry.py(lower[itemIndex] ?? null)}`] : []).reverse();
              return <polygon key={id} points={[...upperPoints, ...lowerPoints].join(' ')} fill={style.fill ?? baseColor} fillOpacity={style.fill_opacity ?? .16} />;
            }
            if (layer.type === 'line') {
              const xs = data[layer.x] ?? [];
              const ys = data[layer.y] ?? [];
              const colors = layer.color ? data[layer.color] ?? [] : [];
              if (layer.color) {
                return <g key={id}>{xs.slice(1).map((x, itemIndex) => {
                  const previousIndex = itemIndex;
                  const currentIndex = itemIndex + 1;
                  const x1 = geometry.px(xs[previousIndex] ?? null);
                  const y1 = geometry.py(ys[previousIndex] ?? null);
                  const x2 = geometry.px(x);
                  const y2 = geometry.py(ys[currentIndex] ?? null);
                  const firstColor = colors[previousIndex];
                  const secondColor = colors[currentIndex];
                  if (![x1, y1, x2, y2].every(Number.isFinite)) return null;
                  const colorValue = typeof firstColor === 'number' && typeof secondColor === 'number'
                    ? (firstColor + secondColor) / 2
                    : typeof secondColor === 'number' ? secondColor : firstColor;
                  return <line
                    key={currentIndex}
                    x1={x1} y1={y1} x2={x2} y2={y2}
                    stroke={typeof colorValue === 'number' ? colorFor(colorValue) : baseColor}
                    strokeWidth={style.width ?? 2.2}
                    strokeOpacity={style.opacity ?? 1}
                    strokeLinecap="round"
                  />;
                })}</g>;
              }
              return <g key={id}><path d={linePath(xs, ys, geometry.px, geometry.py)} fill="none" stroke={baseColor} strokeWidth={style.width ?? 2.2} strokeDasharray={style.dash} strokeOpacity={style.opacity ?? 1} strokeLinecap="round" strokeLinejoin="round" />
                {style.radius ? xs.map((x, itemIndex) => {
                  const pointX = geometry.px(x);
                  const pointY = geometry.py(ys[itemIndex] ?? null);
                  return Number.isFinite(pointX) && Number.isFinite(pointY) ? markerShape(style.marker, pointX, pointY, style.radius!, baseColor, style.opacity ?? 1, itemIndex) : null;
                }) : null}</g>;
            }
            if (layer.type === 'scatter') {
              // Full scatter data is painted by the Canvas layer beneath this
              // SVG. Axes, contours, labels and interaction remain vector based.
              return null;
            }
            if (layer.type === 'categories') {
              const xs = data[layer.x] ?? [];
              const ys = data[layer.y] ?? [];
              const categoryValues = data[layer.category] ?? [];
              const unique = [...new Set(categoryValues.filter((value) => value !== null).map(String))];
              const palette = paletteFor(layer);
              const colorByCategory = new Map(unique.map((value, categoryIndex) => [value, palette[categoryIndex % palette.length]!]));
              const centroids = new Map<string, {x: number; y: number; count: number}>();
              const length = Math.min(xs.length, ys.length, categoryValues.length);
              for (let itemIndex = 0; itemIndex < length; itemIndex += 1) {
                const x = xs[itemIndex];
                const category = categoryValues[itemIndex];
                if (category === null || category === undefined) continue;
                const key = String(category);
                if (hidden.has(`${id}::${key}`)) continue;
                const pointX = geometry.px(x);
                const pointY = geometry.py(ys[itemIndex] ?? null);
                if (!Number.isFinite(pointX) || !Number.isFinite(pointY)) continue;
                const aggregate = centroids.get(key) ?? {x: 0, y: 0, count: 0};
                aggregate.x += pointX;
                aggregate.y += pointY;
                aggregate.count += 1;
                centroids.set(key, aggregate);
              }
              return <g key={id}>{layer.show_labels !== false ? [...centroids.entries()].map(([category, aggregate]) => <text
                key={category}
                x={aggregate.x / aggregate.count}
                y={aggregate.y / aggregate.count - 7}
                textAnchor="middle"
                fill={colorByCategory.get(category)}
                className="scientific-category-label"
              >{layer.labels?.[category] ?? category}</text>) : null}</g>;
            }
            if (layer.type === 'contour') return <g key={id}>{layer.paths.map((path, pathIndex) => {
              const midpoint = path.points[Math.floor(path.points.length / 2)]!;
              return <g key={pathIndex}><path d={linePath(path.points.map(([x]) => x), path.points.map(([, y]) => y), geometry.px, geometry.py)} fill="none" stroke={baseColor} strokeWidth={style.width ?? 1} strokeOpacity={style.opacity ?? .65} />
                <text x={geometry.px(midpoint[0]) + 4} y={geometry.py(midpoint[1]) - 4} className="scientific-contour-label">{path.label ?? decimalText(path.level)}</text></g>;
            })}</g>;
            if (layer.type === 'annotation') return <g key={id}>{layer.items.map((item, itemIndex) => {
              const originX = geometry.px(item.x);
              const originY = geometry.py(item.y);
              const textX = originX + (item.dx ?? 7);
              const textY = originY + (item.dy ?? -7);
              return <g key={itemIndex}>{item.arrow ? <path d={`M${originX},${originY} L${textX},${textY + 3}`} className="scientific-annotation-arrow" stroke={baseColor} /> : null}<text x={textX} y={textY} textAnchor={item.align ?? 'start'} fill={baseColor} className="scientific-annotation">{item.text}</text></g>;
            })}</g>;
            if (layer.type === 'reference') {
              const coordinate = layer.axis === 'x' ? geometry.px(layer.value) : geometry.py(layer.value);
              return <g key={id}>{layer.axis === 'x'
                ? <line x1={coordinate} y1={MARGIN.top} x2={coordinate} y2={MARGIN.top + PLOT_HEIGHT} stroke={baseColor} strokeWidth={style.width ?? 1.1} strokeDasharray={style.dash ?? '5 4'} />
                : <line x1={MARGIN.left} y1={coordinate} x2={MARGIN.left + PLOT_WIDTH} y2={coordinate} stroke={baseColor} strokeWidth={style.width ?? 1.1} strokeDasharray={style.dash ?? '5 4'} />}
                {layer.label ? <text x={layer.axis === 'x' ? coordinate + 5 : MARGIN.left + PLOT_WIDTH - 5} y={layer.axis === 'x' ? MARGIN.top + 14 : coordinate - 6} textAnchor={layer.axis === 'x' ? 'start' : 'end'} fill={baseColor} className="scientific-reference-label">{layer.label}</text> : null}</g>;
            }
            if (layer.type === 'vector') {
              const xs = data[layer.x] ?? [];
              const ys = data[layer.y] ?? [];
              const us = fieldNumbers(data, layer.u);
              const vs = fieldNumbers(data, layer.v);
              const maximum = Math.max(1e-12, ...us.map((u, vectorIndex) => Math.hypot(u, vs[vectorIndex] ?? 0)));
              const factor = layer.scale ?? 34 / maximum;
              return <g key={id}>{xs.map((x, vectorIndex) => {
                const u = data[layer.u]?.[vectorIndex];
                const v = data[layer.v]?.[vectorIndex];
                if (typeof u !== 'number' || typeof v !== 'number') return null;
                const startX = geometry.px(x);
                const startY = geometry.py(ys[vectorIndex] ?? null);
                const endX = startX + u * factor;
                const endY = startY - v * factor * (yAxis.reverse ? -1 : 1);
                const angle = Math.atan2(endY - startY, endX - startX);
                const head = 5;
                const leftX = endX - head * Math.cos(angle - .55);
                const leftY = endY - head * Math.sin(angle - .55);
                const rightX = endX - head * Math.cos(angle + .55);
                const rightY = endY - head * Math.sin(angle + .55);
                return <g key={vectorIndex} opacity={style.opacity ?? .82}><line x1={startX} y1={startY} x2={endX} y2={endY} stroke={baseColor} strokeWidth={style.width ?? 1.35} /><path d={`M${leftX},${leftY} L${endX},${endY} L${rightX},${rightY}`} fill="none" stroke={baseColor} strokeWidth={style.width ?? 1.35} /></g>;
              })}</g>;
            }
            return null;
          })}
        </g>
        <text x={MARGIN.left + PLOT_WIDTH / 2} y={HEIGHT - 13} textAnchor="middle" className="scientific-axis-label">{xAxis.label ?? xAxis.field}{xAxis.units ? ` (${xAxis.units})` : ''}</text>
        <text x="17" y={MARGIN.top + PLOT_HEIGHT / 2} textAnchor="middle" transform={`rotate(-90 17 ${MARGIN.top + PLOT_HEIGHT / 2})`} className="scientific-axis-label">{yAxis.label ?? yAxis.field}{yAxis.units ? ` (${yAxis.units})` : ''}</text>
        {colorValues.length && panel.display?.colorbar !== false ? <g><rect className="scientific-colorbar" x={WIDTH - 45} y={MARGIN.top + 8} width="9" height={PLOT_HEIGHT - 16} fill={`url(#${gradientId})`} />
          <line x1={WIDTH - 35} y1={MARGIN.top + 8} x2={WIDTH - 31} y2={MARGIN.top + 8} className="scientific-tick-mark" />
          <line x1={WIDTH - 35} y1={MARGIN.top + PLOT_HEIGHT / 2} x2={WIDTH - 31} y2={MARGIN.top + PLOT_HEIGHT / 2} className="scientific-tick-mark" />
          <line x1={WIDTH - 35} y1={MARGIN.top + PLOT_HEIGHT - 8} x2={WIDTH - 31} y2={MARGIN.top + PLOT_HEIGHT - 8} className="scientific-tick-mark" />
          <text x={WIDTH - 28} y={MARGIN.top + 12} className="scientific-colorbar-tick">{decimalText(colorDomain[1])}</text>
          <text x={WIDTH - 28} y={MARGIN.top + PLOT_HEIGHT / 2 + 4} className="scientific-colorbar-tick">{decimalText((colorDomain[0] + colorDomain[1]) / 2)}</text>
          <text x={WIDTH - 28} y={MARGIN.top + PLOT_HEIGHT - 4} className="scientific-colorbar-tick">{decimalText(colorDomain[0])}</text>
          {panel.display?.colorbar_label ? <text x={WIDTH - 3} y={MARGIN.top + PLOT_HEIGHT / 2} textAnchor="middle" transform={`rotate(-90 ${WIDTH - 3} ${MARGIN.top + PLOT_HEIGHT / 2})`} className="scientific-colorbar-label">{panel.display.colorbar_label}</text> : null}</g> : null}
        {hover ? <g pointerEvents="none"><line x1={hover.x} y1={MARGIN.top} x2={hover.x} y2={MARGIN.top + PLOT_HEIGHT} className="scientific-hover-line" /><circle cx={hover.x} cy={hover.y} r="4" className="scientific-hover-point" /></g> : null}
      </svg>
      <div className="scientific-panel-controls" role="group" aria-label="Chart zoom controls">
        <button type="button" onClick={() => zoom(.8)} title="Zoom in" aria-label="Zoom in"><ZoomIn size={14} /></button>
        <button type="button" onClick={() => zoom(1.25)} title="Zoom out" aria-label="Zoom out"><ZoomOut size={14} /></button>
        <button type="button" onClick={() => setViewport({})} title="Reset axes" aria-label="Reset axes"><Focus size={14} /></button>
      </div>
      {legendPosition === 'inside' ? legend : null}
      {hover ? <output className="scientific-hover-readout"><strong>{hover.title}</strong><span>{hover.value}</span></output> : null}
    </div>
    {legendPosition === 'bottom' ? legend : null}
  </article>;
}

export function ScientificView({payload, compactHeader = false}: {payload: ScientificPayload; compactHeader?: boolean}): React.JSX.Element {
  const figure = normalizeScientificFigure(payload);
  const columns = figure.layout?.columns ?? (figure.panels.length === 1 ? 1 : 2);
  const autoHeroLayout = figure.panels.length === 3 && !figure.panels.some((panel) => panel.grid);
  const themeStyle = {
    '--scientific-ink': figure.theme?.ink ?? '#172d3b',
    '--scientific-muted': figure.theme?.muted ?? '#647580',
    '--scientific-grid': figure.theme?.grid ?? '#dfe5e8',
    '--scientific-paper': figure.theme?.paper ?? '#ffffff',
    '--scientific-gap': `${figure.layout?.gap ?? 16}px`,
  } as React.CSSProperties;
  return <section className={`scientific-figure scientific-columns-${columns}${autoHeroLayout ? ' scientific-auto-hero' : ''}${compactHeader ? ' scientific-compact' : ''}`} style={themeStyle}>
    {!compactHeader && (figure.title || figure.subtitle) ? <header className="scientific-figure-heading">
      {figure.title ? <h2>{figure.title}</h2> : null}
      {figure.subtitle ? <p>{figure.subtitle}</p> : null}
    </header> : null}
    <div className="scientific-panel-grid">
      {figure.panels.map((panel, index) => <div key={panel.id} className="scientific-panel-slot" style={{
        gridColumn: panel.grid?.column ? `${panel.grid.column} / span ${panel.grid.column_span ?? 1}` : undefined,
        gridRow: panel.grid?.row ? `${panel.grid.row} / span ${panel.grid.row_span ?? 1}` : undefined,
      }}><ScientificPanelView figure={figure} panel={panel} index={index} /></div>)}
    </div>
    {figure.caption ? <footer className="scientific-figure-caption">{figure.caption}</footer> : null}
  </section>;
}
