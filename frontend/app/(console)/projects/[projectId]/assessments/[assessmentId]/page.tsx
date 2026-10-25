'use client';

import { watchAssessment, type AgentName, type AgentRun, type Approval, type AssessmentDetail, type AssessmentState, type ConnectionState, type StreamEvent } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AgentRelay } from '@/components/agent-relay';
import { AgentTeam } from '@/components/agent-team';
import { Term } from '@/components/term';
import { useParam, useProject } from '@/components/project';
import { ApprovalGate } from '@/components/assessment-approval-gate';
import { Badge, Empty, ErrorState, Loading, Mono, PageTitle, Panel, Stat, Tag, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { CancelButton } from '@/components/assessment-cancel';
import { shortSha, usd } from '@/lib/format';
import { assessmentPhases, EVENT_LOG_PAGE_NOTE, fullEventLog, JOURNEY_STATUS } from '@/lib/journey';
import { currentPhaseSentence } from '@/lib/phase';
import { agentRelay, earlyEndNote, providerNote } from '@/lib/relay';
import { useResource } from '@/lib/use-resource';
import {
  agentRole, ASSESSMENT_STATE, FINDING_STATUS, providerLabel,
  railSteps, SEVERITY, TERMINAL_STATES,
} from '@/lib/status';

const CONNECTION: Record<ConnectionState, { label: string; tone: 'ok' | 'warn' | 'bad' | 'info' | 'neutral' }> = {
  connecting: { label: 'Connecting to event stream', tone: 'info' },
  connected: { label: 'Live: receiving persisted events', tone: 'ok' },
  reconnecting: { label: 'Reconnecting (resumes from last event)', tone: 'warn' },
  completed: { label: 'Stream complete: run reached a terminal state', tone: 'neutral' },
  aborted: { label: 'Stream closed', tone: 'neutral' },
  failed: { label: 'Stream failed', tone: 'bad' },
};

export default function AssessmentPage() {
  const id = useParam('assessmentId');
  const { project } = useProject();
  const [detail, setDetail] = useState<AssessmentDetail | null>(null);
  const [detailError, setDetailError] = useState<unknown>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [streamError, setStreamError] = useState<unknown>(null);
  const [streamNonce, setStreamNonce] = useState(0);
  const [selectedAgent, setSelectedAgent] = useState<AgentName | null>(null);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const approvalsReq = useRef(0);
  const detailReq = useRef(0);
  const projectId = project.id;

  // The persisted approval records are the only evidence of who approved a fix.
  const loadApprovals = useCallback(() => {
    const rid = ++approvalsReq.current;
    api().approvals(projectId).then(
      page => { if (rid === approvalsReq.current) setApprovals(page.items); },
      () => { if (rid === approvalsReq.current) setApprovals(null); },
    );
  }, [projectId]);

  const loadDetail = useCallback((signal?: AbortSignal) => {
    // Only the newest request may write state: an older, slower response is dropped.
    const rid = ++detailReq.current;
    api().assessment(id, signal ? { signal } : {}).then(
      d => { if (rid !== detailReq.current) return; setDetail(d); setDetailError(null); loadApprovals(); },
      (err: unknown) => { if (!signal?.aborted && rid === detailReq.current) setDetailError(err); },
    );
  }, [id, loadApprovals]);

  useEffect(() => {
    const controller = new AbortController();
    loadDetail(controller.signal);
    return () => controller.abort();
  }, [loadDetail]);

  // Records change only when an event says so; coalesce bursts into one refetch.
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => loadDetail(), 250);
  }, [loadDetail]);

  useEffect(() => {
    const controller = new AbortController();
    let cursor = 0;
    (async () => {
      for await (const event of watchAssessment(api(), id, { after: cursor, signal: controller.signal, onConnection: setConnection })) {
        cursor = event.seq;
        setEvents(prev => (prev.some(e => e.seq === event.seq) ? prev : [...prev, event].sort((a, b) => a.seq - b.seq)));
        scheduleRefresh();
      }
      scheduleRefresh();
    })().catch((err: unknown) => {
      if (!controller.signal.aborted) setStreamError(err);
    });
    return () => controller.abort();
  }, [id, streamNonce, scheduleRefresh]);

  useEffect(() => () => { if (refreshTimer.current) clearTimeout(refreshTimer.current); }, []);

  // Polling fallback: persisted records stay current even if an intermediary buffers SSE.
  const running = detail !== null && !TERMINAL_STATES.has(detail.assessment.state);
  const phaseLog = useResource(
    `phase-log:${id}`,
    signal => fullEventLog(after => api().eventsLog(id, after, { signal })),
    running ? 5000 : undefined,
  );
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => loadDetail(), 5000);
    return () => clearInterval(t);
  }, [running, loadDetail]);

  const eventApprovalIds = new Set<string>();
  const allEvents = [...events, ...(phaseLog.status === 'ready' ? phaseLog.data.items : [])].sort((a, b) => a.seq - b.seq);
  for (const e of allEvents) if (e.approval_id) eventApprovalIds.add(e.approval_id);
  const relayApprovals = approvals === null ? null : approvals.filter(ap => eventApprovalIds.has(ap.id));
  const skipNote = earlyEndNote(allEvents);

  if (detailError && !detail) return <ErrorState error={detailError} onRetry={() => loadDetail()} context="Could not load assessment" />;
  if (!detail) return <Loading label="Loading assessment" />;
  const a = detail.assessment;
  const terminal = TERMINAL_STATES.has(a.state);

  return (
    <>
      <PageTitle
        title={<span className="flex flex-wrap items-center gap-2">Assessment <Mono className="text-base">{a.id}</Mono> <Badge view={ASSESSMENT_STATE[a.state]} /></span>}
        subtitle={<>Commit <Mono>{shortSha(a.commit_sha)}</Mono> · {a.trigger} trigger · scope <Mono>{a.scope_id}</Mono> · started <Time iso={a.created_at} />{a.completed_at && <> · ended <Time iso={a.completed_at} /></>}</>}
        actions={!terminal && <CancelButton id={a.id} onDone={() => loadDetail()} />}
      />
      {detailError !== null && <p role="status" className="mb-2 text-xs text-warn" data-testid="detail-stale">Could not refresh this assessment. Showing the last successfully loaded records, which may be out of date.</p>}
      <p className="mb-4 text-base text-ink" data-testid="assessment-phase">{currentPhaseSentence(a.state)}</p>
      {a.error && (
        <div role="alert" className="mb-4 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm">
          <p className="font-semibold text-bad">Assessment error · {a.error.code}</p>
          <p className="text-muted">{a.error.message}</p>
        </div>
      )}
      <ProviderSummary runs={detail.agent_runs} />
      {a.state === 'AWAITING_APPROVAL' && <ApprovalGate projectId={project.id} events={events} findings={detail?.findings ?? []} onDecided={() => loadDetail()} />}
      <div className="mt-4 rounded-xl border border-line bg-panel p-5">
        <AgentRelay
          items={agentRelay({ runs: detail.agent_runs, findings: detail.findings, state: a.state, approvals: relayApprovals, skipNote })}
          selected={selectedAgent ?? detail.agent_runs[0]?.agent ?? 'reviewer'}
          onSelect={setSelectedAgent}
        />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[2fr_3fr]">
        <Panel title={`What this check found (${detail.findings.length})`}>
          {detail.findings.length === 0 ? <Empty>{terminal ? 'No findings were recorded by this run.' : 'No findings recorded yet.'}</Empty> : (
            <ul className="divide-y divide-line">
              {detail.findings.map(f => (
                <li key={f.id} className="py-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Link href={`/projects/${project.id}/findings/${f.id}`} className="text-sm font-medium hover:text-accent">{f.title}</Link>
                    <Badge view={FINDING_STATUS[f.status]} />
                  </div>
                  <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                    <Tag tone={f.kind === 'security' ? 'bad' : 'info'}>{f.kind === 'security' ? 'security' : 'behaviour'}</Tag>
                    {f.severity && <Badge view={SEVERITY[f.severity.level]} />}
                    confidence {(f.confidence * 100).toFixed(0)}% · {f.evidence.length} evidence refs
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <section aria-label="Phases of this check">
          <h2 className="mb-2 text-sm font-semibold">Progress through the six phases</h2>
          <AssessmentPhases
            state={a.state}
            findingStatuses={detail.findings.map(finding => finding.status)}
            log={phaseLog}
          />
        </section>
      </div>
      <div className="mt-4">
        <h2 className="mb-2 text-sm font-semibold">What the selected agent did, step by step</h2>
        <AgentTeam runs={detail.agent_runs} selected={selectedAgent ?? detail.agent_runs[0]?.agent ?? 'reviewer'} onSelect={setSelectedAgent} hideList state={a.state} skipNote={skipNote} />
      </div>
      <details className="group mt-4 rounded-xl border border-line bg-panel" data-testid="technical-details" open={technicalOpen} onToggle={e => setTechnicalOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold marker:hidden">
          <span aria-hidden="true" className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
          Technical details: live connection, orchestrator states, usage and the full event log
        </summary>
        <div className="border-t border-line p-4">
          <ConnectionBar state={connection} error={streamError} onReconnect={() => { setStreamError(null); setStreamNonce(n => n + 1); }} />
          <StateRail state={a.state} events={events} />
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-4">
            <Stat label="Tokens" value={`${(a.usage.input_tokens + a.usage.output_tokens).toLocaleString()}`} hint={`cap ${a.budget.max_tokens.toLocaleString()}`} />
            <Stat label="Model cost" value={usd(a.usage.usd)} hint={`cap ${usd(a.budget.max_usd)}`} />
            <Stat label="Model / tool calls" value={`${a.usage.llm_calls} / ${a.usage.tool_calls}`} />
            <Stat label="Sandbox time" value={`${a.usage.sandbox_seconds.toFixed(1)} s`} hint={`wall cap ${a.budget.max_wall_clock_s}s`} />
          </div>
          <BudgetMeter used={a.usage.usd} cap={a.budget.max_usd} />
          <div className="mt-4">
            <Panel title={`Event log (${events.length})`} actions={<a className="text-xs text-accent hover:underline" href={`/api/v1/assessments/${a.id}/report`} target="_blank" rel="noreferrer">JSON report</a>}>
              <EventLog events={events} />
            </Panel>
          </div>
        </div>
      </details>
    </>
  );
}

function AssessmentPhases({
  state, findingStatuses, log,
}: {
  state: AssessmentState;
  findingStatuses: readonly string[];
  log: { status: 'loading' | 'ready' | 'error'; data?: { items: StreamEvent[]; truncated: boolean }; error?: unknown; reload: () => void };
}) {
  if (log.status === 'loading') return <p className="mb-4 text-sm text-muted">Loading the recorded phases…</p>;
  if (log.status === 'error') return <div className="mb-4"><ErrorState error={log.error} onRetry={log.reload} context="Event log" /></div>;
  const items = log.data?.items ?? [];
  const truncated = log.data?.truncated === true;
  const phases = assessmentPhases({ state, events: items, findingStatuses, eventLogTruncated: truncated });
  return (
    <div className="mb-4">
      {truncated && <p className="mb-2 text-sm text-muted" data-testid="event-log-page-note">{EVENT_LOG_PAGE_NOTE}</p>}
      <ol aria-label="Phases of this check" className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="assessment-phases">
        {phases.map(item => (
          <li key={item.id} className="rounded-lg border border-line p-3" data-testid={`assessment-phase-${item.id}`}>
            <p className="text-sm font-medium">{item.label}</p>
            <p className="text-xs text-muted">{JOURNEY_STATUS[item.status]}</p>
            <p className="mt-1 text-xs text-muted">{item.detail}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}

function ConnectionBar({ state, error, onReconnect }: { state: ConnectionState; error: unknown; onReconnect: () => void }) {
  const c = CONNECTION[state];
  return (
    <div className="mb-4 flex flex-col gap-2">
      <p className="flex items-center gap-2 text-xs" data-testid="stream-state">
        <Badge view={{ label: c.label, tone: c.tone, glyph: state === 'connected' ? '●' : '○' }} />
      </p>
      {error !== null && <ErrorState error={error} onRetry={onReconnect} context="Event stream" />}
    </div>
  );
}

function ProviderSummary({ runs }: { runs: AgentRun[] }) {
  const modes = new Map<string, number>();
  for (const r of runs) modes.set(r.provider_mode, (modes.get(r.provider_mode) ?? 0) + 1);
  if (modes.size === 0) return null;
  const notLive = [...modes.keys()].some(m => m !== 'live');
  return (
    <div className={`mb-4 rounded-md border px-3 py-2 text-sm ${notLive ? 'border-warn/50 bg-warn/10' : 'border-ok/40 bg-ok/5'}`} data-testid="provider-summary">
      <span className="font-semibold">{notLive ? 'Not a live-provider run. ' : 'Live provider run. '}</span>
      <Term t="provider">Model turns</Term> came from: {[...modes].map(([m, n]) => `${providerLabel(m).label} × ${n}`).join(', ')}.
      {notLive && <span className="text-muted"> {providerNote(modes)}</span>}
    </div>
  );
}

function StateRail({ state, events }: { state: AssessmentState; events: StreamEvent[] }) {
  const visited = useMemo(() => new Set(events.map(e => e.state).filter((s): s is AssessmentState => s !== null)), [events]);
  const steps = railSteps(state, visited);
  const failed = state === 'FAILED' || state === 'CANCELLED' || state === 'BUDGET_EXHAUSTED';
  return (
    <ol aria-label="Assessment state" className="flex flex-wrap gap-1">
      {steps.map(s => {
        const cls = {
          done: 'border-ok/40 bg-ok/10 text-ok',
          current: 'border-accent bg-accent/15 text-accent',
          upcoming: 'border-line text-faint',
          skipped: 'border-dashed border-line text-faint line-through',
        }[s.phase];
        return (
          <li key={s.state} aria-current={s.phase === 'current' ? 'step' : undefined} className={`rounded border px-2 py-1 text-xs ${cls}`}>
            <span className="sr-only">{s.phase}: </span>{ASSESSMENT_STATE[s.state].label}
          </li>
        );
      })}
      {failed && <li className="rounded border border-bad/50 bg-bad/10 px-2 py-1 text-xs text-bad">{ASSESSMENT_STATE[state].label}</li>}
    </ol>
  );
}

function BudgetMeter({ used, cap }: { used: number; cap: number }) {
  const ratio = cap > 0 ? Math.min(1, used / cap) : 0;
  return (
    <div className="mt-2" aria-label="Budget used">
      <div className="h-1.5 w-full overflow-hidden rounded bg-panel-2" role="meter" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={used} aria-label="Model spend against cap">
        <div className={`h-full ${ratio > 0.9 ? 'bg-bad' : ratio > 0.6 ? 'bg-warn' : 'bg-accent'}`} style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}



function EventLog({ events }: { events: StreamEvent[] }) {
  if (events.length === 0) return <Empty>No events yet.</Empty>;
  return (
    <ol className="max-h-[28rem] overflow-y-auto text-xs" aria-live="polite" data-testid="event-log">
      {events.map(e => (
        <li key={e.seq} className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-2 border-b border-line/60 py-1.5">
          <span className="font-mono text-faint">#{e.seq}</span>
          <div>
            <p className="flex flex-wrap items-center gap-1.5">
              <Mono>{e.type}</Mono>
              {e.agent && <Tag tone="accent">{agentRole(e.agent).label}</Tag>}
              {e.state && <Tag>{ASSESSMENT_STATE[e.state].label}</Tag>}
              <span className="text-faint"><Time iso={e.at} /></span>
            </p>
            <p className="mt-0.5 text-muted">{e.message}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
