import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({ useParams: () => ({ org: 'test-team', repo: 'test-app' }) }));
vi.mock('@/lib/real-repo', () => ({
  useAccessibleRepo: () => ({
    status: 'ready',
    data: { connected_repo_id: 'repo_test', full_name: 'test-team/test-app', default_branch: 'main', private: false },
    reload: vi.fn(),
  }),
}));
vi.mock('@/lib/api', () => ({ api: () => ({ repositoryCommits: vi.fn() }) }));
vi.mock('@/lib/use-resource', () => ({
  useResource: () => ({ status: 'ready', data: { items: [], available: true, reason: null }, error: undefined, reload: vi.fn() }),
}));
vi.mock('@/components/repository-automation', () => ({ RepositoryAutomation: () => null }));
vi.mock('@/components/project-review-cta', () => ({
  ProjectReviewCta: ({ repoId }: { repoId: string | null }) => repoId ? <span data-testid="review-cta-repo">{repoId}</span> : null,
}));

import RepoCommitsPage from '@/app/(console)/connect/[org]/[repo]/page';

afterEach(cleanup);

it('shows the project review CTA for a connected repository', () => {
  render(<RepoCommitsPage />);
  expect(screen.getByTestId('review-cta-repo').textContent).toBe('repo_test');
});
