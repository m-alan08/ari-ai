import { expect, test } from '@playwright/test';
import { env, newUser, expectNoSideScroll } from './helpers';

// Only external GitHub discovery/metadata are fixtures. All run records use the real stack.
test('organization → repository → commit keeps real agent work and replay in that commit', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const full = env('E2E_WORKSPACE_REPO');
  const [org, repo] = full.split('/');
  const sha = env('E2E_COMMIT');
  const otherSha = 'f'.repeat(40);
  const base = `/connect/${org}/${repo}`;
  let quickReads = 0;
  page.on('request', request => { if (request.url().endsWith('/api/analyze')) quickReads++; });
  await page.route('**/api/v1/github/repositories', route => route.fulfill({ json: { items: [
    { full_name: full, installation_id: 1, default_branch: 'main', private: true, connected_repo_id: env('E2E_WORKSPACE_REPO_ID') },
    { full_name: `${org}/sibling-repository`, installation_id: 1, default_branch: 'main', private: true, connected_repo_id: null },
  ], next_cursor: null, install_url: null } }));
  await page.route(`**/api/v1/repositories/${env('E2E_WORKSPACE_REPO_ID')}/commits`, route => route.fulfill({ json: { items: [
    { sha, message: 'Selected payment change', author: 'test-author', committed_at: null, html_url: null },
    { sha: otherSha, message: 'A different commit', author: 'test-author', committed_at: null, html_url: null },
  ], next_cursor: null, available: true, reason: null } }));
  await page.goto(`/connect/${org}`);
  await page.getByTestId(`connect-repo-${full}`).getByRole('button').click();
  await expect(page).toHaveURL(base);
  await expect(page.locator('main')).not.toContainText('sibling-repository');
  await page.getByRole('link', { name: /Selected payment change/ }).click();
  await expect(page).toHaveURL(`${base}/commit/${sha}`);
  await expect(page.getByRole('heading', { name: 'Selected payment change' })).toBeVisible();
  await expect(page.locator('main')).not.toContainText('A different commit');
  await expect(page.getByRole('tab')).toHaveCount(2);
  const panel = page.getByTestId('real-assessment-panel');
  await expect(panel).not.toContainText('Loading…');
  const automation = panel.getByRole('switch', { name: 'Automatic runs' });
  await expect(automation).toBeEnabled();
  if (await automation.getAttribute('aria-checked') === 'true') await automation.click();
  await expect(automation).toHaveAttribute('aria-checked', 'false');
  const request = panel.getByRole('button', { name: 'Request authorization', exact: true });
  if (await request.isVisible()) await request.click();
  const approve = panel.getByRole('button', { name: 'Approve sandbox authorization' });
  await expect.poll(async () => (await approve.isVisible()) || (await panel.getByRole('button', { name: 'Run the 7 agents', exact: true }).isVisible())).toBe(true);
  if (await approve.isVisible()) await approve.click();
  const accepted = page.waitForResponse(response => response.url().includes('/assessments') && response.request().method() === 'POST' && response.status() === 202);
  await panel.getByRole('button', { name: 'Run the 7 agents', exact: true }).click();
  const acceptedBody = await (await accepted).json();
  const assessmentId = acceptedBody.assessment.id as string;
  await expect(panel).toContainText('Last run:');
  await expect(page).toHaveURL(`${base}/commit/${sha}`);
  await page.getByRole('tab', { name: '7 agents', exact: true }).click();
  const map = page.getByTestId('commit-agent-map');
  await expect(map.getByRole('navigation', { name: 'Agents in orchestrator order' }).getByRole('button')).toHaveCount(7);
  await expect.poll(async () => Number(await map.getByRole('slider', { name: 'Replay position' }).getAttribute('max')), { timeout: 180_000 }).toBeGreaterThan(1);
  await map.getByRole('slider', { name: 'Replay position' }).press('End');
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await expectNoSideScroll(page);
    await map.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/ari-commit-workspace-${width}.png`, fullPage: true });
  }
  // Finish this test's run so its approvals cannot leak into later suite cases.
  const bob = await newUser(browser, 'e2e-bob');
  await bob.goto(`/projects/${env('E2E_WORKSPACE_PROJECT_ID')}/assessments/${assessmentId}`);
  await expect(bob.getByRole('heading', { name: 'Human approval required' })).toBeVisible({ timeout: 180_000 });
  const decisions = bob.getByRole('button', { name: 'Approve remediation', exact: true });
  // The functional-repair approval is recorded later in the run than the security one;
  // the gate polls the event log, so the second card can trail the first by a poll cycle.
  await expect(decisions).toHaveCount(2, { timeout: 120_000 });
  for (let i = 0; i < 2; i++) {
    await decisions.first().click();
    // Approving takes a second explicit click; the first only opens the confirm block.
    await bob.getByRole('button', { name: 'Confirm approval' }).click();
    await expect(decisions).toHaveCount(1 - i);
  }
  await expect(bob.getByRole('heading', { name: /Assessment/ }).getByText('Completed')).toBeVisible({ timeout: 180_000 });
  await page.goto(`${base}/commit/${otherSha}`);
  await expect(page.getByRole('heading', { name: 'A different commit' })).toBeVisible();
  await page.getByRole('tab', { name: '7 agents', exact: true }).click();
  await expect(page.getByTestId('commit-agent-map')).toContainText(/No replay yet|Preview · reasoning over the diff/);
  await expect(page.locator('main')).not.toContainText('Selected payment change');
  expect(quickReads).toBeGreaterThanOrEqual(1);
});
