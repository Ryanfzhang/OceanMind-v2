import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const electronExecutable = require('electron');
const environment = {...process.env};

// Some automation shells set this for Electron-as-Node jobs; an app launch must not inherit it.
delete environment.ELECTRON_RUN_AS_NODE;

const child = spawn(electronExecutable, ['.'], {env: environment, stdio: 'inherit'});
child.once('error', (error) => {
  console.error(error.message);
  process.exitCode = 1;
});
child.once('exit', (code, signal) => {
  process.exitCode = code ?? (signal ? 1 : 0);
});
