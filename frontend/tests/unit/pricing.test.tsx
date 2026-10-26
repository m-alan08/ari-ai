import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PricingPage from '@/app/pricing/page';
import { perAssessment, PLANS } from '@/lib/pricing';

afterEach(cleanup);

it('shows the four plans, the FAQ and the preview-pricing note', () => {
  render(<PricingPage />);
  expect(screen.getByRole('heading', { level: 1, name: 'ARI pricing' })).toBeTruthy();
  for (const name of ['Founder', 'Growth', 'Scale', 'Enterprise']) expect(screen.getByRole('heading', { name })).toBeTruthy();
  for (const price of ['$49.99', '$129.99', '$359.99', 'Contact us']) expect(screen.getByText(price)).toBeTruthy();
  expect(screen.getByTestId('plan-growth').textContent).toContain('5 million');
  expect(screen.getByText('Recommended')).toBeTruthy();
  expect(screen.getByText('Does ARI ever change our code on its own?')).toBeTruthy();
  expect(screen.getByTestId('pricing-preview').textContent).toContain('nothing can be purchased');
  expect(screen.getByTestId('pricing-proposal').textContent).toContain('proposals');
  const cta = screen.getByRole('heading', { name: 'See what your code really does before an attacker does.' }).parentElement;
  expect(cta?.classList.contains('pricing-cta')).toBe(true);
  expect(cta?.classList.contains('hero-ink')).toBe(false);
  expect(screen.queryByText(/free trial/i)).toBeNull();
  expect(screen.queryByText(/SSO/)).toBeNull();
});

it('matches the owner\'s price table, and the per-assessment price is computed from it', () => {
  const row = (id: string) => PLANS.find(p => p.id === id)!;
  expect(PLANS.map(p => [p.name, p.monthlyUsd, p.assessments, p.sourceLines])).toEqual([
    ['Founder', 49.99, 7, '1 million'],
    ['Growth', 129.99, 20, '5 million'],
    ['Scale', 359.99, 60, '20 million'],
    ['Enterprise', null, null, 'Custom'],
  ]);
  expect(['founder', 'growth', 'scale', 'enterprise'].map(id => perAssessment(row(id)))).toEqual(['$7.14', '$6.50', '$6.00', 'Custom']);
});
