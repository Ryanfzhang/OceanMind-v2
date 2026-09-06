import {execFileSync} from 'node:child_process';
import {mkdtemp, mkdir, readFile, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {test as base, expect, _electron, type ElectronApplication, type Page} from '@playwright/test';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const repo = resolve(desktop, '../..');
const python = process.env.OCEAN_PYTHON ?? join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');

export type Row = Record<string, any>;
export type ResearchApp = {
  app: ElectronApplication;
  page: Page;
  project: string;
  addProject: (name: string) => Promise<string>;
  createTask: (name: string, method?: 'enter' | 'button') => Promise<void>;
  submit: (text: string) => Promise<void>;
  rows: (table: string, project?: string) => Row[];
  audit: () => Promise<Row[]>;
};

export const test = base.extend<{research: ResearchApp}>({
  research: async ({}, use, testInfo) => {
    const root = await mkdtemp(join(tmpdir(), 'ocean-interaction-e2e-'));
    const env = {...process.env};
    // No inherited model credentials, remote endpoints, or developer config.
    for (const key of Object.keys(env)) {
      if (/API_KEY|TOKEN|SECRET|ANTHROPIC|OPENAI|DEEPSEEK|OCEANMIND_CONFIG/.test(key)) delete env[key];
    }
    const app = await _electron.launch({
      args: [join(desktop, 'dist-electron/main.js')],
      env: {...env, OCEAN_PYTHON: python, PYTHONPATH: join(repo, 'src'),
        OCEAN_DESKTOP_TEST_MODE: '1', OCEAN_DESKTOP_TEST_ISOLATED_PROFILE: '1',
        OCEAN_DESKTOP_TEST_BACKEND_SCRIPT: join(desktop, 'tests/fixtures/research_backend.py'),
        OCEANMIND_CONFIG_DIR: join(root, 'config')},
    });
    try {
      const page = await app.firstWindow();
      const errors: string[] = [];
      const frames: unknown[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.exposeFunction('recordE2EFrame', (frame: unknown) => frames.push(frame));
      await page.addInitScript(() => document.addEventListener('DOMContentLoaded', () => {
        (window as any).oceanDesktop.onBackendFrame((frame: unknown) => (window as any).recordE2EFrame(frame));
      }));
      await page.evaluate(() => {
        (window as any).oceanDesktop.onBackendFrame((frame: unknown) => (window as any).recordE2EFrame(frame));
      });
      await page.context().route(/^https?:\/\//, (route) => route.abort());
      await app.evaluate(({dialog, shell}) => {
        const state = globalThis as typeof globalThis & {e2eFolder?: string; e2eOpened?: string[]};
        state.e2eOpened = [];
        dialog.showOpenDialog = async () => ({canceled: !state.e2eFolder, filePaths: state.e2eFolder ? [state.e2eFolder] : []});
        // The OS application is outside the test. Record the final authorized path.
        shell.openPath = async (path) => {state.e2eOpened!.push(path); return '';};
      });
      const addProject = async (name: string) => {
        const path = join(root, name);
        await mkdir(path);
        await writeFile(join(path, '.e2e-project'), 'Disposable Playwright project');
        await app.evaluate((_electron, path) => {Object.assign(globalThis, {e2eFolder: path});}, path);
        await page.locator('.project-list-heading').hover();
        await page.getByRole('button', {name: 'Add local project', exact: true}).click();
        await expect(page.locator('.project-row').filter({hasText: name})).toBeVisible();
        await expect(page.getByRole('textbox', {name: 'Research task name'})).toBeVisible();
        return path;
      };
      const project = await addProject('Project-A');
      const rows = (table: string, target = project): Row[] => {
        if (!/^[a-z_]+$/.test(table)) throw new Error('Invalid test table');
        return JSON.parse(execFileSync(python, ['-c',
          'import json,sqlite3,sys; from pathlib import Path; c=sqlite3.connect(Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True); c.row_factory=sqlite3.Row; print(json.dumps([dict(r) for r in c.execute("SELECT * FROM "+sys.argv[2])]))',
          join(target, '.oceanmind/workspace.sqlite3'), table], {encoding: 'utf8'}));
      };
      const audit = async () => {
        try {return (await readFile(join(project, 'fixture-audit.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map((line) => JSON.parse(line));}
        catch (error) {if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []; throw error;}
      };
      const createTask = async (name: string, method: 'enter' | 'button' = 'enter') => {
        const input = page.getByRole('textbox', {name: 'Research task name'});
        await input.fill(name);
        if (method === 'enter') await input.press('Enter');
        else await page.getByRole('button', {name: 'Create research task'}).click();
        await expect(page.locator('.conversation-title')).toHaveText(name, {timeout: 15_000});
        await expect(page.locator('.composer textarea')).toBeEnabled();
      };
      const submit = async (text: string) => {
        await page.locator('.composer textarea').fill(text);
        await page.locator('.composer textarea').press('Enter');
      };
      try {
        await use({app, page, project, addProject, createTask, submit, rows, audit});
        expect(errors, 'No uncaught renderer errors').toEqual([]);
      } finally {
        if (testInfo.status !== testInfo.expectedStatus) {
          if (!page.isClosed()) {
            const path = testInfo.outputPath('failure.png');
            await page.screenshot({path, fullPage: true});
            await testInfo.attach('failure.png', {path, contentType: 'image/png'});
          }
          for (const [name, value] of [['renderer-errors', errors], ['fixture-audit', await audit()], ['backend-frames', frames]] as const) {
            const path = testInfo.outputPath(`${name}.json`);
            await writeFile(path, JSON.stringify(value, null, 2));
            await testInfo.attach(name, {path, contentType: 'application/json'});
          }
        }
        // Retain isolated projects for failure inspection; never remove user directories.
        await testInfo.attach('isolated-project.txt', {body: root, contentType: 'text/plain'});
      }
    } finally {
      await app.close();
    }
  },
});
export {expect};
