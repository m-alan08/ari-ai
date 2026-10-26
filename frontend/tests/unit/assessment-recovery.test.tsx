import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '@hackatl/frontend-transport';
import { StartAssessment } from '@/components/start-assessment';

const mocks = vi.hoisted(() => ({ start: vi.fn(), push: vi.fn(), reload: vi.fn(), projectId: 'prj_one', sequence: 0 }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock('@/components/project', () => ({ useProject: () => ({
  project: { id: mocks.projectId, name: 'owned', settings: { monitoring_policy: { enabled: true } } },
  selectedRepo: { id: 'rep_one', full_name: 'owned/repo', default_branch: 'main' },
  repos: [{ id: 'rep_one', full_name: 'owned/repo', default_branch: 'main' }], reloadRepos: mocks.reload,
}) }));
vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  api: () => ({ startAssessment: mocks.start }), idempotencyKey: () => `action-${++mocks.sequence}`,
}));
vi.mock('@/lib/use-resource', () => ({ useResource: (key: string | null) => ({
  status: 'ready', reload: mocks.reload,
  data: { items: key?.startsWith('scopes:') ? [{ id: 'scp_one', name: 'owned scope', status: 'active', targets: [{ repo_id: 'rep_one' }] }] : [] },
}) }));
beforeEach(() => { mocks.start.mockReset(); mocks.push.mockReset(); mocks.projectId = 'prj_one'; mocks.sequence = 0; });
afterEach(cleanup);
function fill() {
  fireEvent.change(screen.getByLabelText('Authorized scope'), { target: { value: 'scp_one' } });
  fireEvent.change(screen.getByLabelText('Commit SHA'), { target: { value: 'b'.repeat(40) } });
}
async function submit() {
  fireEvent.click(screen.getByRole('button', { name: 'Start assessment' }));
  await waitFor(() => expect((screen.getByRole('button', { name: 'Start assessment' }) as HTMLButtonElement).disabled).toBe(false));
}
const conflict = () => new ApiError(409, { code: 'IDEMPOTENCY_CONFLICT', message: 'Idempotency-Key reused with a different request', details: {}, request_id: null });
it('preserves same-action keys after network loss', async () => {
  mocks.start.mockRejectedValue(new TypeError('network lost'));
  render(<StartAssessment onStarted={() => {}} />); fill(); await submit(); await submit();
  expect(mocks.start).toHaveBeenCalledTimes(2);
  expect(mocks.start.mock.calls[1]![2]).toBe(mocks.start.mock.calls[0]![2]);
  expect(screen.queryByRole('button', { name: 'Prepare a new assessment action' })).toBeNull();
});
it('offers explicit conflict recovery without submitting or hiding the backend error', async () => {
  mocks.start.mockRejectedValueOnce(conflict()).mockResolvedValue({ id: 'asm_one' });
  render(<StartAssessment onStarted={() => {}} />); fill(); await submit();
  expect(screen.getByTestId('error-state').textContent).toContain('IDEMPOTENCY_CONFLICT');
  expect(screen.getByText(/Check the assessment list/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare a new assessment action' }));
  expect(mocks.start).toHaveBeenCalledTimes(1);
  await submit();
  expect(mocks.start).toHaveBeenCalledTimes(2);
  expect(mocks.start.mock.calls[1]![2]).not.toBe(mocks.start.mock.calls[0]![2]);
  expect(mocks.push).toHaveBeenCalledTimes(1);
});
it('does not carry an action key across project changes', async () => {
  mocks.start.mockRejectedValue(new TypeError('network lost'));
  const view = render(<StartAssessment onStarted={() => {}} />); fill(); await submit();
  mocks.projectId = 'prj_two'; view.rerender(<StartAssessment onStarted={() => {}} />); await submit();
  expect(mocks.start.mock.calls[1]![0]).toBe('prj_two');
  expect(mocks.start.mock.calls[1]![2]).not.toBe(mocks.start.mock.calls[0]![2]);
});
