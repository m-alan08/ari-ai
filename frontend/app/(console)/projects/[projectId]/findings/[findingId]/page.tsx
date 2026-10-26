'use client';

import { findingStandards, type FindingDetail, type FindingRecord, type PatchView } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { BeforeAfter } from '@/components/before-after';
import { ExpectationLinks } from '@/components/expectation-links';
import { ArtifactPeek, EvidenceList } from '@/components/evidence';
import { PatchDeliveryStatus } from '@/components/patch-delivery-status';
import { useParam, useProject } from '@/components/project';
import { useSession } from '@/components/session';
import { Async, Badge, Button, Empty, ErrorState, Field, inputClass, Mono, PageTitle, Panel, Tag, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { findingStory, repoFullNameForEvidence } from '@/lib/finding-story';
import { shortSha } from '@/lib/format';
import { FINDING_STATUS, SEVERITY, VALIDATION_OUTCOME, VERDICT } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

type Of<T extends FindingRecord['type']> = Extract<FindingRecord, { type: T }>['record'];
const of = <T extends FindingRecord['type']>(records: FindingRecord[], type: T): Of<T>[] =>
  records.filter(r => r.type === type).map(r => r.record as Of<T>);

const CHECK_LABEL: Record<string, string> = {
  original_check_now_fails_to_reproduce: 'Original exploit check no longer reproduces',
  functional_suite_passes: 'Functional test suite passes',
  generated_tests_pass: 'Generated regression tests pass',
  no_test_tampering: 'No test files were weakened',
  diff_scope_ok: 'Diff stays within the planned files',
};

export default function FindingPage() {
  const id = useParam('findingId');
  const { project, repos } = useProject();
  const detail = useResource(`finding:${id}`, s => api().finding(id, { signal: s }));
  const patches = useResource(`patches:${id}`, s => api().patches(id, { signal: s }));
  return (
    <Async resource={detail} label="finding">
      {d => {
        const patchItems = patches.status === 'ready' ? patches.data.items : null;
        const last = patchItems?.[patchItems.length - 1];
        const patchNote = patches.status === 'error' ? 'Could not load the patch record' : patches.status === 'loading' ? 'Loading the patch record' : null;
        const validation = of(d.records, 'validation_result')[0]?.outcome ?? null;
        const story = findingStory({
          title: d.finding.title,
          status: d.finding.status,
          severityRationale: d.finding.severity?.rationale ?? null,
          validationOutcome: validation,
          patchRationale: last?.patch.rationale ?? null,
          verification: last?.verification?.verdict ?? null,
        }).map(line => patchNote && (line.label === 'What we changed' || line.label === 'Did it work?') ? { ...line, text: patchNote } : line);
        return (
        <>
          <PageTitle
            title={<span className="flex flex-wrap items-center gap-2">{d.finding.title} <Badge view={FINDING_STATUS[d.finding.status]} /></span>}
            subtitle={<>
              <Tag tone={d.finding.kind === 'security' ? 'bad' : 'info'}>{d.finding.kind}</Tag>{' '}
              <Mono>{d.finding.id}</Mono> · commit <Mono>{shortSha(d.finding.commit_sha)}</Mono> · from{' '}
              <Link className="text-accent hover:underline" href={`/projects/${project.id}/assessments/${d.finding.assessment_id}`}>{d.finding.assessment_id}</Link>
              {' '}· confidence {(d.finding.confidence * 100).toFixed(0)}%
            </>}
          />
          <Panel title="What happened">
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-testid="finding-story">
              {story.map(line => (
                <div key={line.label}>
                  <dt className="text-xs font-semibold text-faint">{line.label}</dt>
                  <dd className="mt-0.5 text-sm text-ink">{line.text}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <div className="mt-6 min-w-0">
            <BeforeAfter kind={d.finding.kind} status={d.finding.status} validation={validation} patches={patchItems} patchesError={patches.status === 'error' ? { error: patches.error, onRetry: patches.reload } : null} />
          </div>
          <RepoScope assessmentId={d.finding.assessment_id} projectId={project.id} repos={repos}>
          {(repo, note) => (
          <div className="mt-6 grid min-w-0 grid-cols-1 gap-6 xl:grid-cols-[3fr_2fr]">
            <div className="flex min-w-0 flex-col gap-6">
              {note && <p className="text-xs text-muted">{note}</p>}
              <EvidenceChain detail={d} patches={patchItems} repo={repo} projectId={project.id} />
              <Panel title="Patches and independent verification">
                <Async resource={patches} label="patches">
                  {page => page.items.length === 0 ? <Empty>No patch candidate recorded for this finding.</Empty> : (
                    <div className="flex flex-col gap-4">{page.items.map(p => <PatchCard key={p.patch.id} view={p} />)}</div>
                  )}
                </Async>
              </Panel>
            </div>
            <div className="flex min-w-0 flex-col gap-6">
              <StandardsPanel finding={d.finding} />
              {d.finding.severity && (
                <Panel title="Severity">
                  <Badge view={SEVERITY[d.finding.severity.level]} />
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted">
                    <dt>Reachability</dt><dd>{d.finding.severity.factors.reachability}</dd>
                    <dt>Privileges</dt><dd>{d.finding.severity.factors.privileges_required}</dd>
                    <dt>Data</dt><dd>{d.finding.severity.factors.data_sensitivity}</dd>
                    <dt>Path verified</dt><dd>{d.finding.severity.factors.verified_path ? 'yes' : 'no — contains unverified steps'}</dd>
                  </dl>
                  <p className="mt-2 text-sm text-muted">{d.finding.severity.rationale}</p>
                </Panel>
              )}
              <Panel title="Finding evidence"><EvidenceList items={d.finding.evidence} repoFullName={repo} /></Panel>
              <Panel title="Status history">
                <ol className="flex flex-col gap-2 text-xs">
                  {d.finding.history.map((h, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-1.5">
                      {h.from_status && <><Badge view={FINDING_STATUS[h.from_status]} /><span aria-hidden="true">→</span></>}
                      <Badge view={FINDING_STATUS[h.to_status]} />
                      <span className="text-muted">{h.actor} · <Time iso={h.at} /></span>
                      {h.reason && <span className="w-full text-muted">{h.reason}</span>}
                    </li>
                  ))}
                  {d.finding.history.length === 0 && <li className="text-faint">No transitions recorded.</li>}
                </ol>
              </Panel>
              <AcceptRisk detail={d} onDone={detail.reload} />
            </div>
          </div>
          )}
          </RepoScope>
        </>
        );
      }}
    </Async>
  );
}

function StandardsPanel({ finding }: { finding: FindingDetail['finding'] }) {
  const view = findingStandards(finding);
  return (
    <Panel title="Standards">
      <div data-testid="finding-standards">
      <p className="text-sm">{view.cvss}</p>
      {view.labels.length === 0 ? <p className="mt-2 text-xs text-faint">No CWE, ATT&CK, or D3FEND mapping was recorded.</p> : (
        <ul className="mt-2 flex flex-col gap-1 text-xs text-muted">
          {view.labels.map(label => <li key={label}>{label}</li>)}
        </ul>
      )}
      {view.limitations.length > 0 && (
        <ul className="mt-2 list-disc pl-4 text-xs text-muted">
          {view.limitations.map(item => <li key={item}>{item}</li>)}
        </ul>
      )}
      </div>
    </Panel>
  );
}

function RepoScope({ assessmentId, projectId, repos, children }: {
  assessmentId: string;
  projectId: string;
  repos: { id: string; full_name: string }[];
  children: (repo: string | undefined, note: string | null) => ReactNode;
}) {
  const repoKey = repos.map(repo => `${repo.id}:${repo.full_name}`).join('\n');
  const listed = repoKey === '' ? [] : repoKey.split('\n').map(row => {
    const split = row.indexOf(':');
    return { id: row.slice(0, split), full_name: row.slice(split + 1) };
  });
  const [resolved, setResolved] = useState<{ name: string | undefined; note: string | null } | null>(null);
  useEffect(() => {
    const rows = repoKey === '' ? [] : repoKey.split('\n').map(row => {
      const split = row.indexOf(':');
      return { id: row.slice(0, split), full_name: row.slice(split + 1) };
    });
    if (rows.length <= 1) return;
    const controller = new AbortController();
    const signal = controller.signal;
    (async () => {
      const [assessment, scopes] = await Promise.all([
        api().assessment(assessmentId, { signal }),
        api().scopes(projectId, { signal }),
      ]);
      const scope = scopes.items.find(item => item.id === assessment.assessment.scope_id);
      const name = repoFullNameForEvidence(rows, scope?.targets ?? null, assessment.assessment.repo_id ?? null);
      if (!signal.aborted) setResolved({ name, note: name ? null : 'Source lines stay unlinked: this scope does not name exactly one repository.' });
    })().catch((error: unknown) => {
      if (!signal.aborted) setResolved({ name: undefined, note: error instanceof Error ? error.message : 'Could not resolve the repository for source links.' });
    });
    return () => controller.abort();
  }, [assessmentId, projectId, repoKey]);
  if (listed.length <= 1) return children(listed[0]?.full_name, null);
  return children(resolved?.name, resolved?.note ?? null);
}

function Step({ n, title, who, children, empty }: { n: number; title: string; who: string; children?: ReactNode; empty?: string }) {
  return (
    <li className="relative border-l border-line pb-5 pl-5 last:pb-0">
      <span aria-hidden="true" className="absolute -left-[0.55rem] top-0 grid h-4 w-4 place-items-center rounded-full border border-line bg-panel text-[0.6rem] text-muted">{n}</span>
      <p className="text-sm font-semibold">{title} <span className="ml-1 text-xs font-normal text-faint">{who}</span></p>
      <div className="mt-1.5">{children ?? <p className="text-xs text-faint">{empty ?? 'Not recorded.'}</p>}</div>
    </li>
  );
}

function EvidenceChain({ detail, patches, repo, projectId }: { detail: FindingDetail; patches: PatchView[] | null; repo: string | undefined; projectId: string }) {
  const r = detail.records;
  const hyps = of(r, 'hypothesis');
  const vals = of(r, 'validation_result');
  const paths = of(r, 'attack_path');
  const causes = of(r, 'root_cause');
  const plans = of(r, 'remediation_plan');
  const hardening = of(r, 'hardening');
  const verdicts = (patches ?? []).map(p => p.verification).filter(v => v !== null);
  return (
    <Panel title="Evidence chain">
      <ol>
        <Step n={1} title="Expected behaviour" who="code intelligence + reviewer">
          <ExpectationLinks
            projectId={projectId}
            snapshotId={detail.finding.snapshot_id}
            assessmentId={detail.finding.assessment_id}
            ids={detail.finding.expectation_ids}
          />
        </Step>
        <Step n={2} title="Hypothesis" who="Scout" empty={detail.finding.kind === 'functional' ? 'Functional finding — raised by the Reviewer, not Scout.' : undefined}>
          {hyps.length > 0 && hyps.map(h => (
            <div key={h.id} className="mb-2">
              <p className="text-xs"><Tag tone="info">{h.weakness_class}</Tag> confidence {(h.confidence * 100).toFixed(0)}% · check category <Mono>{h.suggested_check_category}</Mono></p>
              <p className="mt-1 text-sm text-muted">{h.rationale}</p>
              <div className="mt-1"><EvidenceList items={h.cited_spans} repoFullName={repo} /></div>
            </div>
          ))}
        </Step>
        <Step n={3} title="Validation" who="Probe — reviewed check in the authorized sandbox">
          {vals.length > 0 && vals.map(v => (
            <div key={v.id} className="mb-2 text-xs">
              <p className="flex flex-wrap items-center gap-2"><Badge view={VALIDATION_OUTCOME[v.outcome]} /> template <Mono>{v.check_template_id}@{v.check_template_version}</Mono> · sandbox run <Mono>{v.sandbox_run_id}</Mono></p>
              <div className="mt-1"><EvidenceList items={v.evidence} repoFullName={repo} /></div>
            </div>
          ))}
        </Step>
        <Step n={4} title="Attack path" who="Trace">
          {paths.length > 0 && paths.map(p => (
            <div key={p.id} className="mb-2">
              <ol className="flex flex-col gap-1 text-xs">
                {p.steps.map(s => (
                  <li key={s.order} className="flex flex-wrap items-center gap-2">
                    <span className="text-faint">{s.order}.</span> {s.description}
                    <Badge view={s.verified ? { label: 'verified', tone: 'ok', glyph: '✓' } : { label: 'hypothesis', tone: 'info', glyph: '?' }} />
                    {s.symbol_ref && <Mono>{s.symbol_ref}</Mono>}
                  </li>
                ))}
              </ol>
              {p.preconditions.length > 0 && <p className="mt-1 text-xs text-muted">Preconditions: {p.preconditions.join('; ')}</p>}
              <p className="mt-1 text-sm text-muted">{p.impact_summary}</p>
            </div>
          ))}
        </Step>
        <Step n={5} title="Root cause" who="Diagnose">
          {causes.length > 0 && causes.map(c => (
            <div key={c.id} className="mb-2">
              <p className="text-xs"><Tag>{c.cause_class}</Tag></p>
              <p className="mt-1 text-sm text-muted">{c.summary}</p>
              <div className="mt-1"><EvidenceList items={c.cause_spans} repoFullName={repo} /></div>
            </div>
          ))}
          {plans.map(p => (
            <div key={p.id} className="mt-2 rounded border border-line p-2 text-xs text-muted">
              <p className="font-semibold text-ink">Remediation plan</p>
              <p className="mt-1">{p.strategy}</p>
              {p.files_expected.length > 0 && <p className="mt-1">Files expected: {p.files_expected.map(f => <Mono key={f}>{f} </Mono>)}</p>}
              {p.constraints.length > 0 && <p className="mt-1">Constraints: {p.constraints.join('; ')}</p>}
              <p className="mt-1">Test plan: {p.test_plan}</p>
            </div>
          ))}
        </Step>
        <Step n={6} title="Candidate patch" who="Patch — isolated worktree, never merged">
          {patches && patches.length > 0 && <p className="text-xs text-muted">{patches.length} candidate{patches.length > 1 ? 's' : ''} — diff below.</p>}
        </Step>
        <Step n={7} title="Independent verification" who="Verify — trusted tests in a fresh sandbox">
          {verdicts.length > 0 && verdicts.map(v => (
            <p key={v.id} className="flex flex-wrap items-center gap-2 text-xs"><Badge view={VERDICT[v.verdict]} /> {v.checks.filter(c => c.passed).length}/{v.checks.length} checks passed</p>
          ))}
        </Step>
        <Step n={8} title="Hardening" who="Fortify">
          {hardening.length > 0 && hardening.map(h => (
            <div key={h.id} className="mb-2 text-sm text-muted">
              <p className="text-xs"><Tag>{h.category}</Tag> effort {h.effort}</p>
              <p className="mt-1">{h.recommendation}</p>
              {h.proposed_test_artifact_id && <ArtifactPeek key={h.proposed_test_artifact_id} id={h.proposed_test_artifact_id} label="proposed regression test" />}
            </div>
          ))}
        </Step>
      </ol>
    </Panel>
  );
}

function PatchCard({ view }: { view: PatchView }) {
  const { patch, verification } = view;
  return (
    <article className="rounded-md border border-line bg-panel-2 p-3" data-testid={`patch-${patch.id}`}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold">Attempt {patch.attempt} · <Mono>{patch.id}</Mono></span>
        {verification ? <Badge view={VERDICT[verification.verdict]} /> : <Badge view={{ label: 'Not yet verified', tone: 'warn', glyph: '…' }} />}
      </header>
      <PatchDeliveryStatus patchId={patch.id} delivery={view.delivery} />
      <p className="mt-1 text-sm text-muted">{patch.rationale}</p>
      <p className="mt-1 text-xs text-muted">
        base <Mono>{shortSha(patch.base_commit_sha)}</Mono> · worktree <Mono>{patch.worktree_ref}</Mono> · files {patch.files_changed.join(', ') || '—'}
        {patch.tests_added.length > 0 && <> · tests added {patch.tests_added.join(', ')}</>}
      </p>
      <ArtifactPeek key={patch.diff_artifact_id} id={patch.diff_artifact_id} label="diff" asDiff />
      {verification && (
        <div className="mt-3 border-t border-line pt-2">
          <p className="text-xs font-semibold">Verify checks <span className="font-normal text-faint">· sandbox runs {verification.sandbox_run_ids.join(', ') || '—'}</span></p>
          <ul className="mt-1 flex flex-col gap-1 text-xs">
            {verification.checks.map(c => (
              <li key={c.name} className="flex flex-col gap-1">
                <span className={c.passed ? 'text-ok' : 'text-bad'}><span aria-hidden="true">{c.passed ? '✓' : '×'} </span>{CHECK_LABEL[c.name] ?? c.name} — {c.passed ? 'passed' : 'failed'}</span>
                {c.evidence.length > 0 && <EvidenceList items={c.evidence} />}
              </li>
            ))}
          </ul>
          {verification.reasons.length > 0 && <ul className="mt-2 list-disc pl-4 text-xs text-muted">{verification.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
        </div>
      )}
    </article>
  );
}

function AcceptRisk({ detail, onDone }: { detail: FindingDetail; onDone: () => void }) {
  const { me } = useSession();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const owner = me.memberships.some(m => m.role === 'owner');
  if (detail.finding.status === 'accepted_risk' || detail.finding.status === 'closed') return null;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { await api().acceptRisk(detail.finding.id, { reason: reason.trim() }); onDone(); }
    catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <Panel title="Accept risk">
      <form onSubmit={submit} className="flex flex-col gap-2">
        <p className="text-xs text-muted">Records an owner decision to leave this finding unfixed, with your reason, in the audit log. It does not change any code.</p>
        <Field label="Reason"><textarea className={inputClass} value={reason} onChange={e => setReason(e.target.value)} required minLength={3} rows={2} name="risk_reason" /></Field>
        <Button type="submit" variant="danger" disabled={busy || reason.trim().length < 3 || !owner} title={owner ? undefined : 'Only project owners can accept risk'}>Accept risk</Button>
        {error !== null && <ErrorState error={error} context="Accept risk" />}
      </form>
    </Panel>
  );
}
