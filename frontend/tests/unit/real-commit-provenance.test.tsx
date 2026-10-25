import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import RealCommitPage from '@/app/(console)/connect/[org]/[repo]/commit/[sha]/page';

vi.mock('next/navigation', () => ({ useParams: () => ({ org: 'test-team', repo: 'test-app', sha: 'c'.repeat(40) }) }));
vi.mock('@/lib/real-repo', () => ({ useAccessibleRepo: () => ({ status: 'ready', data: { connected_repo_id: 'repo_test', full_name: 'test-team/test-app', default_branch: 'main', private: false } }) }));
vi.mock('@/lib/api', () => ({ api: () => ({ repositoryCommits: async () => ({ items: [{ sha: 'c'.repeat(40), message: 'Selected change', author: 'test-author', committed_at: null, html_url: 'https://git.example/team/app/commit/selected' }], available: true, reason: null }) }) }));
vi.mock('@/components/demo/commit-analysis', () => ({ CommitAnalysis: () => null }));
vi.mock('@/components/project-review-cta', () => ({ ProjectReviewCta: ({ repoId }: { repoId: string | null }) => repoId ? <span data-testid="review-cta-repo">{repoId}</span> : null }));
vi.mock('@/components/real-assessment-panel', () => ({ RealAssessmentPanel: () => null }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('starts the quick read on arrival and asks the analyze route for this commit', async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ error: { message: 'not configured' } }), { status: 503 }));
  vi.stubGlobal('fetch', fetchMock);
  render(<RealCommitPage />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/analyze', expect.objectContaining({ method: 'POST', body: JSON.stringify({ repo: 'test-team/test-app', sha: 'c'.repeat(40) }) })));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('no model API key configured'));
});

it('uses the configured provider commit URL rather than assuming GitHub.com', async () => {
  render(<RealCommitPage />);
  expect(screen.getByTestId('review-cta-repo').textContent).toBe('repo_test');
  expect((await screen.findByRole('link', { name: /View on GitHub/ })).getAttribute('href')).toBe('https://git.example/team/app/commit/selected');
});
