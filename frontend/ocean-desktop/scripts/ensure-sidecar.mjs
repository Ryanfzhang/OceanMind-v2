import {readdirSync, statSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(desktopRoot, '..', '..');
const executable = resolve(
  desktopRoot,
  'sidecar',
  'ocean-backend',
  process.platform === 'win32' ? 'ocean-backend.exe' : 'ocean-backend',
);

function newestModification(path) {
  let stat;
  try {stat = statSync(path);}
  catch {return 0;}
  if (!stat.isDirectory()) return stat.mtimeMs;
  return readdirSync(path, {withFileTypes: true}).reduce((latest, entry) => (
    Math.max(latest, newestModification(resolve(path, entry.name)))
  ), stat.mtimeMs);
}

const inputs = [
  resolve(repositoryRoot, 'src', 'oceanx'),
  resolve(repositoryRoot, 'scripts', 'ocean_desktop_sidecar_entry.py'),
  resolve(repositoryRoot, 'scripts', 'build_desktop_sidecar.py'),
  resolve(repositoryRoot, 'pyproject.toml'),
  resolve(repositoryRoot, 'requirements-ocean.txt'),
];
const newestInput = Math.max(...inputs.map(newestModification));
const executableTime = newestModification(executable);

if (!executableTime || executableTime < newestInput) {
  const result = spawnSync(process.execPath, [resolve(desktopRoot, 'scripts', 'build-sidecar.mjs')], {
    cwd: desktopRoot,
    env: process.env,
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
