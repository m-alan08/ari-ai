'use client';

import type { AccessibleRepository } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useState } from 'react';
import { Avatar } from '@/components/demo/ui';
import { ErrorState, Loading, Tag } from '@/components/ui';
import { api } from '@/lib/api';
import { connectRepo } from '@/lib/github-connect';
import { groupByOrg, repoName } from '@/lib/github-orgs';
import { useResource } from '@/lib/use-resource';

/** Second step: choose a repository in the organization. */
export default function ConnectReposPage() {
  const { org: raw } = useParams<{ org: string }>();
  const login = decodeURIComponent(raw);
  const router = useRouter();
  const list = useResource('github-repos', signal => api().githubRepositories({ signal }));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const open = async (repo: AccessibleRepository) => {
    setBusy(repo.full_name);
    setError(null);
    try {
      await connectRepo(login, repo);
      router.push(`/connect/${encodeURIComponent(login)}/${encodeURIComponent(repoName(repo.full_name))}`);
    } catch (err) { setError(err); setBusy(null); }
  };


  const org = list.status === 'ready' ? groupByOrg(list.data.items).find(o => o.login.toLowerCase() === login.toLowerCase()) : undefined;

  return (
    <div className="workspace-index mx-auto max-w-5xl px-4 py-8" data-testid="connect-repos">
      <nav aria-label="Breadcrumb" className="mb-5 text-sm text-muted">
        <Link href="/connect" className="hover:text-ink">Organizations</Link><span aria-hidden="true" className="mx-2">/</span><span className="text-ink">{login}</span>
      </nav>
      <header className="workspace-heading mb-6">
        <p className="workspace-eyebrow">Repositories / 02</p>
        <div className="flex flex-wrap items-center gap-4">
        <Avatar login={login} size={48} />
        <h1 className="text-3xl font-semibold tracking-tight">{login}</h1></div>
        <p className="workspace-description">Select a repository to review its changes.</p>
      </header>
      {list.status === 'loading' && <Loading label="Loading repositories" />}
      {list.status === 'error' && <ErrorState error={list.error} onRetry={list.reload} context="Could not list your GitHub repositories" />}
      {list.status === 'ready' && !org && (
        <p className="text-sm text-muted" data-testid="connect-org-missing">No repositories from {login} are available to you. <Link className="text-brand hover:underline" href="/connect">Choose another organization</Link>.</p>
      )}
      {org && (
        <ul className="workspace-rows flex flex-col divide-y divide-line overflow-hidden rounded-2xl border border-line bg-panel">
          {org.repos.map(repo => (
            <li key={repo.full_name} data-testid={`connect-repo-${repo.full_name}`}>
              <button type="button" className="group flex w-full flex-wrap items-center justify-between gap-3 px-5 py-4 text-left transition-colors hover:bg-panel-2 disabled:opacity-60" disabled={busy !== null} onClick={() => void open(repo)}>
                <span className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="truncate font-semibold group-hover:text-brand">{repoName(repo.full_name)}</span>
                  {repo.private && <Tag>private</Tag>}
                  {repo.connected_repo_id && <span className="flex items-center gap-1.5 text-xs text-ok"><span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />Connected</span>}
                </span>
                <span className="flex items-center gap-2 text-sm text-muted">
                  {busy === repo.full_name ? 'Opening…' : 'Open'}
                  <span aria-hidden="true" className="transition-transform group-hover:translate-x-0.5">→</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {error !== null && <div className="mt-3"><ErrorState error={error} context="Could not connect the repository" /></div>}
    </div>
  );
}
