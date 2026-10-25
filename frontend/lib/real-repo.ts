'use client';

import type { AccessibleRepository } from '@hackatl/frontend-transport';
import { api } from '@/lib/api';
import { useResource, type Resource } from '@/lib/use-resource';

/** The App-visible repository `org/name`, if the signed-in user can reach it. */
export function useAccessibleRepo(org: string, name: string): Resource<AccessibleRepository | null> {
  const full = `${org}/${name}`.toLowerCase();
  return useResource(`github-repo:${full}`, async signal => (await api().githubRepositories({ signal })).items.find(r => r.full_name.toLowerCase() === full) ?? null);
}
