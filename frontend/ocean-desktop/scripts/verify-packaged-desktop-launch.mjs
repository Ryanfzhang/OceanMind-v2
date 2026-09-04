import {existsSync, readFileSync} from 'node:fs';
import {mkdtemp, realpath, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';

import {_electron as electron} from '@playwright/test';

import {writeEvidenceOnce} from './package-verification-attestation.mjs';

const desktopRoot = process.cwd();
const metadata = JSON.parse(readFileSync(join(desktopRoot, 'package.json'), 'utf8'));
const productName = metadata.build?.productName;
const timeoutMs = 45_000;

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  return index === -1 ? null : process.argv[index + 1] ?? null;
}

function assertKnownArguments() {
  const allowed = new Set(['--app', '--output']);
  const seen = new Set();
  for (let index = 2; index < process.argv.length; index += 1) {
    const argument = process.argv[index];
    if (!argument?.startsWith('--') || !allowed.has(argument) || seen.has(argument) || !process.argv[index + 1] || process.argv[index + 1]?.startsWith('--')) {
      throw new Error('Usage: node scripts/verify-packaged-desktop-launch.mjs [--app APP_PATH] [--output REPORT_PATH]');
    }
    seen.add(argument);
    index += 1;
  }
}

function defaultAppPath() {
  if (process.platform === 'darwin') return join(desktopRoot, 'dist', 'mac', `${productName}.app`);
  if (process.platform === 'win32') return join(desktopRoot, 'dist', 'win-unpacked');
  throw new Error('Packaged Desktop launch verification currently supports macOS and Windows bundles only.');
}

function appExecutable(appPath) {
  if (process.platform === 'darwin') return join(appPath, 'Contents', 'MacOS', productName);
  return join(appPath, `${productName}.exe`);
}

function isolatedEnvironment() {
  const environment = {...process.env};
  for (const name of [
    'ELECTRON_RUN_AS_NODE',
    'CONDA_DEFAULT_ENV',
    'CONDA_PREFIX',
    'NVM_BIN',
    'NVM_PATH',
    'NODE_PATH',
    'NODE_OPTIONS',
    'OCEAN_PYTHON',
    'OCEAN_DESKTOP_TEST_MODE',
    'OCEAN_DESKTOP_TEST_BACKEND_SCRIPT',
    'OCEAN_DESKTOP_TEST_ISOLATED_PROFILE',
    'PYTHONHOME',
    'PYTHONPATH',
    'PYTHONUSERBASE',
    'VIRTUAL_ENV',
    'VOLTA_HOME',
    'Path',
  ]) delete environment[name];
  const systemRoot = environment.SystemRoot ?? environment.WINDIR ?? 'C:\\Windows';
  environment.PATH = process.platform === 'win32'
    ? `${systemRoot}\\System32;${systemRoot}`
    : '/usr/bin:/bin:/usr/sbin:/sbin';
  return environment;
}

assertKnownArguments();
const appPath = resolve(argumentValue('--app') ?? defaultAppPath());
const executable = appExecutable(appPath);
if (!existsSync(executable)) throw new Error(`Packaged Ocean Desktop executable is missing: ${executable}`);

const workspace = await mkdtemp(join(tmpdir(), 'ocean-packaged-launch-'));
const userDataProfile = await mkdtemp(join(tmpdir(), 'ocean-packaged-profile-'));
let app;
try {
  app = await electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${userDataProfile}`],
    env: isolatedEnvironment(),
    timeout: timeoutMs,
  });
  const page = await app.firstWindow({timeout: timeoutMs});
  await page.waitForFunction(() => document.getElementById('root')?.childElementCount === 1, {timeout: timeoutMs});
  const packaged = await app.evaluate(({app: electronApp}) => electronApp.isPackaged);
  if (packaged !== true) throw new Error('Desktop launch verification did not start a packaged Electron app.');
  const activeProfile = await app.evaluate(({app: electronApp}) => electronApp.getPath('userData'));
  if (await realpath(activeProfile) !== await realpath(userDataProfile)) {
    throw new Error('Packaged desktop launch verification did not use its isolated user-data profile.');
  }
  await page.evaluate((workspacePath) => window.oceanDesktop.startBackend({workspacePath}), workspace);
  await page.reload();
  await page.locator('.project-picker').waitFor({state: 'visible', timeout: timeoutMs});
  await page.waitForFunction((expectedWorkspace) => document.querySelector('.project-picker')?.getAttribute('title') === expectedWorkspace, workspace, {timeout: timeoutMs});
  await page.locator('.task-create input').fill('Packaged runtime smoke task');
  await page.locator('.task-create input').press('Enter');
  await page.getByRole('button', {name: 'Packaged runtime smoke task'}).waitFor({state: 'visible', timeout: timeoutMs});
  const status = await page.evaluate(() => window.oceanDesktop.getBackendStatus());
  const readyPayload = status.readyEvent?.payload;
  const backendSchema = readyPayload && typeof readyPayload === 'object'
    ? readyPayload.backend_schema
    : null;
  const frozenScientificRuntime = readyPayload && typeof readyPayload === 'object'
    ? readyPayload.runtime_capabilities?.scientific_runtime
    : null;
  if (status.running !== true || backendSchema !== 'ocean-desktop-backend/v1') {
    throw new Error('Packaged desktop did not keep a ready frozen sidecar after creating a task.');
  }
  if (
    !frozenScientificRuntime
    || frozenScientificRuntime.available !== true
    || frozenScientificRuntime.schema_version !== 'ocean-frozen-scientific-runtime/v1'
    || typeof frozenScientificRuntime.fingerprint_sha256 !== 'string'
    || !/^[a-f0-9]{64}$/.test(frozenScientificRuntime.fingerprint_sha256)
    || !Number.isInteger(frozenScientificRuntime.dependency_count)
  ) {
    throw new Error('Packaged desktop did not expose a verified frozen scientific runtime baseline.');
  }
  const report = {
    schema_version: 'ocean-desktop-packaged-launch/v1',
    platform: process.platform,
    architecture: process.arch,
    backend_schema: backendSchema,
    packaged: packaged === true,
    task_created: true,
    inherited_runtime_paths_cleared: true,
    isolated_user_data_profile: true,
    scientific_runtime_fingerprint_sha256: frozenScientificRuntime.fingerprint_sha256,
  };
  const requestedOutput = argumentValue('--output');
  if (requestedOutput) writeEvidenceOnce(requestedOutput, report);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
} finally {
  try {
    await app?.close();
  } finally {
    await Promise.all([
      rm(userDataProfile, {recursive: true, force: true}),
      rm(workspace, {recursive: true, force: true}),
    ]);
  }
}
