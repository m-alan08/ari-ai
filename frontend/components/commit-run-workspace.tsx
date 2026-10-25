'use client';

import type { AgentRun, AssessmentDetail } from '@hackatl/frontend-transport';
import { useMemo, useState } from 'react';
import { CancelButton } from '@/components/assessment-cancel';
import { ApprovalGate } from '@/components/assessment-approval-gate';
import { AgentMap } from '@/components/demo/agent-map';
import { CommitAnalysis, agentStatus, timeline, type LiveRun } from '@/components/demo/commit-analysis';
import { fullEventLog } from '@/lib/journey';
import { ErrorState, Loading } from '@/components/ui';
import { api } from '@/lib/api';
import { plainToolName, ticks } from '@/lib/demo/agent-map';
import type { DemoCommit, DemoRepo } from '@/lib/demo/data';
import { useReplay } from '@/lib/demo/replay';
import type { Analysis } from '@/lib/demo/scenarios';
import { fallbackSectors, recordedAnalysis, recordedStatuses } from '@/lib/recorded-analysis';
import { agentRunSummary, AGENT_RUN_STATUS, providerLabel, ASSESSMENT_STATE, FINDING_STATUS, SEVERITY, TERMINAL_STATES } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

/** A run may only be rendered inside the repository and exact commit that created it. */
export function belongsToCommit(detail: AssessmentDetail, repoId: string, sha: string): boolean {
  return detail.assessment.repo_id === repoId && detail.assessment.commit_sha === sha;
}

export interface LiveCommitView { commit: DemoCommit; repo: DemoRepo; analysis: Analysis; live: LiveRun }
export interface LiveState { status: 'loading' | 'error' | 'ready'; error?: string; retry: () => void }

type Tab = 'review' | 'agents';
const TABS: { id: Tab; label: string }[] = [
  { id: 'review', label: 'Code review' },
  { id: 'agents', label: '7 agents' },
];

export function CommitRunWorkspace({ assessmentId, repoId, sha, fullName, liveView = null, liveState = null }: {
  assessmentId: string | null; repoId: string | null; sha: string; fullName: string;
  liveView?: LiveCommitView | null; liveState?: LiveState | null;
}) {
  const [tab, setTab] = useState<Tab>('review');
  const run = useResource(assessmentId ? `commit-run:${repoId}:${sha}:${assessmentId}` : null,
    signal => api().assessment(assessmentId!, { signal }), 5000,
    d => !TERMINAL_STATES.has(d.assessment.state));
  const matching = run.status === 'ready' && repoId && belongsToCommit(run.data, repoId, sha) ? run.data : null;
  const detail = matching ? { ...matching, agent_runs: matching.agent_runs.filter(r => r.assessment_id === matching.assessment.id), findings: matching.findings.filter(f => f.assessment_id === matching.assessment.id && f.commit_sha === sha) } : null;
  const mismatch = run.status === 'ready' && !detail;
  return <>
    {detail && !TERMINAL_STATES.has(detail.assessment.state) && <div className="mt-4 flex justify-end"><CancelButton compact id={detail.assessment.id} onDone={run.reload} /></div>}
    {detail?.assessment.state === 'AWAITING_APPROVAL' && <CommitApproval assessmentId={detail.assessment.id} projectId={detail.assessment.project_id} findings={detail.findings} onDecided={run.reload} />}
    <div role="tablist" aria-label="Commit analysis" className="demo-tabs mt-6">
      {TABS.map(key => <button type="button" role="tab" key={key.id} id={`commit-tab-${key.id}`} aria-controls="commit-work-panel" aria-selected={tab === key.id} className="demo-tab" onClick={() => setTab(key.id)}>{key.label}</button>)}
    </div>
    <div role="tabpanel" id="commit-work-panel" aria-labelledby={`commit-tab-${tab}`} className="mt-6">
      {assessmentId && run.status === 'loading' && <Loading label="Loading this commit’s run" />}
      {run.status === 'error' && <ErrorState error={run.error} onRetry={run.reload} />}
      {run.refreshError !== undefined && <p role="status">Could not refresh. Showing the last loaded records.</p>}
      {mismatch && <p role="alert">This run belongs to another repository or commit. Its records are not shown here.</p>}
      {/* No preview flash while a selected run is still loading. */}
      {!mismatch && !(assessmentId && run.status === 'loading') && tab === 'review' && <ReviewPanel detail={detail} liveView={liveView} liveState={liveState} />}
      {!mismatch && !(assessmentId && run.status === 'loading') && tab === 'agents' && <AgentsPanel detail={detail} sha={sha} fullName={fullName} liveView={liveView} />}
    </div>
  </>;
}

// ------------------------------------------------------------------ code review

function ReviewPanel({ detail, liveView, liveState }: { detail: AssessmentDetail | null; liveView: LiveCommitView | null; liveState: LiveState | null }) {
  const functional = detail?.findings.filter(f => f.kind === 'functional') ?? [];
  const reviewerRuns = detail?.agent_runs.filter(r => r.agent === 'reviewer') ?? [];
  return <div className="flex flex-col gap-6">
    {detail && <section className="glass p-6" data-testid="commit-reviewer">
      <p className="label-mono text-white/55">Recorded review</p>
      <h2 className="mt-3 text-xl font-semibold">{agentRunSummary(reviewerRuns)}</h2>
      <WorkerEvidence runs={reviewerRuns} />
      {functional.map(f => <FindingCard key={f.id} finding={f} />)}
    </section>}
    {liveState?.status === 'loading' && <section className="glass p-6"><Loading label="Reading the real diff" /></section>}
    {liveState?.status === 'error' && (
      <section className="glass p-6" data-testid="commit-quick-read-error">
        <p className="label-mono text-white/55">Quick read</p>
        {/anthropic_api_key|not configured/i.test(liveState.error ?? '') ? (
          <>
            <p role="alert" className="mt-2 text-sm">The instant AI read is not available — the server has no model API key configured. Everything else on this page still works: an authorized run below records the full review and the seven agents.</p>
            <details className="mt-2 text-xs text-white/45"><summary>Technical detail</summary><p className="mt-1 break-all">{liveState.error}</p></details>
          </>
        ) : (
          <p role="alert" className="mt-2 text-sm">{liveState.error}</p>
        )}
        <button type="button" className="mt-3 rounded-full bg-white/10 px-4 py-2 text-sm" onClick={liveState.retry}>Try again</button>
      </section>
    )}
    {liveView && (
      <div data-testid="commit-quick-read">
        <p className="label-mono mb-3 text-white/45" title="The diff comes from GitHub. One Claude pass over it — nothing executed.">
          Quick read · one Claude pass over the real diff · nothing executed{liveView.live.truncated ? ' · diff too large, partly cut' : ''}
        </p>
        <CommitAnalysis repo={liveView.repo} commit={liveView.commit} analysis={liveView.analysis} live={liveView.live} reviewOnly />
      </div>
    )}
    {!detail && !liveView && liveState?.status !== 'loading' && liveState?.status !== 'error' && (
      <section className="glass p-6"><p className="text-sm text-white/65">Nothing to review yet. Start an authorized run or wait for the quick read.</p></section>
    )}
  </div>;
}

// ------------------------------------------------------------------ agent replay

function AgentsPanel({ detail, sha, fullName, liveView }: { detail: AssessmentDetail | null; sha: string; fullName: string; liveView: LiveCommitView | null }) {
  if (detail) return <RecordedSectorMap detail={detail} sha={sha} fullName={fullName} liveView={liveView} />;
  if (liveView) return <PreviewSectorMap view={liveView} />;
  return (
    <section className="glass p-6" data-testid="commit-agent-map">
      <p className="label-mono text-white/55">No replay yet</p>
      <p className="mt-3 text-sm text-white/65">The sector map appears here once the seven agents record work on this commit — run them above — or once the quick read finishes.</p>
    </section>
  );
}

function sectorRepo(liveView: LiveCommitView | null, fullName: string, detail: AssessmentDetail): DemoRepo {
  if (liveView) return liveView.repo;
  return {
    org: fullName.split('/')[0] ?? '', name: fullName.split('/')[1] ?? fullName, description: '',
    language: '', visibility: 'private', defaultBranch: '', stars: 0,
    components: fallbackSectors(fullName, detail), links: [], commits: [],
  };
}

/** The real recorded run on the sector map: each recorded tool call pinned to the part it touched. */
function RecordedSectorMap({ detail, sha, fullName, liveView }: { detail: AssessmentDetail; sha: string; fullName: string; liveView: LiveCommitView | null }) {
  const repo = useMemo(() => sectorRepo(liveView, fullName, detail), [liveView, fullName, detail]);
  const analysis = useMemo(() => recordedAnalysis(detail, repo, liveView?.analysis.review.diff ?? []), [detail, repo, liveView]);
  const all = useMemo(() => ticks(analysis, repo), [analysis, repo]);
  const replay = useReplay(all.length, 700);
  const statuses = useMemo(() => recordedStatuses(detail, TERMINAL_STATES.has(detail.assessment.state)), [detail]);
  const verify = detail.agent_runs.filter(r => r.agent === 'verify').at(-1);
  const accepted = verify?.status === 'succeeded';
  const security = detail.findings.filter(f => f.kind === 'security');
  return (
    <div data-testid="commit-agent-map">
      <p className="label-mono mb-3 text-white/45" title="Every step below is a persisted tool call from the recorded run.">
        Recorded run · {ASSESSMENT_STATE[detail.assessment.state].label}
        {detail.agent_runs.some(r => r.provider_mode !== 'live') ? ` · ${providerLabel(detail.agent_runs.find(r => r.provider_mode !== 'live')!.provider_mode).label}` : detail.agent_runs.length === 0 ? ' · no runs yet' : ' · live provider'}
      </p>
      <AgentMap repo={repo} commitSha={sha} analysis={analysis} statuses={statuses} played={replay.position} started playback={replay} accepted={accepted} offset={0} />
      {security.length > 0 && (
        <section className="glass mt-4 p-6" data-testid="commit-security-findings">
          <p className="label-mono text-white/55">Security findings · {security.length}</p>
          {security.map(f => <FindingCard key={f.id} finding={f} />)}
        </section>
      )}
    </div>
  );
}

/** No recorded run: the quick read's reasoning replayed on the same map, labelled as a preview. */
function PreviewSectorMap({ view }: { view: LiveCommitView }) {
  const t = useMemo(() => timeline(view.analysis), [view.analysis]);
  const replay = useReplay(t.total);
  const statuses = Object.fromEntries(view.analysis.agents.map(a => [a.agent, agentStatus(a, t, replay.position)])) as Parameters<typeof AgentMap>[0]['statuses'];
  const played = Math.max(0, replay.position - t.agentsStart);
  return (
    <div data-testid="commit-agent-map">
      <p className="label-mono mb-3 text-white/45" title="One Claude pass over the diff — a preview of what the agents check, not an executed run.">
        Preview · reasoning over the diff, nothing executed — run the 7 agents to record a real replay
      </p>
      <AgentMap repo={view.repo} commitSha={view.commit.sha} analysis={view.analysis} statuses={statuses} played={played} started={replay.position >= t.agentsStart} playback={replay} accepted={view.commit.verdict === 'fixed'} offset={t.agentsStart} />
    </div>
  );
}

// ------------------------------------------------------------------ shared bits

function FindingCard({ finding }: { finding: AssessmentDetail['findings'][number] }) {
  const status = FINDING_STATUS[finding.status];
  const severity = finding.severity?.level ? SEVERITY[finding.severity.level] : null;
  const where = (finding.evidence.find(e => e.path)?.path ?? finding.subject_refs[0])?.replace(/^target\//, '');
  return <article className="mt-4 border-t border-white/10 pt-4" data-testid="commit-finding">
    <p className="flex flex-wrap items-center gap-2">
      {severity && <span className={`demo-chip ${severity.tone === 'bad' ? 'demo-chip-bad' : severity.tone === 'warn' ? 'demo-chip-warn' : 'demo-chip-info'}`}>{severity.label}</span>}
      <span className="font-medium">{finding.title}</span>
      <span className={`demo-chip ${status.tone === 'ok' ? 'demo-chip-ok' : status.tone === 'bad' ? 'demo-chip-bad' : status.tone === 'warn' ? 'demo-chip-warn' : 'demo-chip-info'}`}>{status.label}</span>
    </p>
    {where && <p className="mt-1.5 font-mono text-xs text-white/50">{where}</p>}
  </article>;
}

function WorkerEvidence({ runs }: { runs: AgentRun[] }) {
  return <>{runs.map(run => <div key={run.id} className="mt-4 text-sm">
    <p className="text-white/55">{AGENT_RUN_STATUS[run.status].label} · {providerLabel(run.provider_mode).label}</p>
    {run.error && <p role="alert" className="mt-2">{run.error.message}</p>}
    <details className="mt-3"><summary>Recorded tool calls ({run.tool_calls.length})</summary><ol className="mt-2 space-y-2">{run.tool_calls.map((call, i) => <li key={i} className="break-words text-xs text-white/65">{plainToolName(call.tool_name)} · {call.outcome}{call.error_code ? ` · ${call.error_code}` : ''}</li>)}</ol></details>
  </div>)}</>;
}

function CommitApproval({ assessmentId, projectId, findings, onDecided }: { assessmentId: string; projectId: string; findings: AssessmentDetail['findings']; onDecided: () => void }) {
  const events = useResource(`commit-approval:${assessmentId}`, signal => fullEventLog(after => api().eventsLog(assessmentId, after, { signal })), 5000);
  if (events.status === 'loading') return <Loading label="Loading this run’s approval request" />;
  if (events.status === 'error') return <ErrorState error={events.error} onRetry={events.reload} />;
  if (events.data.truncated) return <p role="alert">The run’s approval log is incomplete. Reload to check the approval request.</p>;
  return <ApprovalGate projectId={projectId} events={events.data.items} findings={findings} onDecided={onDecided} />;
}
