'use client';

import type { Approval, ApprovalSubject, DeliveryAuthority, DeliveryMode } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useState } from 'react';
import { useProject } from '@/components/project';
import { useSession } from '@/components/session';
import { Async, Badge, Button, Empty, ErrorState, Field, inputClass, Mono, PageTitle, Panel, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { useActionKeys } from '@/lib/action-key';
import { SCOPE_SANDBOX_EXPLANATION } from '@/lib/scope';
import { APPROVAL_STATUS } from '@/lib/status';
import { type Resource, useResource } from '@/lib/use-resource';

const SUBJECT_LABEL: Record<ApprovalSubject, string> = {
  scope: 'Sandbox testing scope',
  remediation: 'Candidate fix',
  pr: 'Pull request for a verified fix',
};

const WHAT: Record<ApprovalSubject, { will: string; wont: string }> = {
  scope: { will: `Lets agents build and run reviewed checks against the listed sandbox targets until the scope expires. ${SCOPE_SANDBOX_EXPLANATION}`, wont: 'Touch any system outside the scope, or run unreviewed checks.' },
  remediation: { will: 'Lets Patch create a candidate fix in an isolated worktree and Verify re-run security and functional tests on it.', wont: 'Merge, push, deploy, or change the repository.' },
  pr: { will: 'Lets the platform open a pull request with the verified patch for human review.', wont: 'Merge the pull request or deploy.' },
};

const MANUAL_NOTE = 'Ari asks here before it builds a candidate fix or opens a pull request, and it never merges or deploys on its own.';
const MODE_LABEL: Record<DeliveryMode, string> = { manual: 'Ask before each fix', pull_request: 'Open a pull request', auto: 'Commit to the default branch' };
const DELIVERY_NOTE: Record<DeliveryMode, string> = {
  manual: MANUAL_NOTE,
  pull_request: 'Fix delivery is set to "Open a pull request": candidate fixes are approved automatically on behalf of the owner who authorized delivery, and each independently verified fix arrives as a pull request. Sandbox testing scopes are still decided here. Ari never merges or deploys on its own.',
  auto: 'Fix delivery is set to "Commit to the default branch": candidate fixes are approved automatically on behalf of the owner who authorized delivery, and each independently verified fix is committed to the default branch with no human check (a pull request if the branch moved or is protected). Sandbox testing scopes are still decided here.',
};
const AUTO_APPROVED_REASON = 'auto delivery';
const isAutoApproved = (a: Approval) => a.status === 'approved' && (a.reason ?? '').startsWith(AUTO_APPROVED_REASON);

export default function ApprovalsPage() {
  const { project, selectedRepo } = useProject();
  const [wholeProject, setWholeProject] = useState(false);
  const repoId = wholeProject ? undefined : selectedRepo?.id;
  const authority = useResource(`delivery-authority:${project.id}`, s => api().deliveryAuthority(project.id, { signal: s }), 30000);
  const approvals = useResource(`approvals:${project.id}:${repoId ?? 'all'}`, s => api().approvals(project.id, { signal: s, ...(repoId ? { repoId } : {}) }), 5000);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <PageTitle eyebrow="Review" title="Approvals" subtitle={repoId ? `Decisions for ${selectedRepo!.full_name}` : 'Every decision in this project, across all repositories.'} />
      {selectedRepo && (
        <div role="tablist" aria-label="Approval scope" className="flex gap-1 text-sm">
          <button role="tab" type="button" aria-selected={!wholeProject} onClick={() => setWholeProject(false)} className={`rounded-md px-3 py-1.5 ${!wholeProject ? 'bg-panel-2 text-ink' : 'text-muted hover:text-ink'}`}>This repository</button>
          <button role="tab" type="button" aria-selected={wholeProject} onClick={() => setWholeProject(true)} className={`rounded-md px-3 py-1.5 ${wholeProject ? 'bg-panel-2 text-ink' : 'text-muted hover:text-ink'}`}>Whole project</button>
        </div>
      )}
      <Panel title="Approval inbox" actions={<span className="text-xs text-muted">Owners may approve their own sandbox scopes. Other approvals require a different requester and approver.</span>}>
      <Async resource={approvals} label="approvals">
        {page => {
          const items = [...page.items].sort((a, b) => Number(b.status === 'pending') - Number(a.status === 'pending') || b.requested_at.localeCompare(a.requested_at));
          return (<>
            <DeliveryNote authority={authority} projectId={project.id} configured={project.settings.delivery_mode} />
            {items.length === 0 ? <Empty>No approvals requested yet.</Empty> : (
              <ul className="flex flex-col gap-3">{items.map(a => <ApprovalRow key={a.id} approval={a} onDone={approvals.reload} />)}</ul>
            )}
          </>);
        }}
      </Async>
    </Panel>
    </div>
  );
}

/** Copy follows what the server says delivery does now, not just the saved setting: a mode only
 * takes effect while its authorizing owner is still an owner, and the deployment may forbid direct commits. */
function deliveryText(a: DeliveryAuthority): string {
  const label = MODE_LABEL[a.configured_mode];
  if (a.state === 'manual') return MANUAL_NOTE;
  if (a.state === 'inactive') return `Fix delivery is set to "${label}", but no owner has authorized it, so it is not in effect: ${MANUAL_NOTE} An owner saving the setting authorizes it.`;
  if (a.state === 'revoked') return `Fix delivery is set to "${label}", but the owner who authorized it is no longer an owner of this organization, so that authorization has ended: ${MANUAL_NOTE} An owner saving the setting authorizes it again.`;
  if (a.configured_mode === 'auto' && a.effective_mode === 'pull_request') {
    return 'Fix delivery is set to "Commit to the default branch", but this deployment does not allow direct commits, so each independently verified fix arrives as a pull request instead. Candidate fixes are approved automatically on behalf of the owner who authorized delivery. Sandbox testing scopes are still decided here. Ari never merges or deploys on its own.';
  }
  return DELIVERY_NOTE[a.effective_mode];
}

function DeliveryNote({ authority, projectId, configured }: { authority: Resource<DeliveryAuthority>; projectId: string; configured: DeliveryMode }) {
  const settingsLink = <> Change it under <Link className="text-accent hover:underline" href={`/projects/${projectId}/settings`}>Settings → Fix delivery</Link>.</>;
  if (authority.status === 'loading') {
    return <p className="mb-3 text-xs text-muted" data-testid="approval-delivery-note" data-state="loading">Checking whether fix delivery is in effect for this project…</p>;
  }
  if (authority.status === 'error') {
    return (
      <p className="mb-3 text-xs text-warn" data-testid="approval-delivery-note" data-state="error">
        Could not check whether fix delivery (set to &quot;{MODE_LABEL[configured]}&quot;) is in effect, so this page cannot say whether candidate fixes are approved automatically. It is in effect only while the owner who authorized it is still an owner.{' '}
        <button type="button" className="font-semibold underline-offset-2 hover:underline" onClick={authority.reload}>Retry</button>
        {settingsLink}
      </p>
    );
  }
  return <p className="mb-3 text-xs text-muted" data-testid="approval-delivery-note" data-state={authority.data.state}>{deliveryText(authority.data)}{settingsLink}</p>;
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
        <span className="font-medium">{SUBJECT_LABEL[approval.subject_type]}</span>
        <Badge view={APPROVAL_STATUS[approval.status]} />
      </div>
      <p className="mt-1 text-xs text-muted">
        Subject {subjectHref ? <Link className="font-mono text-accent hover:underline" href={subjectHref}>{approval.subject_id}</Link> : <Mono>{approval.subject_id}</Mono>}
        {' '}· requested by {mine ? 'you' : approval.requested_by ? <Mono>{approval.requested_by}</Mono> : 'Ari (automatic)'} <Time iso={approval.requested_at} />
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
          {mine && <p className="mt-2 text-xs text-warn" data-testid="approval-self-note">You requested this, so another project member must approve it. You can still decline it.</p>}
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
        <p className="mt-2 text-xs text-muted" data-testid="approval-decided">
          {isAutoApproved(approval)
            ? <>Approved automatically {approval.decided_by === me.user.id ? 'on your behalf' : <>on behalf of <Mono>{approval.decided_by ?? '—'}</Mono></>} under the project&apos;s fix-delivery setting; no one clicked Approve.</>
            : <>Decided by {approval.decided_by === me.user.id ? 'you' : <Mono>{approval.decided_by ?? '—'}</Mono>}</>}
          {' '}<Time iso={approval.decided_at} />{approval.reason && <> — {approval.reason}</>}
        </p>
      )}
      {error !== null && <div className="mt-2"><ErrorState error={error} context="Decision not recorded" /></div>}
    </li>
  );
}
