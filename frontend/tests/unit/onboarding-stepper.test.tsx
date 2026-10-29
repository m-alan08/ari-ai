import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { SetupSteps } from '@/components/onboarding/stepper';

it('renders the four-step first-customer journey with done/current states and aria-current', () => {
  render(<SetupSteps current={3} org="acme" doneSteps={[1, 2]} onStepChange={() => {}} />);
  const items = screen.getAllByRole('listitem');
  expect(items.map(item => item.textContent?.trim())).toEqual([
    '01Connect GitHubDONE',
    '02Choose repositoryDONE',
    '03Set up the repository',
    '04Run the first review',
  ]);
  expect(items.map(item => item.getAttribute('data-step-state'))).toEqual([
    'done', 'done', 'current', 'upcoming',
  ]);
  expect(screen.getByRole('button', { name: /Set up the repository/ }).getAttribute('aria-current')).toBe('step');
  expect(screen.getByText('Step 3 of 4 · Set up the repository')).toBeTruthy();
  expect(screen.getAllByText('DONE')).toHaveLength(2);
  expect(items[0]?.querySelector('a')?.className).toContain('border-line-3');
  expect(items[2]?.querySelector('button')?.className).toContain('border-brand');
});
