'use client';

/**
 * One commit through Ari: the code review in plain words, and a replay of the
 * seven security agents checking the parts of the app the change touches. Illustrative only (ADR 0013). Continuous 24/7 defense is not part of a
 * commit's analysis and lives under Monitoring / Defense Lab.
 */
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { AgentMap } from '@/components/demo/agent-map';
import { Avatar, CiBadge, DemoDiff, VerdictChip } from '@/components/demo/ui';
import { relativeTime, type DemoCommit, type DemoRepo } from '@/lib/demo/data';
import { useReplay } from '@/lib/demo/replay';
import { analyze, type AgentId, type AgentRunView, type Analysis } from '@/lib/demo/scenarios';

/** Ticks spent on code understanding and on the review before the security agents start. */
const UNDERSTAND_TICKS = 2;
const REVIEW_TICKS = 3;

type Status = 'queued' | 'running' | 'done' | 'skipped' | 'failed';
type Tab = 'review' | 'agents';
const TABS: { id: Tab; label: string }[] = [
  { id: 'review', label: 'Code review' },
  { id: 'agents', label: '7 agents' },
];

export interface Timeline {
  total: number;
  agentsStart: number;
  spans: Record<AgentId, { start: number; end: number }>;
}

export function timeline(analysis: Analysis): Timeline {
  let cursor = UNDERSTAND_TICKS + REVIEW_TICKS;
  const spans = {} as Timeline['spans'];
  for (const agent of analysis.agents) {
    spans[agent.agent] = { start: cursor, end: cursor + agent.steps.length };
    cursor += agent.steps.length;
  }
  return { total: cursor, agentsStart: UNDERSTAND_TICKS + REVIEW_TICKS, spans };
}

export function agentStatus(agent: AgentRunView, t: Timeline, position: number): Status {
  const span = t.spans[agent.agent];
  if (position < span.start) return 'queued';
  if (agent.status === 'skipped') return 'skipped';
  return position >= span.end ? 'done' : 'running';
}

/** A live run on a real commit (see lib/live-analysis.ts): static reasoning, nothing executed. */
export interface LiveRun { model: string; truncated: boolean; htmlUrl: string }

export function CommitAnalysis({ repo, commit, analysis: given, live, realRun, reviewOnly = false }: { repo: DemoRepo; commit: DemoCommit; analysis?: Analysis; live?: LiveRun; realRun?: ReactNode; reviewOnly?: boolean }) {
  const analysis = useMemo(() => given ?? analyze(repo, commit), [given, repo, commit]);
  const t = useMemo(() => timeline(analysis), [analysis]);
  const replay = useReplay(t.total);
  const [tab, setTab] = useState<Tab>('review');
  const statuses = Object.fromEntries(analysis.agents.map(a => [a.agent, agentStatus(a, t, replay.position)])) as Record<AgentId, Status>;
  const pct = Math.round((Math.min(replay.position, t.total) / t.total) * 100);
  const played = Math.max(0, replay.position - t.agentsStart);
  const accepted = commit.verdict === 'fixed';

  if (reviewOnly) return <Review analysis={analysis} accepted={accepted} live={live} />;

  return (
    <>
      <header className="flex flex-col gap-6">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="min-w-0 max-w-3xl">
            <p className="label-mono flex flex-wrap items-center gap-2 text-white/55">
              <span>{repo.org} / {repo.name}</span><span aria-hidden="true">·</span><span className="font-mono normal-case">{commit.sha.slice(0, 7)}</span>
            </p>
            <h1 className="big-verdict mt-3" data-testid="demo-commit-message">{commit.message}</h1>
            <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-white/65">
              <span className="flex items-center gap-2"><Avatar login={commit.author} size={20} /><span className="font-semibold text-white">{commit.author}</span></span>
              <span suppressHydrationWarning>pushed {relativeTime(commit.minutesAgo)}</span>
              <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono">{commit.branch}</span>
              <span><span className="text-[#7fe0aa]">+{commit.additions}</span> <span className="text-[#ff9b88]">−{commit.deletions}</span></span>
              {live ? <a href={live.htmlUrl} target="_blank" rel="noreferrer" className="underline decoration-white/30 underline-offset-2 hover:text-white">View on GitHub</a> : <CiBadge state={commit.ci} />}
            </p>
          </div>
          <div className="flex flex-col items-start gap-2 md:items-end">
            {replay.done ? <VerdictChip verdict={commit.verdict} /> : <span className="demo-chip demo-chip-info" data-testid="demo-analyzing">Analyzing · {pct}%</span>}
            {!replay.done
              ? <button type="button" onClick={replay.skip} className="rounded-full bg-white/10 px-4 py-1.5 text-xs text-white transition-colors hover:bg-white/20" data-testid="demo-skip">Skip to result</button>
              : <button type="button" onClick={replay.restart} className="rounded-full bg-white/10 px-4 py-1.5 text-xs text-white transition-colors hover:bg-white/20" data-testid="demo-replay">↺ Replay</button>}
          </div>
        </div>
      </header>

      <div role="tablist" aria-label="Commit analysis" className="demo-tabs mt-6">
        {TABS.map(item => (
          <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} aria-controls={`panel-${item.id}`} id={`tab-${item.id}`} onClick={() => setTab(item.id)} className="demo-tab" data-testid={`demo-tab-${item.id}`}>
            <span>{item.label}</span>
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="mt-6">
        {tab === 'review' && <Review analysis={analysis} accepted={accepted} live={live} />}
        {tab === 'agents' && (
          <>
            {realRun && <div className="mb-4">{realRun}</div>}
            <AgentMap repo={repo} commitSha={commit.sha} analysis={analysis} statuses={statuses} played={played} started={replay.position >= t.agentsStart} playback={replay} accepted={accepted} offset={t.agentsStart} />
          </>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------- code review

function Review({ analysis, accepted, live }: { analysis: Analysis; accepted: boolean; live?: LiveRun }) {
  const r = analysis.review;
  const blockers = r.comments.filter(c => c.severity === 'blocker' || c.severity === 'major');
  const brokenTests = new Set(r.tests.filter(x => x.before === 'fail').map(x => x.expectation));
  const verdict = blockers.length === 0 ? { tone: 'ok', word: 'Looks right' } : accepted ? { tone: 'ok', word: 'Problem found and fixed' } : { tone: 'warn', word: 'Problem found' };
  return (
    <div className="flex flex-col gap-5" data-testid="demo-review">
      <section className="glass p-6" data-testid="demo-result">
        <p className="label-mono text-white/55">{analysis.findings.length ? 'What Ari found' : 'Result'}</p>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h2 className="big-verdict">{analysis.headline}</h2>
          <span className={`demo-chip ${verdict.tone === 'ok' ? 'demo-chip-ok' : 'demo-chip-warn'} text-[0.75rem]`}>{verdict.word}</span>
        </div>
        <p className="mt-2 text-sm text-white/60">{r.intent}</p>
        <p className="mt-4 text-sm leading-relaxed text-white/70">{analysis.summary}</p>
        {analysis.findings.length > 0 && (
          <ul className="mt-4 flex flex-col gap-2">
            {analysis.findings.map(f => (
              <li key={f.id} className="review-item">
                <p className="flex flex-wrap items-center gap-2">
                  <span className={`demo-chip ${f.severity === 'critical' || f.severity === 'high' ? 'demo-chip-bad' : 'demo-chip-warn'}`}>{f.severity}</span>
                  <span className="text-sm font-medium text-white">{f.title}</span>
                  <span className={`demo-chip ${accepted ? 'demo-chip-ok' : 'demo-chip-warn'}`}>{accepted ? 'fixed' : 'open'}</span>
                </p>
                <p className="mt-2 font-mono text-xs text-white/50">{f.where} · {f.cwe}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="glass p-6">
        <h3 className="review-heading">What the reviewer flagged</h3>
        {r.comments.length === 0 ? <p className="mt-4 text-sm text-white/65">Nothing.</p> : (
          <ul className="mt-4 flex flex-col gap-3">
            {r.comments.map((c, i) => (
              <li key={i} className="review-item">
                <p className="flex flex-wrap items-center gap-2">
                  <span className={`demo-chip ${c.severity === 'blocker' ? 'demo-chip-bad' : c.severity === 'major' ? 'demo-chip-warn' : 'demo-chip-info'}`}>{c.severity === 'blocker' ? 'must fix' : c.severity === 'major' ? 'should fix' : 'tip'}</span>
                  <span className="text-sm font-medium text-white">{c.title}</span>
                </p>
                <p className="mt-2 text-sm leading-relaxed text-white/70">{c.body}</p>
                <p className="mt-1.5 font-mono text-[0.72rem] text-white/45">{c.path} · {c.line}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="review-heading">The code that changed <span className="text-white/45">· {r.diff.length} file{r.diff.length === 1 ? '' : 's'}</span></h3>
        {r.diff.map(file => <DemoDiff key={file.path} file={file} comments={r.comments} />)}
      </section>

      {analysis.patch && (
        <section className="flex flex-col gap-3">
          <h3 className="review-heading">{live ? 'Proposed fix' : 'The fix'} <span className="text-white/45">· {live ? 'not applied, not tested' : accepted ? 'verified' : 'not yet accepted'}</span></h3>
          <DemoDiff file={analysis.patch} label="fix" />
        </section>
      )}

      <details className="more">
        <summary>Expectations and tests ({r.expectations.length + r.tests.length})</summary>
        <div className="mt-4 flex flex-col gap-5">
          <section>
            <h3 className="review-heading">What the code should always do</h3>
            <ul className="mt-4 flex flex-col gap-3">
              {r.expectations.map(e => {
                const broken = brokenTests.has(e.id);
                const tests = r.tests.filter(x => x.expectation === e.id);
                const ok = !broken || accepted;
                return (
                  <li key={e.id} className="review-item">
                    <p className="flex items-start gap-2.5">
                      <span aria-hidden="true" className={ok ? 'text-[#9ff0c6]' : 'text-[#ff9b88]'}>{ok ? '✓' : '✕'}</span>
                      <span className="text-sm text-white">{e.text}</span>
                    </p>
                    <p className="mt-1.5 pl-6 text-xs leading-relaxed text-white/55">{broken ? (accepted ? 'This change broke it; the fix restores it.' : 'This change breaks it.') : 'Still holds after this change.'} Learned from {e.source}.</p>
                    {tests.length > 0 && <ul className="mt-1.5 flex flex-col gap-1 pl-6 font-mono text-[0.72rem] text-white/60">{tests.map(x => <li key={x.name}>{x.name}: {x.before === 'fail' ? 'failed before the fix' : 'passed'}, passes now</li>)}</ul>}
                  </li>
                );
              })}
            </ul>
          </section>
          <section>
            <h3 className="review-heading">{live ? 'The tests Ari proposes' : 'The tests Ari wrote'} <span className="text-white/45">· {r.tests.length}</span></h3>
            <table className="mt-4 w-full text-sm">
              <thead><tr className="text-left text-xs text-white/45"><th className="pb-2 font-normal">Test</th><th className="pb-2 font-normal">{live ? 'Expected on this commit' : 'Before'}</th><th className="pb-2 font-normal">After fix</th></tr></thead>
              <tbody>
                {r.tests.map(test => (
                  <tr key={test.name} className="border-t border-white/10 align-top">
                    <td className="py-2.5 pr-3 font-mono text-xs [overflow-wrap:anywhere]">{test.name}</td>
                    <td className={`whitespace-nowrap py-2.5 pr-3 ${test.before === 'fail' ? 'text-[#ff9b88]' : 'text-[#9ff0c6]'}`}>{test.before === 'fail' ? '✕ fail' : '✓ pass'}</td>
                    <td className={`whitespace-nowrap py-2.5 ${live ? 'text-white/55' : 'text-[#9ff0c6]'}`}>{live ? 'should pass' : '✓ pass'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-white/45">{live ? 'Proposed from the diff and not run yet. A fix only counts once a test fails before it and passes after.' : 'A test that fails before the fix and passes after is what makes a fix count.'}</p>
          </section>
        </div>
      </details>
    </div>
  );
}
