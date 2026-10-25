'use client';

import type { Incident, IncidentAction, IncidentCreate, IncidentState, TriageDecisionKind } from '@hackatl/frontend-transport';
import { useState, type FormEvent } from 'react';
import { ClosurePanel } from '@/components/incident-closure';
import { useProject } from '@/components/project';
import { useSession } from '@/components/session';
import { Async, Button, Empty, ErrorState, Field, inputClass, Mono, PageTitle, Panel, Tag, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { useActionKeys } from '@/lib/action-key';
import { CREATE_INCIDENT_NOTE } from '@/lib/create-incident';
import { action, approvalFor, GATE_FOR, LIFECYCLE, NEXT_STATES, STATE_LABEL } from '@/lib/incidents';
import { useResource } from '@/lib/use-resource';

/** NIST SP 800-61r3 / CSF 2.0 incident lifecycle. Every gated step needs a second person. */
export default function IncidentsPage() {
  const { project } = useProject();
  const list = useResource(`incidents:${project.id}`, s => api().incidents(project.id, { signal: s }), 15000);
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <>
      <PageTitle eyebrow="Defend" title="Incidents" subtitle="Opened from monitoring events, findings or drills — one thread per incident." />
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_3fr]">
      <div className="flex flex-col gap-4">
        <Panel title="Incidents">
          <Async resource={list} label="incidents">
            {page => page.items.length === 0 ? <Empty>No incidents. Open one from a monitoring event, finding or synthetic drill.</Empty> : (
              <ul className="flex flex-col gap-2">
                {page.items.map(i => (
                  <li key={i.id}>
                    <button type="button" onClick={() => setSelected(i.id)} aria-pressed={selected === i.id} data-testid={`incident-${i.id}`}
                      className={`w-full rounded-md border p-3 text-left ${selected === i.id ? 'border-brand bg-white' : 'border-line bg-panel-2 hover:bg-white'}`}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-medium">{i.title}</span>
                        <span className="flex gap-1">{i.simulated && <Tag tone="warn">Synthetic</Tag>}<Tag tone={i.state === 'closed' || i.state === 'rejected' ? 'neutral' : 'info'}>{STATE_LABEL[i.state]}</Tag></span>
                      </div>
                      <div className="mt-1 text-xs text-muted">CSF {i.csf_function} · {i.severity ?? 'unscored'} · priority {i.business_priority} · opened <Time iso={i.opened_at} /></div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Async>
        </Panel>
        <OpenIncident onCreated={id => { list.reload(); setSelected(id); }} />
      </div>
      {selected ? <IncidentDetailView key={selected} id={selected} onChange={list.reload} /> : <Panel title="Incident"><Empty>Select an incident to see its lifecycle and timeline.</Empty></Panel>}
    </div>
    </>
  );
}

function OpenIncident({ onCreated }: { onCreated: (id: string) => void }) {
  const { project } = useProject();
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [severity, setSeverity] = useState<IncidentCreate['severity']>(null);
  const [simulated, setSimulated] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const createKeys = useActionKeys('create-incident');
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { title: title.trim(), note: note.trim(), severity, simulated, monitoring_event_ids: [], finding_ids: [], recommendation_ids: [] };
      const created = await api().createIncident(project.id, body, { idempotencyKey: createKeys.keyFor(JSON.stringify(body)) });
      createKeys.done();
      setTitle(''); setNote('');
      onCreated(created.id);
    } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <Panel title="Open incident">
      <p className="mb-3 text-sm text-muted" data-testid="create-incident-note">{CREATE_INCIDENT_NOTE}</p>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field label="Title"><input className={inputClass} value={title} onChange={e => setTitle(e.target.value)} maxLength={200} /></Field>
        <Field label="Initial report"><textarea className={`${inputClass} min-h-16`} value={note} onChange={e => setNote(e.target.value)} maxLength={500} /></Field>
        <Field label="Severity">
          <select className={inputClass} value={severity ?? ''} onChange={e => setSeverity((e.target.value || null) as IncidentCreate['severity'])}>
            <option value="">Unscored</option>
            {(['info', 'low', 'medium', 'high', 'critical'] as const).map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={simulated} onChange={e => setSimulated(e.target.checked)} />
          Synthetic drill (labelled as synthetic everywhere it appears)
        </label>
        <Button type="submit" variant="primary" disabled={busy || title.trim().length === 0}>Open incident</Button>
        {error !== null && <ErrorState error={error} context="Incident not opened" />}
      </form>
    </Panel>
  );
}

function IncidentDetailView({ id, onChange }: { id: string; onChange: () => void }) {
  const detail = useResource(`incident:${id}`, s => api().incident(id, { signal: s }), 15000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const actionKeys = useActionKeys('incident-action');
  const act = async (body: IncidentAction): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try { await api().actOnIncident(id, body, { idempotencyKey: actionKeys.keyFor(`${id}|${JSON.stringify(body)}`) }); actionKeys.done(); detail.reload(); onChange(); return true; } catch (err) { setError(err); return false; } finally { setBusy(false); }
  };
  return (
    <Async resource={detail} label="incident">
      {({ incident }) => (
        <div className="flex flex-col gap-4">
          <Panel title={incident.title} actions={incident.simulated ? <Tag tone="warn">Synthetic drill</Tag> : undefined}>
            <Lifecycle state={incident.state} />
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted">
              <dt>CSF function</dt><dd>{incident.csf_function}</dd>
              <dt>Owner</dt><dd>{incident.owner ? <Mono>{incident.owner}</Mono> : 'unassigned'}</dd>
              <dt>Severity</dt><dd>{incident.severity ?? 'unscored'} · priority {incident.business_priority} · escalation {incident.escalation_level}</dd>
              {incident.triage && <><dt>Triage</dt><dd>{incident.triage.decision.replaceAll('_', ' ')} · {incident.triage.category} — {incident.triage.rationale}</dd></>}
              <dt>Linked</dt><dd>{incident.finding_ids.length} findings · {incident.monitoring_event_ids.length} events · {incident.verification_ids.length} verifications · {incident.recommendation_ids.length} recommendations</dd>
              <dt>Closure</dt><dd>{incident.closure_criteria.length === 0 ? 'no criteria' : incident.closure_criteria.map(c => `${c.satisfied ? '✓' : '○'} ${c.key}`).join(' · ')}</dd>
            </dl>
          </Panel>
          <Actions incident={incident} busy={busy} act={act} />
          {error !== null && <ErrorState error={error} context="Action refused" />}
          <ClosurePanel incident={incident} busy={busy} act={act} />
          <Panel title="Timeline">
            <ol className="flex flex-col gap-2 text-sm">
              {[...incident.timeline].reverse().map((t, i) => (
                <li key={i} className="border-l-2 border-line pl-3">
                  <div className="text-xs text-muted"><Time iso={t.at} /> · {t.actor} · {t.csf_refs.join(', ')}</div>
                  <div>{t.action.replaceAll('_', ' ')}{t.from_state && t.to_state && <> · {t.from_state} → {t.to_state}</>}{t.note && <> — {t.note}</>}</div>
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      )}
    </Async>
  );
}

function Lifecycle({ state }: { state: IncidentState }) {
  const at = LIFECYCLE.indexOf(state);
  return (
    <ol className="flex flex-wrap items-center gap-1 text-xs" aria-label="Lifecycle">
      {state === 'rejected' ? <li><Tag tone="neutral">Rejected</Tag></li> : LIFECYCLE.map((s, i) => (
        <li key={s} aria-current={s === state ? 'step' : undefined}>
          <Tag tone={i < at ? 'ok' : i === at ? 'accent' : 'neutral'}>{i < at ? '✓ ' : ''}{STATE_LABEL[s]}</Tag>
        </li>
      ))}
    </ol>
  );
}

function Actions({ incident, busy, act: submitAction }: { incident: Incident; busy: boolean; act: (a: IncidentAction) => Promise<boolean> }) {
  const { me } = useSession();
  const [note, setNote] = useState('');
  /**
   * The note is the rationale for one action; clear it once that action succeeded so it is
   * not reused as the next summary. Only clear it if it is still the text that was submitted:
   * anything typed while the request was in flight belongs to the next action.
   */
  const act = async (a: IncidentAction): Promise<boolean> => {
    const submitted = note;
    const ok = await submitAction(a);
    if (ok) setNote(current => (current === submitted ? '' : current));
    return ok;
  };
  const [category, setCategory] = useState('');
  const [decision, setDecision] = useState<TriageDecisionKind>('confirmed_incident');
  const terminal = incident.state === 'closed' || incident.state === 'rejected';
  if (terminal) return null;
  const pendingMine = incident.approvals.filter(a => a.status === 'pending');
  return (
    <Panel title="Next step">
      <Field label="Note / rationale" hint="Recorded in the timeline with your identity.">
        <input className={inputClass} value={note} onChange={e => setNote(e.target.value)} maxLength={500} />
      </Field>
      {incident.state === 'reported' && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <Field label="Triage decision">
            <select className={inputClass} value={decision} onChange={e => setDecision(e.target.value as TriageDecisionKind)}>
              <option value="confirmed_incident">Confirmed incident</option>
              <option value="not_an_incident">Not an incident</option>
              <option value="duplicate">Duplicate</option>
            </select>
          </Field>
          <Field label="Category"><input className={inputClass} value={category} onChange={e => setCategory(e.target.value)} placeholder="e.g. broken access control" /></Field>
          <Button variant="primary" disabled={busy || !note.trim() || !category.trim()} onClick={() => act(action('triage', { triage_decision: decision, category: category.trim(), note: note.trim() }))}>Record triage</Button>
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {!incident.owner && <Button disabled={busy} onClick={() => act(action('assign_owner', { owner: me.user.id, note: note.trim() }))}>Assign to me</Button>}
        {NEXT_STATES[incident.state].map(target => {
          const gate = GATE_FOR[target];
          const approval = gate ? approvalFor(incident, gate) : undefined;
          if (gate && approval?.status !== 'approved') {
            return approval?.status === 'pending' ? null : (
              <Button key={target} disabled={busy || !note.trim()} title="A different project member must approve"
                onClick={() => act(action('request_approval', { gate, action_summary: note.trim(), note: note.trim() }))}>
                Request {gate} approval
              </Button>
            );
          }
          return (
            <Button key={target} variant={target === 'rejected' ? 'danger' : 'primary'} disabled={busy || (target === 'rejected' && !note.trim())}
              onClick={() => act(action('transition', { target_state: target, note: note.trim() }))}>
              Move to {STATE_LABEL[target]}
            </Button>
          );
        })}
      </div>
      {pendingMine.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2">
          {pendingMine.map(a => {
            const own = a.requested_by === me.user.id;
            return (
              <li key={a.gate} className="rounded-md border border-line bg-panel-2 p-2 text-sm">
                <div><strong>{a.gate}</strong> approval pending — {a.action_summary}</div>
                <div className="text-xs text-muted">requested by <Mono>{a.requested_by}</Mono>{own && ' (you — another member must decide)'} · <Time iso={a.requested_at} /></div>
                <div className="mt-2 flex gap-2">
                  <Button variant="primary" disabled={busy || own} onClick={() => act(action('decide_approval', { gate: a.gate, approve: true, note: note.trim() }))}>Approve</Button>
                  <Button variant="danger" disabled={busy || own} onClick={() => act(action('decide_approval', { gate: a.gate, approve: false, note: note.trim() }))}>Decline</Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">
        Eradication additionally requires a linked, independently verified remediation; closure requires satisfied closure criteria and lessons learned with evidence. The backend enforces each rule and explains any refusal.
      </p>
    </Panel>
  );
}
