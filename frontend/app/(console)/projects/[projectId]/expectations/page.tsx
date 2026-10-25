'use client';

import type { BehavioralExpectation, EvidenceRef, ExpectationDecision, ExpectationDecisionRequest, ExpectationStatus, Repository, Snapshot } from '@hackatl/frontend-transport';
import { useMemo, useState } from 'react';
import { useProject } from '@/components/project';
import { Async, Badge, Button, Empty, ErrorState, Field, inputClass, Mono, PageTitle, Panel, Tag, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { EXPECTATION_STATUS, SNAPSHOT_STATUS } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

const FILTERS: Array<ExpectationStatus | 'all'> = ['all', 'inferred', 'ambiguous', 'conflicting', 'confirmed', 'rejected'];
const DECISIONS: Array<{ status: ExpectationDecisionRequest['status']; label: string; variant: 'primary' | 'secondary' | 'danger' }> = [
  { status: 'confirmed', label: 'Confirm', variant: 'primary' },
  { status: 'conflicting', label: 'Mark conflicting', variant: 'secondary' },
  { status: 'rejected', label: 'Reject', variant: 'danger' },
];

function sourceLabel(src: EvidenceRef): string {
  const where = src.path ?? src.symbol ?? src.artifact_id ?? '—';
  if (!src.span) return where;
  const { start_line: a, end_line: b } = src.span;
  return a === b ? `${where}:${a}` : `${where}:${a}-${b}`;
}

/**
 * Behavioral expectations extracted from an indexed snapshot. Inferred ones are
 * hypotheses about intended behavior; only a human decision makes them ground
 * truth for the reviewer. Decisions are version-pinned to the snapshot.
 */
export default function ExpectationsPage() {
  const { repos } = useProject();
  const [repoId, setRepoId] = useState(repos[0]?.id ?? '');
  if (repos.length === 0) return <Panel title="Behavioral expectations"><Empty>Connect a repository and index a snapshot first.</Empty></Panel>;
  const repo = repos.find(r => r.id === repoId) ?? repos[0]!;
  return (
    <div className="flex flex-col gap-4">
      <PageTitle eyebrow="Defend" title="Expectations" subtitle="Intended behavior learned from the code — the baseline findings are judged against." />
      {repos.length > 1 && (
        <Field label="Repository">
          <select className={inputClass} value={repo.id} onChange={e => setRepoId(e.target.value)}>
            {repos.map(r => <option key={r.id} value={r.id}>{r.full_name}</option>)}
          </select>
        </Field>
      )}
      <RepoExpectations key={repo.id} repo={repo} />
    </div>
  );
}

function RepoExpectations({ repo }: { repo: Repository }) {
  const snaps = useResource(`snapshots:${repo.id}`, s => api().snapshots(repo.id, { limit: 20 }, { signal: s }));
  const [chosen, setChosen] = useState<string | null>(null);
  return (
    <Async resource={snaps} label="snapshots">
      {page => {
        const ready = page.items.filter(s => s.status === 'ready');
        if (ready.length === 0) {
          return <Panel title="Behavioral expectations"><Empty>No indexed snapshot for {repo.full_name} yet. Expectations are extracted when a snapshot finishes indexing.</Empty></Panel>;
        }
        const snap = ready.find(s => s.id === chosen) ?? ready[0]!;
        return (
          <>
            <SnapshotPicker snapshots={page.items} selected={snap.id} onSelect={setChosen} />
            <ExpectationList key={snap.id} snapshot={snap} />
          </>
        );
      }}
    </Async>
  );
}

function SnapshotPicker({ snapshots, selected, onSelect }: { snapshots: Snapshot[]; selected: string; onSelect: (id: string) => void }) {
  return (
    <Panel title="Snapshot">
      <p className="mb-2 text-sm text-muted">Expectations and decisions are pinned to one commit. A decision on an older snapshot does not silently carry forward.</p>
      <ul className="flex flex-wrap gap-2">
        {snapshots.map(s => (
          <li key={s.id}>
            <button type="button" disabled={s.status !== 'ready'} onClick={() => onSelect(s.id)} aria-pressed={s.id === selected}
              className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs disabled:opacity-50 ${s.id === selected ? 'border-brand bg-white' : 'border-line bg-panel-2 hover:bg-white'}`}>
              <Mono>{s.commit_sha.slice(0, 10)}</Mono>
              <Badge view={SNAPSHOT_STATUS[s.status]} />
              <Time iso={s.created_at} />
            </button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function ExpectationList({ snapshot }: { snapshot: Snapshot }) {
  const [filter, setFilter] = useState<ExpectationStatus | 'all'>('all');
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const exps = useResource(`expectations:${snapshot.id}:${cursor ?? ''}`, s => api().expectations(snapshot.id, { limit: 100, cursor }, { signal: s }));
  return (
    <Panel title="Behavioral expectations" actions={
      <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-1">
        {FILTERS.map(f => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
            className={`label-mono rounded px-2 py-1 ${filter === f ? 'bg-white text-brand shadow-sm' : 'text-muted hover:text-ink'}`}>
            {f === 'all' ? 'All' : EXPECTATION_STATUS[f].label}
          </button>
        ))}
      </div>
    }>
      <Async resource={exps} label="expectations">
        {page => {
          const items = filter === 'all' ? page.items : page.items.filter(e => e.status === filter);
          return (
            <>
              <Summary items={page.items} />
              {items.length === 0 ? <Empty>No expectations{filter === 'all' ? '' : ` with status “${EXPECTATION_STATUS[filter].label}”`} on this page.</Empty> : (
                <ul className="flex flex-col gap-3">{items.map(e => <ExpectationRow key={e.id} exp={e} onDecided={exps.reload} />)}</ul>
              )}
              <div className="mt-3 flex gap-2">
                {cursor !== undefined && <Button onClick={() => setCursor(undefined)}>First page</Button>}
                {page.next_cursor && <Button onClick={() => setCursor(page.next_cursor ?? undefined)}>Next page</Button>}
              </div>
            </>
          );
        }}
      </Async>
    </Panel>
  );
}

function Summary({ items }: { items: BehavioralExpectation[] }) {
  const counts = useMemo(() => {
    const c: Partial<Record<ExpectationStatus, number>> = {};
    for (const e of items) c[e.status] = (c[e.status] ?? 0) + 1;
    return c;
  }, [items]);
  const open = (counts.inferred ?? 0) + (counts.ambiguous ?? 0);
  return (
    <p className="mb-3 text-sm text-muted" data-testid="expectation-summary">
      {items.length} on this page · <strong className="text-ink">{open}</strong> awaiting a human decision
      {(counts.conflicting ?? 0) > 0 && <> · <strong className="text-bad">{counts.conflicting}</strong> conflicting</>}
    </p>
  );
}

function ExpectationRow({ exp, onDecided }: { exp: BehavioralExpectation; onDecided: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-md border border-line bg-panel-2 p-3" data-testid={`expectation-${exp.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="max-w-3xl text-sm text-ink">{exp.statement}</p>
        <Badge view={EXPECTATION_STATUS[exp.status]} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <Tag>{exp.kind.replace('_', ' ')}</Tag>
        <Tag tone={exp.origin === 'human_input' ? 'ok' : 'neutral'}>{exp.origin === 'human_input' ? 'human input' : exp.origin}</Tag>
        <span>confidence {Math.round(exp.confidence * 100)}%</span>
        <span>subject <Mono>{exp.subject_ref}</Mono></span>
        {exp.data_sensitivity && <Tag tone="warn">{exp.data_sensitivity}</Tag>}
      </div>
      <ul className="mt-2 flex flex-col gap-0.5 text-xs text-muted">
        {exp.sources.map((src, i) => (
          <li key={i}>{src.kind.replace('_', ' ')} · <Mono>{sourceLabel(src)}</Mono>{src.note && <> — {src.note}</>}</li>
        ))}
      </ul>
      <div className="mt-2">
        <Button onClick={() => setOpen(o => !o)} aria-expanded={open}>{open ? 'Hide decision' : 'Decide'}</Button>
      </div>
      {open && <DecisionPanel exp={exp} onDecided={onDecided} />}
    </li>
  );
}

function DecisionPanel({ exp, onDecided }: { exp: BehavioralExpectation; onDecided: () => void }) {
  const history = useResource(`decisions:${exp.id}`, s => api().expectationDecisions(exp.snapshot_id, exp.id, { signal: s }));
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const revision = (items: ExpectationDecision[]) => items.reduce((m, d) => Math.max(m, d.revision), 0);
  const submit = async (status: ExpectationDecisionRequest['status'], current: number) => {
    setBusy(true);
    setError(null);
    try {
      await api().decideExpectation(exp.snapshot_id, exp.id, { status, reason: reason.trim(), expected_revision: current });
      setReason('');
      history.reload();
      onDecided();
    } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <div className="mt-3 rounded-md border border-line bg-white p-3">
      <Async resource={history} label="decision history">
        {page => {
          const current = revision(page.items);
          return (
            <>
              {page.items.length > 0 && (
                <ol className="mb-3 flex flex-col gap-1 text-xs text-muted" aria-label="Decision history">
                  {page.items.map(d => (
                    <li key={d.revision}>rev {d.revision} · <strong className="text-ink">{EXPECTATION_STATUS[d.status].label}</strong> by <Mono>{d.decided_by}</Mono> · <Time iso={d.decided_at} /> — {d.reason}</li>
                  ))}
                </ol>
              )}
              <form onSubmit={e => e.preventDefault()} className="flex flex-col gap-2">
                <Field label="Reason" hint="Recorded in the audit trail with your identity (min 3 characters).">
                  <input className={inputClass} value={reason} onChange={e => setReason(e.target.value)} maxLength={500} />
                </Field>
                <div className="flex flex-wrap gap-2">
                  {DECISIONS.map(d => (
                    <Button key={d.status} type="button" variant={d.variant} disabled={busy || reason.trim().length < 3} onClick={() => submit(d.status, current)}>{d.label}</Button>
                  ))}
                </div>
              </form>
              {error !== null && <ErrorState error={error} context="Decision not recorded" />}
            </>
          );
        }}
      </Async>
    </div>
  );
}
