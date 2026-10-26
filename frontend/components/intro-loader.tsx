'use client';

import { useEffect, useState } from 'react';
import { BRAND_NAME, LogoMark } from '@/components/brand';

/** Session key: the intro plays once per sign-in (cleared on sign-out, see session.tsx). */
export const INTRO_KEY = 'ari-intro-played';

const DURATION_MS = 1800;
const LIFT_MS = 800;

function shouldPlay(): boolean {
  if (typeof window === 'undefined') return false;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
  if (navigator.webdriver) return false; // automated browsers (e2e) skip straight to the page
  try { return window.sessionStorage.getItem(INTRO_KEY) === null; } catch { return false; }
}

/**
 * Full-screen intro shown on the home hero right after sign-in: the supplied mark fades in while a
 * percentage counts up, then the curtain lifts and `onReveal` starts the hero entrance. It never
 * intercepts input (pointer-events: none), and is skipped under reduced motion or automation.
 */
export function IntroLoader({ onReveal }: { onReveal: () => void }) {
  const [pct, setPct] = useState(0);
  // Server and first client render both start 'gone' (no hydration mismatch); playing is decided after mount.
  const [phase, setPhase] = useState<'count' | 'lift' | 'gone'>('gone');

  useEffect(() => {
    if (!shouldPlay()) { onReveal(); return; }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- browser-only decision (storage, media query) must happen after hydration
    setPhase('count');
    const root = document.documentElement;
    root.dataset.intro = 'playing';
    let frame = 0;
    let liftTimer = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / DURATION_MS);
      setPct(Math.round((1 - Math.pow(1 - t, 3)) * 100));
      if (t < 1) { frame = requestAnimationFrame(tick); return; }
      try { window.sessionStorage.setItem(INTRO_KEY, '1'); } catch { /* storage unavailable: replay is harmless */ }
      setPhase('lift');
      delete root.dataset.intro;
      onReveal();
      liftTimer = window.setTimeout(() => setPhase('gone'), LIFT_MS);
    };
    frame = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(liftTimer); delete root.dataset.intro; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once per mount by design
  }, []);

  if (phase === 'gone') return null;
  return (
    <div aria-hidden="true" className="intro" data-phase={phase}>
      <LogoMark size={96} decorative className="intro-mark" />
      <div className="intro-progress">
        <span className="intro-pct">{pct}%</span>
        <span className="intro-line" style={{ transform: `scaleX(${pct / 100})` }} />
      </div>
      <span className="sr-only">{BRAND_NAME}</span>
    </div>
  );
}
