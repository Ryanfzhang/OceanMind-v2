import {execFileSync, spawnSync} from 'node:child_process';
import {existsSync, readFileSync, readdirSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const packageMetadata = JSON.parse(readFileSync(join(desktopRoot, 'package.json'), 'utf8'));
const productName = packageMetadata.build?.productName;

function existingFiles(directory, matcher) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, {withFileTypes: true})
    .filter((entry) => entry.isFile() && matcher(entry.name))
    .map((entry) => join(directory, entry.name))
    .sort();
}

function exactlyOne(paths, description) {
  if (paths.length !== 1) {
    throw new Error(`Expected exactly one ${description}; found ${paths.length}.`);
  }
  return paths[0];
}

function assertExisting(paths) {
  for (const path of paths) {
    if (!existsSync(path)) throw new Error(`Required signed release path is missing: ${path}`);
  }
}

export function signedPackageLayout({platform, root = desktopRoot, name = productName, files = existingFiles}) {
  if (!name) throw new Error('Desktop product name is required for signature verification.');
  const dist = join(root, 'dist');
  if (platform === 'darwin') {
    const app = join(dist, 'mac', `${name}.app`);
    return {
      app,
      executables: [
        join(app, 'Contents', 'MacOS', name),
        join(app, 'Contents', 'Resources', 'sidecar', 'ocean-backend', 'ocean-backend'),
      ],
      dmg: exactlyOne(files(dist, (file) => file.endsWith('.dmg')), 'macOS DMG artifact'),
      zip: exactlyOne(files(dist, (file) => file.endsWith('-mac.zip')), 'macOS ZIP artifact'),
    };
  }
  if (platform === 'win32') {
    const unpacked = join(dist, 'win-unpacked');
    return {
      unpacked,
      executables: [
        join(unpacked, `${name}.exe`),
        join(unpacked, 'resources', 'sidecar', 'ocean-backend', 'ocean-backend.exe'),
      ],
      installer: exactlyOne(files(dist, (file) => file.endsWith('.exe') && !file.includes('win-unpacked')), 'Windows NSIS installer'),
    };
  }
  throw new Error(`Signed Ocean Desktop verification supports macOS and Windows only; received ${platform}.`);
}

export function hasDeveloperIdAuthority(output) {
  return /^Authority=Developer ID Application:/m.test(output);
}

function command(commandName, argumentsList) {
  try {
    return execFileSync(commandName, argumentsList, {encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']});
  } catch (error) {
    const stdout = error?.stdout ? String(error.stdout) : '';
    const stderr = error?.stderr ? String(error.stderr) : '';
    throw new Error(`${commandName} ${argumentsList.join(' ')} failed.\n${stdout}${stderr}`.trim());
  }
}

function macSignatureDetails(path) {
  const result = spawnSync('codesign', ['-dvvv', path], {encoding: 'utf8'});
  if (result.error) throw result.error;
  const output = String(result.stdout ?? '') + String(result.stderr ?? '');
  if (result.status !== 0) {
    throw new Error(`codesign details failed for ${path}.\n${output}`.trim());
  }
  return output;
}

function verifyMac(layout) {
  assertExisting([layout.app, layout.dmg, layout.zip, ...layout.executables]);
  for (const executable of [layout.app, ...layout.executables]) {
    command('codesign', ['--verify', '--deep', '--strict', '--verbose=2', executable]);
    if (!hasDeveloperIdAuthority(macSignatureDetails(executable))) {
      throw new Error(`Signed macOS release path does not have a Developer ID Application authority: ${executable}`);
    }
  }
  command('xcrun', ['stapler', 'validate', layout.app]);
  command('xcrun', ['stapler', 'validate', layout.dmg]);
  command('spctl', ['--assess', '--type', 'execute', '--verbose=4', layout.app]);
}

function verifyWindows(layout) {
  assertExisting([layout.unpacked, layout.installer, ...layout.executables]);
  const verifier = join(desktopRoot, 'scripts', 'verify-signed-package.ps1');
  command('powershell.exe', [
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    verifier,
    ...layout.executables,
    layout.installer,
  ]);
}

export function verifySignedPackage({platform = process.platform, root = desktopRoot, name = productName} = {}) {
  const layout = signedPackageLayout({platform, root, name});
  if (platform === 'darwin') verifyMac(layout);
  else verifyWindows(layout);
  return layout;
}

function main() {
  const target = process.argv[2];
  const expectedPlatform = target === 'mac' ? 'darwin' : target === 'win' ? 'win32' : null;
  if (!expectedPlatform || process.argv.length !== 3) {
    throw new Error('Usage: node scripts/verify-signed-package.mjs <mac|win>');
  }
  if (process.platform !== expectedPlatform) {
    throw new Error(`A signed ${target} package must be verified on ${expectedPlatform}; current platform is ${process.platform}.`);
  }
  const layout = verifySignedPackage();
  console.log(JSON.stringify({platform: process.platform, verified_paths: Object.values(layout).flat().map((path) => String(path).replace(desktopRoot + '/', ''))}, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main();
