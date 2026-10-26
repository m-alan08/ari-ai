'use client';

import { useEffect, useMemo, useState } from 'react';
import { NodeGraph } from '@/components/node-graph';
import { num } from '@/lib/format';
import { episodeOutcome, SCENARIO_WORDS, STAGE_WORDS } from '@/lib/lab-plain';
import {
  ACTION_SHORT, actionMeaning, keyMoments, latestAction, nodeStatusLine, nodeViews, overallStatus, parseAction,
  serviceInfo, signalValue, SIGNAL_BANDS, stepStory, storyMoments, type Moment,
} from '@/lib/lab-story';
import type { HiddenStage, LabEpisode, LabScenario } from '@/lib/lab-types';

const SPEEDS = [{ label: '1×', ms: 1200 }, { label: '4×', ms: 300 }, { label: '8×', ms: 150 }] as const;

/** Recorded-episode player: status, network map, story panel, and playback timeline. */
export function ReplayStage({ episode, scenario }: { episode: LabEpisode; scenario: LabScenario }) {
  const steps = episode.steps.length;
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [truth, setTruth] = useState(true);
  const moments = useMemo(() => keyMoments(episode, scenario), [episode, scenario]);
  const story = useMemo(() => storyMoments(moments), [moments]);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setT(v => { if (v >= steps - 1) { setPlaying(false); return v; } return v + 1; }), SPEEDS[speed]?.ms ?? 180);
    return () => clearInterval(id);
  }, [playing, steps, speed]);

  const step = episode.steps[Math.min(t, steps - 1)];
  const seek = (v: number) => { setPlaying(false); setT(Math.max(0, Math.min(steps - 1, v))); };
  const nextMoment = moments.find(m => m.t > t);
  const prevMoment = [...moments].reverse().find(m => m.t < t);
  const currentMoment = [...story].reverse().find(m => m.t <= t) ?? null;
  const status = overallStatus(scenario, step);
  const last = latestAction(episode, t);
  const s = stepStory(episode, scenario, t);
  const label = (n: string) => serviceInfo(n).label;

  return (
    <div className="flex flex-col gap-5">
      {/* Status overview */}
      <div className="lab-status" data-testid="lab-status">
        <span className="lab-mode label-mono" data-testid="lab-mode">Isolated test network · recorded evaluation</span>
        <span className="lab-status-item"><span className="text-faint">Attacker</span> {SCENARIO_WORDS[episode.scenario]?.name ?? episode.scenario}</span>
        <span className="lab-status-item"><span className="text-faint">Watching</span> {scenario.nodes.length} services</span>
        <span className={`lab-status-item lab-tone-${status.tone}`}><span aria-hidden="true" className="lab-dot" />{status.text}</span>
        <span className="lab-status-item min-w-0"><span className="text-faint">Latest AI action</span> <span className="truncate">{last ? `${ACTION_SHORT[parseAction(last.action).verb] ?? last.action}${parseAction(last.action).node ? ` ${label(parseAction(last.action).node!)}` : ''} (step ${last.t})` : 'none yet'}</span></span>
      </div>

      <div className="replay-stage-grid">
        <div className="min-w-0">
          <NodeGraph scenario={scenario} states={step?.nodes ?? []} action={step?.action} t={t} lastAction={last} selected={selected} onSelect={setSelected} showTruth={truth} />
          <p className="lab-swipe">Swipe the map sideways to see every service →</p>
          {/* Playback: one slim control bar; the scrubber is the timeline (YouTube-style chapter dots). */}
          <div className="lab-player">
            <button type="button" className="lab-play" onClick={() => { if (!playing && t >= steps - 1) setT(0); setPlaying(p => !p); }} aria-label={playing ? 'Pause' : t >= steps - 1 ? 'Replay' : 'Play'}>
              {playing
                ? <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4" y="3" width="3" height="10" rx="1" /><rect x="9" y="3" width="3" height="10" rx="1" /></svg>
                : <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.2v9.6a.6.6 0 0 0 .9.5l7.6-4.8a.6.6 0 0 0 0-1L5.9 2.7a.6.6 0 0 0-.9.5z" /></svg>}
            </button>
            <button type="button" className="lab-skip" onClick={() => prevMoment && seek(prevMoment.t)} disabled={!prevMoment} aria-label="Previous key moment">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 3v10M12.5 3.6v8.8L6.5 8z" /></svg>
            </button>
            <button type="button" className="lab-skip" onClick={() => nextMoment && seek(nextMoment.t)} disabled={!nextMoment} aria-label="Next key moment">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M12 3v10M3.5 3.6v8.8L9.5 8z" /></svg>
            </button>
            <Scrubber episode={episode} moments={story} t={t} steps={steps} horizon={scenario.horizon} onSeek={seek} truth={truth} />
            <span className="lab-time" style={{ minWidth: `${7 + 2 * String(scenario.horizon).length}ch` }}>t = {step?.t ?? 0} / {scenario.horizon}</span>
            <button type="button" className="lab-speed" onClick={() => setSpeed(v => (v + 1) % SPEEDS.length)} aria-label={`Playback speed ${SPEEDS[speed]?.label ?? ''}, click to change`}>
              {SPEEDS[speed]?.label}
            </button>
          </div>
          <p className="lab-now-moment" aria-live="polite">
            {currentMoment ? <><span className="lab-now-moment-when">Last key moment · step {currentMoment.t}</span> {currentMoment.text}</> : <span className="text-faint">Press play or use the arrows to jump between key moments.</span>}
          </p>
        </div>

        <aside className="replay-explanation" aria-label="What's happening">
          <div className="lab-now">
            <p className="label-mono flex justify-between text-white/60"><span>Step {step?.t ?? 0} of {scenario.horizon}</span><span>Recorded</span></p>
            <dl className="mt-3 flex flex-col gap-3 text-sm">
              <div><dt className="lab-q">What’s happening</dt><dd className="text-white/85" data-testid="lab-step-story">{s.now}</dd></div>
              <div>
                <dt className="lab-q">What the AI did</dt>
                <dd data-testid="lab-step-action"><span className="sr-only">At step {step?.t ?? 0} the defender: </span><strong className="block text-white">{s.did}</strong>{s.tradeoff && <span className="mt-1 block text-white/60">{s.tradeoff}</span>}</dd>
              </div>
              <div><dt className="lab-q">Why</dt><dd className="text-white/70">{s.why}</dd></div>
              {s.effect && <div><dt className="lab-q">Did it change anything?</dt><dd className="text-white/85" data-testid="lab-step-effect">{s.effect}</dd></div>}
            </dl>
          </div>
          {selected ? <ServicePanel episode={episode} scenario={scenario} name={selected} t={t} truth={truth} onClose={() => setSelected(null)} /> : (
            <p className="rounded-xl border border-dashed border-line px-4 py-3 text-sm text-muted">Select a service on the map to see what it does and what the AI saw there.</p>
          )}
        </aside>
      </div>

      <div className="lab-replay-foot">
        <div className="lab-outcome-row">
          <span className={`lab-outcome ${episode.compromised ? 'lab-outcome-bad' : 'lab-outcome-ok'}`}>{episode.compromised ? 'Attacker won' : 'Attack contained'}</span>
          <p className="min-w-0 text-sm text-ink" data-testid="lab-episode-outcome">{episodeOutcome(episode)} <span className="text-muted" data-testid="lab-episode-story">{SCENARIO_WORDS[episode.scenario]?.note ?? ''}</span></p>
        </div>
        <GroundTruth episode={episode} t={t} truth={truth} onToggle={setTruth} label={label} />
      </div>
    </div>
  );
}

function ServicePanel({ episode, scenario, name, t, truth, onClose }: { episode: LabEpisode; scenario: LabScenario; name: string; t: number; truth: boolean; onClose: () => void }) {
  const step = episode.steps[t];
  const view = nodeViews(scenario, step?.nodes ?? []).find(v => v.name === name);
  const state = step?.nodes.find(n => n.node === name);
  const info = serviceInfo(name);
  const history = episode.steps.slice(0, t + 1).map(s => signalValue(s.nodes.find(n => n.node === name)) ?? 0);
  const actions = episode.steps.slice(0, t + 1).map((s, i) => ({ t: s.t ?? i, ...parseAction(s.action) })).filter(a => a.node === name);
  const lastAct = actions.at(-1);
  if (!view) return null;
  const w = 240; const h = 40;
  const span = Math.max(20, history.length - 1); // scale to what has been played so far
  const pts = history.map((v, i) => `${(i / span) * w},${h - v * h}`).join(' ');
  return (
    <section className="rounded-xl border border-line bg-panel p-4" aria-labelledby="svc-title" data-testid="lab-service-panel">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="label-mono text-faint">{name}{view.critical ? ' · critical' : ''}</p>
          <h3 id="svc-title" className="mt-1 text-lg font-semibold tracking-tight">{info.label}</h3>
        </div>
        <button type="button" onClick={onClose} className="rounded-md px-2 py-1 text-xs text-muted hover:bg-panel-2" aria-label="Close service details">Close</button>
      </div>
      <p className="mt-1 text-sm text-muted">{info.role}</p>
      <ul className="mt-3 flex flex-col gap-1.5 text-sm">
        <li><span className="text-faint">Reachable from the internet: </span>{view.exposed ? 'yes' : 'no'}</li>
        <li><span className="text-faint">Right now: </span>{nodeStatusLine(view)}</li>
        <li><span className="text-faint">AI actions here so far: </span>{actions.length === 0 ? 'none' : `${actions.length} (latest at step ${lastAct?.t}: ${ACTION_SHORT[lastAct?.verb ?? ''] ?? lastAct?.verb})`}</li>
      </ul>
      {lastAct && <p className="mt-2 text-xs text-muted">{actionMeaning(`${lastAct.verb}:${name}`, () => info.label).did}</p>}
      <figure className="mt-3">
        <svg viewBox={`0 0 ${w} ${h}`} className="h-10 w-full overflow-visible" role="img" aria-label={`Signal at ${info.label} from step 0 to ${t}`}>
          <line x1="0" x2={w} y1={h - SIGNAL_BANDS.high * h} y2={h - SIGNAL_BANDS.high * h} stroke="var(--color-line)" strokeDasharray="3 3" />
          <polyline points={pts} fill="none" stroke="var(--color-brand)" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
        </svg>
        <figcaption className="text-xs text-faint">Observed signal so far (dashed line = suspicious band)</figcaption>
      </figure>
      {truth && state && <p className="mt-2 text-xs text-[#b42318]">Hidden truth (not seen by the defender): attacker {STAGE_WORDS[state.hidden_stage_ground_truth]}.</p>}
      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-muted">Observed values</summary>
        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
          <dt className="text-faint">alert rate</dt><dd className="font-mono">{num(state?.alert_rate ?? 0, 2)}</dd>
          <dt className="text-faint">anomaly score</dt><dd className="font-mono">{num(state?.anomaly_score ?? 0, 2)}</dd>
          <dt className="text-faint">monitoring level</dt><dd className="font-mono">{state?.monitoring_level ?? 0}</dd>
          <dt className="text-faint">rate limited</dt><dd className="font-mono">{String(!!state?.rate_limited)}</dd>
          <dt className="text-faint">isolated</dt><dd className="font-mono">{String(!!state?.isolated)}</dd>
          <dt className="text-faint">reassessment pending</dt><dd className="font-mono">{String(!!state?.reassessment_pending)}</dd>
        </dl>
      </details>
    </section>
  );
}

const STAGE_FILL: Record<HiddenStage, string> = { none: 'transparent', foothold: '#f0a868', escalated: '#e0735c', impact: '#b42318' };
const THUMB = 14; // px; matches .lab-scrub-thumb and the input's thumb in globals.css
const STAGE_RANK: Record<HiddenStage, number> = { none: 0, foothold: 1, escalated: 2, impact: 3 };

/**
 * The scrubber is the timeline: the track is shaded by the strongest observed signal at each
 * step, key moments are small dots on it, and a native range input (label "Step") drives it so
 * keyboard and assistive tech work. The hidden-truth strip appears only when that overlay is on.
 */
function Scrubber({ episode, moments, t, steps, horizon, onSeek, truth }: { episode: LabEpisode; moments: Moment[]; t: number; steps: number; horizon: number; onSeek: (t: number) => void; truth: boolean }) {
  const n = Math.max(1, steps);
  const peaks = useMemo(() => episode.steps.map(s => Math.max(0, ...s.nodes.map(x => signalValue(x) ?? 0))), [episode]);
  const stages = useMemo(() => episode.steps.map(s => {
    let stage: HiddenStage = 'none';
    for (const x of s.nodes) if (STAGE_RANK[x.hidden_stage_ground_truth] > STAGE_RANK[stage]) stage = x.hidden_stage_ground_truth;
    return stage;
  }), [episode]);
  // Every mark sits where the input's own (14px) thumb would: step 0 at the far left, the last
  // step at the far right, never past either end. Step i's shading is centred on that spot too.
  const at = (i: number) => `calc(${THUMB / 2}px + (100% - ${THUMB}px) * ${Math.min(i, n - 1) / Math.max(1, n - 1)})`;
  const cells = `-0.5 0 ${Math.max(1, n - 1)} 1`;
  return (
    <div className="lab-scrub" data-testid="lab-timeline">
      <div className="lab-scrub-track" aria-hidden="true">
        <svg viewBox={cells} preserveAspectRatio="none" className="lab-scrub-heat">
          {peaks.map((p, i) => <rect key={i} x={i - 0.5} y={0} width={1.02} height={1} fill="#c2452d" opacity={0.06 + p * 0.55} />)}
        </svg>
        <span className="lab-scrub-played" style={{ width: at(t) }} />
        {truth && (
          <svg viewBox={cells} preserveAspectRatio="none" className="lab-scrub-truth">
            {stages.map((st, i) => st !== 'none' && <rect key={i} x={i - 0.5} y={0} width={1.02} height={1} fill={STAGE_FILL[st]} />)}
          </svg>
        )}
      </div>
      {moments.filter(m => m.kind !== 'end').map(m => (
        <button key={`${m.t}-${m.text}`} type="button" className="lab-scrub-dot" data-kind={m.kind} style={{ left: at(m.t) }} onClick={() => onSeek(m.t)} aria-label={`Jump to step ${m.t}: ${m.text}`} title={`${m.t} · ${m.text}`} />
      ))}
      <input
        aria-label="Step"
        aria-valuetext={`Step ${episode.steps[t]?.t ?? t} of ${horizon}`}
        type="range" min={0} max={Math.max(0, n - 1)} value={t}
        onChange={e => onSeek(Number(e.target.value))}
        className="lab-scrub-input"
      />
      <span className="lab-scrub-thumb" style={{ left: at(t) }} aria-hidden="true" />
    </div>
  );
}

function GroundTruth({ episode, t, truth, onToggle, label }: { episode: LabEpisode; t: number; truth: boolean; onToggle: (v: boolean) => void; label: (n: string) => string }) {
  const states = episode.steps[t]?.nodes ?? [];
  return (
    <div className="lab-truth">
      <label className="lab-switch">
        <input type="checkbox" role="switch" checked={truth} onChange={e => onToggle(e.target.checked)} />
        <span aria-hidden="true" className="lab-switch-track"><span /></span>
        <span className="text-sm text-ink">Show on the map (for learning)</span>
      </label>
      <p className="text-xs text-faint">Test-environment ground truth — hidden attacker stage, never observed by the policy</p>
      {truth && (
        <ul className="mt-1 flex w-full flex-wrap gap-1.5 text-xs">
          {states.map(s => <li key={s.node} className="rounded-full bg-panel-2 px-2 py-0.5">{label(s.node)} · attacker {STAGE_WORDS[s.hidden_stage_ground_truth]}</li>)}
        </ul>
      )}
    </div>
  );
}
