import {mkdirSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

import {build} from 'esbuild';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const desktopRoot = resolve(scriptDirectory, '..');
const output = resolve(desktopRoot, 'dist-electron', 'preload.cjs');

mkdirSync(dirname(output), {recursive: true});
await build({
  entryPoints: [resolve(desktopRoot, 'src', 'preload.ts')],
  outfile: output,
  bundle: true,
  format: 'cjs',
  platform: 'node',
  target: 'node22',
  external: ['electron'],
});
