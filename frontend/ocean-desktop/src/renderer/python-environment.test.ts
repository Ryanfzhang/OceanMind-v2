import {describe, expect, it, vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {developmentPython} from '../shared/python-environment.mjs';

describe('desktop Python environment', () => {
  it.each(['darwin', 'linux'])('uses activated Conda on %s, even when another Python exists', (platform) => {
    const exists = vi.fn(() => true);
    expect(developmentPython({env: {CONDA_PREFIX: '/envs/oceanx', PATH: '/old/.venv/bin'}, platform, exists}))
      .toBe('/envs/oceanx/bin/python');
    expect(exists).toHaveBeenCalledExactlyOnceWith('/envs/oceanx/bin/python');
  });

  it('uses the Windows Conda layout, including spaces', () => {
    expect(developmentPython({env: {CONDA_PREFIX: 'C:\\Conda Envs\\oceanx'}, platform: 'win32', exists: () => true}))
      .toBe('C:\\Conda Envs\\oceanx\\python.exe');
  });

  it('preserves an explicit OCEAN_PYTHON override', () => {
    expect(developmentPython({env: {OCEAN_PYTHON: '/chosen/python', CONDA_PREFIX: '/broken'}, exists: () => false}))
      .toBe('/chosen/python');
  });

  it('fails instead of silently falling back when active Conda is broken', () => {
    expect(() => developmentPython({env: {CONDA_PREFIX: '/missing'}, platform: 'darwin', exists: () => false}))
      .toThrow('Activated Conda Python is missing');
  });

  it.each([['darwin', 'python3'], ['linux', 'python3'], ['win32', 'python.exe']])(
    'uses PATH without probing a checkout .venv on %s', (platform, expected) => {
      const exists = vi.fn(() => true);
      expect(developmentPython({env: {}, platform, exists})).toBe(expected);
      expect(exists).not.toHaveBeenCalled();
    },
  );

  it('shares selection between build and development, leaving packaged startup separate', () => {
    const main = readFileSync(new URL('../main.ts', import.meta.url), 'utf8');
    const build = readFileSync(new URL('../../scripts/build-sidecar.mjs', import.meta.url), 'utf8');
    expect(main).toContain("from './shared/python-environment.mjs'");
    expect(build).toContain("from '../src/shared/python-environment.mjs'");
    const selection = main.slice(main.indexOf('function defaultPythonExecutable()'), main.indexOf('function parseDesktopBackendLaunch'));
    expect(selection).not.toContain('.venv');
    expect(selection.indexOf('if (app.isPackaged)')).toBeLessThan(selection.indexOf('return developmentPython()'));
    expect(selection).toContain('return executable;');
  });
});
