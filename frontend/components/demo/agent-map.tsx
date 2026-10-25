'use client';

import { useMemo, useState } from 'react';
import type { DemoComponent, DemoRepo } from '@/lib/demo/data';
import { affected, componentStates, coreComponent, PHASES, plainStep, STATE_LABEL, TEAM_COLOR, ticks, type ComponentState, type Tick } from '@/lib/demo/agent-map';
import { SPEEDS, type Playback } from '@/lib/demo/replay';
import type { AgentId, AgentRunView, Analysis } from '@/lib/demo/scenarios';

type Status = 'queued' | 'running' | 'done' | 'skipped' | 'failed';
const STATUS_WORD: Record<Status, string> = { queued: 'Queued', running: 'Working', done: 'Done', skipped: 'Not needed', failed: 'Failed' };

/**
 * Seven-agent replay as a live map: agents grouped by phase on the left (red team, Trace, blue
 * team, Verify), the parts of the app this commit touches in the middle with a beam to the part
 * the active agent is on, and what happened there on the right. The red team looks around; a
 * part turns red only when Probe confirms an issue there, and only that part is traced, fixed and
 * verified. Replay can be paused, sped up or scrubbed. Illustrative (ADR 0013).
 */
export function AgentMap({ repo, commitSha, analysis, statuses, played, started, playback, accepted, offset }: {
  repo: DemoRepo;
  commitSha: string;
  analysis: Analysis;
  statuses: Record<AgentId, Status>;
  /** Agent steps finished so far (0 … all). */
  played: number;
  /** Whether the agents' phase has begun (after reading the change and the review). */
  started: boolean;
  playback: Playback;
  accepted: boolean;
  /** Playhead ticks spent before the first agent step (understanding + review). */
  offset: number;
}) {
  const all = useMemo(() => ticks(analysis, repo), [analysis, repo]);
  const touched = useMemo(() => affected(all, repo), [all, repo]);
  const current: Tick | undefined = started && played < all.length ? all[played] : undefined;
  const states = componentStates(all, played, repo, analysis, accepted, current);
  const [focus, setFocus] = useState<{ kind: 'component'; id: string } | { kind: 'agent'; id: AgentId } | null>(null);
  const following = focus === null;
  const view = focus ?? (current ? { kind: 'component' as const, id: current.component } : { kind: 'component' as const, id: coreComponent(repo) });
  const byId = new Map(analysis.agents.map(a => [a.agent, a]));
  const finished = played >= all.length;
  const label = current ? `${byId.get(current.agent)?.label} · ${plainStep(current.step)}` : finished ? 'All seven agents finished' : 'Reading the change before the agents start';

  return (
    <section className="amap" data-testid="demo-agents" aria-label="Seven agents at work">
      <header className="amap-head">
        <p className="amap-now">
          <span className="amap-live" data-on={playback.playing || undefined} aria-hidden="true" />
          <span className="min-w-0 truncate" data-testid="demo-agent-now">{label}</span>
        </p>
        <ol className="amap-phases" aria-label="Phases">
          {PHASES.map(phase => {
            const st = phase.agents.map(a => statuses[a]);
            const state = st.every(s => s === 'skipped') ? 'skipped' : st.some(s => s === 'running') ? 'active' : st.every(s => s === 'done' || s === 'skipped') ? 'done' : 'todo';
            return <li key={phase.label} data-state={state}><span>{phase.label}</span>{state === 'skipped' && <span className="sr-only"> (not needed)</span>}</li>;
          })}
        </ol>
      </header>

      <div className="amap-grid">
        <nav className="amap-rail" aria-label="Agents in orchestrator order">
          {PHASES.map(phase => (
            <div key={phase.label} className="amap-group">
              <p className="amap-group-label">{phase.label}</p>
              <ol>
                {phase.agents.map(id => {
                  const agent = byId.get(id);
                  if (!agent) return null;
                  const status = statuses[id];
                  const live = current?.agent === id;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        className="amap-agent"
                        data-status={status}
                        aria-pressed={view.kind === 'agent' && view.id === id}
                        onClick={() => setFocus(view.kind === 'agent' && view.id === id ? null : { kind: 'agent', id })}
                        data-testid={`demo-agent-${id}`}
                        style={{ ['--team' as string]: TEAM_COLOR[agent.team] }}
                      >
                        <StatusMark status={status} />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-baseline justify-between gap-2">
                            <span className="amap-agent-name">{agent.label}</span>
                            <span className="amap-agent-status">{STATUS_WORD[status]}</span>
                          </span>
                          <span className="amap-agent-job">{live && current ? plainStep(current.step) : agent.job}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </div>
          ))}
        </nav>

        <div className="amap-stage">
          <SystemMap repo={repo} commitSha={commitSha} states={states} touched={touched} current={current} analysis={analysis} selected={view.kind === 'component' ? view.id : null} onSelect={id => setFocus({ kind: 'component', id })} />
          <ul className="amap-legend" aria-label="Colours on the map">
            <li data-k="issue">Issue confirmed</li>
            <li data-k="refuted">False alarm</li>
            <li data-k="fixed">Fixed &amp; verified</li>
            <li data-k="scanned">Waiting for proof</li>
            <li data-k="red">Red team</li>
            <li data-k="blue">Blue team</li>
          </ul>
          <Transport playback={playback} total={all.length} played={played} offset={offset} />
        </div>

        <aside className="amap-inspect" aria-live="polite">
          {view.kind === 'agent'
            ? <AgentDetail agent={byId.get(view.id)!} status={statuses[view.id]} />
            : <ComponentDetail component={repo.components.find(c => c.id === view.id)!} state={states[view.id] ?? 'outside'} done={all.slice(0, played).filter(t => t.component === view.id)} live={current?.component === view.id ? current : undefined} />}
          {!following && <button type="button" className="amap-follow" onClick={() => setFocus(null)}>Follow the active agent</button>}
        </aside>
      </div>
    </section>
  );
}

function StatusMark({ status }: { status: Status }) {
  return (
    <svg viewBox="0 0 16 16" className="amap-mark" aria-hidden="true">
      {status === 'done' && <><circle cx="8" cy="8" r="7" className="amap-mark-fill" /><path d="M5 8.2l2 2 4-4.4" className="amap-mark-ink" /></>}
      {status === 'running' && <><circle cx="8" cy="8" r="6.25" className="amap-mark-ring" /><path d="M8 1.75a6.25 6.25 0 0 1 6.25 6.25" className="amap-mark-arc" /></>}
      {status === 'queued' && <circle cx="8" cy="8" r="6.25" className="amap-mark-ring" />}
      {status === 'skipped' && <><circle cx="8" cy="8" r="6.25" className="amap-mark-ring" /><path d="M5.5 8h5" className="amap-mark-dash" /></>}
      {status === 'failed' && <><circle cx="8" cy="8" r="7" className="amap-mark-fill" /><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" className="amap-mark-ink" /></>}
    </svg>
  );
}

function Transport({ playback, total, played, offset }: { playback: Playback; total: number; played: number; offset: number }) {
  const speedIndex = SPEEDS.indexOf(playback.speed);
  const next = SPEEDS[(speedIndex + 1) % SPEEDS.length]!;
  return (
    <div className="amap-transport">
      <button type="button" className="amap-play" onClick={playback.toggle} aria-label={playback.playing ? 'Pause replay' : 'Play replay'} data-testid="demo-play">
        {playback.playing
          ? <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4" y="3" width="3" height="10" rx="1" /><rect x="9" y="3" width="3" height="10" rx="1" /></svg>
          : <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.2v9.6a.6.6 0 0 0 .9.5l7.6-4.8a.6.6 0 0 0 0-1L5.9 2.7a.6.6 0 0 0-.9.5z" /></svg>}
      </button>
      {/* The bar spans the agents' steps only: its far left is the first agent step, so dragging to
          the start shows the start (the read and code-review ticks before it are not on this bar). */}
      <input
        type="range" min={0} max={Math.max(1, total)} step={1} value={Math.min(played, total)}
        onChange={e => playback.seek(offset + Number(e.target.value))}
        aria-label="Replay position" aria-valuetext={`Step ${Math.min(played, total)} of ${total}`} className="amap-scrub"
        style={{ ['--f' as string]: Math.min(played, total) / Math.max(1, total) }}
      />
      <span className="amap-count">{played} / {total}</span>
      <button type="button" className="amap-speed" onClick={() => playback.setSpeed(next)} aria-label={`Replay speed ${playback.speed}×, change to ${next}×`}>{playback.speed}×</button>
    </div>
  );
}

const half = (xs: DemoComponent[]) => Math.ceil(xs.length / 2);
const rowY = (row: number, rows: number) => ((row + 0.5) / rows) * 100;

function SystemMap({ repo, commitSha, states, touched, current, analysis, selected, onSelect }: {
  repo: DemoRepo; commitSha: string; states: Record<string, ComponentState>; touched: Set<string>; current?: Tick; analysis: Analysis; selected: string | null; onSelect: (id: string) => void;
}) {
  const h = half(repo.components);
  const left = repo.components.slice(0, h);
  const right = repo.components.slice(h);
  const idx = current ? repo.components.findIndex(c => c.id === current.component) : -1;
  const target = idx < 0 ? null : idx < h ? { side: 'left' as const, y: rowY(idx, left.length) } : { side: 'right' as const, y: rowY(idx - h, right.length) };
  const agent = current ? analysis.agents.find(a => a.agent === current.agent) : undefined;
  const color = current ? TEAM_COLOR[current.team] : '#fff';
  const files = analysis.review.diff.length;
  const card = (c: DemoComponent) => {
    const active = current?.component === c.id;
    const state = states[c.id] ?? 'outside';
    return (
      <li key={c.id} className="amap-slot">
        {active && agent && current && (
          <span className="amap-tag" style={{ ['--team' as string]: color }} aria-hidden="true">{agent.label} · {plainStep(current.step).toLowerCase()}</span>
        )}
        <button
          type="button"
          className="amap-node"
          data-state={state}
          data-active={active || undefined}
          aria-pressed={selected === c.id}
          aria-label={`${c.label}${c.critical ? ', critical' : ''}: ${STATE_LABEL[state]}${active && agent ? `, ${agent.label} working here` : ''}`}
          onClick={() => onSelect(c.id)}
          data-testid={`demo-node-${c.id}`}
          style={active ? { ['--beam' as string]: color } : undefined}
        >
          <span className="flex items-center gap-2.5">
            <ComponentIcon kind={c.kind} />
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{c.label}</span>
            {c.critical && <span className="amap-crit">Critical</span>}
          </span>
          <span className="amap-node-state"><span className="amap-node-dot" aria-hidden="true" />{active && state !== 'issue' ? 'Being tested' : STATE_LABEL[state]}</span>
        </button>
      </li>
    );
  };
  return (
    <div className="amap-map" role="group" aria-label="Parts of the app this commit touches">
      <ul className="amap-col">{left.map(card)}</ul>
      <div className="amap-hub-wrap">
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {([['left', left], ['right', right]] as const).map(([side, items]) => {
            const edge = side === 'left' ? 0 : 100;
            const bus = side === 'left' ? 10 : 90;
            const hub = side === 'left' ? 20 : 80;
            const ys = items.map((_, i) => rowY(i, items.length));
            return (
              <g key={side} className="amap-wire">
                {ys.map((y, i) => <line key={items[i]!.id} x1={edge} y1={y} x2={bus} y2={y} vectorEffect="non-scaling-stroke" data-touched={touched.has(items[i]!.id) || undefined} />)}
                {ys.length > 0 && <line x1={bus} y1={Math.min(...ys)} x2={bus} y2={Math.max(...ys)} vectorEffect="non-scaling-stroke" />}
                <line x1={bus} y1={50} x2={hub} y2={50} vectorEffect="non-scaling-stroke" />
              </g>
            );
          })}
          {target && (
            <polyline
              key={`${current?.agent}-${current?.index}`}
              className="amap-beam"
              points={`${target.side === 'left' ? 0 : 100},${target.y} ${target.side === 'left' ? 10 : 90},${target.y} ${target.side === 'left' ? 10 : 90},50 ${target.side === 'left' ? 20 : 80},50`}
              stroke={color}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
        <div className="amap-hub" data-active={current ? true : undefined} style={current ? { ['--beam' as string]: color } : undefined}>
          <span className="amap-hub-label">This commit</span>
          <span className="mt-1.5 block font-mono text-base text-white">{commitSha.slice(0, 7)}</span>
          <span className="mt-1 block text-[0.72rem] text-white/50">{files > 0 ? `${files} file${files === 1 ? '' : 's'} · ` : ''}{touched.size} of {repo.components.length} parts</span>
        </div>
      </div>
      <ul className="amap-col">{right.map(card)}</ul>
    </div>
  );
}

/** One consistent line-icon set for the parts of an app. */
function ComponentIcon({ kind }: { kind: string }) {
  const p = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  let glyph;
  switch (kind) {
    case 'edge': glyph = <g {...p}><path d="M8 1.8 13.2 4v3.6c0 3.2-2.2 5.6-5.2 6.6-3-1-5.2-3.4-5.2-6.6V4z" /><path d="M5.6 8h4.8" /></g>; break;
    case 'service': glyph = <g {...p}><rect x="2.5" y="2.5" width="11" height="4.5" rx="1.2" /><rect x="2.5" y="9" width="11" height="4.5" rx="1.2" /><path d="M5 4.75h.01M5 11.25h.01" /></g>; break;
    case 'site': glyph = <g {...p}><rect x="2" y="3" width="12" height="10" rx="1.6" /><path d="M2 6h12" /></g>; break;
    case 'identity': glyph = <g {...p}><circle cx="5.5" cy="8" r="2.8" /><path d="M8.3 8H14M12 8v2.2M14 8v1.6" /></g>; break;
    case 'worker': glyph = <g {...p}><path d="M13 8a5 5 0 1 1-1.5-3.55" /><path d="M13.2 2.6v2.6h-2.6" /></g>; break;
    case 'data': glyph = <g {...p}><ellipse cx="8" cy="4" rx="5" ry="1.8" /><path d="M3 4v8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8V4M3 8c0 1 2.2 1.8 5 1.8S13 9 13 8" /></g>; break;
    case 'logging': glyph = <g {...p}><path d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01" /></g>; break;
    case 'endpoints': glyph = <g {...p}><path d="M6 2.5v3M10 2.5v3M4.5 5.5h7v2.5a3.5 3.5 0 0 1-7 0zM8 11.5v2" /></g>; break;
    default: glyph = <g {...p}><rect x="3" y="3" width="10" height="10" rx="2" /></g>;
  }
  return <svg viewBox="0 0 16 16" className="amap-icon" aria-hidden="true">{glyph}</svg>;
}

const LATEST = 5;

function ComponentDetail({ component, state, done, live }: { component: DemoComponent; state: ComponentState; done: Tick[]; live?: Tick }) {
  const recent = [...done].reverse();
  return (
    <div data-testid="demo-node-detail">
      <p className="amap-kicker">Part of the app</p>
      <p className="mt-1 text-lg font-semibold tracking-tight">{component.label}</p>
      <p className="amap-pill mt-2" data-state={state}>{STATE_LABEL[state]}</p>
      {done.length === 0 && !live && <p className="mt-4 text-sm text-white/55">{state === 'outside' ? 'This commit does not touch it, so the agents leave it alone.' : 'No agent has reached this part yet.'}</p>}
      {(done.length > 0 || live) && (
        <ol className="amap-ticks">
          {live && <TickLine tick={live} live />}
          {recent.slice(0, LATEST).map((t, i) => <TickLine key={i} tick={t} />)}
          {recent.length > LATEST && <li className="text-xs text-white/40">and {recent.length - LATEST} earlier step{recent.length - LATEST === 1 ? '' : 's'}</li>}
        </ol>
      )}
    </div>
  );
}

function TickLine({ tick, live = false }: { tick: Tick; live?: boolean }) {
  return (
    <li className="amap-tick" data-live={live || undefined} style={{ ['--team' as string]: TEAM_COLOR[tick.team] }}>
      <p className="text-sm text-white/90"><span className="font-medium capitalize">{tick.agent}</span> <span className="text-white/45">·</span> {plainStep(tick.step)}</p>
      <p className={`mt-0.5 text-xs ${tick.step.outcome === 'denied' ? 'text-[#ffc36b]' : 'text-white/50'}`}>{live ? 'Working…' : tick.step.outcome === 'denied' ? `Blocked by policy: ${tick.step.result}` : tick.step.result}</p>
    </li>
  );
}

function AgentDetail({ agent, status }: { agent: AgentRunView; status: Status }) {
  return (
    <div>
      <p className="amap-kicker">{agent.team === 'Independent' ? 'Independent check' : `${agent.team} team`}</p>
      <p className="mt-1 text-lg font-semibold tracking-tight">{agent.label} <span className="text-sm font-normal text-white/50">· {agent.job}</span></p>
      <p className="mt-3 text-sm leading-relaxed text-white/85" data-testid="demo-agent-outcome">
        {status === 'queued' ? 'Waiting for the previous agent to hand off.' : status === 'running' ? 'Working…' : agent.outcome}
      </p>
      {(status === 'done' || status === 'skipped') && <p className="mt-3 text-xs leading-relaxed text-white/55" data-testid="demo-agent-handoff">Hands off: {agent.handoff}</p>}
      {agent.evidence.length > 0 && status === 'done' && (
        <>
          <p className="amap-kicker mt-5">Evidence</p>
          <ul className="mt-2 flex flex-col gap-1.5 text-xs text-white/70">{agent.evidence.map(e => <li key={e} className="amap-evidence">{e}</li>)}</ul>
        </>
      )}
      {agent.steps.length > 0 && (
        <>
          <p className="amap-kicker mt-5">Every step ({agent.steps.length})</p>
          <ol className="mt-2 flex flex-col gap-2.5" data-testid="demo-agent-steps">
            {agent.steps.map((step, i) => (
              <li key={i} className="text-xs">
                <p className="text-white/85">{plainStep(step)} <span className="font-mono text-white/35">{step.tool}</span></p>
                <p className="text-white/45">{step.input}</p>
                <p className={step.outcome === 'denied' ? 'text-[#ffc36b]' : 'text-white/60'}>{step.outcome === 'denied' ? 'Blocked by policy: ' : ''}{step.result}</p>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
