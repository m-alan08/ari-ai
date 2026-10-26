import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProblemScene, ProofStats, Walkthrough } from '@/components/story';

beforeEach(() => {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  }));
});

describe('story a11y in non-sticky mode', () => {
  it('shows all problem beats without aria-hidden', () => {
    const { container } = render(<ProblemScene />);
    const items = container.querySelectorAll('.story-swap-item');
    expect(items).toHaveLength(3);
    items.forEach(i => expect(i.hasAttribute('aria-hidden')).toBe(false));
  });
  it('renders key walkthrough labels as real text', () => {
    render(<Walkthrough />);
    expect(screen.getByText('root cause · create_refund', { selector: 'span.block' })).toBeTruthy();
    expect(screen.getByText('verified · replay 2 / 2', { selector: 'span.block' })).toBeTruthy();
  });
  it('shows Never rather than a zero counter', () => {
    render(<ProofStats />);
    expect(screen.getByText('Never')).toBeTruthy();
    expect(screen.queryByText('0')).toBeNull();
  });
});
