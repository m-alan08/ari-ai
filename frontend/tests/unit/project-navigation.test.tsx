import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ProjectNavigation } from '@/components/project';

const route = vi.hoisted(() => ({ path: '/projects/p1' }));
vi.mock('next/navigation', () => ({
  usePathname: () => route.path,
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams('repo=r1'),
  useRouter: () => ({ replace: vi.fn() }),
}));
afterEach(cleanup);

it('shows the primary tabs and repo-scoped More destinations', () => {
  render(<ProjectNavigation base="/projects/p1" repoId="r1" />);
  const nav = screen.getByRole('navigation', { name: 'Project' });
  const links = [...nav.querySelectorAll('.project-tab-scroll a')].map(a => [a.textContent, a.getAttribute('href')]);
  expect(links).toEqual([
    ['Analytics', '/projects/p1?repo=r1'],
    ['Commits', '/projects/p1/commits?repo=r1'],
    ['Findings', '/projects/p1/findings?repo=r1'],
    ['Approvals', '/projects/p1/approvals?repo=r1'],
    ['Settings', '/projects/p1/settings?repo=r1'],
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'More' }));
  expect([...nav.querySelectorAll('.project-more-link')].map(a => [a.textContent, a.getAttribute('href')])).toEqual([
    ['Live Defense', '/projects/p1/live-defense?repo=r1'],
    ['Incidents', '/projects/p1/incidents?repo=r1'],
    ['Monitoring', '/projects/p1/monitoring?repo=r1'],
    ['Authorized scopes', '/projects/p1/scopes?repo=r1'],
    ['Expectations', '/projects/p1/expectations?repo=r1'],
    ['Simulate', '/projects/p1/simulate?repo=r1'],
    ['Onboarding', '/projects/p1/onboarding?repo=r1'],
    ['Setup', '/projects/p1/setup?repo=r1'],
  ]);
});

it('marks Analytics only on the exact base and Commits on run routes', () => {
  route.path = '/projects/p1';
  const { unmount } = render(<ProjectNavigation base="/projects/p1" repoId="r1" />);
  expect(screen.getByRole('link', { name: 'Analytics' }).getAttribute('aria-current')).toBe('page');
  unmount();
  route.path = '/projects/p1/assessments/a1';
  render(<ProjectNavigation base="/projects/p1" repoId="r1" />);
  expect(screen.getByRole('link', { name: 'Analytics' }).getAttribute('aria-current')).toBeNull();
  expect(screen.getByRole('link', { name: 'Commits' }).getAttribute('aria-current')).toBe('page');
});

it('shows the active More route label and underline', () => {
  route.path = '/projects/p1/scopes';
  render(<ProjectNavigation base="/projects/p1" repoId="r1" />);
  const toggle = screen.getByRole('button', { name: 'Authorized scopes' });
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(toggle.getAttribute('aria-current')).toBe('page');
  fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByRole('link', { name: 'Authorized scopes' }).getAttribute('aria-current')).toBe('page');
});
