import { expect, test, type Locator, type Page } from '@playwright/test';
import { env, evidenceShot, newUser } from './helpers';

const project = () => env('E2E_PROJECT_ID');

async function expectStatusBadgesInsideRows(rows: Locator, statusTestId: string) {
  const count = await rows.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index++) {
    const row = rows.nth(index);
    const badge = row.getByTestId(statusTestId);
    await expect(badge).toBeVisible();
    const rowBox = await row.boundingBox();
    const badgeBox = await badge.boundingBox();
    expect(rowBox).not.toBeNull();
    expect(badgeBox).not.toBeNull();
    expect(badgeBox!.x).toBeGreaterThanOrEqual(rowBox!.x);
    expect(badgeBox!.x + badgeBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width + 0.5);
  }
}

async function expectNoDocumentOverflow(page: Page) {
  const { clientWidth, scrollWidth } = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
}

test('repo analytics and commits stay scoped to seeded payments-lite data', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const repoId = env('E2E_REPO_ID');
  const commit = env('E2E_COMMIT');

  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
    await page.goto(`/projects/${project()}?repo=${repoId}`);
    await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
    await expect(page.getByText(/From Ari's saved records for acme\/payments-lite/)).toBeVisible();
    await expect(page.getByText('Checks run', { exact: true })).toBeVisible();
    await expectNoDocumentOverflow(page);

    await page.goto(`/projects/${project()}/commits?repo=${repoId}&commit=${commit}`);
    await expect(page.getByRole('heading', { name: 'Commits', exact: true })).toBeVisible();
    await expect(page.getByTestId(`commit-row-${commit}`)).toBeVisible();
    await page.getByRole('tab', { name: '7 agents', exact: true }).click();
    const agents = page.getByTestId('agent-team');
    await expect(agents).toBeVisible();
    for (const agent of ['scout', 'probe', 'trace', 'diagnose', 'patch', 'verify', 'fortify']) {
      await expect(agents.getByTestId(`agent-${agent}`)).toBeVisible();
    }
    await expectNoDocumentOverflow(page);
  }
});

test('owner edits project settings and they persist on reload', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  await page.goto(`/projects/${project()}/settings`);
  const form = page.getByRole('form', { name: 'Project settings' });
  const interval = form.getByLabel('Minimum interval between reassessments (s)');
  await expect(interval).toHaveValue(/\d+/);
  const updatedInterval = await interval.inputValue() === '900' ? '901' : '900';
  // The form renders the action row at the top and the bottom; scope to the bottom
  // row, which also carries the "Saved." status text.
  const save = form.getByRole('button', { name: 'Save settings' }).last();
  await expect(save).toBeDisabled();
  await interval.fill('abc');
  await expect(form.getByRole('alert')).toContainText('Minimum interval');
  await interval.fill(updatedInterval);
  await save.click();
  await expect(form.getByRole('status')).toContainText('Saved.');
  await page.reload();
  await expect(page.getByLabel('Minimum interval between reassessments (s)')).toHaveValue(updatedInterval);
  await evidenceShot(page, 'settings');
});

test('owner opts into defense from OFF and the setting persists after reload', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const me = await page.request.get(`${env('E2E_API')}/me`);
  const created = await page.request.post(`${env('E2E_API')}/projects`, {
    data: { name: 'Defense opt-in' },
    headers: { 'X-CSRF-Token': (await me.json()).csrf_token },
  });
  expect(created.status()).toBe(201);
  const fresh = await created.json();
  await page.goto(`/projects/${fresh.id}/monitoring`);
  const toggle = page.getByRole('switch', { name: '24/7 defense' });
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await expect(toggle).toBeEnabled();
  await page.reload();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await expect(toggle).toBeEnabled();
  await page.reload();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
});

test('simulated incident: triage, owner, two-person containment approval', async ({ browser }) => {
  const alice = await newUser(browser, 'e2e-alice');
  const bob = await newUser(browser, 'e2e-bob');
  const title = `drill-${Date.now()}`;
  await alice.goto(`/projects/${project()}/incidents`);
  await alice.getByLabel('Title').fill(title);
  await alice.getByLabel('Initial report').fill('Monitoring flagged cross-customer payment reads');
  await alice.getByRole('button', { name: 'Open incident' }).click();
  const note = alice.getByLabel('Note / rationale');
  await expect(alice.getByText('Synthetic drill', { exact: true })).toBeVisible();

  await note.fill('Reproduced in sandbox');
  await alice.getByLabel('Category').fill('broken access control');
  await alice.getByRole('button', { name: 'Record triage' }).click();
  await expect(alice.getByRole('list', { name: 'Lifecycle' }).locator('[aria-current="step"]')).toHaveText('Triaged');

  await alice.getByRole('button', { name: 'Move to Analysis' }).click();
  await expect(alice.getByText('assign an incident owner before analysis')).toBeVisible();
  await alice.getByRole('button', { name: 'Assign to me' }).click();
  await alice.getByRole('button', { name: 'Move to Analysis' }).click();
  await expect(alice.getByRole('list', { name: 'Lifecycle' }).locator('[aria-current="step"]')).toHaveText('Analysis');

  await note.fill('Block cross-tenant read path');
  await alice.getByRole('button', { name: 'Request containment approval' }).click();
  await expect(alice.getByRole('button', { name: 'Approve' })).toBeDisabled();

  await bob.goto(`/projects/${project()}/incidents`);
  await bob.locator('[data-testid^="incident-"]').filter({ hasText: title }).click();
  await bob.getByRole('button', { name: 'Approve' }).click();
  await expect(bob.getByRole('button', { name: 'Move to Containment' })).toBeVisible();
  await bob.getByRole('button', { name: 'Move to Containment' }).click();
  await expect(bob.getByRole('list', { name: 'Lifecycle' }).locator('[aria-current="step"]')).toHaveText('Containment');
  await evidenceShot(bob, 'incident');
});

test('incident closure: link verified finding, criteria with evidence, communication', async ({ browser }) => {
  // Runs after journey.spec (workers: 1, file order), which leaves a finding with an accepted Verify.
  const alice = await newUser(browser, 'e2e-alice');
  const bob = await newUser(browser, 'e2e-bob');
  alice.setDefaultTimeout(20_000);
  bob.setDefaultTimeout(20_000);
  const title = `closure-${Date.now()}`;
  await alice.goto(`/projects/${project()}/incidents`);
  await alice.getByLabel('Title').fill(title);
  await alice.getByRole('button', { name: 'Open incident' }).click();
  const closure = alice.locator('#closure');
  await expect(closure.getByText('Before closure')).toBeVisible();

  const finding = closure.getByLabel('Finding to link');
  const retained = finding.locator('option').filter({ hasText: /hardened/ }).first();
  await finding.selectOption((await retained.getAttribute('value'))!);
  await closure.getByRole('button', { name: 'Link finding' }).click();
  await expect(alice.getByText(/1 findings · 0 events · 0 verifications/)).toBeVisible();
  await closure.getByRole('button', { name: 'Link verification' }).click();
  await expect(alice.getByText(/1 findings · 0 events · 1 verifications/)).toBeVisible();

  await closure.getByLabel('Criterion key').fill('patch-verified');
  await closure.getByLabel('Criterion description').fill('Independent Verify accepted the patch');
  await closure.getByRole('button', { name: 'Add criterion' }).click();
  const criterion = closure.getByTestId('criterion-patch-verified');
  await expect(criterion.getByText('Open', { exact: true })).toBeVisible();
  await criterion.getByRole('button', { name: 'Mark satisfied' }).click();
  await expect(criterion.getByText('Satisfied', { exact: true })).toBeVisible();

  await closure.getByLabel('Channel').fill('#sec-incidents');
  await closure.getByLabel('Summary').fill('Engineering notified of containment plan');
  await closure.getByRole('button', { name: 'Record communication' }).click();
  await expect(closure.getByText('Engineering notified of containment plan')).toBeVisible();
  await expect(closure.getByLabel('Regression evidence')).toBeVisible();
  await closure.getByRole('textbox', { name: 'Lessons learned', exact: true }).fill('Retain cross-customer authorization regression sources');
  await expect(closure.getByRole('button', { name: 'Record lessons', exact: true })).toBeEnabled();
  await closure.getByRole('button', { name: 'Record lessons', exact: true }).click();
  await expect(closure.getByText('Retain cross-customer authorization regression sources').first()).toBeVisible();
  await alice.getByLabel('Category').fill('sandbox authorization');
  await alice.getByLabel('Note / rationale').fill('Verified sandbox regression retained');
  await alice.getByRole('button', { name: 'Record triage' }).click();
  await alice.getByRole('button', { name: 'Assign to me' }).click();
  await alice.getByRole('button', { name: 'Move to Analysis' }).click();
  for (const [gate, target] of [['containment', 'Containment'], ['eradication', 'Eradication'], ['recovery', 'Recovery'], ['closure', 'Closed']] as const) {
    await alice.getByLabel('Note / rationale').fill('Verified sandbox regression retained');
    await alice.getByRole('button', { name: `Request ${gate} approval` }).click();
    await bob.goto(`/projects/${project()}/incidents`);
    await bob.locator('[data-testid^="incident-"]').filter({ hasText: title }).click();
    await bob.getByRole('button', { name: 'Approve', exact: true }).click();
    await bob.getByRole('button', { name: `Move to ${target}` }).click();
    await expect(bob.getByRole('list', { name: 'Lifecycle' }).locator('[aria-current="step"]')).toHaveText(target);
    await alice.reload();
    await alice.locator('[data-testid^="incident-"]').filter({ hasText: title }).click();
  }
  await alice.reload();
  await alice.locator('[data-testid^="incident-"]').filter({ hasText: title }).click();
  await expect(alice.getByRole('list', { name: 'Lifecycle' }).locator('[aria-current="step"]')).toHaveText('Closed');
  await evidenceShot(alice, 'incident-closure');
});

test('expectations page shows the version-pinned snapshot view', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  await page.goto(`/projects/${project()}/expectations`);
  await expect(page.getByRole('button', { name: 'Expectations' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText(/No indexed snapshot|awaiting a human decision/)).toBeVisible();
  await evidenceShot(page, 'expectations');
});

test('findings and assessment history use contained mobile rows and desktop tables', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const repoId = env('E2E_REPO_ID');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/projects/${project()}/findings?repo=${repoId}`);
  await page.getByRole('tab', { name: 'Fixed (independently verified)' }).click();
  const findingsTable = page.getByTestId('findings-table-scroll');
  const findingsMobile = page.getByTestId('findings-mobile-list');
  await expect(findingsMobile).toBeVisible();
  await expect(findingsTable).toBeHidden();
  await expectStatusBadgesInsideRows(findingsMobile.getByTestId('finding-mobile-row'), 'finding-mobile-status');
  await expectNoDocumentOverflow(page);
  await evidenceShot(page, 'findings-mobile');

  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(findingsTable).toBeVisible();
  await expect(findingsMobile).toBeHidden();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/projects/${project()}/commits?repo=${repoId}`);
  const historyTable = page.getByTestId('assessment-history-table-scroll');
  const historyMobile = page.getByTestId('assessment-history-mobile-list');
  await expect(historyMobile).toBeVisible();
  await expect(historyTable).toBeHidden();
  await expectStatusBadgesInsideRows(historyMobile.getByTestId('assessment-history-mobile-row'), 'assessment-mobile-status');
  await expectNoDocumentOverflow(page);
  await evidenceShot(page, 'project-overview-mobile');

  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(historyTable).toBeVisible();
  await expect(historyMobile).toBeHidden();
});
