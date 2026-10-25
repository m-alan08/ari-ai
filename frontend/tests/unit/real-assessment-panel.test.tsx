import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AccessibleRepository, AuthorizedScope } from '@hackatl/frontend-transport';

const mocks = vi.hoisted(() => ({ repository: vi.fn(), updateRepository: vi.fn(), scopes: vi.fn(), assessments: vi.fn(), approveScope: vi.fn(), startAssessment: vi.fn(), assessment: vi.fn(), push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock('@/lib/api', () => ({ api: () => mocks, idempotencyKey: () => 'key-test' }));
vi.mock('@/lib/github-connect', () => ({ projectForRepository: async () => 'prj_test', connectRepo: vi.fn() }));
vi.mock('@/components/ui', () => ({ ErrorState: ({ context }: { context?: string }) => <div role="alert">{context}</div> }));
import { RealAssessmentPanel } from '@/components/real-assessment-panel';

const repo: AccessibleRepository = { full_name: 'alice/app', installation_id: 1, default_branch: 'main', private: true, connected_repo_id: 'repo_test' };
const scope: AuthorizedScope = {
  id: 'scp_test', project_id: 'prj_test', name: 'Sandbox', status: 'pending', requested_by: 'usr_alice',
  approved_by: null, approved_at: null, expires_at: '2099-01-01T00:00:00Z', notes: null,
  targets: [{ kind: 'sandbox_build', repo_id: 'repo_test', build_recipe_ref: 'build_recipe.json', exposed_ports: [] }], allowed_check_categories: [],
};
const view = () => <RealAssessmentPanel org="alice" repo={repo} sha={'a'.repeat(40)} onConnected={vi.fn()} />;

beforeEach(() => {
  mocks.repository.mockResolvedValue({ automatic_assessments: false });
  mocks.updateRepository.mockImplementation(async (_id, body) => { mocks.repository.mockResolvedValue(body); return body; });
  mocks.scopes.mockResolvedValue({ items: [scope], next_cursor: null });
  mocks.assessments.mockResolvedValue({ items: [], next_cursor: null });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); });

it('lets an owner explicitly approve sandbox authorization and then offers the real run', async () => {
  mocks.approveScope.mockImplementation(async () => {
    mocks.scopes.mockResolvedValue({ items: [{ ...scope, status: 'active' }], next_cursor: null });
    return { ...scope, status: 'active' };
  });
  render(view());
  fireEvent.click(await screen.findByRole('button', { name: 'Approve sandbox authorization' }));
  expect(await screen.findByRole('button', { name: 'Run the 7 agents' })).toBeTruthy();
  expect(mocks.approveScope).toHaveBeenCalledWith('scp_test');
  expect(mocks.push).not.toHaveBeenCalled(); // Approval never starts or spends on an assessment.
});

it('refreshes approval changes in place instead of staying stuck on pending', async () => {
  vi.useFakeTimers();
  await act(async () => { render(view()); });
  expect(screen.getByRole('button', { name: 'Approve sandbox authorization' })).toBeTruthy();
  mocks.scopes.mockResolvedValue({ items: [{ ...scope, status: 'active' }], next_cursor: null });
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(screen.getByRole('button', { name: 'Run the 7 agents' })).toBeTruthy();
  expect(screen.queryByText('Loading…')).toBeNull();
});

it('rechecks approval when returning from another tab', async () => {
  render(view());
  await screen.findByRole('button', { name: 'Approve sandbox authorization' });
  mocks.scopes.mockResolvedValue({ items: [{ ...scope, status: 'active' }], next_cursor: null });
  fireEvent.focus(window);
  expect(await screen.findByRole('button', { name: 'Run the 7 agents' })).toBeTruthy();
});

it('starts only this commit and stays in its workspace', async () => {
  const selected = vi.fn();
  mocks.scopes.mockResolvedValue({ items: [{ ...scope, status: 'active' }], next_cursor: null });
  mocks.startAssessment.mockResolvedValue({ id: 'asm_selected', repo_id: 'repo_test', commit_sha: 'a'.repeat(40), state: 'CREATED' });
  render(<RealAssessmentPanel org="alice" repo={repo} sha={'a'.repeat(40)} onConnected={vi.fn()} onRunSelected={selected} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Run the 7 agents' }));
  await screen.findByText('Last run: Created');
  expect(screen.queryByRole('button', { name: 'Run the 7 agents' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Run again' })).toBeNull();
  expect(mocks.startAssessment).toHaveBeenCalledWith('prj_test', expect.objectContaining({ repo_id: 'repo_test', commit_sha: 'a'.repeat(40) }), expect.any(String));
  expect(selected).toHaveBeenCalledWith('asm_selected');
  expect(mocks.push).not.toHaveBeenCalled();
});

it('scans older history once and refreshes the resolved assessment directly', async () => {
  vi.useFakeTimers();
  const matching = { id: 'asm_old', repo_id: 'repo_test', commit_sha: 'a'.repeat(40), created_at: '2026-01-01', state: 'VALIDATION' };
  mocks.assessments.mockImplementation(async (_id: string, options: { cursor?: string }) => options.cursor ? { items: [matching], next_cursor: null } : { items: [], next_cursor: 'old-page' });
  mocks.assessment.mockResolvedValue({ assessment: matching });
  await act(async () => { render(view()); });
  expect(mocks.assessments.mock.calls.filter(call => call[1]?.cursor === 'old-page')).toHaveLength(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(mocks.assessments.mock.calls.filter(call => call[1]?.cursor === 'old-page')).toHaveLength(1);
  expect(mocks.assessment).toHaveBeenCalledWith('asm_old', expect.any(Object));
});

it('stops polling once the resolved assessment is terminal', async () => {
  vi.useFakeTimers();
  const matching = { id: 'asm_done', repo_id: 'repo_test', commit_sha: 'a'.repeat(40), created_at: '2026-01-01', state: 'COMPLETED' };
  mocks.assessments.mockImplementation(async (_id: string, options: { cursor?: string }) => options.cursor ? { items: [matching], next_cursor: null } : { items: [], next_cursor: 'old-page' });
  mocks.assessment.mockResolvedValue({ assessment: matching });
  await act(async () => { render(view()); });
  const calls = mocks.assessments.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(mocks.assessments.mock.calls).toHaveLength(calls);
  expect(mocks.assessment).not.toHaveBeenCalled();
});


it('shows automatic mode without a manual start and switches to manual only after saving OFF', async () => {
  mocks.repository.mockResolvedValue({ automatic_assessments: true });
  mocks.scopes.mockResolvedValue({ items: [{ ...scope, status: 'active' }], next_cursor: null });
  render(view());
  const toggle = await screen.findByRole('switch', { name: 'Automatic runs' });
  await screen.findByText(/Waiting for an automatic run/);
  expect(screen.queryByRole('button', { name: 'Run the 7 agents' })).toBeNull();
  expect(mocks.startAssessment).not.toHaveBeenCalled();
  fireEvent.click(toggle);
  expect(await screen.findByRole('button', { name: 'Run the 7 agents' })).toBeTruthy();
  expect(mocks.updateRepository).toHaveBeenCalledWith('repo_test', { automatic_assessments: false });
});
