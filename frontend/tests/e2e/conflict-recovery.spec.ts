import { expect, test, type Page } from '@playwright/test';
import { env, newUser } from './helpers';

// Real HTTP conflict, not a fulfilled error fixture. This project deliberately has
// no operator source binding, so this test cannot consume the scripted agent run.
test('a conflicting action requires explicit preparation before a single new submission', async ({ browser }) => {
  const alice = await newUser(browser, 'e2e-alice');
  const bob = await newUser(browser, 'e2e-bob');
  const api = env('E2E_API');
  async function post(page: Page, path: string, data: object) {
    const me = await page.request.get(`${api}/me`);
    expect(me.ok()).toBeTruthy();
    const response = await page.request.post(`${api}${path}`, { data, headers: { 'X-CSRF-Token': (await me.json()).csrf_token } });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  const project = await post(alice, '/projects', { name: 'Explicit conflict recovery' });
  const repo = await post(alice, `/projects/${project.id}/repositories`, { full_name: 'owned/conflict-recovery' });
  const scope = await post(alice, `/projects/${project.id}/scopes`, {
    name: 'recovery sandbox', expires_at: new Date(Date.now() + 3600000).toISOString(),
    targets: [{ kind: 'sandbox_build', repo_id: repo.id, build_recipe_ref: 'build_recipe.json' }],
    allowed_check_categories: ['authorization.ownership'],
  });
  await post(bob, `/scopes/${scope.id}/approve`, {});
  const keys: string[] = [];
  const accepted: { assessment: { id: string }; job_id: string }[] = [];
  await alice.route(`**/api/v1/projects/${project.id}/assessments`, async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const key = route.request().headers()['idempotency-key'];
    expect(key).toBeTruthy(); keys.push(key!);
    if (keys.length === 1) {
      const seeded = await alice.request.post(`${api}/projects/${project.id}/assessments`, {
        headers: { 'X-CSRF-Token': route.request().headers()['x-csrf-token']!, 'Idempotency-Key': key! }, data: { ...route.request().postDataJSON(), commit_sha: 'a'.repeat(40) },
      });
      expect(seeded.status()).toBe(202); accepted.push(await seeded.json());
      await post(alice, `/assessments/${accepted[0]!.assessment.id}/cancel`, {});
    }
    const response = await route.fetch();
    if (response.status() === 202) accepted.push(await response.json());
    await route.fulfill({ response });
  });
  await alice.goto(`/projects/${project.id}/commits?repo=${encodeURIComponent(repo.id)}`);
  await expect(alice.getByTestId('repo-switcher')).toContainText('owned/conflict-recovery');
  await alice.getByLabel('Authorized scope').selectOption(scope.id);
  await alice.getByLabel('Commit SHA').fill(env('E2E_COMMIT'));
  const start = alice.getByRole('button', { name: 'Start assessment' });
  await start.click();
  await expect(alice.getByTestId('error-state')).toContainText('IDEMPOTENCY_CONFLICT');
  await start.click();
  await expect.poll(() => keys.length).toBe(2);
  await expect(start).toBeEnabled();
  expect(keys[1]).toBe(keys[0]); expect(accepted).toHaveLength(1);
  await alice.getByRole('button', { name: 'Prepare a new assessment action' }).click();
  expect(keys).toHaveLength(2); // preparing alone sends nothing
  await start.click();
  await alice.waitForURL(/\/assessments\/asm_/);
  expect(keys).toHaveLength(3); expect(keys[2]).not.toBe(keys[0]);
  expect(accepted).toHaveLength(2);
  expect(accepted[1]!.job_id).not.toBe(accepted[0]!.job_id);
  const list = await alice.request.get(`${api}/projects/${project.id}/assessments`);
  expect((await list.json()).items).toHaveLength(2);
  // Unbound projects often hit SNAPSHOT_NOT_READY before cancel runs; terminal is fine here.
  const meAgain = await alice.request.get(`${api}/me`);
  const cancel = await alice.request.post(`${api}/assessments/${accepted[1]!.assessment.id}/cancel`, {
    data: {},
    headers: { 'X-CSRF-Token': (await meAgain.json()).csrf_token },
  });
  if (!cancel.ok()) {
    expect((await cancel.json()).error?.code).toBe('CONFLICT_STATE');
  }
});
