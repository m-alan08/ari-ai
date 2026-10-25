'use client';
import type { Approval, AssessmentDetail, StreamEvent } from '@hackatl/frontend-transport';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSession } from '@/components/session';
import { Badge, Button, Empty, ErrorState, Loading, Mono, Panel, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { useActionKeys } from '@/lib/action-key';
import { APPROVAL_STATUS, SEVERITY } from '@/lib/status';

const WHAT: Record<string, { will: string; wont: string }> = {
  scope: { will: 'Lets agents build and run reviewed checks against the listed sandbox targets until the scope expires.', wont: 'Touch any system outside the scope, or run unreviewed checks.' },
  remediation: { will: 'Lets Patch create a candidate fix in an isolated copy and Verify re-run tests on it.', wont: 'Merge, push, deploy, or change the repository.' },
  pr: { will: 'Lets the platform open a pull request with the verified patch for human review.', wont: 'Merge the pull request or deploy.' },
};

export function ApprovalGate({ projectId, events, findings = [], onDecided }: {
  projectId: string; events: StreamEvent[]; findings?: AssessmentDetail['findings']; onDecided: () => void;
}) {
  const { me } = useSession();
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [planSubjects, setPlanSubjects] = useState<Record<string, AssessmentDetail['findings'][number]>>({});
  const requested = useMemo(() => new Set(events.map(e => e.approval_id).filter((x): x is string => !!x)), [events]);
  const load = useCallback(() => {
    api().pendingApprovals(projectId).then(p => setApprovals(p.items.filter(a => requested.has(a.id))), setError);
  }, [projectId, requested]);
  useEffect(load, [load]);
  // Remediation subjects may be the finding itself (fnd_) or its remediation plan (rmp_).
  // Plans live in the finding's detail records, so resolve those lazily — names beat raw ids.
  // The ref survives re-renders so each subject is fetched exactly once even though
  // `approvals`/`findings` get fresh array identities on every poll.
  const resolving = useRef(new Set<string>());
  useEffect(() => {
    for (const a of approvals ?? []) {
      if (a.subject_type !== 'remediation') continue;
      if (findings.some(f => f.id === a.subject_id)) continue;
      if (planSubjects[a.id] || resolving.current.has(a.id)) continue;
      resolving.current.add(a.id);
      void (async () => {
        try {
          for (const f of findings) {
            const d = await api().finding(f.id);
            if (d.records.some(r => r.record.id === a.subject_id)) {
              setPlanSubjects(m => ({ ...m, [a.id]: f }));
              break;
            }
          }
        } catch { /* leave unresolved: the card falls back to the subject id */ }
      })();
    }
  }, [approvals, findings, planSubjects]);
  const keys = useActionKeys('decide-approval');
  const decide = async (ap: Approval, decision: 'approved' | 'declined') => {
    setBusy(true);
    setError(null);
    try { await api().decideApproval(ap.id, { decision, reason: null }, { idempotencyKey: keys.keyFor(`${ap.id}|${decision}`) }); keys.done(); setConfirming(null); onDecided(); load(); }
    catch (err) { setError(err); } finally { setBusy(false); }
  };
  const what = (ap: Approval) => WHAT[ap.subject_type] ?? WHAT.remediation!;
  return (
    <Panel title="Human approval required" className="mt-4 border-warn/50">
      <p className="text-sm text-muted">Approve a candidate fix in an isolated copy. Verify will retest it; nothing is merged or deployed.</p>
      {approvals === null && !error && <Loading label="Loading approval" />}
      {approvals?.length === 0 && <Empty>No pending approval found for this run.</Empty>}
      {approvals?.map((ap) => {
        const subject = findings.find(f => f.id === ap.subject_id) ?? planSubjects[ap.id];
        const subjectPath = (subject?.evidence.find(e => e.path)?.path ?? subject?.subject_refs[0])?.replace(/^target\//, '');
        const severity = subject?.severity?.level ? SEVERITY[subject.severity.level] : null;
        return (
        <div key={ap.id} className="mt-3 rounded-lg border border-line p-3 text-sm">
          <p className="font-medium">
            {subject ? <>Fix request · {subject.title}</> : `Fix request (${ap.subject_type})`}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <Badge view={APPROVAL_STATUS[ap.status]} />
            {severity && <Badge view={severity} />}
            {subject && <span className="capitalize">{subject.kind}</span>}
            {subjectPath && <Mono>{subjectPath}</Mono>}
          </p>
          <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
            <p className="rounded border border-ok/30 bg-ok/5 p-2"><span className="font-semibold text-ok">Will: </span>{what(ap).will}</p>
            <p className="rounded border border-line p-2"><span className="font-semibold text-muted">Will not: </span>{what(ap).wont}</p>
          </div>
          {confirming === ap.id ? (
            <div className="mt-3 rounded border border-warn/50 bg-warn/5 p-2" role="alert">
              <p className="text-xs">
                Confirm approval{subject ? ` of a candidate fix for “${subject.title}”` : ` of ${ap.subject_type} ${ap.subject_id}`}. This only lets the agents prepare and retest a fix — it never merges or deploys.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button variant="primary" disabled={busy} onClick={() => void decide(ap, 'approved')}>Confirm approval</Button>
                <Button variant="ghost" disabled={busy} onClick={() => setConfirming(null)}>Back</Button>
              </div>
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button variant="primary" disabled={busy || ap.requested_by === me.user.id} onClick={() => setConfirming(ap.id)}>Approve remediation</Button>
              <Button variant="danger" disabled={busy} onClick={() => void decide(ap, 'declined')}>Decline</Button>
            </div>
          )}
          <details className="mt-3 text-xs text-muted"><summary>Request details</summary><p className="mt-2 break-all"><Mono>{ap.subject_type}:{ap.subject_id}</Mono></p><p>Requested by {ap.requested_by ?? 'system'} <Time iso={ap.requested_at} /></p></details>
        </div>
        );
      })}
      {error !== null && <div className="mt-2"><ErrorState error={error} context="Approval" /></div>}
    </Panel>
  );
}
