import { expect, test, type Page } from '@playwright/test';
import { completeSetup, env, evidenceShot, newUser } from './helpers';

type Repo = { id: string; full_name: string; disconnected: boolean; automatic_assessments: boolean };

async function apiClient(page: Page) {
  const api = env('E2E_API');
  const csrf = ((await (await page.request.get(`${api}/me`)).json()) as { csrf_token: string }).csrf_token;
  const headers = { 'X-CSRF-Token': csrf };
  return {
    get: async <T>(path: string): Promise<T> => {
      const res = await page.request.get(`${api}${path}`);
      expect(res.ok(), await res.text()).toBeTruthy();
      return (await res.json()) as T;
    },
    post: (path: string, data: unknown) => page.request.post(`${api}${path}`, { data, headers }),
    patch: (path: string, data: unknown) => page.request.patch(`${api}${path}`, { data, headers }),
  };
}

/**
 * Two repositories in one project, through the rendered console against the real API:
 * connect both by name -> each repository has its own first-time setup -> disconnect one
 * (the sibling stays live) -> the disconnected repository refuses setup changes -> reconnect
 * (automatic reviews stay off) -> completed setup survives the whole cycle and a reload.
 */
test('multi-repository project: connect, per-repository setup, disconnect and reconnect', async ({ browser }) => {
  const page = await newUser(browser, 'e2e-release-owner');
  const client = await apiClient(page);
  const stamp = Date.now();
  const first = `acme/release-a-${stamp}`;
  const second = `acme/release-b-${stamp}`;
  const created = await client.post('/projects', { name: `Release journey ${stamp}` });
  expect(created.status(), await created.text()).toBe(201);
  const projectId = ((await created.json()) as { id: string }).id;

  // `repo` pins the project's selected repository: without it the shell picks the first
  // live row of the API list, whose order is unspecified, and an unfinished pick redirects
  // Settings to that repository's Setup (see REPORT.md, P2).
  const connect = async (name: string, repo?: string): Promise<void> => {
    const query = repo ? `?repo=${encodeURIComponent(repo)}` : '';
    await page.goto(`/projects/${projectId}/settings${query}#connect-repo`);
    await page.getByLabel('owner/name').fill(name);
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
  };
  const idOf = async (name: string): Promise<string> => {
    const { items } = await client.get<{ items: Repo[] }>(`/projects/${projectId}/repositories`);
    const id = items.find(r => r.full_name === name)?.id;
    expect(id, `${name} is listed by the API`).toBeTruthy();
    return id!;
  };
  // The first repository of a fresh project lands on its first-time setup.
  await connect(first);
  await expect(page).toHaveURL(/\/projects\/[^/]+\/setup\?repo=/);
  const firstId = new URL(page.url()).searchParams.get('repo')!;
  expect(firstId).toBe(await idOf(first));
  await completeSetup(page, projectId, firstId);
  // A second repository joins the same project's Repositories list.
  await connect(second, firstId);
  await expect(page.getByTestId(`repo-controls-${second}`)).toBeVisible();
  const secondId = await idOf(second);
  expect(secondId).not.toBe(firstId);

  // Setup is per repository: the second one starts at question 1 while the first stays done.
  await page.goto(`/projects/${projectId}?repo=${secondId}`);
  await expect(page).toHaveURL(new RegExp(`/setup\\?repo=${secondId}`));
  await expect(page.getByTestId('setup-step-1')).toBeVisible();
  await page.goto(`/projects/${projectId}/setup?repo=${firstId}`);
  await expect(page.getByTestId('setup-done')).toBeVisible();

  const listed = await client.get<{ items: Repo[] }>(`/projects/${projectId}/repositories`);
  expect(listed.items.map(r => r.full_name).sort()).toEqual([first, second]);

  // Disconnect the first repository from its settings row.
  await page.goto(`/projects/${projectId}/settings?repo=${firstId}`);
  const controls = page.getByTestId(`repo-controls-${first}`);
  await controls.getByRole('button', { name: 'Disconnect…' }).click();
  await controls.getByRole('button', { name: 'Disconnect', exact: true }).click();
  const parked = page.getByTestId(`repo-disconnected-${first}`);
  await expect(parked).toBeVisible();
  await page.reload();
  await expect(page.getByTestId(`repo-disconnected-${first}`)).toBeVisible();
  await evidenceShot(page, 'release-journey-disconnected');

  const afterDisconnect = await client.get<{ items: Repo[] }>(`/projects/${projectId}/repositories`);
  const state = (name: string) => afterDisconnect.items.find(r => r.full_name === name)?.disconnected;
  expect(state(first)).toBe(true);
  expect(state(second)).toBe(false);
  const blocked = await client.patch(`/repositories/${firstId}/setup`, { answers: {}, complete: true });
  expect(blocked.status()).toBe(409);

  // Reconnect: the row returns live, automatic reviews stay off, setup history is kept.
  await page.getByTestId(`repo-disconnected-${first}`).getByRole('button', { name: 'Reconnect' }).click();
  await expect(page.getByTestId(`repo-disconnected-${first}`)).toHaveCount(0);
  const reconnected = page.getByTestId(`repo-controls-${first}`);
  await expect(reconnected).toBeVisible();
  await expect(reconnected.getByRole('switch', { name: `Automatic reviews for ${first}` })).toHaveAttribute('aria-checked', 'false');
  await page.reload();
  await expect(page.getByTestId(`repo-controls-${first}`)).toBeVisible();
  await page.goto(`/projects/${projectId}/setup?repo=${firstId}`);
  await expect(page.getByTestId('setup-done')).toBeVisible();
  await evidenceShot(page, 'release-journey-reconnected');

  const final = await client.get<{ items: Repo[] }>(`/projects/${projectId}/repositories`);
  expect(final.items.every(r => !r.disconnected)).toBe(true);
  expect(final.items.find(r => r.id === firstId)?.automatic_assessments).toBe(false);
});
