import {useEffect, useMemo, useRef, useState, type CSSProperties} from 'react';
import {ChevronDown, ChevronUp, Download, Image, Layers3, LoaderCircle, X} from 'lucide-react';
import type {FeatureCollection, Geometry, GeoJsonProperties} from 'geojson';
import maplibregl, {type Map as MapLibreMap, type StyleSpecification} from 'maplibre-gl';

import type {ResultDocument} from '../types.js';
import {
  isSpatial,
  isStructured,
  ResultRenderBoundary,
  StructuredView,
  type SpatialContext,
  type SpatialPayload,
  type StructuredPayload,
  structuredViewKind,
  viewLabel,
} from './InteractiveViewWorkbench.js';

const BASEMAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    'carto-light': {
      type: 'raster',
      tiles: [
        'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
        'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
        'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
        'https://d.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
      ],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors © CARTO',
    },
  },
  layers: [{
    id: 'carto-light',
    type: 'raster',
    source: 'carto-light',
    paint: {
      'raster-saturation': -.82,
      'raster-contrast': -.08,
      'raster-brightness-min': .12,
      'raster-brightness-max': .96,
    },
  }],
};

const OVERLAY_SOURCE = 'ocean-result-overlay';
const OVERLAY_LAYER = 'ocean-result-overlay';
const CONTEXT_SOURCE = 'ocean-result-context';
const CONTEXT_FILL = 'ocean-result-context-fill';
const CONTEXT_LINE = 'ocean-result-context-line';
const CONTEXT_POINT = 'ocean-result-context-point';
const CONTEXT_LABEL = 'ocean-result-context-label';
const REGION_COLORS = ['#28777d', '#a7772f', '#6f6e9c', '#b7664f', '#44769a', '#627b5f'];

type RegionLegendEntry = {label: string; color: string};
type DisplayContext = {
  collection: FeatureCollection<Geometry>;
  legend: RegionLegendEntry[];
};

type ResultWorkbenchProps = {
  document?: ResultDocument | null;
  loading?: boolean;
  data?: unknown;
  previewUrl?: string | null;
  downloadUrl?: string | null;
  error?: string | null;
  onClose?: () => void;
};

type HoverValue = {longitude: number; latitude: number; value: number | null};

const SPATIAL_PALETTES: Record<string, string[]> = {
  viridis: ['#440154', '#414487', '#2a788e', '#22a884', '#7ad151', '#fde725'],
  magma: ['#000004', '#2c115f', '#721f81', '#b73779', '#f1605d', '#feb078', '#fcfdbf'],
  plasma: ['#0d0887', '#6a00a8', '#b12a90', '#e16462', '#fca636', '#f0f921'],
  cividis: ['#00224e', '#24476d', '#576d72', '#8d8a66', '#c3aa4b', '#fee838'],
  thermal: ['#243c62', '#47759a', '#82afb5', '#e8e2cd', '#da9a70', '#a94b42'],
  balance: ['#315f91', '#8cb5ca', '#f4f3ed', '#dda17d', '#9f443f'],
  haline: ['#f4f0e5', '#c6dcd5', '#83b9b2', '#47888e', '#27536d'],
};

type GridPosition = {lower: number; upper: number; ratio: number};

function finiteValue(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function gridPosition(coordinates: number[], target: number): GridPosition | null {
  if (!coordinates.length || !Number.isFinite(target)) return null;
  if (coordinates.length === 1) return {lower: 0, upper: 0, ratio: 0};
  const ascending = coordinates.at(-1)! >= coordinates[0]!;
  const normalized = (value: number) => ascending ? value : -value;
  const sought = ascending ? target : -target;
  if (sought <= normalized(coordinates[0]!)) return {lower: 0, upper: 0, ratio: 0};
  if (sought >= normalized(coordinates.at(-1)!)) {
    const last = coordinates.length - 1;
    return {lower: last, upper: last, ratio: 0};
  }
  let low = 0;
  let high = coordinates.length - 1;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (normalized(coordinates[middle]!) <= sought) low = middle;
    else high = middle;
  }
  const start = normalized(coordinates[low]!);
  const end = normalized(coordinates[high]!);
  return {lower: low, upper: high, ratio: end === start ? 0 : (sought - start) / (end - start)};
}

/** Sample a regular scientific field while keeping land/NaN cells transparent.
 * The nearest source cell owns the mask; finite neighbours only smooth values
 * inside the ocean, so interpolation cannot paint across a coastline.
 */
export function sampleSpatialGrid(
  payload: SpatialPayload,
  longitude: number,
  latitude: number,
  interpolation: 'linear' | 'nearest' = 'linear',
): number | null {
  const x = gridPosition(payload.longitude, longitude);
  const y = gridPosition(payload.latitude, latitude);
  if (!x || !y) return null;
  const nearestColumn = x.ratio < .5 ? x.lower : x.upper;
  const nearestRow = y.ratio < .5 ? y.lower : y.upper;
  const nearest = payload.values[nearestRow]?.[nearestColumn];
  if (!finiteValue(nearest) || interpolation === 'nearest') return finiteValue(nearest) ? nearest : null;

  const samples = [
    {row: y.lower, column: x.lower, weight: (1 - x.ratio) * (1 - y.ratio)},
    {row: y.lower, column: x.upper, weight: x.ratio * (1 - y.ratio)},
    {row: y.upper, column: x.lower, weight: (1 - x.ratio) * y.ratio},
    {row: y.upper, column: x.upper, weight: x.ratio * y.ratio},
  ];
  let weighted = 0;
  let totalWeight = 0;
  samples.forEach(({row, column, weight}) => {
    const value = payload.values[row]?.[column];
    if (finiteValue(value) && weight > 0) {
      weighted += value * weight;
      totalWeight += weight;
    }
  });
  return totalWeight > 0 ? weighted / totalWeight : nearest;
}

export function spatialRasterDimensions(payload: SpatialPayload): {width: number; height: number} {
  const sourceWidth = Math.max(1, payload.longitude.length);
  const sourceHeight = Math.max(1, payload.latitude.length);
  const scale = Math.max(1, Math.min(16, 1_200 / Math.max(sourceWidth, sourceHeight)));
  return {
    width: Math.max(1, Math.round(sourceWidth * scale)),
    height: Math.max(1, Math.round(sourceHeight * scale)),
  };
}

function parseHex(value: string): [number, number, number] {
  const normalized = /^#[0-9a-f]{6}$/i.test(value) ? value : '#518d98';
  return [1, 3, 5].map((offset) => Number.parseInt(normalized.slice(offset, offset + 2), 16)) as [number, number, number];
}

function spatialPalette(payload: SpatialPayload): string[] {
  const colorbar = payload.colorbar as {colormap?: unknown} | undefined;
  const name = typeof colorbar?.colormap === 'string' ? colorbar.colormap.toLowerCase() : 'viridis';
  return SPATIAL_PALETTES[name] ?? SPATIAL_PALETTES.viridis!;
}

function spatialDomain(payload: SpatialPayload): [number, number] {
  const colorbar = payload.colorbar as {levels?: unknown} | undefined;
  const levels = Array.isArray(colorbar?.levels)
    ? colorbar.levels.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    : [];
  const values = levels.length >= 2
    ? levels
    : payload.values.flat().filter((value): value is number => finiteValue(value));
  if (!values.length) return [0, 1];
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  values.forEach((value) => {
    minimum = Math.min(minimum, value);
    maximum = Math.max(maximum, value);
  });
  return minimum === maximum ? [minimum - .5, maximum + .5] : [minimum, maximum];
}

function spatialColor(palette: string[], ratio: number): [number, number, number] {
  const bounded = Math.max(0, Math.min(1, ratio));
  const position = bounded * (palette.length - 1);
  const index = Math.min(palette.length - 2, Math.floor(position));
  const fraction = position - index;
  const left = parseHex(palette[index]!);
  const right = parseHex(palette[index + 1]!);
  return left.map((value, channel) => Math.round(value + (right[channel]! - value) * fraction)) as [number, number, number];
}

/** Build the map overlay directly from the immutable numeric grid instead of
 * enlarging a preview or serialising the canvas through a data: URL.  MapLibre
 * can consume the canvas itself, which avoids CSP/image-decoding failures that
 * otherwise leave a valid (and hoverable) grid visually blank.
 */
function spatialRasterCanvas(payload: SpatialPayload): HTMLCanvasElement | null {
  if (typeof document === 'undefined') return null;
  const source = document.createElement('canvas');
  const sourceWidth = payload.longitude.length;
  const sourceHeight = payload.latitude.length;
  if (!sourceWidth || !sourceHeight) return null;
  source.width = sourceWidth;
  source.height = sourceHeight;
  const sourceContext = source.getContext('2d');
  if (!sourceContext) return null;
  const sourceImage = sourceContext.createImageData(sourceWidth, sourceHeight);
  const longitudeAscending = payload.longitude.at(-1)! >= payload.longitude[0]!;
  const latitudeAscending = payload.latitude.at(-1)! >= payload.latitude[0]!;
  const [minimum, maximum] = spatialDomain(payload);
  const palette = spatialPalette(payload);
  for (let displayRow = 0; displayRow < sourceHeight; displayRow += 1) {
    const dataRow = latitudeAscending ? sourceHeight - displayRow - 1 : displayRow;
    for (let displayColumn = 0; displayColumn < sourceWidth; displayColumn += 1) {
      const dataColumn = longitudeAscending ? displayColumn : sourceWidth - displayColumn - 1;
      const value = payload.values[dataRow]?.[dataColumn];
      const offset = (displayRow * sourceWidth + displayColumn) * 4;
      if (!finiteValue(value)) continue;
      const [red, green, blue] = spatialColor(palette, (value - minimum) / (maximum - minimum));
      sourceImage.data[offset] = red;
      sourceImage.data[offset + 1] = green;
      sourceImage.data[offset + 2] = blue;
      sourceImage.data[offset + 3] = 255;
    }
  }
  sourceContext.putImageData(sourceImage, 0, 0);

  const canvas = document.createElement('canvas');
  const {width, height} = spatialRasterDimensions(payload);
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) return null;
  context.imageSmoothingEnabled = payload.rendering?.kind !== 'categorical';
  context.imageSmoothingQuality = 'high';
  context.drawImage(source, 0, 0, width, height);
  // Restore the original finite-cell mask with nearest-neighbour edges after
  // colour interpolation. This prevents smoothed ocean values leaking inland.
  context.globalCompositeOperation = 'destination-in';
  context.imageSmoothingEnabled = false;
  context.drawImage(source, 0, 0, width, height);
  context.globalCompositeOperation = 'source-over';
  return canvas;
}

function contextFor(data: unknown): SpatialContext | null {
  if (isSpatial(data)) {
    return data.spatial_context ?? {
      region_key: `bounds:${data.bounds.map((value) => value.toFixed(6)).join(',')}`,
      bounds: data.bounds,
      fit_policy: 'region_change',
    };
  }
  if (!isStructured(data)) return null;
  const context = data.spatial_context;
  return context && typeof context === 'object' && 'region_key' in context
    ? context as SpatialContext
    : null;
}

function visitCoordinates(value: unknown, visit: (longitude: number, latitude: number) => void): void {
  if (!Array.isArray(value)) return;
  if (
    value.length >= 2
    && typeof value[0] === 'number'
    && typeof value[1] === 'number'
    && Number.isFinite(value[0])
    && Number.isFinite(value[1])
  ) {
    visit(value[0], value[1]);
    return;
  }
  value.forEach((entry) => visitCoordinates(entry, visit));
}

export function boundsForContext(context: SpatialContext | null): [number, number, number, number] | null {
  if (!context) return null;
  if (context.bounds) return context.bounds;
  let west = Number.POSITIVE_INFINITY;
  let south = Number.POSITIVE_INFINITY;
  let east = Number.NEGATIVE_INFINITY;
  let north = Number.NEGATIVE_INFINITY;
  context.features?.features.forEach((feature) => {
    visitCoordinates((feature.geometry as {coordinates?: unknown}).coordinates, (longitude, latitude) => {
      west = Math.min(west, longitude);
      south = Math.min(south, latitude);
      east = Math.max(east, longitude);
      north = Math.max(north, latitude);
    });
  });
  return [west, south, east, north].every(Number.isFinite) && west < east && south < north
    ? [west, south, east, north]
    : null;
}

export function shouldFitRegion(
  previousRegionKey: string | null,
  context: SpatialContext | null,
): boolean {
  if (!context || context.fit_policy === 'never') return false;
  return context.fit_policy === 'always' || previousRegionKey !== context.region_key;
}

function featureLabel(properties: GeoJsonProperties, index: number): string {
  const candidate = properties?.label ?? properties?.name ?? properties?.region_name ?? properties?.region;
  return typeof candidate === 'string' && candidate.trim() ? candidate.trim() : `Region ${index + 1}`;
}

/** Convert explicit spatial geometry into a visible, labelled map mask.
 * Bounds describe the camera only; rendering them as a scientific region can
 * include land and excluded cells, so bounds never become overlay geometry.
 */
export function displayContextFor(context: SpatialContext | null): DisplayContext | null {
  if (!context) return null;
  const sourceFeatures = context.features?.features ?? [];
  if (!sourceFeatures.length) return null;
  const legend: RegionLegendEntry[] = [];
  const features = sourceFeatures.map((feature, index) => {
    const label = featureLabel(feature.properties, index);
    const color = REGION_COLORS[index % REGION_COLORS.length];
    legend.push({label, color});
    return {
      ...feature,
      properties: {
        ...(feature.properties ?? {}),
        _ocean_region_label: label,
        _ocean_region_color: color,
      },
    };
  });
  return {collection: {type: 'FeatureCollection', features}, legend};
}

function clearResultLayers(map: MapLibreMap): void {
  for (const layer of [CONTEXT_LABEL, CONTEXT_POINT, CONTEXT_LINE, CONTEXT_FILL, OVERLAY_LAYER]) {
    if (map.getLayer(layer)) map.removeLayer(layer);
  }
  for (const source of [CONTEXT_SOURCE, OVERLAY_SOURCE]) {
    if (map.getSource(source)) map.removeSource(source);
  }
}

function addContextLayers(map: MapLibreMap, collection: FeatureCollection<Geometry>): void {
  map.addSource(CONTEXT_SOURCE, {type: 'geojson', data: collection});
  map.addLayer({
    id: CONTEXT_FILL,
    type: 'fill',
    source: CONTEXT_SOURCE,
    filter: ['in', '$type', 'Polygon'],
    paint: {'fill-color': ['get', '_ocean_region_color'], 'fill-opacity': .13},
  });
  map.addLayer({
    id: CONTEXT_LINE,
    type: 'line',
    source: CONTEXT_SOURCE,
    paint: {'line-color': ['get', '_ocean_region_color'], 'line-width': 1.6, 'line-opacity': .92},
  });
  map.addLayer({
    id: CONTEXT_POINT,
    type: 'circle',
    source: CONTEXT_SOURCE,
    filter: ['in', '$type', 'Point'],
    paint: {
      'circle-radius': 4.5,
      'circle-color': ['get', '_ocean_region_color'],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 1.25,
    },
  });
  map.addLayer({
    id: CONTEXT_LABEL,
    type: 'symbol',
    source: CONTEXT_SOURCE,
    layout: {
      'text-field': ['get', '_ocean_region_label'],
      'text-size': 11,
      'text-font': ['Open Sans Semibold'],
      'text-offset': [0, 1.15],
      'text-allow-overlap': false,
    },
    paint: {
      'text-color': '#243f4b',
      'text-halo-color': '#ffffff',
      'text-halo-width': 1.25,
    },
  });
}

function spatialValue(payload: SpatialPayload, longitude: number, latitude: number): HoverValue | null {
  const [west, south, east, north] = payload.bounds;
  if (longitude < west || longitude > east || latitude < south || latitude > north) return null;
  return {longitude, latitude, value: sampleSpatialGrid(payload, longitude, latitude, 'nearest')};
}

function formatValue(value: number): string {
  if (Math.abs(value) >= 1_000 || (Math.abs(value) > 0 && Math.abs(value) < .01)) return value.toExponential(3);
  return value.toFixed(3).replace(/\.0+$|(?<=\.[0-9]*?)0+$/g, '');
}

function extentLabel(context: SpatialContext | null): string | null {
  const bounds = boundsForContext(context);
  if (!bounds) return null;
  const [west, south, east, north] = bounds;
  const longitude = (value: number) => `${Math.abs(value).toFixed(1)}°${value < 0 ? 'W' : 'E'}`;
  const latitude = (value: number) => `${Math.abs(value).toFixed(1)}°${value < 0 ? 'S' : 'N'}`;
  return `${longitude(west)}–${longitude(east)} · ${latitude(south)}–${latitude(north)}`;
}

export function ResultWorkbench({
  document = null,
  loading = false,
  data = null,
  previewUrl = null,
  downloadUrl = null,
  error = null,
  onClose = () => undefined,
}: ResultWorkbenchProps = {}): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const splitDragRef = useRef(false);
  const regionKeyRef = useRef<string | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [hover, setHover] = useState<HoverValue | null>(null);
  const [chartExpanded, setChartExpanded] = useState(false);
  const [mapShare, setMapShare] = useState(36);
  const spatial = isSpatial(data) ? data : null;
  const spatialCanvas = useMemo(() => spatial ? spatialRasterCanvas(spatial) : null, [spatial]);
  const structured = isStructured(data) ? data : null;
  const context = useMemo(() => contextFor(data), [data]);
  const displayContext = useMemo(() => displayContextFor(context), [context]);
  const structuredKind = structured ? structuredViewKind(structured) : undefined;
  const renderStatus = typeof document?.content?.render_status === 'string' ? document.content.render_status : null;
  const renderMessage = typeof document?.content?.render_message === 'string' ? document.content.render_message : null;
  const fallback = Boolean(document && !loading && !error && !spatial && !structured && (previewUrl || downloadUrl));
  const hasSpatialContext = Boolean(context);
  const figureOnly = Boolean(structured && !hasSpatialContext);
  const contextLabel = extentLabel(context);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let disposed = false;
    let loadTimer: ReturnType<typeof setTimeout> | null = null;
    try {
      const map = new maplibregl.Map({
        container: containerRef.current,
        style: BASEMAP_STYLE,
        center: [121, 24],
        zoom: 2.8,
        minZoom: 1,
        maxZoom: 12,
        attributionControl: false,
      });
      mapRef.current = map;
      map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-left');
      map.addControl(new maplibregl.AttributionControl({compact: true}), 'bottom-left');
      map.once('load', () => {
        if (loadTimer) clearTimeout(loadTimer);
        if (!disposed) {
          setFailed(false);
          setReady(true);
        }
      });
      loadTimer = setTimeout(() => {if (!disposed) setFailed(true);}, 8000);
    } catch {
      setFailed(true);
    }
    return () => {
      disposed = true;
      if (loadTimer) clearTimeout(loadTimer);
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    clearResultLayers(map);
    setHover(null);

    if (spatial && (spatialCanvas || previewUrl)) {
      const [west, south, east, north] = spatial.bounds;
      const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
        [west, north], [east, north], [east, south], [west, south],
      ];
      map.addSource(OVERLAY_SOURCE, spatialCanvas
        ? {type: 'canvas', canvas: spatialCanvas, animate: false, coordinates}
        : {type: 'image', url: previewUrl!, coordinates});
      map.addLayer({
        id: OVERLAY_LAYER,
        type: 'raster',
        source: OVERLAY_SOURCE,
        paint: {
          'raster-opacity': .84,
          'raster-resampling': spatial.rendering?.interpolation ?? (
            spatial.rendering?.kind === 'categorical' ? 'nearest' : 'linear'
          ),
        },
      });
    }
    if (displayContext) addContextLayers(map, displayContext.collection);

    const contextBounds = boundsForContext(context);
    if (context && contextBounds) {
      if (shouldFitRegion(regionKeyRef.current, context)) {
        const [west, south, east, north] = contextBounds;
        map.fitBounds([[west, south], [east, north]], {padding: 48, duration: regionKeyRef.current ? 450 : 0});
      }
      regionKeyRef.current = context.region_key;
    }
  }, [context, displayContext, previewUrl, ready, spatial, spatialCanvas]);

  useEffect(() => setChartExpanded(false), [document?.key]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => mapRef.current?.resize());
    return () => cancelAnimationFrame(frame);
  }, [chartExpanded, figureOnly, structured]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !spatial) return;
    const onMove = (event: maplibregl.MapMouseEvent) => setHover(spatialValue(spatial, event.lngLat.lng, event.lngLat.lat));
    const onLeave = () => setHover(null);
    map.on('mousemove', onMove);
    map.on('mouseout', onLeave);
    return () => {
      map.off('mousemove', onMove);
      map.off('mouseout', onLeave);
    };
  }, [ready, spatial]);

  return <aside className="spatial-workbench" aria-label="Result Workbench">
    <div ref={bodyRef} style={{'--spatial-map-share': `${chartExpanded ? 18 : mapShare}%`} as CSSProperties} className={`spatial-workbench-body${structured ? ' with-figure' : ''}${figureOnly ? ' figure-only' : ''}${chartExpanded ? ' figure-focused' : ''}`}>
      {document ? <button className="workbench-close" onClick={onClose} title="Close Result" aria-label="Close Result"><X size={17} /></button> : null}
      <div className="spatial-map-stage">
        <div className="spatial-map-canvas" ref={containerRef} />
        {!ready && !failed ? <div className="map-loading">Preparing map…</div> : null}
        {failed ? <div className="map-fallback"><Layers3 size={32} /><strong>Map unavailable</strong><span>The basemap could not be loaded. Result data remains available.</span></div> : null}
        {ready && !document ? <div className="map-empty-hint"><Layers3 size={22} /><span>No Interactive Result Selected</span><small>Open a map or scientific chart from an OceanMind answer.</small></div> : null}
        {loading ? <div className="workbench-map-status"><LoaderCircle className="spin" size={22} />Loading result…</div> : null}
        {error ? <div className="workbench-map-error"><strong>View unavailable</strong><span>{error}</span></div> : null}
        {document && !loading && !error && !spatial && !structured && !fallback ? <div className="workbench-map-error"><strong>Result unavailable</strong><span>No interactive, preview, or downloadable presentation is available.</span></div> : null}
        {spatial && hover ? <output className="map-value-readout"><strong>{spatial.variable}</strong><span>{hover.longitude.toFixed(3)}° · {hover.latitude.toFixed(3)}°</span><b>{hover.value === null ? 'No data' : `${formatValue(hover.value)} ${spatial.units}`}</b></output> : null}
        {spatial ? <div className="map-result-caption"><strong>{spatial.variable}</strong><span>{spatial.units}</span></div> : null}
        {displayContext?.legend.length ? <aside className="map-region-legend" aria-label="Analysis regions">
          <strong>Study area</strong>
          {displayContext.legend.map((entry, index) => <span key={`${entry.label}-${index}`}><i style={{backgroundColor: entry.color}} />{entry.label}</span>)}
        </aside> : null}
        {contextLabel && !displayContext?.legend.length ? <aside className="map-context-summary" aria-label="Analysis extent">
          <strong>Analysis extent</strong><span>{contextLabel}</span><small>Camera bounds; valid ocean cells remain masked.</small>
        </aside> : null}
      </div>
      {structured && hasSpatialContext ? <div
        className="spatial-split-rule"
        role="separator"
        aria-label="Resize map and figure"
        aria-orientation="horizontal"
        aria-valuemin={18}
        aria-valuemax={70}
        aria-valuenow={chartExpanded ? 18 : Math.round(mapShare)}
        tabIndex={0}
        onPointerDown={(event) => {
          splitDragRef.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (!splitDragRef.current || !bodyRef.current) return;
          const bounds = bodyRef.current.getBoundingClientRect();
          const share = (event.clientY - bounds.top) / bounds.height * 100;
          setChartExpanded(false);
          setMapShare(Math.max(18, Math.min(70, share)));
        }}
        onPointerUp={() => {splitDragRef.current = false;}}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
          event.preventDefault();
          setChartExpanded(false);
          setMapShare((value) => Math.max(18, Math.min(70, value + (event.key === 'ArrowDown' ? 4 : -4))));
        }}
      /> : null}
      {structured ? <section className="result-chart-surface" aria-label={`${viewLabel(structuredKind)} result`}>
        {hasSpatialContext ? <header className="result-drawer-header">
          <div><span className="result-drawer-kicker">{viewLabel(structuredKind)}</span><strong>{document?.title ?? 'Scientific result'}</strong></div>
          <button className="figure-focus-toggle" onClick={() => setChartExpanded((value) => !value)} title={chartExpanded ? 'Show more map' : 'Give figure more room'} aria-label={chartExpanded ? 'Show more map' : 'Give figure more room'}>
            {chartExpanded ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
          </button>
        </header> : null}
        <div className="result-chart-body"><ResultRenderBoundary key={document?.key ?? structured.plot_kind} resetKey={document?.key ?? structured.plot_kind}><StructuredView payload={structured as StructuredPayload} compactHeader={hasSpatialContext} /></ResultRenderBoundary></div>
      </section> : null}
      {fallback ? <section className="result-fallback-surface" aria-label="Result fallback">
        <header><div>{previewUrl ? <Image size={16} /> : <Download size={16} />}<span><strong>{document?.title}</strong><small>{renderStatus === 'preview' ? 'Static preview' : 'Result file'}</small></span></div></header>
        {previewUrl ? <div className="result-fallback-preview"><img src={previewUrl} alt={document?.title ?? 'Scientific result preview'} /></div> : null}
        <footer><span>{renderMessage ?? (previewUrl ? 'Interactive rendering is unavailable; showing the saved preview.' : 'Preview is unavailable; the saved result can still be downloaded.')}</span>
          {downloadUrl ? <a href={downloadUrl} download><Download size={14} />Download result</a> : null}
        </footer>
      </section> : null}
    </div>
  </aside>;
}
