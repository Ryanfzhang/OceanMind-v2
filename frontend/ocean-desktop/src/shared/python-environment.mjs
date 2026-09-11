import {existsSync} from 'node:fs';
import {posix, win32} from 'node:path';

// Both local desktop startup and sidecar builds use the activated environment.
// A checkout's stale .venv must never override Conda activation.
export function developmentPython({env = process.env, platform = process.platform, exists = existsSync} = {}) {
  if (env.OCEAN_PYTHON) return env.OCEAN_PYTHON;
  if (env.CONDA_PREFIX) {
    const python = platform === 'win32'
      ? win32.join(env.CONDA_PREFIX, 'python.exe')
      : posix.join(env.CONDA_PREFIX, 'bin', 'python');
    if (!exists(python)) {
      throw new Error(`Activated Conda Python is missing: ${python}. Reactivate the intended environment; OceanX will not fall back to another Python.`);
    }
    return python;
  }
  return platform === 'win32' ? 'python.exe' : 'python3';
}
