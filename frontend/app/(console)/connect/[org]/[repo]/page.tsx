'use client';

import type { AccessibleRepository } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState } from 'react';
import { RepositoryAutomation } from '@/components/repository-automation';
import { CommitAssessmentChip, CommitAssessmentProvider } from '@/components/commit-assessment-chips';
import { Avatar } from '@/components/demo/ui';
import { ProjectReviewCta } from '@/components/project-review-cta';
import { Button, Empty, ErrorState, Loading } from '@/components/ui';
import { api } from '@/lib/api';
import { relativeTime } from '@/lib/demo/data';
import { connectRepo } from '@/lib/github-connect';
import { useAccessibleRepo } from '@/lib/real-repo';
import { minutesSince } from '@/lib/simulated-attack';
import { useResource } from '@/lib/use-resource';

/** A connected repository's most recent commits, read live from GitHub. */
export default function RepoCommitsPage() {
  const params = useParams<{ org: string; repo: string }>();
  const org = decodeURIComponent(params.org);
  const name = decodeURIComponent(params.repo);
  const repo = useAccessibleRepo(org, name);
  return (
    <div className="mx-auto min-w-0 max-w-5xl px-4 py-8" data-testid="real-commits">
      <nav aria-label="Breadcrumb" className="mb-5 text-sm text-muted">
        <Link href="/connect" className="hover:text-ink">Organizations</Link><span aria-hidden="true" className="mx-2">/</span>
        <Link href={`/connect/${encodeURIComponent(org)}`} className="hover:text-ink">{org}</Link><span aria-hidden="true" className="mx-2">/</span>
        <span className="text-ink">{name}</span>
      </nav>
      {repo.status === 'loading' && <Loading label="Loading the repository" />}
      {repo.status === 'error' && <ErrorState error={repo.error} onRetry={repo.reload} context="Could not load your GitHub repositories" />}
      {repo.status === 'ready' && !repo.data && <Empty>{org}/{name} is not available to you through the Ari GitHub App.</Empty>}
      {repo.status === 'ready' && repo.data && <Commits key={repo.data.full_name} org={org} name={name} repo={repo.data} onConnected={repo.reload} />}
    </div>
  );
}

function Commits({ org, name, repo, onConnected }: { org: string; name: string; repo: AccessibleRepository; onConnected: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const id = repo.connected_repo_id;
  const commits = useResource(id ? `commits:${id}` : null, signal => api().repositoryCommits(id!, { signal }));
  const connect = async () => {
    setBusy(true);
    setError(null);
    try { await connectRepo(org, repo); onConnected(); } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <>
      <header className="mb-6 border-b border-line pb-6">
        <p className="label-mono text-muted">Commit history / 03</p>
        <h1 className="text-3xl font-semibold tracking-tight">{name}</h1>
        <p className="mt-1 text-sm text-muted">Commits on {repo.default_branch}</p>
      </header>
      {id && <div className="mb-4"><ProjectReviewCta repoId={id} /></div>}
      {!id && (
        <div className="rounded-2xl border border-line bg-panel p-6">
          <p className="text-sm text-muted">Connect this repository to read its commits.</p>
          <Button variant="primary" className="mt-3" onClick={() => void connect()} disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</Button>
          {error !== null && <div className="mt-3"><ErrorState error={error} context="Could not connect" /></div>}
        </div>
      )}
      {id && <RepositoryAutomation repoId={id} />}
      {id && commits.status === 'loading' && <Loading label="Reading commits from GitHub" />}
      {id && commits.status === 'error' && <ErrorState error={commits.error} onRetry={commits.reload} context="Could not read commits from GitHub" />}
      {id && commits.status === 'ready' && (commits.data.items.length === 0
        ? <Empty>{commits.data.available ? `No commits on ${repo.default_branch} yet.` : commits.data.reason}</Empty>
        : (
          <CommitAssessmentProvider repoId={id}>
            <ol className="overflow-hidden rounded-2xl border border-line bg-panel" data-testid="real-commit-list">
              {commits.data.items.map((c, i) => (
                <li key={c.sha} className="demo-rise border-b border-line last:border-b-0" style={{ ['--i' as string]: i }}>
                  <div className="group grid gap-3 px-5 py-4 transition-colors hover:bg-panel-2 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                    <Link href={`/connect/${encodeURIComponent(org)}/${encodeURIComponent(name)}/commit/${c.sha}`} className="flex min-w-0 gap-3" data-testid="real-commit-row">
                      <Avatar login={c.author} size={30} />
                      <span className="min-w-0">
                        <span className="block truncate font-medium group-hover:text-brand">{c.message}</span>
                        <span className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted">
                          <span className="font-semibold text-ink">{c.author}</span>
                          {c.committed_at && <span suppressHydrationWarning>committed {relativeTime(minutesSince(c.committed_at))}</span>}
                        </span>
                      </span>
                    </Link>
                    <span className="flex flex-wrap items-center gap-3 pl-[42px] md:pl-0">
                      <CommitAssessmentChip repoId={id} sha={c.sha} />
                      <Link
                        href={`/connect/${encodeURIComponent(org)}/${encodeURIComponent(name)}/commit/${c.sha}`}
                        className="rounded-md border border-line px-2 py-1 font-mono text-xs text-muted group-hover:border-brand/40"
                      >
                        {c.sha.slice(0, 7)} <span aria-hidden="true">→</span>
                      </Link>
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          </CommitAssessmentProvider>
        ))}
    </>
  );
}
