'use client';

import type { Assessment, Page } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useState } from 'react';
import { Badge, Button, Empty, ErrorState, Mono, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { shortSha } from '@/lib/format';
import { ASSESSMENT_STATE } from '@/lib/status';

interface OlderChecks { anchor: string; repoId: string; items: Assessment[]; next: string | null }

export function AssessmentList({ projectId, repoId, page }: { projectId: string; repoId: string; page: Page<Assessment> }) {
  const [older, setOlder] = useState<OlderChecks | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const scopedPage = page.items.filter(assessment => assessment.repo_id === repoId);
  const validOlder = older !== null && older.anchor === page.next_cursor && older.repoId === repoId ? older : null;
  const seen = new Set(scopedPage.map(assessment => assessment.id));
  const items = [...scopedPage, ...(validOlder?.items ?? []).filter(assessment => !seen.has(assessment.id))];
  const nextCursor = validOlder ? validOlder.next : page.next_cursor;
  const loadOlder = async () => {
    if (nextCursor === null || page.next_cursor === null) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api().assessments(projectId, { cursor: nextCursor });
      setOlder(prev => {
        const base = prev !== null && prev.anchor === page.next_cursor && prev.repoId === repoId ? prev.items : [];
        const have = new Set([...scopedPage, ...base].map(assessment => assessment.id));
        return {
          anchor: page.next_cursor as string,
          repoId,
          items: [...base, ...result.items.filter(assessment => assessment.repo_id === repoId && !have.has(assessment.id))],
          next: result.next_cursor,
        };
      });
    } catch (err) { setError(err); } finally { setBusy(false); }
  };
  if (items.length === 0 && nextCursor === null) return <Empty>No assessments for this repository yet. Approve a scope, then start a check above.</Empty>;
  return (
    <div className="min-w-0">
      {items.length === 0 ? <Empty>No assessments for this repository on the latest page.</Empty> : (
        <>
          <div className="hidden min-w-0 overflow-x-auto sm:block" data-testid="assessment-history-table-scroll">
            <table className="w-full min-w-[520px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wider text-faint">
                <tr><th className="py-1.5 pr-3">Run</th><th className="pr-4">Status</th><th className="pr-3">Commit</th><th>Started</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {items.map(assessment => (
                  <tr key={assessment.id}>
                    <td className="py-2 pr-3"><Link className="text-sm text-accent hover:underline" href={`/projects/${projectId}/assessments/${assessment.id}?repo=${encodeURIComponent(repoId)}`} aria-label={`Open run & replay ${assessment.id}`} title={assessment.id}>Open run & replay</Link></td>
                    <td className="whitespace-nowrap pr-4"><Badge view={ASSESSMENT_STATE[assessment.state]} /></td>
                    <td className="pr-3"><Mono>{shortSha(assessment.commit_sha)}</Mono></td>
                    <td className="text-muted"><Time iso={assessment.created_at} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex min-w-0 flex-col gap-2 sm:hidden" data-testid="assessment-history-mobile-list">
            {items.map(assessment => (
              <article key={assessment.id} className="min-w-0 rounded-md border border-line bg-panel-2/40 p-3" data-testid="assessment-history-mobile-row">
                <Link className="block break-words text-sm text-accent hover:underline" href={`/projects/${projectId}/assessments/${assessment.id}?repo=${encodeURIComponent(repoId)}`} aria-label={`Open run & replay ${assessment.id}`} title={assessment.id}>Open run & replay</Link>
                <div className="mt-2 flex flex-wrap gap-2">
                  <span data-testid="assessment-mobile-status"><Badge view={ASSESSMENT_STATE[assessment.state]} /></span>
                  <Mono>{shortSha(assessment.commit_sha)}</Mono>
                  <span className="text-xs text-muted"><Time iso={assessment.created_at} /></span>
                </div>
              </article>
            ))}
          </div>
        </>
      )}
      {nextCursor !== null && (
        <div className="mt-3 flex flex-wrap items-center gap-3" data-testid="assessments-truncated">
          <p className="text-xs text-muted">Showing the newest {items.length} checks. Older checks exist.</p>
          <Button type="button" disabled={busy} onClick={() => void loadOlder()} data-testid="load-older-assessments">{busy ? 'Loading…' : 'Load older checks'}</Button>
        </div>
      )}
      {error !== null && <div className="mt-2"><ErrorState error={error} onRetry={() => void loadOlder()} context="Older checks" /></div>}
    </div>
  );
}
