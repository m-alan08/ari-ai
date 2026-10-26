'use client';

import { useEffect, useState } from 'react';
import type { Project } from '@hackatl/frontend-transport';
import { DefenseSwitch } from '@/components/defense-switch';
import { ReplayStage } from '@/components/lab-replay';
import { OptionalSession, useOptionalSession } from '@/components/session';
import { Empty, ErrorState, Field, inputClass, Loading, Mono, Panel } from '@/components/ui';
import { defenderCountPhrase } from '@/components/lab-copy';
import { api } from '@/lib/api';
import { DEMO_ONLY } from '@/lib/deployment';
import { sampleSize } from '@/lib/lab';
import { keyTakeaway, POLICY_NAME, policyName, scenarioName, SCORE_METRICS, scoreboard, type ScoreMetric, tradeoffNote } from '@/lib/lab-plain';
import type { BundleResponse, DefenseLabBundle, LabEpisode } from '@/lib/lab-types';

type Load<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error'; error: unknown };

async function getJson<T>(url: string, signal: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal, cache: 'no-store' });
  const body = await res.json() as T & { error?: string };
  if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
  return body;
}

export default function DefenseLabPage() {
  const [protocols, setProtocols] = useState<Load<string[]>>({ status: 'loading' });
  const [protocol, setProtocol] = useState('');
  const [loaded, setLoaded] = useState<{ protocol: string; result: Load<BundleResponse> } | null>(null);
  const bundle: Load<BundleResponse> = loaded && loaded.protocol === protocol ? loaded.result : { status: 'loading' };

  useEffect(() => {
    const c = new AbortController();
    getJson<{ protocols: string[] }>('/lab/bundles', c.signal).then(
      d => { setProtocols({ status: 'ready', data: d.protocols }); setProtocol(p => p || d.protocols[0] || ''); },
      (error: unknown) => { if (!c.signal.aborted) setProtocols({ status: 'error', error }); },
    );
    return () => c.abort();
  }, []);

  useEffect(() => {
    if (!protocol) return;
    const c = new AbortController();
    getJson<BundleResponse>(`/lab/bundles/${encodeURIComponent(protocol)}`, c.signal).then(
      data => setLoaded({ protocol, result: { status: 'ready', data } }),
      (error: unknown) => { if (!c.signal.aborted) setLoaded({ protocol, result: { status: 'error', error } }); },
    );
    return () => c.abort();
  }, [protocol]);

  return (
    <>
      <header className="hero-ink mb-6 rounded-2xl px-6 py-8 md:px-10 md:py-10">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-2xl">
            <p className="label-mono text-white/60">Defense Lab · research</p>
            <h1 className="display mt-3 text-3xl font-semibold md:text-5xl">Can an AI learn to defend a network?</h1>
            <p className="mt-4 max-w-xl text-sm text-white/75 md:text-base" data-testid="lab-intro">
              A controlled network evaluation. We trained an AI defender in a practice network and scored it against simpler defenders on runs it never saw in training.
            </p>
            <p role="note" className="lab-caveat lab-caveat-dark mt-4" data-testid="lab-caveat">
              <svg viewBox="0 0 16 16" className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><path d="M8 1.8 14.6 13.5H1.4z" strokeLinejoin="round" /><path d="M8 6.3v3.4M8 11.7v.01" /></svg>
              <span><strong>Controlled test environment.</strong> Results come from an isolated test network, not a connected production system. No action is ever applied automatically.</span>
            </p>
          </div>
          {protocols.status === 'ready' && protocols.data.length > 0 && (
            <div className="lab-run-picker">
              <Field label="Evaluation run">
                <select className={inputClass} value={protocol} onChange={e => setProtocol(e.target.value)} name="protocol">
                  {protocols.data.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </Field>
            </div>
          )}
        </div>
      </header>
      {!DEMO_ONLY && <OptionalSession><SignedInDefenseProjects /></OptionalSession>}
      {protocols.status === 'loading' && <Loading label="Finding evaluation runs" />}
      {protocols.status === 'error' && <ErrorState error={protocols.error} context="Could not list evaluation runs" />}
      {protocols.status === 'ready' && protocols.data.length === 0 && <Empty>No committed evaluation bundle (rl-defense/results/protocol-*/defense_lab.json) was found.</Empty>}
      {protocol && bundle.status === 'loading' && <Loading label={`Loading ${protocol}`} />}
      {bundle.status === 'error' && <ErrorState error={bundle.error} context="Evaluation bundle rejected" />}
      {bundle.status === 'ready' && <Lab data={bundle.data} />}
    </>
  );
}

function SignedInDefenseProjects() {
  const session = useOptionalSession();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const userId = session?.me.user.id ?? null;

  useEffect(() => {
    if (!userId) return;
    const controller = new AbortController();
    api().projects({ signal: controller.signal }).then(
      page => { if (!controller.signal.aborted) setProjects(page.items); },
      () => { if (!controller.signal.aborted) setProjects(null); },
    );
    return () => controller.abort();
  }, [userId]);

  if (!userId || projects === null) return null;

  return (
    <Panel title="Your 24/7 defense" className="mb-6">
      {projects.length === 0 ? (
        <p className="text-sm text-muted">No projects yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {projects.map(project => (
            <li key={project.id} className="flex flex-wrap items-start justify-between gap-4 py-3 first:pt-0 last:pb-0">
              <p className="pt-2 text-sm font-medium">{project.name}</p>
              <DefenseSwitch
                project={project}
                onChange={next => setProjects(current => current?.map(item => item.id === next.id ? next : item) ?? null)}
              />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Lab({ data }: { data: BundleResponse }) {
  const { bundle, provenance } = data;
  const n = sampleSize(bundle, null);
  return (
    <div className="flex flex-col gap-16">
      <ul className="lab-chips" aria-label="Evaluation at a glance">
        <li className="lab-chip"><strong>{n.toLocaleString()}</strong> held-out runs</li>
        <li className="lab-chip"><strong>{bundle.metrics.length}</strong> defenders compared</li>
        <li className="lab-chip"><strong>{bundle.scenarios.length}</strong> attacker scenarios</li>
        <li className="lab-chip"><strong>{bundle.training_seeds.length}</strong> training seeds</li>
        {!bundle.simulation_only && <li className="lab-chip lab-chip-warn">simulation flag missing</li>}
      </ul>
      <Scoreboard bundle={bundle} />
      <Replay bundle={bundle} />
      <details className="group rounded-2xl border border-line bg-panel" data-testid="lab-details">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-4 text-sm font-semibold">
          <span aria-hidden="true" className="inline-block transition-transform group-open:rotate-90">▸</span>
          Where these numbers come from
        </summary>
        <div className="border-t border-line p-5">
          <Panel title="Provenance">
            <dl className="grid gap-x-4 gap-y-1 text-xs text-muted sm:grid-cols-[auto_1fr]">
              <dt>Source</dt><dd><Mono>{provenance.path}</Mono> — committed evaluation artifact, validated against <Mono>{provenance.schemaPath}</Mono>. Not yet served by the backend API.</dd>
              <dt>Environment</dt><dd><Mono>{bundle.env_id}</Mono> · schema {bundle.schema_version}</dd>
              <dt>Run</dt><dd><Mono>{bundle.source_run}</Mono> at commit <Mono>{bundle.git_sha.slice(0, 12)}</Mono></dd>
              <dt>Training seeds</dt><dd>{bundle.training_seeds.join(', ')} · {bundle.episodes_per_config_per_seed} episodes per held-out config per seed</dd>
            </dl>
          </Panel>
        </div>
      </details>
    </div>
  );
}

const HEADLINE: readonly { key: string; label: string; vs: string }[] = [
  { key: 'compromise_rate', label: 'of attacks reached their goal', vs: 'noop' },
  { key: 'service_uptime', label: 'of the time services stayed up', vs: 'threshold_rules' },
  { key: 'time_to_containment', label: 'steps on average to contain', vs: 'threshold_rules' },
];
const HEADLINE_KEYS = new Set(HEADLINE.map(h => h.key));

const fmtScore = (metric: ScoreMetric, x: number | null) => (x === null ? '—' : metric.percent ? `${x.toFixed(1)}%` : x.toFixed(1));
const fmtRange = (metric: ScoreMetric, lo: number | null, hi: number | null) =>
  lo === null || hi === null ? '—' : `${lo.toFixed(1)}–${hi.toFixed(1)}${metric.percent ? '%' : ''}`;

function Headline({ bundle }: { bundle: DefenseLabBundle }) {
  const tiles = HEADLINE.flatMap(h => {
    const metric = SCORE_METRICS.find(m => m.key === h.key);
    if (!metric) return [];
    const cells = scoreboard(bundle, metric);
    const ai = cells.find(c => c.policy === 'ddqn');
    if (!ai || ai.value === null) return [];
    const vs = cells.find(c => c.policy === h.vs);
    return [{ metric, label: h.label, ai, vs }];
  });
  if (tiles.length === 0) return null;
  return (
    <dl className="lab-headline mt-6" data-testid="lab-takeaway" aria-label={keyTakeaway(bundle) ?? undefined}>
      {tiles.map(({ metric, label, ai, vs }) => (
        <div key={metric.key} className="lab-headline-tile">
          <dt className="sr-only">Trained AI defender: {metric.title}</dt>
          <dd>
            <span className="lab-headline-num">{fmtScore(metric, ai.value)}</span>
            <span className="mt-1 block text-sm text-ink">{label}</span>
            {vs && vs.value !== null && (
              <span className="mt-2 block text-xs text-muted">vs {fmtScore(metric, vs.value)} for {policyName(vs.policy).toLowerCase()}</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Direction({ higher }: { higher: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-faint">
      <svg viewBox="0 0 12 12" className="h-3 w-3" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
        {higher ? <path d="M6 10V2M2.5 5.5 6 2l3.5 3.5" /> : <path d="M6 2v8M2.5 6.5 6 10l3.5-3.5" />}
      </svg>
      {higher ? 'Higher is better' : 'Lower is better'}
    </span>
  );
}

function Scoreboard({ bundle }: { bundle: DefenseLabBundle }) {
  const n = sampleSize(bundle, null);
  const tradeoff = tradeoffNote(bundle);
  const metrics = SCORE_METRICS.filter(m => HEADLINE_KEYS.has(m.key))
    .map(metric => ({ metric, cells: scoreboard(bundle, metric) }))
    .filter(m => !m.cells.every(c => c.value === null));
  return (
    <section aria-labelledby="score-title" data-testid="lab-scoreboard" className="py-14 md:py-20">
      <p className="lab-eyebrow">01 — The numbers</p>
      <h2 id="score-title" className="mt-2 text-2xl font-semibold tracking-tight md:text-3xl">Who defends best?</h2>
      <p className="mt-2 max-w-3xl text-sm text-muted">
        {defenderCountPhrase(bundle.metrics.length, true)}, each tested on {n.toLocaleString()} evaluation runs excluded from training.
      </p>
      <Headline bundle={bundle} />
      <div className="mt-8 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {metrics.map(({ metric, cells }) => {
          const top = Math.max(...cells.map(x => x.hi ?? x.value ?? 0), 1);
          const pct = (x: number | null) => (x === null ? 0 : Math.min(100, Math.max(0, metric.percent ? x : (x / top) * 100)));
          return (
            <figure key={metric.key} className="lab-score-card" data-testid={`score-${metric.key}`}>
              <figcaption className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="font-semibold tracking-tight text-ink">{metric.title}</span>
                <Direction higher={metric.higherIsBetter} />
              </figcaption>
              <ul className="mt-5 flex flex-col gap-4">
                {cells.map(c => {
                  const ai = c.policy === 'ddqn';
                  return (
                    <li key={c.policy} className="lab-score-row" data-ai={ai || undefined}>
                      <span className="lab-score-name" title={POLICY_NAME[c.policy]?.note}>
                        {policyName(c.policy)}
                        {c.best && <span className="lab-best">Best</span>}
                      </span>
                      <span className="lab-score-track" aria-hidden="true">
                        {c.lo !== null && c.hi !== null && <span className="lab-score-band" style={{ left: `${pct(c.lo)}%`, width: `${Math.max(0.6, pct(c.hi) - pct(c.lo))}%` }} />}
                        <span className="lab-score-bar" style={{ width: `${pct(c.value)}%` }} />
                      </span>
                      <span className="lab-score-value">
                        {fmtScore(metric, c.value)}
                        <span className="sr-only"> 95% range {fmtRange(metric, c.lo, c.hi)}</span>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </figure>
          );
        })}
      </div>
      {tradeoff && (
        <p className="mt-5 flex items-start gap-2.5 text-sm text-muted" data-testid="lab-tradeoff">
          <svg viewBox="0 0 16 16" className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={1.5}><circle cx="8" cy="8" r="6.5" /><path d="M8 7.2v4M8 4.8v.01" strokeLinecap="round" /></svg>
          <span>{tradeoff}</span>
        </p>
      )}
    </section>
  );
}

function Replay({ bundle }: { bundle: DefenseLabBundle }) {
  const policies = [...new Set(bundle.episodes.map(e => e.policy))];
  const scenarios = [...new Set(bundle.episodes.map(e => e.scenario))];
  const [policy, setPolicy] = useState(policies.includes('ddqn') ? 'ddqn' : policies[0] ?? '');
  const [scenarioId, setScenarioId] = useState(scenarios[0] ?? '');
  const index = Math.max(0, bundle.episodes.findIndex(e => e.policy === policy && e.scenario === scenarioId));
  const episode: LabEpisode | undefined = bundle.episodes[index];
  const scenario = bundle.scenarios.find(s => s.name === episode?.scenario);
  return (
    <section aria-labelledby="replay-title">
      <p className="lab-eyebrow">02 — Watch it in action</p>
      <h2 id="replay-title" className="mt-2 text-2xl font-semibold tracking-tight md:text-3xl">Replay a recorded evaluation</h2>
      <p className="mt-2 max-w-3xl text-sm text-muted">Pick a defender and an attacker, press play, and follow what the defender saw and did at each step.</p>
      {!episode || !scenario ? <div className="mt-4"><Empty>This bundle has no recorded episodes.</Empty></div> : (
        <div className="lab-replay-card mt-6">
          <div className="flex flex-wrap items-end justify-between gap-4 border-b border-line px-5 py-4">
            <div role="group" aria-label="Defender" className="lab-segment">
              {policies.map(p => (
                <button key={p} type="button" aria-pressed={p === policy} onClick={() => setPolicy(p)} className="lab-segment-btn">{policyName(p)}</button>
              ))}
            </div>
            <label className="flex flex-col gap-1 text-xs text-muted">
              Attacker
              <select aria-label="Attacker scenario" className={inputClass} value={scenarioId} onChange={e => setScenarioId(e.target.value)}>
                {scenarios.map(s => <option key={s} value={s}>{scenarioName(s)}</option>)}
              </select>
            </label>
          </div>
          <div className="p-5"><ReplayStage key={index} episode={episode} scenario={scenario} /></div>
        </div>
      )}
    </section>
  );
}
