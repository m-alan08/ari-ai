import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BRAND_NAME, LOGO_ASPECT, LOGO_SRC, LogoMark, Wordmark } from '@/components/brand';
import { Reveal } from '@/components/reveal';
import { Button, RollText } from '@/components/ui';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('ARI brand and interactive UI', () => {
  it('keeps one accessible name with no duplicated button label', () => {
    render(<Button>Development sign-in</Button>);
    const btn = screen.getByRole('button', { name: 'Development sign-in' });
    expect(btn.querySelectorAll('[aria-hidden="true"]').length).toBe(0);
  });

  it('names a rolling link once and hides the visual copy from assistive tech', () => {
    render(<a href="#how" className="group"><RollText>Go to projects</RollText></a>);
    const link = screen.getByRole('link', { name: 'Go to projects' });
    expect(link.querySelectorAll('[aria-hidden="true"]')).toHaveLength(1);
    expect(screen.getAllByText('Go to projects')).toHaveLength(2);
    expect(screen.getAllByRole('link')).toHaveLength(1);
  });

  it('renders non-string button children untouched', () => {
    render(<Button><span data-testid="icon">+</span> Add</Button>);
    expect(screen.getByTestId('icon')).toBeTruthy();
  });

  it('shows the product name once beside a decorative bare ant', () => {
    render(<Wordmark />);
    expect(BRAND_NAME).toBe('ARI');
    expect(screen.queryByRole('img')).toBeNull();
    const mark = screen.getByTestId('ari-logo');
    expect(mark.getAttribute('aria-hidden')).toBe('true');
    expect(mark.style.height).toBe('22px');
    expect(mark.className).toContain('ari-mark');
    expect(screen.getByText('ARI')).toBeTruthy();
  });

  it('draws just the ant (no tile) at the requested height and natural width', () => {
    render(<LogoMark size={40} />);
    expect(screen.getByRole('img', { name: `${BRAND_NAME} logo` })).toBeTruthy();
    const logo = screen.getByTestId('ari-logo');
    expect(logo.style.height).toBe('40px');
    expect(logo.style.width).toBe(`${Math.round(40 * LOGO_ASPECT)}px`);
    expect(logo.getAttribute('data-src')).toBe(LOGO_SRC);
    expect(logo.className).not.toMatch(/rounded|bg-/);
    expect(logo.querySelector('img')).toBeNull();
  });

  it('reveal content is visible by default (no JS / no IntersectionObserver)', () => {
    const { container } = render(<Reveal><p>visible</p></Reveal>);
    expect(container.firstElementChild?.getAttribute('data-shown')).toBe('true');
    expect(screen.getByText('visible')).toBeTruthy();
  });

  it('keeps below-fold reveal content visible when reduced motion is requested', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    vi.stubGlobal('IntersectionObserver', class {
      observe() {}
      disconnect() {}
    });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 1000, 100, 40));
    const scrollingElement = document.scrollingElement;
    if (scrollingElement) vi.spyOn(scrollingElement, 'scrollHeight', 'get').mockReturnValue(2000);

    const { container } = render(<Reveal><p>visible with reduced motion</p></Reveal>);
    expect(container.firstElementChild?.getAttribute('data-shown')).toBe('true');
    expect(screen.getByText('visible with reduced motion')).toBeTruthy();
  });
});
