'use client';

import type { Repository } from '@hackatl/frontend-transport';
import { useCallback, useSyncExternalStore } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

export function resolveSelectedRepo(
  repos: readonly Repository[],
  param: string | null,
  stored: string | null,
): Repository | null {
  return repos.find(repo => repo.id === param)
    ?? repos.find(repo => repo.id === stored)
    ?? repos[0]
    ?? null;
}

export function useSelectedRepo(projectId: string, repos: readonly Repository[]) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const storageKey = `ari:repo:${projectId}`;
  const param = searchParams.get('repo');
  const subscribe = useCallback((onStoreChange: () => void) => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null) onStoreChange();
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [storageKey]);
  const getStored = useCallback(() => {
    try {
      return window.localStorage.getItem(storageKey);
    } catch {
      return null;
    }
  }, [storageKey]);
  const stored = useSyncExternalStore(subscribe, getStored, () => null);

  const selectRepo = useCallback((id: string) => {
    if (!repos.some(repo => repo.id === id)) return;
    try {
      window.localStorage.setItem(storageKey, id);
    } catch {
      // The URL remains the source of truth when storage is unavailable.
    }
    const params = new URLSearchParams(searchParams.toString());
    params.set('repo', id);
    params.delete('commit');
    const query = params.toString();
    const hash = typeof window === 'undefined' ? '' : window.location.hash;
    router.replace(`${pathname}${query ? `?${query}` : ''}${hash}`, { scroll: false });
  }, [pathname, repos, router, searchParams, storageKey]);

  return {
    selectedRepo: resolveSelectedRepo(repos, param, stored),
    selectRepo,
  };
}
