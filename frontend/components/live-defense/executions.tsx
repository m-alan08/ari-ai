'use client';

import type {
  FirstResponderDiagnostics,
  FirstResponderInvocationView,
  InvestigationSummary,
  InvestigationTimeline,
} from '@hackatl/frontend-transport';
import { useCallback, useState } from 'react';
import { ArtifactPeek } from '@/components/evidence';
import { SourceTag } from '@/components/live-defense/common';
import { Async, Button, Empty, ErrorState, Mono, Panel, Tag } from '@/components/ui';
import { api } from '@/lib/api';
import { formatTime, relativeTime } from '@/lib/format';
import type { LiveDefenseMode } from '@/lib/live-defense';
import type { Resource } from '@/lib/use-resource';
import { useResource } from '@/lib/use-resource';
import type { Tone } from '@/lib/status';

/**
 * First-responder execution observability. Persisted records only: an agent
 * step that never ran is skipped, never faked as success; investigative
 * execution is not a confirmed compromise and not executed enforcement.
 * Missing fields render as "unavailable", never estimated.
 */

function statusTone(status: string): Tone {
  if (status === 'failed' || status === 'dead') return 'bad';
  if (status === 'cancelled') return 'neutral';
  if (status.startsWith('skipped_') || status === 'blocked') return 'warn';
  if (status === 'completed_unverified' || status === 'executed' || status === 'succeeded') return 'ok';
  if (status === 'queued' || status === 'eligible' || status === 'running') return 'info';
  return 'neutral';
}

const UNAVAILABLE = <span className="text-faint">unavailable</span>;

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-faint">{label}</p>
      <p className="truncate text-ink">{children ?? UNAVAILABLE}</p>
    </div>
  );
}

function Ms({ ms }: { ms: number | null | undefined }) {
  if (ms === null || ms === undefined) return UNAVAILABLE;
  return <>{ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`}</>;
}

function Timestamp({ iso }: { iso: string | null | undefined }) {
  if (!iso) return UNAVAILABLE;
  return <span title={iso}>{formatTime(iso)}</span>;
}

export function Executions({
  mode,
  projectId,
  diagnostics,
}: {
  mode: LiveDefenseMode;
  projectId: string | null;
  diagnostics?: Resource<FirstResponderDiagnostics>;
  onRefresh?: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  if (mode === 'layout-preview') {
    return (
      <Panel title="Sample executions chrome" actions={<SourceTag mode="layout-preview" />}>
        <p className="text-sm text-ink">Sample layout only. No investigation data is loaded here.</p>
      </Panel>
    );
  }
  if (!projectId) return <Empty>Choose a project to inspect first-responder executions.</Empty>;
  return (
    <div className="flex flex-col gap-4" data-testid="live-defense-executions">
      <p data-testid="live-defense-executions-copy" className="rounded-xl border border-line bg-panel-2 px-4 py-3 text-sm text-ink">
        Persisted first-responder records only. Investigative execution is not a confirmed compromise and not executed
        enforcement. Values the backend has not persisted render as unavailable — never estimated.
      </p>
      <DiagnosticsPanel diagnostics={diagnostics} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <InvestigationList projectId={projectId} selected={selected} onSelect={setSelected} />
        <InvestigationDetail decisionId={selected} />
      </div>
    </div>
  );
}

function InvestigationList({
  projectId,
  selected,
  onSelect,
}: {
  projectId: string;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const first = useResource(
    `ld-inv-list:${projectId}`,
    s => api().firstResponderInvestigations(projectId, { signal: s, limit: 50 }),
    15000,
  );
  const [extra, setExtra] = useState<InvestigationSummary[]>([]);
  const [extraCursor, setExtraCursor] = useState<string | null>(null);
  const [moreError, setMoreError] = useState<unknown>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const cursor = extraCursor ?? (first.status === 'ready' ? first.data.next_cursor : null);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await api().firstResponderInvestigations(projectId, { cursor, limit: 50 });
      setExtra(prev => {
        const seen = new Set(prev.map(i => i.decision_id));
        return [...prev, ...page.items.filter(i => !seen.has(i.decision_id))];
      });
      setExtraCursor(page.next_cursor);
    } catch (error) {
      setMoreError(error);
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, projectId]);

  const firstIds = new Set(first.status === 'ready' ? first.data.items.map(i => i.decision_id) : []);
  const items: InvestigationSummary[] = [
    ...(first.status === 'ready' ? first.data.items : []),
    ...extra.filter(i => !firstIds.has(i.decision_id)),
  ];

  return (
    <Panel
      title="Investigations"
      actions={
        <Button
          className="!px-2 !py-0.5 text-xs"
          onClick={() => {
            setExtra([]);
            setExtraCursor(null);
            first.reload();
          }}
          data-testid="ld-inv-refresh"
        >
          Refresh
        </Button>
      }
    >
      <Async resource={first} label="investigations">
        {() =>
          items.length === 0 ? (
            <Empty>No first-responder decisions recorded yet. Investigations appear here after a decision persists.</Empty>
          ) : (
            <ul className="flex flex-col gap-2" data-testid="ld-inv-list">
              {items.map(item => (
                <li key={item.decision_id}>
                  <button
                    type="button"
                    onClick={() => onSelect(item.decision_id)}
                    data-testid={`ld-inv-row-${item.decision_id}`}
                    aria-pressed={selected === item.decision_id}
                    className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                      selected === item.decision_id ? 'border-brand bg-panel-2' : 'border-line hover:border-muted'
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Mono className="text-xs">{item.decision_id}</Mono>
                      <Tag tone={item.cancelled ? 'neutral' : statusTone(item.invocations_failed > 0 ? 'failed' : item.evidence_state)}>
                        {item.cancelled ? 'cancelled' : item.action}
                      </Tag>
                      {item.simulated && <Tag tone="accent">simulated</Tag>}
                      <span className="text-faint text-xs">{relativeTime(item.created_at)}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
                      <span>{item.invocations_total} invocations</span>
                      {item.invocations_queued_or_running > 0 && <span className="text-info">{item.invocations_queued_or_running} queued/running</span>}
                      {item.invocations_completed > 0 && <span className="text-ok">{item.invocations_completed} completed</span>}
                      {item.invocations_failed > 0 && <span className="text-bad">{item.invocations_failed} failed</span>}
                      {item.invocations_skipped > 0 && <span className="text-warn">{item.invocations_skipped} skipped</span>}
                      {item.invocations_cancelled > 0 && <span className="text-muted">{item.invocations_cancelled} cancelled</span>}
                      {item.handoff_state && <span>handoff {item.handoff_state}</span>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )
        }
      </Async>
      {moreError !== null && <ErrorState error={moreError} context="Load more" onRetry={() => void loadMore()} />}
      {cursor && (
        <Button className="mt-2 !px-2 !py-0.5 text-xs" disabled={loadingMore} onClick={() => void loadMore()} data-testid="ld-inv-more">
          {loadingMore ? 'Loading…' : 'Load more'}
        </Button>
      )}
    </Panel>
  );
}

function Section({ title, children, testId }: { title: string; children: React.ReactNode; testId?: string }) {
  return (
    <section data-testid={testId} className="rounded-lg border border-line bg-panel-2 px-3 py-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">{title}</h3>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function InvocationRow({ view }: { view: FirstResponderInvocationView }) {
  const inv = view.invocation;
  const job = view.job;
  const exec = view.execution;
  return (
    <details data-testid={`ld-inv-detail-${inv.id}`} className="rounded-md border border-line bg-bg px-3 py-2">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-ink">{inv.agent}</span>
        <Tag tone={statusTone(inv.status)}>{inv.status}</Tag>
        {inv.provider_mode !== 'none' && <Tag tone="accent">{inv.provider_mode} provider</Tag>}
        {job?.stalled && <Tag tone="bad">stalled lease</Tag>}
        {inv.reason && <span className="text-faint text-xs">{inv.reason}</span>}
      </summary>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs md:grid-cols-3">
        <Cell label="Invocation"><Mono>{inv.id}</Mono></Cell>
        <Cell label="Queued at"><Timestamp iso={inv.created_at} /></Cell>
        <Cell label="Capability"><Mono>{inv.capability_class}</Mono></Cell>
        <Cell label="Execution record">{view.execution_recorded ? <Tag tone="ok">recorded</Tag> : UNAVAILABLE}</Cell>
        <Cell label="Model">{exec?.model_id ?? null}</Cell>
        <Cell label="Provider">{exec?.provider_mode ?? null}</Cell>
        <Cell label="Started"><Timestamp iso={exec?.started_at} /></Cell>
        <Cell label="Ended"><Timestamp iso={exec?.ended_at} /></Cell>
        <Cell label="Duration"><Ms ms={exec?.latency_ms ?? job?.wall_time_ms ?? null} /></Cell>
        <Cell label="LLM turns">{exec?.llm_turns ?? null}</Cell>
        <Cell label="Tool calls">{exec?.tool_call_count ?? null}</Cell>
        <Cell label="Cost (USD)">{exec?.cost_usd != null ? `$${exec.cost_usd.toFixed(4)}` : UNAVAILABLE}</Cell>
        <Cell label="Tokens">{view.token_usage_note}</Cell>
        <Cell label="Error code">{exec?.error_code ?? job?.error_code ?? null}</Cell>
        <Cell label="Retries">{job ? `${job.attempts}/${job.max_attempts} attempts` : 'no job'}</Cell>
        <Cell label="Integrity">{exec?.execution_integrity ?? null}</Cell>
        <Cell label="Evidence verified">{exec?.evidence_verified === null || exec?.evidence_verified === undefined ? UNAVAILABLE : exec.evidence_verified ? 'verified' : 'not verified'}</Cell>
        <Cell label="Confirmation">{exec?.confirmation ?? null}</Cell>
      </div>
      {job && (
        <p className="mt-2 text-xs text-muted">
          Job <Mono>{job.job_id}</Mono> · status <Tag tone={statusTone(job.status)}>{job.status}</Tag>
          {job.error_message && <span className="text-bad"> · {job.error_message}</span>}
        </p>
      )}
      {exec?.transcript_artifact_id && <ArtifactPeek id={exec.transcript_artifact_id} label="transcript" />}
      {exec?.output_artifact_id && <ArtifactPeek id={exec.output_artifact_id} label="output" />}
      {!exec && (
        <p className="mt-2 text-xs text-faint">
          No execution record persisted — this step never ran an agent loop (or ran before execution recording landed).
        </p>
      )}
    </details>
  );
}

function Timeline({ t }: { t: InvestigationTimeline }) {
  return (
    <div className="flex flex-col gap-3" data-testid="ld-inv-timeline">
      <Section title="Final state" testId="ld-tl-final">
        <div className="flex flex-wrap items-center gap-2">
          <Tag tone={statusTone(t.final_state)}>{t.final_state}</Tag>
          <Tag tone="info">evidence: {t.decision.evidence_state}</Tag>
          <Tag tone={t.decision.confirmation === 'confirmed' ? 'ok' : 'neutral'}>confirmation: {t.decision.confirmation}</Tag>
          {t.decision.simulated && <Tag tone="accent">simulated</Tag>}
          {t.decision.cancelled && <Tag tone="neutral">cancelled{t.decision.cancel_reason ? `: ${t.decision.cancel_reason}` : ''}</Tag>}
        </div>
        <p className="mt-1 text-xs text-faint">{t.caveat}</p>
      </Section>

      <Section title="1 · Triggering security events" testId="ld-tl-events">
        {t.trigger_events.length === 0 ? (
          <p className="text-xs text-faint">{t.decision.trigger_event_ids.length === 0 ? 'No trigger events recorded on the decision.' : 'Trigger event records unavailable (referenced but not found).'}</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {t.trigger_events.map(e => (
              <li key={e.id} className="flex flex-wrap items-center gap-2 text-xs" data-testid={`ld-tl-event-${e.id}`}>
                <Mono>{e.id}</Mono>
                <Tag tone="neutral">{e.type}</Tag>
                <Tag tone={statusTone(e.status)}>{e.status}</Tag>
                {e.simulated && <Tag tone="accent">simulated</Tag>}
                <span className="text-faint">{formatTime(e.occurred_at)}</span>
                {e.payload_artifact_id && <Mono className="text-faint">payload {e.payload_artifact_id}</Mono>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="2 · Application, deploy mapping, commit" testId="ld-tl-mapping">
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs md:grid-cols-3">
          <Cell label="Application">{t.application ? `${t.application.name} (${t.application.environment})` : t.decision.application_id ?? 'no application'}</Cell>
          <Cell label="Mapping">{t.mapping ? <Mono>{t.mapping.id}</Mono> : t.decision.correlation.mapping_id ?? 'no mapping'}</Cell>
          <Cell label="Commit">{t.snapshot?.commit_sha ? <Mono>{t.snapshot.commit_sha.slice(0, 12)}</Mono> : 'unresolved'}</Cell>
          <Cell label="Snapshot">{t.snapshot ? (t.snapshot.resolved ? <Mono>{t.snapshot.snapshot_id}</Mono> : <Tag tone="warn">unresolved</Tag>) : UNAVAILABLE}</Cell>
          <Cell label="Repo">{t.snapshot?.repo_id ? <Mono>{t.snapshot.repo_id}</Mono> : UNAVAILABLE}</Cell>
          <Cell label="Correlation confidence">{t.decision.correlation.confidence}</Cell>
        </div>
        {t.snapshot && <p className="mt-1 text-xs text-faint">{t.snapshot.note}</p>}
      </Section>

      <Section title="3 · First-responder decision" testId="ld-tl-decision">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Mono>{t.decision.id}</Mono>
          <Tag tone="info">{t.decision.action}</Tag>
          <span className="text-faint">{t.decision.orchestrator_version} · {formatTime(t.decision.created_at)}</span>
          {t.decision.executed === false && <Tag tone="neutral">executed=false</Tag>}
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-xs md:grid-cols-3">
          <Cell label="Selected agents">{t.decision.selected_agents.join(', ') || 'none'}</Cell>
          <Cell label="Blocked agents">{t.decision.blocked_agents.length ? t.decision.blocked_agents.map(b => `${b.name}`).join(', ') : 'none'}</Cell>
          <Cell label="Retries / loops">{t.decision.retry_count} / {t.decision.loop_count}</Cell>
        </div>
        {t.decision.blocked_agents.length > 0 && (
          <ul className="mt-2 flex flex-col gap-1">
            {t.decision.blocked_agents.map(b => (
              <li key={b.name} className="text-xs text-warn" data-testid={`ld-tl-blocked-${b.name}`}>
                {b.name}: {b.reason}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="4 · Agent invocations" testId="ld-tl-invocations">
        {t.invocations.length === 0 ? (
          <p className="text-xs text-faint">No invocation records on this decision.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {t.invocations.map(v => (
              <li key={v.invocation.id}>
                <InvocationRow view={v} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="5 · Analyst handoff" testId="ld-tl-handoff">
        {t.handoff === null ? (
          <p className="text-xs text-faint">No analyst handoff recorded.</p>
        ) : (
          <div className="text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Mono>{t.handoff.id}</Mono>
              <Tag tone={t.handoff.state === 'delivered' ? 'ok' : 'info'}>{t.handoff.state}</Tag>
              <Tag tone="neutral">confirmation: {t.handoff.confirmation}</Tag>
              {t.handoff.threat_confirmed && <Tag tone="bad">threat confirmed</Tag>}
            </div>
            <p className="mt-1 text-ink">{t.handoff.title}</p>
            {t.handoff.evidence_event_ids.length > 0 && (
              <p className="mt-1 text-faint">evidence events: {t.handoff.evidence_event_ids.join(', ')}</p>
            )}
          </div>
        )}
      </Section>
    </div>
  );
}

function InvestigationDetail({ decisionId }: { decisionId: string | null }) {
  const timeline = useResource(
    decisionId ? `ld-inv-tl:${decisionId}` : null,
    s => api().firstResponderInvestigation(decisionId!, { signal: s }),
    8000,
  );
  return (
    <Panel title="Investigation timeline" id="ld-investigation-detail">
      {decisionId === null ? (
        <Empty>Select an investigation to trace Event → Decision → Invocation → Artifact → Handoff.</Empty>
      ) : (
        <Async resource={timeline} label="timeline">
          {t => <Timeline t={t} />}
        </Async>
      )}
    </Panel>
  );
}

function Stat({ label, value, tone = 'neutral' }: { label: string; value: React.ReactNode; tone?: Tone }) {
  return (
    <div className="rounded-lg border border-line bg-bg px-3 py-2">
      <p className="text-faint text-xs">{label}</p>
      <p className={`text-lg font-semibold ${tone === 'bad' ? 'text-bad' : tone === 'warn' ? 'text-warn' : tone === 'ok' ? 'text-ok' : tone === 'info' ? 'text-info' : 'text-ink'}`}>{value}</p>
    </div>
  );
}

function DiagnosticsPanel({ diagnostics }: { diagnostics?: Resource<FirstResponderDiagnostics> }) {
  if (!diagnostics) return null;
  return (
    <Panel title="Operational diagnostics" actions={diagnostics.status === 'ready' ? <span className="text-faint text-xs">as of {formatTime(diagnostics.data.generated_at)}</span> : undefined}>
      <Async resource={diagnostics} label="diagnostics">
        {d => (
          <div className="flex flex-col gap-3" data-testid="ld-diagnostics">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-6">
              <Stat label="Investigations" value={d.investigations_total} />
              <Stat label="Running jobs" value={d.jobs_running} tone={d.jobs_running ? 'info' : 'neutral'} />
              <Stat label="Stalled (lease expired)" value={d.jobs_stalled} tone={d.jobs_stalled ? 'bad' : 'neutral'} />
              <Stat label="Waiting in queue" value={d.jobs_queued} tone={d.jobs_queued ? 'info' : 'neutral'} />
              <Stat label="Failed jobs" value={d.jobs_failed + d.jobs_dead} tone={d.jobs_failed + d.jobs_dead ? 'bad' : 'neutral'} />
              <Stat label="Cancelled" value={d.jobs_cancelled + d.decisions_cancelled} />
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-faint">Invocations by status</h4>
                {Object.keys(d.invocations_by_status).length === 0 ? (
                  <p className="mt-1 text-xs text-faint">No invocations persisted.</p>
                ) : (
                  <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="ld-diag-status">
                    {Object.entries(d.invocations_by_status).map(([status, n]) => (
                      <li key={status}><Tag tone={statusTone(status)}>{status}: {n}</Tag></li>
                    ))}
                  </ul>
                )}
                {Object.keys(d.missing_prerequisites).length > 0 && (
                  <>
                    <h4 className="mt-2 text-xs font-semibold uppercase tracking-wide text-faint">Missing prerequisites</h4>
                    <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="ld-diag-prereqs">
                      {Object.entries(d.missing_prerequisites).map(([k, n]) => (
                        <li key={k}><Tag tone="warn">{k.replace(/^skipped_/, '').replace(/_/g, ' ')}: {n}</Tag></li>
                      ))}
                    </ul>
                  </>
                )}
                {Object.keys(d.failures_by_agent).length > 0 && (
                  <>
                    <h4 className="mt-2 text-xs font-semibold uppercase tracking-wide text-faint">Failures by agent</h4>
                    <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="ld-diag-agent-failures">
                      {Object.entries(d.failures_by_agent).map(([agent, n]) => (
                        <li key={agent}><Tag tone="bad">{agent}: {n}</Tag></li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-faint">Latency (job wall time)</h4>
                {d.agent_step_latency ? (
                  <p className="mt-1 text-xs text-muted" data-testid="ld-diag-latency">
                    agent steps: {d.agent_step_latency.samples} samples · avg <Ms ms={d.agent_step_latency.avg_ms} /> · max <Ms ms={d.agent_step_latency.max_ms} />
                  </p>
                ) : (
                  <p className="mt-1 text-xs text-faint">No finished agent-step jobs to measure.</p>
                )}
                <h4 className="mt-2 text-xs font-semibold uppercase tracking-wide text-faint">Cost</h4>
                <p className="mt-1 text-xs text-muted" data-testid="ld-diag-cost">
                  {d.cost_usd_total !== null ? `$${d.cost_usd_total.toFixed(4)}` : UNAVAILABLE}
                </p>
                <p className="text-xs text-faint">{d.cost_note}</p>
                {d.waiting_jobs.length > 0 && (
                  <>
                    <h4 className="mt-2 text-xs font-semibold uppercase tracking-wide text-faint">Waiting jobs</h4>
                    <ul className="mt-1 flex flex-col gap-1" data-testid="ld-diag-waiting">
                      {d.waiting_jobs.map(w => (
                        <li key={w.job_id} className="text-xs text-muted">
                          <Mono>{w.job_id}</Mono> · {w.kind} · attempts {w.attempts} · runs {w.run_after ? relativeTime(w.run_after) : 'when claimed'}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </div>
            {d.recent_errors.length > 0 && (
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-faint">Recent errors</h4>
                <ul className="mt-1 flex flex-col gap-1" data-testid="ld-diag-errors">
                  {d.recent_errors.map(e => (
                    <li key={e.job_id} className="rounded border border-line bg-bg px-2 py-1 text-xs">
                      <Mono>{e.job_id}</Mono> · {e.kind}
                      {e.agent && <> · {e.agent}</>}
                      {e.code && <> · <span className="text-bad">{e.code}</span></>}
                      {e.message && <span className="text-muted"> · {e.message}</span>}
                      {e.at && <span className="text-faint"> · {relativeTime(e.at)}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <ul className="flex flex-col gap-0.5">
              {d.notes.map((n, i) => (
                <li key={i} className="text-xs text-faint">{n}</li>
              ))}
            </ul>
          </div>
        )}
      </Async>
    </Panel>
  );
}
