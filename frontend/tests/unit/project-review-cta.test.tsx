import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ scopes: [] as unknown[] }));

vi.mock('@/lib/use-resource', () => ({
  useResource: (key: string | null) => {
    if (key?.startsWith('review-project:')) return { status: 'ready', data: 'project-1', error: undefined, reload: vi.fn() };
    if (key?.startsWith('review-scopes:')) return { status: 'ready', data: { items: state.scopes }, error: undefined, reload: vi.fn() };
    return { status: 'loading', data: undefined, error: undefined, reload: vi.fn() };
  },
}));

import { ProjectReviewCta } from '@/components/project-review-cta';

afterEach(() => {
  cleanup();
  state.scopes = [];
});

it('links to scope authorization when the repository has no active scope', () => {
  state.scopes = [{
    status: 'pending',
    targets: [{ repo_id: 'repo-1' }],
    expires_at: '2999-01-01T00:00:00Z',
  }];

  render(<ProjectReviewCta repoId="repo-1" />);

  const cta = screen.getByRole('link', { name: 'Authorize & run your first review' });
  expect(cta.getAttribute('href')).toBe('/projects/project-1/scopes');
});

it('links to Start an assessment when the repository has an active scope', () => {
  state.scopes = [{
    status: 'active',
    targets: [{ repo_id: 'repo-1' }],
    expires_at: '2999-01-01T00:00:00Z',
  }];

  render(<ProjectReviewCta repoId="repo-1" />);

  const cta = screen.getByRole('link', { name: 'Run a review' });
  expect(cta.getAttribute('href')).toBe('/projects/project-1#start-assessment');
});
