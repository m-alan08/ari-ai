import { expect, test } from '@playwright/test';
import { env, evidenceShot, expectNoSideScroll, newUser } from './helpers';

const project = () => env('E2E_PROJECT_ID');

const step = (page: import('@playwright/test').Page, id: string) =>
  page.getByTestId(`onboarding-step-${id}`);

/**
 * The customer journey through the rendered wizard against the real API:
 * progress computed from real state -> register staging app + ingest token ->
 * owner-attested deploy mapping -> authorized test event -> evidence ->
 * disconnect revokes access. Every step state is backend-derived, never painted.
 */
test('onboarding wizard drives the full customer journey', async ({ browser }) => {
  const alice = await newUser(browser, 'e2e-alice');
  const api = env('E2E_API');
  const csrf = ((await (await alice.request.get(`${api}/me`)).json()) as { csrf_token: string })
    .csrf_token;
  const post = (path: string, data: unknown) =>
    alice.request.post(`${api}${path}`, { data: data ?? {}, headers: { 'X-CSRF-Token': csrf } });

  // Own project + repo so suite-mates cannot pre-seed apps, tokens or events
  // into the shared demo project and make step states nondeterministic.
  const own = await (await post('/projects', { name: 'alice onboarding (e2e)' })).json();
  const repo = await (
    await post(`/projects/${own.id}/repositories`, { full_name: 'acme/alice-staging' })
  ).json();

  await alice.goto(`/projects/${own.id}/onboarding`);
  await expect(alice.getByTestId('onboarding-title')).toBeVisible();
  await expect(step(alice, 'sign_in')).toHaveAttribute('data-state', 'done');
  await expect(step(alice, 'organization')).toHaveAttribute('data-state', 'done');
  // The repository is connected by name only — the App grant is honestly open.
  await expect(step(alice, 'repository')).toHaveAttribute('data-state', 'done');
  await expect(step(alice, 'github_access')).toHaveAttribute('data-state', 'todo');
  await expect(step(alice, 'snapshot')).toHaveAttribute('data-state', 'todo');
  await expect(step(alice, 'test_event')).toHaveAttribute('data-state', 'blocked');

  // Permission explanations are explicit before any grant.
  await expect(alice.getByTestId('permission-list')).toContainText('contents:read');
  await expect(alice.getByTestId('permission-list')).toContainText('No autonomous remediation');

  // Register a staging app and mint its ingest token through the real UI.
  await alice.goto(`/projects/${own.id}/live-defense/apps`);
  await alice.getByLabel('Name').fill('onboarding-staging');
  await alice.getByLabel('Environment').selectOption('owned_test');
  await alice.getByRole('button', { name: 'Register app' }).click();
  const appCard = alice.locator('li', { hasText: 'onboarding-staging' }).first();
  await expect(appCard).toBeVisible();
  await appCard.getByRole('button', { name: 'Issue ingest token' }).click();
  await expect(appCard.getByTestId('live-defense-token-once')).toBeVisible();

  // Attest the deployment-to-commit mapping for the connected repo.
  await alice.goto(`/projects/${own.id}/live-defense/first-responder`);
  const mapping = alice.locator('section[aria-label="Owner-attested deploy-to-commit mapping"]');
  await mapping.getByLabel('Application').selectOption({ label: 'onboarding-staging' });
  await mapping.getByLabel('Repository id').fill(repo.id);
  await mapping.getByLabel('Commit SHA').fill(env('E2E_COMMIT'));
  await mapping.getByRole('button', { name: 'Attest mapping' }).click();
  await expect(mapping.getByRole('button', { name: 'Attest mapping' })).toBeEnabled();

  // Back on the wizard the computed steps moved; the test event is now allowed.
  await alice.goto(`/projects/${own.id}/onboarding`);
  await expect(step(alice, 'application')).toHaveAttribute('data-state', 'done');
  await expect(step(alice, 'deploy_mapping')).toHaveAttribute('data-state', 'done');
  await expect(step(alice, 'monitoring')).toHaveAttribute('data-state', 'done');
  await expect(step(alice, 'test_event')).toHaveAttribute('data-state', 'todo');

  await alice.getByTestId('onb-test-event').click();
  await expect(alice.getByTestId('onb-test-event-result')).toBeVisible({ timeout: 30_000 });
  await expect(alice.getByTestId('onb-test-event-result')).toContainText('monitoring event');
  await expect(step(alice, 'test_event')).toHaveAttribute('data-state', 'done', { timeout: 15_000 });
  await expect(step(alice, 'evidence')).toHaveAttribute('data-state', 'done', { timeout: 15_000 });
  await evidenceShot(alice, 'onboarding-complete');

  // Disconnect stops mediated access and revokes the attested mapping — and says so
  // honestly (the GitHub App grant itself is the customer's GitHub-side action).
  await alice.getByTestId('onb-disconnect-repo').click();
  await alice.getByTestId('onb-disconnect-repo-confirm').click();
  await expect(alice.getByTestId('onb-disconnect-repo-done')).toBeVisible({ timeout: 15_000 });
  await expect(alice.getByTestId('onb-disconnect-repo-done')).toContainText('GitHub App grant still stands');
  await expect(step(alice, 'deploy_mapping')).toHaveAttribute('data-state', 'todo', { timeout: 15_000 });
  await evidenceShot(alice, 'onboarding-disconnected');

  await expectNoSideScroll(alice);
});

/**
 * A brand-new synthetic customer: first sign-in creates a fresh organization, then a
 * new project, repository, scope, application and deploy mapping — every onboarding
 * step through the real HTTP routes, including missing-prerequisite and
 * permission-failure states, and the monitoring event stays labelled as a test
 * everywhere even though it traversed the authenticated ingest channel.
 */
test('a fresh customer org completes onboarding through real routes', async ({ browser }) => {
  const fresh = await newUser(browser, 'e2e-fresh-customer');
  const api = env('E2E_API');
  const csrf = ((await (await fresh.request.get(`${api}/me`)).json()) as { csrf_token: string }).csrf_token;
  const post = (path: string, data: unknown) =>
    fresh.request.post(`${api}${path}`, { data: data ?? {}, headers: { 'X-CSRF-Token': csrf } });

  const project = await (await post('/projects', { name: 'fresh customer (e2e)' })).json();
  const repo = await (
    await post(`/projects/${project.id}/repositories`, { full_name: 'acme/fresh-staging' })
  ).json();

  // Missing prerequisites surface as explicit states, never silently skipped.
  await fresh.goto(`/projects/${project.id}/onboarding`);
  await expect(step(fresh, 'repository')).toHaveAttribute('data-state', 'done');
  await expect(step(fresh, 'github_access')).toHaveAttribute('data-state', 'todo');
  await expect(step(fresh, 'snapshot')).toHaveAttribute('data-state', 'todo');
  await expect(step(fresh, 'scope')).toHaveAttribute('data-state', 'todo');
  await expect(step(fresh, 'test_event')).toHaveAttribute('data-state', 'blocked');

  // Prerequisite failure is a real error, not a silent no-op: no app registered yet.
  const denied = await post(`/projects/${project.id}/onboarding/test-event`, {});
  expect(denied.status()).toBe(409);
  expect((await denied.json()).error.message).toContain('staging application');

  // A sandbox-only scope may be owner-approved — no second human blocks solo onboarding.
  const scope = await (
    await post(`/projects/${project.id}/scopes`, {
      name: 'onboarding sandbox',
      expires_at: new Date(Date.now() + 7 * 86_400_000).toISOString(),
      targets: [
        {
          kind: 'sandbox_build',
          repo_id: repo.id,
          build_recipe_ref: 'examples/payments-lite/sandbox.yaml',
        },
      ],
    })
  ).json();
  expect((await post(`/scopes/${scope.id}/approve`, {})).ok()).toBeTruthy();
  await fresh.reload();
  await expect(step(fresh, 'scope')).toHaveAttribute('data-state', 'done', { timeout: 15_000 });

  // Register the staging app and mint its ingest token through the real UI.
  await fresh.goto(`/projects/${project.id}/live-defense/apps`);
  await fresh.getByLabel('Name').fill('fresh-staging-app');
  await fresh.getByLabel('Environment').selectOption('owned_test');
  await fresh.getByRole('button', { name: 'Register app' }).click();
  const appCard = fresh.locator('li', { hasText: 'fresh-staging-app' }).first();
  await expect(appCard).toBeVisible();
  await appCard.getByRole('button', { name: 'Issue ingest token' }).click();
  await expect(appCard.getByTestId('live-defense-token-once')).toBeVisible();

  // Attest the deployment-to-commit mapping for the fresh repository.
  await fresh.goto(`/projects/${project.id}/live-defense/first-responder`);
  const mapping = fresh.locator('section[aria-label="Owner-attested deploy-to-commit mapping"]');
  await mapping.getByLabel('Application').selectOption({ label: 'fresh-staging-app' });
  await mapping.getByLabel('Repository id').fill(repo.id);
  await mapping.getByLabel('Commit SHA').fill(env('E2E_COMMIT'));
  await mapping.getByRole('button', { name: 'Attest mapping' }).click();
  await expect(mapping.getByRole('button', { name: 'Attest mapping' })).toBeEnabled();

  await fresh.goto(`/projects/${project.id}/onboarding`);
  await expect(step(fresh, 'application')).toHaveAttribute('data-state', 'done');
  await expect(step(fresh, 'deploy_mapping')).toHaveAttribute('data-state', 'done');
  await expect(step(fresh, 'monitoring')).toHaveAttribute('data-state', 'done');
  await fresh.getByTestId('onb-test-event').click();
  await expect(fresh.getByTestId('onb-test-event-result')).toBeVisible({ timeout: 30_000 });
  await expect(step(fresh, 'test_event')).toHaveAttribute('data-state', 'done', { timeout: 15_000 });
  await expect(step(fresh, 'evidence')).toHaveAttribute('data-state', 'done', { timeout: 15_000 });

  // The produced event is authenticated (not simulated) yet unmistakably a test —
  // both in the record and in the customer-facing view.
  const events = await (
    await fresh.request.get(`${api}/projects/${project.id}/events`)
  ).json();
  const testEvents = events.items.filter((e: { test_event: boolean }) => e.test_event);
  expect(testEvents.length).toBeGreaterThan(0);
  expect(testEvents.every((e: { simulated: boolean }) => !e.simulated)).toBeTruthy();
  await fresh.goto(`/projects/${project.id}/live-defense`);
  await expect(fresh.getByText('Onboarding test event (authorized)')).toBeVisible({ timeout: 15_000 });
  await evidenceShot(fresh, 'onboarding-fresh-complete');

  // Disconnect: mediated access stops, mappings die, the grant claim stays honest.
  await fresh.goto(`/projects/${project.id}/onboarding`);
  await fresh.getByTestId('onb-disconnect-repo').click();
  await fresh.getByTestId('onb-disconnect-repo-confirm').click();
  await expect(fresh.getByTestId('onb-disconnect-repo-done')).toBeVisible({ timeout: 15_000 });
  await expect(step(fresh, 'deploy_mapping')).toHaveAttribute('data-state', 'todo', { timeout: 15_000 });
});

test('onboarding progress is project-isolated', async ({ browser }) => {
  const alice = await newUser(browser, 'e2e-alice');
  const mallory = await newUser(browser, 'e2e-onboard-outsider');
  await mallory.goto(`/projects/${project()}/onboarding`);
  await expect(mallory.getByText('Could not load project')).toBeVisible({ timeout: 15_000 });
  await alice.goto(`/projects/${project()}/onboarding`);
  await expect(alice.getByTestId('onboarding-title')).toBeVisible();
});
