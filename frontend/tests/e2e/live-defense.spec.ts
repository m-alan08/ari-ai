import { expect, test } from '@playwright/test';
import { env, evidenceShot, newUser } from './helpers';

const project = () => env('E2E_PROJECT_ID');

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

test('Live Defense shadow pilot: staging app, ingest, unknown health, review without enforcement', async ({ browser, request }) => {
  const page = await newUser(browser, 'e2e-alice');
  const pid = project();
  const recId = env('E2E_REC_ID');
  const api = env('E2E_API');

  await page.goto(`/projects/${pid}/live-defense`);
  await expect(page.getByTestId('live-defense')).toHaveAttribute('data-mode', 'live');
  await expect(page.getByTestId('live-defense-preview-banner')).toHaveCount(0);
  await expect(page.getByTestId('live-defense-no-enforcement')).toContainText('does not apply, isolate, rate-limit, or block');
  await expect(page.getByTestId('live-defense-shadow-vs-controls')).toContainText('never execute a control');
  await expect(page.getByTestId('live-defense-health-unknown')).toContainText('unknown');
  await expect(page.getByTestId('defense-map')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^(apply|isolate|rate-limit|block source)$/i })).toHaveCount(0);

  await page.getByRole('link', { name: 'Staging apps' }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/live-defense/apps`));
  await expect(page.getByTestId('live-defense-shadow-mode')).toContainText('Shadow mode');
  await page.getByLabel('Name').fill('payments-lite staging');
  await page.getByLabel('Environment').selectOption('owned_test');
  await page.getByRole('button', { name: 'Register app' }).click();
  const appCard = page.locator('[data-testid^="live-defense-app-"]').filter({ hasText: 'payments-lite staging' });
  await expect(appCard).toBeVisible();
  await expect(appCard.getByText('owned test', { exact: true })).toBeVisible();
  await expect(appCard.getByText('OWNED TEST ENVIRONMENT', { exact: true })).toBeVisible();
  await appCard.getByRole('button', { name: 'Issue ingest token' }).click();
  const once = page.getByTestId('live-defense-token-once');
  await expect(once).toContainText('will not show it again');
  const token = await once.locator('code').innerText();
  expect(token.startsWith('hkt_ing_')).toBeTruthy();
  await evidenceShot(page, 'live-defense-apps');

  const baseline = await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: ingestBatch(0, 0),
  });
  expect(baseline.ok(), await baseline.text()).toBeTruthy();
  const spike = await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: ingestBatch(120_000, 18),
  });
  expect(spike.ok(), await spike.text()).toBeTruthy();

  const me = await page.request.get(`${api}/me`);
  const csrf = (await me.json() as { csrf_token: string }).csrf_token;
  const eventsPage = await page.request.get(`${api}/projects/${pid}/events`);
  expect(eventsPage.ok()).toBeTruthy();
  const events = (await eventsPage.json() as { items: { id: string }[] }).items;
  const incidentRes = await page.request.post(`${api}/projects/${pid}/incidents`, {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      title: 'Staging ingest spike',
      note: 'Opened from authenticated runtime ingest on the owned staging app.',
      monitoring_event_ids: events.slice(0, 1).map(item => item.id),
      recommendation_ids: [recId],
    },
  });
  expect(incidentRes.ok(), await incidentRes.text()).toBeTruthy();
  const incident = await incidentRes.json() as { id: string };

  await page.getByRole('navigation', { name: 'Live Defense' }).getByRole('link', { name: 'Overview' }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/live-defense/?$`));
  await expect.poll(async () => {
    const listed = await page.request.get(`${api}/projects/${pid}/live-policy-decisions`);
    if (!listed.ok()) return 0;
    return ((await listed.json()) as { items: unknown[] }).items.length;
  }).toBeGreaterThan(0);
  await page.reload();
  await expect(page.getByTestId('live-defense')).toHaveAttribute('data-mode', 'live');
  await expect(page.getByTestId('live-defense-summary')).toContainText('staging app');
  await expect(page.getByTestId('live-defense-summary')).toContainText('Runtime ingest has been recorded');
  await expect(page.getByTestId('live-defense-summary')).toContainText('none executed');
  await expect(page.getByTestId('live-defense-health')).toContainText('unknown');
  await expect(page.getByRole('button', { name: /^(apply|isolate|rate-limit|block)$/i })).toHaveCount(0);
  await expect(page.getByTestId('live-defense-graph')).toBeVisible();
  await evidenceShot(page, 'live-defense-overview');

  await page.getByRole('link', { name: 'Investigation' }).click();
  await expect(page.getByTestId(`live-defense-incident-${incident.id}`)).toContainText('Staging ingest spike');
  await evidenceShot(page, 'live-defense-investigation');
  await page.getByTestId(`live-defense-incident-${incident.id}`).click();
  if (events.length === 0) {
    await expect(page.getByTestId('incident-event-correlation')).toContainText('unknown');
  } else {
    await expect(page.getByText('Authenticated runtime ingest log anomaly')).toBeVisible();
  }
  await expect(page.getByRole('link', { name: 'raise_monitoring on payments' })).toBeVisible();
  await evidenceShot(page, 'live-defense-incident');

  await page.getByRole('link', { name: 'Explainability' }).click();
  const recCard = page.getByTestId(`live-defense-rec-${recId}`);
  await expect(recCard).toContainText('recommendation only');
  await expect(recCard).toContainText('simulation-derived');
  await expect(recCard).toContainText('not a probability');
  await recCard.getByRole('button', { name: 'Load stored explanation' }).click();
  await expect(recCard).toContainText('occlusion');
  const review = page.getByTestId(`live-defense-review-${recId}`);
  await review.getByLabel('Decision').selectOption('accept');
  await review.getByLabel('Reason (optional)').fill('Shadow-pilot opinion only; do not execute.');
  await review.getByRole('button', { name: 'Record review' }).click();
  await expect(review).toContainText('accept');
  await expect(review).toContainText('did not execute');
  const listed = await page.request.get(`${api}/projects/${pid}/live-policy-decisions`);
  expect(listed.ok()).toBeTruthy();
  const shadowItems = (await listed.json() as { items: { id: string }[] }).items;
  const shadowId = shadowItems[0]?.id;
  if (!shadowId) throw new Error('expected a persisted shadow decision');
  const shadow = page.getByTestId(`live-defense-decision-${shadowId}`);
  await expect(shadow).toContainText('NOT EXECUTED');
  await expect(shadow).toContainText('authenticated');
  const shadowReview = page.getByTestId(`live-defense-review-${shadowId}`);
  await shadowReview.getByLabel('Decision').selectOption('accept');
  await shadowReview.getByLabel('Reason (optional)').fill('Accept does not execute a control.');
  await shadowReview.getByRole('button', { name: 'Record review' }).click();
  await expect(shadowReview).toContainText('did not execute');
  await expect(page.getByRole('button', { name: /^(apply|isolate|rate-limit|block)$/i })).toHaveCount(0);
  await evidenceShot(page, 'live-defense-explain-review');
});

test('Live Defense Phase 5: rotate, revoke, disconnect, restart ingest, export report', async ({ browser, request }) => {
  const page = await newUser(browser, 'e2e-alice');
  const pid = project();
  const api = env('E2E_API');

  await page.goto(`/projects/${pid}/live-defense/apps`);
  await page.getByLabel('Name').fill('phase5 rehearsal app');
  await page.getByLabel('Environment').selectOption('owned_test');
  await page.getByRole('button', { name: 'Register app' }).click();
  const appCard = page.locator('[data-testid^="live-defense-app-"]').filter({ hasText: 'phase5 rehearsal app' });
  await expect(appCard).toBeVisible();
  await appCard.getByRole('button', { name: 'Issue ingest token' }).click();
  const once = page.getByTestId('live-defense-token-once');
  const firstToken = await once.locator('code').innerText();

  const baseline = await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${firstToken}` },
    data: ingestBatch(0, 0),
  });
  expect(baseline.ok(), await baseline.text()).toBeTruthy();

  await appCard.getByRole('button', { name: 'Rotate credential' }).click();
  await expect(once.locator('code')).not.toHaveText(firstToken);
  const rotated = (await once.locator('code').innerText()).trim();
  expect(rotated.startsWith('hkt_ing_')).toBeTruthy();
  expect(rotated).not.toEqual(firstToken);

  const stale = await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${firstToken}` },
    data: ingestBatch(60_000, 0),
  });
  expect(stale.status()).toBe(401);
  const restarted = await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${rotated}` },
    data: ingestBatch(60_000, 0),
  });
  expect(restarted.ok(), await restarted.text()).toBeTruthy();

  await appCard.getByRole('button', { name: 'Verify from ingest records' }).click();
  await expect(appCard.getByText('ingest seen')).toBeVisible();
  await page.getByRole('button', { name: 'Download pilot report' }).click();
  await expect(page.getByTestId('live-defense-pilot-report-saved')).toBeVisible();
  await evidenceShot(page, 'live-defense-phase5-report');

  await appCard.getByRole('button', { name: 'Disconnect app' }).click();
  await expect(appCard.getByText('disconnected', { exact: true })).toBeVisible();
  await expect(appCard.getByTestId('live-defense-app-disconnected')).toBeVisible();
  const disabled = await request.post(`${api}/ingest/runtime/${pid}`, {
    headers: { Authorization: `Bearer ${rotated}` },
    data: ingestBatch(120_000, 0),
  });
  expect(disabled.status()).toBe(401);
  await expect(page.getByRole('button', { name: /^(apply|isolate|rate-limit|block)$/i })).toHaveCount(0);
  await evidenceShot(page, 'live-defense-phase5-disconnect');
});

test('RL-gated readiness dashboard stays BLOCKED from the backend', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const pid = project();
  await page.goto(`/projects/${pid}/live-defense/readiness`);
  await expect(page.getByTestId('live-defense-readiness-verdict')).toContainText('INFRASTRUCTURE READY — RL BLOCKED');
  await expect(page.getByTestId('live-defense-readiness-categories')).toContainText('BLOCKED');
  await expect(page.getByTestId('live-defense-readiness')).toContainText('ready=false');
  await expect(page.getByRole('button', { name: /^(apply|isolate|rate-limit|block)$/i })).toHaveCount(0);
});
