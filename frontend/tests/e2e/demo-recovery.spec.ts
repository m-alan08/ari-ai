import { expect, test } from '@playwright/test';
import { env, newUser, expectNoSideScroll } from './helpers';

// Real application and backend. Only the deliberate transient HTTP fault is injected.
test('project assessment list recovers from a lost backend response and survives refresh', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const pid = env('E2E_PROJECT_ID');
  let offline = true;
  await page.route(`**/api/v1/projects/${pid}/assessments*`, route =>
    offline ? route.abort('connectionrefused') : route.continue());
  await page.goto(`/projects/${pid}`);
  const failure = page.getByTestId('error-state').filter({ hasText: 'Could not load assessments' });
  await expect(failure).toContainText('Backend unreachable');
  offline = false;
  await failure.getByRole('button', { name: 'Retry' }).click();
  await expect(failure).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Runs & replay', exact: true })).toContainText('No assessments yet.');
  await page.reload();
  await expect(page.getByRole('region', { name: 'Runs & replay', exact: true })).toContainText('No assessments yet.');
  await expect(page.getByTestId('error-state')).toHaveCount(0);
});

test('a missing assessment shows the real backend error rather than completed results', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  await page.goto(`/projects/${env('E2E_PROJECT_ID')}/assessments/asm_00000000000000000000000000`);
  await expect(page.getByTestId('error-state')).toContainText('Not found');
  await expect(page.getByTestId('error-state')).toContainText('assessment not found');
  await expect(page.getByTestId('provider-summary')).toHaveCount(0);
});


test('project navigation leads with two workflows and keeps tools reachable', async ({ browser }) => {
  test.setTimeout(60_000);
  const page = await newUser(browser, 'e2e-alice');
  const base = `/projects/${env('E2E_PROJECT_ID')}`;
  await page.goto(base);
  const nav = page.getByRole('navigation', { name: 'Project', exact: true });
  await expect(nav.getByRole('link')).toHaveCount(11);
  await expect(nav.getByRole('link', { name: '7 Agents', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByTestId('pillar-loop')).toBeHidden();
  await expect(page.getByRole('region', { name: 'Runs & replay' })).toBeVisible();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expectNoSideScroll(page);
    await expect(page.getByTestId('project-title')).toHaveCSS('opacity', '1');
    await page.screenshot({ path: `/tmp/ari-project-${width}.png`, fullPage: true });
  }
  await expect(nav.getByRole('link', { name: 'Approvals', exact: true })).toBeVisible();
  await nav.getByRole('link', { name: 'Authorized scopes', exact: true }).click();
  await expect(page).toHaveURL(`${base}/scopes`);
  await nav.getByRole('link', { name: 'Code review', exact: true }).click();
  await expect(page).toHaveURL(`${base}/commits`);
  await expect(page.getByRole('heading', { name: 'Review a change.' })).toBeVisible();
});
