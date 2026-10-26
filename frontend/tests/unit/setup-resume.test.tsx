import { cleanup, render, screen } from '@testing-library/react';
import type { AuthorizedScope, Project, Repository } from '@hackatl/frontend-transport';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import SetupPage from '@/app/(console)/projects/[projectId]/setup/page';

const mocks = vi.hoisted(() => ({
  query: 'repo=rep_12345678&step=5',
  useResource: vi.fn(),
  project: null as unknown,
  replace: vi.fn(),
  push: vi.fn(),
  automaticAssessments: false,
  scopeItems: [] as unknown[],
  runItems: [] as unknown[],
}));
vi.mock('@/components/project', () => ({ useProject: () => mocks.project }));
vi.mock('@/lib/use-resource', () => ({ useResource: mocks.useResource }));
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(mocks.query),
  usePathname: () => '/projects/prj_12345678/setup',
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}));

const project = {
  id: 'prj_12345678',
  name: 'Payments',
  settings: {
    review: { strictness: 2, review_pushes: false, review_pull_requests: false, comment_on_pull_requests: false },
    monitoring_policy: { enabled: false },
    delivery_mode: 'pull_request',
  },
} as unknown as Project;
const repo = {
  id: 'rep_12345678',
  full_name: 'acme/payments-lite',
  default_branch: 'main',
  installation_id: null,
  automatic_assessments: false,
} as unknown as Repository;

beforeEach(() => {
  localStorage.clear();
  mocks.query = 'repo=rep_12345678&step=5';
  mocks.automaticAssessments = false;
  mocks.scopeItems = [];
  mocks.runItems = [];
  mocks.project = { project, reloadProject: vi.fn() };
  mocks.useResource.mockImplementation((key: string | null) => {
    if (key?.includes('setup-repos')) return {
      status: 'ready',
      data: { items: [{ ...repo, automatic_assessments: mocks.automaticAssessments }] },
      reload: vi.fn(),
    };
    if (key?.includes('setup-scopes')) return { status: 'ready', data: { items: mocks.scopeItems }, reload: vi.fn() };
    if (key?.includes('setup-runs')) return { status: 'ready', data: { items: mocks.runItems }, reload: vi.fn() };
    return { status: 'ready', data: { items: [] }, reload: vi.fn() };
  });
});
afterEach(cleanup);

it('resumes the testing-scope panel from ?step=5', () => {
  render(<SetupPage />);
  expect(screen.getByTestId('setup-step-5')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Approve sandbox testing for 30 days' })).toBeTruthy();
});

it('shows the stored expiry when approving a pending scope', () => {
  const expiresAt = new Date(Date.now() + 7 * 86_400_000);
  const expiresAtIso = expiresAt.toISOString();
  mocks.scopeItems = [{
    id: 'scp_12345678',
    project_id: project.id,
    name: 'Authorize acme/payments-lite',
    targets: [{ kind: 'sandbox_build', repo_id: repo.id, build_recipe_ref: 'build_recipe.json', exposed_ports: [] }],
    allowed_check_categories: [],
    status: 'pending',
    requested_by: 'usr_requester',
    approved_by: null,
    approved_at: null,
    expires_at: expiresAtIso,
    notes: null,
  } as AuthorizedScope];
  render(<SetupPage />);
  const expiry = screen.getByText(expiresAt.toLocaleDateString());
  expect(expiry.getAttribute('datetime')).toBe(expiresAtIso);
  expect(expiry.closest('dd')?.textContent).toBe(`Until ${expiresAt.toLocaleDateString()}`);
  expect(screen.getByRole('button', { name: /Approve sandbox testing until/ })).toBeTruthy();
});

it('disables Start until the testing scope is authorized', () => {
  mocks.query = 'repo=rep_12345678&step=7';
  render(<SetupPage />);
  expect(screen.getByTestId('setup-step-7')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Start first assessment' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('button', { name: 'Review step 5: testing scope' })).toBeTruthy();
  expect(screen.getByText('Automatic reviews')).toBeTruthy();
  expect(screen.getByText('Off — you start each review')).toBeTruthy();
  expect(screen.getByText('Review on push (when automatic)')).toBeTruthy();
  expect(screen.getByText('Review on PR (when automatic)')).toBeTruthy();
});

it('shows automatic reviews as On when repository automatic assessments are enabled', () => {
  mocks.query = 'repo=rep_12345678&step=7';
  mocks.automaticAssessments = true;
  render(<SetupPage />);
  expect(screen.getByText('On')).toBeTruthy();
  expect(screen.queryByText('Off — you start each review')).toBeNull();
});

it('links setup review history to the project assessment section', () => {
  mocks.query = 'repo=rep_12345678&step=7';
  mocks.runItems = [{ id: 'asm_12345678', repo_id: repo.id }];
  render(<SetupPage />);
  expect(screen.getByRole('link', { name: 'See your reviews' }).getAttribute('href')).toBe('/projects/prj_12345678/commits?repo=rep_12345678#assessments');
});
