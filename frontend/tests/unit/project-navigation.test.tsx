import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ProjectNavigation } from '@/components/project';
const route = vi.hoisted(() => ({ path: '/projects/p1' }));
vi.mock('next/navigation', () => ({ usePathname: () => route.path, useParams: () => ({}) }));
afterEach(cleanup);
it('groups every project route under labelled rail groups', () => {
  render(<ProjectNavigation base="/projects/p1" />);
  const nav = screen.getByRole('navigation', { name: 'Project' });
  expect(nav.querySelectorAll('.rail-group-label').length).toBe(3);
  const links = [...nav.querySelectorAll('a.rail-link')].map(a => [a.textContent, a.getAttribute('href')]);
  expect(links).toEqual([
    ['7 Agents', '/projects/p1'],
    ['Code review', '/projects/p1/commits'],
    ['Findings', '/projects/p1/findings'],
    ['Approvals', '/projects/p1/approvals'],
    ['Live Defense', '/projects/p1/live-defense'],
    ['Incidents', '/projects/p1/incidents'],
    ['Monitoring', '/projects/p1/monitoring'],
    ['Authorized scopes', '/projects/p1/scopes'],
    ['Expectations', '/projects/p1/expectations'],
    ['Simulate', '/projects/p1/simulate'],
    ['Settings', '/projects/p1/settings'],
  ]);
  expect(screen.getByRole('link', { name: '7 Agents' }).getAttribute('aria-current')).toBe('page');
});
it('keeps the seven-agent workflow selected on replay pages', () => {
  route.path = '/projects/p1/assessments/a1';
  render(<ProjectNavigation base="/projects/p1" />);
  expect(screen.getByRole('link', { name: '7 Agents' }).getAttribute('aria-current')).toBe('page');
});

it('marks the current tool page and only that page', () => {
  route.path = '/projects/p1/scopes';
  render(<ProjectNavigation base="/projects/p1" />);
  expect(screen.getByRole('link', { name: 'Authorized scopes' }).getAttribute('aria-current')).toBe('page');
  expect(screen.getByRole('link', { name: '7 Agents' }).getAttribute('aria-current')).toBeNull();
});
