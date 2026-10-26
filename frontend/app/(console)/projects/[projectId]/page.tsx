'use client';

import type { Finding, SeverityLevel } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { ChecksChart } from '@/components/checks-chart';
import { PillarLoopSection, ProjectJourney } from '@/components/project-progress';
import { useProject } from '@/components/project';
import { Async, Badge, Empty, ErrorState, Loading, PageTitle, Panel, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { shortSha } from '@/lib/format';
import { projectOverview } from '@/lib/overview';
import { findingsForRepo, repoAnalytics } from '@/lib/repo-analytics';
import { effectiveScopeStatus, FINDING_STATUS, SEVERITY } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

export default function ProjectOverview() {
  const { project, selectedRepo } = useProject();
  const assessments = useResource(`asm:${project.id}`, signal => api().assessments(project.id, { signal }), 5000);
  const findings = useResource(`fnd:${project.id}`, signal => api().findings(project.id, { signal }), 10000);
  const scopes = useResource(`scopes:${project.id}`, signal => api().scopes(project.id, { signal }));
  const snapshots = useResource(
    selectedRepo ? `snapshots:${selectedRepo.id}` : null,
    signal => selectedRepo
      ? api().snapshots(selectedRepo.id, { limit: 100 }, { signal })
      : Promise.resolve({ items: [], next_cursor: null }),
  );

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <PageTitle title="Analytics" subtitle={selectedRepo?.full_name ?? 'No repository connected'} />
      {!selectedRepo ? (
        <Empty>
          No repository is connected. <Link className="text-brand hover:underline" href={`/projects/${project.id}/settings#connect-repo`}>Connect a repository</Link>.
        </Empty>
      ) : (
        <Async resource={assessments} label="assessments">
          {assessmentPage => (
            <Async resource={findings} label="findings">
              {findingPage => (
                <Async resource={snapshots} label="saved snapshots">
                  {snapshotPage => {
                    const repoAssessments = assessmentPage.items.filter(assessment => assessment.repo_id === selectedRepo.id);
                    const repoFindings = findingsForRepo(
                      selectedRepo.id,
                      assessmentPage.items,
                      findingPage.items,
                      snapshotPage.items.map(snapshot => snapshot.id),
                    );
                    const analytics = repoAnalytics({
                      repoId: selectedRepo.id,
                      assessments: assessmentPage.items,
                      findings: findingPage.items,
                      snapshotIds: snapshotPage.items.map(snapshot => snapshot.id),
                      now: new Date(),
                    });
                    const activeScopeCount = scopes.status === 'ready'
                      ? scopes.data.items.filter(scope =>
                        effectiveScopeStatus(scope) === 'active'
                        && scope.targets.some(target => target.repo_id === selectedRepo.id),
                      ).length
                      : null;
                    const summary = projectOverview({
                      projectId: project.id,
                      repoCount: 1,
                      activeScopeCount,
                      monitoringEnabled: project.settings.monitoring_policy.enabled,
                      assessments: repoAssessments,
                      findings: repoFindings,
                    });
                    const stale = [assessments, findings, snapshots, scopes].some(resource => resource.refreshError !== undefined);
                    const truncated = assessmentPage.next_cursor !== null || findingPage.next_cursor !== null || snapshotPage.next_cursor !== null;
                    const summaryPath = summary.action.href.split('#')[0] ?? summary.action.href;
                    const summaryHash = summary.action.href.includes('#') ? `#${summary.action.href.split('#')[1]}` : '';
                    const summaryHref = `${summaryPath}${summaryPath.includes('?') ? '&' : '?'}repo=${encodeURIComponent(selectedRepo.id)}${summaryHash}`;

                    return (
                      <div className="flex min-w-0 flex-col gap-6">
                        <section aria-label="Project summary" className="min-w-0" data-testid="project-summary">
                          {scopes.status === 'loading' ? <Loading label="Loading project summary" /> : (
                            <>
                              <h2 className="text-xl font-semibold tracking-tight">{summary.headline}</h2>
                              <p className="mt-2 max-w-3xl text-sm text-muted">{summary.detail}</p>
                              {scopes.status === 'error' && <div className="mt-3"><ErrorState error={scopes.error} onRetry={scopes.reload} context="Scope status unavailable" /></div>}
                              {stale && <p role="status" className="mt-3 text-xs text-warn" data-testid="summary-stale">Could not refresh the latest records. The numbers above are from the last successful load and may be out of date.</p>}
                              <div className="mt-4"><Link href={summaryHref} className="inline-flex items-center rounded-lg bg-brand px-4 py-2 text-sm font-medium text-on-brand hover:bg-brand-deep">{summary.action.label}</Link></div>
                            </>
                          )}
                        </section>
                        {analytics.checksRun === 0 && (
                          <p className="text-sm">
                            <Link className="font-medium text-brand hover:underline" href={`/projects/${project.id}/setup?repo=${encodeURIComponent(selectedRepo.id)}`}>Run your first check →</Link>
                          </p>
                        )}
                        <Panel title="Repository activity">
                          <div data-testid="analytics-stats" className="grid grid-cols-2 sm:grid-cols-4">
                            <StatCell label="Commits checked" value={analytics.commitsChecked} />
                            <StatCell label="Checks run" value={analytics.checksRun} className="border-l border-line" />
                            <StatCell label="Open findings" value={analytics.openFindings} className="border-t border-line sm:border-l sm:border-t-0" />
                            <StatCell label="Fixes verified" value={analytics.fixesVerified} className="border-l border-t border-line sm:border-t-0" />
                          </div>
                        </Panel>
                        <p className="-mt-4 text-xs text-faint">
                          From Ari&apos;s saved records for {selectedRepo.full_name}
                          {truncated && <> · based on the latest {assessmentPage.items.length} checks and {findingPage.items.length} findings loaded</>}
                        </p>
                        <Panel title="Checks per day">
                          <ChecksChart days={analytics.checksPerDay} />
                        </Panel>
                        <div className="grid min-w-0 gap-6 lg:grid-cols-2">
                          <Panel title="Findings by severity">
                            <BreakdownRows
                              rows={(Object.keys(analytics.bySeverity) as (SeverityLevel | 'unassessed')[]).map(level => ({
                                label: level === 'unassessed' ? 'Unassessed' : SEVERITY[level].label,
                                value: analytics.bySeverity[level],
                                tone: level === 'critical' || level === 'high' ? 'var(--color-bad)'
                                  : level === 'medium' ? 'var(--color-warn)'
                                  : level === 'low' ? 'var(--color-info)'
                                  : 'var(--color-muted)',
                              }))}
                            />
                          </Panel>
                          <Panel title="Findings by status">
                            <BreakdownRows rows={[
                              { label: 'Open', value: analytics.byStatusGroup.open, tone: 'var(--color-bad)' },
                              { label: 'Fixed', value: analytics.byStatusGroup.fixed, tone: 'var(--color-ok)' },
                              { label: 'Closed', value: analytics.byStatusGroup.closed, tone: 'var(--color-muted)' },
                            ]} />
                          </Panel>
                        </div>
                        <Panel title="Typical check duration">
                          <p className="font-mono text-3xl font-semibold text-ink">{analytics.medianDurationMs === null ? '—' : durationLabel(analytics.medianDurationMs)}</p>
                          {analytics.medianDurationMs === null && <p className="mt-2 text-sm text-muted">No completed checks yet</p>}
                        </Panel>
                        <Panel title="Top open findings">
                          {analytics.topOpen.length === 0 ? <Empty>No open findings for this repository.</Empty> : (
                            <ul className="divide-y divide-line">
                              {analytics.topOpen.map(finding => <FindingRow key={finding.id} finding={finding} projectId={project.id} repoId={selectedRepo.id} />)}
                            </ul>
                          )}
                        </Panel>
                        <details className="project-detail">
                          <summary>Project progress</summary>
                          <PillarLoopSection
                            projectId={project.id}
                            repoCount={1}
                            activeScopeCount={activeScopeCount}
                            monitoringEnabled={project.settings.monitoring_policy.enabled}
                            assessmentCount={repoAssessments.length}
                            findings={repoFindings}
                          />
                          {activeScopeCount !== null && (
                            <ProjectJourney
                              projectId={project.id}
                              repoCount={1}
                              activeScopeCount={activeScopeCount}
                              monitoringEnabled={project.settings.monitoring_policy.enabled}
                              assessments={repoAssessments}
                              findingStatuses={repoFindings.map(finding => finding.status)}
                            />
                          )}
                        </details>
                      </div>
                    );
                  }}
                </Async>
              )}
            </Async>
          )}
        </Async>
      )}
    </div>
  );
}

function StatCell({ label, value, className = '' }: { label: string; value: number; className?: string }) {
  return (
    <div className={`min-w-0 px-4 py-3 ${className}`}>
      <p className="font-mono text-2xl font-semibold text-ink">{value.toLocaleString()}</p>
      <p className="mt-1 text-xs text-muted">{label}</p>
    </div>
  );
}

function BreakdownRows({ rows }: { rows: { label: string; value: number; tone: string }[] }) {
  const max = Math.max(1, ...rows.map(row => row.value));
  return (
    <ul className="divide-y divide-line">
      {rows.map(row => (
        <li key={row.label} className="flex items-center gap-3 py-2.5 text-sm">
          <span className="w-24 shrink-0 text-muted">{row.label}</span>
          <span className="h-1.5 min-w-0 flex-1 bg-panel-2" role="meter" aria-label={`${row.label} findings`} aria-valuemin={0} aria-valuemax={max} aria-valuenow={row.value}>
            <span className="block h-full" style={{ width: `${row.value / max * 100}%`, backgroundColor: row.tone }} />
          </span>
          <span className="w-8 text-right font-mono text-ink">{row.value}</span>
        </li>
      ))}
    </ul>
  );
}

function FindingRow({ finding, projectId, repoId }: { finding: Finding; projectId: string; repoId: string }) {
  return (
    <li className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-3">
      <Link className="min-w-0 flex-1 break-words text-sm hover:text-accent" href={`/projects/${projectId}/findings/${finding.id}?repo=${encodeURIComponent(repoId)}`}>
        <span className="block">{finding.title}</span>
        <span className="mt-1 block text-xs text-muted"><span className="font-mono">{shortSha(finding.commit_sha)}</span> · <Time iso={finding.updated_at} /></span>
      </Link>
      <span className="flex flex-wrap items-center gap-2">
        {finding.severity ? <Badge view={SEVERITY[finding.severity.level]} /> : <span className="text-xs text-faint">unassessed</span>}
        <Badge view={FINDING_STATUS[finding.status]} />
      </span>
    </li>
  );
}

function durationLabel(durationMs: number): string {
  const totalSeconds = Math.floor(durationMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}
