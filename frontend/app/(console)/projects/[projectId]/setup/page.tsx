'use client';

import type { DeliveryMode, Repository, RepositorySetup } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { BuildRecipeReadiness } from '@/components/onboarding/build-recipe-readiness';
import { SetupDone } from '@/components/onboarding/setup-done';
import { useProject } from '@/components/project';
import { Button, ErrorState, Loading } from '@/components/ui';
import { api } from '@/lib/api';
import { useActionKeys } from '@/lib/action-key';
import {
  answerSetupQuestion,
  clearLegacySetupProgress,
  initialSetupProgress,
  progressFromServer,
  progressToServer,
  readLegacySetupProgress,
  resumeSetupQuestion,
  SETUP_SAVED_EVENT,
  type SetupProgress,
  type SetupQuestion,
} from '@/lib/setup-progress';
import { SCOPE_DURATION_DAYS, scopeBody, scopeStateFor } from '@/lib/real-assessment';
import { useResource } from '@/lib/use-resource';

const STRICTNESS = [
  [1, 'Thorough', 'Comment on every finding, including low severity.'],
  [2, 'Balanced', 'Comment on medium severity and above. Recommended.'],
  [3, 'Critical only', 'Comment only on high and critical findings.'],
] as const;
const DELIVERY: Array<[DeliveryMode, string, string]> = [
  ['pull_request', 'Open a pull request', 'Each verified fix arrives as a PR. You review and merge. Recommended.'],
  ['manual', 'Ask me first', 'Nothing is pushed until you approve each fix in Ari.'],
  ['auto', 'Commit to the default branch', 'Ari pushes verified fixes straight to your default branch with no human check.'],
];
const QUESTIONS = [
  ['Readiness', 'Check the repo and branch.'],
  ['PR-comment strictness', 'Be stricter for fewer comments.'],
  ['Automatic-review changes', 'Choose when reviews run.'],
  ['Pull-request comments', 'PR comments are optional.'],
  ['Project monitoring', 'Watching spots commits; testing needs approval.'],
  ['Sandbox testing', 'Approve temporary sandbox testing.'],
  ['Fix delivery', 'Auto-delivery needs confirmation.'],
  ['Review and start', 'Starting a check is optional.'],
] as const;
export default function SetupPage() {
  const { project, reloadProject } = useProject();
  const searchParams = useSearchParams();
  const repoParam = searchParams.get('repo');
  const repos = useResource(`setup-repos:${project.id}`, signal => api().repositories(project.id, { signal }));
  const scopes = useResource(`setup-scopes:${project.id}`, signal => api().scopes(project.id, { signal }));

  if (repos.status === 'loading' || scopes.status === 'loading') return <Loading label="Loading setup" />;
  if (repos.status === 'error') return <ErrorState error={repos.error} onRetry={repos.reload} />;
  if (scopes.status === 'error') return <ErrorState error={scopes.error} onRetry={scopes.reload} />;
  const repo = repos.data.items.find(item => item.id === repoParam)
    ?? repos.data.items.find(item => !item.disconnected)
    ?? repos.data.items[0];
  if (!repo) {
    return <div className="text-sm text-muted">No repository is connected yet. <Link className="font-semibold text-brand" href="/connect">Connect one</Link> to start setup.</div>;
  }
  if (repo.disconnected) {
    return (
      <div className="text-sm text-muted" data-testid="setup-disconnected">
        <span className="font-mono text-ink">{repo.full_name}</span> is disconnected, so Ari can&apos;t set it up.{' '}
        <Link className="font-semibold text-brand" href="/connect">Connect a repository</Link> to continue.
      </div>
    );
  }
  const scope = scopeStateFor(scopes.data.items, repo.id, new Date());
  return (
    <SavedWalkthrough
      key={repo.id}
      repo={repo}
      scope={scope}
      reload={() => { scopes.reload(); reloadProject(); }}
    />
  );
}

function SavedWalkthrough(props: { repo: Repository; scope: ReturnType<typeof scopeStateFor>; reload: () => void }) {
  const { project } = useProject();
  const setup = useResource(`setup-progress:${props.repo.id}`, signal => api().repositorySetup(props.repo.id, { signal }));
  // Filtered on the server: another repository's newer runs can't hide this one's.
  const runs = useResource(`setup-runs:${project.id}:${props.repo.id}`, signal => api().assessments(project.id, { signal, repoId: props.repo.id }));
  if (setup.status === 'loading' || runs.status === 'loading') return <Loading label="Loading saved setup" />;
  if (setup.status === 'error') return <ErrorState error={setup.error} onRetry={setup.reload} context="Could not load saved setup" />;
  if (runs.status === 'error') return <ErrorState error={runs.error} onRetry={runs.reload} />;
  return <Walkthrough {...props} hasRun={runs.data.items.length > 0} reload={() => { runs.reload(); props.reload(); }} saved={setup.data} />;
}

function Walkthrough({
  repo,
  scope,
  hasRun,
  reload,
  saved,
}: {
  repo: Repository;
  scope: ReturnType<typeof scopeStateFor>;
  hasRun: boolean;
  reload: () => void;
  saved: RepositorySetup;
}) {
  const { project, reloadProject } = useProject();
  const scopeKeys = useActionKeys('setup-scope');
  const runKeys = useActionKeys('setup-run');
  const [initial] = useState(() => initialSetupProgress(progressFromServer(saved), readLegacySetupProgress(project.id, repo.id)));
  const [progress, setProgress] = useState<SetupProgress>(initial.progress);
  const [questionOverride, setQuestionOverride] = useState<number | null>(null);
  const currentQuestion = questionOverride ?? resumeSetupQuestion(progress);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [runError, setRunError] = useState<unknown>(null);
  // Finished setup opens on the completed view; it is never asked again (Back to setup reopens it).
  const [finished, setFinished] = useState(initial.progress.complete);
  const [startedAssessmentId, setStartedAssessmentId] = useState<string | null>(null);
  const review = project.settings.review;
  const [strictness, setStrictness] = useState(review.strictness);
  const [pushes, setPushes] = useState(review.review_pushes);
  const [pullRequests, setPullRequests] = useState(review.review_pull_requests);
  const [comments, setComments] = useState(review.comment_on_pull_requests);
  // Per repository: starts from this repository's own automatic switch.
  const [watch, setWatch] = useState(repo.automatic_assessments);
  const [delivery, setDelivery] = useState<DeliveryMode>(project.settings.delivery_mode);
  // Direct default-branch commits are off for pilot deployments; only show the choice already in effect.
  const deliveryChoices = DELIVERY.filter(([mode]) => mode !== 'auto' || project.settings.delivery_mode === 'auto');
  const authorized = scope.kind === 'active';
  const savedAnswer = progress.answers[QUESTION_KEYS[currentQuestion - 1] as SetupQuestion];
  const strictnessLabel = STRICTNESS.find(([value]) => value === strictness)?.[1] ?? 'Unknown';
  const deliveryLabel = DELIVERY.find(([mode]) => mode === project.settings.delivery_mode)?.[1] ?? project.settings.delivery_mode;

  const saveProgress = async (next: SetupProgress) => {
    const stored = progressFromServer(await api().saveRepositorySetup(repo.id, progressToServer(next)));
    setProgress(stored);
    clearLegacySetupProgress(project.id, repo.id);
    window.dispatchEvent(new Event(SETUP_SAVED_EVENT));
    return stored;
  };

  // Progress an earlier build kept only in this browser moves to the server once.
  const migrated = useRef(false);
  useEffect(() => {
    if (!initial.migrate || migrated.current) return;
    migrated.current = true;
    api().saveRepositorySetup(repo.id, progressToServer(initial.progress)).then(
      stored => {
        setProgress(current => (current === initial.progress ? progressFromServer(stored) : current));
        clearLegacySetupProgress(project.id, repo.id);
        window.dispatchEvent(new Event(SETUP_SAVED_EVENT));
      },
      () => { /* kept locally; the next answer saves everything */ },
    );
  }, [initial, project.id, repo.id]);

  const recordAnswer = async (key: SetupQuestion, value: string | boolean) => {
    await saveProgress(answerSetupQuestion(progress, key, value));
    setQuestionOverride(Math.min(currentQuestion + 1, 8));
  };

  const persistSettings = async (settings: typeof project.settings) => {
    await api().updateProject(project.id, { name: project.name, settings });
    await reloadProject();
  };

  const act = async (label: string, run: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try { await run(); } catch (err) { setError(err); } finally { setBusy(null); }
  };

  const continueQuestion = () => act('save', async () => {
    switch (currentQuestion) {
      case 1:
        await recordAnswer('readiness', true);
        return;
      case 2:
        await persistSettings({ ...project.settings, review: { ...review, strictness } });
        await recordAnswer('strictness', String(strictness));
        return;
      case 3:
        await persistSettings({ ...project.settings, review: { ...review, strictness, review_pushes: pushes, review_pull_requests: pullRequests } });
        await recordAnswer('reviewChanges', true);
        return;
      case 4:
        await persistSettings({ ...project.settings, review: { ...review, strictness, review_pushes: pushes, review_pull_requests: pullRequests, comment_on_pull_requests: comments } });
        await recordAnswer('comments', comments);
        return;
      case 5:
        await persistSettings({
          ...project.settings,
          // Opting in needs project monitoring on; opting this repository out must not
          // switch it off for the project's other repositories.
          monitoring_policy: { ...project.settings.monitoring_policy, enabled: watch || project.settings.monitoring_policy.enabled },
          review: { ...review, strictness, review_pushes: pushes, review_pull_requests: pullRequests, comment_on_pull_requests: comments },
        });
        // Watching means this repository's commits are actually reviewed: the per-repository
        // automatic switch is the gate pushes check, so the answer always sets it (the
        // repository prop is a snapshot and can be stale after Back).
        await api().updateRepository(repo.id, { automatic_assessments: watch });
        window.dispatchEvent(new Event('ari:automation-changed'));
        await recordAnswer('watch', watch);
        return;
      case 6:
        if (!authorized) return;
        await recordAnswer('scope', true);
        return;
      case 7:
        if (delivery === 'auto' && !window.confirm('Ari will commit verified fixes straight to your default branch with no human check. Continue?')) return;
        await persistSettings({ ...project.settings, delivery_mode: delivery });
        await recordAnswer('delivery', delivery);
        return;
      default:
        await finishSetup();
    }
  });

  const requestScope = () => act('scope', async () => {
    const signature = `${project.id}|${repo.id}`;
    const body = scopeKeys.payloadFor(signature, () => scopeBody(repo.id, new Date()));
    const created = await api().createScope(project.id, body, { idempotencyKey: scopeKeys.keyFor(signature) });
    scopeKeys.done();
    await api().approveScope(created.id);
    reload();
  });
  const approveScope = (id: string) => act('scope', async () => { await api().approveScope(id); reload(); });

  const startAssessment = async () => {
    if (scope.kind !== 'active') return;
    setBusy('run');
    setRunError(null);
    try { await launchAssessment(scope.scope.id); } catch (err) { setRunError(err); } finally { setBusy(null); }
  };

  const launchAssessment = async (scopeId: string) => {
    const signature = `${project.id}|${repo.id}`;
    const assessment = await api().startAssessment(
      project.id,
      { scope_id: scopeId, repo_id: repo.id, commit_sha: null, snapshot_id: null, budget: null },
      runKeys.keyFor(signature),
    );
    runKeys.done();
    setStartedAssessmentId(assessment.id);
  };

  const finishSetup = async () => {
    const completed = { ...progress, answers: { ...progress.answers, reviewStart: true }, complete: true };
    await saveProgress(completed);
    setQuestionOverride(8);
    setFinished(true);
  };

  const goBack = () => {
    if (currentQuestion > 1) setQuestionOverride(currentQuestion - 1);
  };

  const scopeExpiry = scope.kind === 'none' ? null : scope.scope.expires_at;
  const scopeDate = scopeExpiry ? new Date(scopeExpiry).toLocaleDateString() : null;
  const scopeLabel = authorized ? 'Approved' : scope.kind === 'pending' ? 'Pending approval' : 'Not approved';
  const title = QUESTIONS[currentQuestion - 1]?.[0] ?? QUESTIONS[7]![0];
  const canContinue = currentQuestion !== 6 || authorized;

  return (
      <div className="grid w-full min-w-0 grid-cols-1 gap-6 pb-10 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-10 lg:pt-2">
        <nav aria-label="Setup progress">
          <p className="font-mono text-xs text-muted lg:hidden">Question {currentQuestion} of 8 · {title}</p>
          <div role="progressbar" aria-label={`Question ${currentQuestion} of 8`} aria-valuemin={1} aria-valuemax={8} aria-valuenow={currentQuestion} className="mt-2 h-px bg-panel-2 lg:sr-only">
            <span className="block h-px bg-brand" style={{ width: `${currentQuestion / 8 * 100}%` }} />
          </div>
          <ol className="hidden space-y-1 lg:block">
            {QUESTIONS.map(([label], index) => {
              const number = index + 1;
              const done = finished || progress.answers[QUESTION_KEYS[index] as SetupQuestion] !== undefined;
              return (
                <li key={label} aria-current={!finished && currentQuestion === number ? 'step' : undefined} className={`flex items-center gap-3 border-l-2 px-3 py-2 text-sm ${!finished && currentQuestion === number ? 'border-brand bg-brand/10 text-ink' : done ? 'border-line-3 text-muted' : 'border-transparent text-faint'}`}>
                  <span className="w-6 shrink-0 font-mono text-[10px] tabular-nums">{String(number).padStart(2, '0')}</span>
                  <span>{label}</span>
                  {done && <span className="ml-auto font-mono text-[10px] text-muted">SAVED</span>}
                </li>
              );
            })}
          </ol>
        </nav>

        <div className="min-w-0">
          <p className="label-mono text-muted">Setup / {repo.full_name}</p>
          <h1 className="setup-title mt-1 min-w-0 max-w-full whitespace-normal break-words text-2xl font-semibold tracking-tight sm:text-3xl">Let&apos;s set up Ari for {repo.full_name.split('/')[1]}</h1>
          <p className="mt-2 mb-6 text-sm text-muted">Eight questions. You can change these choices later in Settings.</p>
          {error !== null && <div className="mb-4"><ErrorState error={error} /></div>}
          {finished ? (
            <SetupDone projectId={project.id} repoId={repo.id} onBack={() => setFinished(false)} />
          ) : (
          <section data-testid={`setup-step-${currentQuestion}`} className="rounded-xl border border-line bg-panel p-5 sm:p-6 motion-safe:animate-[setup-slide-in_250ms_ease-out]">
            <p className="label-mono text-muted">Question {currentQuestion} / 8 {savedAnswer !== undefined && <span role="status" className="ml-2 text-ok">Saved</span>}</p>
            <h2 className="mt-1 text-xl font-semibold tracking-tight">{title}</h2>
            <p className="mt-1 text-sm text-muted">{QUESTIONS[currentQuestion - 1]![1]}</p>
            <div className="mt-5">
              {currentQuestion === 1 && (
                <>
                  <p className="mb-4 text-sm text-muted">Read-only repository readiness. Review the connected repository, branch, GitHub App installation, and saved build-recipe status.</p>
                  <dl className="grid gap-4 sm:grid-cols-2">
                    <div><dt className="label-mono text-muted">Repository</dt><dd className="mt-1 break-all font-mono text-sm text-ink">{repo.full_name}</dd></div>
                    <div><dt className="label-mono text-muted">Default branch</dt><dd className="mt-1 font-mono text-sm text-ink">{repo.default_branch}</dd></div>
                    <div className="sm:col-span-2"><dt className="label-mono text-muted">GitHub App</dt><dd className="mt-1 text-sm text-muted">{repo.installation_id != null ? `GitHub App installation #${repo.installation_id}` : 'Not connected through the GitHub App'}</dd></div>
                  </dl>
                  <div className="mt-5 border-t border-line pt-4">
                    <p className="label-mono mb-2 text-muted">Build recipe</p>
                    <BuildRecipeReadiness projectId={project.id} repo={repo} />
                  </div>
                </>
              )}
              {currentQuestion === 2 && (
                <fieldset className="grid gap-3">
                  <legend className="mb-2 text-sm font-medium">How strict should Ari be when it comments on pull requests?</legend>
                  {STRICTNESS.map(([value, label, hint]) => (
                    <label key={value} className="flex cursor-pointer items-start gap-3 border-b border-line py-3 text-sm last:border-0">
                      <input type="radio" name="strictness" checked={strictness === value} onChange={() => setStrictness(value)} />
                      <span><span className="block font-semibold">{label}</span><span className="mt-1 block text-xs text-muted">{hint}</span></span>
                    </label>
                  ))}
                </fieldset>
              )}
              {currentQuestion === 3 && (
                <fieldset>
                  <legend className="mb-3 text-sm font-medium">Which changes may trigger automatic reviews?</legend>
                  <div className="divide-y divide-line">
                    <label className="flex items-center gap-3 py-3 text-sm"><input type="checkbox" checked={pushes} onChange={event => setPushes(event.target.checked)} />Pushes to any branch</label>
                    <label className="flex items-center gap-3 py-3 text-sm"><input type="checkbox" checked={pullRequests} onChange={event => setPullRequests(event.target.checked)} />Pull requests</label>
                  </div>
                  <p className="mt-3 text-xs text-muted">Automatic reviews are opt-in per repository; these choices only select eligible change types.</p>
                </fieldset>
              )}
              {currentQuestion === 4 && (
                <fieldset>
                  <legend className="mb-3 text-sm font-medium">Should Ari leave review comments on pull requests?</legend>
                  <div className="divide-y divide-line">
                    {[[true, 'Yes, leave review comments'], [false, 'No, keep findings in Ari']] .map(([value, label]) => (
                      <label key={String(value)} className="flex items-center gap-3 py-3 text-sm"><input type="radio" name="comments" checked={comments === value} onChange={() => setComments(value as boolean)} />{label}</label>
                    ))}
                  </div>
                  <p className="mt-3 text-xs text-muted">Comment strictness is set in the previous question.</p>
                </fieldset>
              )}
              {currentQuestion === 5 && (
                <fieldset>
                  <legend className="mb-3 text-sm font-medium">Should Ari review new commits on this repository automatically?</legend>
                  <p className="mb-3 text-sm text-muted">Monitoring can notice changes, but automatic testing still requires an approved scope for this repository.</p>
                  <div className="divide-y divide-line">
                    {[[true, 'Yes, watch for commits'], [false, 'No, do not watch yet']] .map(([value, label]) => (
                      <label key={String(value)} className="flex items-center gap-3 py-3 text-sm"><input type="radio" name="watch" checked={watch === value} onChange={() => setWatch(value as boolean)} />{label}</label>
                    ))}
                  </div>
                </fieldset>
              )}
              {currentQuestion === 6 && (
                <>
                  <p className="text-sm text-muted">Approve isolated sandbox testing for <span className="font-mono text-ink">{repo.full_name}</span>. This scope expires after {SCOPE_DURATION_DAYS} days and can be revoked in Authorized scopes.</p>
                  <dl className="mt-4 grid gap-3 border-y border-line py-4 text-sm sm:grid-cols-2">
                    <div><dt className="label-mono text-muted">Target</dt><dd className="mt-1 break-words font-mono text-ink">{repo.full_name}</dd></div>
                    <div><dt className="label-mono text-muted">Duration</dt><dd className="mt-1 text-muted">{scopeDate ? <>Until <time dateTime={scopeExpiry!}>{scopeDate}</time></> : `${SCOPE_DURATION_DAYS} days`}</dd></div>
                  </dl>
                  {authorized ? <p className="mt-4 text-sm text-info">Testing authorized{scope.scope.expires_at ? <> until <time dateTime={scope.scope.expires_at}>{new Date(scope.scope.expires_at).toLocaleString()}</time></> : ''}.</p> :
                    scope.kind === 'pending'
                      ? <Button variant="primary" className="mt-4" disabled={busy !== null} onClick={() => void approveScope(scope.scope.id)}>{busy === 'scope' ? 'Approving…' : scopeDate ? `Approve sandbox testing until ${scopeDate}` : 'Approve pending sandbox testing'}</Button>
                      : <Button variant="primary" className="mt-4" disabled={busy !== null} onClick={() => void requestScope()}>{busy === 'scope' ? 'Approving…' : `Approve sandbox testing for ${SCOPE_DURATION_DAYS} days`}</Button>}
                </>
              )}
              {currentQuestion === 7 && (
                <fieldset>
                  <legend className="mb-3 text-sm font-medium">How should Ari deliver verified fixes?</legend>
                  <div className="divide-y divide-line">
                    {deliveryChoices.map(([mode, label, hint]) => (
                      <label key={mode} className="flex cursor-pointer items-start gap-3 py-3 text-sm">
                        <input type="radio" name="delivery" checked={delivery === mode} onChange={() => setDelivery(mode)} />
                        <span><span className="block font-semibold">{label}</span><span className="mt-1 block text-xs text-muted">{hint}</span></span>
                      </label>
                    ))}
                  </div>
                  <p className="mt-4 border-t border-line pt-4 text-sm text-muted"><strong className="text-ink">Independent verification.</strong> Verify replays the exploit and tests on a fresh checkout before any fix is offered.</p>
                </fieldset>
              )}
              {currentQuestion === 8 && (
                <>
                  <p className="mb-4 text-sm text-muted">Review these settings before Ari starts its first assessment.</p>
                  <dl className="divide-y divide-line border-y border-line">
                    <Summary label="Comment strictness" value={strictnessLabel} />
                    <Summary label="Review pushes" value={pushes ? 'Yes' : 'No'} />
                    <Summary label="Review pull requests" value={pullRequests ? 'Yes' : 'No'} />
                    <Summary label="Leave PR comments" value={comments ? 'Yes' : 'No'} />
                    <Summary label="Review new commits automatically" value={watch ? 'Yes' : 'No'} />
                    <Summary label="Testing scope" value={scopeLabel} />
                    <Summary label="Fix delivery" value={deliveryLabel} />
                  </dl>
                  <div className="mt-4"><BuildRecipeReadiness projectId={project.id} repo={repo} compact /></div>
                  {hasRun && <p className="mt-4 text-sm"><Link className="font-semibold text-brand" href={`/projects/${project.id}/commits?repo=${encodeURIComponent(repo.id)}#assessments`}>See your reviews</Link></p>}
                  <div className="mt-5 flex flex-wrap items-center gap-3">
                    <Button variant="primary" disabled={busy !== null || !authorized} title={authorized ? undefined : 'Approve sandbox testing first'} onClick={() => void startAssessment()}>{busy === 'run' ? 'Starting…' : hasRun ? 'Start another assessment' : 'Start first assessment'}</Button>
                    <span className="text-xs text-muted">{authorized ? 'Starting is optional; you can finish setup if a start fails.' : 'Approve sandbox testing in question 6 to start an assessment.'}</span>
                  </div>
                  {runError !== null && (
                    <div className="mt-3 flex flex-col gap-2" data-testid="setup-start-failed">
                      <ErrorState error={runError} onRetry={() => void startAssessment()} context="The first assessment did not start" />
                      <p className="text-sm text-muted">
                        Your setup choices are saved. You can retry, re-check GitHub access from{' '}
                        <Link className="font-semibold text-brand" href="/connect">Organizations → Refresh from GitHub</Link>, or finish setup and start a review later from{' '}
                        <Link className="font-semibold text-brand" href={`/projects/${project.id}/commits?repo=${encodeURIComponent(repo.id)}#start-assessment`}>Commits</Link>.
                      </p>
                    </div>
                  )}
                  {startedAssessmentId && <p role="status" className="mt-3 text-sm text-ok">Assessment started. <Link className="font-semibold text-brand" href={`/projects/${project.id}/assessments/${startedAssessmentId}?repo=${encodeURIComponent(repo.id)}`}>Open assessment</Link></p>}
                </>
              )}
            </div>
            <div className="mt-5 flex items-center justify-between gap-3">
              <Button disabled={currentQuestion === 1 || busy !== null} onClick={goBack}>Back</Button>
              {currentQuestion < 8
                ? <Button variant="primary" disabled={busy !== null || !canContinue} onClick={() => void continueQuestion()}>{busy === 'save' ? 'Saving…' : 'Continue'}</Button>
                : <Button variant="primary" disabled={busy !== null} onClick={() => void act('save', finishSetup)}>Finish setup</Button>}
            </div>
          </section>
          )}
        </div>
      </div>
  );
}

const QUESTION_KEYS = ['readiness', 'strictness', 'reviewChanges', 'comments', 'watch', 'scope', 'delivery', 'reviewStart'] as const;

function Summary({ label, value }: { label: string; value: string }) {
  return <div className="flex items-center justify-between gap-3 py-3 text-sm"><dt className="text-muted">{label}</dt><dd className="text-right text-ink">{value}</dd></div>;
}
