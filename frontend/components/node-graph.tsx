'use client';

import { useCallback, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { num } from '@/lib/format';
import type { LabNodeState, LabScenario } from '@/lib/lab-types';
import { STAGE_WORDS } from '@/lib/lab-plain';
import { INTERNET, nodeStatusLine, nodeViews, parseAction, SIGNAL_BANDS, type NodeView } from '@/lib/lab-story';
import type { HiddenStage } from '@/lib/lab-types';

/**
 * Network map of a simulated scenario (Defense Lab). The layout is stable (columns from the
 * internet inwards along the recorded topology), so nodes never drift during replay. Every
 * visual state comes from observed values in the bundle; the hidden attacker stage is drawn
 * only when the viewer turns on the clearly labelled ground-truth overlay. A visually hidden
 * table repeats every node value for assistive technology.
 */

const W = 1080;
const H = 480;
export const INTERNET_ID = '__internet__';

type Point = { x: number; y: number };

/** Columns: internet, then breadth-first depth from internet-exposed services. */
export function layoutNetwork(scenario: LabScenario): Map<string, Point> {
  const depth = new Map<string, number>();
  const queue = scenario.nodes.filter(n => n.exposed).map(n => n.name);
  queue.forEach(n => depth.set(n, 0));
  while (queue.length) {
    const cur = queue.shift() as string;
    for (const [a, b] of scenario.edges) {
      const next = a === cur ? b : b === cur ? a : null;
      if (next && !depth.has(next)) { depth.set(next, (depth.get(cur) ?? 0) + 1); queue.push(next); }
    }
  }
  scenario.nodes.forEach(n => { if (!depth.has(n.name)) depth.set(n.name, 0); });
  const cols = new Map<number, string[]>();
  for (const n of scenario.nodes) { const d = depth.get(n.name) ?? 0; cols.set(d, [...(cols.get(d) ?? []), n.name]); }
  const maxD = Math.max(0, ...cols.keys());
  const x0 = 100; const x1 = W - 110;
  const step = (x1 - x0) / (maxD + 1);
  const pos = new Map<string, Point>();
  pos.set(INTERNET_ID, { x: x0, y: H / 2 });
  for (const [d, names] of cols) names.forEach((name, i) => pos.set(name, { x: x0 + step * (d + 1), y: ((i + 1) * H) / (names.length + 1) }));
  return pos;
}

type BadgeKind = 'watch' | 'slow' | 'cut' | 'recheck';
const BADGE_TITLE: Record<BadgeKind, string> = { watch: 'Watched closely', slow: 'Traffic slowed', cut: 'Cut off from the network', recheck: 'Re-check requested' };

/** Small state badge drawn beside a node. Exported so the legend uses identical marks. */
export function StateBadge({ kind, x = 0, y = 0 }: { kind: BadgeKind; x?: number; y?: number }) {
  const p = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  return (
    <g transform={`translate(${x},${y})`} className={`map-badge map-badge-${kind}`}>
      <title>{BADGE_TITLE[kind]}</title>
      <circle r="9" />
      {kind === 'watch' && <g {...p}><path d="M-5 0c2-3.2 8-3.2 10 0-2 3.2-8 3.2-10 0z" /><circle r="1.4" /></g>}
      {kind === 'slow' && <g {...p}><path d="M-4.5 3a5 5 0 1 1 9 0" /><path d="M0 2.5l2.5-3.5" /></g>}
      {kind === 'cut' && <g {...p}><path d="M-4-4l8 8M4-4l-8 8" /></g>}
      {kind === 'recheck' && <g {...p}><path d="M4 -2.5A4.6 4.6 0 1 0 4.4 2" /><path d="M4.5-5v3h-3" /></g>}
    </g>
  );
}

/* Node cards (pattern study of Omen's network view, own implementation): a card per service with
   its name, one status line with a coloured dot, and the observed signal as a bar. Colour means
   the same everywhere: red = attacker present for sure (ground truth), green = attacker cut off,
   yellow = false alarm (high signal, no attacker), blue = what the defender did. Without the
   ground-truth overlay only what the defender observed is shown (amber = suspicious). */
const CW = 196; const CH = 88; // card size in map units
const IW = 140; const IH = 64; // the internet card

type Tone = 'compromised' | 'contained' | 'false-alarm' | 'suspicious' | 'isolated' | 'healthy';

export function nodeTone(v: NodeView, stage: HiddenStage, truth: boolean): { tone: Tone; text: string } {
  const attacker = truth && stage !== 'none';
  if (attacker && v.isolated) return { tone: 'contained', text: 'Attacker cut off' };
  if (attacker) return { tone: 'compromised', text: `Attacker ${STAGE_WORDS[stage]}` };
  if (v.isolated) return { tone: truth && v.signal === 'high' ? 'false-alarm' : 'isolated', text: truth && v.signal === 'high' ? 'Cut off · false alarm' : 'Cut off (offline)' };
  if (v.signal === 'high') return truth ? { tone: 'false-alarm', text: 'False alarm' } : { tone: 'suspicious', text: 'Suspicious activity' };
  if (v.signal === 'elevated') return { tone: 'suspicious', text: 'Elevated activity' };
  if (v.rateLimited) return { tone: 'healthy', text: 'Healthy · traffic slowed' };
  if (v.watching) return { tone: 'healthy', text: 'Healthy · watched closely' };
  return { tone: 'healthy', text: 'Healthy' };
}

const DEFENDER_DID: Record<string, string> = {
  isolate: 'Defender cut it off',
  rate_limit: 'Defender slowed traffic',
  raise_monitoring: 'Defender is watching',
  request_reassessment: 'Defender asked for a re-check',
};

/** What the defender did, as a phrase after "Defender" in the tracker line. */
const DEFENDER_MOVE: Record<string, (service: string) => string> = {
  isolate: n => `cut off ${n}`,
  rate_limit: n => `slowed traffic to ${n}`,
  raise_monitoring: n => `is watching ${n} closely`,
  request_reassessment: n => `asked for a re-check of ${n}`,
  escalate_to_human: () => 'called in a person',
};
const STAGE_RANK: Record<HiddenStage, number> = { none: 0, foothold: 1, escalated: 2, impact: 3 };

const listNames = (names: string[]) => (names.length <= 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`);

/**
 * One line each for who is where at this step. The attacker line is ground truth, so it exists
 * only with the overlay on; the defender line is its latest recorded move and when it happened.
 */
export function trackerLines(views: NodeView[], states: LabNodeState[], lastAction: { t: number; action: string } | null, t: number, truth: boolean) {
  let attacker: string | null = null;
  let inside = false;
  if (truth) {
    const label = (n: string) => views.find(v => v.name === n)?.info.label ?? n;
    const present = states.filter(s => s.hidden_stage_ground_truth !== 'none').sort((a, b) => STAGE_RANK[b.hidden_stage_ground_truth] - STAGE_RANK[a.hidden_stage_ground_truth]);
    const loose = present.filter(s => !s.isolated);
    const cut = present.filter(s => s.isolated);
    inside = loose.length > 0;
    if (loose.length) attacker = `${STAGE_WORDS[loose[0]!.hidden_stage_ground_truth]} on ${listNames(loose.map(s => label(s.node)))}`;
    else if (cut.length) attacker = `is cut off on ${listNames(cut.map(s => label(s.node)))}`;
    else attacker = 'is not inside the network right now';
  }
  let defender = 'has not acted yet, only watching';
  if (lastAction) {
    const a = parseAction(lastAction.action);
    const name = a.node ? (views.find(v => v.name === a.node)?.info.label ?? a.node) : '';
    defender = `${DEFENDER_MOVE[a.verb]?.(name) ?? a.verb} · ${lastAction.t === t ? 'this step' : `at step ${lastAction.t}`}`;
  }
  return { attacker, inside, defender };
}

const tagWidth = (text: string) => Math.round(text.length * 5.5 + 16);

function Tag({ text, x, className, row = 0 }: { text: string; x: number; className: string; row?: number }) {
  const w = tagWidth(text);
  return <g transform={`translate(${x},${-CH / 2 - 19 - row * 19})`} className={`net-tag ${className}`}><rect width={w} height="16" rx="5" /><text x={w / 2} y="11.3" textAnchor="middle">{text}</text></g>;
}

/** The card's status line: the state in the tone colour; a healthy service's note ("· watched closely") quieter. */
function StatusText({ text, tone }: { text: string; tone: Tone }) {
  const cut = tone === 'healthy' ? text.indexOf(' · ') : -1;
  if (cut < 0) return <text x={-CW / 2 + 28} y={3} className="net-status">{text}</text>;
  return <text x={-CW / 2 + 28} y={3} className="net-status">{text.slice(0, cut)}<tspan className="net-status-note">{text.slice(cut)}</tspan></text>;
}

export interface NetworkMapProps {
  scenario: LabScenario;
  states: LabNodeState[];
  /** Recorded action at the current step, e.g. "isolate:api". */
  action?: string;
  /** Current step; restarts the one-shot action highlight. */
  t?: number;
  /** The defender's latest recorded action at or before this step; defaults to this step's action. */
  lastAction?: { t: number; action: string } | null;
  selected?: string | null;
  onSelect?: (node: string | null) => void;
  showTruth?: boolean;
}

export function NodeGraph({ scenario, states, action = 'noop', t = 0, lastAction, selected = null, onSelect = () => {}, showTruth = false }: NetworkMapProps) {
  const base = useMemo(() => layoutNetwork(scenario), [scenario]);
  const [moved, setMoved] = useState<Map<string, Point>>(new Map());
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const [hover, setHover] = useState<string | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: string | null; sx: number; sy: number; ox: number; oy: number; moved: boolean } | null>(null);

  const pos = (id: string): Point => moved.get(id) ?? base.get(id) ?? { x: W / 2, y: H / 2 };
  const views = nodeViews(scenario, states);
  const byName = new Map(views.map(v => [v.name, v]));
  const stateBy = new Map(states.map(s => [s.node, s]));
  const exposed = scenario.nodes.filter(n => n.exposed).map(n => n.name);
  const edges: [string, string][] = [...exposed.map(e => [INTERNET_ID, e] as [string, string]), ...scenario.edges];
  const target = action.startsWith('noop') ? null : parseAction(action).node;
  const latest = lastAction !== undefined ? lastAction : action.startsWith('noop') ? null : { t, action };
  const latestNode = latest ? parseAction(latest.action).node : null;
  const track = trackerLines(views, states, latest, t, showTruth);
  const focus = selected ?? hover;
  const neighbours = new Set<string>();
  if (focus) { neighbours.add(focus); for (const [a, b] of edges) { if (a === focus) neighbours.add(b); if (b === focus) neighbours.add(a); } }

  const toSvg = useCallback((e: { clientX: number; clientY: number }) => {
    const m = svg.current?.getScreenCTM?.();
    if (!m || typeof DOMPoint === 'undefined') return { x: e.clientX, y: e.clientY };
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
    return { x: p.x, y: p.y };
  }, []);

  const onPointerDown = (e: ReactPointerEvent) => {
    if (e.button !== 0) return;
    const id = (e.currentTarget as Element).getAttribute('data-node');
    const p = toSvg(e);
    const start = id ? pos(id) : { x: view.x, y: view.y };
    drag.current = { id, sx: p.x, sy: p.y, ox: start.x, oy: start.y, moved: false };
    // Touch keeps the page scrollable (touch-action: pan-y): nodes are tap-to-select only.
    if (e.pointerType !== 'touch') (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    e.stopPropagation();
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d || e.pointerType === 'touch') return;
    const p = toSvg(e);
    const dx = p.x - d.sx; const dy = p.y - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    if (d.id) {
      const id = d.id;
      setMoved(m => new Map(m).set(id, { x: Math.min(W - 30, Math.max(30, d.ox + dx / view.k)), y: Math.min(H - 30, Math.max(30, d.oy + dy / view.k)) }));
    } else setView(v => ({ ...v, x: d.ox + dx, y: d.oy + dy }));
  };
  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved) return;
    if (d.id === null) onSelect(null);
    else if (d.id !== INTERNET_ID) onSelect(selected === d.id ? null : d.id);
  };
  const zoom = (f: number) => setView(v => {
    const k = Math.min(2.5, Math.max(0.6, v.k * f));
    return { k, x: W / 2 - ((W / 2 - v.x) * k) / v.k, y: H / 2 - ((H / 2 - v.y) * k) / v.k };
  });
  const reset = () => { setView({ k: 1, x: 0, y: 0 }); setMoved(new Map()); };

  const order = views.map(v => v.name);
  const focusNode = (name: string) => requestAnimationFrame(() => svg.current?.querySelector<SVGGElement>(`[data-node="${CSS.escape(name)}"]`)?.focus());
  const onKey = (name: string) => (e: KeyboardEvent) => {
    const i = order.indexOf(name);
    const go = (n: string | undefined) => { if (n) { onSelect(n); focusNode(n); } };
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(selected === name ? null : name); }
    else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); go(order[(i + 1) % order.length]); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); go(order[(i - 1 + order.length) % order.length]); }
    else if (e.key === 'Escape') onSelect(null);
  };

  const hovered = hover && hover !== INTERNET_ID ? byName.get(hover) : null;
  const tip = hovered ? pos(hovered.name) : null;

  return (
    <div className="net-map" data-truth={showTruth || undefined}>
      <div className="network-heading">
        <span className="network-title">Network at step {t}</span>
        <ul className="network-legend" aria-label="Map colours">
          {showTruth && <li data-k="attacker">Attacker present</li>}
          {showTruth && <li data-k="contained">Attacker cut off</li>}
          {showTruth && <li data-k="false">False alarm</li>}
          {!showTruth && <li data-k="suspicious">Suspicious</li>}
          <li data-k="defender">Defender&apos;s move</li>
          <li data-k="isolated">Cut off</li>
          <li data-k="slow">Slowed traffic</li>
          {showTruth && <li data-k="path">Attack path</li>}
        </ul>
        {/* Who is where, in words: the same facts as the tags on the cards. */}
        <p className="net-track" data-testid="lab-tracker">
          {track.attacker && <span className="net-track-item" data-who="attacker" data-out={!track.inside || undefined}><span aria-hidden="true" className="net-track-dot" /><strong>Attacker</strong> {track.attacker}</span>}
          <span className="net-track-item" data-who="defender"><span aria-hidden="true" className="net-track-dot" /><strong>Defender</strong> {track.defender}</span>
        </p>
      </div>
      <div className="net-scroll">
      <div className="net-canvas">
      <svg
        ref={svg}
        viewBox={`0 0 ${W} ${H}`}
        className="net-svg"
        style={{ touchAction: 'pan-y' }}
        role="group"
        aria-label={`Test network for ${scenario.name}: ${views.length} services. Tab to a service and use the arrow keys to move between them.`}
        data-testid="topology"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { drag.current = null; setHover(null); }}
        onWheel={e => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoom(e.deltaY < 0 ? 1.1 : 0.9); } }}
      >
        <defs>
          <pattern id="net-dots" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.9" fill="rgba(255,255,255,0.07)" /></pattern>
          <radialGradient id="net-hot"><stop offset="0%" stopColor="#c2452d" stopOpacity="0.42" /><stop offset="100%" stopColor="#c2452d" stopOpacity="0" /></radialGradient>
        </defs>
        <rect width={W} height={H} fill="url(#net-dots)" />
        <text x="28" y="30" className="network-zone">OUTSIDE</text><text x="260" y="30" className="network-zone">YOUR APPLICATION</text>
        <line x1="205" x2="205" y1="48" y2={H - 20} className="network-boundary" />
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {edges.map(([a, b]) => {
            let p = pos(a); let q = pos(b);
            let from = a; let to = b;
            if (q.x < p.x - 1) { [p, q] = [q, p]; [from, to] = [to, from]; }
            const va = byName.get(from); const vb = byName.get(to);
            const cut = va?.isolated || vb?.isolated;
            const slow = !cut && (va?.rateLimited || vb?.rateLimited);
            const lit = focus !== null && (a === focus || b === focus);
            const acted = target !== null && (a === target || b === target);
            const hasAttacker = (n: string) => n === INTERNET_ID || (stateBy.get(n)?.hidden_stage_ground_truth ?? 'none') !== 'none';
            const attack = showTruth && hasAttacker(from) && hasAttacker(to) && (from !== INTERNET_ID || to !== INTERNET_ID);
            const fromW = from === INTERNET_ID ? IW : CW; const toW = to === INTERNET_ID ? IW : CW;
            let d: string; let mid: Point;
            if (Math.abs(p.x - q.x) < 1) {
              // Same column: a short vertical link on the right side, clear of the tags.
              const [top, bottom] = p.y < q.y ? [p, q] : [q, p];
              const x = p.x + CW / 2 - 24;
              d = `M${x},${top.y + CH / 2} L${x},${bottom.y - CH / 2}`;
              mid = { x, y: (top.y + bottom.y) / 2 };
            } else {
              const x1 = p.x + fromW / 2; const x2 = q.x - toW / 2; const dx = (x2 - x1) / 2;
              d = `M${x1},${p.y} C${x1 + dx},${p.y} ${x2 - dx},${q.y} ${x2},${q.y}`;
              mid = { x: (x1 + x2) / 2, y: (p.y + q.y) / 2 };
            }
            return (
              <g key={`${a}>${b}`}>
                <path d={d} className="net-edge" data-cut={cut || undefined} data-slow={(slow && !attack) || undefined} data-lit={lit || undefined} data-attack={attack || undefined} data-dim={(focus !== null && !lit) || undefined} />
                {acted && <path key={`flash-${t}`} d={d} className="net-edge-flash" pathLength={1} />}
                {cut && <g transform={`translate(${mid.x},${mid.y})`} className="net-cut"><circle r="6" /><path d="M-3-3l6 6M3-3l-6 6" /></g>}
              </g>
            );
          })}

          {(() => {
            const p = pos(INTERNET_ID);
            return (
              <g transform={`translate(${p.x},${p.y})`} className="net-node net-internet" data-dim={(focus !== null && !neighbours.has(INTERNET_ID)) || undefined}
                onPointerEnter={() => setHover(INTERNET_ID)} onPointerLeave={() => setHover(null)}>
                <title>{INTERNET.role}</title>
                <rect x={-IW / 2} y={-IH / 2} width={IW} height={IH} rx="12" className="network-card" />
                <text x={-IW / 2 + 14} y="-5" className="net-label">Internet</text>
                <text x={-IW / 2 + 14} y="14" className="net-sub">Outside traffic</text>
                <circle cx={IW / 2} cy="0" r="2.6" className="net-port" />
              </g>
            );
          })()}

          {views.map(v => {
            const p = pos(v.name);
            const stage = stateBy.get(v.name)?.hidden_stage_ground_truth ?? 'none';
            const isTarget = target === v.name;
            const { tone, text } = nodeTone(v, stage, showTruth);
            const intruder = showTruth && stage !== 'none' ? (v.isolated ? 'Attacker cut off' : 'Attacker here') : null;
            // The defender's tag stays on the service of its latest move, faded once that move is in the past.
            const did = latest && latestNode === v.name ? DEFENDER_DID[parseAction(latest.action).verb] : undefined;
            const didBefore = did !== undefined && latest!.t !== t;
            const value = v.value ?? 0;
            const band = value >= SIGNAL_BANDS.high ? 'high' : value >= SIGNAL_BANDS.elevated ? 'elevated' : 'quiet';
            const barW = 74;
            return (
              <g
                key={v.name}
                data-node={v.name}
                transform={`translate(${p.x},${p.y})`}
                className="net-node"
                data-tone={tone}
                data-signal={v.signal}
                data-isolated={v.isolated || undefined}
                data-selected={selected === v.name || undefined}
                data-dim={(focus !== null && !neighbours.has(v.name)) || undefined}
                role="button"
                tabIndex={0}
                aria-pressed={selected === v.name}
                aria-label={`${v.info.label} (${v.name})${v.critical ? ', critical' : ''}${v.exposed ? ', internet-exposed' : ''}: ${nodeStatusLine(v)}`}
                onPointerDown={onPointerDown}
                onPointerEnter={() => setHover(v.name)}
                onPointerLeave={() => setHover(h => (h === v.name ? null : h))}
                onFocus={() => setHover(v.name)}
                onBlur={() => setHover(h => (h === v.name ? null : h))}
                onKeyDown={onKey(v.name)}
              >
                {isTarget && <rect key={`act-${t}`} x={-CW / 2 - 5} y={-CH / 2 - 5} width={CW + 10} height={CH + 10} rx="16" className="net-act" />}
                <rect x={-CW / 2} y={-CH / 2} width={CW} height={CH} rx="12" className="network-card" />
                <circle cx={-CW / 2} cy="0" r="2.6" className="net-port" /><circle cx={CW / 2} cy="0" r="2.6" className="net-port" />
                <text x={-CW / 2 + 14} y={-CH / 2 + 24} className="net-label">{v.info.label}</text>
                {v.critical && <text x={CW / 2 - 14} y={-CH / 2 + 23} textAnchor="end" className="net-crit">critical</text>}
                <circle cx={-CW / 2 + 18} cy={-1} r="3.6" className="net-dot" />
                <StatusText text={text} tone={tone} />
                <text x={-CW / 2 + 14} y={CH / 2 - 15} className="net-sub">Signal</text>
                <rect x={-CW / 2 + 60} y={CH / 2 - 21} width={barW} height="4" rx="2" className="net-bar-track" />
                <rect x={-CW / 2 + 60} y={CH / 2 - 21} width={Math.max(2, barW * Math.min(1, value))} height="4" rx="2" className="net-bar" data-band={band} />
                <text x={CW / 2 - 14} y={CH / 2 - 15} textAnchor="end" className="net-pct">{Math.round(value * 100)}%</text>
                {intruder && <g className="network-intruder"><Tag text={intruder} x={-CW / 2} className={v.isolated ? 'net-tag-contained' : 'net-tag-attacker'} /></g>}
                {did && <g className="network-defender" data-before={didBefore || undefined}><Tag text={did} x={-CW / 2} row={intruder ? 1 : 0} className="net-tag-defender" /></g>}
              </g>
            );
          })}
        </g>
      </svg>

      {hovered && tip && (
        <div className="net-tip" style={{ left: `${((tip.x * view.k + view.x) / W) * 100}%`, top: `${((tip.y * view.k + view.y) / H) * 100}%` }} aria-hidden="true">
          <p className="font-medium text-white">{hovered.info.label}</p>
          <p className="text-white/70">{nodeStatusLine(hovered)}</p>
        </div>
      )}
      </div>
      </div>

      <div className="net-tools" role="toolbar" aria-label="Map view">
        <button type="button" onClick={() => zoom(1.2)} aria-label="Zoom in">+</button>
        <button type="button" onClick={() => zoom(1 / 1.2)} aria-label="Zoom out">−</button>
        <button type="button" onClick={reset} aria-label="Reset view">Fit</button>
      </div>
      {showTruth && <p className="net-truth-chip" role="note">Showing hidden test-environment state — the defender cannot see this</p>}

      <table className="sr-only">
        <caption>Observed values per service at this step</caption>
        <thead><tr><th scope="col">Service</th><th scope="col">Anomaly score</th><th scope="col">Alert rate</th><th scope="col">Monitoring level</th><th scope="col">State</th></tr></thead>
        <tbody>
          {views.map(v => {
            const s = stateBy.get(v.name);
            return (
              <tr key={v.name}>
                <th scope="row">{v.name}</th>
                <td>{num(s?.anomaly_score ?? 0, 2)}</td>
                <td>{num(s?.alert_rate ?? 0, 2)}</td>
                <td>{s?.monitoring_level ?? 0}</td>
                <td>{nodeStatusLine(v).toLowerCase()}{v.isolated ? ' (isolated)' : ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
