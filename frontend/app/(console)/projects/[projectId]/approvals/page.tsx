'use client';

import type { Approval, ApprovalSubject } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useState } from 'react';
import { useProject } from '@/components/project';
import { useSession } from '@/components/session';
import { Async, Badge, Button, Empty, ErrorState, Field, inputClass, Mono, PageTitle, Panel, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { useActionKeys } from '@/lib/action-key';
import { SCOPE_SANDBOX_EXPLANATION } from '@/lib/scope';
import { APPROVAL_STATUS } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

const WHAT: Record<ApprovalSubject, { will: string; wont: string }> = {
  scope: { will: `Lets agents build and run reviewed checks against the listed sandbox targets until the scope expires. ${SCOPE_SANDBOX_EXPLANATION}`, wont: 'Touch any system outside the scope, or run unreviewed checks.' },
  remediation: { will: 'Lets Patch create a candidate fix in an isolated worktree and Verify re-run security and functional tests on it.', wont: 'Merge, push, deploy, or change the repository.' },
  pr: { will: 'Lets the platform open a pull request with the verified patch for human review.', wont: 'Merge the pull request or deploy.' },
};

export default function ApprovalsPage() {
  const { project } = useProject();
  const approvals = useResource(`approvals:${project.id}`, s => api().approvals(project.id, { signal: s }), 5000);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <PageTitle eyebrow="Review" title="Approvals" subtitle="Decisions that let agents proceed — scoped to this project." />
      <Panel title="Approval inbox" actions={<span className="text-xs text-muted">Owners may approve their own sandbox scopes. Other approvals require a different requester and approver.</span>}>
      <Async resource={approvals} label="approvals">
        {page => {
          const items = [...page.items].sort((a, b) => Number(b.status === 'pending') - Number(a.status === 'pending') || b.requested_at.localeCompare(a.requested_at));
          return items.length === 0 ? <Empty>No approvals requested.</Empty> : (
            <ul className="flex flex-col gap-3">{items.map(a => <ApprovalRow key={a.id} approval={a} onDone={approvals.reload} />)}</ul>
          );
        }}
      </Async>
    </Panel>
    </div>
  );
}

function ApprovalRow({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  const { me } = useSession();
  const { project } = useProject();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const mine = approval.requested_by === me.user.id;
  const what = WHAT[approval.subject_type];
  const keys = useActionKeys('decide-approval');
  const decide = async (decision: 'approved' | 'declined') => {
    setBusy(true);
    setError(null);
    try {
      await api().decideApproval(approval.id, { decision, reason: reason.trim() || null }, { idempotencyKey: keys.keyFor(`${approval.id}|${decision}|${reason.trim()}`) });
      keys.done();
      onDone();
    }
    catch (err) { setError(err); } finally { setBusy(false); }
  };
  const subjectHref = approval.subject_type === 'scope' ? `/projects/${project.id}/scopes` : approval.subject_id.startsWith('asm_') ? `/projects/${project.id}/assessments/${approval.subject_id}` : approval.subject_id.startsWith('fnd_') ? `/projects/${project.id}/findings/${approval.subject_id}` : null;
  return (
    <li className="rounded-md border border-line bg-panel-2 p-3" data-testid={`approval-${approval.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium capitalize">{approval.subject_type} approval</span>
        <Badge view={APPROVAL_STATUS[approval.status]} />
      </div>
      <p className="mt-1 text-xs text-muted">
        Subject {subjectHref ? <Link className="font-mono text-accent hover:underline" href={subjectHref}>{approval.subject_id}</Link> : <Mono>{approval.subject_id}</Mono>}
        {' '}· requested by {approval.requested_by ? <Mono>{approval.requested_by}</Mono> : 'the orchestrator'}{mine && ' (you)'} <Time iso={approval.requested_at} />
      </p>
      <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
        <p className="rounded border border-ok/30 bg-ok/5 p-2"><span className="font-semibold text-ok">Will: </span>{what.will}</p>
        <p className="rounded border border-line p-2"><span className="font-semibold text-muted">Will not: </span>{what.wont}</p>
      </div>
      {approval.status === 'pending' ? (
        approval.subject_type === 'scope' ? <p className="mt-2 text-xs text-muted">Decide scope approvals on the <Link className="text-accent hover:underline" href={`/projects/${project.id}/scopes`}>Scopes</Link> tab.</p> : (<>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <Field label="Reason (optional)"><input className={inputClass} value={reason} onChange={e => setReason(e.target.value)} name="approval_reason" /></Field>
            <Button variant="primary" disabled={busy || mine} onClick={() => setConfirming(true)}>Approve {approval.subject_type}</Button>
            <Button variant="danger" disabled={busy} onClick={() => void decide('declined')}>Decline</Button>
          </div>
          {confirming && (
            <div className="mt-3 rounded border border-warn/50 bg-warn/5 p-2" role="alert">
              <p className="text-xs">Confirm {approval.subject_type} approval — {what.will} It never merges or deploys.</p>
              <div className="mt-2 flex gap-2">
                <Button variant="primary" disabled={busy} onClick={() => { setConfirming(false); void decide('approved'); }}>Confirm approval</Button>
                <Button variant="ghost" onClick={() => setConfirming(false)}>Back</Button>
              </div>
            </div>
          )}
        </>)
      ) : (
        <p className="mt-2 text-xs text-muted">Decided by <Mono>{approval.decided_by ?? '—'}</Mono> <Time iso={approval.decided_at} />{approval.reason && <> — {approval.reason}</>}</p>
      )}
      {error !== null && <div className="mt-2"><ErrorState error={error} context="Decision not recorded" /></div>}
    </li>
  );
}
