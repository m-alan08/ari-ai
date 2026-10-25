import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Executions } from '@/components/live-defense/executions';
import type {
  FirstResponderDecision,
  FirstResponderDiagnostics,
  FirstResponderInvocationView,
  InvestigationSummary,
  InvestigationTimeline,
} from '@hackatl/frontend-transport';

const resources = vi.hoisted(() => ({
  list: { status: 'ready' as const, data: { items: [] as InvestigationSummary[], next_cursor: null }, error: undefined, reload: vi.fn() },
  timeline: { status: 'ready' as const, data: null as InvestigationTimeline | null, error: undefined, reload: vi.fn() },
  diagnostics: { status: 'ready' as const, data: null as unknown as FirstResponderDiagnostics, error: undefined, reload: vi.fn() },
}));

vi.mock('@/lib/use-resource', () => ({
  useResource: (key: string | null) => {
    if (key === null) return { status: 'loading', data: undefined, error: undefined, reload: vi.fn() };
    if (key.startsWith('ld-inv-list:')) return resources.list;
    if (key.startsWith('ld-inv-tl:')) return resources.timeline;
    return { status: 'loading', data: undefined, error: undefined, reload: vi.fn() };
  },
}));

vi.mock('@/lib/api', () => ({
  api: () => ({
    firstResponderInvestigations: vi.fn(),
    firstResponderInvestigation: vi.fn(),
    firstResponderDiagnostics: vi.fn(),
    artifactText: vi.fn(),
  }),
}));

const DECISION: FirstResponderDecision = {
  id: 'frd_aaaaaaaaaaaaaaaa', project_id: 'prj_1', application_id: 'app_1',
  created_at: '2026-10-24T10:00:00Z', orchestrator_version: '7a.1.1',
  intent_mode: 'monitor', effective_mode: 'monitor', action: 'investigate',
  evidence_state: 'suspected', trigger_event_ids: ['evt_aaaaaaaaaaaaaaaa'],
  live_policy_decision_id: null, incident_id: null,
  selected_agents: ['scout', 'diagnose'],
  blocked_agents: [{ name: 'patch', reason: 'no candidate patch', capability_class: 'code_changing' }],
  correlation: { application_id: 'app_1', mapping_id: null, commit_sha: null, confidence: 'unknown', finding_ids_at_commit: [], finding_is_not_proof_of_this_request: true, note: 'n' },
  confirmation: 'unverified', llm_prose_is_not_confirmation: true,
  recommended_control: null, recommended_is_not_executed: true, executed: false,
  auto_execute: false, simulated: false, budget_usd_remaining: 3.0, deadline_at: null,
  retry_count: 0, loop_count: 0, cancelled: false, cancel_reason: null,
  transition: 'test', outcome_label: null, predicted_probability: null, q_values: null,
  caveat: 'test caveat',
};

function invocation(overrides: Partial<FirstResponderInvocationView> & { status?: string } = {}): FirstResponderInvocationView {
  const { status = 'queued', ...rest } = overrides;
  return {
    invocation: {
      id: 'fri_aaaaaaaaaaaaaaaa', decision_id: DECISION.id, project_id: 'prj_1',
      agent: 'scout', capability_class: 'investigative_readonly',
      status: status as FirstResponderInvocationView['invocation']['status'],
      provider_mode: 'none', independent_of_patch: false, worktree_only: false,
      silent_deploy: false, authorized_scope_id: null, job_id: null, reason: null,
      created_at: '2026-10-24T10:00:01Z',
    },
    job: null, execution: null, execution_recorded: false,
    token_usage_available: false, token_usage_note: 'Per-invocation token usage is not persisted yet',
    ...rest,
  } as FirstResponderInvocationView;
}

const TIMELINE: InvestigationTimeline = {
  decision: DECISION,
  trigger_events: [{
    id: 'evt_aaaaaaaaaaaaaaaa', project_id: 'prj_1', repo_id: null, source: 'runtime',
    type: 'log_anomaly', occurred_at: '2026-10-24T09:59:00Z', received_at: '2026-10-24T09:59:05Z',
    dedupe_key: 'dk', payload_artifact_id: null, commit_range: null, ref: null, pr_number: null,
    simulated: false, status: 'processed', status_reason: null, impact: null,
  }],
  application: null, mapping: null, snapshot: null,
  invocations: [
    invocation({ status: 'skipped_no_snapshot' }),
    invocation({ status: 'failed', invocation: { ...invocation().invocation, id: 'fri_bbbbbbbbbbbbbbbb', agent: 'diagnose', status: 'failed' } }),
  ],
  handoff: null,
  final_state: 'failed',
  caveat: 'Persisted records only.',
};

const DIAGNOSTICS: FirstResponderDiagnostics = {
  generated_at: '2026-10-24T10:05:00Z', investigations_total: 3, decisions_cancelled: 1,
  invocations_by_status: { queued: 2, skipped_no_snapshot: 1, failed: 1 },
  failures_by_agent: { scout: 1 },
  missing_prerequisites: { skipped_no_snapshot: 1 },
  jobs_queued: 2, jobs_running: 0, jobs_stalled: 1, jobs_succeeded: 3, jobs_failed: 1,
  jobs_cancelled: 1, jobs_dead: 0,
  waiting_jobs: [{ job_id: 'job_aaaaaaaaaaaaaaaa', kind: 'live_defense_agent_step', run_after: null, enqueued_at: null, attempts: 1 }],
  recent_errors: [{ job_id: 'job_bbbbbbbbbbbbbbbb', kind: 'live_defense_agent_step', decision_id: DECISION.id, invocation_id: null, agent: 'scout', code: 'TIMEOUT', message: 'worker timed out', at: '2026-10-24T10:04:00Z' }],
  agent_step_latency: { samples: 4, avg_ms: 1200, max_ms: 4000 },
  cost_usd_total: null,
  cost_note: 'Per-invocation cost requires persisted execution records (Phase 7C).',
  notes: ['Stalled = running job whose lease expired.'],
};

const SUMMARY: InvestigationSummary = {
  decision_id: DECISION.id, created_at: DECISION.created_at, action: 'investigate',
  evidence_state: 'suspected', confirmation: 'unverified', application_id: 'app_1',
  simulated: false, cancelled: false, trigger_event_count: 1, invocations_total: 2,
  invocations_queued_or_running: 0, invocations_completed: 0, invocations_failed: 1,
  invocations_skipped: 1, invocations_cancelled: 0, handoff_state: null,
};

beforeEach(() => {
  resources.list = { status: 'ready', data: { items: [], next_cursor: null }, error: undefined, reload: vi.fn() };
  resources.timeline = { status: 'ready', data: TIMELINE, error: undefined, reload: vi.fn() };
  resources.diagnostics = { status: 'ready', data: DIAGNOSTICS, error: undefined, reload: vi.fn() };
});

afterEach(() => cleanup());

describe('Executions observability UI', () => {
  it('renders diagnostics with failed, skipped and stalled states, and explicit unavailable cost', () => {
    render(<Executions mode="live" projectId="prj_1" diagnostics={resources.diagnostics} />);
    const d = screen.getByTestId('ld-diagnostics');
    expect(d.textContent).toContain('skipped_no_snapshot: 1');
    expect(d.textContent).toContain('failed: 1');
    expect(d.textContent).toContain('scout: 1');
    expect(screen.getByTestId('ld-diag-cost').textContent).toContain('unavailable');
    expect(d.textContent).toContain('Phase 7C');
    expect(screen.getByTestId('ld-diag-errors').textContent).toContain('TIMEOUT');
    expect(screen.getByTestId('ld-diag-waiting').textContent).toContain('live_defense_agent_step');
  });

  it('lists investigations and renders the timeline with failed and skipped execution states', () => {
    resources.list = { status: 'ready', data: { items: [SUMMARY], next_cursor: null }, error: undefined, reload: vi.fn() };
    render(<Executions mode="live" projectId="prj_1" diagnostics={resources.diagnostics} />);
    fireEvent.click(screen.getByTestId(`ld-inv-row-${DECISION.id}`));
    const tl = screen.getByTestId('ld-inv-timeline');
    expect(tl.textContent).toContain('failed');
    expect(screen.getByTestId('ld-tl-events').textContent).toContain('evt_aaaaaaaaaaaaaaaa');
    expect(screen.getByTestId('ld-tl-blocked-patch').textContent).toContain('no candidate patch');
    const detail = screen.getByTestId('ld-inv-detail-fri_aaaaaaaaaaaaaaaa');
    expect(detail.textContent).toContain('skipped_no_snapshot');
    expect(detail.textContent).toContain('No execution record persisted');
  });

  it('renders unknown execution fields explicitly as unavailable, never fabricated', () => {
    resources.list = { status: 'ready', data: { items: [SUMMARY], next_cursor: null }, error: undefined, reload: vi.fn() };
    render(<Executions mode="live" projectId="prj_1" diagnostics={resources.diagnostics} />);
    fireEvent.click(screen.getByTestId(`ld-inv-row-${DECISION.id}`));
    const detail = screen.getByTestId('ld-inv-detail-fri_bbbbbbbbbbbbbbbb');
    expect(detail.textContent).toContain('unavailable');
    expect(detail.textContent).not.toContain('tokens used');
  });

  it('surfaces recorded execution fields and artifact navigation when the record exists', () => {
    const withExec = invocation({
      status: 'executed',
      execution_recorded: true,
      execution: {
        provider_mode: 'fake', model_id: 'claude-haiku-5-5', cost_usd: 0.0042,
        latency_ms: 1500, llm_turns: 2, tool_call_count: 3,
        started_at: '2026-10-24T10:00:02Z', ended_at: '2026-10-24T10:00:04Z',
        transcript_artifact_id: 'art_aaaaaaaaaaaaaaaa', output_artifact_id: 'art_bbbbbbbbbbbbbbbb',
        error_code: null, typed_output: true, hypothesis_count: 2,
        execution_integrity: 'verified', independent_verification_id: null,
        evidence_verified: true, confirmation: 'unverified', predicted_probability: null,
        execution_is_not_confirmation: true,
      },
    });
    resources.timeline = { status: 'ready', data: { ...TIMELINE, invocations: [withExec] }, error: undefined, reload: vi.fn() };
    resources.list = { status: 'ready', data: { items: [SUMMARY], next_cursor: null }, error: undefined, reload: vi.fn() };
    render(<Executions mode="live" projectId="prj_1" diagnostics={resources.diagnostics} />);
    fireEvent.click(screen.getByTestId(`ld-inv-row-${DECISION.id}`));
    const detail = screen.getByTestId('ld-inv-detail-fri_aaaaaaaaaaaaaaaa');
    expect(detail.textContent).toContain('claude-haiku-5-5');
    expect(detail.textContent).toContain('$0.0042');
    expect(detail.textContent).toContain('recorded');
    expect(detail.textContent).toContain('art_aaaaaaaaaaaaaaaa');
    expect(detail.textContent).toContain('art_bbbbbbbbbbbbbbbb');
  });

  it('renders cancelled investigations distinctly', () => {
    const cancelledSummary: InvestigationSummary = { ...SUMMARY, decision_id: 'frd_cccccccccccccccc', cancelled: true, invocations_cancelled: 2 };
    resources.list = { status: 'ready', data: { items: [cancelledSummary], next_cursor: null }, error: undefined, reload: vi.fn() };
    resources.timeline = { status: 'ready', data: { ...TIMELINE, final_state: 'cancelled', decision: { ...DECISION, cancelled: true, cancel_reason: 'mode_off' } }, error: undefined, reload: vi.fn() };
    render(<Executions mode="live" projectId="prj_1" diagnostics={resources.diagnostics} />);
    expect(screen.getByTestId('ld-inv-row-frd_cccccccccccccccc').textContent).toContain('cancelled');
    fireEvent.click(screen.getByTestId('ld-inv-row-frd_cccccccccccccccc'));
    expect(screen.getByTestId('ld-tl-final').textContent).toContain('cancelled');
    expect(screen.getByTestId('ld-tl-final').textContent).toContain('mode_off');
  });

  it('shows the error state and keeps the empty prompt when the timeline fails', () => {
    resources.timeline = { status: 'error', data: undefined, error: new Error('boom'), reload: vi.fn() } as never;
    render(<Executions mode="live" projectId="prj_1" diagnostics={resources.diagnostics} />);
    expect(screen.getByText(/Select an investigation/)).toBeTruthy();
  });

  it('layout-preview mode renders sample chrome without loading', () => {
    render(<Executions mode="layout-preview" projectId="prj_1" />);
    expect(screen.getByText(/Sample layout only/)).toBeTruthy();
    expect(screen.queryByTestId('ld-inv-timeline')).toBeNull();
  });
});
