import { expect, test } from '@playwright/test';
import { env, newUser, expectNoSideScroll } from './helpers';

// Real application and backend. Only the deliberate transient HTTP fault is injected.
test('project assessment list recovers from a lost backend response and survives refresh', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const pid = env('E2E_PROJECT_ID');
  let offline = true;
  await page.route(`**/api/v1/projects/${pid}/assessments*`, route =>
    offline ? route.abort('connectionrefused') : route.continue());
  await page.goto(`/projects/${pid}/commits`);
  const failure = page.getByTestId('error-state').filter({ hasText: 'Could not load assessments' });
  await expect(failure).toContainText('Backend unreachable');
  offline = false;
  await failure.getByRole('button', { name: 'Retry' }).click();
  await expect(failure).toHaveCount(0);
  const history = page.getByRole('region', { name: 'Runs & replay', exact: true });
  await expect(history).toContainText(/No assessments for this repository yet\.|No assessments for this repository on the latest page\.|Open run & replay/);
  await page.reload();
  await expect(history).toContainText(/No assessments for this repository yet\.|No assessments for this repository on the latest page\.|Open run & replay/);
  await expect(page.getByTestId('error-state')).toHaveCount(0);
});

test('a missing assessment shows the real backend error rather than completed results', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  await page.goto(`/projects/${env('E2E_PROJECT_ID')}/assessments/asm_00000000000000000000000000`);
  await expect(page.getByTestId('error-state')).toContainText('Not found');
  await expect(page.getByTestId('error-state')).toContainText('assessment not found');
  await expect(page.getByTestId('provider-summary')).toHaveCount(0);
});


test('project navigation leads with Analytics and Commits and keeps More tools reachable', async ({ browser }) => {
  test.setTimeout(60_000);
  const page = await newUser(browser, 'e2e-alice');
  const base = `/projects/${env('E2E_PROJECT_ID')}`;
  await page.goto(base);
  const nav = page.getByRole('navigation', { name: 'Project', exact: true });
  await expect(nav.getByRole('link')).toHaveCount(5);
  await expect(nav.getByRole('link', { name: 'Analytics', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByTestId('pillar-loop')).toBeHidden();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expectNoSideScroll(page);
    await expect(page.getByTestId('project-title')).toHaveCSS('opacity', '1');
    await page.screenshot({ path: `/tmp/ari-project-${width}.png`, fullPage: true });
  }
  const moreButton = nav.locator('button[aria-controls="project-more-menu"]');
  await expect(moreButton).toBeVisible();
  await moreButton.click();
  await expect(moreButton).toHaveAttribute('aria-expanded', 'true');
  await expect(nav.getByRole('link', { name: 'Approvals', exact: true })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Authorized scopes', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 900 });
  await nav.getByRole('link', { name: 'Authorized scopes', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${base}/scopes(?:\\?repo=[^&]+)?`));
  await expect(moreButton).toHaveAttribute('aria-current', 'page');
  await nav.getByRole('link', { name: 'Commits', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${base}/commits(?:\\?repo=[^&]+)?`));
  await expect(page.getByRole('heading', { name: 'Commits', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Runs & replay', exact: true })).toBeVisible();
});
