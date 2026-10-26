'use client';

import type { DefenseRecommendation, Incident, IncidentDetail, MonitoringEvent } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { SourceTag } from '@/components/live-defense/common';
import { Async, Empty, Mono, Panel, Tag, Time } from '@/components/ui';
import { liveDefenseHref, provenanceLabel, type LiveDefenseMode } from '@/lib/live-defense';
import { PREVIEW_INCIDENTS, previewIncident } from '@/lib/live-defense-preview';
import { LIFECYCLE, STATE_LABEL } from '@/lib/incidents';
import type { Resource } from '@/lib/use-resource';

export function InvestigationList({
  mode,
  projectId,
  incidents,
}: {
  mode: LiveDefenseMode;
  projectId: string | null;
  incidents?: Resource<{ items: Incident[] }>;
}) {
  const [stateFilter, setStateFilter] = useState('all');
  const [originFilter, setOriginFilter] = useState('all');
  if (mode === 'layout-preview') {
    return (
      <Panel title="Sample investigations" actions={<SourceTag mode="layout-preview" />}>
        <ul className="flex flex-col gap-2">
          {PREVIEW_INCIDENTS.map(item => (
            <li key={item.id}>
              <Link href={`${liveDefenseHref(projectId, 'incidents', 'layout-preview')}/${encodeURIComponent(item.id)}`} className="block rounded-md border border-line bg-panel-2 p-3 text-ink hover:bg-panel">
                <p className="font-medium">{item.title}</p>
                <p className="mt-1 text-xs text-muted">{item.summary}</p>
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    );
  }
  return (
    <Panel title="Incidents available to investigate" actions={<SourceTag mode="live" />}>
      <p className="mb-3 text-sm text-muted">
        These are NIST SP 800-61 records from the control plane. Human actions (triage, two-person gates, closure) stay on the{' '}
        {projectId ? <Link className="text-accent hover:underline" href={`/projects/${projectId}/incidents`}>Incidents</Link> : 'Incidents'} page.
        Shadow decisions from authenticated ingest live on Explainability and are always NOT EXECUTED. Analyst review does not apply a control.
        The exportable pilot report on Staging apps lists this trail from recorded audit events; it does not invent labels or claim that shadow mode prevented breaches.
      </p>
      <div className="mb-3 flex flex-wrap gap-2 text-sm">
        <label className="text-muted">State
          <select className="ml-2 rounded-md border border-control bg-bg px-2 py-1 text-ink" value={stateFilter} onChange={e => setStateFilter(e.target.value)}>
            <option value="all">All</option>
            {LIFECYCLE.map(step => <option key={step} value={step}>{STATE_LABEL[step]}</option>)}
          </select>
        </label>
        <label className="text-muted">Origin
          <select className="ml-2 rounded-md border border-control bg-bg px-2 py-1 text-ink" value={originFilter} onChange={e => setOriginFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="recorded">Recorded</option>
            <option value="synthetic">Synthetic</option>
          </select>
        </label>
      </div>
      {incidents ? (
        <Async resource={incidents} label="incidents">
          {page => {
            const items = page.items.filter(item => {
              if (stateFilter !== 'all' && item.state !== stateFilter) return false;
              if (originFilter === 'synthetic' && !item.simulated) return false;
              if (originFilter === 'recorded' && item.simulated) return false;
              return true;
            });
            if (page.items.length === 0) return <Empty>No incidents. Open one from Monitoring or the Incidents page.</Empty>;
            if (items.length === 0) return <Empty>No incidents match these filters.</Empty>;
            return (
            <ul className="flex flex-col gap-2">
              {items.map(item => (
                <li key={item.id}>
                  <Link href={`${liveDefenseHref(projectId, 'incidents')}/${item.id}`} className="block rounded-md border border-line bg-panel-2 p-3 text-left text-ink hover:bg-panel" data-testid={`live-defense-incident-${item.id}`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">{item.title}</span>
                      <span className="flex gap-1">{item.simulated && <Tag tone="warn">Synthetic</Tag>}<Tag>{STATE_LABEL[item.state]}</Tag></span>
                    </div>
                    <p className="mt-1 text-xs text-muted">CSF {item.csf_function} · {item.severity ?? 'unscored'} · opened <Time iso={item.opened_at} /> · {item.monitoring_event_ids.length} linked events · {item.recommendation_ids.length} linked recommendations</p>
                  </Link>
                </li>
              ))}
            </ul>
            );
          }}
        </Async>
      ) : <Empty>Incidents were not requested.</Empty>}
    </Panel>
  );
}

export function InvestigationDetail({
  mode,
  projectId,
  incidentId,
  detail,
  events,
  recs,
}: {
  mode: LiveDefenseMode;
  projectId: string | null;
  incidentId: string;
  detail?: Resource<IncidentDetail>;
  events?: Resource<{ items: MonitoringEvent[] }>;
  recs?: Resource<{ items: DefenseRecommendation[]; label: string }>;
}) {
  if (mode === 'layout-preview') {
    const sample = previewIncident(incidentId);
    if (!sample) return <Empty>That sample incident is not in the layout-preview fixtures.</Empty>;
    return (
      <div className="flex min-w-0 flex-col gap-6" data-testid="live-defense-preview-investigation">
        <Panel title={sample.title} actions={<SourceTag mode="layout-preview" />}>
          <p className="text-sm text-muted">{sample.summary}</p>
          <p className="mt-2 text-xs text-muted">Linked in this sample: {sample.linked.events} events · {sample.linked.findings} findings · {sample.linked.recommendations} recommendations</p>
        </Panel>
        <Panel title="Sample timeline">
          <ol className="flex flex-col gap-2">
            {sample.timeline.map((entry, index) => (
              <li key={`${entry.label}-${index}`} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink">
                <p className="font-medium">{entry.label}</p>
                <p className="text-xs text-muted">{entry.note}</p>
              </li>
            ))}
          </ol>
        </Panel>
        <Link className="text-sm text-accent hover:underline" href={liveDefenseHref(projectId, 'incidents', 'layout-preview')}>Back to sample list</Link>
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {detail ? (
        <Async resource={detail} label="incident">
          {({ incident }) => (
            <>
              <nav aria-label="Investigation trail" className="label-mono text-xs text-muted">
                <Link className="text-accent hover:underline" href={liveDefenseHref(projectId, 'overview')}>Events</Link>
                {' → '}
                <span className="text-ink">Incident</span>
                {' → '}
                <span>Evidence</span>
                {' → '}
                <Link className="text-accent hover:underline" href={liveDefenseHref(projectId, 'explain')}>Recommendation</Link>
                {' → '}
                <Link className="text-accent hover:underline" href={liveDefenseHref(projectId, 'explain')}>Review</Link>
              </nav>
              <Panel title={incident.title} actions={<>{incident.simulated && <Tag tone="warn">Synthetic drill</Tag>}<SourceTag mode="live" /></>}>
                <Lifecycle state={incident.state} />
                <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted">
                  <dt>CSF function</dt><dd className="text-ink">{incident.csf_function}</dd>
                  <dt>Owner</dt><dd>{incident.owner ? <Mono>{incident.owner}</Mono> : 'unassigned'}</dd>
                  <dt>Severity</dt><dd className="text-ink">{incident.severity ?? 'unscored'} · priority {incident.business_priority}</dd>
                  {incident.triage && <><dt>Triage</dt><dd className="text-ink">{incident.triage.decision.replaceAll('_', ' ')} · {incident.triage.category} — {incident.triage.rationale}</dd></>}
                  <dt>Linked</dt>
                  <dd className="text-ink">
                    {incident.finding_ids.length} findings · {incident.monitoring_event_ids.length} events · {incident.verification_ids.length} verifications · {incident.recommendation_ids.length} recommendations
                  </dd>
                </dl>
                <p className="mt-3 text-sm text-muted">
                  To triage, assign, or close this incident, use the{' '}
                  {projectId ? <Link className="text-accent hover:underline" href={`/projects/${projectId}/incidents`}>Incidents workspace</Link> : 'Incidents workspace'}.
                  Live Defense review of a recommendation is not an enforcement approval.
                </p>
              </Panel>
              <Panel title="Linked evidence">
                <EvidenceLinks projectId={projectId} incident={incident} events={events} recs={recs} />
              </Panel>
              <Panel title="Recorded timeline">
                {incident.timeline.length === 0 ? <Empty>No timeline entries.</Empty> : (
                  <ol className="flex flex-col gap-2">
                    {incident.timeline.map((entry, index) => (
                      <li key={`${entry.at}-${index}`} className="rounded-md border border-line bg-panel-2 p-2.5 text-sm text-ink">
                        <div className="flex flex-wrap items-center gap-2">
                          <Tag>{entry.action.replaceAll('_', ' ')}</Tag>
                          {entry.to_state && <Tag tone="info">{STATE_LABEL[entry.to_state]}</Tag>}
                          <span className="text-xs text-muted"><Time iso={entry.at} /> · {entry.actor}</span>
                        </div>
                        {entry.note && <p className="mt-1 text-muted">{entry.note}</p>}
                        {entry.csf_refs.length > 0 && <p className="mt-1 text-xs text-faint">CSF {entry.csf_refs.join(', ')}</p>}
                      </li>
                    ))}
                  </ol>
                )}
              </Panel>
            </>
          )}
        </Async>
      ) : <Empty>Incident detail was not requested.</Empty>}
      <Link className="text-sm text-accent hover:underline" href={liveDefenseHref(projectId, 'incidents')}>Back to investigation list</Link>
    </div>
  );
}

function EvidenceLinks({
  projectId,
  incident,
  events,
  recs,
}: {
  projectId: string | null;
  incident: Incident;
  events?: Resource<{ items: MonitoringEvent[] }>;
  recs?: Resource<{ items: DefenseRecommendation[]; label: string }>;
}) {
  const linkedEvents = useMemo(() => {
    const eventItems = events?.status === 'ready' ? events.data.items : [];
    return incident.monitoring_event_ids.map(id => eventItems.find(item => item.id === id) ?? null);
  }, [incident.monitoring_event_ids, events]);
  const linkedRecs = useMemo(() => {
    const recItems = recs?.status === 'ready' ? recs.data.items : [];
    return incident.recommendation_ids.map(id => recItems.find(item => item.id === id) ?? null);
  }, [incident.recommendation_ids, recs]);
  return (
    <div className="flex flex-col gap-3 text-sm text-ink">
      <div>
        <h3 className="label-mono mb-2 text-xs">Events</h3>
        {incident.monitoring_event_ids.length === 0 ? (
          <p className="text-muted" data-testid="incident-event-correlation">Correlation: unknown — no monitoring events are linked.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {linkedEvents.map((event, index) => {
              const id = incident.monitoring_event_ids[index];
              if (!event) {
                return <li key={id} className="text-muted">Event <Mono>{id}</Mono> · correlation: unknown (not in the loaded feed)</li>;
              }
              return (
                <li key={id}>
                  <Tag>{provenanceLabel(event)}</Tag> {event.type.replaceAll('_', ' ')} · received <Time iso={event.received_at} />
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div>
        <h3 className="label-mono mb-2 text-xs">Recommendations</h3>
        {incident.recommendation_ids.length === 0 ? (
          <p className="text-muted" data-testid="incident-rec-correlation">Correlation: unknown — no stored recommendation is linked.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {linkedRecs.map((rec, index) => {
              const id = incident.recommendation_ids[index];
              if (!rec) {
                return <li key={id} className="text-muted">Recommendation <Mono>{id}</Mono> · correlation: unknown</li>;
              }
              return (
                <li key={id}>
                  <Link className="text-accent hover:underline" href={`${liveDefenseHref(projectId, 'explain')}#${id}`}>{rec.action} on {rec.node ?? 'n/a'}</Link>
                  {' · '}recommendation only
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {incident.finding_ids.length > 0 && (
        <p className="text-xs text-muted">{incident.finding_ids.length} linked finding{incident.finding_ids.length === 1 ? '' : 's'} (open on the Findings page).</p>
      )}
    </div>
  );
}

function Lifecycle({ state }: { state: Incident['state'] }) {
  return (
    <ol className="flex flex-wrap gap-1" aria-label="NIST incident lifecycle">
      {LIFECYCLE.map(step => {
        const current = step === state;
        const past = LIFECYCLE.indexOf(step) < LIFECYCLE.indexOf(state);
        return (
          <li key={step} className={`rounded-full border px-2 py-0.5 text-[11px] ${current ? 'border-brand bg-brand/10 text-ink' : past ? 'border-line text-muted' : 'border-dashed border-line text-faint'}`}>
            {STATE_LABEL[step]}
          </li>
        );
      })}
    </ol>
  );
}
