import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ChecksChart } from '@/components/checks-chart';

afterEach(cleanup);

it('renders thirty responsive columns with a summary label and percentage-sized segments', () => {
  const days = Array.from({ length: 30 }, (_, index) => ({
    date: `2026-10-${String(index + 1).padStart(2, '0')}`,
    completed: index === 29 ? 2 : 0,
    active: index === 29 ? 1 : 0,
    failed: index === 29 ? 1 : 0,
  }));
  render(<ChecksChart days={days} />);

  expect(screen.getAllByTestId('check-day-column')).toHaveLength(30);
  expect(screen.getByRole('img').getAttribute('aria-label')).toBe('30-day check activity: 2 completed, 1 active, 1 failed.');
  expect(screen.getByTestId('check-segment-completed').getAttribute('style')).toContain('height: 50%');
  expect(screen.getByTestId('check-segment-active').getAttribute('style')).toContain('height: 25%');
  expect(screen.getByTestId('check-segment-failed').getAttribute('style')).toContain('height: 25%');
});
