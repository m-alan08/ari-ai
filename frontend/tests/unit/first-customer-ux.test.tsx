import { ApiError } from '@hackatl/frontend-transport';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const MANUAL_AUTHORITY = { configured_mode: 'manual', effective_mode: 'manual', state: 'manual', authorized_by: null, direct_commits_allowed: true };
const state = vi.hoisted(() => ({ approvals: [] as unknown[], findings: [] as unknown[], assessments: [] as unknown[], deliveryMode: 'manual' as string, authority: { status: 'ready', data: null as unknown, error: undefined as unknown } }));

vi.mock('@/components/project', () => ({ useProject: () => ({
  project: { id: 'prj_1', settings: { delivery_mode: state.deliveryMode } },
  selectedRepo: { id: 'rep_1', full_name: 'acme/payments', default_branch: 'main' },
}) }));
vi.mock('@/components/session', () => ({ useSession: () => ({ me: { user: { id: 'usr_me' } } }) }));
vi.mock('@/lib/action-key', () => ({ useActionKeys: () => ({ keyFor: () => 'key', done: () => {} }) }));
vi.mock('@/lib/use-resource', () => ({
  useResource: (key: string | null) => key?.startsWith('delivery-authority') ? { ...state.authority, data: state.authority.data ?? MANUAL_AUTHORITY, reload: vi.fn() } : ({
    status: 'ready',
    data: { items: key?.startsWith('approvals') ? state.approvals : key?.includes('assessments') ? state.assessments : state.findings, next_cursor: null },
    error: undefined,
    reload: vi.fn(),
  }),
}));

import ApprovalsPage from '@/app/(console)/projects/[projectId]/approvals/page';
import FindingsPage from '@/app/(console)/projects/[projectId]/findings/page';
import { ErrorState } from '@/components/ui';
import { describeError } from '@/lib/api';

afterEach(() => {
  cleanup();
  state.approvals = [];
  state.findings = [];
  state.deliveryMode = 'manual';
  state.assessments = [];
  state.authority = { status: 'ready', data: null, error: undefined };
});

const authority = (over: Record<string, unknown>) => { state.authority = { status: 'ready', data: { ...MANUAL_AUTHORITY, ...over }, error: undefined }; };

const apiError = (status: number, code: ConstructorParameters<typeof ApiError>[1]['code'], message: string) => new ApiError(status, { code, message, details: {}, request_id: 'req_1' });

describe('error states say what to do next', () => {
  it('turns a GitHub/subsystem outage into a recovery step', () => {
    const e = describeError(apiError(503, 'SUBSYSTEM_UNAVAILABLE', 'could not read the latest commit from GitHub'));
    expect(e.title).toBe('Request failed (503)');
    expect(e.recovery).toMatch(/nothing was started/);
    expect(e.recovery).toMatch(/Ari GitHub App/);
  });

  it('explains missing access and links back to projects for 403 and 404', () => {
    expect(describeError(apiError(403, 'FORBIDDEN', 'not a member')).recovery).toMatch(/Ask a project owner/);
    render(<ErrorState error={apiError(404, 'NOT_FOUND', 'project not found')} onRetry={() => {}} context="Could not load project" />);
    expect(screen.getByTestId('error-recovery').textContent).toContain('choose it again');
    expect(screen.getByRole('link', { name: 'Back to projects' }).getAttribute('href')).toBe('/connect');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('keeps transient failures to retry only', () => {
    render(<ErrorState error={apiError(503, 'SUBSYSTEM_UNAVAILABLE', 'down')} onRetry={() => {}} />);
    expect(screen.queryByRole('link', { name: 'Back to projects' })).toBeNull();
    expect(describeError(new TypeError('Failed to fetch')).recovery).toBe('Check your connection, then retry.');
  });
});

describe('approval inbox explains who decides and what is allowed', () => {
  const approval = (over: Record<string, unknown>) => ({
    id: 'apv_1', project_id: 'prj_1', subject_type: 'remediation', subject_id: 'asm_1', status: 'pending',
    requested_by: 'usr_me', requested_at: '2026-10-27T00:00:00Z', decided_by: null, decided_at: null, reason: null, ...over,
  });

  it('names the subject in plain words and says why the requester cannot approve', () => {
    state.approvals = [approval({})];
    render(<ApprovalsPage />);
    expect(screen.getByText('Candidate fix')).toBeTruthy();
    expect(screen.getByText(/requested by you/)).toBeTruthy();
    expect(screen.getByTestId('approval-self-note').textContent).toContain('another project member must approve it');
    expect((screen.getByRole('button', { name: 'Approve remediation' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('lets a different member approve without the self-approval note', () => {
    state.approvals = [approval({ requested_by: 'usr_other', subject_type: 'pr' })];
    render(<ApprovalsPage />);
    expect(screen.getByText('Pull request for a verified fix')).toBeTruthy();
    expect(screen.queryByTestId('approval-self-note')).toBeNull();
    expect((screen.getByRole('button', { name: 'Approve pr' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('describes the execution boundary when nothing is pending and delivery is manual', () => {
    render(<ApprovalsPage />);
    expect(screen.getByTestId('approval-delivery-note').textContent).toContain('Ari asks here before it builds a candidate fix');
    expect(screen.getByText(/never merges or deploys on its own/)).toBeTruthy();
  });

  it('does not promise a human decision when standing owner authorization delivers fixes as pull requests', () => {
    state.deliveryMode = 'pull_request';
    authority({ configured_mode: 'pull_request', effective_mode: 'pull_request', state: 'active', authorized_by: 'usr_owner' });
    render(<ApprovalsPage />);
    const note = screen.getByTestId('approval-delivery-note').textContent ?? '';
    expect(note).not.toContain('asks here before it builds');
    expect(note).toContain('approved automatically');
    expect(note).toContain('pull request');
    expect(note).toContain('never merges or deploys');
  });

  it('says that auto-commit delivery pushes without a human check', () => {
    state.deliveryMode = 'auto';
    authority({ configured_mode: 'auto', effective_mode: 'auto', state: 'active', authorized_by: 'usr_owner' });
    render(<ApprovalsPage />);
    const note = screen.getByTestId('approval-delivery-note').textContent ?? '';
    expect(note).toContain('approved automatically');
    expect(note).toContain('default branch');
    expect(note).not.toContain('never merges');
  });

  it('does not claim automatic approval when pull request mode has no recorded authorizer (inactive)', () => {
    state.deliveryMode = 'pull_request';
    authority({ configured_mode: 'pull_request', effective_mode: 'manual', state: 'inactive', authorized_by: null });
    render(<ApprovalsPage />);
    const note = screen.getByTestId('approval-delivery-note');
    expect(note.getAttribute('data-state')).toBe('inactive');
    expect(note.textContent).not.toContain('candidate fixes are approved automatically');
    expect(note.textContent).toContain('no owner has authorized it');
    expect(note.textContent).toContain('Ari asks here before it builds a candidate fix');
  });

  it('says a demoted authorizer ended automatic approval (revoked)', () => {
    state.deliveryMode = 'pull_request';
    authority({ configured_mode: 'pull_request', effective_mode: 'manual', state: 'revoked', authorized_by: 'usr_former' });
    render(<ApprovalsPage />);
    const note = screen.getByTestId('approval-delivery-note').textContent ?? '';
    expect(note).not.toContain('candidate fixes are approved automatically');
    expect(note).toContain('no longer an owner');
    expect(note).toContain('Ari asks here before it builds a candidate fix');
  });

  it('says direct commits become pull requests when the deployment forbids them', () => {
    state.deliveryMode = 'auto';
    authority({ configured_mode: 'auto', effective_mode: 'pull_request', state: 'active', authorized_by: 'usr_owner', direct_commits_allowed: false });
    render(<ApprovalsPage />);
    const note = screen.getByTestId('approval-delivery-note').textContent ?? '';
    expect(note).toContain('does not allow direct commits');
    expect(note).toContain('arrives as a pull request');
    expect(note).not.toContain('committed to the default branch with no human check');
  });

  it('makes no authorization claim while the effective authority is loading', () => {
    state.deliveryMode = 'pull_request';
    state.authority = { status: 'loading', data: null, error: undefined };
    render(<ApprovalsPage />);
    const note = screen.getByTestId('approval-delivery-note');
    expect(note.getAttribute('data-state')).toBe('loading');
    expect(note.textContent).not.toContain('approved automatically on behalf');
    expect(note.textContent).not.toContain('Ari asks here before');
  });

  it('says it cannot tell when the effective authority failed to load', () => {
    state.deliveryMode = 'pull_request';
    state.authority = { status: 'error', data: null, error: apiError(503, 'SUBSYSTEM_UNAVAILABLE', 'down') };
    render(<ApprovalsPage />);
    const note = screen.getByTestId('approval-delivery-note');
    expect(note.getAttribute('data-state')).toBe('error');
    expect(note.textContent).toContain('cannot say whether candidate fixes are approved automatically');
    expect(note.textContent).not.toContain('approved automatically on behalf');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('labels an auto-approved remediation as automatic, not as a decision you made', () => {
    state.deliveryMode = 'pull_request';
    state.approvals = [approval({ requested_by: null, status: 'approved', decided_by: 'usr_me', decided_at: '2026-10-27T00:01:00Z', reason: 'auto delivery: pre-approved by the project owner' })];
    render(<ApprovalsPage />);
    const decided = screen.getByTestId('approval-decided').textContent ?? '';
    expect(decided).toContain('Approved automatically');
    expect(decided).toContain('on your behalf');
    expect(decided).not.toMatch(/Decided by you/);
  });
});

describe('findings before the first review', () => {
  it('says no review has run and links to start one', () => {
    render(<FindingsPage />);
    expect(screen.getByTestId('findings-no-review').textContent).toContain('Ari has not reviewed acme/payments yet');
    expect(screen.getByRole('link', { name: 'Run a review' }).getAttribute('href')).toBe('/projects/prj_1/commits?repo=rep_1#start-assessment');
  });

  it('keeps the normal empty state once a review has run', () => {
    state.assessments = [{ id: 'asm_1', repo_id: 'rep_1' }];
    render(<FindingsPage />);
    expect(screen.queryByTestId('findings-no-review')).toBeNull();
    expect(screen.getByText('No open findings in this list.')).toBeTruthy();
  });
});
