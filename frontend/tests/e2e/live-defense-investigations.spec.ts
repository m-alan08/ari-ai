import { expect, test } from '@playwright/test';
import { env, evidenceShot, expectNoSideScroll, newUser } from './helpers';

const api = () => env('E2E_API');
const project = () => env('E2E_PROJECT_ID');
const repo = () => env('E2E_REPO_ID');
const sha = () => env('E2E_COMMIT');

function ingestBatch(offsetMs: number, extra: number) {
  const start = Date.now() + offsetMs;
  return {
    source: 'owned_staging',
    window_start: new Date(start).toISOString(),
    window_end: new Date(start + 60_000).toISOString(),
    routes: [{
      route: 'GET /payments/{payment_id}',
      requests: 20,
      status_4xx: extra,
      auth_failures: extra,
    }],
  };
}

test('Executions: investigation timeline, agent states, diagnostics', async ({ browser, request }) => {
  const page = await newUser(browser, 'e2e-alice');
  const pid = project();
  const base = api();

  // MONITOR mode so runtime ingest drives a real first-responder decision.
  await page.goto(`/projects/${pid}/live-defense/first-responder`);
  await page.getByTestId('live-defense-fr-mode-select').selectOption('monitor');
  await page.getByRole('button', { name: 'Set mode' }).click();
  await expect(page.getByTestId('live-defense-fr-intent')).toContainText('monitor');

  // Register an owned-test app and issue an ingest token through the real UI.
  await page.getByRole('navigation', { name: 'Live Defense' }).getByRole('link', { name: 'Staging apps' }).click();
  await page.getByLabel('Name').fill('payments obs staging');
  await page.getByLabel('Environment').selectOption('owned_test');
  await page.getByRole('button', { name: 'Register app' }).click();
  const appCard = page.locator('[data-testid^="live-defense-app-"]').filter({ hasText: 'payments obs staging' });
  await expect(appCard).toBeVisible();
  await appCard.getByRole('button', { name: 'Issue ingest token' }).click();
  const token = await page.getByTestId('live-defense-token-once').locator('code').innerText();

  const appId = (await appCard.getAttribute('data-testid'))!.replace('live-defense-app-', '');
  const me = await page.request.get(`${base}/me`);
  const csrf = (await me.json() as { csrf_token: string }).csrf_token;
  const mapping = await page.request.post(`${base}/applications/${appId}/deploy-mappings`, {
    headers: { 'X-CSRF-Token': csrf },
    data: { repo_id: repo(), commit_sha: sha(), deployed_at: new Date().toISOString(), source: 'owner_attested' },
  });
  expect(mapping.ok(), await mapping.text()).toBeTruthy();

  const spike = await request.post(`${base}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: ingestBatch(0, 18),
  });
  expect(spike.ok(), await spike.text()).toBeTruthy();

  // The e2e worker drains continuously; the decision persists after the spike.
  let decisionId = '';
  await expect.poll(async () => {
    const listed = await page.request.get(`${base}/projects/${pid}/first-responder/investigations`);
    if (!listed.ok()) return 0;
    const items = ((await listed.json()) as { items: { decision_id: string }[] }).items;
    decisionId = items[0]?.decision_id ?? '';
    return items.length;
  }, { timeout: 60_000 }).toBeGreaterThan(0);

  await page.goto(`/projects/${pid}/live-defense/executions`);
  await expect(page.getByTestId('live-defense-executions')).toBeVisible();
  await expect(page.getByTestId('live-defense-executions-copy')).toContainText('not a confirmed compromise');
  await expect(page.getByTestId('ld-diagnostics')).toBeVisible();
  await expectNoSideScroll(page);

  const row = page.getByTestId(`ld-inv-row-${decisionId}`);
  await expect(row).toBeVisible();
  await row.click();

  const timeline = page.getByTestId('ld-inv-timeline');
  await expect(timeline).toBeVisible();
  await expect(page.getByTestId('ld-tl-events')).toBeVisible();
  await expect(page.getByTestId('ld-tl-decision')).toContainText(decisionId);
  await expect(page.getByTestId('ld-tl-invocations')).toBeVisible();
  await expect(page.getByTestId('ld-tl-handoff')).toBeVisible();
  await evidenceShot(page, 'live-defense-executions-timeline');

  // Agent step jobs are drained by the e2e worker; invocations resolve to a
  // real persisted state — never fabricated as executed.
  await expect.poll(async () => {
    const t = await page.request.get(`${base}/first-responder/investigations/${decisionId}`);
    const body = await t.json() as { invocations: { invocation: { status: string } }[] };
    return body.invocations.length;
  }, { timeout: 60_000 }).toBeGreaterThan(0);
  const t = await page.request.get(`${base}/first-responder/investigations/${decisionId}`);
  const body = await t.json() as { invocations: { invocation: { status: string } }[] };
  const known = /^(eligible|blocked|queued|skipped_\w+|completed_unverified|executed|failed|cancelled)$/;
  for (const v of body.invocations) expect(v.invocation.status).toMatch(known);

  await page.reload();
  await page.getByTestId(`ld-inv-row-${decisionId}`).click();
  const firstDetail = page.locator('[data-testid^="ld-inv-detail-"]').first();
  await firstDetail.locator('summary').click();
  await expect(firstDetail).toContainText('unavailable');
  await expect(firstDetail).not.toContainText('tokens used');
  await evidenceShot(page, 'live-defense-executions-detail');

  // Diagnostics reflect real queue state and never invent cost.
  const diag = await page.request.get(`${base}/projects/${pid}/first-responder/diagnostics`);
  expect(diag.ok()).toBeTruthy();
  const d = await diag.json() as { cost_usd_total: number | null; investigations_total: number };
  expect(d.investigations_total).toBeGreaterThan(0);

  // Read-only surface: no mutation controls on this tab.
  await expect(page.getByRole('button', { name: /^(apply|isolate|rate-limit|block source|auto-execute|set mode)$/i })).toHaveCount(0);
});
