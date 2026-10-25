import { mkdir } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { env, newUser, expectNoSideScroll } from './helpers';

// GitHub discovery and metadata fixtures exercise layout; session and app routing are real.
test('workspace design stays navigable and fits desktop and phone', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const full = env('E2E_WORKSPACE_REPO');
  const [org, repo] = full.split('/');
  const sha = env('E2E_COMMIT');
  await page.route('**/api/v1/github/repositories', route => route.fulfill({ json: { items: [
    { full_name: full, installation_id: 1, default_branch: 'main', private: true, connected_repo_id: env('E2E_WORKSPACE_REPO_ID') },
    { full_name: 'northstar/design-system', installation_id: 2, default_branch: 'main', private: false, connected_repo_id: null },
    { full_name: 'orbit/checkout', installation_id: 3, default_branch: 'main', private: true, connected_repo_id: null },
  ], next_cursor: null, install_url: null } }));
  await page.route(`**/api/v1/repositories/${env('E2E_WORKSPACE_REPO_ID')}/commits`, route => route.fulfill({ json: { items: [
    { sha, message: 'Validate payment boundaries', author: 'e2e-alice', committed_at: null, html_url: null },
  ], next_cursor: null, available: true, reason: null } }));
  const screenshots = process.env.ARI_UI_SCREENSHOTS;
  if (screenshots) await mkdir(screenshots, { recursive: true });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/connect');
    await expect(page.getByRole('heading', { name: 'Organizations' })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Projects', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByTestId(`connect-org-${org}`)).toBeVisible();
    await expectNoSideScroll(page);
    if (screenshots) await page.screenshot({ animations: 'disabled', path: `${screenshots}/organizations-${width}.png`, fullPage: true });
    await page.getByTestId(`connect-org-${org}`).click();
    await expect(page.getByTestId(`connect-repo-${full}`).getByRole('button')).toBeVisible();
    await expectNoSideScroll(page);
    if (screenshots) await page.screenshot({ animations: 'disabled', path: `${screenshots}/repositories-${width}.png`, fullPage: true });
    await page.getByTestId(`connect-repo-${full}`).getByRole('button').click();
    await expect(page).toHaveURL(`/connect/${org}/${repo}`);
    await page.getByRole('link', { name: /Validate payment boundaries/ }).click();
    await expect(page.getByRole('tab')).toHaveCount(2);
    await page.getByRole('tab', { name: '7 agents' }).click();
    await expect(page.getByTestId('commit-agent-map')).toBeVisible();
    await expectNoSideScroll(page);
    if (screenshots) await page.screenshot({ animations: 'disabled', path: `${screenshots}/agents-${width}.png`, fullPage: true });
    await page.goto('/');
    await expect(page.locator('.hero-headline')).toBeVisible();
    await expect.poll(() => page.locator('.hero-line').first().evaluate(el => Number(getComputedStyle(el).opacity))).toBe(1);
    await expectNoSideScroll(page);
    if (screenshots) await page.screenshot({ animations: 'disabled', path: `${screenshots}/landing-${width}.png` });
    await page.getByTestId('landing-footer').scrollIntoViewIfNeeded();
    await expect(page.getByRole('navigation', { name: 'Footer' })).toBeVisible();
    await expectNoSideScroll(page);
    if (screenshots) await page.screenshot({ animations: 'disabled', path: `${screenshots}/footer-${width}.png` });
  }
});
