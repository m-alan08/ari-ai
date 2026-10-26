import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ items: [] as unknown[] }));

vi.mock('@/components/project', () => ({ useProject: () => ({
  project: { id: 'project-1' },
  selectedRepo: { id: 'repo-1', full_name: 'acme/payments', default_branch: 'main' },
}) }));
vi.mock('@/lib/use-resource', () => ({
  useResource: (key: string) => ({
    status: 'ready',
    data: { items: key?.includes('snapshots') ? [{ id: 'snap-1' }] : key?.includes('assessments') ? [{ id: 'asm-1', repo_id: 'repo-1' }] : state.items },
    error: undefined,
    reload: vi.fn(),
  }),
}));
vi.mock('@/lib/api', () => ({ api: () => ({ findings: vi.fn() }) }));

import FindingsPage from '@/app/(console)/projects/[projectId]/findings/page';

afterEach(() => {
  cleanup();
  state.items = [];
});

it('shows other finding counts and opens the fixed tab when Open is empty', () => {
  state.items = [{
    id: 'finding-fixed',
    assessment_id: 'asm-1',
    snapshot_id: null,
    title: 'Verified fix',
    status: 'verified',
    kind: 'security',
    severity: null,
    commit_sha: 'abcdef123456',
    updated_at: '2026-10-25T00:00:00Z',
  }];

  render(<FindingsPage />);

  expect(screen.getByText('No open findings. Counts: 0 open, 0 hypotheses, 1 fixed, 0 refuted or closed.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'View fixed findings' }));
  expect(screen.getByRole('tab', { name: 'Fixed (independently verified)' }).getAttribute('aria-selected')).toBe('true');
  expect(within(screen.getByTestId('findings-mobile-list')).getByRole('link', { name: 'Verified fix' })).toBeTruthy();
});
