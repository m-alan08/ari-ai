import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import type { AgentRun } from '@hackatl/frontend-transport';
import { afterEach, expect, it } from 'vitest';
import { AgentRunList } from '@/components/agent-run-list';

afterEach(cleanup);

function run(id: string, agent: AgentRun['agent']): AgentRun {
  return {
    id,
    assessment_id: 'assessment-1',
    agent,
    step_key: 'review',
    attempt: 1,
    status: 'succeeded',
    provider_mode: 'fake',
    model_id: 'scripted-model',
    prompt_version: null,
    input_artifact_id: null,
    output_artifact_id: null,
    transcript_artifact_id: null,
    tool_calls: [{ turn: 0, tool_name: 'read_file', outcome: 'ok', error_code: null, duration_ms: 10, input_sha256: 'sha256', output_artifact_id: null }],
    usage: { input_tokens: 10, output_tokens: 20, cache_read_input_tokens: 0, usd: 0, sandbox_seconds: 1, llm_calls: 1, tool_calls: 1 },
    error: null,
    started_at: '2026-10-25T00:00:00Z',
    ended_at: '2026-10-25T00:00:10Z',
  };
}

it('expands the first agent with runs, permits multiple open rows, and renders persisted provider details', () => {
  render(<AgentRunList runs={[run('reviewer-run', 'reviewer'), run('scout-run', 'scout')]} state="COMPLETED" />);

  expect(screen.getByTestId('agent-team').querySelectorAll('button[data-testid^="agent-"]')).toHaveLength(8);
  expect(screen.getByTestId('agent-reviewer').getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByTestId('agent-scout').getAttribute('aria-expanded')).toBe('false');
  expect(screen.getByTestId('agent-run-reviewer-run')).toBeTruthy();
  expect(within(screen.getByTestId('agent-run-reviewer-run')).getByText('Test provider (scripted)')).toBeTruthy();
  expect(screen.getByTestId('agent-reviewer').textContent).toContain('1 run · 1 tool call');
  expect(screen.getByTestId('agent-run-scout-run').closest('[hidden]')).not.toBeNull();

  fireEvent.click(screen.getByTestId('agent-scout'));
  expect(screen.getByTestId('agent-reviewer').getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByTestId('agent-scout').getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByTestId('agent-run-scout-run')).toBeTruthy();
  expect(screen.getByTestId('agent-run-scout-run').closest('[hidden]')).toBeNull();

  fireEvent.click(screen.getByTestId('agent-reviewer'));
  expect(screen.getByTestId('agent-reviewer').getAttribute('aria-expanded')).toBe('false');
  expect(screen.getByTestId('agent-scout').getAttribute('aria-expanded')).toBe('true');
});
