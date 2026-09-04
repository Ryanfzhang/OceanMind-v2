import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {request} from 'node:http';
import {resolve} from 'node:path';

const require = createRequire(import.meta.url);
const electronExecutable = require('electron');
const viteExecutable = resolve('node_modules/vite/bin/vite.js');
const rendererUrl = 'http://127.0.0.1:5177';
const environment = {...process.env};
delete environment.ELECTRON_RUN_AS_NODE;

const vite = spawn(process.execPath, [viteExecutable, '--host', '127.0.0.1', '--port', '5177'], {
  env: environment,
  stdio: 'inherit',
});

function rendererReady() {
  return new Promise((resolveReady) => {
    const probe = () => {
      const call = request(rendererUrl, (response) => {
        response.resume();
        if ((response.statusCode ?? 500) < 500) resolveReady();
        else setTimeout(probe, 120);
      });
      call.once('error', () => setTimeout(probe, 120));
      call.end();
    };
    probe();
  });
}

await rendererReady();
const electron = spawn(electronExecutable, ['.'], {
  env: {...environment, ELECTRON_RENDERER_URL: rendererUrl},
  stdio: 'inherit',
});

const stop = () => {
  if (electron.exitCode === null) electron.kill('SIGTERM');
  if (vite.exitCode === null) vite.kill('SIGTERM');
};
process.once('SIGINT', stop);
process.once('SIGTERM', stop);
electron.once('error', (error) => {
  console.error(error.message);
  stop();
  process.exitCode = 1;
});
electron.once('exit', (code, signal) => {
  if (vite.exitCode === null) vite.kill('SIGTERM');
  process.exitCode = code ?? (signal ? 1 : 0);
});
