import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AssessmentDetail } from '@hackatl/frontend-transport';
const state = vi.hoisted(() => ({ detail: null as AssessmentDetail | null, cancel: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: () => ({ cancelAssessment: state.cancel }) }));
vi.mock('@/lib/use-resource', () => ({ useResource: () => state.detail ? { status: 'ready', data: state.detail, reload: vi.fn() } : { status: 'loading', reload: vi.fn() } }));
import { belongsToCommit, CommitRunWorkspace } from '@/components/commit-run-workspace';
const detail = (repo = 'repo_one', sha = 'abc'): AssessmentDetail => ({ assessment: { id: 'asm_one', repo_id: repo, commit_sha: sha, state: 'COMPLETED', project_id: 'prj_one' }, agent_runs: [], findings: [] } as unknown as AssessmentDetail);
beforeEach(() => { state.detail = null; vi.stubGlobal('matchMedia', () => ({ matches: false })); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('rejects both sibling repositories and other commits', () => {
  expect(belongsToCommit(detail(), 'repo_one', 'abc')).toBe(true);
  expect(belongsToCommit(detail('repo_two'), 'repo_one', 'abc')).toBe(false);
  expect(belongsToCommit(detail('repo_one', 'def'), 'repo_one', 'abc')).toBe(false);
  state.detail = detail('repo_two');
  render(<CommitRunWorkspace assessmentId="asm_one" repoId="repo_one" sha="abc" fullName="acme/one" />);
  expect(screen.getByRole('alert').textContent).toContain('another repository or commit');
  expect(screen.queryByTestId('commit-reviewer')).toBeNull();
});

it('shows an honest empty state instead of inventing work when nothing has run', () => {
  render(<CommitRunWorkspace assessmentId={null} repoId="repo_one" sha="abc" fullName="acme/one" />);
  expect(screen.getByText(/Nothing to review yet/)).toBeTruthy();
  fireEvent.click(screen.getByRole('tab', { name: '7 agents' }));
  expect(screen.getByTestId('commit-agent-map').textContent).toContain('No replay yet');
});

it('can cancel the selected active run without leaving the commit', async () => {
  state.detail = detail();
  state.detail.assessment.state = 'FUNCTIONAL_REVIEW';
  state.cancel.mockResolvedValue({ cancelled: false });
  render(<CommitRunWorkspace assessmentId="asm_one" repoId="repo_one" sha="abc" fullName="acme/one" />);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel assessment' }));
  expect((await screen.findByTestId('cancel-assessment-result')).textContent).toContain('Stop requested');
  expect(state.cancel).toHaveBeenCalledWith('asm_one');
});

it('plays the recorded tool calls back on the sector map in saved order', () => {
  state.detail = detail();
  state.detail.agent_runs = [{
    assessment_id: 'asm_one', agent: 'scout', step_key: 'recon', status: 'succeeded',
    started_at: '2026-01-01T00:00:00Z', ended_at: '2026-01-01T00:00:10Z', provider_mode: 'fake',
    tool_calls: [
      { turn: 0, tool_name: 'first_saved_call', outcome: 'ok', error_code: null, duration_ms: 5 },
      { turn: 1, tool_name: 'second_saved_call', outcome: 'ok', error_code: null, duration_ms: 5 },
    ],
  }] as unknown as AssessmentDetail['agent_runs'];
  render(<CommitRunWorkspace assessmentId="asm_one" repoId="repo_one" sha="abc" fullName="acme/one" />);
  fireEvent.click(screen.getByRole('tab', { name: '7 agents' }));
  const map = screen.getByTestId('commit-agent-map');
  expect(map.querySelectorAll('nav[aria-label="Agents in orchestrator order"] button')).toHaveLength(7);
  const now = screen.getByTestId('demo-agent-now');
  expect(now.textContent).toContain('first_saved_call');
  fireEvent.change(screen.getByRole('slider', { name: 'Replay position' }), { target: { value: '1' } });
  expect(now.textContent).toContain('second_saved_call');
});
