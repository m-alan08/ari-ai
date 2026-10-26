'use client';

import type { AgentName, AgentRun, AssessmentState } from '@hackatl/frontend-transport';
import { useId, useState } from 'react';
import { absentLabel, RunDetail } from '@/components/agent-team';
import { AGENTS, AGENT_RUN_STATUS, agentRunSummary, handoffNote } from '@/lib/status';

export function AgentRunList({ runs, state }: { runs: AgentRun[]; state?: AssessmentState }) {
  const id = useId();
  const firstWithRuns = AGENTS.find(role => runs.some(run => run.agent === role.name))?.name;
  const [expanded, setExpanded] = useState<Set<AgentName>>(() => new Set(firstWithRuns ? [firstWithRuns] : []));

  const toggle = (name: AgentName) => {
    setExpanded(current => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <ul data-testid="agent-team" className="divide-y divide-line">
      {AGENTS.map((role, index) => {
        const mine = runs.filter(run => run.agent === role.name);
        const latest = mine[mine.length - 1];
        const nextRole = AGENTS[index + 1];
        const nextRuns = nextRole ? runs.filter(run => run.agent === nextRole.name) : null;
        const isExpanded = expanded.has(role.name);
        const detailsId = `agent-run-details-${id}-${role.name}`;
        const toolCalls = mine.reduce((total, run) => total + run.tool_calls.length, 0);

        return (
          <li key={role.name} className={`border-l-2 border-transparent ${isExpanded ? 'border-brand bg-brand/5' : ''}`}>
            <button
              type="button"
              className="flex w-full items-start justify-between gap-4 px-3 py-3 text-left hover:bg-panel-2/60"
              data-testid={`agent-${role.name}`}
              aria-expanded={isExpanded}
              aria-controls={detailsId}
              onClick={() => toggle(role.name)}
            >
              <span className="min-w-0">
                <span className="block font-semibold">{role.label}</span>
                <span className="mt-0.5 block text-xs text-muted">{role.job}</span>
              </span>
              <span className="shrink-0 text-right">
                <span className="block text-xs text-faint">{latest ? AGENT_RUN_STATUS[latest.status].label : absentLabel(state).short}</span>
                {mine.length > 0 && <span className="mt-0.5 block text-xs text-muted">{mine.length} {mine.length === 1 ? 'run' : 'runs'} · {toolCalls} {toolCalls === 1 ? 'tool call' : 'tool calls'}</span>}
              </span>
            </button>
            <div id={detailsId} hidden={!isExpanded} className="px-4 pb-4 pt-1">
              <p className="text-sm text-muted" data-testid="agent-outcome">{agentRunSummary(mine)}</p>
              {mine.map(run => <RunDetail key={run.id} run={run} />)}
              <p className="mt-3 text-xs text-muted">Hands off to: {role.handsTo} {handoffNote(mine, nextRuns)}</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
