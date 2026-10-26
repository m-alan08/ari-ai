import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Assessment, Page } from '@hackatl/frontend-transport';
import { AssessmentList } from '@/components/assessment-history';

const mocks = vi.hoisted(() => ({ assessments: vi.fn(), reload: vi.fn(), nextCursor: 'asm_003' as string | null }));
vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  api: () => ({ assessments: mocks.assessments }),
}));
const asm = (id: string, repoId = 'repo-1') => ({
  id, repo_id: repoId, state: 'CREATED', commit_sha: 'a'.repeat(40), trigger: 'manual', created_at: '2026-10-17T03:00:00Z',
  usage: { llm_calls: 1, usd: 0 },
});
const FIRST_PAGE = [asm('asm_005'), asm('asm_004'), asm('asm_003')];
const renderList = (items = FIRST_PAGE) => render(
  <AssessmentList projectId="prj_one" repoId="repo-1" page={{ items: items as unknown as Assessment[], next_cursor: mocks.nextCursor } as Page<Assessment>} />,
);

beforeEach(() => { mocks.assessments.mockReset(); mocks.nextCursor = 'asm_003'; });
afterEach(cleanup);

it('says the list is truncated and appends older checks, deduped by id', async () => {
  mocks.assessments.mockResolvedValue({ items: [asm('asm_003'), asm('asm_002'), asm('asm_001')], next_cursor: null });
  renderList();
  expect(screen.getByTestId('assessments-truncated').textContent).toContain('Showing the newest 3 checks');
  fireEvent.click(screen.getByTestId('load-older-assessments'));
  await waitFor(() => expect(screen.queryByTestId('assessments-truncated')).toBeNull());
  expect(mocks.assessments).toHaveBeenCalledWith('prj_one', { cursor: 'asm_003' });
  expect(within(screen.getByTestId('assessment-history-mobile-list')).getAllByRole('link', { name: /^Open run & replay asm_/ }).map(a => a.getAttribute('href')?.split('?')[0]?.split('/').at(-1))).toEqual(['asm_005', 'asm_004', 'asm_003', 'asm_002', 'asm_001']);
});

it('shows no truncation note when there is no next cursor', () => {
  mocks.nextCursor = null;
  renderList();
  expect(screen.queryByTestId('assessments-truncated')).toBeNull();
});

it('surfaces a failed older-page load with a retry', async () => {
  mocks.assessments.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValue({ items: [asm('asm_002')], next_cursor: null });
  renderList();
  fireEvent.click(screen.getByTestId('load-older-assessments'));
  await waitFor(() => expect(screen.getByTestId('error-state')).toBeTruthy());
  expect(screen.getByTestId('assessments-truncated')).toBeTruthy();
});

it('shows only checks for the selected repository', () => {
  renderList([asm('asm_a'), asm('asm_b', 'repo-2')]);
  const links = within(screen.getByTestId('assessment-history-mobile-list')).getAllByRole('link', { name: /^Open run & replay/ });
  expect(links).toHaveLength(1);
  expect(links[0]?.getAttribute('href')).toContain('repo=repo-1');
});

it('still allows pagination when the first page has no checks for the selected repository', () => {
  renderList([asm('asm_other', 'repo-2')]);
  expect(screen.getByText('No assessments for this repository on the latest page.')).toBeTruthy();
  expect(screen.getByTestId('load-older-assessments')).toBeTruthy();
});
