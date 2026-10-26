'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import { CommitRunWorkspace, type LiveCommitView } from '@/components/commit-run-workspace';
import { ProjectReviewCta } from '@/components/project-review-cta';
import { RealAssessmentPanel } from '@/components/real-assessment-panel';
import { Avatar, VerdictChip } from '@/components/demo/ui';
import { Empty, ErrorState, Loading } from '@/components/ui';
import type { DemoCommit, DemoRepo } from '@/lib/demo/data';
import type { LiveResult } from '@/lib/live-analysis';
import { relativeTime } from '@/lib/format';
import { minutesSince } from '@/lib/simulated-attack';
import { useAccessibleRepo } from '@/lib/real-repo';
import { useResource } from '@/lib/use-resource';
import { api } from '@/lib/api';
import { commitFreshness } from '@/lib/commit-freshness';

type QuickRead = { key: string; data?: LiveResult; error?: string };

export default function RealCommitPage() {
  const params = useParams<{ org: string; repo: string; sha: string }>();
  const org = decodeURIComponent(params.org);
  const name = decodeURIComponent(params.repo);
  const repo = useAccessibleRepo(org, name);
  if (repo.status === 'loading') return <Loading label="Loading the repository" />;
  if (repo.status === 'error') return <ErrorState error={repo.error} onRetry={repo.reload} />;
  if (!repo.data) return <Empty>{org}/{name} is not reachable through your GitHub App installation.</Empty>;
  return <SelectedCommit key={`${repo.data.full_name}:${params.sha}`} org={org} name={name} sha={params.sha} repo={repo.data} onConnected={repo.reload} />;
}

function SelectedCommit({ org, name, sha, repo, onConnected }: {
  org: string; name: string; sha: string; repo: NonNullable<ReturnType<typeof useAccessibleRepo>['data']>; onConnected: () => void;
}) {
  const [connectedRepoId, setConnectedRepoId] = useState(repo.connected_repo_id);
  const metadata = useResource(connectedRepoId ? `selected-commit:${connectedRepoId}:${sha}` : null, signal => api().repositoryCommits(connectedRepoId!, { signal }));
  const selectedCommit = metadata.data?.items.find(commit => commit.sha === sha || commit.sha.startsWith(sha));
  const fullSha = selectedCommit?.sha ?? sha;
  const [assessmentId, setAssessmentId] = useState<string | null>(null);
  // The quick read starts on arrival: it is the page's code review, not an opt-in extra.
  const [request, setRequest] = useState(1);
  const [quick, setQuick] = useState<QuickRead | null>(null);
  const identity = `${repo.full_name}:${fullSha}:${request}`;
  useEffect(() => {
    if (!request) return;
    const ctrl = new AbortController();
    fetch('/api/analyze', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ repo: repo.full_name, sha: fullSha }), signal: ctrl.signal })
      .then(async res => {
        const body = await res.json().catch(() => null);
        if (!res.ok) throw new Error(body?.error?.message ?? `Quick read failed (${res.status}).`);
        // fullSha may be a short prefix from the URL before the commit list resolves it.
        if (!body.commit.sha.startsWith(fullSha)) throw new Error('The response does not match this commit.');
        if (!ctrl.signal.aborted) setQuick({ key: identity, data: body as LiveResult });
      }).catch((err: unknown) => { if (!ctrl.signal.aborted) setQuick({ key: identity, error: err instanceof Error ? err.message : 'Quick read failed.' }); });
    return () => ctrl.abort();
  }, [request, repo.full_name, fullSha, identity]);
  const live = quick?.key === identity ? quick : null;
  const view = useMemo<LiveCommitView | null>(() => {
    if (!live?.data) return null;
    const data = live.data;
    const commit: DemoCommit = { sha: fullSha, message: data.commit.message, body: data.commit.body, author: data.commit.author, minutesAgo: minutesSince(data.commit.committedAt), branch: repo.default_branch, ci: 'pending', verdict: data.verdict, scenario: 'clean', files: data.commit.files, additions: data.commit.additions, deletions: data.commit.deletions };
    const demoRepo: DemoRepo = { org, name, description: '', language: '', visibility: repo.private ? 'private' : 'public', defaultBranch: repo.default_branch, stars: 0, components: data.components, links: data.links, commits: [commit] };
    return { commit, repo: demoRepo, analysis: data.analysis, live: { model: data.model, truncated: data.truncated, htmlUrl: data.commit.htmlUrl } };
  }, [live, fullSha, repo.default_branch, repo.private, org, name]);
  const liveState = live?.error
    ? { status: 'error' as const, error: live.error, retry: () => setRequest(n => n + 1) }
    : view ? { status: 'ready' as const, retry: () => setRequest(n => n + 1) }
      : { status: 'loading' as const, retry: () => setRequest(n => n + 1) };
  const data = live?.data;
  const htmlUrl = data?.commit.htmlUrl ?? selectedCommit?.html_url;
  const title = data?.commit.message ?? selectedCommit?.message ?? `Commit ${fullSha.slice(0, 7)}`;
  const author = data?.commit.author ?? selectedCommit?.author;
  const when = data?.commit.committedAt ?? selectedCommit?.committed_at;
  const base = `/connect/${encodeURIComponent(org)}/${encodeURIComponent(name)}`;
  const freshness = commitFreshness(metadata.data?.available ? metadata.data.items : undefined, fullSha);
  return <div className="commit-stage" data-testid="real-commit">
    <nav aria-label="Breadcrumb" className="mb-6 flex flex-wrap items-center gap-2 text-sm text-white/65">
      <Link href="/connect">Organizations</Link><span aria-hidden="true">/</span><Link href={`/connect/${encodeURIComponent(org)}`}>{org}</Link><span aria-hidden="true">/</span><Link href={base}>{name}</Link><span aria-hidden="true">/</span><span>{fullSha.slice(0, 7)}</span>
    </nav>
    <header className="mb-6 border-b border-line pb-6">
      <p className="label-mono text-muted">{repo.full_name} / {fullSha.slice(0, 7)}</p>
      <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
        <h1 className="max-w-[22ch] text-3xl font-semibold">{title}</h1>
        {data && <VerdictChip verdict={data.verdict} />}
      </div>
      <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-white/60">
        {author && <span className="inline-flex items-center gap-2"><Avatar login={author} size={20} />{author}</span>}
        {when && <span>{relativeTime(when)}</span>}
        {data && <span className="demo-chip demo-chip-ok">+{data.commit.additions}</span>}
        {data && <span className="demo-chip demo-chip-bad">−{data.commit.deletions}</span>}
        {data && <span>{data.commit.files} file{data.commit.files === 1 ? '' : 's'}</span>}
        {htmlUrl && /^https?:\/\//.test(htmlUrl) && <a className="underline" href={htmlUrl} target="_blank" rel="noreferrer">View on GitHub ↗</a>}
      </p>
    </header>
    {freshness?.kind === 'stale' && (
      <p className="mb-4 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm text-warn" data-testid="commit-behind-default">
        This isn&apos;t the latest commit on the default branch. Ari reviews and fixes this exact commit, so a fix may already be there.
        {' '}<Link className="underline" href={`${base}/commit/${freshness.latestSha}`}>Review the latest commit →</Link>
      </p>
    )}
    <div className="mb-4"><ProjectReviewCta repoId={connectedRepoId} /></div>
    <RealAssessmentPanel org={org} repo={{ ...repo, connected_repo_id: connectedRepoId }} sha={fullSha} onConnected={id => { setConnectedRepoId(id); onConnected(); }} onRunSelected={setAssessmentId} />
    <CommitRunWorkspace assessmentId={assessmentId} repoId={connectedRepoId} sha={fullSha} fullName={repo.full_name} liveView={view} liveState={liveState} />
  </div>;
}
