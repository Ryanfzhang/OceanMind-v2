import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {delimiter, dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(desktopRoot, '..', '..');
const sourceRoot = resolve(repositoryRoot, 'src');
const projectPython = resolve(
  repositoryRoot,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
const python = process.env.OCEAN_PYTHON
  || (existsSync(projectPython) ? projectPython : (process.platform === 'win32' ? 'py' : 'python3'));
const pythonArgs = process.platform === 'win32' && !process.env.OCEAN_PYTHON && !existsSync(projectPython) ? ['-3'] : [];
const result = spawnSync(python, [
  ...pythonArgs,
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
