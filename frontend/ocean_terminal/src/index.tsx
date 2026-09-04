import React from 'react';
import {render} from 'ink';

import {App} from './App.js';
import type {OceanTerminalConfig} from './types.js';

const config = JSON.parse(process.env.OCEAN_TERMINAL_CONFIG ?? '{}') as OceanTerminalConfig;

render(<App config={config} />);
