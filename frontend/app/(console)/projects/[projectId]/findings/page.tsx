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
  const { project } = useProject();
  const findings = useResource(`findings:${project.id}`, s => api().findings(project.id, { signal: s }));
  const [tab, setTab] = useState<FindingGroup>('open');
  return (
    <>
      <PageTitle eyebrow="Review" title="Findings" subtitle="Security and correctness findings from assessments, deduplicated by state." />
    <Panel title="Findings">
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
          const counts = findingGroupCounts(page.items);
          const items = page.items.filter(f => findingGroup(f.status) === tab);
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
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wider text-faint">
                  <tr><th className="py-1.5 pr-3">Finding</th><th className="pr-3">Status</th><th className="pr-3">Kind</th><th className="pr-3">Severity</th><th className="pr-3">Commit</th><th>Updated</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {items.map(f => (
                    <tr key={f.id}>
                      <td className="py-2 pr-3"><Link className="hover:text-accent" href={`/projects/${project.id}/findings/${f.id}`}>{f.title}</Link></td>
                      <td className="pr-3"><Badge view={FINDING_STATUS[f.status]} /></td>
                      <td className="pr-3"><Tag tone={f.kind === 'security' ? 'bad' : 'info'}>{f.kind}</Tag></td>
                      <td className="pr-3">{f.severity ? <Badge view={SEVERITY[f.severity.level]} /> : <span className="text-faint">not assessed</span>}</td>
                      <td className="pr-3"><Mono>{shortSha(f.commit_sha)}</Mono></td>
                      <td className="text-muted"><Time iso={f.updated_at} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }}
      </Async>
    </Panel>
    </>
  );
}
