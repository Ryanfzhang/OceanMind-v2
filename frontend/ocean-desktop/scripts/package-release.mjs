import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

import {assertReleaseTarget, sidecarArchitectures} from './release-target.mjs';
import {assertReleaseSigningEnvironment, signedElectronBuilderArguments} from './release-signing.mjs';

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const executable = resolve(
  desktopRoot,
  'sidecar',
  'ocean-backend',
  process.platform === 'win32' ? 'ocean-backend.exe' : 'ocean-backend',
);

const target = process.argv[2];
const signed = process.argv.slice(3).includes('--signed');
const expectedPlatform = target === 'mac' ? 'darwin' : target === 'win' ? 'win32' : null;
if (!expectedPlatform || process.argv.slice(3).some((argument) => argument !== '--signed')) {
  throw new Error('Usage: node scripts/package-release.mjs <mac|win> [--signed]');
}
if (process.platform !== expectedPlatform) {
  throw new Error(`A ${target} release must be built on ${expectedPlatform}; current platform is ${process.platform}.`);
}
if (!existsSync(executable)) {
  throw new Error(`Frozen sidecar is missing: ${executable}. Run npm run build:sidecar with a native target Python first.`);
}
if (signed) assertReleaseSigningEnvironment(process.platform);

const release = assertReleaseTarget({
  platform: process.platform,
  nodeArchitecture: process.arch,
  sidecarArchitectures: sidecarArchitectures(process.platform, executable),
});

const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const npx = process.platform === 'win32' ? 'npx.cmd' : 'npx';
function run(command, args) {
  const result = spawnSync(command, args, {cwd: desktopRoot, stdio: 'inherit'});
  if (result.error) throw new Error(`Could not start ${command}: ${result.error.message}`);
  if (result.status !== 0) process.exit(result.status ?? 1);
}

run(npm, ['run', 'build']);
run(npm, ['run', 'verify:sidecar']);
if (target === 'mac') {
  run(npx, ['electron-builder', '--mac', 'zip', 'dmg', `--${release.architecture}`, ...(signed ? signedElectronBuilderArguments(process.platform) : [])]);
} else {
  run(npx, ['electron-builder', '--win', 'nsis', `--${release.architecture}`, ...(signed ? signedElectronBuilderArguments(process.platform) : [])]);
}
run(npm, ['run', 'verify:package']);
run(npm, ['run', 'verify:package-launch']);
if (signed) run(npm, ['run', 'verify:signature', '--', target]);
