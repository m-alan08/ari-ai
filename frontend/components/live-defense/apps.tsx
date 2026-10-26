'use client';

import type {
  ConnectedApplication,
  ControlChannelCreated,
  ControlGrant,
  DefenseHealth,
  IngestTokenCreated,
  IngestTokenRotated,
  IngestTokenView,
  LiveControlKind,
} from '@hackatl/frontend-transport';
import { useState } from 'react';
import { OwnedTestBanner, ShadowModeNote, SourceTag } from '@/components/live-defense/common';
import { Async, Button, Empty, ErrorState, Field, inputClass, Mono, Panel, Tag, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { controlKindLabel, ingestCurl, OWNED_TEST_LABEL, RL_GATED_COPY, type LiveDefenseMode } from '@/lib/live-defense';
import type { Resource } from '@/lib/use-resource';

export function StagingApps({
  mode,
  projectId,
  apps,
  tokens,
  health,
  grants,
  onChange,
}: {
  mode: LiveDefenseMode;
  projectId: string | null;
  apps?: Resource<{ items: ConnectedApplication[] }>;
  tokens?: Resource<{ items: IngestTokenView[] }>;
  health?: Resource<DefenseHealth>;
  grants?: Resource<{ items: ControlGrant[] }>;
  onChange?: () => void;
}) {
  if (mode === 'layout-preview') {
    return (
      <Panel title="Sample staging-app chrome" actions={<SourceTag mode="layout-preview" />}>
        <p className="text-sm text-ink">Sample layout only. No app is registered and no ingest token is issued here.</p>
      </Panel>
    );
  }
  if (!projectId) return <Empty>Choose a project to register a staging app.</Empty>;
  return (
    <div className="flex min-w-0 flex-col gap-6" data-testid="live-defense-apps">
      <OwnedTestBanner />
      <ShadowModeNote />
      <RegisterApp projectId={projectId} onChange={onChange} />
      <GrantForm projectId={projectId} apps={apps} grants={grants} onChange={onChange} />
      <Panel title="Registered staging apps" actions={<SourceTag mode="live" />}>
        {apps ? (
          <Async resource={apps} label="staging apps">
            {page => page.items.length === 0 ? (
              <Empty>No staging app yet. Only sandbox and owned-test environments are allowed. Production enforcement grants are not offered.</Empty>
            ) : (
              <ul className="flex flex-col gap-3">
                {page.items.map(app => (
                  <AppCard key={app.id} app={app} projectId={projectId} tokens={tokens} onChange={onChange} />
                ))}
              </ul>
            )}
          </Async>
        ) : <Empty>Apps were not requested.</Empty>}
      </Panel>
      <PilotReportPanel projectId={projectId} />
      <Panel title="Ingest guidance">
        <p className="text-sm text-ink">Send aggregated per-route counters to the existing ingest endpoint. Never put raw logs, payloads, or secrets in the batch.</p>
        <pre className="mt-3 overflow-x-auto rounded-md border border-line bg-panel-2 p-3 text-xs text-ink">{ingestCurl(projectId)}</pre>
        <p className="mt-2 text-xs text-muted">The token is shown once at issuance. Later lists return metadata only (id, last used, revoked). Owners revoke from this page.</p>
        {health ? (
          <Async resource={health} label="ingest health">
            {item => (
              <p className="mt-3 text-sm text-ink">
                Last ingest window: {item.last_ingest_at ? <Time iso={item.last_ingest_at} /> : 'unavailable'}.
                Status remains <strong>{item.status}</strong> — {item.status_reason}
              </p>
            )}
          </Async>
        ) : null}
      </Panel>
    </div>
  );
}

function RegisterApp({ projectId, onChange }: { projectId: string; onChange?: () => void }) {
  const [name, setName] = useState('');
  const [environment, setEnvironment] = useState<'sandbox' | 'owned_test'>('sandbox');
  const [gated, setGated] = useState(false);
  const [status, setStatus] = useState<'idle' | 'saving' | 'error'>('idle');
  const [error, setError] = useState<unknown>(null);
  const submit = async () => {
    setStatus('saving');
    setError(null);
    try {
      await api().createConnectedApplication(projectId, { name: name.trim(), environment, rl_gated_pilot: gated });
      setName('');
      setStatus('idle');
      onChange?.();
    } catch (err) {
      setError(err);
      setStatus('error');
    }
  };
  return (
    <Panel title="Register an authorized staging app">
      <p className="mb-3 text-sm text-muted">Identity and environment class only. Ari will not grant production enforcement from this form.</p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <Field label="Name">
          <input className={inputClass} value={name} onChange={e => setName(e.target.value)} maxLength={500} required />
        </Field>
        <Field label="Environment">
          <select className={inputClass} value={environment} onChange={e => setEnvironment(e.target.value as 'sandbox' | 'owned_test')}>
            <option value="sandbox">sandbox</option>
            <option value="owned_test">owned test</option>
          </select>
        </Field>
        <Button variant="primary" disabled={status === 'saving' || name.trim().length === 0} onClick={() => void submit()}>Register app</Button>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={gated} onChange={e => setGated(e.target.checked)} data-testid="live-defense-register-gated" />
        RL-gated shadow-only (server refuses grants, channels, and execute)
      </label>
      {status === 'error' && error !== null && <div className="mt-2"><ErrorState error={error} onRetry={() => void submit()} context="Register app" /></div>}
    </Panel>
  );
}

function AppCard({
  app,
  projectId,
  tokens,
  onChange,
}: {
  app: ConnectedApplication;
  projectId: string;
  tokens?: Resource<{ items: IngestTokenView[] }>;
  onChange?: () => void;
}) {
  const [issued, setIssued] = useState<IngestTokenCreated | IngestTokenRotated | null>(null);
  const [error, setError] = useState<unknown>(null);
  const disconnected = app.verification === 'disconnected' || app.disconnected_at !== null;
  const gated = app.rl_gated_pilot === true;
  const mint = async () => {
    setError(null);
    try {
      setIssued(await api().createApplicationIngestToken(app.id));
      onChange?.();
    } catch (err) {
      setError(err);
    }
  };
  const rotate = async () => {
    setError(null);
    try {
      setIssued(await api().rotateApplicationIngestToken(app.id));
      onChange?.();
    } catch (err) {
      setError(err);
    }
  };
  const disconnect = async () => {
    setError(null);
    try {
      await api().disconnectConnectedApplication(app.id);
      onChange?.();
    } catch (err) {
      setError(err);
    }
  };
  const verify = async () => {
    setError(null);
    try {
      await api().shadowVerifyConnectedApplication(app.id);
      onChange?.();
    } catch (err) {
      setError(err);
    }
  };
  const enableGated = async () => {
    setError(null);
    try {
      await api().enableRlGatedPilot(app.id);
      onChange?.();
    } catch (err) {
      setError(err);
    }
  };
  const appTokens = tokens?.status === 'ready' ? tokens.data.items.filter(item => item.application_id === app.id) : [];
  return (
    <li className="rounded-md border border-line bg-panel-2 p-3 text-sm text-ink" data-testid={`live-defense-app-${app.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium">{app.name}</p>
        <span className="flex flex-wrap gap-1">
          <Tag>{app.environment.replaceAll('_', ' ')}</Tag>
          {(app.environment === 'owned_test' || app.environment === 'sandbox') && <Tag tone="ok">{OWNED_TEST_LABEL}</Tag>}
          <Tag tone="info">shadow ingest</Tag>
          {gated ? <Tag tone="ok">RL-gated shadow-only</Tag> : <Tag>owned-test controls allowed</Tag>}
          <Tag>{app.verification.replaceAll('_', ' ')}</Tag>
          {app.control_channel_configured ? <Tag>control channel registered</Tag> : <Tag>no control channel</Tag>}
        </span>
      </div>
      <p className="mt-1 text-xs text-muted">
        Ingest configured: {app.ingest_configured ? 'yes' : 'no'} · last seen {app.last_ingest_at ? <Time iso={app.last_ingest_at} /> : 'unavailable'}
      </p>
      <p className="mt-2 text-xs text-muted">POST {`/api/v1/ingest/runtime/${projectId}`} with Bearer token. Token secret is not stored in recoverable form.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {!disconnected && (
          <>
            <Button className="!px-2 !py-0.5 text-xs" onClick={() => void mint()}>Issue ingest token</Button>
            <Button className="!px-2 !py-0.5 text-xs" onClick={() => void rotate()}>Rotate credential</Button>
          </>
        )}
        <Button className="!px-2 !py-0.5 text-xs" onClick={() => void verify()}>Verify from ingest records</Button>
        {!disconnected && !gated && (
          <Button className="!px-2 !py-0.5 text-xs" onClick={() => void enableGated()}>Enable RL-gated shadow-only</Button>
        )}
        {!disconnected && (
          <Button className="!px-2 !py-0.5 text-xs" onClick={() => void disconnect()}>Disconnect app</Button>
        )}
      </div>
      {gated && (
        <p className="mt-2 text-xs text-muted" data-testid="live-defense-app-gated">
          {RL_GATED_COPY}
        </p>
      )}
      {disconnected && (
        <p className="mt-2 text-xs text-muted" data-testid="live-defense-app-disconnected">
          Disconnected. Ingest is rejected. This is not a production block or isolate.
        </p>
      )}
      {error !== null && <div className="mt-2"><ErrorState error={error} /></div>}
      {issued && (
        <div className="mt-3 rounded-md border border-warn/50 bg-warn/10 p-2 text-xs" data-testid="live-defense-token-once">
          <p className="font-medium text-warn">Copy this token now. Ari will not show it again.</p>
          <Mono className="mt-1 block break-all text-ink">{issued.token}</Mono>
        </div>
      )}
      {!gated && <ControlChannelForm app={app} onChange={onChange} />}
      {appTokens.length > 0 && (
        <ul className="mt-3 text-xs text-muted">
          {appTokens.map(token => (
            <li key={token.id} className="flex flex-wrap items-center gap-2 py-1">
              <Mono>{token.id}</Mono>
              {token.revoked_at ? <Tag tone="warn">revoked</Tag> : <Tag tone="ok">active</Tag>}
              last used {token.last_used_at ? <Time iso={token.last_used_at} /> : 'unavailable'}
              {!token.revoked_at && (
                <Button className="!h-6 !px-2 !py-0 text-[11px]" onClick={() => { void api().revokeIngestToken(token.id).then(() => onChange?.()); }}>
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

const GRANT_KINDS: LiveControlKind[] = ['rate_limit', 'block_source', 'increase_logging', 'escalate'];

function GrantForm({
  projectId,
  apps,
  grants,
  onChange,
}: {
  projectId: string;
  apps?: Resource<{ items: ConnectedApplication[] }>;
  grants?: Resource<{ items: ControlGrant[] }>;
  onChange?: () => void;
}) {
  const appItems = apps?.status === 'ready' ? apps.data.items : [];
  const [appId, setAppId] = useState('');
  const selectedAppId = appId || appItems[0]?.id || '';
  const [kinds, setKinds] = useState<LiveControlKind[]>([...GRANT_KINDS]);
  const [maxDuration, setMaxDuration] = useState('300');
  const [error, setError] = useState<unknown>(null);
  const toggle = (kind: LiveControlKind) => {
    setKinds(current => current.includes(kind) ? current.filter(item => item !== kind) : [...current, kind]);
  };
  const submit = async () => {
    setError(null);
    try {
      await api().createControlGrant(projectId, {
        application_id: selectedAppId,
        allowed_kinds: kinds,
        max_duration_s: Number(maxDuration),
        hourly_budget: 5,
        expires_at: null,
      });
      onChange?.();
    } catch (err) {
      setError(err);
    }
  };
  return (
    <Panel title="Owner grant (exact kinds on one app)">
      <p className="mb-3 text-sm text-muted">Grants do not execute. They bound later authorizations to this app, these kinds, and this duration ceiling.</p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <Field label="Application">
          <select className={inputClass} value={selectedAppId} onChange={e => setAppId(e.target.value)}>
            {appItems.map(app => <option key={app.id} value={app.id}>{app.name}</option>)}
          </select>
        </Field>
        <Field label="Max duration (seconds)">
          <input className={inputClass} type="number" min={5} max={300} value={maxDuration} onChange={e => setMaxDuration(e.target.value)} />
        </Field>
        <Button variant="primary" disabled={selectedAppId.length === 0 || kinds.length === 0 || appItems.find(item => item.id === selectedAppId)?.rl_gated_pilot === true} onClick={() => void submit()}>Create grant</Button>
      </div>
      <fieldset className="mt-3">
        <legend className="text-xs text-muted">Allowed kinds</legend>
        <div className="mt-1 flex flex-wrap gap-3 text-sm text-ink">
          {GRANT_KINDS.map(kind => (
            <label key={kind} className="flex items-center gap-1">
              <input type="checkbox" checked={kinds.includes(kind)} onChange={() => toggle(kind)} />
              {controlKindLabel(kind)}
            </label>
          ))}
        </div>
      </fieldset>
      {error !== null && <div className="mt-2"><ErrorState error={error} /></div>}
      {grants ? (
        <Async resource={grants} label="control grants">
          {page => page.items.length === 0 ? <p className="mt-3 text-xs text-muted">No grant yet.</p> : (
            <ul className="mt-3 text-xs text-muted">
              {page.items.map(grant => (
                <li key={grant.id} className="flex flex-wrap items-center gap-2 py-1">
                  <Mono>{grant.id}</Mono>
                  {grant.allowed_kinds.map(kind => <Tag key={kind}>{kind}</Tag>)}
                  {grant.revoked_at ? <Tag tone="warn">revoked</Tag> : <Tag tone="ok">active</Tag>}
                  {!grant.revoked_at && (
                    <Button className="!h-6 !px-2 !py-0 text-[11px]" onClick={() => { void api().revokeControlGrant(grant.id).then(() => onChange?.()); }}>
                      Revoke grant
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Async>
      ) : null}
    </Panel>
  );
}

function ControlChannelForm({ app, onChange }: { app: ConnectedApplication; onChange?: () => void }) {
  const [port, setPort] = useState('8099');
  const [issued, setIssued] = useState<ControlChannelCreated | null>(null);
  const [error, setError] = useState<unknown>(null);
  const submit = async () => {
    setError(null);
    try {
      setIssued(await api().createControlChannel(app.id, { port: Number(port) }));
      onChange?.();
    } catch (err) {
      setError(err);
    }
  };
  return (
    <div className="mt-3 border-t border-line pt-3" data-testid={`live-defense-channel-${app.id}`}>
      <p className="text-xs text-muted">
        Register a loopback control port only. Arbitrary URLs, scripts, and browser-supplied destinations are rejected.
        HMAC secret is shown once.
      </p>
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <Field label="Loopback port">
          <input className={inputClass} type="number" min={1024} max={65535} value={port} onChange={e => setPort(e.target.value)} />
        </Field>
        <Button className="!px-2 !py-0.5 text-xs" onClick={() => void submit()}>Register control channel</Button>
      </div>
      {error !== null && <div className="mt-2"><ErrorState error={error} /></div>}
      {issued && (
        <div className="mt-3 rounded-md border border-warn/50 bg-warn/10 p-2 text-xs" data-testid="live-defense-hmac-once">
          <p className="font-medium text-warn">Copy this HMAC secret now. Ari will not show it again.</p>
          <Mono className="mt-1 block break-all text-ink">{issued.hmac_secret}</Mono>
          <p className="mt-1 text-muted">Adapter: http://127.0.0.1:{issued.port}{issued.path}</p>
        </div>
      )}
    </div>
  );
}
function PilotReportPanel({ projectId }: { projectId: string }) {
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState<'idle' | 'saving' | 'ok'>('idle');
  const download = async () => {
    setError(null);
    setStatus('saving');
    try {
      const report = await api().pilotReport(projectId);
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `live-defense-pilot-report-${projectId}.json`;
      link.click();
      URL.revokeObjectURL(url);
      setStatus('ok');
    } catch (err) {
      setError(err);
      setStatus('idle');
    }
  };
  return (
    <Panel title="Exportable pilot report">
      <p className="text-sm text-ink">
        Downloads recorded staging-app, ingest, shadow-decision, and review counts.
        HTTP 200 is not attack success. This report never claims that shadow mode prevented breaches.
      </p>
      <Button className="mt-3" variant="primary" disabled={status === 'saving'} onClick={() => void download()}>
        Download pilot report
      </Button>
      {status === 'ok' && <p className="mt-2 text-xs text-muted" data-testid="live-defense-pilot-report-saved">Report downloaded from recorded control-plane data.</p>}
      {error !== null && <div className="mt-2"><ErrorState error={error} /></div>}
    </Panel>
  );
}
