import { expect, test, type Page } from '@playwright/test';
import { env, evidenceShot, newUser } from './helpers';


async function freshRepository(page: Page): Promise<{ projectId: string; repoId: string }> {
  const csrf = ((await (await page.request.get(`${env('E2E_API')}/me`)).json()) as { csrf_token: string }).csrf_token;
  const stamp = Date.now();
  const created = await page.request.post(`${env('E2E_API')}/projects`, { data: { name: `First customer ${stamp}` }, headers: { 'X-CSRF-Token': csrf } });
  expect(created.status()).toBe(201);
  const projectId = (await created.json()).id as string;
  const connected = await page.request.post(`${env('E2E_API')}/projects/${projectId}/repositories`, {
    data: { full_name: `acme/first-customer-${stamp}`, installation_id: null },
    headers: { 'X-CSRF-Token': csrf },
  });
  expect(connected.status()).toBe(201);
  return { projectId, repoId: (await connected.json()).id as string };
}

async function answerSetupUntilStart(page: Page, projectId: string, repoId: string): Promise<void> {
  await page.goto(`/projects/${projectId}/setup?repo=${encodeURIComponent(repoId)}`);
  await expect(page.getByTestId('setup-step-1')).toBeVisible();
  for (let question = 1; question < 8; question += 1) {
    if (question === 6) {
      await page.getByRole('button', { name: /Approve sandbox testing/ }).click();
      await expect(page.getByText(/Testing authorized/)).toBeVisible();
    }
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await expect(page.getByTestId(`setup-step-${question + 1}`)).toBeVisible();
  }
}

test('connect shows the same four-step journey that setup completes', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  await page.goto('/connect');
  const steps = page.getByRole('navigation', { name: 'Setup steps' }).getByRole('listitem');
  await expect(steps).toHaveText([/Connect GitHub/, /Choose repository/, /Set up the repository/, /Run the first review/]);
  await evidenceShot(page, 'first-customer-connect');
});

test('a failed first assessment explains recovery, then findings, analytics, and approvals say what happens next', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  const { projectId, repoId } = await freshRepository(page);
  const repoQuery = `repo=${encodeURIComponent(repoId)}`;
  await answerSetupUntilStart(page, projectId, repoId);
  await page.route(`**/api/v1/projects/${projectId}/assessments`, route => route.request().method() === 'POST'
    ? route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'SUBSYSTEM_UNAVAILABLE', message: 'could not read the latest commit from GitHub', details: {}, request_id: 'req_01M5ZTNQ7EE47RV9F20V91KZE4' } }),
      })
    : route.continue());
  await page.getByRole('button', { name: /Start first assessment/ }).click();
  const failed = page.getByTestId('setup-start-failed');
  await expect(failed).toContainText('The first assessment did not start');
  await expect(failed.getByTestId('error-recovery')).toContainText('nothing was started');
  await expect(failed.getByRole('link', { name: /Refresh from GitHub/ })).toHaveAttribute('href', '/connect');
  await expect(failed.getByRole('link', { name: 'Commits' })).toHaveAttribute('href', `/projects/${projectId}/commits?${repoQuery}#start-assessment`);
  await evidenceShot(page, 'first-customer-start-failed');
  await page.unroute(`**/api/v1/projects/${projectId}/assessments`);
  await page.getByRole('button', { name: 'Finish setup', exact: true }).click();
  await expect(page.getByTestId('setup-done')).toBeVisible();

  await page.goto(`/projects/${projectId}?${repoQuery}`);
  await expect(page.getByTestId('project-summary')).toContainText('Nothing has been checked yet.');
  await expect(page.getByRole('link', { name: 'Run your first check →' })).toHaveCount(0);

  await page.goto(`/projects/${projectId}/findings?${repoQuery}`);
  const noReview = page.getByTestId('findings-no-review');
  await expect(noReview).toContainText('has not reviewed');
  await expect(noReview.getByRole('link', { name: 'Run a review' })).toHaveAttribute('href', `/projects/${projectId}/commits?${repoQuery}#start-assessment`);
  await evidenceShot(page, 'first-customer-findings-empty');

  await page.goto(`/projects/${projectId}/approvals?${repoQuery}`);
  await expect(page.getByText('Sandbox testing scope').first()).toBeVisible();
  await expect(page.getByText(/requested by you/).first()).toBeVisible();
  await evidenceShot(page, 'first-customer-approvals');
});

test('an unknown project offers a way back instead of only Retry', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  await page.goto('/projects/prj_00000000000000000000000000');
  const error = page.getByTestId('error-state');
  await expect(error).toContainText('Not found');
  await expect(error.getByTestId('error-recovery')).toContainText('choose it again');
  await expect(error.getByRole('link', { name: 'Back to projects' })).toHaveAttribute('href', '/connect');
  await evidenceShot(page, 'first-customer-not-found');
});
