'use client';

import type { Assessment, RepositoryCommit } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useState } from 'react';
import { AgentRunList } from '@/components/agent-run-list';
import { AssessmentList } from '@/components/assessment-history';
import { CommitAssessmentChip, CommitAssessmentProvider } from '@/components/commit-assessment-chips';
import { useProject } from '@/components/project';
import { StartAssessment } from '@/components/start-assessment';
import { Async, Badge, Empty, ErrorState, Mono, PageTitle, Panel, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { shortSha } from '@/lib/format';
import { FINDING_STATUS, SEVERITY } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

type Commit = { sha: string; message: string | null; committed_at: string | null };
type ViewTab = 'review' | 'agents';

export default function CommitsPage() {
  const { project, selectedRepo } = useProject();
  const router = useRouter();
  const search = useSearchParams();
  const [view, setView] = useState<ViewTab>('review');
  const [runId, setRunId] = useState('');
  const github = useResource(selectedRepo ? `commits:${selectedRepo.id}` : null, signal => selectedRepo ? api().repositoryCommits(selectedRepo.id, { signal }) : Promise.resolve({ items: [], available: false, reason: null }));
  const assessments = useResource(selectedRepo ? `commits-asm:${project.id}` : null, signal => api().assessments(project.id, { signal }), 5000);
  const findings = useResource(selectedRepo ? `commits-findings:${project.id}` : null, signal => api().findings(project.id, { signal }));
  const selectedSha = search.get('commit');
  const commitQuery = (sha: string | null) => {
    const params = new URLSearchParams(search.toString());
    if (sha) params.set('commit', sha); else params.delete('commit');
    router.replace(`/projects/${project.id}/commits?${params.toString()}`, { scroll: false });
  };

  if (!selectedRepo) return <div className="flex flex-col gap-6"><PageTitle title="Commits" subtitle="Select a repository to view its commits." /><Empty>No repository connected. <Link className="text-brand hover:underline" href={`/projects/${project.id}/settings#connect-repo`}>Connect a repository</Link>.</Empty></div>;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <PageTitle title="Commits" subtitle={selectedRepo.full_name} />
      <Async resource={assessments} label="assessments">
        {assessmentPage => {
          const repoAssessments = assessmentPage.items.filter(a => a.repo_id === selectedRepo.id);
          const commits = mergeCommits(github.status === 'ready' && github.data.available ? github.data.items : [], repoAssessments);
          const current = commits.find(commit => commit.sha === selectedSha);
          const runs = repoAssessments.filter(a => a.commit_sha === selectedSha).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
          const activeRun = runs.find(run => run.id === runId) ?? runs[0];
          const openFullRun = activeRun ? `/projects/${project.id}/assessments/${activeRun.id}?repo=${encodeURIComponent(selectedRepo.id)}` : null;
          const analyze = selectedSha ? `/connect/${selectedRepo.full_name.split('/').map(encodeURIComponent).join('/')}/commit/${selectedSha}` : null;
          return (
            <>
              <div className="grid min-w-0 gap-6 lg:grid-cols-[2fr_3fr]">
                <Panel title="History">
                  {github.status === 'error' && <ErrorState error={github.error} onRetry={github.reload} />}
                  {github.status === 'ready' && !github.data.available && <p className="mb-3 text-xs text-muted">{github.data.reason ?? 'GitHub commit history is unavailable.'}</p>}
                  <CommitAssessmentProvider repoId={selectedRepo.id}>
                    {commits.length ? <ul className="divide-y divide-line">
                      {commits.map(commit => (
                        <li key={commit.sha}>
                          <button type="button" onClick={() => { commitQuery(commit.sha); setRunId(''); }}
                            className={`flex w-full min-w-0 items-center gap-3 border-l-2 px-3 py-3 text-left hover:bg-panel-2 ${selectedSha === commit.sha ? 'border-brand bg-brand/10' : 'border-transparent'}`}
                            aria-current={selectedSha === commit.sha ? 'true' : undefined} data-testid={`commit-row-${commit.sha}`}>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm">{commit.message || `Commit ${shortSha(commit.sha)}`}</span>
                              <span className="mt-1 flex flex-wrap gap-2 text-xs text-muted"><Mono>{shortSha(commit.sha)}</Mono>{commit.committed_at && <Time iso={commit.committed_at} />}</span>
                            </span>
                            <CommitAssessmentChip repoId={selectedRepo.id} sha={commit.sha} />
                          </button>
                        </li>
                      ))}
                    </ul> : <Empty>No commits or assessments are available for this repository.</Empty>}
                  </CommitAssessmentProvider>
                </Panel>
                <Panel title="Commit">
                  {!selectedSha ? <Empty>Pick a commit to see its code review and the 7 agents&apos; work.</Empty> : (
                    <>
                      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
                        <div><p className="text-base text-ink">{current?.message ?? `Commit ${shortSha(selectedSha)}`}</p><p className="mt-1 font-mono text-xs text-muted">{shortSha(selectedSha)}</p></div>
                        <div className="flex gap-4 text-sm">{analyze && <Link className="text-brand hover:underline" href={analyze}>Analyze this commit →</Link>}{openFullRun && <Link className="text-brand hover:underline" href={openFullRun}>Open full run →</Link>}</div>
                      </div>
                      {!runs.length ? <p className="py-4 text-sm text-muted">Not reviewed yet. {analyze && <Link className="text-brand hover:underline" href={analyze}>Analyze this commit →</Link>}</p> : (
                        <>
                          {runs.length > 1 && <label className="my-3 flex max-w-full flex-wrap items-center gap-2 text-xs text-muted">Run: <select className="min-w-0 max-w-full rounded border border-line bg-panel px-2 py-1" value={activeRun?.id ?? ''} onChange={event => setRunId(event.target.value)}>{runs.map(run => <option key={run.id} value={run.id}>{new Date(run.created_at).toLocaleString()} · {run.state}</option>)}</select></label>}
                          <div role="tablist" aria-label="Commit review sections" className="flex gap-5 border-b border-line">
                            {([['review', 'Code review'], ['agents', '7 agents']] as const).map(([id, label]) => <button key={id} id={`commit-tab-${id}`} role="tab" aria-selected={view === id} aria-controls="commit-panel" className={`border-b-2 py-2 text-sm ${view === id ? 'border-brand text-ink' : 'border-transparent text-muted'}`} onKeyDown={event => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') setView(view === 'review' ? 'agents' : 'review'); }} onClick={() => setView(id)}>{label}</button>)}
                          </div>
                          <div role="tabpanel" id="commit-panel" aria-labelledby={`commit-tab-${view}`} className="pt-3">
                            {view === 'review' && <Async resource={findings} label="findings">{page => {
                              const items = page.items.filter(f => f.assessment_id === activeRun?.id && f.kind !== 'security');
                              return items.length ? <ul className="divide-y divide-line">{items.map(f => <li key={f.id} className="flex flex-wrap items-center justify-between gap-2 py-3"><Link className="text-sm hover:text-brand" href={`/projects/${project.id}/findings/${f.id}?repo=${encodeURIComponent(selectedRepo.id)}`}>{f.title}</Link><span className="flex gap-2"><Badge view={FINDING_STATUS[f.status]} />{f.severity && <Badge view={SEVERITY[f.severity.level]} />}</span></li>)}</ul> : <p className="text-sm text-muted">No functional findings in this run.</p>;
                            }}</Async>}
                            {view === 'agents' && <RecordedAgents run={activeRun ?? null} projectId={project.id} />}
                          </div>
                        </>
                      )}
                    </>
                  )}
                </Panel>
              </div>
              <StartAssessment onStarted={() => { void assessments.reload(); }} />
              <Panel title="Runs & replay" id="assessments" className="scroll-mt-24">
                <AssessmentList projectId={project.id} repoId={selectedRepo.id} page={assessmentPage} />
              </Panel>
            </>
          );
        }}
      </Async>
    </div>
  );
}

function mergeCommits(github: RepositoryCommit[], assessments: Assessment[]): Commit[] {
  const bySha = new Map<string, Commit>();
  for (const commit of github) bySha.set(commit.sha, { sha: commit.sha, message: commit.message, committed_at: commit.committed_at });
  for (const assessment of assessments) {
    const existing = bySha.get(assessment.commit_sha);
    if (!existing || (!existing.committed_at || Date.parse(assessment.created_at) > Date.parse(existing.committed_at))) {
      bySha.set(assessment.commit_sha, { sha: assessment.commit_sha, message: existing?.message ?? null, committed_at: assessment.created_at });
    }
  }
  return [...bySha.values()].sort((a, b) => Date.parse(b.committed_at ?? '') - Date.parse(a.committed_at ?? ''));
}

function RecordedAgents({ run, projectId }: { run: Assessment | null; projectId: string }) {
  const result = useResource(run ? `commit-agent-run:${run.id}` : null, signal => run ? api().agentRuns(run.id, { signal }) : Promise.resolve({ items: [], next_cursor: null }));
  if (!run) return <p className="text-sm text-muted">Not reviewed yet.</p>;
  return <Async resource={result} label="agent runs">{page => <><AgentRunList runs={page.items} state={run.state} /><Link className="mt-3 inline-block text-sm text-brand hover:underline" href={`/projects/${projectId}/assessments/${run.id}?repo=${encodeURIComponent(run.repo_id ?? '')}`}>Open full run &amp; replay →</Link></>}</Async>;
}
