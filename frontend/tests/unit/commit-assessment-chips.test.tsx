import type { Assessment } from '@hackatl/frontend-transport';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ assessments: vi.fn(), projectForRepository: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: () => ({ assessments: mocks.assessments }) }));
vi.mock('@/lib/github-connect', () => ({ projectForRepository: mocks.projectForRepository }));

import { CommitAssessmentChip, CommitAssessmentProvider } from '@/components/commit-assessment-chips';

afterEach(() => { cleanup(); vi.resetAllMocks(); });

const assessment = (id: string, repoId: string, sha: string, createdAt: string, state: Assessment['state']): Assessment =>
  ({ id, repo_id: repoId, commit_sha: sha, created_at: createdAt, state }) as Assessment;

function renderChip() {
  return render(
    <CommitAssessmentProvider repoId="repo-1">
      <ol><li><span>Commit message stays visible</span><CommitAssessmentChip repoId="repo-1" sha="sha-1" /></li></ol>
    </CommitAssessmentProvider>,
  );
}

describe('commit assessment chips', () => {
  it('links the newest matching assessment and counts other runs', async () => {
    mocks.projectForRepository.mockResolvedValue('project-1');
    mocks.assessments.mockResolvedValue({
      items: [
        assessment('old', 'repo-1', 'sha-1', '2026-01-01T00:00:00Z', 'AWAITING_APPROVAL'),
        assessment('new', 'repo-1', 'sha-1', '2026-02-01T00:00:00Z', 'COMPLETED'),
        assessment('other-repo', 'repo-2', 'sha-1', '2026-03-01T00:00:00Z', 'FAILED'),
        assessment('other-sha', 'repo-1', 'sha-2', '2026-04-01T00:00:00Z', 'FAILED'),
      ],
      next_cursor: null,
    });
    renderChip();
    const link = await screen.findByTestId('commit-assessment-status');
    expect(link.getAttribute('href')).toBe('/projects/project-1/assessments/new?repo=repo-1');
    expect(link.textContent).toContain('Completed');
    expect(screen.getByTestId('other-assessments').textContent).toBe('+1');
    expect(screen.getByTestId('other-assessments').getAttribute('title')).toBe('1 more assessment of this commit');
    expect(screen.getByTestId('other-assessments').getAttribute('aria-label')).toBe('1 more assessment of this commit');
  });

  it('pluralizes the accessible label for multiple additional assessments', async () => {
    mocks.projectForRepository.mockResolvedValue('project-1');
    mocks.assessments.mockResolvedValue({
      items: [
        assessment('newest', 'repo-1', 'sha-1', '2026-03-01T00:00:00Z', 'COMPLETED'),
        assessment('middle', 'repo-1', 'sha-1', '2026-02-01T00:00:00Z', 'FAILED'),
        assessment('old', 'repo-1', 'sha-1', '2026-01-01T00:00:00Z', 'AWAITING_APPROVAL'),
      ],
      next_cursor: null,
    });
    renderChip();
    const count = await screen.findByTestId('other-assessments');
    expect(count.textContent).toBe('+2');
    expect(count.getAttribute('title')).toBe('2 more assessments of this commit');
    expect(count.getAttribute('aria-label')).toBe('2 more assessments of this commit');
  });

  it('omits a matching assessment chip when its state is unknown', async () => {
    mocks.projectForRepository.mockResolvedValue('project-1');
    mocks.assessments.mockResolvedValue({
      items: [assessment('unknown', 'repo-1', 'sha-1', '2026-02-01T00:00:00Z', 'UNKNOWN' as Assessment['state'])],
      next_cursor: null,
    });
    renderChip();
    await waitFor(() => expect(mocks.assessments).toHaveBeenCalled());
    expect(screen.queryByTestId('commit-assessment-status')).toBeNull();
    expect(screen.queryByTestId('not-reviewed')).toBeNull();
  });

  it('shows NOT REVIEWED only after the complete assessment list is read', async () => {
    mocks.projectForRepository.mockResolvedValue('project-1');
    mocks.assessments.mockResolvedValue({ items: [], next_cursor: null });
    renderChip();
    expect((await screen.findByTestId('not-reviewed')).textContent).toBe('NOT REVIEWED');

    cleanup();
    mocks.assessments.mockClear();
    mocks.assessments
      .mockResolvedValueOnce({ items: [], next_cursor: 'cursor-1' })
      .mockResolvedValueOnce({ items: [], next_cursor: 'cursor-2' })
      .mockResolvedValueOnce({ items: [], next_cursor: 'cursor-3' });
    renderChip();
    await waitFor(() => expect(mocks.assessments).toHaveBeenCalledTimes(3));
    expect(screen.queryByTestId('not-reviewed')).toBeNull();
    expect(screen.queryByTestId('commit-assessment-status')).toBeNull();
  });

  it('keeps the commit list visible when project lookup fails', async () => {
    mocks.projectForRepository.mockRejectedValue(new Error('offline'));
    renderChip();
    expect(screen.getByText('Commit message stays visible')).toBeTruthy();
    await waitFor(() => expect(mocks.projectForRepository).toHaveBeenCalledWith('repo-1', expect.any(AbortSignal)));
    expect(screen.queryByTestId('not-reviewed')).toBeNull();
    expect(screen.queryByTestId('commit-assessment-status')).toBeNull();
  });
});
