import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  sha: 'a'.repeat(40),
  selected: 'a'.repeat(40) as string | null,
  replace: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: state.replace }),
  useSearchParams: () => ({
    get: (key: string) => key === 'commit' ? state.selected : null,
    toString: () => `repo=r1${state.selected ? `&commit=${state.selected}` : ''}`,
  }),
}));
vi.mock('@/components/project', () => ({ useProject: () => ({
  project: { id: 'p1', settings: { monitoring_policy: { enabled: false } } },
  selectedRepo: { id: 'r1', full_name: 'team/app', default_branch: 'main' },
}) }));
vi.mock('@/components/commit-assessment-chips', () => ({
  CommitAssessmentProvider: ({ children }: { children: React.ReactNode }) => children,
  CommitAssessmentChip: () => null,
}));
vi.mock('@/components/assessment-history', () => ({ AssessmentList: () => null }));
vi.mock('@/components/start-assessment', () => ({ StartAssessment: () => null }));
vi.mock('@/components/agent-run-list', () => ({ AgentRunList: () => <div data-testid="agent-team" /> }));
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: React.PropsWithChildren<{ href: string }>) => <a href={href} {...props}>{children}</a>,
}));
vi.mock('@/lib/use-resource', () => ({
  useResource: (key: string | null) => {
    const run = { id: 'a1', repo_id: 'r1', commit_sha: state.sha, state: 'COMPLETED', created_at: '2026-10-25T00:00:00Z', trigger: 'manual', usage: { llm_calls: 1, usd: 0 } };
    const data = key?.startsWith('commits:') ? { items: [{ sha: state.sha, message: 'Fix payment check', committed_at: '2026-10-24T00:00:00Z' }], available: true, reason: null }
      : key?.startsWith('commits-asm:') ? { items: [run], next_cursor: null }
      : { items: [], next_cursor: null };
    return { status: 'ready', data, error: undefined, refreshError: undefined, reload: vi.fn() };
  },
}));

import CommitsPage from '@/app/(console)/projects/[projectId]/commits/page';

afterEach(() => {
  cleanup();
  state.replace.mockReset();
});

it('keeps commit details linked to the selected repo and updates the URL on selection', () => {
  render(<CommitsPage />);
  const runLink = screen.getByRole('link', { name: 'Open full run →' });
  expect(runLink.getAttribute('href')).toBe('/projects/p1/assessments/a1?repo=r1');
  expect(screen.getByRole('link', { name: 'Analyze this commit →' }).getAttribute('href'))
    .toBe(`/connect/team/app/commit/${state.sha}`);

  state.selected = null;
  fireEvent.click(screen.getByTestId(`commit-row-${state.sha}`));
  expect(state.replace).toHaveBeenCalledWith(
    `/projects/p1/commits?repo=r1&commit=${state.sha}`,
    { scroll: false },
  );
});
