'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AntColony } from '@/components/ant-colony';

/**
 * Full-screen interactive hero band. Pointer position and scroll progress are written as CSS
 * variables (--mx/--my on the band and --scroll) once per
 * frame; all visuals live in globals.css (`.hero-stage`). While mounted it also drives the
 * fixed home header through <html data-home-hero>. Scroll attributes remain available
 * to page styling; navigation itself stays visible with independent contrast surfaces.
 * `play` starts the entrance (after the intro loader lifts). Without JS, or with reduced
 * motion, the band renders as a static dark hero with fully visible text. `colony` adds the
 * landing page's ant-colony canvas behind the content.
 */
/** The seven steps of an assessment, shown as chips on the hero and sign-in screens. */
export const ASSESSMENT_LOOP = ['Understand', 'Review', 'Attack Safely', 'Diagnose', 'Repair', 'Verify', 'Monitor'];

export function HeroStage({ children, className = '', labelledBy, play = true, colony = false }: { children: ReactNode; className?: string; labelledBy: string; play?: boolean; colony?: boolean }) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const root = document.documentElement;
    root.dataset.homeHero = '';
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let frame = 0;
    let pointer: { x: number; y: number } | null = null;
    let lastScroll = '';
    const paint = () => {
      frame = 0;
      const box = el.getBoundingClientRect();
      const height = Math.max(1, box.height);
      const y = window.scrollY;
      toggle(root, 'heroScrolled', y > 24);
      toggle(root, 'heroPast', y > height - 64);
      // Once the page has fully covered the pinned hero, stop painting it (orbs, glow, text).
      toggle(el.dataset, 'covered', y > height * 1.05);
      if (reduced) return;
      if (pointer && y < height) {
        el.style.setProperty('--mx', `${pointer.x - box.left}px`);
        el.style.setProperty('--my', `${pointer.y - box.top}px`);
      }
      const scroll = Math.min(1, Math.max(0, y / height)).toFixed(3);
      if (scroll !== lastScroll) { lastScroll = scroll; el.style.setProperty('--scroll', scroll); }
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(paint); };
    const onMove = (e: PointerEvent) => { pointer = { x: e.clientX, y: e.clientY }; el.dataset.pointer = 'on'; schedule(); };
    const onLeave = () => { el.dataset.pointer = 'off'; };
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerleave', onLeave);
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      el.removeEventListener('pointermove', onMove);
      el.removeEventListener('pointerleave', onLeave);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      delete root.dataset.homeHero;
      delete root.dataset.heroScrolled;
      delete root.dataset.heroPast;
    };
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (el && play && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) el.dataset.fx = 'on';
  }, [play]);

  return (
    <section ref={ref} className={`hero-ink hero-stage ${className}`} aria-labelledby={labelledBy}>
      <div aria-hidden="true" className="hero-orbs">
        <span className="hero-orb hero-orb-a" />
        <span className="hero-orb hero-orb-b" />
        <span className="hero-orb hero-orb-c" />
        <span className="hero-orb hero-orb-d" />
        <span className="hero-orb hero-orb-e" />
        <span className="hero-glow" />
      </div>
      {colony && <AntColony play={play} />}
      <div className="hero-content">{children}</div>
    </section>
  );
}

/** Sets or clears a data attribute, writing only on change: an attribute write on <html>
 *  restyles the whole document, so doing it every scroll frame costs a full style pass. */
function toggle(target: HTMLElement | DOMStringMap, key: string, on: boolean) {
  const data = target instanceof HTMLElement ? target.dataset : target;
  if (on === key in data) return;
  if (on) data[key] = '';
  else delete data[key];
}

/** Highlights each loop step in turn, like a pipeline running. Static under reduced motion. */
export function useLoopCursor(length: number, ms = 1400): number {
  const [active, setActive] = useState(-1);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let i = -1;
    const id = window.setInterval(() => { i = (i + 1) % length; setActive(i); }, ms);
    return () => window.clearInterval(id);
  }, [length, ms]);
  return active;
}
