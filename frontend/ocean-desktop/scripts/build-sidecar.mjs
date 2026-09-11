import {spawnSync} from 'node:child_process';
import {delimiter, dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {developmentPython} from '../src/shared/python-environment.mjs';

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(desktopRoot, '..', '..');
const sourceRoot = resolve(repositoryRoot, 'src');
const python = developmentPython();
console.log(`Building OceanX backend with Python: ${python}`);
const result = spawnSync(python, [
  resolve(repositoryRoot, 'scripts', 'build_desktop_sidecar.py'),
  '--output', resolve(desktopRoot, 'sidecar', 'ocean-backend'),
  '--work', resolve(desktopRoot, '.sidecar-build'),
], {
  cwd: repositoryRoot,
  env: {
    ...process.env,
    PYTHONPATH: [sourceRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter),
  },
  stdio: 'inherit',
});

if (result.error) {
  throw new Error(`Could not start ${python}: ${result.error.message}`);
}
process.exitCode = result.status ?? 1;
