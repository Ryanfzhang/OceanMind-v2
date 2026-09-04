import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {performance} from 'node:perf_hooks';

import {_electron as electron} from '@playwright/test';

const timeoutMs = 15_000;

function optionalLimit(name) {
  const value = process.env[name];
  if (value === undefined || value === '') return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive number of milliseconds.`);
  }
  return parsed;
}

function assertLimit(name, value, limit) {
  if (limit !== null && value > limit) {
    throw new Error(`${name} took ${value} ms, exceeding ${limit} ms.`);
  }
}

function outputPath() {
  const flagIndex = process.argv.indexOf('--output');
  if (flagIndex === -1) return null;
  const path = process.argv[flagIndex + 1];
  if (!path || path.startsWith('--')) throw new Error('--output requires a path.');
  return path;
}

const workspace = await mkdtemp(join(tmpdir(), 'ocean-desktop-benchmark-'));
const launchStarted = performance.now();
const environment = {...process.env};
delete environment.ELECTRON_RUN_AS_NODE;
let app;

try {
  app = await electron.launch({args: ['.'], env: environment});
  const page = await app.firstWindow();
  await page.waitForFunction(() => document.getElementById('root')?.childElementCount === 1, {timeout: timeoutMs});
  const rendererReadyMs = Math.round(performance.now() - launchStarted);
  const paint = await page.evaluate(() => performance.getEntriesByType('paint').map((entry) => ({name: entry.name, startTime: entry.startTime})));

  const backendStarted = performance.now();
  await page.evaluate((workspacePath) => window.oceanDesktop.startBackend({workspacePath}), workspace);
  await page.reload();
  await page.locator('.sidebar-footer').getByText('Ready', {exact: true}).waitFor({state: 'visible', timeout: timeoutMs});
  const backendReadyMs = Math.round(performance.now() - backendStarted);

  const taskStarted = performance.now();
  await page.locator('.task-create input').fill('Benchmark task');
  await page.locator('.task-create input').press('Enter');
  await page.getByRole('button', {name: 'Benchmark task'}).waitFor({state: 'visible', timeout: timeoutMs});
  const taskSnapshotMs = Math.round(performance.now() - taskStarted);

  const mapStarted = performance.now();
  const compactMapButton = page.getByRole('button', {name: 'Map', exact: true});
  if (await compactMapButton.isVisible()) await compactMapButton.click();
  await page.locator('.map-canvas canvas').waitFor({state: 'visible', timeout: timeoutMs});
  const mapCanvasReadyMs = Math.round(performance.now() - mapStarted);

  const metrics = {
    schema_version: 'ocean-desktop-smoke-benchmark/v1',
    platform: process.platform,
    architecture: process.arch,
    renderer_ready_ms: rendererReadyMs,
    backend_ready_ms: backendReadyMs,
    task_snapshot_ms: taskSnapshotMs,
    map_canvas_ready_ms: mapCanvasReadyMs,
    paint_entries: paint,
  };
  const limits = {
    renderer_ready_ms: optionalLimit('OCEAN_BENCHMARK_MAX_RENDERER_READY_MS'),
    backend_ready_ms: optionalLimit('OCEAN_BENCHMARK_MAX_BACKEND_READY_MS'),
    task_snapshot_ms: optionalLimit('OCEAN_BENCHMARK_MAX_TASK_SNAPSHOT_MS'),
    map_canvas_ready_ms: optionalLimit('OCEAN_BENCHMARK_MAX_MAP_CANVAS_READY_MS'),
  };
  for (const [metric, limit] of Object.entries(limits)) {
    assertLimit(metric, metrics[metric], limit);
  }

  const serialized = JSON.stringify(metrics, null, 2) + '\n';
  const destination = outputPath();
  if (destination) await writeFile(destination, serialized, 'utf8');
  process.stdout.write(serialized);
} finally {
  await app?.close();
  await rm(workspace, {recursive: true, force: true});
}
