import {test, expect} from './fixtures/research-app.js';

for (const kind of ['CHART', 'MAP']) {
  test(`object citations focus saved ${kind} geometry and survive reload`, async ({research: r}, testInfo) => {
    const {page} = r;
    await r.createTask(`Generic ${kind} objects`);
    await r.submit(`OBJECTS ${kind}`);
    const answer = page.locator('.message.assistant');
    await expect(answer.getByRole('button', {name: 'Open First object', exact: true})).toBeVisible();
    await expect(answer.getByText('Wrong name', {exact: true})).toHaveCount(0);
    await expect(answer.getByText(/Result object unavailable: missing/)).toBeVisible();
    await answer.getByRole('button', {name: 'Open First object', exact: true}).click();
    const objects = page.getByRole('navigation', {name: 'Result objects'});
    const focus = objects.getByRole('combobox', {name: 'Focus object'});
    await expect(focus).toHaveValue('first');
    await expect(objects.getByRole('region', {name: 'Selected object details'})).toContainText('First object');
    if (kind === 'CHART') {
      await expect(page.locator('[data-feature-id="first"]')).toHaveAttribute('data-selected', 'true');
      await expect(page.locator('[data-feature-id="first"] [data-mask-part="fill"]')).toHaveAttribute('stroke', 'none');
      await expect(page.locator('[data-feature-id="first"] [data-mask-part="halo"]')).toHaveAttribute('stroke', 'white');
      await expect(page.locator('[data-feature-id="first"] [data-mask-part="boundary"]')).toBeVisible();
    } else {
      await expect(page.getByRole('button', {name: 'First object', exact: true})).toBeVisible();
      await expect(page.getByLabel('Field categories')).toContainText('1: Group A');
      await expect(page.getByLabel('Field categories')).toContainText('2: Group B');
    }
    await answer.getByRole('button', {name: 'Open Second object', exact: true}).click();
    await expect(focus).toHaveValue('second');
    await focus.selectOption('first');
    await expect(focus).toHaveValue('first');
    if (kind === 'MAP') {
      const selected = page.getByRole('button', {name: 'First object', exact: true});
      await expect(selected).toHaveAttribute('aria-pressed', 'true');
      await expect.poll(async () => {
        const marker = await selected.boundingBox();
        const stage = await page.locator('.spatial-map-stage').boundingBox();
        return Boolean(marker && stage && Math.abs(marker.x+marker.width/2-stage.x-stage.width/2) < stage.width*.15 && Math.abs(marker.y+marker.height/2-stage.y-stage.height/2) < stage.height*.15);
      }).toBe(true);
    } else {
      await expect(page.locator('[data-feature-id="first"]')).toHaveAttribute('data-selected', 'true');
      await expect(page.locator('.scientific-tick')).toHaveCount(10);
    }
    await page.screenshot({path: testInfo.outputPath('object-selected.png')});
    await objects.getByRole('button', {name: 'Clear object selection'}).click();
    await expect(focus).toHaveValue('');
    await expect(objects.getByRole('region', {name: 'Selected object details'})).toHaveCount(0);
    await page.reload();
    await page.getByRole('button', {name: `Generic ${kind} objects`, exact: true}).click();
    await page.locator('.message.assistant').getByRole('button', {name: 'Open Second object', exact: true}).click();
    await expect(focus).toHaveValue('second');
  });
}

test('dense masks and long labels stay compact without modifying saved geometry', async ({research: r}, testInfo) => {
  const {page} = r;
  await r.createTask('Dense generic masks');
  await r.submit('OBJECTS MAP DENSE');
  await page.locator('.message.assistant .markdown-result-link').first().click();
  const focus = page.getByRole('combobox', {name: 'Focus object'});
  await expect(focus).toHaveValue('first');
  await focus.selectOption('');
  await expect(page.locator('.result-feature-marker')).toHaveCount(5);
  await expect(page.locator('.result-feature-marker')).toHaveText(['1', '2', '3', '4', '5']);
  await expect(page.getByRole('region', {name: 'Selected object details'})).toHaveCount(0);
  await expect(page.getByLabel('Field color scale')).toContainText('signal · units');
  const toolbar = await page.getByRole('navigation', {name: 'Result objects'}).boundingBox();
  expect(toolbar!.height).toBeLessThan(48);
  for (const marker of await page.locator('.result-feature-marker').all()) {
    expect((await marker.boundingBox())!.width).toBeLessThan(30);
  }
  // Wait for the all-object camera transition without fixed sleeps.
  await expect.poll(async () => {
    const stage = (await page.locator('.spatial-map-stage').boundingBox())!;
    const markers = await Promise.all((await page.locator('.result-feature-marker').all()).map(marker => marker.boundingBox()));
    return markers.every(marker => marker && marker.y > stage.y && marker.y+marker.height < stage.y+stage.height);
  }).toBe(true);
  await page.screenshot({path: testInfo.outputPath('dense-mask-overview.png')});
  await page.locator('.result-feature-marker').nth(2).click();
  await expect(focus).toHaveValue('region_3');
  await expect(page.getByRole('region', {name: 'Selected object details'})).toContainText('Region 3: selected cells');
  await expect(page.locator('.result-feature-marker').nth(2)).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({path: testInfo.outputPath('dense-mask-selected.png')});
});
