'use client';

import type {
  ComponentGraph,
  ConnectedApplication,
  DefenseHealth,
  DefenseRecommendation,
  Incident,
  LivePolicyDecision,
  MonitoringEvent,
  Project,
  ReassessmentRequest,
} from '@hackatl/frontend-transport';
import Link from 'next/link';
import { DefenseMap } from '@/components/defense-map';
import { DefenseSwitch } from '@/components/defense-switch';
import { HealthUnknownNote, SimulationCaveat, SourceTag } from '@/components/live-defense/common';
import { Async, Badge, Empty, Mono, Panel, Tag, Time } from '@/components/ui';
import {
  actionInWords,
  eventIncidentIds,
  isOpenIncidentState,
  liveDefenseHref,
  liveDefenseOverviewCopy,
  MISSING_LIVE_APIS,
  provenanceLabel,
  USED_LIVE_APIS,
  type LiveDefenseMode,
} from '@/lib/live-defense';
import { PREVIEW_EVENTS, PREVIEW_INCIDENTS, PREVIEW_LINKS, PREVIEW_NODES, PREVIEW_RECOMMENDATIONS, PREVIEW_STATES } from '@/lib/live-defense-preview';
import { STATE_LABEL } from '@/lib/incidents';
import { EVENT_STATUS } from '@/lib/status';
import type { Resource } from '@/lib/use-resource';

export function LiveOverview({
  mode,
  projectId,
  project,
  reloadProject,
  events,
  incidents,
  recs,
  decisions,
  reassess,
  health,
  graph,
  apps,
}: {
  mode: LiveDefenseMode;
  projectId: string | null;
  project?: Project;
  reloadProject?: () => void;
  events?: Resource<{ items: MonitoringEvent[] }>;
  incidents?: Resource<{ items: Incident[] }>;
  recs?: Resource<{ items: DefenseRecommendation[]; label: string }>;
  decisions?: Resource<{ items: LivePolicyDecision[]; label: string }>;
  reassess?: Resource<{ items: ReassessmentRequest[] }>;
  health?: Resource<DefenseHealth>;
  graph?: Resource<ComponentGraph>;
  apps?: Resource<{ items: ConnectedApplication[] }>;
}) {
  if (mode === 'layout-preview') return <PreviewOverview projectId={projectId} />;
  return (
    <LiveOverviewRecords
      projectId={projectId}
      project={project}
      reloadProject={reloadProject}
      events={events}
      incidents={incidents}
      recs={recs}
      decisions={decisions}
      reassess={reassess}
      health={health}
      graph={graph}
      apps={apps}
    />
  );
}

function LiveOverviewRecords({
  projectId, project, reloadProject, events, incidents, recs, decisions, reassess, health, graph, apps,
}: {
  projectId: string | null;
  project?: Project;
  reloadProject?: () => void;
  events?: Resource<{ items: MonitoringEvent[] }>;
  incidents?: Resource<{ items: Incident[] }>;
  recs?: Resource<{ items: DefenseRecommendation[]; label: string }>;
  decisions?: Resource<{ items: LivePolicyDecision[]; label: string }>;
  reassess?: Resource<{ items: ReassessmentRequest[] }>;
  health?: Resource<DefenseHealth>;
  graph?: Resource<ComponentGraph>;
  apps?: Resource<{ items: ConnectedApplication[] }>;
}) {
  const eventItems = events?.status === 'ready' ? events.data.items : [];
  const incidentItems = incidents?.status === 'ready' ? incidents.data.items : [];
  const recItems = recs?.status === 'ready' ? recs.data.items : [];
  const decisionItems = decisions?.status === 'ready' ? decisions.data.items : [];
  const open = incidentItems.filter(item => isOpenIncidentState(item.state));
  const copy = liveDefenseOverviewCopy({
    monitoringEnabled: project?.settings.monitoring_policy.enabled ?? false,
    eventsKnown: events?.status === 'ready',
    eventCount: eventItems.length,
    simulatedEventCount: eventItems.filter(item => item.simulated).length,
    incidentsKnown: incidents?.status === 'ready',
    openIncidentCount: open.length,
    simulatedIncidentCount: open.filter(item => item.simulated).length,
    recommendationsKnown: recs?.status === 'ready',
    recommendationCount: recItems.length,
    appsKnown: apps?.status === 'ready',
    appCount: apps?.status === 'ready' ? apps.data.items.length : 0,
    healthKnown: health?.status === 'ready',
    lastIngestAt: health?.status === 'ready' ? health.data.last_ingest_at : null,
    shadowKnown: decisions?.status === 'ready',
    shadowCount: decisionItems.length,
  });
  return (
    <div className="flex flex-col gap-4">
      <p className="rounded-xl border border-line bg-panel p-5 text-sm text-ink" data-testid="live-defense-summary">{copy}</p>
      {project && reloadProject && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-panel px-5 py-4">
          <DefenseSwitch project={project} onChange={() => reloadProject()} />
          <p className="text-xs text-muted">Same owner-only switch as Monitoring. It turns repository re-checks on or off. It does not arm an enforcer.</p>
        </div>
      )}
      <ul className="ld-stat-row" aria-label="Recorded Live Defense counts">
        <li><span>Events</span> {events?.status === 'ready' ? eventItems.length : '…'}</li>
        <li><span>Open incidents</span> {incidents?.status === 'ready' ? open.length : '…'}</li>
        <li><span>Simulation recommendations</span> {recs?.status === 'ready' ? recItems.length : '…'}</li>
        <li><span>Shadow decisions</span> {decisions?.status === 'ready' ? decisionItems.length : '…'}</li>
        <li><span>Re-checks</span> {reassess?.status === 'ready' ? reassess.data.items.length : '…'}</li>
        <li><span>Staging apps</span> {apps?.status === 'ready' ? apps.data.items.length : '…'}</li>
      </ul>
      <Panel title="Recorded health and topology">
        <HealthUnknownNote />
        {health ? (
          <Async resource={health} label="defense health">
            {item => (
              <dl className="mt-3 grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1 text-sm text-ink" data-testid="live-defense-health">
                <dt className="text-muted">Status</dt><dd>{item.status}</dd>
                <dt className="text-muted">Reason</dt><dd>{item.status_reason}</dd>
                <dt className="text-muted">Worker</dt><dd>{item.worker_status} — {item.worker_status_reason}</dd>
                <dt className="text-muted">Telemetry</dt><dd>{item.telemetry_status} — {item.telemetry_status_reason}</dd>
                <dt className="text-muted">Policy</dt><dd>{item.policy_status} — {item.policy_status_reason}</dd>
                <dt className="text-muted">Worker last heartbeat</dt><dd>{item.worker_seen_at ? <Time iso={item.worker_seen_at} /> : 'unavailable'}</dd>
                <dt className="text-muted">Queue depth (this project)</dt><dd>{item.queue_depth ?? 'unavailable'}</dd>
                <dt className="text-muted">Last ingest window</dt><dd>{item.last_ingest_at ? <Time iso={item.last_ingest_at} /> : 'unavailable'}</dd>
                <dt className="text-muted">Last runtime event</dt><dd>{item.last_runtime_event_at ? <Time iso={item.last_runtime_event_at} /> : 'unavailable'}</dd>
              </dl>
            )}
          </Async>
        ) : <Empty>Health was not requested.</Empty>}
        {graph ? (
          <div className="mt-4">
            <Async resource={graph} label="recorded topology">
              {item => item.nodes.length === 0 ? (
                <Empty>No recorded applications or ingest routes. This page does not draw a map or mark anything healthy.</Empty>
              ) : (
                <div data-testid="live-defense-graph">
                  <p className="mb-2 text-xs text-muted">{item.note}</p>
                  <ul className="flex flex-col gap-2">
                    {item.nodes.map(node => {
                      const obs = item.observations.find(entry => entry.node_id === node.id);
                      return (
                        <li key={node.id} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink">
                          <span className="font-medium">{node.label}</span>
                          <span className="mt-1 flex flex-wrap gap-1 text-xs">
                            <Tag>{node.kind}</Tag>
                            <Tag tone={obs?.state === 'attention' ? 'warn' : 'neutral'}>{obs?.state ?? 'unknown'}</Tag>
                            {obs?.simulated && <Tag tone="warn">simulated source</Tag>}
                            {obs?.last_checked_at && <span className="text-muted">last window <Time iso={obs.last_checked_at} /></span>}
                          </span>
                          {obs?.note && <p className="mt-1 text-xs text-muted">{obs.note}</p>}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </Async>
          </div>
        ) : null}
      </Panel>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Recorded events" actions={<SourceTag mode="live" />}>
          {events ? (
            <Async resource={events} label="monitoring events">
              {page => page.items.length === 0 ? <Empty>No events received. Only signed GitHub deliveries or authenticated runtime batches appear here.</Empty> : (
                <ol className="flex flex-col gap-2">
                  {page.items.slice(0, 8).map(event => {
                    const linked = eventIncidentIds(event.id, incidentItems);
                    return (
                      <li key={event.id} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink">
                        <div className="flex flex-wrap items-center gap-2">
                          <Tag tone="accent">{event.source}</Tag>
                          <Tag>{event.type.replaceAll('_', ' ')}</Tag>
                          <Tag tone={event.simulated || event.test_event ? 'warn' : 'info'}>{provenanceLabel(event)}</Tag>
                          <Badge view={EVENT_STATUS[event.status]} />
                          <span className="text-xs text-muted">received <Time iso={event.received_at} /></span>
                        </div>
                        <p className="mt-1 text-xs">
                          {linked.length === 0
                            ? <span className="text-muted" data-testid={`event-correlation-${event.id}`}>Correlation: unknown</span>
                            : linked.map(id => (
                              <Link key={id} className="mr-2 text-accent hover:underline" href={`${liveDefenseHref(projectId, 'incidents')}/${id}`}>Incident {id}</Link>
                            ))}
                        </p>
                      </li>
                    );
                  })}
                </ol>
              )}
            </Async>
          ) : <Empty>Events were not requested.</Empty>}
        </Panel>
        <Panel title="Open incidents" actions={<Link className="text-xs text-accent hover:underline" href={liveDefenseHref(projectId, 'incidents')}>Investigate</Link>}>
          {incidents ? (
            <Async resource={incidents} label="incidents">
              {page => {
                const openItems = page.items.filter(item => isOpenIncidentState(item.state));
                if (openItems.length === 0) return <Empty>No open incidents.</Empty>;
                return (
                  <ul className="flex flex-col gap-2">
                    {openItems.slice(0, 6).map(item => (
                      <li key={item.id}>
                        <Link href={liveDefenseHref(projectId, 'incidents') + `/${item.id}`} className="block rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink hover:bg-white">
                          <span className="font-medium">{item.title}</span>
                          <span className="mt-1 flex flex-wrap gap-1 text-xs text-muted">
                            {item.simulated && <Tag tone="warn">Synthetic</Tag>}
                            <Tag>{STATE_LABEL[item.state]}</Tag>
                            opened <Time iso={item.opened_at} />
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                );
              }}
            </Async>
          ) : <Empty>Incidents were not requested.</Empty>}
        </Panel>
      </div>
      <Panel title="Shadow decisions" actions={<Link className="text-xs text-accent hover:underline" href={liveDefenseHref(projectId, 'explain')}>Explainability</Link>}>
        <p className="text-sm text-muted">Produced only when rules or validated frozen-v6 inference actually ran. Enforcement is always NOT EXECUTED.</p>
        {decisions ? (
          <Async resource={decisions} label="shadow decisions">
            {page => (
              <>
                <p className="my-2 text-xs text-muted">Backend label: {page.label}</p>
                {page.items.length === 0 ? <Empty>No shadow decision yet. Authenticated owned-staging ingest is required.</Empty> : (
                  <ul className="flex flex-col gap-2">
                    {page.items.slice(0, 5).map(item => (
                      <li key={item.id} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink" data-testid={`live-defense-decision-${item.id}`}>
                        <p className="font-medium">{item.action ? actionInWords(item.action, item.node) : 'Abstained'}</p>
                        <p className="mt-1 flex flex-wrap gap-1 text-xs">
                          <Tag>{item.producer.replaceAll('_', ' ')}</Tag>
                          <Tag tone="info">NOT EXECUTED</Tag>
                          {item.simulated ? <Tag tone="warn">simulated source</Tag> : <Tag tone="ok">authenticated</Tag>}
                          {!item.inference_ran && <Tag>inference not run</Tag>}
                        </p>
                        <p className="mt-1 text-xs text-muted">
                          {item.telemetry_source} · <Link className="text-accent hover:underline" href={`${liveDefenseHref(projectId, 'explain')}#${item.id}`}>Review</Link>
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </Async>
        ) : <Empty>Shadow decisions were not requested.</Empty>}
      </Panel>
      <Panel title="Simulation recommendations" actions={<Link className="text-xs text-accent hover:underline" href={liveDefenseHref(projectId, 'explain')}>Explainability</Link>}>
        <SimulationCaveat />
        {recs ? (
          <Async resource={recs} label="recommendations">
            {page => (
              <>
                <p className="my-2 text-xs text-muted">Backend label: {page.label}</p>
                {page.items.length === 0 ? <Empty>No recommendation has been requested for this project.</Empty> : (
                  <ul className="flex flex-col gap-2">
                    {page.items.slice(0, 5).map(rec => (
                      <li key={rec.id} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink">
                        <p className="font-medium">{actionInWords(rec.action, rec.node)}</p>
                        <p className="mt-1 text-xs text-muted">policy <Mono>{rec.policy_version}</Mono> · <Link className="text-accent hover:underline" href={`${liveDefenseHref(projectId, 'explain')}#${rec.id}`}>Explain and review</Link></p>
                        <p className="mt-1 text-xs text-warn">{rec.caveat}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </Async>
        ) : <Empty>Recommendations were not requested.</Empty>}
      </Panel>
      <Panel title="Owned-test controls" actions={<Link className="text-xs text-accent hover:underline" href={liveDefenseHref(projectId, 'controls')}>Authorize</Link>}>
        <p className="text-sm text-muted">
          Shadow decisions on this page stay NOT EXECUTED. Temporary rate-limit, exact-source block, logging, or human escalation run only after a separate authorization on the Controls tab.
        </p>
      </Panel>
      <Panel title="Staging apps" actions={<Link className="text-xs text-accent hover:underline" href={liveDefenseHref(projectId, 'apps')}>Onboard</Link>}>
        {apps ? (
          <Async resource={apps} label="staging apps">
            {page => page.items.length === 0 ? <Empty>No staging app registered. Production enforcement grants are not available.</Empty> : (
              <ul className="flex flex-col gap-2">
                {page.items.map(app => (
                  <li key={app.id} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink">
                    <span className="font-medium">{app.name}</span>
                    <span className="mt-1 flex flex-wrap gap-1 text-xs">
                      <Tag>{app.environment.replaceAll('_', ' ')}</Tag>
                      <Tag tone="info">shadow mode</Tag>
                      <Tag>{app.verification.replaceAll('_', ' ')}</Tag>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Async>
        ) : <Empty>Apps were not requested.</Empty>}
      </Panel>
      <IntegrationStatus />
    </div>
  );
}

function PreviewOverview({ projectId }: { projectId: string | null }) {
  return (
    <div className="flex flex-col gap-4" data-testid="live-defense-preview-overview">
      <p className="rounded-xl border border-line bg-panel p-5 text-sm text-ink">
        Sample layout only. Counts, map states and Q-values below are fixtures. They are not telemetry and not a loaded policy.
      </p>
      <DefenseMap
        nodes={PREVIEW_NODES}
        links={PREVIEW_LINKS}
        states={PREVIEW_STATES}
        title="Illustrative components (sample layout)"
        footnote="SAMPLE LAYOUT. States are drawn so the map chrome can be reviewed. They are not recorded health checks."
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Example event feed" actions={<SourceTag mode="layout-preview" />}>
          <ol className="flex flex-col gap-2">
            {PREVIEW_EVENTS.map(event => (
              <li key={event.id} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink">
                <div className="flex flex-wrap items-center gap-2">
                  <Tag tone="warn">Sample</Tag>
                  <span className="font-medium">{event.title}</span>
                </div>
                <p className="mt-1 text-xs text-muted">{event.detail}</p>
              </li>
            ))}
          </ol>
        </Panel>
        <Panel title="Example incidents" actions={<Link className="text-xs text-accent hover:underline" href={liveDefenseHref(projectId, 'incidents', 'layout-preview')}>Investigate sample</Link>}>
          <ul className="flex flex-col gap-2">
            {PREVIEW_INCIDENTS.map(item => (
              <li key={item.id} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink">
                <p className="font-medium">{item.title}</p>
                <p className="mt-1 text-xs text-muted">{item.summary}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      <Panel title="Example recommendation chrome" actions={<Link className="text-xs text-accent hover:underline" href={liveDefenseHref(projectId, 'explain', 'layout-preview')}>Sample explainability</Link>}>
        <SimulationCaveat text={PREVIEW_RECOMMENDATIONS[0]?.caveat} />
        <p className="mt-2 text-sm text-ink">{actionInWords(PREVIEW_RECOMMENDATIONS[0]?.action ?? 'noop', PREVIEW_RECOMMENDATIONS[0]?.node ?? null)}</p>
      </Panel>
      <IntegrationStatus />
    </div>
  );
}

function IntegrationStatus() {
  return (
    <div data-testid="live-defense-api-coverage">
    <Panel title="API coverage for this dashboard">
      <p className="mb-3 text-sm text-muted">Live mode uses the endpoints that exist. Missing ones stay empty — they are not filled with mock telemetry.</p>
      <h3 className="label-mono mb-2 text-xs text-ink">In use</h3>
      <ul className="mb-4 divide-y divide-line text-sm">
        {USED_LIVE_APIS.map(item => (
          <li key={item.path} className="flex flex-wrap justify-between gap-2 py-1.5">
            <Mono>{item.method} {item.path}</Mono>
            <span className="text-xs text-muted">{item.use}</span>
          </li>
        ))}
      </ul>
      <h3 className="label-mono mb-2 text-xs text-ink">Not in the control plane yet</h3>
      <ul className="divide-y divide-line text-sm">
        {MISSING_LIVE_APIS.map(item => (
          <li key={item.id} className="py-1.5">
            <p className="font-medium text-ink">{item.title}</p>
            <p className="text-xs text-muted">{item.reason}</p>
          </li>
        ))}
      </ul>
    </Panel>
    </div>
  );
}
