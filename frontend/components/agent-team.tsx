'use client';

import type { AgentName, AgentRun, AssessmentState } from '@hackatl/frontend-transport';
import { useState } from 'react';
import { Badge, Empty, Mono, Panel, Tag } from '@/components/ui';
import { duration, usd } from '@/lib/format';
import { AGENTS, AGENT_RUN_STATUS, agentRole, agentRunSummary, handoffNote, providerLabel, TERMINAL_STATES } from '@/lib/status';

/** Label for an agent with no recorded run, from the assessment's final state. */
export function absentLabel(state: AssessmentState | undefined): { short: string; detail: string } {
  if (state === 'COMPLETED') return { short: 'not needed', detail: 'Not needed this run — the assessment ended before this step had work' };
  if (state && TERMINAL_STATES.has(state)) return { short: 'not reached', detail: 'Not reached — the run ended before this step' };
  return { short: 'not run', detail: 'No persisted run' };
}

/** Selectable team view. Every row, outcome, and tool line comes from persisted runs. */
export function AgentTeam({ runs, selected: controlled, onSelect, hideList = false, state, skipNote }: { runs: AgentRun[]; selected?: AgentName; onSelect?: (agent: AgentName) => void; hideList?: boolean; state?: AssessmentState; skipNote?: string | null }) {
  const withRuns = AGENTS.filter(role => runs.some(run => run.agent === role.name));
  const [own, setOwn] = useState<AgentName>(withRuns[0]?.name ?? 'scout');
  const selected = controlled ?? own;
  const setSelected = onSelect ?? setOwn;
  const role = agentRole(selected);
  const index = AGENTS.findIndex(item => item.name === selected);
  const next = index >= 0 && index < AGENTS.length - 1 ? AGENTS[index + 1] : null;
  const mine = runs.filter(run => run.agent === selected);
  const nextRuns = next ? runs.filter(run => run.agent === next.name) : null;

  return (
    <div className={`grid gap-4 ${hideList ? 'lg:grid-cols-[1fr_16rem]' : 'lg:grid-cols-[16rem_1fr_16rem]'}`} data-testid="agent-team">
      {!hideList && <ol aria-label="Agents" className="flex flex-col gap-1">
        {AGENTS.map(item => {
          const mine = runs.filter(run => run.agent === item.name);
          const latest = mine[mine.length - 1];
          const pressed = item.name === selected;
          return (
            <li key={item.name}>
              <button
                type="button"
                aria-pressed={pressed}
                data-testid={`agent-${item.name}`}
                onClick={() => setSelected(item.name)}
                className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${pressed ? 'border-ink bg-ink/5' : 'border-line bg-panel-2 hover:border-ink/30'}`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="font-semibold">{item.label}</span>
                  <span className="text-xs text-faint">{latest ? AGENT_RUN_STATUS[latest.status].label : absentLabel(state).short}</span>
                </span>
                <span className="mt-0.5 block text-xs text-muted">{item.job}</span>
              </button>
            </li>
          );
        })}
      </ol>}
      <Panel title={role.label} actions={<span className="text-xs text-muted">{role.team}</span>}>
        <p className="text-sm">{role.job}</p>
        <p className="mt-2 text-sm text-muted" data-testid="agent-outcome">{agentRunSummary(mine)}</p>
        {mine.length === 0 ? <Empty>{absentLabel(state).detail} for {role.label}.{skipNote ? ` The run recorded: ${skipNote}.` : ''}</Empty> : mine.map(run => <RunDetail key={run.id} run={run} />)}
      </Panel>
      <Panel title="Handoff">
        <p className="text-sm">{role.handsTo}</p>
        <p className="mt-3 text-sm text-muted" data-testid="agent-handoff">{handoffNote(mine, nextRuns)}</p>
        {next && <p className="mt-3 text-xs text-faint">Next: {next.label}. {next.job}</p>}
      </Panel>
    </div>
  );
}

export function RunDetail({ run }: { run: AgentRun }) {
  const provider = providerLabel(run.provider_mode);
  const denied = run.tool_calls.filter(call => call.outcome === 'denied').length;
  const errors = run.tool_calls.filter(call => call.outcome === 'error').length;
  return (
    <article className="mt-3 border-t border-line pt-3 text-xs" data-testid={`agent-run-${run.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge view={AGENT_RUN_STATUS[run.status]} />
        <Badge view={provider} />
        <Mono>{run.step_key}</Mono>
        {run.attempt > 1 && <Tag tone="warn">attempt {run.attempt}</Tag>}
      </div>
      <p className="mt-1 text-muted">
        {duration(run.started_at, run.ended_at)} · {(run.usage.input_tokens + run.usage.output_tokens).toLocaleString()} tokens · {usd(run.usage.usd)} · model <Mono>{run.model_id ?? '—'}</Mono>
      </p>
      {run.error && <p className="mt-1 text-bad">{run.error.code}: {run.error.message}</p>}
      {denied > 0 && <p className="mt-1 text-warn">{denied} tool call{denied === 1 ? '' : 's'} denied by policy.</p>}
      {errors > 0 && <p className="mt-1 text-bad">{errors} tool call{errors === 1 ? '' : 's'} errored.</p>}
      {run.tool_calls.length === 0 ? <p className="mt-2 text-faint">No tool calls recorded for this run.</p> : (
        <ol className="mt-2 flex flex-col gap-1" aria-label="Tool calls">
          {run.tool_calls.map((call, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2 border-t border-line/60 py-1">
              <span className="text-faint">turn {call.turn}</span>
              <Mono>{call.tool_name}</Mono>
              <span className={call.outcome === 'ok' ? 'text-ok' : call.outcome === 'denied' ? 'text-warn' : 'text-bad'}>{call.outcome}{call.error_code ? ` (${call.error_code})` : ''}</span>
              <span className="text-faint">{call.duration_ms} ms</span>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}
