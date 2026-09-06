import {readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {test, expect} from './fixtures/research-app.js';

test('project/task creation, hover actions, stable ordering, and persistence', async ({research: r}) => {
  const {page} = r;
  await r.createTask('Task One', 'enter');
  await r.submit('Review the first task');
  await expect(page.locator('.conversation-exchange > .message.assistant').getByText('Conclusion for Task One: fixture evidence reviewed.', {exact: true})).toBeVisible();
  const group = page.locator('.project-group').filter({hasText: 'Project-A'});
  await page.locator('.conversation-title').hover();
  await expect(group.locator('.project-actions')).toHaveCSS('opacity', '0');
  await group.locator('.project-heading-row').hover();
  await page.getByRole('button', {name: 'New task in Project-A', exact: true}).click();
  await r.createTask('Task Two', 'button');
  await r.submit('Review the second task');
  await expect(page.locator('.conversation-exchange > .message.assistant').getByText('Conclusion for Task Two: fixture evidence reviewed.', {exact: true})).toBeVisible();
  expect(r.rows('research_tasks').map((t) => t.title).sort()).toEqual(['Task One', 'Task Two']);
  await r.addProject('Project-B');
  await r.createTask('Other project');
  const before = await page.locator('.project-row strong').allTextContents();
  await page.getByRole('button', {name: 'Task One', exact: true}).click();
  await expect(page.locator('.conversation-title')).toHaveText('Task One');
  await expect(page.getByText('Conclusion for Task One: fixture evidence reviewed.', {exact: true})).toBeVisible();
  await expect(page.getByText('Conclusion for Task Two: fixture evidence reviewed.', {exact: true})).toHaveCount(0);
  await expect(page.locator('.project-row strong')).toHaveText(before);
  const taskOrder = await group.locator('.task-row > button:first-child').allTextContents();
  await page.getByRole('button', {name: 'Task Two', exact: true}).click();
  await expect(group.locator('.task-row > button:first-child')).toHaveText(taskOrder);
  await page.reload();
  await expect(page.locator('.project-row strong')).toHaveText(before);
  await expect(page.getByRole('button', {name: 'Task One', exact: true})).toBeVisible();
});

test('paper selection stays inline and resumes the same request and Expert', async ({research: r}) => {
  const {page} = r;
  await r.createTask('Paper workflow');
  await r.submit('PAPERS');
  const selection = page.getByRole('region', {name: 'Choose papers', exact: true});
  await expect(selection).toBeVisible();
  await expect(page.getByText(/Candidate paper details:/)).toBeVisible();
  await expect(selection.getByRole('columnheader')).toHaveCount(2);
  const use = selection.getByRole('button', {name: 'Use selected papers and continue'});
  await expect(use).toBeDisabled();
  const active = r.rows('research_tasks')[0].active_request_id;
  expect(active).toBeTruthy();
  await selection.getByRole('checkbox', {name: 'Select all papers', exact: true}).check();
  await selection.getByRole('checkbox', {name: 'Select Paper Beta', exact: true}).uncheck();
  await use.dblclick();
  await expect(selection).toHaveCount(0);
  await expect(page.locator('.conversation-exchange > .message.assistant').getByText('Conclusion for Paper workflow: fixture evidence reviewed. Selected papers: alpha', {exact: true})).toBeVisible();
  await expect.poll(() => r.rows('research_tasks')[0].active_request_id).toBeNull();
  const submits = r.rows('request_records').filter((record) => record.request_type === 'session.submit');
  expect(submits).toHaveLength(1);
  expect(submits[0].request_id).toBe(active);
  expect(r.rows('task_interactions').map((i) => i.state)).toEqual(['answered']);
  const audit = await r.audit();
  expect(audit.filter((e) => e.kind === 'papers_selected')).toHaveLength(1);
  const experts = audit.filter((e) => e.kind === 'expert_started');
  expect(experts).toHaveLength(2);
  expect(new Set(experts.map((e) => e.job_key)).size).toBe(1);
  expect(new Set(experts.map((e) => JSON.stringify(e.session_key))).size).toBe(1);
  await page.reload();
  await page.getByRole('button', {name: 'Paper workflow', exact: true}).click();
  await expect(page.getByText(/Conclusion for Paper workflow:/)).toBeVisible();
  await expect(page.getByRole('region', {name: 'Choose papers', exact: true})).toHaveCount(0);
});

for (const scenario of ['WAIT_RUNNING', 'PAPERS']) {
  test(`interrupt ${scenario} and submit a follow-up`, async ({research: r}) => {
    const {page} = r;
    await r.createTask(`Cancel ${scenario}`);
    await r.submit(scenario);
    if (scenario === 'PAPERS') await expect(page.getByRole('region', {name: 'Choose papers', exact: true})).toBeVisible();
    else await expect(page.getByText('Fixture analysis is running.', {exact: true})).toBeVisible();
    await page.locator('.send-button.stop').click();
    await expect(page.locator('.send-button.stop')).toHaveCount(0);
    await expect(page.locator('.composer textarea')).toBeEnabled();
    await expect.poll(() => r.rows('research_tasks')[0].active_request_id).toBeNull();
    expect(r.rows('task_interactions').every((i) => i.state !== 'pending')).toBe(true);
    await expect.poll(async () => (await r.audit()).filter((e) => e.kind === 'model_cancelled').length).toBe(1);
    await r.submit('Continue after interruption');
    await expect(page.locator('.conversation-exchange > .message.assistant').getByText(`Conclusion for Cancel ${scenario}: fixture evidence reviewed.`, {exact: true})).toBeVisible();
    expect(r.rows('request_records').filter((i) => i.request_type === 'session.submit')).toHaveLength(2);
    await page.reload();
    await page.getByRole('button', {name: `Cancel ${scenario}`, exact: true}).click();
    await expect(page.locator('.send-button.stop')).toHaveCount(0);
    await expect(page.locator('.composer textarea')).toBeEnabled();
  });
}

test('historical Canvas, answer order, and editable notebook links survive follow-up and reload', async ({research: r}) => {
  const {page} = r;
  await r.createTask('Notebook workflow');
  await r.submit('RESULT first round');
  const notebook = page.getByRole('button', {name: 'analysis.ipynb', exact: true});
  await expect(notebook).toHaveCount(1);
  const exchange = page.locator('.conversation-exchange').first();
  const order = await exchange.evaluate((element) => {
    const targets = ['.message.user', '.request-elapsed-time', '.agent-canvas', '.message.assistant', '.supplementary-materials'];
    return targets.map((selector) => element.querySelector(selector)?.getBoundingClientRect().top ?? null);
  });
  expect(order.every((top) => top !== null), 'All final presentation sections exist').toBe(true);
  expect(order).toEqual([...order].sort((a, b) => a! - b!));
  await notebook.click();
  await expect.poll(() => r.app.evaluate(() => (globalThis as any).e2eOpened.length)).toBe(1);
  const firstPath = await r.app.evaluate(() => (globalThis as any).e2eOpened[0] as string);
  const edited = JSON.parse(await readFile(firstPath, 'utf8'));
  edited.metadata.user_note = 'Keep my edits';
  await writeFile(firstPath, JSON.stringify(edited));
  await r.submit('RESULT second round');
  await expect(notebook).toHaveCount(2);
  await notebook.nth(1).click();
  await expect.poll(() => r.app.evaluate(() => (globalThis as any).e2eOpened.length)).toBe(2);
  const secondPath = await r.app.evaluate(() => (globalThis as any).e2eOpened[1] as string);
  expect(secondPath).not.toBe(firstPath);
  expect(JSON.parse(await readFile(firstPath, 'utf8')).metadata.user_note).toBe('Keep my edits');
  await page.reload();
  await page.getByRole('button', {name: 'Notebook workflow', exact: true}).click();
  await expect(notebook).toHaveCount(2);
  await expect(page.locator('.agent-canvas')).toHaveCount(2);
  await notebook.first().click();
  await expect.poll(() => r.app.evaluate(() => (globalThis as any).e2eOpened.at(-1))).toBe(firstPath);
});

test('a Curator commit refreshes the open task without navigation', async ({research: r}) => {
  const {page} = r;
  await r.createTask('Learning workflow');
  await r.submit('Complete a bounded analysis');
  await expect(page.locator('.conversation-exchange > .message.assistant').getByText('Conclusion for Learning workflow: fixture evidence reviewed.', {exact: true})).toBeVisible();
  const task = r.rows('research_tasks')[0];
  const request = r.rows('request_records').find((r) => r.request_type === 'session.submit')!;
  await writeFile(join(r.project, 'review-experience.json'), JSON.stringify({workspace_id: task.workspace_id, task_id: task.task_id, request_id: request.request_id}));
  await expect(page.getByText('e2e-coordinate-check', {exact: true})).toBeVisible();
  await expect(page.locator('.conversation-title')).toHaveText('Learning workflow');
  expect(r.rows('request_records').filter((r) => r.request_type === 'session.submit')).toHaveLength(1);
  expect(r.rows('evolved_skill_documents')).toHaveLength(1);
  await page.reload();
  await page.getByRole('button', {name: 'Learning workflow', exact: true}).click();
  await expect(page.getByText('e2e-coordinate-check', {exact: true})).toBeVisible();
});

test('Chinese task creation and input copy', async ({research: r}) => {
  const {page} = r;
  await page.evaluate(() => localStorage.setItem('oceanmind.ui-language.v1', 'zh'));
  await page.reload();
  await expect(page.getByText('你想用 OceanMind 做什么？', {exact: true})).toBeVisible();
  const input = page.getByRole('textbox', {name: '研究任务名称'});
  await expect(input).toHaveAttribute('placeholder', '给这项研究起个名字');
  await input.fill('中文研究任务');
  await input.press('Enter');
  await expect(page.locator('.conversation-title')).toHaveText('中文研究任务', {timeout: 15_000});
  await expect(page.locator('.composer textarea')).toHaveAttribute('placeholder', '询问这个研究任务');
  expect(r.rows('research_tasks')[0].title).toBe('中文研究任务');
});
