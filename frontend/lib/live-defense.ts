/**
 * Live Defense dashboard helpers. Live mode reads only control-plane records.
 * Layout-preview samples live in `live-defense-preview.ts` and must never be
 * mixed into a live list or labelled as telemetry.
 */

import type { Incident, MonitoringEvent } from '@hackatl/frontend-transport';

export type LiveDefenseMode = 'live' | 'layout-preview';
export type LiveDefenseTab = 'overview' | 'incidents' | 'explain' | 'apps' | 'controls' | 'readiness' | 'first-responder' | 'executions';

export const PREVIEW_QUERY = 'preview';
export const PREVIEW_VALUE = 'layout';

export const SIMULATION_CAVEAT =
  'Recommendation from a policy trained in an isolated test environment. Not applied automatically.';

export const NO_ENFORCEMENT =
  'Analyst accept is a recorded opinion, not enforcement approval, and does not execute a control. This dashboard does not apply, isolate, rate-limit, or block anything from a review or sample layout. Isolate is never offered. Owned-test rate-limit and exact-source block require a separate authorization endpoint after the deterministic gate. Adapter HTTP 200 is not effectiveness.';

export const OWNED_TEST_LABEL = 'OWNED TEST ENVIRONMENT';

export const OWNED_TEST_AUTHORIZE_COPY =
  'Authorization is a separate owner action from analyst review. It can apply a temporary control only on a registered owned-test loopback app after the deterministic gate. Production is not granted. Adapter HTTP 200 is not effectiveness. RL and rules cannot execute without that authorization.';

export const SHADOW_MODE_COPY =
  'Shadow mode: Ari records ingest and recommendations for authorized staging apps. It does not grant production enforcement and never re-shows an ingest token after issuance.';

export const FIRST_RESPONDER_COPY =
  'First-responder modes are server-enforced: OFF, MONITOR (alias SHADOW), and ASSISTED RESPONSE. AUTO-EXECUTE does not exist. Scout and Diagnose may execute a real authorized agent loop when a mapping and snapshot exist. That execution is not independent verification and not a confirmed threat. Probe still needs AuthorizedScope; Patch stays worktree-only. An RL recommendation is not executed defense.';

export const EVIDENCE_STATES = ['Observed', 'Suspected', 'Verified', 'Recommended', 'Authorized', 'Executed', 'Failed', 'Rolled back'] as const;

export const SHADOW_VS_CONTROLS_COPY =
  'Recommendation-only shadow path: ingest, LivePolicyDecision, analyst review, and the pilot report never execute a control. The Controls tab is a separate owned-test authorization. RL-gated pilot apps are server-enforced shadow-only: no grant, channel, or execute.';

export const RL_GATED_COPY =
  'RL-gated shadow-only. Server refuses grants, control channels, authorization, and leftover execute. Analyst accept is still a recorded opinion. This is not customer deployment.';

export const RL_BLOCKED_COPY =
  'Phase 6 CONDITIONAL GO is rules-based shadow only. The RL-gated pilot stays blocked until the RL owner supplies evidence. Merged research PRs, unit tests, and docs are not model readiness.';

export const HEALTH_UNKNOWN_COPY =
  'Worker, telemetry, and policy stay unknown. Heartbeat and queue depth are measured timestamps or counts, not a healthy verdict. Missing values are unavailable — never guessed. HTTP 200 from this page is not health.';

export function liveDefenseMode(previewParam: string | null | undefined): LiveDefenseMode {
  return previewParam === PREVIEW_VALUE ? 'layout-preview' : 'live';
}

/** Keep the current path and swap only the layout-preview flag. */
export function withLiveDefenseMode(href: string, mode: LiveDefenseMode): string {
  const cut = href.indexOf('?');
  const path = cut === -1 ? href : href.slice(0, cut);
  const params = new URLSearchParams(cut === -1 ? '' : href.slice(cut + 1));
  if (mode === 'layout-preview') params.set(PREVIEW_QUERY, PREVIEW_VALUE);
  else params.delete(PREVIEW_QUERY);
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export function liveDefenseHref(projectId: string | null, tab: LiveDefenseTab = 'overview', mode: LiveDefenseMode = 'live'): string {
  const base = projectId === null ? '/live-defense-pilot' : `/projects/${projectId}/live-defense`;
  const suffix = tab === 'overview' ? '' : tab === 'incidents' ? '/incidents' : tab === 'explain' ? '/explain' : tab === 'controls' ? '/controls' : tab === 'readiness' ? '/readiness' : tab === 'first-responder' ? '/first-responder' : tab === 'executions' ? '/executions' : '/apps';
  const path = `${base}${suffix}`;
  return projectId === null ? path : withLiveDefenseMode(path, mode);
}

export function liveDefenseTab(pathname: string): LiveDefenseTab {
  if (/live-defense(?:-pilot)?\/explain(?:\/|$)/.test(pathname)) return 'explain';
  if (/live-defense(?:-pilot)?\/incidents(?:\/|$)/.test(pathname)) return 'incidents';
  if (/live-defense(?:-pilot)?\/controls(?:\/|$)/.test(pathname)) return 'controls';
  if (/live-defense(?:-pilot)?\/readiness(?:\/|$)/.test(pathname)) return 'readiness';
  if (/live-defense(?:-pilot)?\/first-responder(?:\/|$)/.test(pathname)) return 'first-responder';
  if (/live-defense(?:-pilot)?\/executions(?:\/|$)/.test(pathname)) return 'executions';
  if (/live-defense(?:-pilot)?\/apps(?:\/|$)/.test(pathname)) return 'apps';
  return 'overview';
}

/** Endpoints this UI already consumes. Do not invent siblings. */
export const USED_LIVE_APIS = [
  { method: 'GET', path: '/projects/{id}', use: '24/7 defense switch and project name' },
  { method: 'PATCH', path: '/projects/{id}', use: 'owner toggle of monitoring_policy.enabled' },
  { method: 'GET', path: '/projects/{id}/events', use: 'recorded webhook and runtime events' },
  { method: 'GET', path: '/projects/{id}/first-responder/investigations', use: 'bounded investigation list (Executions tab)' },
  { method: 'GET', path: '/first-responder/investigations/{decisionId}', use: 'investigation timeline: events, mapping, snapshot, invocations, handoff' },
  { method: 'GET', path: '/first-responder/invocations/{invocationId}', use: 'agent execution detail: queue state + optional execution record' },
  { method: 'GET', path: '/projects/{id}/first-responder/diagnostics', use: 'operational diagnostics: stalled jobs, failures, waiting queue, redacted errors' },
  { method: 'GET', path: '/projects/{id}/reassessments', use: 're-checks those events requested' },
  { method: 'GET', path: '/projects/{id}/incidents', use: 'NIST incident list' },
  { method: 'GET', path: '/incidents/{id}', use: 'incident timeline and linked evidence' },
  { method: 'GET', path: '/projects/{id}/defense-recommendations', use: 'simulation-derived policy suggestions' },
  { method: 'GET', path: '/defense-recommendations/{id}/explanation', use: 'occlusion explanation of one suggestion' },
  { method: 'GET', path: '/projects/{id}/rl-evaluations', use: 'imported held-out evaluation pointers' },
  { method: 'GET/POST', path: '/projects/{id}/applications', use: 'staging-app identity (sandbox or owned_test)' },
  { method: 'POST', path: '/applications/{id}/ingest-tokens', use: 'mint ingest token (shown once)' },
  { method: 'POST', path: '/applications/{id}/ingest-tokens/rotate', use: 'rotate ingest credential; prior tokens revoked; secret shown once' },
  { method: 'POST', path: '/ingest-tokens/{id}/revoke', use: 'revoke one ingest token' },
  { method: 'POST', path: '/applications/{id}/disconnect', use: 'disconnect staging app and revoke ingest' },
  { method: 'POST', path: '/applications/{id}/shadow-verify', use: 'recompute verification from recorded ingest; not a healthy verdict' },
  { method: 'GET', path: '/projects/{id}/ingest-tokens', use: 'token metadata; secret never returned' },
  { method: 'GET', path: '/projects/{id}/defense-health', use: 'recorded worker/ingest timestamps; worker/telemetry/policy stay unknown' },
  { method: 'GET', path: '/projects/{id}/pilot-report', use: 'exportable shadow-pilot snapshot; HTTP 200 is not attack success' },
  { method: 'GET', path: '/projects/{id}/rl-readiness', use: 'backend RL-gated readiness; ready=true only if every mandatory category is PASS' },
  { method: 'POST', path: '/applications/{id}/enable-rl-gated-pilot', use: 'server-enforced shadow-only; fails closed if leftover controls remain' },
  { method: 'GET', path: '/projects/{id}/defense-graph', use: 'recorded apps and ingest routes; missing checks stay unknown' },
  { method: 'GET/POST', path: '/defense-recommendations/{id}/reviews', use: 'analyst accept/reject/needs-investigation; accept does not execute' },
  { method: 'GET', path: '/projects/{id}/live-policy-decisions', use: 'shadow decisions from rules or validated v6; NOT EXECUTED' },
  { method: 'GET/POST', path: '/live-policy-decisions/{id}/reviews', use: 'analyst review of a shadow decision; accept does not execute' },
  { method: 'GET/POST', path: '/projects/{id}/control-grants', use: 'owner grant for exact kinds on one owned-test app' },
  { method: 'POST', path: '/applications/{id}/control-channel', use: 'register loopback control port; HMAC secret shown once' },
  { method: 'POST', path: '/applications/{id}/control-authorizations', use: 'separate authorization endpoint; gate is final authority' },
  { method: 'GET', path: '/projects/{id}/live-controls', use: 'pending/active owned-test controls; not NIST incidents' },
  { method: 'POST', path: '/live-controls/{id}/rollback', use: 'request rollback; success only after verify' },
  { method: 'POST', path: '/projects/{id}/emergency-stop', use: 'kill switch; does not claim success until verified' },
  { method: 'GET/POST', path: '/projects/{id}/first-responder/mode', use: 'owner-attributable OFF / MONITOR|SHADOW / ASSISTED RESPONSE; AUTO-EXECUTE rejected' },
  { method: 'GET', path: '/projects/{id}/first-responder/decisions', use: 'versioned deterministic first-responder records; not executed; no invented probabilities' },
  { method: 'GET', path: '/projects/{id}/first-responder/handoffs', use: 'analyst handoff; delivered only after provider confirmation' },
  { method: 'GET', path: '/projects/{id}/first-responder/capabilities', use: 'seven-agent registry from real AgentSpecs' },
  { method: 'GET', path: '/projects/{id}/first-responder/invocations', use: 'genuine agent-loop outcomes: executed vs skipped vs unverified; cost and latency when a loop ran' },
] as const;

/** Capabilities still missing. Shown as missing — never filled with live-looking numbers. */
export const MISSING_LIVE_APIS = [
  {
    id: 'live_incidents',
    title: 'Live-defense incident machine',
    reason: 'DETECTED → TRIAGED → RECOMMENDED → AUTHORIZED → EXECUTING → VERIFYING is not an API. Existing incidents stay on the NIST human lifecycle. Phase 4 uses a separate LiveControl lifecycle.',
  },
  {
    id: 'autonomous',
    title: 'Autonomous enforcement / AUTO-EXECUTE',
    reason: 'Phase 7C executes authorized investigative agents (Scout/Diagnose) only. AUTO-EXECUTE does not exist. Probe/Patch privileges are not expanded. RL recommendations are not executed defense. Phase 7B RL training is not this work.',
  },
  {
    id: 'production_grants',
    title: 'Production environment grants',
    reason: 'StagingEnvironment still rejects production. Controls are owned-test/sandbox only.',
  },
] as const;

export interface OverviewCounts {
  monitoringEnabled: boolean;
  eventsKnown: boolean;
  eventCount: number;
  simulatedEventCount: number;
  incidentsKnown: boolean;
  openIncidentCount: number;
  simulatedIncidentCount: number;
  recommendationsKnown: boolean;
  recommendationCount: number;
  appsKnown: boolean;
  appCount: number;
  healthKnown: boolean;
  lastIngestAt: string | null;
  shadowKnown: boolean;
  shadowCount: number;
}

export function liveDefenseOverviewCopy(counts: OverviewCounts): string {
  const watch = counts.monitoringEnabled
    ? '24/7 defense is on for this project (repository push re-checks).'
    : '24/7 defense is off for this project.';
  const events = !counts.eventsKnown
    ? 'Recorded events have not loaded.'
    : counts.eventCount === 0
      ? 'No recorded events yet.'
      : `${counts.eventCount} recorded event${counts.eventCount === 1 ? '' : 's'}${counts.simulatedEventCount > 0 ? `, ${counts.simulatedEventCount} labelled simulated` : ''}.`;
  const incidents = !counts.incidentsKnown
    ? 'Incidents have not loaded.'
    : counts.openIncidentCount === 0
      ? 'No open incidents.'
      : `${counts.openIncidentCount} open incident${counts.openIncidentCount === 1 ? '' : 's'}${counts.simulatedIncidentCount > 0 ? `, ${counts.simulatedIncidentCount} labelled synthetic` : ''}.`;
  const recs = !counts.recommendationsKnown
    ? 'Simulation recommendations have not loaded.'
    : counts.recommendationCount === 0
      ? 'No simulation recommendation is stored.'
      : `${counts.recommendationCount} simulation recommendation${counts.recommendationCount === 1 ? '' : 's'} listed; none is applied automatically.`;
  const shadow = !counts.shadowKnown
    ? 'Shadow decisions have not loaded.'
    : counts.shadowCount === 0
      ? 'No shadow decision is stored.'
      : `${counts.shadowCount} shadow decision${counts.shadowCount === 1 ? '' : 's'} recorded; none executed.`;
  const apps = !counts.appsKnown
    ? 'Staging apps have not loaded.'
    : counts.appCount === 0
      ? 'No staging app is registered.'
      : `${counts.appCount} staging app${counts.appCount === 1 ? '' : 's'} registered in shadow mode.`;
  const ingest = !counts.healthKnown
    ? 'Ingest timestamps have not loaded.'
    : counts.lastIngestAt
      ? 'Runtime ingest has been recorded (it may include replay or simulated windows); health stays unknown without a health-check producer.'
      : 'No ingest recorded; health is unknown.';
  return `${watch} ${events} ${incidents} ${recs} ${shadow} ${apps} ${ingest}`;
}

export interface RankedAction {
  action: string;
  node: string | null;
  q_value: number;
  rank: number;
  /** 0–1 bar length from |q| relative to the largest |q| in the set. */
  bar: number;
}

/** Rank stored Q-values. Empty input stays empty — never invent a confidence score. */
export function rankQValues(actions: readonly { action: string; node: string | null; q_value: number }[]): RankedAction[] {
  if (actions.length === 0) return [];
  const sorted = [...actions].sort((a, b) => b.q_value - a.q_value || a.action.localeCompare(b.action));
  const peak = Math.max(...sorted.map(a => Math.abs(a.q_value)), Number.EPSILON);
  return sorted.map((action, index) => ({
    ...action,
    rank: index + 1,
    bar: Math.abs(action.q_value) / peak,
  }));
}

export function actionInWords(action: string, node: string | null): string {
  const target = node ? ` on ${node}` : '';
  switch (action) {
    case 'noop': return 'Do nothing extra';
    case 'raise_monitoring': return `Watch more closely${target}`;
    case 'rate_limit': return `Slow traffic${target} (not applied)`;
    case 'isolate': return `Isolate${target} (not applied)`;
    case 'request_reassessment': return 'Ask for a re-check';
    case 'escalate_to_human': return 'Escalate to a person';
    case 'block_principal': return 'Block a flagged identity (not applied)';
    case 'block_principal_long': return 'Longer block of a flagged identity (not applied)';
    case 'block_top_principal_long': return 'Longer block of the heaviest flagged identity (not applied)';
    default: return action.replaceAll('_', ' ');
  }
}

export type Sourced<T> = T & { source: LiveDefenseMode };

/** Refuse to render a mixed live + preview collection. */
export function assertSingleSource<T extends { source: LiveDefenseMode }>(items: readonly T[], label: string): LiveDefenseMode | null {
  if (items.length === 0) return null;
  const first = items[0]?.source;
  if (first === undefined) return null;
  if (items.some(item => item.source !== first)) {
    throw new Error(`Live Defense refused to mix live records with layout-preview samples (${label}).`);
  }
  return first;
}

export function isOpenIncidentState(state: string): boolean {
  return state !== 'closed' && state !== 'rejected';
}

export function eventIncidentIds(eventId: string, incidents: readonly Incident[]): string[] {
  return incidents.filter(item => item.monitoring_event_ids.includes(eventId)).map(item => item.id);
}

export function provenanceLabel(event: Pick<MonitoringEvent, 'source' | 'simulated' | 'test_event'>): string {
  // Authorized onboarding test events travel the real authenticated channel but are
  // never regular telemetry — say so before any ingest-channel claim.
  if (event.test_event) return 'Onboarding test event (authorized)';
  if (event.source === 'runtime' && !event.simulated) return 'Authenticated runtime ingest';
  if (event.source === 'runtime' && event.simulated) return 'Runtime ingest (simulated / replay)';
  if (event.source === 'github' && event.simulated) return 'GitHub delivery (replayed / fixture)';
  if (event.source === 'github') return 'Signed GitHub delivery';
  return event.simulated ? `${event.source} (simulated)` : event.source;
}

export function unavailable(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return 'unavailable';
  return String(value);
}

/** The only source IP authz and the adapter accept for block_source (RFC 5737 TEST-NET-2). */
export const ISOLATED_FIXTURE_SOURCE = '198.51.100.66';

/**
 * Owned-test probe header. Spoofable; never production identity or attribution.
 * Honored only on the loopback fixture for allowlisted probe IPs.
 */
export const FIXTURE_SOURCE_HEADER = 'x-ari-source';

export const PENDING_CONTROL_STATUSES = [
  'pending_authorization',
  'authorized',
  'executing',
  'verifying',
] as const;

export const ACTIVE_CONTROL_STATUSES = ['verified'] as const;

export function isPendingControl(status: string): boolean {
  return (PENDING_CONTROL_STATUSES as readonly string[]).includes(status);
}

export function isActiveControl(status: string): boolean {
  return (ACTIVE_CONTROL_STATUSES as readonly string[]).includes(status);
}

export function controlKindLabel(kind: string): string {
  switch (kind) {
    case 'rate_limit': return 'Request-scoped rate limit';
    case 'block_source': return 'Exact-source block';
    case 'increase_logging': return 'Increase logging';
    case 'escalate': return 'Escalate to human';
    default: return kind.replaceAll('_', ' ');
  }
}

export function ttlRemaining(expiresAt: string | null, nowMs = Date.now()): string {
  if (!expiresAt) return 'unavailable';
  const remaining = Date.parse(expiresAt) - nowMs;
  if (Number.isNaN(remaining)) return 'unavailable';
  if (remaining <= 0) return 'expired on the app (TTL is local to the fixture)';
  const seconds = Math.round(remaining / 1000);
  return `${seconds}s remaining (app-local TTL is independent of this worker)`;
}

export function ingestCurl(projectId: string): string {
  return `curl -X POST "$ARI_API/api/v1/ingest/runtime/${projectId}" \\\n  -H "Authorization: Bearer $INGEST_TOKEN" \\\n  -H "Content-Type: application/json" \\\n  -d '{"source":"owned_staging","window_start":"<utc>","window_end":"<utc>","routes":[{"route":"GET /health","requests":1}]}'`;
}
