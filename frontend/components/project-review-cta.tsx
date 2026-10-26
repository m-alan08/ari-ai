'use client';

import Link from 'next/link';
import { api } from '@/lib/api';
import { projectForRepository } from '@/lib/github-connect';
import { scopeStateFor } from '@/lib/real-assessment';
import { useResource } from '@/lib/use-resource';

export function ProjectReviewCta({ repoId }: { repoId: string | null }) {
  const project = useResource(
    repoId ? `review-project:${repoId}` : null,
    signal => projectForRepository(repoId!, signal),
  );
  const projectId = project.status === 'ready' ? project.data : null;
  const scopes = useResource(
    projectId ? `review-scopes:${projectId}` : null,
    signal => api().scopes(projectId!, { signal }),
  );

  if (!repoId || !projectId || scopes.status !== 'ready') return null;

  const activeScope = scopeStateFor(scopes.data.items, repoId, new Date()).kind === 'active';
  const href = activeScope ? `/projects/${projectId}/commits#start-assessment` : `/projects/${projectId}/setup?repo=${encodeURIComponent(repoId)}`;
  const label = activeScope ? 'Run a review' : 'Set up & run your first review';

  return (
    <Link
      href={href}
      className="inline-flex rounded-lg bg-brand px-4 py-2 text-sm font-medium text-on-brand transition-colors hover:bg-brand-deep"
      data-testid="project-review-cta"
    >
      {label}
    </Link>
  );
}
