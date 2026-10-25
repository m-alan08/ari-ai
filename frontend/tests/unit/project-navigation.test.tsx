import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ProjectNavigation } from '@/components/project';
const route = vi.hoisted(() => ({ path: '/projects/p1' }));
vi.mock('next/navigation', () => ({ usePathname: () => route.path, useParams: () => ({}) }));
afterEach(cleanup);
it('leads with the workflows and retains secondary routes behind More', () => {
  render(<ProjectNavigation base="/projects/p1" />);
  const nav = screen.getByRole('navigation', { name: 'Project' });
  expect(within(nav.querySelector('.project-tabs') as HTMLElement).getAllByRole('link').map(a => a.textContent)).toEqual(['Onboarding', 'Code review', '7 agents']);
  expect(screen.getByRole('link', { name: '7 agents' }).getAttribute('aria-current')).toBe('page');
  expect(screen.getByRole('link', { name: 'Onboarding' }).getAttribute('href')).toBe('/projects/p1/onboarding');
  fireEvent.click(screen.getByText('More'));
  expect(screen.getByRole('link', { name: 'Live Defense' }).getAttribute('href')).toBe('/projects/p1/live-defense');
  expect(screen.getByRole('link', { name: 'Authorized scopes' }).getAttribute('href')).toBe('/projects/p1/scopes');
  expect(screen.getByRole('link', { name: 'Approvals' }).getAttribute('href')).toBe('/projects/p1/approvals');
  fireEvent.keyDown(screen.getByText('More'), { key: 'Escape' });
  expect(screen.getByText('More').closest('details')!.open).toBe(false);
});
it('keeps the seven-agent workflow selected on replay pages', () => {
  route.path = '/projects/p1/assessments/a1';
  render(<ProjectNavigation base="/projects/p1" />);
  expect(screen.getByRole('link', { name: '7 agents' }).getAttribute('aria-current')).toBe('page');
});

it('announces the current secondary page while More is collapsed', () => {
  route.path = '/projects/p1/scopes';
  render(<ProjectNavigation base="/projects/p1" />);
  expect(screen.getByText('More').getAttribute('aria-label')).toBe('More: Authorized scopes');
  expect(screen.getByText('More').getAttribute('aria-current')).toBe('page');
});
