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

// Workstream 2 e2e: drive the first-responder OFF interruption through the real
// UI + API. Mode is set through the page, telemetry through the real ingest
// route, and OFF takes effect through the same mode control an operator uses.
test('Live Defense e2e: OFF mode stops investigation and renders honestly', async ({ browser, request }) => {
  const page = await newUser(browser, 'e2e-alice');
  // Dedicated project: this spec runs Scout, and the first-responder page
  // renders one live-defense-fr-invocation-* row per invocation across the
  // whole project — sharing E2E_PROJECT_ID would collide with the Phase 7C
  // spec's single-element assertions.
  const pid = env('E2E_LD_PROJECT_ID');
  const rid = env('E2E_LD_REPO_ID');
  const sha = env('E2E_COMMIT');
  const api = env('E2E_API');

  await page.goto(`/projects/${pid}/live-defense/first-responder`);
  await page.getByTestId('live-defense-fr-mode-select').selectOption('monitor');
  await page.getByRole('button', { name: 'Set mode' }).click();
  await expect(page.getByTestId('live-defense-fr-intent')).toContainText('monitor');

  await page.getByRole('navigation', { name: 'Live Defense' }).getByRole('link', { name: 'Staging apps' }).click();
  await page.getByLabel('Name').fill('e2e owned staging');
  await page.getByLabel('Environment').selectOption('owned_test');
  await page.getByRole('button', { name: 'Register app' }).click();
  const appCard = page.locator('[data-testid^="live-defense-app-"]').filter({ hasText: 'e2e owned staging' });
  await expect(appCard).toBeVisible();
  const appId = (await appCard.getAttribute('data-testid'))!.replace('live-defense-app-', '');
  await appCard.getByRole('button', { name: 'Issue ingest token' }).click();
  const token = await page.getByTestId('live-defense-token-once').locator('code').innerText();

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

  // Operator flips OFF through the same control, then posts a second window.
  await page.getByTestId('live-defense-fr-mode-select').selectOption('off');
  await page.getByRole('button', { name: 'Set mode' }).click();
  await expect(page.getByTestId('live-defense-fr-intent')).toContainText('off');

  const knownIds = new Set(
    (await (await page.request.get(`${api}/projects/${pid}/first-responder/decisions`)).json() as {
      items: Array<{ id: string }>
    }).items.map(d => d.id),
  );

  // Post a window on a NEW route so it is anomalous against history — while
  // OFF. Dispatch must be suppressed: either no new decision is recorded, or
  // any new decision is cancelled and no agent executes for it.
  const offBatch = ingestBatch(1, 19);
  offBatch.routes[0]!.route = 'GET /admin/users';
  await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${token.trim()}` },
    data: offBatch,
  });

  await page.waitForTimeout(10_000); // give the shadow job a full drain window
  const decisions = (await (await page.request.get(
    `${api}/projects/${pid}/first-responder/decisions`,
  )).json() as { items: Array<{ id: string; cancelled: boolean }> }).items;
  const newDecisions = decisions.filter(d => !knownIds.has(d.id));
  for (const d of newDecisions) {
    expect(d.cancelled, `post-OFF decision ${d.id} must be cancelled`).toBeTruthy();
  }
  const invResp = await page.request.get(`${api}/projects/${pid}/first-responder/invocations`);
  const postOffInvocations = (await invResp.json() as {
    items: Array<{ decision_id: string; status: string }>
  }).items.filter(i => newDecisions.some(d => d.id === i.decision_id));
  expect(postOffInvocations.every(i => i.status !== 'executed')).toBeTruthy();

  await expect(page.getByTestId('live-defense-fr-mode-select')).toHaveValue('off');
  await evidenceShot(page, 'live-defense-e2e-off-mode');

  // Re-arm: a fresh anomalous window resumes the pipeline — OFF was a pause,
  // not a destructive teardown.
  await page.getByTestId('live-defense-fr-mode-select').selectOption('monitor');
  await page.getByRole('button', { name: 'Set mode' }).click();
  const resumeBatch = ingestBatch(2, 18);
  resumeBatch.routes[0]!.route = 'GET /admin/audit';
  await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${token.trim()}` },
    data: resumeBatch,
  });
  await expect.poll(async () => {
    const resp = await page.request.get(`${api}/projects/${pid}/first-responder/decisions`);
    const items = (await resp.json() as { items: Array<{ id: string; cancelled: boolean }> }).items;
    return items.some(d => !knownIds.has(d.id) && !d.cancelled);
  }, { timeout: 60_000 }).toBeTruthy();
});
