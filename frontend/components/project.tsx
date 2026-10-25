'use client';

import type { Project, Repository } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useParams, usePathname } from 'next/navigation';
import { createContext, useContext, type ReactNode } from 'react';
import { api } from '@/lib/api';
import { useResource } from '@/lib/use-resource';
import { ErrorState, Loading } from '@/components/ui';

interface ProjectValue { project: Project; repos: Repository[]; reloadRepos: () => void; reloadProject: () => void }
const ProjectContext = createContext<ProjectValue | null>(null);

export function useProject(): ProjectValue {
  const value = useContext(ProjectContext);
  if (!value) throw new Error('useProject outside ProjectShell');
  return value;
}

/** Route params are untrusted strings; the transport rejects non-ID characters. */
export function useParam(name: string): string {
  const params = useParams();
  const value = params[name];
  return typeof value === 'string' ? value : '';
}

interface RailItem { href: string; label: string }
const GROUPS: { label: string; items: RailItem[] }[] = [
  {
    label: 'Review',
    items: [
      { href: '', label: '7 Agents' },
      { href: '/commits', label: 'Code review' },
      { href: '/findings', label: 'Findings' },
      { href: '/approvals', label: 'Approvals' },
    ],
  },
  {
    label: 'Defend',
    items: [
      { href: '/live-defense', label: 'Live Defense' },
      { href: '/incidents', label: 'Incidents' },
      { href: '/monitoring', label: 'Monitoring' },
      { href: '/scopes', label: 'Authorized scopes' },
      { href: '/expectations', label: 'Expectations' },
    ],
  },
  {
    label: 'Configure',
    items: [
      { href: '/simulate', label: 'Simulate' },
      { href: '/settings', label: 'Settings' },
    ],
  },
];

const isActive = (pathname: string, base: string, href: string) =>
  href === ''
    ? pathname === base || pathname.startsWith(`${base}/assessments/`)
    : pathname.startsWith(base + href);

export function ProjectNavigation({ base }: { base: string }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Project" className="rail-nav">
      {GROUPS.map(group => (
        <div key={group.label} className="rail-group">
          <p className="rail-group-label">{group.label}</p>
          {group.items.map(item => {
            const active = isActive(pathname, base, item.href);
            return (
              <Link
                key={item.label}
                href={base + item.href}
                aria-current={active ? 'page' : undefined}
                className="rail-link"
              >
                {item.label}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

export function ProjectShell({ children }: { children: ReactNode }) {
  const id = useParam('projectId');
  const project = useResource(`project:${id}`, signal => api().project(id, { signal }));
  const repos = useResource(`repos:${id}`, signal => api().repositories(id, { signal }));
  if (project.status === 'loading' || repos.status === 'loading') return <Loading label="Loading project" />;
  if (project.status === 'error') return <ErrorState error={project.error} onRetry={project.reload} context="Could not load project" />;
  if (repos.status === 'error') return <ErrorState error={repos.error} onRetry={repos.reload} context="Could not load repositories" />;
  const base = `/projects/${project.data.id}`;
  const monitoring = project.data.settings.monitoring_policy.enabled;
  return (
    <ProjectContext.Provider value={{ project: project.data, repos: repos.data.items, reloadRepos: repos.reload, reloadProject: project.reload }}>
      <div className="project-layout">
        <aside className="project-rail">
          <div className="rail-head">
            <Link href="/connect" className="rail-back label-mono">← Projects</Link>
            <p className="rail-name" data-testid="project-title">{project.data.name}</p>
            <div className="rail-meta">
              {repos.data.items.length === 0
                ? <span>No repository connected</span>
                : repos.data.items.map(r => <span key={r.id} className="rail-repo">{r.full_name}<span className="rail-branch"> · {r.default_branch}</span></span>)}
            </div>
          </div>
          <ProjectNavigation base={base} />
          <div className="rail-foot">
            <span className={`rail-status ${monitoring ? 'is-on' : ''}`} aria-hidden="true" />
            Monitoring {monitoring ? 'on' : 'off'}
          </div>
        </aside>
        <div className="project-body">{children}</div>
      </div>
    </ProjectContext.Provider>
  );
}
