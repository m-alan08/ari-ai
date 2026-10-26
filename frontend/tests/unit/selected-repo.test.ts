import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Repository } from '@hackatl/frontend-transport';

const navigation = vi.hoisted(() => ({
  path: '/projects/project-1/commits',
  params: new URLSearchParams('repo=repo-1&commit=foreign-sha&view=agents'),
  replace: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  usePathname: () => navigation.path,
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => navigation.params,
}));
import { resolveSelectedRepo, useSelectedRepo } from '@/lib/selected-repo';

const repos = [
  { id: 'repo-1', full_name: 'acme/payments', default_branch: 'main' },
  { id: 'repo-2', full_name: 'acme/store', default_branch: 'trunk' },
] as Repository[];

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  navigation.replace.mockReset();
});

describe('resolveSelectedRepo', () => {
  it('prefers a valid URL repository over stored selection', () => {
    expect(resolveSelectedRepo(repos, 'repo-2', 'repo-1')).toBe(repos[1]);
  });

  it('falls through invalid ids to the first available repository', () => {
    expect(resolveSelectedRepo(repos, 'unknown', 'also-unknown')).toBe(repos[0]);
  });

  it('uses the stored selection when no valid URL id exists', () => {
    expect(resolveSelectedRepo(repos, null, 'repo-2')).toBe(repos[1]);
    expect(resolveSelectedRepo(repos, 'unknown', 'repo-2')).toBe(repos[1]);
  });

  it('returns null when the project has no repositories', () => {
    expect(resolveSelectedRepo([], null, null)).toBeNull();
  });

  it('removes a stale commit from the URL when selecting another repository', () => {
    const { result } = renderHook(() => useSelectedRepo('project-1', repos));
    act(() => result.current.selectRepo('repo-2'));
    expect(navigation.replace).toHaveBeenCalledWith(
      '/projects/project-1/commits?repo=repo-2&view=agents',
      { scroll: false },
    );
  });
});
