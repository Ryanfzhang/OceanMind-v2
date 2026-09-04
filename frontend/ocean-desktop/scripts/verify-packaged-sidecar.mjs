import {spawn} from 'node:child_process';
import {existsSync, readFileSync, realpathSync} from 'node:fs';
import {dirname, isAbsolute, join, relative, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

import {verifySidecar} from './verify-sidecar.mjs';
import {packageVerificationAttestation, writePackageVerificationAttestation} from './package-verification-attestation.mjs';
import {assertReleaseTarget, sidecarArchitectures} from './release-target.mjs';

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const packageMetadata = JSON.parse(readFileSync(join(desktopRoot, 'package.json'), 'utf8'));
const productName = packageMetadata.build?.productName;
const doctorTimeoutMs = 120_000;
const desktopBackendSchema = 'ocean-desktop-backend/v1';
const maxSidecarJsonBytes = 4 * 1024 * 1024;
const maxSidecarStderrBytes = 256 * 1024;
const {parseStrictJsonBytes} = await import('../dist-electron/shared/strict-json.js');

function appendBoundedText(current, chunk, maximum) {
  const remaining = maximum - Buffer.byteLength(current, 'utf8');
  if (remaining <= 0) return current;
  const text = Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk);
  return Buffer.byteLength(text, 'utf8') <= remaining
    ? current + text
    : current + Buffer.from(text, 'utf8').subarray(0, remaining).toString('utf8');
}

function parseSidecarJson(source, command) {
  const bytes = Buffer.isBuffer(source) ? source : Buffer.from(source, 'utf8');
  if (bytes.byteLength > maxSidecarJsonBytes) {
    throw new Error(`Packaged sidecar ${command} JSON exceeds its byte limit.`);
  }
  try {
    return parseStrictJsonBytes(bytes);
  } catch {
    throw new Error(`Packaged sidecar ${command} did not emit strict JSON.`);
  }
}

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
      throw new Error('Usage: node scripts/verify-packaged-sidecar.mjs [--app APP_PATH] [--output REPORT_PATH]');
    }
    seen.add(argument);
    index += 1;
  }
}

function defaultAppPath() {
  if (process.platform === 'darwin') return join(desktopRoot, 'dist', 'mac', `${productName}.app`);
  if (process.platform === 'win32') return join(desktopRoot, 'dist', 'win-unpacked');
  throw new Error('Packaged sidecar verification currently supports macOS and Windows bundles only.');
}

function packagedResources(appPath) {
  const app = realpathSync(appPath);
  const resources = process.platform === 'darwin'
    ? join(app, 'Contents', 'Resources')
    : join(app, 'resources');
  if (!existsSync(resources)) {
    throw new Error(`Packaged Ocean resources directory is missing: ${resources}`);
  }
  return realpathSync(resources);
}

function packagedSidecar(appPath) {
  const resources = packagedResources(appPath);
  const executable = join(
    resources,
    'sidecar',
    'ocean-backend',
    process.platform === 'win32' ? 'ocean-backend.exe' : 'ocean-backend',
  );
  if (!existsSync(executable)) {
    throw new Error(`Packaged Ocean sidecar is missing: ${executable}`);
  }
  const realExecutable = realpathSync(executable);
  const pathWithinResources = relative(resources, realExecutable);
  if (!pathWithinResources || isAbsolute(pathWithinResources) || pathWithinResources === '..' || pathWithinResources.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`)) {
    throw new Error('Packaged Ocean sidecar resolves outside the application resources directory.');
  }
  return realExecutable;
}

async function assertPackagedUpdateConfig(appPath) {
  const resources = packagedResources(appPath);
  const configPath = join(resources, 'update-config.json');
  if (!existsSync(configPath)) {
    throw new Error('Packaged desktop is missing update-config.json.');
  }
  const {parseDesktopUpdateConfig} = await import('../dist-electron/shared/update-config.js');
  let raw;
  try {
    raw = parseStrictJsonBytes(readFileSync(configPath));
  } catch {
    throw new Error('Packaged update-config.json is not valid JSON.');
  }
  parseDesktopUpdateConfig(raw);
}

function packagedElectronExecutable(appPath) {
  const executable = process.platform === 'darwin'
    ? join(appPath, 'Contents', 'MacOS', productName)
    : join(appPath, `${productName}.exe`);
  if (!existsSync(executable)) {
    throw new Error(`Packaged Ocean Desktop executable is missing: ${executable}`);
  }
  return realpathSync(executable);
}

function assertPackagedArchitecture(appPath, sidecar) {
  const target = assertReleaseTarget({
    platform: process.platform,
    nodeArchitecture: process.arch,
    sidecarArchitectures: sidecarArchitectures(process.platform, sidecar),
  });
  const electron = packagedElectronExecutable(appPath);
  const electronArchitectures = sidecarArchitectures(process.platform, electron);
  if (!electronArchitectures.has(target.architecture)) {
    throw new Error(
      `Packaged Electron architecture (${[...electronArchitectures].sort().join(', ')}) does not match the frozen sidecar target (${target.architecture}).`,
    );
  }
  return target.architecture;
}

function sidecarJsonCommand(executable, command) {
  return new Promise((resolveDoctor, rejectDoctor) => {
    const child = spawn(executable, [command], {stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true});
    const stdout = [];
    let stdoutBytes = 0;
    let stderr = '';
    let stdoutLimitExceeded = false;
    const timeout = setTimeout(() => {
      child.kill('SIGKILL');
      rejectDoctor(new Error(`Packaged sidecar ${command} did not finish within ${doctorTimeoutMs / 1_000} seconds.`));
    }, doctorTimeoutMs);
    child.once('error', (error) => {
      clearTimeout(timeout);
      rejectDoctor(error);
    });
    child.stdout.on('data', (chunk) => {
      if (stdoutLimitExceeded) return;
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      stdoutBytes += bytes.byteLength;
      if (stdoutBytes > maxSidecarJsonBytes) {
        stdoutLimitExceeded = true;
        child.kill('SIGKILL');
        return;
      }
      stdout.push(bytes);
    });
    child.stderr.on('data', (chunk) => { stderr = appendBoundedText(stderr, chunk, maxSidecarStderrBytes); });
    child.once('exit', (code) => {
      clearTimeout(timeout);
      if (stdoutLimitExceeded) {
        rejectDoctor(new Error(`Packaged sidecar ${command} JSON exceeds its byte limit.`));
        return;
      }
      if (code !== 0) {
        rejectDoctor(new Error(stderr || `Packaged sidecar ${command} exited with ${code ?? 'unknown'}.`));
        return;
      }
      try {
        resolveDoctor(parseSidecarJson(Buffer.concat(stdout, stdoutBytes), command));
      } catch {
        rejectDoctor(new Error(`Packaged sidecar ${command} did not emit strict JSON.`));
      }
    });
  });
}

assertKnownArguments();
const requestedApp = argumentValue('--app');
const appPath = resolve(requestedApp ?? defaultAppPath());
const executable = packagedSidecar(appPath);
await assertPackagedUpdateConfig(appPath);
const architecture = assertPackagedArchitecture(appPath, executable);
const report = await sidecarJsonCommand(executable, 'doctor');
if (report.schema_version !== 'ocean-doctor/v1') {
  throw new Error('Packaged sidecar did not emit the expected doctor contract.');
}
if (report.backend_schema !== desktopBackendSchema) {
  throw new Error('Packaged sidecar did not emit the expected desktop backend schema.');
}

let selfCheck = null;
if (process.platform === 'darwin') {
  if (
    report.sandbox?.backend !== 'macos-seatbelt-rlimit-v1'
    || report.sandbox?.available !== true
    || report.capabilities?.expert_code_execution !== true
  ) {
    throw new Error('Packaged macOS sidecar does not report the required sandboxed Expert code capability.');
  }
  selfCheck = await sidecarJsonCommand(executable, 'sandbox-self-check');
  if (
    selfCheck.schema_version !== 'ocean-sandbox-self-check/v1'
    || selfCheck.backend !== 'macos-seatbelt-rlimit-v1'
    || selfCheck.passed !== true
    || selfCheck.checks?.declared_output_written !== true
    || selfCheck.checks?.outside_read_denied !== true
  ) {
    throw new Error('Packaged macOS sidecar did not prove the required sandbox read/write isolation contract.');
  }
} else if (process.platform === 'win32') {
  if (
    report.sandbox?.available !== false
    || (report.sandbox?.backend !== null && report.sandbox?.backend !== undefined)
    || report.capabilities?.expert_code_execution !== false
    || typeof report.sandbox?.reason !== 'string'
  ) {
    throw new Error('Packaged Windows sidecar must remain fail closed until the native broker has signed target-platform trust evidence.');
  }
  const broker = join(dirname(executable), 'ocean-sandbox-broker.exe');
  if (!existsSync(broker)) {
    throw new Error('Packaged Windows sidecar is missing its native sandbox broker artifact.');
  }
  const nativeContractFixture = join(dirname(executable), 'native-contract-fixture.exe');
  if (existsSync(nativeContractFixture)) {
    throw new Error('Production Windows package must not include the native sandbox contract fixture.');
  }
  const brokerReport = await sidecarJsonCommand(broker, 'doctor');
  if (
    brokerReport.schema_version !== 'ocean-windows-sandbox-broker-doctor/v1'
    || brokerReport.protocol_version !== 'ocean-windows-sandbox-broker/v1'
    || brokerReport.available !== false
    || typeof brokerReport.reason !== 'string'
  ) {
    throw new Error('Packaged Windows sandbox broker did not preserve its fail-closed doctor contract.');
  }
  if (existsSync(join(dirname(executable), 'ocean-sandbox-broker.release.json'))) {
    throw new Error('Unsigned Windows package must not include a trusted sandbox broker release manifest.');
  }
} else {
  throw new Error('Packaged sidecar verification currently supports macOS and Windows bundles only.');
}
const frozenScientificRuntime = await verifySidecar(executable);
if (
  report.frozen_scientific_runtime?.available !== true
  || report.frozen_scientific_runtime?.schema_version !== frozenScientificRuntime.schema_version
  || report.frozen_scientific_runtime?.fingerprint_sha256 !== frozenScientificRuntime.fingerprint_sha256
  || report.frozen_scientific_runtime?.dependency_count !== frozenScientificRuntime.dependency_count
) {
  throw new Error('Packaged sidecar doctor does not attest the verified frozen scientific runtime baseline.');
}
const attestation = packageVerificationAttestation({
  platform: process.platform,
  architecture,
  backendSchema: desktopBackendSchema,
  sandbox: report.sandbox,
  sandboxSelfCheck: selfCheck,
  scientificRuntime: frozenScientificRuntime,
});
const requestedOutput = argumentValue('--output');
if (requestedOutput) writePackageVerificationAttestation(requestedOutput, attestation);
console.log(JSON.stringify(attestation, null, 2));
