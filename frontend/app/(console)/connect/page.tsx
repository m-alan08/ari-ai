'use client';

import Link from 'next/link';
import { Avatar, GitHubMark } from '@/components/demo/ui';
import { BRAND_NAME } from '@/components/brand';
import { ErrorState, Loading, Panel } from '@/components/ui';
import { RefreshFromGitHub } from '@/components/refresh-from-github';
import { SetupLayout } from '@/components/onboarding/stepper';
import { api } from '@/lib/api';
import { groupByOrg } from '@/lib/github-orgs';
import { useResource } from '@/lib/use-resource';

/** After GitHub sign-in: choose the organization (App installation account) to work in. */
export default function ConnectOrgsPage() {
  const list = useResource('github-repos', signal => api().githubRepositories({ signal }));

  return (
    <SetupLayout current={1}>
      <div className="mx-auto min-w-0 max-w-6xl" data-testid="connect-orgs">
        <header className="mb-6 border-b border-line pb-6">
          <p className="label-mono text-muted">Your workspace / 01</p>
          <h1 className="text-3xl font-semibold tracking-tight">Organizations</h1>
          <div className="mt-4 flex flex-wrap items-baseline justify-between gap-3 text-sm text-muted"><p>Choose an organization to see its repositories.</p><RefreshFromGitHub className="mt-1" /></div>
        </header>
        <Panel title="What Ari can access" className="mb-6">
          <dl className="grid gap-4 text-sm sm:grid-cols-2">
            <div><dt className="font-medium text-ink">Contents: read &amp; write</dt><dd className="mt-1 text-muted">Read code to review; write only to open the build-recipe PR and fix PRs you approve.</dd></div>
            <div><dt className="font-medium text-ink">Pull requests: read &amp; write</dt><dd className="mt-1 text-muted">Open those PRs and leave review comments if you turn comments on.</dd></div>
            <div><dt className="font-medium text-ink">Metadata: read-only</dt><dd className="mt-1 text-muted">Read repository names and default branches.</dd></div>
            <div><dt className="font-medium text-ink">Events: push, pull request, repository</dt><dd className="mt-1 text-muted">Used only for reviews you enable; automatic reviews are off by default.</dd></div>
            <div className="sm:col-span-2"><dt className="font-medium text-ink">Revoke or limit access</dt><dd className="mt-1 text-muted">GitHub → Settings → Applications → Installed GitHub Apps → Ari → Configure → Uninstall, or limit to selected repositories.</dd></div>
          </dl>
        </Panel>
        {list.status === 'loading' && <Loading label="Loading your GitHub organizations" />}
        {list.status === 'error' && <ErrorState error={list.error} onRetry={list.reload} context="Could not list your GitHub repositories" />}
        {list.status === 'ready' && (() => {
          const orgs = groupByOrg(list.data.items);
          const install = list.data.install_url;
          return (
            <>
              {orgs.length === 0 ? (
                <Panel title="No repositories yet" className="text-center">
                  <p data-testid="connect-empty" className="mx-auto max-w-md text-sm text-muted">Install the {BRAND_NAME} GitHub App on an organization or your account.</p>
                  {install !== null && <a href={install} className="mt-5 inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3.5 text-sm font-medium text-on-brand hover:bg-brand-deep"><GitHubMark />Install the GitHub App</a>}
                </Panel>
              ) : (
                <ul className="grid gap-3 md:grid-cols-3">
                  {orgs.map((org, i) => (
                    <li key={org.login}>
                      <Link href={`/connect/${encodeURIComponent(org.login)}`} className="group flex h-full min-w-0 flex-col gap-4 rounded-2xl border border-line bg-panel p-5 transition-colors hover:bg-panel-2" data-testid={`connect-org-${org.login}`}>
                        <span className="flex w-full items-center justify-between gap-4"><span className="label-mono text-muted">{String(i + 1).padStart(2, '0')} / ORGANIZATION</span><span aria-hidden="true" className="text-2xl text-muted transition-transform group-hover:translate-x-1">↗</span></span>
                        <span className="flex items-center gap-3">
                          <Avatar login={org.login} size={40} />
                          <span className="min-w-0 truncate font-semibold">{org.login}</span>
                        </span>
                        <span className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                          <span>{org.repos.length} {org.repos.length === 1 ? 'repository' : 'repositories'}</span>
                          {org.connected > 0 && <span className="text-info">{org.connected} connected</span>}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
              {orgs.length > 0 && install !== null && (
                <p className="mt-6 text-sm text-muted">Missing an organization or repository? <a className="text-brand underline-offset-2 hover:underline" href={install}>Add it to the GitHub App</a>.</p>
              )}
            </>
          );
        })()}
      </div>
    </SetupLayout>
  );
}
