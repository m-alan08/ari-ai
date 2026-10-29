'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useProject } from '@/components/project';
import { Term } from '@/components/term';
import { Async, Badge, Button, Empty, Mono, PageTitle, Panel, Tag, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { FINDING_GROUP_NOTE, findingGroup, findingGroupCounts, type FindingGroup } from '@/lib/findings';
import { shortSha } from '@/lib/format';
import { FINDING_STATUS, SEVERITY } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

const TABS = [
  ['open', 'Open'],
  ['hypothesis', 'Hypotheses (unvalidated)'],
  ['fixed', 'Fixed (independently verified)'],
  ['closed', 'Refuted / closed'],
] as const;

export default function FindingsPage() {
  const { project, selectedRepo } = useProject();
  const findings = useResource(selectedRepo ? `findings:${project.id}:${selectedRepo.id}` : null, s => api().findings(project.id, { signal: s, repoId: selectedRepo!.id }));
  const assessments = useResource(selectedRepo ? `findings-assessments:${project.id}:${selectedRepo.id}` : null, s => api().assessments(project.id, { signal: s, repoId: selectedRepo!.id }));
  const [tab, setTab] = useState<FindingGroup>('open');
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <PageTitle eyebrow="Review" title="Findings" subtitle={selectedRepo?.full_name ?? 'No repository connected'} />
      {!selectedRepo && <Empty>No repository connected. <Link className="text-brand hover:underline" href={`/projects/${project.id}/settings#connect-repo`}>Connect a repository</Link>.</Empty>}
      {selectedRepo && <Panel title="Findings">
      <p className="mb-1 text-sm text-muted">What these words mean: <Term t="finding">finding</Term>, <Term t="hypothesis">hypothesis</Term>, <Term t="validated">validated</Term>, <Term t="verification">verified</Term>.</p>
      <p className="mb-3 text-sm text-muted" data-testid="finding-group-note">{FINDING_GROUP_NOTE}</p>
      <div role="tablist" aria-label="Finding groups" className="mb-3 flex flex-wrap gap-1">
        {TABS.map(([t, label]) => (
          <button key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1.5 text-sm ${tab === t ? 'bg-panel-2 text-ink ring-1 ring-accent/50' : 'text-muted hover:text-ink'}`}>{label}</button>
        ))}
      </div>
      <Async resource={findings} label="findings">
        {page => {
          const repoItems = page.items;
          const counts = findingGroupCounts(repoItems);
          const items = repoItems.filter(f => findingGroup(f.status) === tab);
          if (repoItems.length === 0 && assessments.status === 'ready' && assessments.data.items.length === 0) {
            return (
              <div data-testid="findings-no-review">
                <Empty>Ari has not reviewed {selectedRepo.full_name} yet, so there are no findings. Findings appear here after the first review finishes.</Empty>
                <div className="mt-3 flex justify-center">
                  <Link className="inline-flex items-center rounded-lg bg-brand px-4 py-2 text-sm font-medium text-on-brand hover:bg-brand-deep" href={`/projects/${project.id}/commits?repo=${encodeURIComponent(selectedRepo.id)}#start-assessment`}>Run a review</Link>
                </div>
              </div>
            );
          }
          if (items.length === 0 && tab === 'open' && counts.open === 0 && counts.hypothesis + counts.fixed + counts.closed > 0) {
            return (
              <>
                <Empty>No open findings. Counts: 0 open, {counts.hypothesis} hypotheses, {counts.fixed} fixed, {counts.closed} refuted or closed.</Empty>
                <div className="mt-3 flex justify-center">
                  <Button variant="primary" onClick={() => setTab('fixed')}>View fixed findings</Button>
                </div>
              </>
            );
          }
          if (items.length === 0) return <Empty>{tab === 'hypothesis' ? 'No open hypotheses.' : tab === 'closed' ? 'Nothing refuted or closed.' : tab === 'fixed' ? 'No fix has been accepted by independent tests yet.' : 'No open findings in this list.'}</Empty>;
          return (
            <>
              <div className="hidden min-w-0 overflow-x-auto sm:block" data-testid="findings-table-scroll">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead className="text-xs uppercase tracking-wider text-faint">
                    <tr><th className="py-1.5 pr-3">Finding</th><th className="pr-4">Status</th><th className="pr-3">Kind</th><th className="pr-3">Severity</th><th className="pr-3">Commit</th><th>Updated</th></tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {items.map(f => (
                      <tr key={f.id}>
                        <td className="py-2 pr-3"><Link className="hover:text-accent" href={`/projects/${project.id}/findings/${f.id}?repo=${encodeURIComponent(selectedRepo.id)}`}>{f.title}</Link></td>
                        <td className="whitespace-nowrap pr-4"><Badge view={FINDING_STATUS[f.status]} /></td>
                        <td className="pr-3"><Tag tone={f.kind === 'security' ? 'bad' : 'info'}>{f.kind}</Tag></td>
                        <td className="pr-3">{f.severity ? <Badge view={SEVERITY[f.severity.level]} /> : <span className="text-faint">not assessed</span>}</td>
                        <td className="pr-3"><Mono>{shortSha(f.commit_sha)}</Mono></td>
                        <td className="text-muted"><Time iso={f.updated_at} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex min-w-0 flex-col gap-2 sm:hidden" data-testid="findings-mobile-list">
                {items.map(f => (
                  <article key={f.id} className="min-w-0 rounded-md border border-line bg-panel-2/40 p-3" data-testid="finding-mobile-row">
                    <Link className="block break-words hover:text-accent" href={`/projects/${project.id}/findings/${f.id}?repo=${encodeURIComponent(selectedRepo.id)}`}>{f.title}</Link>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <span data-testid="finding-mobile-status"><Badge view={FINDING_STATUS[f.status]} /></span>
                      <Tag tone={f.kind === 'security' ? 'bad' : 'info'}>{f.kind}</Tag>
                      {f.severity ? <Badge view={SEVERITY[f.severity.level]} /> : <span className="text-xs text-faint">not assessed</span>}
                      <Mono>{shortSha(f.commit_sha)}</Mono>
                      <span className="text-xs text-muted"><Time iso={f.updated_at} /></span>
                    </div>
                  </article>
                ))}
              </div>
            </>
          );
        }}
      </Async>
    </Panel>
      }
    </div>
  );
}
