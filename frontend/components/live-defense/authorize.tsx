'use client';

import { ApiError, type ConnectedApplication, type ControlAuthorizationCreate, type LiveControl, type LiveControlKind } from '@hackatl/frontend-transport';
import { useState } from 'react';
import { Button, ErrorState, Field, inputClass, Mono, Tag } from '@/components/ui';
import { api } from '@/lib/api';
import { controlKindLabel, ISOLATED_FIXTURE_SOURCE, OWNED_TEST_LABEL } from '@/lib/live-defense';

const KINDS: LiveControlKind[] = ['rate_limit', 'block_source', 'increase_logging', 'escalate'];

function defaultTarget(kind: LiveControlKind, appId: string): ControlAuthorizationCreate['target'] {
  switch (kind) {
    case 'rate_limit':
      return { type: 'route', value: 'GET /payments/pay_fixture' };
    case 'block_source':
      return { type: 'source_ip', value: ISOLATED_FIXTURE_SOURCE };
    case 'increase_logging':
      return { type: 'application', value: appId };
    case 'escalate':
      return { type: 'none', value: '' };
  }
}

export function AuthorizeControlForm({
  apps,
  onSaved,
}: {
  apps: ConnectedApplication[];
  onSaved?: () => void;
}) {
  const owned = apps.filter(app => (app.environment === 'owned_test' || app.environment === 'sandbox') && app.rl_gated_pilot !== true);
  const [appId, setAppId] = useState('');
  const selectedAppId = appId || owned[0]?.id || '';
  const [kind, setKind] = useState<LiveControlKind>('rate_limit');
  const [targetValue, setTargetValue] = useState(defaultTarget('rate_limit', appId).value);
  const [duration, setDuration] = useState('60');
  const [maxRequests, setMaxRequests] = useState('1');
  const [rationale, setRationale] = useState('');
  const [decisionId, setDecisionId] = useState('');
  const [status, setStatus] = useState<'idle' | 'saving' | 'error' | 'ok'>('idle');
  const [error, setError] = useState<unknown>(null);
  const [created, setCreated] = useState<LiveControl | null>(null);
  const [denial, setDenial] = useState<string | null>(null);

  const changeKind = (next: LiveControlKind) => {
    setKind(next);
    setTargetValue(defaultTarget(next, appId).value);
  };

  const submit = async () => {
    setStatus('saving');
    setError(null);
    setDenial(null);
    setCreated(null);
    const target = defaultTarget(kind, selectedAppId);
    if (kind === 'block_source') {
      target.value = ISOLATED_FIXTURE_SOURCE;
    } else {
      target.value = kind === 'escalate' ? '' : targetValue.trim();
    }
    const body: ControlAuthorizationCreate = {
      kind,
      target,
      duration_s: Number(duration),
      rationale: rationale.trim() ? rationale.trim() : null,
      live_policy_decision_id: decisionId.trim() ? decisionId.trim() : null,
      max_requests: kind === 'rate_limit' ? Number(maxRequests) : null,
    };
    try {
      const control = await api().authorizeControl(selectedAppId, body);
      setCreated(control);
      setStatus('ok');
      onSaved?.();
    } catch (err) {
      setError(err);
      setStatus('error');
      if (err instanceof ApiError) {
        const details = err.error.details;
        const reason = typeof details.control_id === 'string'
          ? `${err.error.message} (control ${details.control_id}, status ${String(details.status ?? 'denied')})`
          : err.error.message;
        setDenial(reason);
      }
    }
  };

  if (owned.length === 0) {
    return <p className="text-sm text-muted">Register an owned-test or sandbox app before authorizing a control.</p>;
  }

  return (
    <div data-testid="live-defense-authorize" className="flex flex-col gap-3">
      <p className="text-sm text-ink">
        This form POSTs <Mono>/applications/&#123;id&#125;/control-authorizations</Mono> only.
        It is not analyst accept and not a browser-callable apply, isolate, rate-limit, or block.
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Field label="Owned-test application">
          <select className={inputClass} value={selectedAppId} onChange={e => setAppId(e.target.value)}>
            {owned.map(app => (
              <option key={app.id} value={app.id}>{app.name} ({app.environment.replaceAll('_', ' ')})</option>
            ))}
          </select>
        </Field>
        <Field label="Control kind">
          <select className={inputClass} value={kind} onChange={e => changeKind(e.target.value as LiveControlKind)}>
            {KINDS.map(item => <option key={item} value={item}>{controlKindLabel(item)}</option>)}
          </select>
        </Field>
        {kind !== 'escalate' && (
          <Field label={kind === 'block_source' ? 'Exact source IP' : kind === 'rate_limit' ? 'Exact route' : 'Application id'}>
            <input
              className={inputClass}
              value={kind === 'block_source' ? ISOLATED_FIXTURE_SOURCE : targetValue}
              onChange={e => setTargetValue(e.target.value)}
              maxLength={500}
              readOnly={kind === 'block_source'}
            />
          </Field>
        )}
        <Field label="Duration (seconds)">
          <input className={inputClass} type="number" min={5} max={300} value={duration} onChange={e => setDuration(e.target.value)} />
        </Field>
        {kind === 'rate_limit' && (
          <Field label="Max requests per window">
            <input className={inputClass} type="number" min={1} value={maxRequests} onChange={e => setMaxRequests(e.target.value)} />
          </Field>
        )}
        <Field label="Optional shadow decision id">
          <input className={inputClass} value={decisionId} onChange={e => setDecisionId(e.target.value)} placeholder="lpd_…" maxLength={64} />
        </Field>
        <Field label="Rationale (optional)">
          <input className={inputClass} value={rationale} onChange={e => setRationale(e.target.value)} maxLength={500} />
        </Field>
      </div>
      {kind === 'block_source' && (
        <p className="text-xs text-muted">
          Only the owned-test fixture IP {ISOLATED_FIXTURE_SOURCE} is allowed.
          Public, private, loopback, link-local, CIDR, ranges, malformed, and
          protected addresses are rejected. Authz and the adapter share this allowlist.
        </p>
      )}
      <Button
        variant="primary"
        disabled={status === 'saving' || selectedAppId.length === 0}
        onClick={() => void submit()}
      >
        Authorize owned-test control
      </Button>
      {status === 'error' && error !== null && (
        <div data-testid="live-defense-authorize-denied">
          {denial && <p className="mb-2 text-sm text-warn">{denial}</p>}
          <ErrorState error={error} onRetry={() => void submit()} context="Authorize control" />
        </div>
      )}
      {created && (
        <div className="rounded-md border border-line bg-panel-2 p-3 text-sm text-ink" data-testid={`live-defense-authorized-${created.id}`}>
          <p className="font-medium">
            {controlKindLabel(created.kind)} · <Tag tone="ok">{created.environment_label}</Tag>
          </p>
          <p className="mt-1 text-xs text-muted">
            status {created.status} · origin {created.origin} · TTL {created.duration_s}s
          </p>
          <p className="mt-1 text-xs">{created.caveat}</p>
          {created.status === 'authorized' && (
            <p className="mt-1 text-xs text-muted">Authorized is not executing. The worker applies the adapter after this gate.</p>
          )}
        </div>
      )}
      <p className="text-xs text-muted">{OWNED_TEST_LABEL} only. A recommendation cannot jump to EXECUTING.</p>
    </div>
  );
}
