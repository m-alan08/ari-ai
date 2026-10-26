'use client';

import type {
  AgentCapabilityRegistry,
  AnalystHandoff,
  ConnectedApplication,
  FirstResponderDecision,
  FirstResponderIntent,
  FirstResponderInvocation,
  FirstResponderModeView,
} from '@hackatl/frontend-transport';
import { useState } from 'react';
import { SourceTag } from '@/components/live-defense/common';
import { Async, Button, Empty, ErrorState, Field, inputClass, Mono, Panel, Tag } from '@/components/ui';
import { api } from '@/lib/api';
import { EVIDENCE_STATES, FIRST_RESPONDER_COPY, type LiveDefenseMode } from '@/lib/live-defense';
import type { Resource } from '@/lib/use-resource';

const INTENTS: { value: FirstResponderIntent; label: string }[] = [
  { value: 'off', label: 'OFF' },
  { value: 'monitor', label: 'MONITOR / SHADOW' },
  { value: 'assisted_response', label: 'ASSISTED RESPONSE' },
];

export function FirstResponder({
  mode,
  projectId,
  apps,
  frMode,
  decisions,
  invocations,
  handoffs,
  capabilities,
  onChange,
}: {
  mode: LiveDefenseMode;
  projectId: string | null;
  apps?: Resource<{ items: ConnectedApplication[] }>;
  frMode?: Resource<FirstResponderModeView>;
  decisions?: Resource<{ items: FirstResponderDecision[]; label: string }>;
  invocations?: Resource<{ items: FirstResponderInvocation[]; next_cursor: string | null }>;
  handoffs?: Resource<{ items: AnalystHandoff[] }>;
  capabilities?: Resource<AgentCapabilityRegistry>;
  onChange?: () => void;
}) {
  if (mode === 'layout-preview') {
    return (
      <Panel title="Sample first-responder chrome" actions={<SourceTag mode="layout-preview" />}>
        <p className="text-sm text-ink">Sample layout only. No mode is changed and no agent is invoked here.</p>
      </Panel>
    );
  }
  if (!projectId) return <Empty>Choose a project to configure first-responder mode.</Empty>;
  return (
    <div className="flex min-w-0 flex-col gap-6" data-testid="live-defense-first-responder">
      <p data-testid="live-defense-first-responder-copy" className="rounded-xl border border-line bg-panel-2 px-4 py-3 text-sm text-ink">
        {FIRST_RESPONDER_COPY}
      </p>
      <p data-testid="live-defense-evidence-states" className="text-sm text-ink">
        Case states: {EVIDENCE_STATES.join(' / ')}. Recommended is not Executed. An RL recommendation never becomes Executed defense.
      </p>
      <ModePanel projectId={projectId} frMode={frMode} onChange={onChange} />
      <CapabilitiesPanel capabilities={capabilities} />
      <DecisionsPanel decisions={decisions} />
      <InvocationsPanel invocations={invocations} />
      <HandoffsPanel handoffs={handoffs} />
      <MappingPanel apps={apps} onChange={onChange} />
    </div>
  );
}

function ModePanel({
  projectId,
  frMode,
  onChange,
}: {
  projectId: string;
  frMode?: Resource<FirstResponderModeView>;
  onChange?: () => void;
}) {
  const [intent, setIntent] = useState<FirstResponderIntent>('off');
  const [status, setStatus] = useState<'idle' | 'saving' | 'error'>('idle');
  const [error, setError] = useState<unknown>(null);
  const submit = async () => {
    setStatus('saving');
    setError(null);
    try {
      await api().setFirstResponderMode(projectId, { intent, note: null });
      setStatus('idle');
      onChange?.();
    } catch (err) {
      setError(err);
      setStatus('error');
    }
  };
  return (
    <Panel title="Server-enforced mode" actions={<SourceTag mode="live" />}>
      {frMode ? (
        <Async resource={frMode} label="first-responder mode">
          {view => (
            <p className="mb-3 text-sm text-ink" data-testid="live-defense-fr-intent">
              Intent <strong>{view.intent}</strong>
              {view.stop_kind !== 'none' ? ` · ${view.stop_kind.replaceAll('_', ' ')}` : ''}.
              AUTO-EXECUTE is not offered. Org intent is not app effective permissions.
            </p>
          )}
        </Async>
      ) : null}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <Field label="Mode">
          <select className={inputClass} value={intent} onChange={e => setIntent(e.target.value as FirstResponderIntent)} data-testid="live-defense-fr-mode-select">
            {INTENTS.map(item => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </Field>
        <Button variant="primary" disabled={status === 'saving'} onClick={() => void submit()}>Set mode</Button>
      </div>
      {status === 'error' && error !== null && <div className="mt-2"><ErrorState error={error} onRetry={() => void submit()} context="Set first-responder mode" /></div>}
    </Panel>
  );
}

function CapabilitiesPanel({ capabilities }: { capabilities?: Resource<AgentCapabilityRegistry> }) {
  return (
    <Panel title="Seven-agent capability registry">
      {capabilities ? (
        <Async resource={capabilities} label="agent capabilities">
          {reg => (
            <ul className="flex flex-col gap-2" data-testid="live-defense-fr-capabilities">
              {reg.agents.map(agent => (
                <li key={agent.name} className="rounded-lg border border-line px-3 py-2 text-sm">
                  <span className="font-medium">{agent.name}</span>
                  <Tag>{agent.capability_class.replaceAll('_', ' ')}</Tag>
                  <span className="ml-2 text-muted">{agent.notes}</span>
                </li>
              ))}
            </ul>
          )}
        </Async>
      ) : <Empty>Capabilities were not requested.</Empty>}
    </Panel>
  );
}

function DecisionsPanel({ decisions }: { decisions?: Resource<{ items: FirstResponderDecision[]; label: string }> }) {
  return (
    <Panel title="Deterministic decisions">
      {decisions ? (
        <Async resource={decisions} label="first-responder decisions">
          {page => page.items.length === 0 ? (
            <Empty>No first-responder decision yet. OFF does not start jobs.</Empty>
          ) : (
            <ul className="flex flex-col gap-3">
              {page.items.map(item => (
                <li key={item.id} className="rounded-lg border border-line px-3 py-2 text-sm" data-testid={`live-defense-fr-decision-${item.id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <Mono>{item.id}</Mono>
                    <Tag>{item.evidence_state}</Tag>
                    <Tag>{item.action.replaceAll('_', ' ')}</Tag>
                    <Tag tone={item.executed ? 'bad' : 'info'}>{item.executed ? 'control executed' : 'control not executed'}</Tag>
                  </div>
                  <p className="mt-1 text-ink">Correlation {item.correlation.confidence}. Confirmation {item.confirmation}. Predicted probability is not set. Agent execution is listed separately and is not a confirmed threat.</p>
                  <p className="mt-1 text-muted">{item.caveat}</p>
                </li>
              ))}
            </ul>
          )}
        </Async>
      ) : <Empty>Decisions were not requested.</Empty>}
    </Panel>
  );
}

function InvocationsPanel({
  invocations,
}: {
  invocations?: Resource<{ items: FirstResponderInvocation[]; next_cursor: string | null }>;
}) {
  return (
    <Panel title="Agent invocations">
      {invocations ? (
        <Async resource={invocations} label="first-responder invocations">
          {page => page.items.length === 0 ? (
            <Empty>No agent invocation yet. OFF does not start jobs.</Empty>
          ) : (
            <ul className="flex flex-col gap-3" data-testid="live-defense-fr-invocations">
              {page.items.map(item => (
                <li key={item.id} className="rounded-lg border border-line px-3 py-2 text-sm" data-testid={`live-defense-fr-invocation-${item.agent}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{item.agent}</span>
                    <Tag>{item.status.replaceAll('_', ' ')}</Tag>
                    <Tag>{item.provider_mode}</Tag>
                  </div>
                  <p className="mt-1 text-ink">{item.reason}</p>
                  {item.execution ? (
                    <p className="mt-1 text-muted" data-testid={`live-defense-fr-execution-${item.agent}`}>
                      Provider {item.execution.provider_mode}
                      {item.execution.latency_ms != null ? ` · ${item.execution.latency_ms} ms` : ''}
                      {item.execution.cost_usd != null ? ` · $${item.execution.cost_usd.toFixed(4)}` : ''}
                      {item.execution.tool_call_count != null ? ` · ${item.execution.tool_call_count} tools` : ''}
                      {' · integrity '}{item.execution.execution_integrity}
                      {' · confirmed '}{item.execution.confirmation}
                      {' · predicted probability is not set'}
                    </p>
                  ) : (
                    <p className="mt-1 text-muted">No agent loop ran. Eligibility or skip is not verification.</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Async>
      ) : <Empty>Invocations were not requested.</Empty>}
    </Panel>
  );
}

function HandoffsPanel({
  handoffs,
}: {
  handoffs?: Resource<{ items: AnalystHandoff[] }>;
}) {
  return (
    <Panel title="Analyst handoff">
      {handoffs ? (
        <Async resource={handoffs} label="analyst handoffs">
          {page => page.items.length === 0 ? (
            <Empty>No handoff. An alert is not delivered until the provider confirms receipt.</Empty>
          ) : (
            <ul className="flex flex-col gap-3">
              {page.items.map(item => (
                <li key={item.id} className="rounded-lg border border-line px-3 py-2 text-sm" data-testid={`live-defense-handoff-${item.id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{item.title}</span>
                    <Tag>{item.state.replaceAll('_', ' ')}</Tag>
                    <Tag>{item.confirmation}</Tag>
                  </div>
                  <p className="mt-1 text-ink">{item.summary}</p>
                  <p className="mt-1 text-muted">{item.caveat}</p>
                  {item.state === 'delivery_pending' ? (
                    <p className="mt-1 text-muted" data-testid="live-defense-handoff-pending">
                      Delivery pending. Waiting for a genuine provider acknowledgement. This UI does not invent a receipt.
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Async>
      ) : <Empty>Handoffs were not requested.</Empty>}
    </Panel>
  );
}

function MappingPanel({
  apps,
  onChange,
}: {
  apps?: Resource<{ items: ConnectedApplication[] }>;
  onChange?: () => void;
}) {
  const [appId, setAppId] = useState('');
  const [repoId, setRepoId] = useState('');
  const [commit, setCommit] = useState('');
  const [status, setStatus] = useState<'idle' | 'saving' | 'error'>('idle');
  const [error, setError] = useState<unknown>(null);
  const submit = async () => {
    setStatus('saving');
    setError(null);
    try {
      await api().createDeployCommitMapping(appId, {
        repo_id: repoId.trim(),
        commit_sha: commit.trim(),
        deployed_at: new Date().toISOString(),
        source: 'owner_attested',
      });
      setStatus('idle');
      onChange?.();
    } catch (err) {
      setError(err);
      setStatus('error');
    }
  };
  return (
    <Panel title="Owner-attested deploy-to-commit mapping">
      <p className="mb-3 text-sm text-muted">Without this mapping, runtime↔code correlation is UNKNOWN. A vulnerability at a commit is not proof of this request.</p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <Field label="Application">
          <select className={inputClass} value={appId} onChange={e => setAppId(e.target.value)}>
            <option value="">Select app</option>
            {apps?.status === 'ready' ? apps.data.items.map(app => (
              <option key={app.id} value={app.id}>{app.name}</option>
            )) : null}
          </select>
        </Field>
        <Field label="Repository id">
          <input className={inputClass} value={repoId} onChange={e => setRepoId(e.target.value)} placeholder="rep_…" />
        </Field>
        <Field label="Commit SHA">
          <input className={inputClass} value={commit} onChange={e => setCommit(e.target.value)} minLength={40} maxLength={40} />
        </Field>
        <Button variant="primary" disabled={status === 'saving' || !appId || repoId.trim().length === 0 || commit.trim().length !== 40} onClick={() => void submit()}>Attest mapping</Button>
      </div>
      {status === 'error' && error !== null && <div className="mt-2"><ErrorState error={error} onRetry={() => void submit()} context="Attest mapping" /></div>}
    </Panel>
  );
}
