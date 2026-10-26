import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { INTRO_KEY, IntroLoader } from '@/components/intro-loader';

beforeEach(() => {
  window.sessionStorage.clear();
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('uses the supplied ant (bare mark) in the intro without drawing a different logo', () => {
  const { container } = render(<IntroLoader onReveal={vi.fn()} />);
  expect(screen.getByTestId('ari-logo').style.height).toBe('96px');
  expect(container.querySelector('svg')).toBeNull();
  expect(container.querySelector('.intro')?.getAttribute('aria-hidden')).toBe('true');
});

it('skips the intro under reduced motion', () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }));
  const onReveal = vi.fn();
  render(<IntroLoader onReveal={onReveal} />);
  expect(screen.queryByTestId('ari-logo')).toBeNull();
  expect(onReveal).toHaveBeenCalledOnce();
});

it('keeps the once-per-session skip', () => {
  window.sessionStorage.setItem(INTRO_KEY, '1');
  const onReveal = vi.fn();
  render(<IntroLoader onReveal={onReveal} />);
  expect(screen.queryByTestId('ari-logo')).toBeNull();
  expect(onReveal).toHaveBeenCalledOnce();
});
