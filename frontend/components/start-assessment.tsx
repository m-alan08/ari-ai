'use client';

import { ApiError } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type FormEvent } from 'react';
import { useProject } from '@/components/project';
import { Button, Empty, ErrorState, Field, inputClass, Mono, Panel } from '@/components/ui';
import { api, idempotencyKey } from '@/lib/api';
import { assessmentSelection } from '@/lib/assessment-selection';
import { START_ASSESSMENT_NOTE } from '@/lib/start-assessment';
import { effectiveScopeStatus } from '@/lib/status';
import { useResource } from '@/lib/use-resource';

const SHA = /^[0-9a-f]{7,40}$/;

export function StartAssessment({ onStarted }: { onStarted: () => void }) {
  const { project, selectedRepo } = useProject();
  const router = useRouter();
  const scopes = useResource(`scopes:${project.id}`, signal => api().scopes(project.id, { signal }));
  const [scopeId, setScopeId] = useState('');
  const [sha, setSha] = useState('');
  const [key, setKey] = useState<{ sig: string; value: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const active = scopes.status === 'ready' ? scopes.data.items.filter(scope => effectiveScopeStatus(scope) === 'active') : [];
  const selection = assessmentSelection(selectedRepo?.id ?? '', scopeId, selectedRepo ? [selectedRepo] : [], active);
  const conflict = error instanceof ApiError && error.status === 409 && error.error.code === 'IDEMPOTENCY_CONFLICT';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedRepo) return;
    const sig = `${project.id}|${selectedRepo.id}|${selection.scopeId}|${sha.trim()}`;
    const actionKey = key?.sig === sig ? key.value : idempotencyKey('assessment');
    setKey({ sig, value: actionKey });
    setBusy(true);
    setError(null);
    try {
      const assessment = await api().startAssessment(project.id, {
        scope_id: selection.scopeId,
        repo_id: selectedRepo.id,
        commit_sha: sha.trim() || null,
        snapshot_id: null,
        budget: null,
      }, actionKey);
      onStarted();
      router.push(`/projects/${project.id}/assessments/${assessment.id}?repo=${encodeURIComponent(selectedRepo.id)}`);
    } catch (err) { setError(err); } finally { setBusy(false); }
  };

  return (
    <Panel title="Start an assessment" id="start-assessment">
      <p className="mb-3 text-sm text-muted" data-testid="start-assessment-note">{START_ASSESSMENT_NOTE}</p>
      {!selectedRepo ? <Empty>Connect a repository first.</Empty> : (
        <>
          <p className="mb-3 text-sm text-muted">Repository <Mono className="text-ink">{selectedRepo.full_name}</Mono></p>
          <form onSubmit={submit} className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Authorized scope" hint={scopes.status === 'ready' && selection.scopes.length === 0
              ? 'No active scope covers this repository.'
              : undefined}>
              <select className={inputClass} value={selection.scopeId} onChange={event => setScopeId(event.target.value)} required name="scope">
                <option value="">Select…</option>
                {selection.scopes.map(scope => <option key={scope.id} value={scope.id}>{scope.name}</option>)}
              </select>
            </Field>
            {scopes.status === 'ready' && selection.scopes.length === 0 && (
              <p className="text-xs text-muted sm:col-span-3">Request and approve a scope under <Link className="text-brand hover:underline" href={`/projects/${project.id}/scopes?repo=${encodeURIComponent(selectedRepo.id)}`}>More → Authorized scopes</Link>.</p>
            )}
            <Field label="Commit SHA" hint="Blank = latest commit on the default branch.">
              <input className={`${inputClass} font-mono`} value={sha} onChange={event => setSha(event.target.value.toLowerCase())} pattern="[0-9a-f]{7,40}" name="commit_sha" placeholder="optional" />
            </Field>
            <div className="flex items-end">
              <Button type="submit" variant="primary" disabled={busy || !selection.scopeId || (sha !== '' && !SHA.test(sha))}>Start assessment</Button>
            </div>
            {error !== null && <div className="sm:col-span-3">
              <ErrorState error={error} context="Assessment not started" />
              {conflict && <div className="mt-2 text-sm">
                <p>Check the assessment list before starting a separate action. The previous key belongs to another request; retrying unchanged will keep returning this conflict.</p>
                <Button type="button" className="mt-2" disabled={busy} onClick={() => { setKey(null); setError(null); }}>
                  Prepare a new assessment action
                </Button>
                <p className="mt-1 text-xs text-muted">This only resets the action key. Press Start assessment to submit a new action; it may create another assessment.</p>
              </div>}
            </div>}
          </form>
        </>
      )}
    </Panel>
  );
}
