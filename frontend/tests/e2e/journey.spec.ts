import { createHmac, randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { env, evidenceShot, expectNoSideScroll, newUser } from './helpers';

const project = () => env('E2E_PROJECT_ID');

/**
 * One assessed change, end to end, through the rendered UI against the real API:
 * scope request -> second-person approval -> assessment -> live timeline over SSE ->
 * human remediation approval -> seven security agents + reviewer -> finding
 * evidence chain -> hash-checked patch diff -> independent Verify -> persists on reload.
 */
test('one real assessment from scope approval to verified patch', async ({ browser }) => {
  const alice = await newUser(browser, 'e2e-alice');
  const bob = await newUser(browser, 'e2e-bob');

  // Alice requests a sandbox scope. As an owner she may explicitly approve it,
  // but this journey exercises independent approval by Bob.
  await alice.goto(`/projects/${project()}/scopes`);
  await alice.getByLabel('Name').fill('payments-lite sandbox');
  await alice.getByLabel('Repository (sandbox build target)').selectOption({ label: env('E2E_REPO') });
  await alice.getByRole('button', { name: 'Request scope' }).click();
  const scopeRow = alice.locator('[data-testid^="scope-"]').filter({ hasText: 'payments-lite sandbox' });
  await expect(scopeRow.getByText('Pending approval')).toBeVisible();
  await expect(scopeRow.getByRole('button', { name: 'Approve scope' })).toBeEnabled();

  // Bob approves it.
  await bob.goto(`/projects/${project()}/scopes`);
  const bobRow = bob.locator('[data-testid^="scope-"]').filter({ hasText: 'payments-lite sandbox' });
  await bobRow.getByRole('button', { name: 'Approve scope' }).click();
  await expect(bobRow.getByText('Active')).toBeVisible();

  // Alice starts the assessment at the exact commit.
  await alice.goto(`/projects/${project()}`);
  await alice.getByLabel('Repository', { exact: true }).selectOption({ label: env('E2E_REPO') });
  await alice.getByLabel('Authorized scope').selectOption({ label: 'payments-lite sandbox' });
  await alice.getByLabel('Commit SHA').fill(env('E2E_COMMIT'));
  await alice.getByRole('button', { name: 'Start assessment' }).click();
  await alice.waitForURL(/\/assessments\/asm_/);
  const asmUrl = alice.url();

  // The page leads with the eight-agent relay; technical detail (SSE log) is one click away.
  await expect(alice.getByTestId('agent-relay')).toBeVisible();
  await alice.getByTestId('technical-details').locator('summary').click();
  await expect(alice.getByTestId('event-log')).toBeVisible();
  await expect(alice.getByRole('heading', { name: 'Human approval required' })).toBeVisible({ timeout: 180_000 });
  await expect(alice.getByTestId('relay-reviewer')).toHaveAttribute('data-status', 'done');
  // The relay draws the human gate as waiting, and nothing after it as done yet.
  await expect(alice.getByTestId('relay-gate')).toHaveAttribute('data-status', 'waiting');
  await expect(alice.getByTestId('relay-patch')).toHaveAttribute('data-status', 'not_run');
  await expect(alice.getByTestId('relay-diagnose')).toHaveAttribute('data-status', 'done');
  await evidenceShot(alice, 'assessment-awaiting-approval');

  // The on-page gate names each finding, spells out what approving will and will not do,
  // and requires a second explicit click — an accidental tap cannot approve a security action.
  const gate = alice.getByRole('region', { name: 'Human approval required' });
  await expect(gate.getByText(/Fix request ·/).first()).toBeVisible();
  await expect(gate.getByText('Will:', { exact: false }).first()).toBeVisible();
  await expect(gate.getByText(/Will not:/).first()).toBeVisible();
  await gate.getByRole('button', { name: 'Approve remediation' }).first().click();
  await expect(gate.getByRole('alert')).toContainText('Confirm approval');
  await expect(gate.getByRole('alert')).toContainText(/never merges or deploys/);
  await gate.getByRole('button', { name: 'Back' }).click();
  await expect(gate.getByRole('alert')).toHaveCount(0);

  // Bob approves remediation from his inbox (with the will / will-not summary): one for the
  // security fix and one for the reviewer's functional repair. The run resumes when both are decided.
  await bob.goto(`/projects/${project()}/approvals`);
  const pending = bob.locator('[data-testid^="approval-"]').filter({ hasText: 'remediation approval' }).filter({ hasText: 'Pending' });
  await expect(pending).toHaveCount(2);
  await expect(pending.first().getByText('Merge, push, deploy, or change the repository.')).toBeVisible();
  for (let i = 0; i < 2; i++) {
    await pending.first().getByRole('button', { name: 'Approve remediation' }).click();
    // The inbox requires the same second explicit click as the on-page gate.
    await pending.first().getByRole('button', { name: 'Confirm approval' }).click();
    await expect(pending).toHaveCount(1 - i);
  }
  await expect(bob.locator('[data-testid^="approval-"]').filter({ hasText: 'remediation approval' }).getByText('Approved')).toHaveCount(2);

  // Alice's open page receives the rest of the run live.
  await expect(alice.getByRole('heading', { name: /Assessment/ }).getByText('Completed')).toBeVisible({ timeout: 180_000 });
  await expect(alice.getByTestId('stream-state')).toContainText('terminal state');
  // Honest provider labelling: scripted model turns are never presented as live.
  await expect(alice.getByTestId('provider-summary')).toContainText('Not a live-provider run');
  // review + seven agents + the reviewer's functional repair + its independent Verify
  await expect(alice.getByTestId('provider-summary')).toContainText('Test provider (scripted) × 9, Deterministic (no model call) × 1');
  for (const agent of ['reviewer', 'scout', 'probe', 'trace', 'diagnose', 'patch', 'verify', 'fortify']) {
    await expect(alice.getByTestId(`relay-${agent}`)).toHaveAttribute('data-status', 'done');
  }
  await expect(alice.getByTestId('relay-gate')).toHaveAttribute('data-status', 'passed');
  await expect(alice.getByTestId('relay-scout')).toContainText('Passed 1 suspicion to Probe.');
  // Selecting a relay tile opens that agent's recorded runs.
  await alice.getByTestId('relay-verify').click();
  await expect(alice.getByTestId('agent-team').getByRole('heading', { name: 'Verify' })).toBeVisible();
  // Keyboard only: focus a tile and press Enter; Space toggles the technical details.
  await alice.getByTestId('relay-trace').focus();
  await alice.keyboard.press('Enter');
  await expect(alice.getByTestId('agent-team').getByRole('heading', { name: 'Trace' })).toBeVisible();
  await expect(alice.getByTestId('relay-trace')).toHaveAttribute('aria-pressed', 'true');
  await evidenceShot(alice, 'assessment-completed');

  // Security finding evidence chain, patch diff and Verify verdict from persisted records.
  const findingLinks = alice.locator('a[href*="/findings/fnd_"]');
  await expect(findingLinks).toHaveCount(2);
  const security = alice.locator('li').filter({ has: alice.getByText('security', { exact: true }) }).locator('a[href*="/findings/"]').first();
  await security.click();
  await alice.waitForURL(/\/findings\/fnd_/);
  await expect(alice.getByRole('heading', { name: 'Evidence chain' })).toBeVisible();
  await expect(alice.getByText('Confirmed by Probe')).toBeVisible();
  await expect(alice.getByText('Verify: accepted').first()).toBeVisible();
  // Before / after: recorded reproduction vs independent re-test, and the code on both sides.
  await expect(alice.getByTestId('ba-before')).toContainText('The problem was reproduced');
  await expect(alice.getByTestId('ba-after')).toContainText('Independent re-test accepted the fix');
  await expect(alice.getByTestId('ba-after')).toContainText('The original problem no longer happens');
  await expect(alice.getByTestId('ba-code')).toContainText('Before');
  // The stored diff is redacted before hashing (a bearer token in the test), so it is checked
  // against its server hash and the difference from the patch's raw-diff hash is explained.
  await expect(alice.getByTestId('ba-hash')).toHaveText(/^(The diff matches the hash recorded with the patch\.|The stored diff matches its server hash\. It differs from the hash recorded with the patch because the store hid secret-looking text)/);
  await evidenceShot(alice, 'finding-before-after');
  await alice.getByRole('button', { name: /View diff/ }).first().click();
  await expect(alice.getByTestId('diff-view').first()).toBeVisible();
  await expect(alice.getByText('matches server hash')).toBeVisible();
  await evidenceShot(alice, 'finding-evidence-chain');

  // Everything survives a full reload: it is persisted state, not client memory.
  await alice.goto(asmUrl);
  await expect(alice.getByRole('heading', { name: /Assessment/ }).getByText('Completed')).toBeVisible();
  await alice.getByTestId('technical-details').locator('summary').click();
  await expect(alice.getByTestId('event-log').locator('li')).not.toHaveCount(0);

  // The overview connects the three pillars with this project's real records.
  await alice.goto(`/projects/${project()}`);
  await alice.getByText('Project progress & details', { exact: true }).click();
  const loop = alice.getByTestId('pillar-loop');
  await expect(loop.getByTestId('loop-review')).toContainText('No open problems in how the code behaves.');
  await expect(loop.getByTestId('loop-prove')).toContainText('2 fixes accepted by independent tests.');
  await expect(loop.getByTestId('loop-prove')).toContainText('nothing was merged or deployed');
  await expect(loop.getByTestId('loop-lab')).toContainText('isolated test environment');
  await alice.getByTestId('term-monitoring').focus();
  await alice.keyboard.press('Enter');
  await expect(loop.getByRole('note')).toContainText('Watching the code repository');
  await alice.keyboard.press('Escape');
  await expect(loop.getByRole('note')).toHaveCount(0);
  await alice.getByTestId('term-monitoring').click();
  await evidenceShot(alice, 'overview-pillar-loop');
  await alice.goto(asmUrl);
  await alice.reload();
  await expect(alice.getByTestId('relay-verify')).toHaveAttribute('data-status', 'done');

  // Phone width: the clarity views fit without sideways scrolling.
  for (const width of [375, 390]) {
    await alice.setViewportSize({ width, height: 800 });
    for (const url of [asmUrl, `/projects/${project()}`, '/projects', '/']) {
      await alice.goto(url);
      if (url === asmUrl) await expect(alice.getByTestId('agent-relay')).toBeVisible();
      else if (url.startsWith('/projects/')) await expect(alice.getByRole('heading', { name: '7 Agents', exact: true })).toBeVisible();
      else await alice.waitForLoadState('networkidle');
      await expectNoSideScroll(alice);
    }
  }
});

test('signed GitHub push appears in the monitoring feed', async ({ browser, request }) => {
  const page = await newUser(browser, 'e2e-alice');
  const me = await page.request.get(`${env('E2E_API')}/me`);
  const headers = { 'X-CSRF-Token': (await me.json()).csrf_token };
  const current = await (await page.request.get(`${env('E2E_API')}/projects/${project()}`)).json();
  expect((await page.request.patch(`${env('E2E_API')}/projects/${project()}`, {
    headers, data: { settings: { ...current.settings, monitoring_policy: { ...current.settings.monitoring_policy, enabled: true } } },
  })).ok()).toBeTruthy();
  expect((await page.request.patch(`${env('E2E_API')}/repositories/${env('E2E_REPO_ID')}`, {
    headers, data: { automatic_assessments: true },
  })).ok()).toBeTruthy();
  const payload = {
    // A docs-only push: monitoring must record it, and the default policy skips re-assessment.
    ref: 'refs/heads/main', before: env('E2E_COMMIT'), after: env('E2E_COMMIT'), forced: false, deleted: false,
    repository: { full_name: env('E2E_REPO'), id: 1 }, sender: { login: 'dev' },
    head_commit: { timestamp: new Date().toISOString(), message: 'e2e push' },
    commits: [{ added: [], modified: ['docs/notes.md'], removed: [] }],
  };
  const raw = JSON.stringify(payload);
  const signature = `sha256=${createHmac('sha256', env('E2E_WEBHOOK_SECRET')).update(raw).digest('hex')}`;
  const res = await request.post(`${env('E2E_API')}/webhooks/github`, {
    data: raw,
    headers: { 'Content-Type': 'application/json', 'X-GitHub-Event': 'push', 'X-GitHub-Delivery': randomUUID(), 'X-Hub-Signature-256': signature },
  });
  expect(res.status()).toBe(202);
  await page.goto(`/projects/${project()}/monitoring`);
  const feed = page.getByTestId('monitoring-feed');
  await expect(feed).toContainText('github');
  await expect(feed).toContainText('push');
  await expect(feed).toContainText('refs/heads/main');
  await evidenceShot(page, 'monitoring');
  await expect(page.getByTestId('rl-caveat')).toHaveText('Recommendation from a policy trained in an isolated test environment. Not applied automatically.');
});

test('connect a repository to a project through the UI', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-alice');
  // Projects are created implicitly when a repository is connected; for this walkthrough
  // seed one through the API, then drive the project pages in the rendered UI.
  const me = await page.request.get(`${env('E2E_API')}/me`);
  const created = await page.request.post(`${env('E2E_API')}/projects`, {
    data: { name: `ui-project-${Date.now()}` },
    headers: { 'X-CSRF-Token': (await me.json()).csrf_token },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const seeded = (await created.json()) as { id: string };
  await page.goto(`/projects/${seeded.id}`);
  await expect(page.getByText('No repository connected.')).toBeVisible();
  // Dev login has no GitHub App access, so the picker explains how to add repositories.
  await expect(page.getByTestId('repo-picker-empty')).toBeVisible();
  await page.getByLabel('owner/name').fill('acme/another-repo');
  await page.getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByRole('region', { name: 'Repositories' }).getByText('acme/another-repo')).toBeVisible();
  await expect(page.getByText('No assessments yet.')).toBeVisible();
  // Connected by name (no App installation): commits are explicitly unavailable, never invented.
  await expect(page.getByTestId('repo-commits-unavailable')).toBeVisible();

  // A repository that cannot be attacked live gets a labelled simulated attack and defense.
  await page.getByTestId('simulate-acme/another-repo').click();
  await expect(page.getByTestId('simulated-attack-note')).toContainText('acme/another-repo');
  await expect(page.getByTestId('simulated-attack-note').getByTestId('demo-marker')).toBeVisible();
  await page.getByTestId('demo-skip').click();
  await expect(page.getByTestId('demo-commit-message')).toHaveText('Simulated attack on acme/another-repo');

  // Continuous defense: an attack drill goes through the real detection pipeline, labelled simulated.
  await page.goto(page.url().replace(/\/simulate.*$/, '/monitoring'));
  await page.getByTestId('attack-drill-start').click();
  const report = page.getByTestId('attack-drill-report');
  await expect(report).toBeVisible({ timeout: 30_000 });
  await expect(report).toContainText('Detected');
  await expect(report).toContainText('recommended, not executed');
  await expect(page.getByTestId('monitoring-feed')).toContainText('log anomaly', { timeout: 15_000 });

  // GitHub onboarding: without App access the org chooser says how to add repositories.
  await page.goto('/connect');
  await expect(page.getByTestId('connect-empty')).toBeVisible();
});
