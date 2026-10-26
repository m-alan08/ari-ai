'use client';

import type {
  ApiError,
  OnboardingStep,
  OnboardingTestEventResult,
} from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useState, type ReactNode } from 'react';
import { useProject } from '@/components/project';
import { Async, Button, Mono, Panel, Tag } from '@/components/ui';
import { api } from '@/lib/api';
import type { Tone } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

const LABELS: Record<OnboardingStep['id'], string> = {
  sign_in: 'Sign in',
  organization: 'Organization & project',
  repository: 'Connect repository',
  github_access: 'GitHub access',
  snapshot: 'Repository snapshot',
  scope: 'Authorized scope',
  application: 'Staging application',
  deploy_mapping: 'Deployment mapping',
  monitoring: 'Monitoring (read-only)',
  test_event: 'Authorized test event',
  evidence: 'Investigation evidence',
};

const STATE_TONE: Record<OnboardingStep['state'], Tone> = {
  done: 'ok',
  todo: 'neutral',
  in_progress: 'info',
  blocked: 'warn',
  failed: 'bad',
};

const STATE_LABEL: Record<OnboardingStep['state'], string> = {
  done: 'Done',
  todo: 'To do',
  in_progress: 'In progress',
  blocked: 'Waiting',
  failed: 'Failed',
};

const PERMISSIONS: Array<{ name: string; why: string }> = [
  {
    name: 'GitHub App — contents:read, metadata:read',
    why: 'Ari reads the commits and code you connect. Required for review and snapshots.',
  },
  {
    name: 'GitHub App — pull requests (write)',
    why: 'Only used to open a fix pull request when you choose PR delivery. Never pushed silently.',
  },
  {
    name: 'Ingest token (hkt_ing_…)',
    why: 'Write-only credential your staging reporter uses to send runtime counters. Shown once; rotate or revoke anytime.',
  },
  {
    name: 'Authorized scope approval',
    why: 'Testing stays inside the sandbox targets you approve. Another org owner approves non-sandbox scopes.',
  },
  {
    name: 'No autonomous remediation',
    why: 'Shadow investigation produces recommendations only. Nothing executes on your systems.',
  },
];

function errorText(e: unknown): string {
  if (e && typeof e === 'object' && 'error' in e) {
    const apiErr = e as ApiError;
    return `${apiErr.error.message}${apiErr.status ? ` (${apiErr.status})` : ''}`;
  }
  return e instanceof Error ? e.message : 'Something went wrong';
}

export default function OnboardingPage() {
  const { project, repos } = useProject();
  const status = useResource(`onboarding:${project.id}`, s => api().onboardingStatus(project.id, { signal: s }), 8000);
  const done = status.status === 'ready' ? status.data.steps.filter(s => s.state === 'done').length : 0;
  const total = status.status === 'ready' ? status.data.steps.length : 0;
  return (
    <div className="flex flex-col gap-4">
      <header>
        <p className="label-mono text-faint">Customer onboarding</p>
        <h2 className="mt-1 text-xl font-semibold tracking-tight" data-testid="onboarding-title">
          Set up {project.name} for its first investigation
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Each step below is read from what is actually configured — nothing is marked done
          until the backend sees it. Progress: {done} of {total} steps.
        </p>
      </header>
      <Panel title="What Ari asks for — and why" actions={<Tag tone="info">read-only by default</Tag>}>
        <ul className="flex flex-col gap-2" data-testid="permission-list">
          {PERMISSIONS.map(p => (
            <li key={p.name} className="text-sm">
              <span className="font-medium">{p.name}</span>
              <span className="text-muted"> — {p.why}</span>
            </li>
          ))}
        </ul>
      </Panel>
      <Async resource={status} label="onboarding status">
        {data => (
          <ol className="flex flex-col gap-3" data-testid="onboarding-steps">
            {data.steps.map((step, i) => (
              <StepRow
                key={step.id}
                index={i + 1}
                step={step}
                projectId={project.id}
                repoId={repos[0]?.id}
                onChanged={status.reload}
              />
            ))}
          </ol>
        )}
      </Async>
      <DisconnectPanel projectId={project.id} repoId={repos[0]?.id} onChanged={status.reload} />
    </div>
  );
}

function StepRow({
  index,
  step,
  projectId,
  repoId,
  onChanged,
}: {
  index: number;
  step: OnboardingStep;
  projectId: string;
  repoId: string | undefined;
  onChanged: () => void;
}) {
  const action = actionFor(step, projectId, repoId, onChanged);
  return (
    <li
      className="rounded-xl border border-line bg-panel p-4"
      data-testid={`onboarding-step-${step.id}`}
      data-state={step.state}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="label-mono mt-0.5 text-faint">{index}</span>
          <div>
            <p className="text-sm font-medium">{LABELS[step.id] ?? step.id}</p>
            <p className="mt-0.5 max-w-xl text-sm text-muted">{step.detail}</p>
          </div>
        </div>
        <Tag tone={STATE_TONE[step.state]}>{STATE_LABEL[step.state]}</Tag>
      </div>
      {action ? <div className="mt-3">{action}</div> : null}
    </li>
  );
}

function actionFor(
  step: OnboardingStep,
  projectId: string,
  repoId: string | undefined,
  onChanged: () => void,
): ReactNode {
  const base = `/projects/${projectId}`;
  switch (step.id) {
    case 'repository':
      if (step.state === 'todo')
        return <Link className="text-sm text-brand underline" href="/connect">Connect a repository</Link>;
      return null;
    case 'github_access':
      if (step.state === 'todo')
        return <Link className="text-sm text-brand underline" href="/connect">Install the Ari GitHub App</Link>;
      return null;
    case 'snapshot':
      if (step.state === 'todo' || step.state === 'failed')
        return repoId ? <SnapshotForm repoId={repoId} onDone={onChanged} /> : null;
      return null;
    case 'scope':
      if (step.state !== 'done')
        return <Link className="text-sm text-brand underline" href={`${base}/scopes`}>Request or approve a scope</Link>;
      return null;
    case 'application':
      if (step.state !== 'done')
        return <Link className="text-sm text-brand underline" href={`${base}/live-defense/apps`}>Open staging applications</Link>;
      return null;
    case 'deploy_mapping':
      if (step.state !== 'done')
        return <Link className="text-sm text-brand underline" href={`${base}/live-defense/first-responder`}>Attest the deploy-to-commit mapping</Link>;
      return null;
    case 'monitoring':
      if (step.state === 'todo')
        return <Link className="text-sm text-brand underline" href={`${base}/monitoring`}>Open monitoring</Link>;
      return null;
    case 'test_event':
      // Keep mounted when done so the recorded result survives the status refetch;
      // owners may also send further authorized test events.
      if (step.state === 'todo' || step.state === 'done')
        return <TestEventButton projectId={projectId} onDone={onChanged} />;
      return null;
    case 'evidence':
      if (step.state === 'done')
        return <Link className="text-sm text-brand underline" href={`${base}/incidents`}>View investigation records</Link>;
      return null;
    default:
      return null;
  }
}

function SnapshotForm({ repoId, onDone }: { repoId: string; onDone: () => void }) {
  const [sha, setSha] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={event => {
        event.preventDefault();
        setBusy(true);
        setError(null);
        api()
          .createSnapshot(repoId, { commit_sha: sha.trim() }, `onb-${repoId}-${sha.trim()}`)
          .then(() => onDone(), e => setError(errorText(e)))
          .finally(() => setBusy(false));
      }}
    >
      <label className="text-sm text-muted" htmlFor="onb-snapshot-sha">Commit SHA on your default branch</label>
      <input
        id="onb-snapshot-sha"
        className="max-w-full rounded-md border border-control bg-bg px-2.5 py-1.5 font-mono text-sm"
        placeholder="40-char commit SHA"
        value={sha}
        onChange={e => setSha(e.target.value)}
        required
        minLength={40}
        maxLength={40}
      />
      <Button type="submit" variant="primary" disabled={busy} data-testid="onb-snapshot-submit">
        {busy ? 'Requesting…' : 'Request snapshot'}
      </Button>
      {error ? <p className="text-sm text-bad" role="alert">{error}</p> : null}
    </form>
  );
}

function TestEventButton({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OnboardingTestEventResult | null>(null);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={busy}
          data-testid="onb-test-event"
          onClick={() => {
            setBusy(true);
            setError(null);
            api()
              .onboardingTestEvent(projectId, {
                application_id: null,
                route: 'GET /ari/onboarding-test',
                requests: 100,
                status_4xx: 80,
                auth_failures: 80,
              })
              .then(r => {
                setResult(r);
                onDone();
              }, e => setError(errorText(e)))
              .finally(() => setBusy(false));
          }}
        >
          {busy ? 'Sending…' : 'Send authorized test event'}
        </Button>
        <span className="text-xs text-muted">
          Synthetic auth-failure spike through your app&apos;s real ingest channel. Scored and
          recorded — nothing executes.
        </span>
      </div>
      {result ? (
        <p className="text-sm" data-testid="onb-test-event-result">
          Accepted: {result.anomalies} {result.anomalies === 1 ? 'anomaly' : 'anomalies'} recorded at{' '}
          <Mono>{result.window_end}</Mono>
          {result.monitoring_event_ids.length
            ? ` · ${result.monitoring_event_ids.length} monitoring event(s)`
            : ''}
          .
        </p>
      ) : null}
      {error ? <p className="text-sm text-bad" role="alert" data-testid="onb-test-event-error">{error}</p> : null}
    </div>
  );
}

function DisconnectPanel({
  projectId,
  repoId,
  onChanged,
}: {
  projectId: string;
  repoId: string | undefined;
  onChanged: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = `/projects/${projectId}`;
  return (
    <Panel title="Revoke access" actions={<Tag tone="warn">takes effect immediately</Tag>}>
      <p className="text-sm text-muted">
        Disconnecting the repository stops Ari&apos;s mediated access — push-triggered monitoring
        ends and every deploy-to-commit mapping is revoked. It does not remove the GitHub App
        grant: uninstalling or limiting the Ari App on GitHub is your action and is not claimed
        here. Disconnecting an application revokes its ingest tokens and stops telemetry.
        History is kept; access is removed.
      </p>
      {done ? (
        <p className="mt-3 text-sm" data-testid="onb-disconnect-repo-done">
          Repository disconnected: Ari&apos;s mediated access stopped and deploy mappings were
          revoked. The GitHub App grant still stands — uninstall or limit the Ari App on GitHub
          to remove it.
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {repoId && !done ? (
          confirm ? (
            <>
              <Button
                variant="danger"
                disabled={busy}
                data-testid="onb-disconnect-repo-confirm"
                onClick={() => {
                  setBusy(true);
                  setError(null);
                  api()
                    .disconnectRepository(repoId)
                    .then(() => {
                      setConfirm(false);
                      setDone(true);
                      onChanged();
                    }, e => setError(errorText(e)))
                    .finally(() => setBusy(false));
                }}
              >
                {busy ? 'Disconnecting…' : 'Confirm disconnect repository'}
              </Button>
              <Button variant="ghost" onClick={() => setConfirm(false)}>Keep connected</Button>
            </>
          ) : (
            <Button variant="danger" data-testid="onb-disconnect-repo" onClick={() => setConfirm(true)}>
              Disconnect repository
            </Button>
          )
        ) : (
          <span className="text-sm text-faint">No repository connected.</span>
        )}
        <Link className="text-sm text-brand underline" href={`${base}/live-defense/apps`}>
          Manage staging applications
        </Link>
      </div>
      {error ? <p className="mt-2 text-sm text-bad" role="alert">{error}</p> : null}
    </Panel>
  );
}
