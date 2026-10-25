import type { Finding } from '@hackatl/frontend-transport';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApprovalGate } from '@/components/assessment-approval-gate';

let mockApi: Record<string, unknown> = {};
vi.mock('@/components/session', () => ({ useSession: () => ({ me: { user: { id: 'usr_bob' } } }) }));
vi.mock('@/lib/api', () => ({ api: () => mockApi, idempotencyKey: (p: string) => `${p}-key` }));

const finding = (id: string, title: string): Finding => ({
  id, assessment_id: 'asm_1', snapshot_id: null, commit_sha: 'a'.repeat(40), kind: 'security', title,
  status: 'diagnosed', severity: { level: 'medium', rationale: '', scored_by: 'agent' }, subject_refs: ['app.py'],
  expectation_ids: [], evidence: [{ id: 'ev1', kind: 'diff', path: 'app.py', note: null }], confidence: 0.8,
  produced_by_run_id: 'run_1', history: [], standards: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
} as unknown as Finding);

const approval = (id: string, subject: string) => ({
  id, project_id: 'prj_1', subject_type: 'remediation' as const, subject_id: subject, status: 'pending' as const,
  requested_by: 'agent:orchestrator', requested_at: '2026-01-01T00:00:00Z', decided_by: null, decided_at: null, reason: null,
});

afterEach(() => { cleanup(); mockApi = {}; });

it('names the finding for both fnd_ subjects and rmp_ plan subjects resolved via finding details', async () => {
  const functional = finding('fnd_func', 'Refunds can exceed the payment amount in total');
  const security = finding('fnd_sec', 'Payment detail route may not enforce payment ownership');
  mockApi = {
    pendingApprovals: async () => ({ items: [approval('apv_plan', 'rmp_sec'), approval('apv_fnd', 'fnd_func')] }),
    decideApproval: vi.fn(),
    finding: async (id: string) => ({
      finding: id === 'fnd_sec' ? security : functional,
      records: id === 'fnd_sec' ? [{ type: 'remediation_plan', record: { id: 'rmp_sec', finding_id: 'fnd_sec', strategy: '', constraints: [], files_expected: [], test_plan: '' } }] : [],
    }),
  };
  render(<ApprovalGate projectId="prj_1" events={[{ approval_id: 'apv_plan' }, { approval_id: 'apv_fnd' }] as never[]} findings={[security, functional]} onDecided={() => {}} />);
  await waitFor(() => expect(screen.getByText(/Refunds can exceed the payment amount/)).toBeTruthy());
  await waitFor(() => expect(screen.getByText(/Payment detail route may not enforce/)).toBeTruthy());
  expect(screen.queryByText('Fix request (remediation)')).toBeNull();
});

it('keeps the subject id when the finding cannot be resolved', async () => {
  mockApi = {
    pendingApprovals: async () => ({ items: [approval('apv_x', 'rmp_unknown')] }),
    decideApproval: vi.fn(),
    finding: async () => ({ finding: finding('fnd_x', 'Other'), records: [] }),
  };
  render(<ApprovalGate projectId="prj_1" events={[{ approval_id: 'apv_x' }] as never[]} findings={[finding('fnd_x', 'Other')]} onDecided={() => {}} />);
  await waitFor(() => expect(screen.getByText('Fix request (remediation)')).toBeTruthy());
});
