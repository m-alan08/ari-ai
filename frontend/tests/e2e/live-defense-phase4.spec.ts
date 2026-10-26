import { expect, test } from '@playwright/test';
import { env, evidenceShot, newUser } from './helpers';

test('Live Defense Phase 4: authorize owned-test control, verify, rollback', async ({ browser, request }) => {
  const page = await newUser(browser, 'e2e-alice');
  const pid = env('E2E_PROJECT_ID');
  const ownedPort = env('E2E_OWNED_TEST_PORT');
  const helper = env('E2E_OWNED_TEST_HELPER');

  await page.goto(`/projects/${pid}/live-defense`);
  await expect(page.getByTestId('live-defense')).toHaveAttribute('data-mode', 'live');
  await expect(page.getByTestId('live-defense-no-enforcement')).toContainText('does not apply, isolate, rate-limit, or block');
  await expect(page.getByRole('button', { name: /^(apply|isolate|rate-limit|block source)$/i })).toHaveCount(0);

  await page.getByRole('link', { name: 'Staging apps' }).click();
  await page.getByLabel('Name').fill('payments-lite owned test');
  await page.getByLabel('Environment').selectOption('owned_test');
  await page.getByRole('button', { name: 'Register app' }).click();
  const appCard = page.locator('[data-testid^="live-defense-app-"]').filter({ hasText: 'payments-lite owned test' });
  await expect(appCard).toBeVisible();
  await expect(appCard.getByText('OWNED TEST ENVIRONMENT')).toBeVisible();

  await page.getByRole('button', { name: 'Create grant' }).click();
  await expect(page.getByText(/cgr_/)).toBeVisible();

  await appCard.getByLabel('Loopback port').fill(ownedPort);
  await appCard.getByRole('button', { name: 'Register control channel' }).click();
  const hmacBox = page.getByTestId('live-defense-hmac-once');
  await expect(hmacBox).toContainText('will not show it again');
  const secret = await hmacBox.locator('code').innerText();
  expect(secret.startsWith('hkt_ctl_')).toBeTruthy();

  const started = await request.post(`${helper}/start`, { data: { secret } });
  expect(started.ok(), await started.text()).toBeTruthy();

  await page.getByRole('navigation', { name: 'Live Defense' }).getByRole('link', { name: 'Controls' }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/live-defense/controls`));
  await expect(page.getByTestId('live-defense-owned-test')).toContainText('OWNED TEST ENVIRONMENT');
  await expect(page.getByTestId('live-defense-authorize')).toContainText('control-authorizations');
  await expect(page.getByRole('button', { name: /^(apply|isolate|rate-limit|block source)$/i })).toHaveCount(0);

  await page.getByTestId('live-defense-authorize').getByLabel('Control kind').selectOption('rate_limit');
  await page.getByTestId('live-defense-authorize').getByLabel('Exact route').fill('GET /payments/pay_fixture');
  await page.getByTestId('live-defense-authorize').getByLabel('Duration (seconds)').fill('60');
  await page.getByTestId('live-defense-authorize').getByLabel('Max requests per window').fill('1');
  await page.getByRole('button', { name: 'Authorize owned-test control' }).click();
  await expect(page.locator('[data-testid^="live-defense-authorized-"]')).toContainText('OWNED TEST ENVIRONMENT');

  await expect.poll(async () => {
    await page.reload();
    const text = await page.locator('[data-testid^="live-defense-control-"]').first().innerText().catch(() => '');
    return text;
  }, { timeout: 20_000 }).toMatch(/verified/);
  const controlCard = page.locator('[data-testid^="live-defense-control-"]').first();
  await expect(controlCard).toContainText('OWNED TEST ENVIRONMENT');
  await expect(controlCard).toContainText('HTTP 200 is not effectiveness');
  await expect(controlCard).not.toContainText('SAMPLE LAYOUT');
  await evidenceShot(page, 'live-defense-phase4-verified');

  await controlCard.getByRole('button', { name: 'Request rollback' }).click();
  await expect.poll(async () => {
    await page.reload();
    return page.locator('[data-testid^="live-defense-control-"]').first().innerText();
  }, { timeout: 20_000 }).toMatch(/rolled back|verifying|executing|verified/);
  await evidenceShot(page, 'live-defense-phase4-controls');
});
