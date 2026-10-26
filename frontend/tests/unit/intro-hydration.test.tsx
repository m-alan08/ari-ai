import { cleanup, render } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { IntroLoader } from '@/components/intro-loader';

beforeEach(() => {
  window.sessionStorage.clear();
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('renders nothing on the first (server-equivalent) render even on a first visit', () => {
  expect(renderToString(<IntroLoader onReveal={vi.fn()} />)).toBe('');
});

it('skips for automated browsers (navigator.webdriver) and reveals immediately', () => {
  vi.stubGlobal('navigator', { ...navigator, webdriver: true });
  const onReveal = vi.fn();
  const { container } = render(<IntroLoader onReveal={onReveal} />);
  expect(container.querySelector('.intro')).toBeNull();
  expect(onReveal).toHaveBeenCalledOnce();
});
