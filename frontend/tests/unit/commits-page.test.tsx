import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ sha: 'b'.repeat(40) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => ({ get: (key: string) => key === 'commit' ? state.sha : null, toString: () => `repo=repo-1&commit=${state.sha}` }),
}));
vi.mock('@/components/project', () => ({ useProject: () => ({
  project: { id: 'project-1', settings: { monitoring_policy: { enabled: false } } },
  selectedRepo: { id: 'repo-1', full_name: 'acme/payments-lite', default_branch: 'main' },
}) }));
vi.mock('@/components/commit-assessment-chips', () => ({
  CommitAssessmentProvider: ({ children }: { children: React.ReactNode }) => children,
  CommitAssessmentChip: () => null,
}));
vi.mock('@/components/assessment-history', () => ({ AssessmentList: () => null }));
vi.mock('@/components/start-assessment', () => ({ StartAssessment: () => null }));
vi.mock('@/components/agent-run-list', () => ({ AgentRunList: () => <p>Recorded agent runs</p> }));
vi.mock('@/lib/use-resource', () => ({
  useResource: (key: string | null) => {
    const run = { id: 'asm-1', repo_id: 'repo-1', commit_sha: state.sha, state: 'COMPLETED', created_at: '2026-10-25T00:00:00Z', trigger: 'manual', usage: { llm_calls: 1, usd: 0 } };
    const data = key?.startsWith('commits:') ? { items: [{ sha: 'a'.repeat(40), message: 'GitHub commit', committed_at: '2026-10-24T00:00:00Z' }], available: true, reason: null }
      : key?.startsWith('commits-asm:') ? { items: [run], next_cursor: null }
      : key?.startsWith('commits-findings:') ? { items: [], next_cursor: null }
      : key?.startsWith('commit-agent-run:') ? { items: [], next_cursor: null }
      : { items: [], next_cursor: null };
    return { status: 'ready', data, error: undefined, refreshError: undefined, reload: vi.fn() };
  },
}));

import CommitsPage from '@/app/(console)/projects/[projectId]/commits/page';

afterEach(cleanup);

it('merges GitHub and assessment-only commits and switches to the recorded agents tab', () => {
  render(<CommitsPage />);
  expect(screen.getByTestId(`commit-row-${'a'.repeat(40)}`)).toBeTruthy();
  expect(screen.getByTestId(`commit-row-${state.sha}`)).toBeTruthy();
  fireEvent.click(screen.getByRole('tab', { name: '7 agents' }));
  expect(screen.getByText('Recorded agent runs')).toBeTruthy();
});
