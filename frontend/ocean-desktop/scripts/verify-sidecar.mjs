import {spawn} from 'node:child_process';
import {existsSync, lstatSync, mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

import {canonicalJson, frozenRuntimeAttestation, frozenScientificRuntimeFilename} from './frozen-runtime-baseline.mjs';

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
// This runs only while building/verifying a package. Scientific wheels can take
// substantially longer to load cold than the interactive desktop shell.
const coldStartTimeoutMs = 120_000;
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
    throw new Error(`Desktop sidecar ${command} JSON exceeds its byte limit.`);
  }
  try {
    return parseStrictJsonBytes(bytes);
  } catch {
    throw new Error(`Desktop sidecar ${command} did not emit strict JSON.`);
  }
}

function sidecarJsonCommand(executable, command) {
  return new Promise((resolveResult, rejectResult) => {
    const child = spawn(executable, [command], {stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true});
    const stdout = [];
    let stdoutBytes = 0;
    let stderr = '';
    let stdoutLimitExceeded = false;
    const timeout = setTimeout(() => {
      child.kill('SIGKILL');
      rejectResult(new Error(`Desktop sidecar ${command} did not finish within ${coldStartTimeoutMs / 1_000} seconds.`));
    }, coldStartTimeoutMs);
    child.once('error', (error) => {
      clearTimeout(timeout);
      rejectResult(error);
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
        rejectResult(new Error(`Desktop sidecar ${command} JSON exceeds its byte limit.`));
        return;
      }
      if (code !== 0) {
        rejectResult(new Error(stderr || `Desktop sidecar ${command} exited (${code ?? 'unknown'}).`));
        return;
      }
      try {
        resolveResult(parseSidecarJson(Buffer.concat(stdout, stdoutBytes), command));
      } catch {
        rejectResult(new Error(`Desktop sidecar ${command} did not emit strict JSON.`));
      }
    });
  });
}

export async function verifyFrozenScientificRuntime(executable) {
  const manifestPath = join(dirname(executable), frozenScientificRuntimeFilename);
  if (!existsSync(manifestPath) || !lstatSync(manifestPath).isFile()) {
    throw new Error(`Desktop sidecar frozen scientific runtime baseline is missing: ${manifestPath}`);
  }
  const raw = readFileSync(manifestPath);
  if (raw.byteLength > maxSidecarJsonBytes) {
    throw new Error('Desktop sidecar frozen scientific runtime baseline exceeds its size limit.');
  }
  let recorded;
  try {
    recorded = parseStrictJsonBytes(raw);
  } catch {
    throw new Error('Desktop sidecar frozen scientific runtime baseline is not strict JSON.');
  }
  const recordedAttestation = frozenRuntimeAttestation(recorded);
  const actual = await sidecarJsonCommand(executable, 'scientific-runtime');
  const actualAttestation = frozenRuntimeAttestation(actual);
  if (canonicalJson(recorded) !== canonicalJson(actual) || recordedAttestation.fingerprint_sha256 !== actualAttestation.fingerprint_sha256) {
    throw new Error('Desktop sidecar frozen scientific runtime baseline does not match the final executable.');
  }
  return recordedAttestation;
}

export async function verifySidecar(executable) {
  if (!existsSync(executable)) {
    throw new Error(`Desktop sidecar is missing: ${executable}. Run npm run build:sidecar on this platform first.`);
  }
  const frozenScientificRuntime = await verifyFrozenScientificRuntime(executable);
  const stateDirectory = mkdtempSync(join(tmpdir(), 'ocean-desktop-sidecar-'));
  const handshake = {
    protocol_version: 2,
    request_id: 'req_desktop_sidecar_verify',
    type: 'system.handshake',
    payload: {client_kind: 'desktop', client_version: 'package-verify', supported_protocol_versions: [2]},
  };
  try {
    await new Promise((resolveReady, rejectReady) => {
    const child = spawn(executable, ['backend', '--state-dir', stateDirectory, '--client-kind', 'desktop'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    const protocolPrefix = Buffer.from('OHJSON:', 'ascii');
    let pendingStdout = Buffer.alloc(0);
    let streamedStdoutBytes = 0;
    let stderr = '';
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      child.kill('SIGTERM');
      if (error) rejectReady(error);
      else resolveReady();
    };
    const timeout = setTimeout(
      () => finish(new Error(`Desktop sidecar did not emit system.ready within ${coldStartTimeoutMs / 1_000} seconds.`)),
      coldStartTimeoutMs,
    );
    child.once('error', (error) => finish(error));
    child.stderr.on('data', (chunk) => { stderr = appendBoundedText(stderr, chunk, maxSidecarStderrBytes); });
    const readProtocolLines = () => {
      while (true) {
        const newline = pendingStdout.indexOf(0x0a);
        if (newline === -1) break;
        const rawLine = pendingStdout.subarray(0, newline);
        pendingStdout = pendingStdout.subarray(newline + 1);
        const line = rawLine.at(-1) === 0x0d ? rawLine.subarray(0, -1) : rawLine;
        if (!line.subarray(0, protocolPrefix.byteLength).equals(protocolPrefix)) continue;
        try {
          const frame = parseSidecarJson(line.subarray(protocolPrefix.byteLength), 'backend frame');
          if (frame === null || typeof frame !== 'object' || Array.isArray(frame)) {
            throw new Error('Protocol v2 frame must be an object.');
          }
          if (frame.type === 'system.ready') {
            if (frame.payload?.client_kind !== 'desktop') {
              finish(new Error(`Desktop sidecar returned an unexpected client kind: ${frame.payload?.client_kind ?? 'unknown'}.`));
            } else if (frame.payload?.backend_schema !== desktopBackendSchema) {
              finish(new Error(`Desktop sidecar returned an unexpected backend schema: ${frame.payload?.backend_schema ?? 'unknown'}.`));
            } else {
              finish();
            }
            return;
          }
        } catch {
          finish(new Error('Desktop sidecar emitted malformed Protocol v2 JSON.'));
          return;
        }
      }
      if (pendingStdout.byteLength > maxSidecarJsonBytes + protocolPrefix.byteLength) {
        finish(new Error('Desktop sidecar emitted an oversized Protocol v2 frame.'));
      }
    };
    child.stdout.on('data', (chunk) => {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      streamedStdoutBytes += bytes.byteLength;
      if (streamedStdoutBytes > maxSidecarJsonBytes + protocolPrefix.byteLength) {
        finish(new Error('Desktop sidecar emitted oversized Protocol v2 stdout.'));
        return;
      }
      pendingStdout = Buffer.concat([pendingStdout, bytes]);
      readProtocolLines();
    });
    child.stdout.on('end', () => {
      if (pendingStdout.byteLength) {
        pendingStdout = Buffer.concat([pendingStdout, Buffer.from('\n')]);
        readProtocolLines();
      }
    });
    child.once('exit', (code) => {
      if (!settled) finish(new Error(stderr || `Desktop sidecar exited (${code ?? 'unknown'}) before system.ready.`));
    });
    child.stdin.write(`${JSON.stringify(handshake)}\n`);
    });
  } finally {
    rmSync(stateDirectory, {recursive: true, force: true});
  }
  return frozenScientificRuntime;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  const executable = resolve(
    desktopRoot,
    'sidecar',
    'ocean-backend',
    process.platform === 'win32' ? 'ocean-backend.exe' : 'ocean-backend',
  );
  await verifySidecar(executable);
}
