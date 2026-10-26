'use client';

import type { Assessment, AssessmentState, StreamEvent } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useState } from 'react';
import { PillarLoop } from '@/components/pillar-loop';
import { ErrorState, Time } from '@/components/ui';
import { api } from '@/lib/api';
import {
  EVENT_LOG_PAGE_NOTE, JOURNEY_STATUS, fullEventLog, latestAssessmentEvent,
  projectJourney, statesFromLog,
} from '@/lib/journey';
import { pillarLoop } from '@/lib/pillars';
import { TERMINAL_STATES } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

export function PillarLoopSection(props: {
  projectId: string; repoCount: number; activeScopeCount: number | null; monitoringEnabled: boolean;
  assessmentCount: number | null; findings: readonly { kind: string; status: string }[] | null;
}) {
  const monitoring = useResource(`loop-mon:${props.projectId}`, signal => api().monitoringEvents(props.projectId, { signal }), 15000);
  const reassess = useResource(`loop-re:${props.projectId}`, signal => api().reassessments(props.projectId, { signal }), 15000);
  return (
    <PillarLoop nodes={pillarLoop({
      ...props,
      monitoringEventCount: monitoring.status === 'ready' ? monitoring.data.items.length : null,
      reassessmentCount: reassess.status === 'ready' ? reassess.data.items.length : null,
    })} />
  );
}

export function ProjectJourney({
  projectId, repoCount, activeScopeCount, monitoringEnabled, assessments, findingStatuses,
}: {
  projectId: string;
  repoCount: number;
  activeScopeCount: number;
  monitoringEnabled: boolean;
  assessments: readonly Assessment[];
  findingStatuses: readonly string[];
}) {
  const latest = [...assessments].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0] ?? null;
  // The log grows while the check runs, so poll it until the latest check is terminal. The state is
  // part of the key so a phase change refetches at once and `visited` never lags the badge.
  const latestRunning = latest !== null && !TERMINAL_STATES.has(latest.state);
  const events = useResource(
    `journey-events:${latest?.id ?? 'none'}:${latest?.state ?? 'none'}`,
    signal => latest
      ? fullEventLog(after => api().eventsLog(latest.id, after, { signal }))
      : Promise.resolve({ items: [], truncated: false }),
    latestRunning ? 5000 : undefined,
  );
  // Keep showing the previous log of the same check while the next one loads, instead of flashing "Loading".
  const [lastLog, setLastLog] = useState<{ id: string; items: StreamEvent[]; truncated: boolean } | null>(null);
  if (events.status === 'ready' && latest && lastLog?.items !== events.data.items) {
    setLastLog({ id: latest.id, items: events.data.items, truncated: events.data.truncated });
  }
  const shownLog = events.status === 'ready' ? events.data : lastLog && lastLog.id === latest?.id ? lastLog : null;
  const logItems = shownLog?.items ?? null;
  const monitoring = useResource(
    `journey-mon:${projectId}`,
    signal => api().monitoringEvents(projectId, { signal }),
    15000,
  );
  const logReady = latest === null || events.status !== 'loading' || logItems !== null;
  const visited = logItems ? statesFromLog(logItems) : null;
  const logTruncated = shownLog?.truncated === true;
  const monitoringItems = monitoring.status === 'ready' ? monitoring.data.items : [];
  const stages = projectJourney({
    repoCount,
    activeScopeCount,
    current: (latest?.state ?? null) as AssessmentState | null,
    visited: logReady ? visited : null,
    findingStatuses,
    monitoringEnabled,
    monitoringKnown: monitoring.status === 'ready',
    monitoringEventCount: monitoringItems.length,
    monitoringTruncated: monitoringItems.length >= 50,
    monitoringFailed: monitoringItems.some(event => event.status === 'failed'),
    newestMonitoringSimulated: monitoringItems[0]?.simulated === true,
    eventLogTruncated: logTruncated,
  });
  const latestEvent = logItems ? latestAssessmentEvent(logItems) : null;
  const assessmentHref = latest ? `/projects/${projectId}/assessments/${latest.id}` : null;

  return (
    <section aria-label="Check progress" className="min-w-0 rounded-xl border border-line bg-panel p-5 sm:p-6" data-testid="project-journey">
      <h2 className="text-sm font-semibold tracking-tight">Where this check is</h2>
      {!logReady ? <p className="mt-2 text-sm text-muted">Loading the recorded phases…</p> : (
        <>
          {logTruncated && (
            <p className="mt-2 text-sm text-muted" data-testid="event-log-page-note">{EVENT_LOG_PAGE_NOTE}</p>
          )}
          <ol className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {stages.map(item => {
              const href = item.id === 'monitor' ? `/projects/${projectId}/monitoring` : assessmentHref;
              return (
                <li key={item.id} className="rounded-lg border border-line p-3" data-testid={`journey-${item.id}`}>
                  <p className="text-sm font-medium">{item.label}</p>
                  <p className="text-xs text-muted">{JOURNEY_STATUS[item.status]}</p>
                  <p className="mt-1 text-xs text-muted">{item.detail}</p>
                  {href && <Link className="mt-2 inline-block text-xs text-accent hover:underline" href={href}>Open</Link>}
                </li>
              );
            })}
          </ol>
        </>
      )}
      {latestEvent && (
        <p className="mt-3 text-sm text-muted" data-testid="project-latest-event">
          Latest recorded event: {latestEvent.message} <Time iso={latestEvent.at} />
        </p>
      )}
      {events.status === 'ready' && events.refreshError !== undefined && <p role="status" className="mt-3 text-xs text-warn">Could not refresh the event log. The phases shown are from the last successful load.</p>}
      {events.status === 'error' && <div className="mt-3"><ErrorState error={events.error} onRetry={events.reload} context="Event log" /></div>}
      {monitoring.status === 'error' && <div className="mt-3"><ErrorState error={monitoring.error} onRetry={monitoring.reload} context="Monitoring events" /></div>}
    </section>
  );
}
