'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Reveal } from '@/components/reveal';
import { BRAND_NAME } from '@/components/brand';

/*
 * Scroll-told landing chapters (pattern study of kairo.security, own implementation).
 * Each chapter is a tall "track" whose inner stage is sticky; scroll progress through the
 * track (0..1) drives the scene. Under reduced motion, or below the lg breakpoint, tracks
 * collapse to normal flow and scenes render their final, fully readable state.
 */

export function useTrack<T extends HTMLElement>(): [React.RefObject<T | null>, number, boolean] {
  const ref = useRef<T>(null);
  const [p, setP] = useState(1);
  const [sticky, setSticky] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const mq = window.matchMedia('(min-width: 1024px) and (prefers-reduced-motion: no-preference)');
    let frame = 0;
    // Only a chapter on screen measures itself and re-renders; the others sit still.
    let visible = true;
    const update = () => {
      frame = 0;
      setSticky(mq.matches);
      if (!mq.matches) { setP(1); return; }
      const r = el.getBoundingClientRect();
      const total = r.height - window.innerHeight;
      const v = total <= 0 ? 1 : Math.min(1, Math.max(0, -r.top / total));
      el.style.setProperty('--p', v.toFixed(4));
      setP(Math.round(v * 200) / 200);
    };
    const schedule = () => { if (visible && !frame) frame = requestAnimationFrame(update); };
    const io = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? true;
      // One last measurement on the way out, so the scene settles at its start or end state.
      if (!frame) frame = requestAnimationFrame(update);
    });
    io?.observe(el);
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    mq.addEventListener('change', schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      io?.disconnect();
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      mq.removeEventListener('change', schedule);
    };
  }, []);
  return [ref, p, sticky];
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/* ---------- 1. The problem ---------- */

const TICKS = 12;
const TICK_X = [4, 11, 17, 26, 31, 39, 47, 55, 62, 70, 79, 88];
const SNAPSHOT_CAUGHT = new Set([1, 6, 7]); // a PR review and a periodic scan each see a moment
const PROBLEM_LINES = [
  { title: 'Code changes every day.', body: 'Developers and AI agents ship new behaviour with every merge, and each one can change what the software does.' },
  { title: 'Reviews see a diff. Scans see a snapshot.', body: 'A reviewer reads the lines that changed. A scanner checks a moment in time. Intent and real exploitability go unchecked.' },
  { title: `${BRAND_NAME} checks every change.`, body: 'Every change is tested against what the software is supposed to do and probed like an attacker would, then re-checked as code moves.' },
];

export function ProblemScene() {
  const [ref, p, sticky] = useTrack<HTMLDivElement>();
  const phase = p < 0.34 ? 0 : p < 0.67 ? 1 : 2;
  const shown = Math.max(1, Math.ceil(clamp01(p / 0.3) * TICKS));
  const lens = clamp01((p - 0.7) / 0.25);
  const caught = (n: number) => (phase >= 1 && SNAPSHOT_CAUGHT.has(n)) || (phase === 2 && (TICK_X[n] ?? 100) <= lens * 92);
  const count = TICK_X.filter((_, n) => caught(n)).length;

  return (
    <div ref={ref} className="story-track story-track-3 bleed bleed-paper">
      <section aria-labelledby="problem-title" className="story-stage flex flex-col justify-center py-16">
        <p className="kicker">The problem</p>
        <h2 id="problem-title" className="sr-only">Why every change needs checking</h2>
        <div className="story-swap mt-4 min-h-[9.5rem] max-w-3xl md:min-h-[8.5rem]">
          {PROBLEM_LINES.map((line, n) => (
            <div key={line.title} className="story-swap-item" data-on={phase === n || undefined} aria-hidden={sticky ? phase !== n : undefined}>
              <p className="display text-3xl font-semibold md:text-5xl">{line.title}</p>
              <p className="mt-3 max-w-xl text-sm text-muted md:text-base">{line.body}</p>
            </div>
          ))}
        </div>

        <p className="label-mono mt-10 text-faint">Illustration · not measured</p>
        <div className="relative mt-10" aria-hidden="true">
          <span className="label-mono absolute -top-8 left-0 rounded-md bg-ink px-2 py-1 text-white" data-testid="problem-count">
            {count} of {TICKS} changes checked
          </span>
          <span className="label-mono absolute -top-8 right-0 text-faint">Today</span>
          <div className="story-timeline">
            {TICK_X.map((x, n) => (
              <span key={x} className="story-tick" data-shown={n < shown || undefined} data-caught={caught(n) || undefined} style={{ left: `${x}%` }} />
            ))}
            <span className="story-pill" data-on={phase === 1 || undefined} style={{ left: '7%' }}>PR review</span>
            <span className="story-pill" data-on={phase === 1 || undefined} style={{ left: '43%' }}>Scanner</span>
            <span className="story-lens" style={{ width: `${Math.max(phase === 2 ? 8 : 0, lens * 96)}%`, opacity: phase === 2 ? 1 : 0 }}>
              <span className="label-mono truncate">{BRAND_NAME} · every change, against intent</span>
            </span>
          </div>
          <div className="label-mono mt-3 flex justify-between text-faint">
            <span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span>
          </div>
        </div>
      </section>
    </div>
  );
}

/* ---------- 2. How it works ---------- */

const HOW = [
  {
    label: 'Understand intent', title: 'Learn what it should do',
    body: 'Code intelligence indexes the repository and extracts expectations from specs, docs, tests and history.',
    file: 'expectations',
    lines: [['ok', 'refund ≤ captured amount', 'confirmed · docstring'], ['ok', 'discounts round half-up', 'confirmed · test'], ['q', 'owner check on /refunds', 'inferred']],
  },
  {
    label: 'Review and attack', title: 'Test it like an attacker',
    body: 'The Reviewer writes tests that reproduce defects, and seven security agents probe inside an approved sandbox scope.',
    file: 'sandbox · pytest',
    lines: [['run', 'generated/test_refund_bound.py', ''], ['bad', 'test_refund_exceeds_capture', 'AssertionError'], ['bad', 'reproduced on head', '2 / 2']],
  },
  {
    label: 'Repair and verify', title: 'Fix it, then prove it',
    body: 'After a human approves, Patch proposes the smallest fix. Verify replays independent tests on a fresh checkout.',
    file: 'payments/refunds.py',
    lines: [['del', 'if amount > 0:', ''], ['add', 'if 0 < amount <= remaining:', ''], ['ok', 'fresh checkout · replay', 'passed']],
  },
  {
    label: 'Monitor and retest', title: 'Keep it secure',
    body: 'Signed GitHub events re-run the assessment as code changes, and every finding feeds the next test.',
    file: 'events',
    lines: [['ok', 'push · main · signature verified', ''], ['run', 're-assessment queued', ''], ['q', 'policy advice (test-environment-trained)', 'not auto-applied']],
  },
] as const;

const MARK: Record<string, string> = { ok: '✓', q: '?', bad: '✕', run: '›', del: '−', add: '+' };

export function HowItWorks() {
  const [ref, p] = useTrack<HTMLDivElement>();
  const active = Math.min(HOW.length - 1, Math.floor(p * HOW.length * 0.999));

  return (
    <div ref={ref} className="story-track story-track-4 bleed bleed-ink text-white" id="how">
      <section aria-labelledby="how-title" className="story-stage story-rain flex flex-col justify-center py-16">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.2fr_1fr] lg:items-end">
          <div>
            <p className="kicker kicker-ghost">How it works</p>
            <h2 id="how-title" className="display mt-3 text-3xl font-semibold md:text-5xl">Assurance that runs<br /><span className="text-white/60">on every change.</span></h2>
          </div>
          <p className="max-w-md text-sm text-white/70">
            {BRAND_NAME} learns how the software is meant to behave, tests that behaviour safely, repairs only with approval,
            and keeps checking as the code moves.
          </p>
        </div>
        <ol className="story-cards mt-10">
          {HOW.map((card, n) => (
            <li key={card.label} className="story-card" data-active={active === n || undefined}>
              <span className="story-card-num" aria-hidden="true">{String(n + 1).padStart(2, '0')}</span>
              <div className="story-card-body">
                <p className="label-mono mb-2 text-white/60">Illustration · not a recorded run</p>
                <div className="story-window" aria-hidden="true">
                  <p className="story-window-bar"><span>{card.file}</span><span>{BRAND_NAME}</span></p>
                  {card.lines.map(([kind, text, note]) => (
                    <p key={text} className="story-window-line" data-kind={kind}>
                      <span className="w-4 shrink-0">{MARK[kind]}</span><span className="flex-1 truncate">{text}</span>{note && <span className="text-faint">{note}</span>}
                    </p>
                  ))}
                </div>
                <h3 className="mt-5 text-xl font-semibold md:text-2xl">{card.title}</h3>
                <p className="mt-2 max-w-md text-sm text-white/70">{card.body}</p>
              </div>
              <span className="story-card-label label-mono">{card.label}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

/* ---------- 3. One finding, start to proof ---------- */

const WALK = [
  { title: 'Map the surface', agent: 'Scout', body: 'Scout maps endpoints, data flows and trust boundaries inside the approved scope.' },
  { title: 'Chain the evidence', agent: 'Probe · Trace', body: 'Probe exercises each harmless-looking step; Trace links them into one exploitable path.' },
  { title: 'Find the root cause', agent: 'Diagnose', body: 'Diagnose pins the defect to source spans and explains why it happens.' },
  { title: 'Fix and prove it', agent: 'Patch · Verify', body: 'Patch proposes a fix after approval; Verify replays the exploit and the suite on a fresh checkout.' },
];
const ROWS = [
  { y: 40, label: 'api' },
  { y: 100, label: 'payments' },
  { y: 160, label: 'ledger' },
];
const NODES = [
  { x: 70, y: 40 }, { x: 150, y: 100 }, { x: 235, y: 40 }, { x: 300, y: 160 },
];

export function Walkthrough() {
  const [ref, p] = useTrack<HTMLDivElement>();
  const step = Math.min(WALK.length - 1, Math.floor(p * WALK.length * 0.999));
  const path = NODES.map(n => `${n.x},${n.y}`).join(' ');

  return (
    <div ref={ref} className="story-track story-track-4">
      <section aria-labelledby="walk-title" className="story-stage grid content-center gap-10 py-16 lg:grid-cols-[1fr_1.3fr] lg:items-center">
        <div>
          <p className="kicker">One finding, start to proof</p>
          <h2 id="walk-title" className="display mt-3 text-3xl font-semibold md:text-4xl">Four harmless steps.<br /><span className="text-muted">One verified fix.</span></h2>
          <ol className="mt-8 space-y-1">
            {WALK.map((s, n) => (
              <li key={s.title} className="story-step" data-active={step === n || undefined} data-done={n < step || undefined}>
                <p className="flex items-baseline justify-between gap-3">
                  <span className="text-lg font-semibold">{s.title}</span>
                  <span className="label-mono text-faint">{s.agent}</span>
                </p>
                <p className="story-step-body text-sm text-muted">{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
        <div>
        <p className="label-mono mb-2 text-faint">Illustration · not a recorded run</p>
        <div className="story-panel" data-step={step} aria-hidden="true">
          <svg viewBox="0 0 360 200" className="h-auto w-full">
            {ROWS.map(r => (
              <g key={r.label}>
                <text x="8" y={r.y - 14} className="story-svg-label">{r.label}</text>
                {Array.from({ length: 34 }, (_, k) => (
                  <rect key={k} x={8 + k * 10.3} y={r.y - 8} width="2" height="16" rx="1" className="story-svg-bar" style={{ opacity: 0.25 + ((k * 7 + r.y) % 5) * 0.08 }} />
                ))}
              </g>
            ))}
            <polyline points={path} className="story-svg-path" pathLength={1} />
            {NODES.map((n, k) => (
              <circle key={k} cx={n.x} cy={n.y} r="5" className="story-svg-node" data-root={k === 3 || undefined} />
            ))}
            <text x="300" y="186" textAnchor="middle" className="story-svg-root">root cause · create_refund</text>
            <text x="300" y="186" textAnchor="middle" className="story-svg-ok">verified · replay 2 / 2</text>
          </svg>
        </div>
        <p className="label-mono mt-2 text-xs text-muted lg:hidden" data-testid="walk-labels">
          {step >= 2 && <span className="block">root cause · create_refund</span>}
          {step === 3 && <span className="block">verified · replay 2 / 2</span>}
        </p>
        </div>
      </section>
    </div>
  );
}

/* ---------- 4. The record is checkable ---------- */

function CountUp({ to, suffix = '' }: { to: number; suffix?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [v, setV] = useState(to);
  useEffect(() => {
    const el = ref.current;
    if (!el || to === 0 || window.matchMedia('(prefers-reduced-motion: reduce)').matches || typeof IntersectionObserver === 'undefined') return;
    let frame = 0;
    const io = new IntersectionObserver(entries => {
      if (!entries.some(e => e.isIntersecting)) return;
      io.disconnect();
      const start = performance.now();
      const tick = (now: number) => {
        const t = Math.min(1, (now - start) / 1200);
        setV(Math.round((1 - Math.pow(1 - t, 3)) * to));
        if (t < 1) frame = requestAnimationFrame(tick);
      };
      setV(0);
      frame = requestAnimationFrame(tick);
    }, { threshold: 0.6 });
    io.observe(el);
    return () => { io.disconnect(); cancelAnimationFrame(frame); };
  }, [to]);
  return <span ref={ref}>{v}{suffix}</span>;
}

const STATS: Array<{ n: number; suffix?: string; tag: string; body: string }> = [
  { n: 7, tag: 'Design', body: 'Separate security agents, each its own tool loop with enforced permissions.' },
  { n: 2, suffix: '×', tag: 'Rule', body: 'A failing test only counts if it fails the same way twice.' },
  { n: 0, tag: 'Rule', body: 'Remediation approval stays with a different human.' },
];

export function ProofStats(): ReactNode {
  return (
    <section aria-labelledby="proof-title" className="bleed bleed-ink story-rain py-16 text-white">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.2fr_1fr] lg:items-end">
        <div>
          <p className="kicker kicker-ghost">Proof, not promises</p>
          <h2 id="proof-title" className="display mt-3 text-3xl font-semibold md:text-5xl">The record is checkable.</h2>
        </div>
        <p className="max-w-md text-sm text-white/70">No model output counts as proof, including ours. In the console, every status comes from saved records.</p>
      </div>
      <div className="mt-10 grid grid-cols-1 gap-3 md:grid-cols-3">
        {STATS.map((s, n) => (
          <Reveal key={s.body} className="story-stat" delay={n * 90}>
            <p className="label-mono flex justify-between text-white/50"><span>{String(n + 1).padStart(2, '0')}</span><span>{s.tag}</span></p>
            <p className="stat-num mt-8 font-mono font-semibold tracking-tight">{s.n === 0 ? 'Never' : <CountUp to={s.n} suffix={s.suffix} />}</p>
            <p className="mt-3 text-sm text-white/70">{s.body}</p>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
