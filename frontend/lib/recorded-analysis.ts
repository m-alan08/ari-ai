import type { AgentRun, AgentRunStatus, AssessmentDetail, Finding } from '@hackatl/frontend-transport';
import type { DemoComponent } from '@/lib/demo/data';
import { kindForPath } from '@/lib/demo/agent-map';
import { AGENT_ORDER, type AgentId, type AgentRunView, type Analysis, type DiffFile, type ToolStep } from '@/lib/demo/scenarios';
import { agentRunSummary, handoffNote } from '@/lib/status';

/**
 * Re-shape a recorded assessment run into the `Analysis` the sector map renders, so real
 * recorded work plays back on the same map as the quick read — never the other way around.
 * Tool-call records carry names, outcomes and durations only; what a step touched comes from
 * its orchestrator step key (`remediation:fnd_…:patch`) resolved to the finding's evidence path.
 */

export type SectorMapStatus = 'queued' | 'running' | 'done' | 'skipped' | 'failed';

const RUN_STATUS: Record<AgentRunStatus, SectorMapStatus> = {
  queued: 'queued',
  running: 'running',
  succeeded: 'done',
  failed: 'failed',
  cancelled: 'skipped',
};

/** step keys that name a finding: analysis:fnd_…, diagnosis:fnd_…, remediation:fnd_…:patch|verify, hardening:fnd_… */
const STEP_FINDING = /(?:^|:)(fnd_[A-Za-z0-9_-]+)(?::|$)/;

function stepFindingId(run: AgentRun): string | null {
  return run.step_key?.match(STEP_FINDING)?.[1] ?? null;
}

function findingPath(finding: Finding | undefined): string | undefined {
  return finding?.evidence.find(e => typeof e.path === 'string' && e.path.length > 0)?.path ?? undefined;
}

function kindFor(run: AgentRun, findings: Map<string, Finding>): string | undefined {
  const id = stepFindingId(run);
  const path = id ? findingPath(findings.get(id)) : undefined;
  return path ? kindForPath(path) : undefined;
}

const secondsBetween = (started: string, ended: string | null | undefined) =>
  ended ? Math.max(0, Math.round((Date.parse(ended) - Date.parse(started)) / 1000)) : 0;

function runSteps(run: AgentRun, findings: Map<string, Finding>): ToolStep[] {
  const on = kindFor(run, findings);
  return [...run.tool_calls]
    .sort((a, b) => a.turn - b.turn)
    .map(call => ({
      tool: call.tool_name,
      input: run.step_key ?? '',
      result: call.error_code ?? call.outcome ?? 'ok',
      ms: call.duration_ms ?? 0,
      outcome: call.outcome === 'denied' ? 'denied' as const : 'ok' as const,
      ...(on ? { on } : {}),
    }));
}

/** Runs of one of the seven agents, oldest first. */
export function runsByAgent(detail: AssessmentDetail): Map<AgentId, AgentRun[]> {
  const map = new Map<AgentId, AgentRun[]>();
  for (const role of AGENT_ORDER) {
    map.set(role.agent, detail.agent_runs
      .filter(r => r.agent === role.agent)
      .sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at)));
  }
  return map;
}

function agentView(agent: AgentId, runs: AgentRun[], findings: Map<string, Finding>, nextRuns: AgentRun[] | null): AgentRunView {
  const role = AGENT_ORDER.find(a => a.agent === agent)!;
  const latest = runs.at(-1);
  const produced = new Set(runs.map(r => r.id));
  const evidence = [...findings.values()].filter(f => f.produced_by_run_id !== null && produced.has(f.produced_by_run_id)).map(f => f.title);
  return {
    ...role,
    status: runs.length ? 'done' : 'skipped',
    outcome: latest ? agentRunSummary([latest]) : 'Has not run.',
    evidence,
    steps: runs.flatMap(r => runSteps(r, findings)),
    handoff: handoffNote(runs, nextRuns),
    seconds: latest ? secondsBetween(latest.started_at, latest.ended_at) : 0,
    tokens: runs.reduce((n, r) => n + (r.usage?.input_tokens ?? 0) + (r.usage?.output_tokens ?? 0), 0),
  };
}

/** Real per-agent status for the rail: the latest recorded run, else queued or skipped by run state. */
export function recordedStatuses(detail: AssessmentDetail, terminal: boolean): Record<AgentId, SectorMapStatus> {
  const byAgent = runsByAgent(detail);
  const out = {} as Record<AgentId, SectorMapStatus>;
  for (const role of AGENT_ORDER) {
    const latest = byAgent.get(role.agent)?.at(-1);
    out[role.agent] = latest ? RUN_STATUS[latest.status] : terminal ? 'skipped' : 'queued';
  }
  return out;
}

/** A minimal Analysis view of the recorded run for the shared map's issue/refute colouring. */
export function recordedAnalysis(detail: AssessmentDetail, repo: { components: DemoComponent[] }, diff: DiffFile[]): Analysis {
  const findings = new Map(detail.findings.map(f => [f.id, f]));
  const byAgent = runsByAgent(detail);
  const order = AGENT_ORDER.map(r => r.agent);
  const agents = order.map((agent, i) => agentView(agent, byAgent.get(agent) ?? [], findings, byAgent.get(order[i + 1]!) ?? null));
  const on = (f: Finding) => kindForPath(findingPath(f) ?? '') ?? 'service';
  return {
    headline: '',
    summary: '',
    seconds: 0,
    usd: 0,
    findings: detail.findings
      .filter(f => f.status !== 'refuted')
      .map(f => ({
        id: f.id, title: f.title, kind: f.kind, cwe: f.standards?.cwe?.id ?? '',
        severity: (['critical', 'high', 'medium', 'low'] as const).find(s => s === f.severity?.level) ?? 'low',
        cvss: f.severity?.cvss_v31?.base_score != null ? String(f.severity.cvss_v31.base_score) : '—',
        where: findingPath(f) ?? f.subject_refs[0] ?? '',
        on: on(f),
      })),
    falseAlarms: detail.findings
      .filter(f => f.status === 'refuted')
      .map(f => ({ on: on(f), text: f.title })),
    review: { intent: '', expectations: [], diff, comments: [], tests: [], verdict: '' },
    agents,
    patch: null,
    defense: { watch: {}, checkedMinutesAgo: {}, recommendation: { action: '', target: '', why: '', confidence: 0 }, events: [] },
  };
}

/** Sectors for the map when no quick read loaded: the app plus one node per kind the findings point at. */
export function fallbackSectors(fullName: string, detail: AssessmentDetail): DemoComponent[] {
  const kinds = new Set<string>(['service']);
  for (const f of detail.findings) {
    const kind = kindForPath(findingPath(f) ?? '');
    if (kind) kinds.add(kind);
  }
  const labels: Record<string, string> = {
    service: fullName.split('/')[1] ?? 'Application', site: 'Web app', data: 'Data store',
    logging: 'Logging', identity: 'Identity', edge: 'Edge', worker: 'Worker', endpoints: 'Endpoints',
  };
  const order = ['edge', 'service', 'site', 'identity', 'worker', 'data', 'logging', 'endpoints'];
  return order.filter(k => kinds.has(k)).map(k => ({ id: k, label: labels[k] ?? k, kind: k, critical: k === 'service' }));
}
