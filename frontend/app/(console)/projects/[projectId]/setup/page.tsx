'use client';

import type { DeliveryMode, Repository } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { BuildRecipeReadiness } from '@/components/onboarding/build-recipe-readiness';
import { SetupLayout } from '@/components/onboarding/stepper';
import { useProject } from '@/components/project';
import { RepositoryAutomation } from '@/components/repository-automation';
import { Button, ErrorState, Loading, Tag } from '@/components/ui';
import { api } from '@/lib/api';
import { useActionKeys } from '@/lib/action-key';
import { SCOPE_DURATION_DAYS, scopeBody, scopeStateFor } from '@/lib/real-assessment';
import { useResource } from '@/lib/use-resource';

const STRICTNESS: Array<[number, string, string]> = [
  [1, 'Thorough', 'Comment on every finding, including low severity.'],
  [2, 'Balanced', 'Comment on medium severity and above. Recommended.'],
  [3, 'Critical only', 'Comment only on high and critical findings.'],
];
const DELIVERY: Array<[DeliveryMode, string, string]> = [
  ['pull_request', 'Open a pull request', 'Each verified fix arrives as a PR. You review and merge. Recommended.'],
  ['manual', 'Ask me first', 'Nothing is pushed until you approve each fix in Ari.'],
  ['auto', 'Commit to the default branch', 'Ari pushes verified fixes straight to your default branch with no human check.'],
];

function stepFromParam(value: string | null): number | null {
  const step = Number(value);
  return Number.isInteger(step) && step >= 3 && step <= 7 ? step : null;
}

function savedSetupSteps(progressKey: string): string[] {
  return typeof window === 'undefined' ? [] : (window.localStorage.getItem(progressKey) ?? '').split(/\s+/).filter(Boolean);
}

function saveSetupStep(progressKey: string, step: string) {
  const saved = new Set(savedSetupSteps(progressKey));
  saved.add(step);
  window.localStorage.setItem(progressKey, [...saved].join(' '));
}

function StepPanel({ step, title, children }: { step: number; title: string; children: ReactNode }) {
  return (
    <section data-testid={`setup-step-${step}`} className="rounded-2xl border border-line bg-panel p-5 sm:p-6">
      <p className="label-mono text-muted">Step {step} / 7</p>
      <h2 className="mt-1 text-xl font-semibold tracking-tight">{title}</h2>
      <div className="mt-5">{children}</div>
    </section>
  );
}

/** Guided setup after connecting a repository: authorize, tune the reviewer, choose delivery, run. */
export default function SetupPage() {
  const { project, reloadProject } = useProject();
  const searchParams = useSearchParams();
  const repoParam = searchParams.get('repo');
  const repos = useResource(`setup-repos:${project.id}`, signal => api().repositories(project.id, { signal }));
  const scopes = useResource(`setup-scopes:${project.id}`, signal => api().scopes(project.id, { signal }));
  const runs = useResource(`setup-runs:${project.id}`, signal => api().assessments(project.id, { signal }));

  if (repos.status === 'loading' || scopes.status === 'loading' || runs.status === 'loading') return <Loading label="Loading setup" />;
  if (repos.status === 'error') return <ErrorState error={repos.error} onRetry={repos.reload} />;
  if (scopes.status === 'error') return <ErrorState error={scopes.error} onRetry={scopes.reload} />;
  if (runs.status === 'error') return <ErrorState error={runs.error} onRetry={runs.reload} />;
  const repo = repos.data.items.find(r => r.id === repoParam) ?? repos.data.items[0];
  if (!repo) {
    return <div className="rounded-2xl border border-line bg-panel p-5 text-sm sm:p-6">No repository is connected yet. <Link className="font-semibold text-brand" href="/connect">Connect one</Link> to start setup.</div>;
  }
  const scope = scopeStateFor(scopes.data.items, repo.id, new Date());
  const authorized = scope.kind === 'active';
  const hasRun = runs.data.items.some(a => a.repo_id === repo.id);
  return (
    <Walkthrough
      key={repo.id}
      repo={repo}
      scope={scope}
      authorized={authorized}
      hasRun={hasRun}
      reload={() => { scopes.reload(); runs.reload(); reloadProject(); }}
    />
  );
}

function Walkthrough({ repo, scope, authorized, hasRun, reload }: {
  repo: Repository; scope: ReturnType<typeof scopeStateFor>; authorized: boolean; hasRun: boolean; reload: () => void;
}) {
  const { project, reloadProject } = useProject();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const scopeKeys = useActionKeys('setup-scope');
  const scopeExpiresAt = scope.kind === 'none' ? null : scope.scope.expires_at;
  const scopeExpiryDate = scopeExpiresAt ? new Date(scopeExpiresAt).toLocaleDateString() : null;
  const runKeys = useActionKeys('setup-run');
  const review = project.settings.review;
  const progressKey = `ari-setup:${project.id}:${repo.id}`;
  const [readinessViewed, setReadinessViewed] = useState(() => savedSetupSteps(progressKey).includes('readiness'));
  const [reviewerSaved, setReviewerSaved] = useState(() => savedSetupSteps(progressKey).includes('reviewer'));
  const [deliverySaved, setDeliverySaved] = useState(() => savedSetupSteps(progressKey).includes('delivery'));
  const [strictness, setStrictness] = useState(review.strictness);
  const [pushes, setPushes] = useState(review.review_pushes);
  const [prs, setPrs] = useState(review.review_pull_requests);
  const [comments, setComments] = useState(review.comment_on_pull_requests);
  const [monitoring, setMonitoring] = useState(project.settings.monitoring_policy.enabled);
  const [delivery, setDelivery] = useState<DeliveryMode>(project.settings.delivery_mode === 'auto' ? 'pull_request' : project.settings.delivery_mode);
  const firstIncomplete = !readinessViewed ? 3 : !reviewerSaved ? 4 : !authorized ? 5 : !deliverySaved ? 6 : !hasRun ? 7 : 7;
  const [currentStep, setCurrentStep] = useState(() => stepFromParam(searchParams.get('step')) ?? firstIncomplete);
  const initialUrlHandled = useRef(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const writeStep = useCallback((step: number) => {
    router.replace(`${pathname}?repo=${encodeURIComponent(repo.id)}&step=${step}`, { scroll: false });
  }, [router, pathname, repo.id]);
  const navigateStep = (step: number) => {
    if (step < 3 || step > 7) return;
    if (step === 3 || currentStep === 3) {
      saveSetupStep(progressKey, 'readiness');
      setReadinessViewed(true);
    }
    setCurrentStep(step);
    writeStep(step);
  };

  useEffect(() => {
    if (!initialUrlHandled.current) {
      initialUrlHandled.current = true;
      if (stepFromParam(searchParams.get('step')) === null) writeStep(currentStep);
    }
  }, [searchParams, currentStep, writeStep]);

  useEffect(() => {
    if (currentStep === 3) saveSetupStep(progressKey, 'readiness');
  }, [currentStep, progressKey]);

  const act = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError(null);
    try { await fn(); } catch (err) { setError(err); } finally { setBusy(null); }
  };
  const save = (settings: typeof project.settings) => api().updateProject(project.id, { name: project.name, settings }).then(() => reloadProject());

  const requestScope = () => act('scope', async () => {
    const sig = `${project.id}|${repo.id}`;
    const body = scopeKeys.payloadFor(sig, () => scopeBody(repo.id, new Date()));
    const created = await api().createScope(project.id, body, { idempotencyKey: scopeKeys.keyFor(sig) });
    scopeKeys.done();
    await api().approveScope(created.id);
    reload();
  });
  const approveScope = (id: string) => act('scope', async () => { await api().approveScope(id); reload(); });
  const saveReviewer = () => act('reviewer', async () => {
    await save({
      ...project.settings,
      monitoring_policy: { ...project.settings.monitoring_policy, enabled: monitoring },
      review: { ...review, strictness, review_pushes: pushes, review_pull_requests: prs, comment_on_pull_requests: comments },
    });
    setReviewerSaved(true);
    saveSetupStep(progressKey, 'reviewer');
  });
  const saveDelivery = () => act('delivery', async () => {
    if (delivery === 'auto' && !window.confirm('Ari will commit verified fixes straight to your default branch with no human check. Continue?')) return;
    await save({ ...project.settings, delivery_mode: delivery });
    setDeliverySaved(true);
    saveSetupStep(progressKey, 'delivery');
  });
  const run = () => act('run', async () => {
    if (scope.kind !== 'active') return;
    const sig = `${project.id}|${repo.id}`;
    const asm = await api().startAssessment(project.id, { scope_id: scope.scope.id, repo_id: repo.id, commit_sha: null, snapshot_id: null, budget: null }, runKeys.keyFor(sig));
    runKeys.done();
      router.push(`/projects/${project.id}/assessments/${asm.id}?repo=${encodeURIComponent(repo.id)}`);
  });

  const doneSteps = [
    1,
    2,
    ...(readinessViewed ? [3] : []),
    ...(reviewerSaved ? [4] : []),
    ...(authorized ? [5] : []),
    ...(deliverySaved ? [6] : []),
    ...(hasRun ? [7] : []),
  ];
  const org = repo.full_name.split('/')[0] ?? '';
  const deliveryLabel = DELIVERY.find(([mode]) => mode === project.settings.delivery_mode)?.[1] ?? project.settings.delivery_mode;
  const strictnessLabel = STRICTNESS.find(([value]) => value === project.settings.review.strictness)?.[1] ?? 'Unknown';
  const scopeLabel = scope.kind === 'active' ? 'Active' : scope.kind === 'pending' ? 'Pending approval' : 'Not authorized';

  return (
    <SetupLayout current={currentStep} org={org} doneSteps={doneSteps} onStepChange={navigateStep}>
    <div className="mx-auto min-w-0 max-w-3xl py-4" data-testid="repo-setup">
        <p className="label-mono text-muted">Setup / {repo.full_name}</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">Let&apos;s set up Ari for {repo.full_name.split('/')[1]}</h1>
        <p className="mt-2 mb-6 text-sm text-muted">Seven steps. You can change any of this later in Settings.</p>
        {error !== null && <div className="mb-4"><ErrorState error={error} /></div>}

        {currentStep === 3 && (
          <StepPanel step={3} title="Readiness">
            <dl className="grid gap-4 sm:grid-cols-2">
              <div><dt className="label-mono text-muted">Repository</dt><dd className="mt-1 break-all font-mono text-sm text-ink">{repo.full_name}</dd></div>
              <div><dt className="label-mono text-muted">Default branch</dt><dd className="mt-1 font-mono text-sm text-ink">{repo.default_branch}</dd></div>
              <div className="sm:col-span-2"><dt className="label-mono text-muted">GitHub App</dt><dd className="mt-1 text-sm text-muted">{repo.installation_id != null ? `GitHub App installation #${repo.installation_id}` : 'Not connected through the GitHub App'}</dd></div>
            </dl>
            <div className="mt-5 border-t border-line pt-4">
              <p className="label-mono mb-2 text-muted">Build recipe</p>
              <BuildRecipeReadiness projectId={project.id} repo={repo} />
            </div>
          </StepPanel>
        )}

        {currentStep === 4 && (
          <StepPanel step={4} title="Review behavior">
            <ul className="space-y-3 border-b border-line pb-4 text-sm">
              <li><strong>Manual reviews.</strong> You start each one — pilot default.</li>
              <li><strong>Automatic reviews.</strong> Opt-in per repository, off by default; manage this in Repository automation below.</li>
              <li><strong>Functional issues.</strong> Review findings describe behavior that does not match the code&apos;s intended outcomes.</li>
              <li><strong>Security hypotheses.</strong> <Tag tone="warn">FLAGGED</Tag> until a check runs.</li>
              <li><strong>Executed validation.</strong> <Tag tone="ok">VALIDATED</Tag> only after Probe reproduces it in the sandbox.</li>
            </ul>
            <fieldset className="mt-4 grid gap-2 sm:grid-cols-3">
              <legend className="mb-2 text-sm font-medium">How strict should PR comments be?</legend>
              {STRICTNESS.map(([value, label, hint]) => (
                <label key={value} className={`cursor-pointer rounded-xl border p-3 text-sm transition-colors ${strictness === value ? 'border-brand bg-brand/10' : 'border-line bg-panel-2 hover:border-brand'}`}>
                  <input type="radio" name="strictness" className="sr-only" checked={strictness === value} onChange={() => setStrictness(value)} />
                  <span className="block font-semibold">{label}</span><span className="mt-1 block text-xs text-muted">{hint}</span>
                </label>
              ))}
            </fieldset>
            <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
              <Check checked={monitoring} onChange={setMonitoring} label="Watch this project for new commits" />
              <Check checked={pushes} onChange={setPushes} label="Review pushes to the default branch" />
              <Check checked={prs} onChange={setPrs} label="Review pull requests" />
              <Check checked={comments} onChange={setComments} label="Leave a review comment on pull requests" />
            </div>
            <div className="mt-4"><RepositoryAutomation repoId={repo.id} /></div>
            <p className="mt-3 text-xs text-muted">Every review, automatic or manual, counts toward this project&apos;s limit of 10 per hour and 50 per day. Branch and author filters are in Settings.</p>
            <Button variant="primary" className="mt-4" disabled={busy !== null} onClick={() => void saveReviewer()}>{busy === 'reviewer' ? 'Saving…' : reviewerSaved ? 'Saved' : 'Save reviewer settings'}</Button>
          </StepPanel>
        )}

        {currentStep === 5 && (
          <StepPanel step={5} title="Testing scope">
            <p className="text-sm text-muted">Authorize Ari to test this repository in an isolated sandbox.</p>
            <dl className="mt-4 grid gap-3 border-y border-line py-4 text-sm sm:grid-cols-2">
              <div><dt className="label-mono text-muted">Target</dt><dd className="mt-1 break-words font-mono text-ink">{repo.full_name}</dd></div>
              <div><dt className="label-mono text-muted">Sandbox build</dt><dd className="mt-1 text-muted">Built from <code className="font-mono text-ink">build_recipe.json</code></dd></div>
              <div><dt className="label-mono text-muted">Duration</dt><dd className="mt-1 text-muted">{scopeExpiresAt && scopeExpiryDate ? <>Until <time dateTime={scopeExpiresAt}>{scopeExpiryDate}</time></> : scope.kind === 'none' ? `${SCOPE_DURATION_DAYS} days` : 'Expiry not recorded'}</dd></div>
              <div><dt className="label-mono text-muted">Revocation</dt><dd className="mt-1 text-muted">Revoke anytime in <Link className="text-brand underline-offset-2 hover:underline" href={`/projects/${project.id}/scopes`}>Scopes</Link>.</dd></div>
            </dl>
            {scope.kind === 'active' && (
              <p className="mt-4 text-sm text-info">
                Testing authorized{scope.scope.expires_at ? <> until <time dateTime={scope.scope.expires_at}>{new Date(scope.scope.expires_at).toLocaleString()}</time></> : ''}.
              </p>
            )}
            {scope.kind === 'pending' && <Button variant="primary" className="mt-4" disabled={busy !== null} onClick={() => void approveScope(scope.scope.id)}>{busy === 'scope' ? 'Approving…' : scopeExpiryDate ? `Approve sandbox testing until ${scopeExpiryDate}` : 'Approve pending sandbox testing'}</Button>}
            {scope.kind !== 'active' && scope.kind !== 'pending' && <Button variant="primary" className="mt-4" disabled={busy !== null} onClick={() => void requestScope()}>{busy === 'scope' ? 'Approving…' : `Approve sandbox testing for ${SCOPE_DURATION_DAYS} days`}</Button>}
          </StepPanel>
        )}

        {currentStep === 6 && (
          <StepPanel step={6} title="Fix delivery">
            <p className="mb-4 text-sm text-muted">Choose where a verified fix should go. Pull requests are the default.</p>
            <div className="grid gap-2">
              {DELIVERY.map(([mode, label, hint]) => (
                <label key={mode} className={`cursor-pointer rounded-xl border p-3 text-sm transition-colors ${delivery === mode ? 'border-brand bg-brand/10' : 'border-line bg-panel-2 hover:border-brand'}`}>
                  <input type="radio" name="delivery" className="sr-only" checked={delivery === mode} onChange={() => setDelivery(mode)} />
                  <span className="block font-semibold">{label}</span><span className="mt-1 block text-xs text-muted">{hint}</span>
                </label>
              ))}
            </div>
            <p className="mt-4 border-t border-line pt-4 text-sm text-muted"><strong className="text-ink">Independent verification.</strong> Verify replays the exploit and tests on a fresh checkout before any fix is offered.</p>
            <Button variant="primary" className="mt-4" disabled={busy !== null} onClick={() => void saveDelivery()}>{busy === 'delivery' ? 'Saving…' : deliverySaved ? 'Saved' : 'Save delivery choice'}</Button>
          </StepPanel>
        )}

        {currentStep === 7 && (
          <StepPanel step={7} title="Review & start">
            <ul className="divide-y divide-line border-y border-line">
              <Checklist label="Automatic reviews" value={repo.automatic_assessments ? 'On' : 'Off — you start each review'} />
              <Checklist label="Comment strictness" value={strictnessLabel} />
              <Checklist label="Review on push (when automatic)" value={project.settings.review.review_pushes ? 'On' : 'Off'} />
              <Checklist label="Review on PR (when automatic)" value={project.settings.review.review_pull_requests ? 'On' : 'Off'} />
              <Checklist label="Review comments" value={project.settings.review.comment_on_pull_requests ? 'On' : 'Off'} />
              <Checklist label="Monitoring" value={project.settings.monitoring_policy.enabled ? 'On' : 'Off'} />
              <Checklist label="Fix delivery" value={deliveryLabel} />
              <Checklist label="Testing scope" value={scopeLabel} />
              <li className="flex items-center justify-between gap-3 py-3 text-sm"><span className="text-muted">Build recipe</span><BuildRecipeReadiness projectId={project.id} repo={repo} compact /></li>
            </ul>
            {!authorized && <button type="button" className="mt-4 text-sm text-brand underline-offset-2 hover:underline" onClick={() => navigateStep(5)}>Review step 5: testing scope</button>}
            {hasRun && <p className="mt-4 text-sm"><Link className="font-semibold text-brand" href={`/projects/${project.id}/commits?repo=${encodeURIComponent(repo.id)}#assessments`}>See your reviews</Link></p>}
            <div><Button variant="primary" className="mt-4" disabled={busy !== null || !authorized} title={authorized ? undefined : 'Approve sandbox testing first'} onClick={() => void run()}>{busy === 'run' ? 'Starting…' : hasRun ? 'Run another review' : 'Start first assessment'}</Button></div>
          </StepPanel>
        )}

        <div className="mt-5 flex items-center justify-between gap-3">
          {currentStep === 3 ? (
            <Link href={`/connect/${encodeURIComponent(org)}`} className="inline-flex h-8 items-center rounded-lg border border-line-2 px-3 text-xs font-medium text-ink hover:bg-panel-2">Back</Link>
          ) : (
            <Button onClick={() => navigateStep(currentStep - 1)}>Back</Button>
          )}
          {currentStep < 7 && <Button variant="primary" onClick={() => navigateStep(currentStep + 1)}>Continue</Button>}
        </div>
      </div>
    </SetupLayout>
  );
}

function Checklist({ label, value }: { label: string; value: string }) {
  return <li className="flex items-center justify-between gap-3 py-3 text-sm"><span className="text-muted">{label}</span><span className="text-right text-ink">{value}</span></li>;
}

function Check({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return <label className="flex items-center gap-2"><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />{label}</label>;
}
