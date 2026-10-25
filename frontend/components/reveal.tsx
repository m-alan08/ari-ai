'use client';

import { useEffect, useRef, type ReactNode, type RefObject } from 'react';

/**
 * Rise-and-unblur the first time an element scrolls into view (globals.css `[data-reveal]`).
 * Only elements that mount below the fold are hidden, so the first screen never flashes and
 * server render / no-JS / reduced motion keep everything visible. Elements side by side get a
 * small left-to-right stagger.
 */
export function useRevealOnScroll<T extends HTMLElement>(): RefObject<T | null> {
  const ref = useRef<T>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const box = el.getBoundingClientRect();
    if (box.top < window.innerHeight) return; // already on screen: no entrance
    // Same end-of-document guard as Reveal: never hide what can never enter view.
    const maxScroll = Math.max(0, (document.scrollingElement?.scrollHeight ?? 0) - window.innerHeight);
    if (box.top + window.scrollY - maxScroll > window.innerHeight * 0.9) return;
    el.dataset.reveal = 'hidden';
    const io = new IntersectionObserver(entries => {
      if (!entries.some(e => e.isIntersecting)) return;
      io.disconnect();
      el.style.transitionDelay = `${Math.round(Math.min(1, Math.max(0, el.getBoundingClientRect().left / window.innerWidth)) * 160)}ms`;
      el.dataset.reveal = 'shown';
      // The stagger is for the entrance only; later hover transitions start at once.
      el.addEventListener('transitionend', () => { el.style.transitionDelay = ''; }, { once: true });
    }, { rootMargin: '0px 0px -6% 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return ref;
}

/**
 * Fade-and-rise when scrolled into view. Server render and no-JS keep content visible;
 * only elements that start below the fold are hidden, then revealed once. Honours
 * prefers-reduced-motion through CSS (globals.css `.reveal`).
 */
export function Reveal({ children, delay = 0, className = '' }: { children: ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const rect = el.getBoundingClientRect();
    if (rect.top < window.innerHeight) return; // already visible
    // An element at the document's end can never rise above the shrunken bottom margin:
    // hiding it would keep it invisible forever.
    const maxScroll = Math.max(0, (document.scrollingElement?.scrollHeight ?? 0) - window.innerHeight);
    if (rect.top + window.scrollY - maxScroll > window.innerHeight * 0.9) return;
    el.dataset.shown = 'false';
    const io = new IntersectionObserver(
      entries => {
        if (entries.some(e => e.isIntersecting)) {
          el.dataset.shown = 'true';
          io.disconnect();
        }
      },
      { rootMargin: '0px 0px -8% 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div ref={ref} data-shown="true" className={`reveal ${className}`} style={delay ? { transitionDelay: `${delay}ms` } : undefined}>
      {children}
    </div>
  );
}
