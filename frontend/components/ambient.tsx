'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useRef } from 'react';

/**
 * The landing hero's light, carried to every other page: drifting ember orbs, a faint grid and
 * grain on a fixed layer behind the content (styles in globals.css, `.ambient`; hidden on the
 * landing itself). Once per frame it writes the page's scroll progress as --sy for a slow
 * parallax, and the pointer position (--gx/--gy) on the panel under the cursor for its glow.
 * Purely decorative: aria-hidden, no pointer events, static under reduced motion.
 */
export function Ambient() {
  const ref = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let frame = 0;
    let lastScroll = '';
    let pointer: { x: number; y: number; target: Element | null } | null = null;
    const paint = () => {
      frame = 0;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const sy = (max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0).toFixed(3);
      if (sy !== lastScroll) { lastScroll = sy; el.style.setProperty('--sy', sy); }
      const panel = pointer?.target?.closest<HTMLElement>('.panel');
      if (pointer && panel) {
        const box = panel.getBoundingClientRect();
        panel.style.setProperty('--gx', `${pointer.x - box.left}px`);
        panel.style.setProperty('--gy', `${pointer.y - box.top}px`);
      }
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(paint); };
    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse') return;
      pointer = { x: e.clientX, y: e.clientY, target: e.target instanceof Element ? e.target : null };
      schedule();
    };
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    window.addEventListener('pointermove', onMove, { passive: true });
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      window.removeEventListener('pointermove', onMove);
    };
  }, [pathname]);

  // The landing keeps its own hero exactly as designed: nothing of this layer is rendered there.
  if (pathname === '/') return null;
  return (
    <div ref={ref} aria-hidden="true" className="ambient">
      <span className="ambient-orb ambient-orb-a" />
      <span className="ambient-orb ambient-orb-b" />
      <span className="ambient-orb ambient-orb-c" />
      <span className="ambient-grid" />
      <span className="ambient-grain" />
    </div>
  );
}
