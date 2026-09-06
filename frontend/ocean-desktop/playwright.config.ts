import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: {timeout: 10_000},
  workers: 1,
  reporter: 'list',
  use: {trace: 'retain-on-failure'},
});
