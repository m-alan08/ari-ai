'use client';

import type { Assessment } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { Badge } from '@/components/ui';
import { api } from '@/lib/api';
import { projectForRepository } from '@/lib/github-connect';
import { ASSESSMENT_STATE } from '@/lib/status';

type Lookup =
  | { status: 'loading' | 'error'; repoId: string }
  | { status: 'ready'; repoId: string; projectId: string; items: Assessment[]; truncated: boolean };

const LookupContext = createContext<Lookup | null>(null);

export function CommitAssessmentProvider({ repoId, children }: { repoId: string; children: ReactNode }) {
  const [lookup, setLookup] = useState<Lookup>({ status: 'loading', repoId });
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void (async () => {
      const projectId = await projectForRepository(repoId, controller.signal);
      const items: Assessment[] = [];
      let cursor: string | undefined;
      let pages = 0;
      do {
        const page = await api().assessments(projectId, { cursor, signal: controller.signal });
        items.push(...page.items);
        cursor = page.next_cursor ?? undefined;
        pages += 1;
      } while (cursor && pages < 3);
      if (active) setLookup({ status: 'ready', repoId, projectId, items, truncated: cursor !== undefined });
    })().catch(() => {
      if (active) setLookup({ status: 'error', repoId });
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [repoId]);

  const currentLookup = lookup.repoId === repoId ? lookup : { status: 'loading' as const, repoId };
  return <LookupContext.Provider value={currentLookup}>{children}</LookupContext.Provider>;
}

export function CommitAssessmentChip({ repoId, sha }: { repoId: string; sha: string }) {
  const lookup = useContext(LookupContext);
  if (!lookup || lookup.status !== 'ready' || lookup.truncated) return null;
  const matches = lookup.items
    .filter(assessment => assessment.repo_id === repoId && assessment.commit_sha === sha)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const newest = matches[0];
  if (!newest) return <span data-testid="not-reviewed" className="font-mono text-[10px] uppercase tracking-[0.06em] text-faint">NOT REVIEWED</span>;
  const status = ASSESSMENT_STATE[newest.state];
  if (!status) return null;
  const additionalCount = matches.length - 1;
  const additionalDescription = `${additionalCount} more assessment${additionalCount === 1 ? '' : 's'} of this commit`;
  return (
    <>
      <Link
        href={`/projects/${lookup.projectId}/assessments/${newest.id}?repo=${encodeURIComponent(repoId)}`}
        aria-label={`${status.label} assessment`}
        data-testid="commit-assessment-status"
        className="inline-flex"
      >
        <Badge view={status} />
      </Link>
      {additionalCount > 0 && (
        <span
          className="font-mono text-[10px] text-muted"
          data-testid="other-assessments"
          title={additionalDescription}
          aria-label={additionalDescription}
        >
          +{additionalCount}
        </span>
      )}
    </>
  );
}
