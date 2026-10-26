import { expect, test } from '@playwright/test';
import { env, evidenceShot, newUser } from './helpers';

function ingestBatch(offsetMs: number, extra: number) {
  const end = new Date(Date.now() + offsetMs).toISOString();
  const start = new Date(Date.now() + offsetMs - 60_000).toISOString();
  return {
    source: 'owned_staging',
    window_start: start,
    window_end: end,
    routes: [
      {
        route: 'GET /payments/{payment_id}',
        requests: 20,
        status_4xx: extra,
        auth_failures: extra,
      },
    ],
  };
}

test('Live Defense Phase 7C: Scout executes, Probe is not expanded, no AUTO-EXECUTE', async ({ browser, request }) => {
  const page = await newUser(browser, 'e2e-alice');
  const pid = env('E2E_PROJECT_ID');
  const rid = env('E2E_REPO_ID');
  const sha = env('E2E_COMMIT');
  const api = env('E2E_API');

  await page.goto(`/projects/${pid}/live-defense/first-responder`);
  await expect(page.getByTestId('live-defense-first-responder-copy')).toContainText('AUTO-EXECUTE does not exist');
  await expect(page.getByTestId('live-defense-fr-mode-select').locator('option[value="auto_execute"]')).toHaveCount(0);

  await page.getByTestId('live-defense-fr-mode-select').selectOption('monitor');
  await page.getByRole('button', { name: 'Set mode' }).click();
  await expect(page.getByTestId('live-defense-fr-intent')).toContainText('monitor');

  await page.getByRole('navigation', { name: 'Live Defense' }).getByRole('link', { name: 'Staging apps' }).click();
  await page.getByLabel('Name').fill('phase7c staging');
  await page.getByLabel('Environment').selectOption('owned_test');
  await page.getByRole('button', { name: 'Register app' }).click();
  const appCard = page.locator('[data-testid^="live-defense-app-"]').filter({ hasText: 'phase7c staging' });
  await expect(appCard).toBeVisible();
  const appId = (await appCard.getAttribute('data-testid'))!.replace('live-defense-app-', '');
  await appCard.getByRole('button', { name: 'Issue ingest token' }).click();
  const once = page.getByTestId('live-defense-token-once');
  const token = await once.locator('code').innerText();

  // Owner-attested mapping via the same CSRF API the UI uses. Playwright's
  // selectOption on the controlled Application <select> did not enable Attest
  // in the full CI suite (button stayed disabled for 240s).
  const me = await page.request.get(`${api}/me`);
  const csrf = (await me.json() as { csrf_token: string }).csrf_token;
  const mapping = await page.request.post(`${api}/applications/${appId}/deploy-mappings`, {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      repo_id: rid,
      commit_sha: sha,
      deployed_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      source: 'owner_attested',
    },
  });
  expect(mapping.ok(), await mapping.text()).toBeTruthy();

  await page.getByRole('navigation', { name: 'Live Defense' }).getByRole('link', { name: 'First responder' }).click();

  await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${token.trim()}` },
    data: ingestBatch(0, 18),
  });

  await expect(page.getByTestId('live-defense-fr-invocation-scout')).toContainText(/executed|queued/, { timeout: 60_000 });
  await expect(page.getByTestId('live-defense-fr-invocation-scout')).toContainText('fake', { timeout: 60_000 });
  await expect(page.getByTestId('live-defense-fr-execution-scout')).toContainText('predicted probability is not set');
  await expect(page.getByTestId('live-defense-fr-execution-scout')).toContainText('unverified');
  await evidenceShot(page, 'live-defense-phase7c-scout-executed');

  await expect(page.getByTestId('live-defense-fr-invocation-fortify')).toContainText('skipped');
  await evidenceShot(page, 'live-defense-phase7c-invocations');
});
