'use client';

import type { Project } from '@hackatl/frontend-transport';
import { useState, type FormEvent } from 'react';
import { useProject } from '@/components/project';
import { Repositories } from '@/components/repositories';
import { Button, ErrorState, Field, inputClass, PageTitle, Panel } from '@/components/ui';
import { api } from '@/lib/api';
import { AUTO_WARNING, draftOf, parseSettings, type Draft } from '@/lib/settings';

export default function SettingsPage() {
  const { project, repos, reloadProject } = useProject();
  const [saved, setSaved] = useState<Project>(project);
  const [draft, setDraft] = useState<Draft>(() => draftOf(project));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<string | null>(null);
  const set = <K extends keyof Draft>(k: K) => (v: Draft[K]) => setDraft(d => ({ ...d, [k]: v }));
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftOf(saved));
  const parsed = parseSettings(draft);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if ('error' in parsed) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const next = await api().updateProject(saved.id, { name: draft.name.trim(), settings: parsed.settings });
      setSaved(next);
      reloadProject();
      setDraft(draftOf(next));
      setMessage('Saved. New assessments and monitoring decisions use these settings; running assessments keep the budget they started with.');
    } catch (err) { setError(err); } finally { setBusy(false); }
  };

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <PageTitle eyebrow="Configure" title="Settings" subtitle="Project name, languages, budgets and monitoring policy." />
      <Repositories repos={repos} />
    <form onSubmit={submit} className="grid min-w-0 grid-cols-1 gap-6 lg:grid-cols-2" aria-label="Project settings">
      <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
        <Button type="submit" variant="primary" disabled={busy || !dirty || 'error' in parsed}>Save settings</Button>
        <Button type="button" disabled={busy || !dirty} onClick={() => setDraft(draftOf(saved))}>Discard changes</Button>
        {!dirty && !message && <span className="text-sm text-muted">Unsaved changes show here and at the bottom of this form.</span>}
      </div>
      <Panel title="Project">
        <Field label="Name"><input className={inputClass} value={draft.name} onChange={e => set('name')(e.target.value)} maxLength={200} /></Field>
        <div className="mt-3">
          <Field label="Languages" hint="Comma-separated. Code intelligence currently indexes Python.">
            <input className={inputClass} value={draft.languages} onChange={e => set('languages')(e.target.value)} />
          </Field>
        </div>
      </Panel>
      <Panel title="Default assessment budget">
        <p className="mb-3 text-sm text-muted">Hard ceilings enforced by the orchestrator. An assessment that reaches any limit stops and is marked budget-exhausted, never silently extended.</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Max tokens"><input className={inputClass} inputMode="numeric" value={draft.maxTokens} onChange={e => set('maxTokens')(e.target.value)} /></Field>
          <Field label="Max USD"><input className={inputClass} inputMode="decimal" value={draft.maxUsd} onChange={e => set('maxUsd')(e.target.value)} /></Field>
          <Field label="Wall clock (s)"><input className={inputClass} inputMode="numeric" value={draft.maxWall} onChange={e => set('maxWall')(e.target.value)} /></Field>
        </div>
      </Panel>
      <Panel title="Continuous monitoring trigger policy" className="lg:col-span-2">
        <p className="mb-3 text-sm text-muted">
          Controls whether a pushed commit starts a targeted reassessment. Reassessments still require an active, approved scope;
          without one the event is recorded as blocked rather than tested.
        </p>
        <label className="mb-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={draft.enabled} onChange={e => set('enabled')(e.target.checked)} />
          24/7 defense: re-check every relevant push automatically
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Minimum interval between reassessments (s)" hint="Changes inside this window are coalesced.">
            <input className={inputClass} inputMode="numeric" value={draft.interval} onChange={e => set('interval')(e.target.value)} />
          </Field>
          <Field label="Record-only path globs" hint="One per line. If every changed path matches, the event is recorded without an assessment.">
            <textarea className={`${inputClass} min-h-24 font-mono`} value={draft.ignore} onChange={e => set('ignore')(e.target.value)} />
          </Field>
        </div>
        <p className="mb-2 mt-4 text-sm text-muted">Budget for each monitoring-triggered reassessment (separate from manual assessments).</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Max tokens"><input className={inputClass} inputMode="numeric" value={draft.monTokens} onChange={e => set('monTokens')(e.target.value)} /></Field>
          <Field label="Max USD"><input className={inputClass} inputMode="decimal" value={draft.monUsd} onChange={e => set('monUsd')(e.target.value)} /></Field>
          <Field label="Wall clock (s)"><input className={inputClass} inputMode="numeric" value={draft.monWall} onChange={e => set('monWall')(e.target.value)} /></Field>
        </div>
      </Panel>
      <Panel title="Code reviewer" className="lg:col-span-2">
        <p className="mb-3 text-sm text-muted">
          Choose which pushes and pull requests Ari reviews. Eligible events still require repository automatic assessments,
          an active approved scope, and available project quota.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.reviewPushes} onChange={e => set('reviewPushes')(e.target.checked)} />
            Review pushes to the default branch
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.reviewPullRequests} onChange={e => set('reviewPullRequests')(e.target.checked)} />
            Review pull requests
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.reviewDraftPrs} onChange={e => set('reviewDraftPrs')(e.target.checked)} />
            Include draft pull requests
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.commentOnPullRequests} onChange={e => set('commentOnPullRequests')(e.target.checked)} />
            Post review comments on pull requests
          </label>
        </div>
        <fieldset className="mt-4">
          <legend className="mb-2 text-sm font-medium">Finding strictness</legend>
          {([
            [1, 'Report everything', 'Include findings of every severity.'],
            [2, 'Medium and above', 'Include medium, high, and critical findings.'],
            [3, 'High and critical only', 'Show only high and critical findings.'],
          ] as const).map(([value, label, hint]) => (
            <label key={value} className="mb-2 flex items-start gap-2 text-sm">
              <input type="radio" name="review-strictness" checked={draft.strictness === value}
                onChange={() => set('strictness')(value)} />
              <span><strong>{label}</strong> — {hint}</span>
            </label>
          ))}
          <p className="text-sm text-muted">Strictness filters only the pull-request comment; all findings remain stored in Ari.</p>
        </fieldset>
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Include branches" hint="Comma- or newline-separated globs. Pushes default to the default branch; pull requests default to all base branches.">
            <textarea className={`${inputClass} min-h-20 font-mono`} value={draft.includeBranches} onChange={e => set('includeBranches')(e.target.value)} />
          </Field>
          <Field label="Exclude branches" hint="Comma- or newline-separated globs; matching branches are skipped.">
            <textarea className={`${inputClass} min-h-20 font-mono`} value={draft.excludeBranches} onChange={e => set('excludeBranches')(e.target.value)} />
          </Field>
          <Field label="Include authors" hint="Comma- or newline-separated GitHub login globs. Empty means everyone.">
            <textarea className={`${inputClass} min-h-20 font-mono`} value={draft.includeAuthors} onChange={e => set('includeAuthors')(e.target.value)} />
          </Field>
          <Field label="Exclude authors" hint="Comma- or newline-separated GitHub login globs. Defaults to *[bot].">
            <textarea className={`${inputClass} min-h-20 font-mono`} value={draft.excludeAuthors} onChange={e => set('excludeAuthors')(e.target.value)} />
          </Field>
        </div>
        <div className="mt-4 max-w-xs">
          <Field label="Maximum reviews per hour" hint="Optional project limit. The server cap is 10 reviews per hour; this setting can lower, never raise it.">
            <input className={inputClass} type="number" min={1} step={1} value={draft.maxReviewsPerHour}
              onChange={e => set('maxReviewsPerHour')(e.target.value)} placeholder="Use server cap" />
          </Field>
        </div>
      </Panel>
      <Panel title="Fix delivery" className="lg:col-span-2">
        <p className="mb-3 text-sm text-muted">How verified fixes from the code reviewer and the seven agents reach your repository. Needs the GitHub App&apos;s Contents and Pull requests: Read &amp; write permissions.</p>
        {([
          ['auto', 'Auto-commit', 'Ari commits every fix the Verify agent accepts straight to your default branch, no human check. If the branch moved or is protected, it opens a pull request instead.'],
          ['pull_request', 'Pull request', 'Every verified fix arrives as a pull request. Your team reviews and merges.'],
          ['manual', 'Manual', 'Nothing is pushed. You approve each fix in ARI yourself.'],
        ] as const).map(([value, label, hint]) => (
          <label key={value} className="mb-2 flex items-start gap-2 text-sm">
            <input type="radio" name="delivery" checked={draft.deliveryMode === value}
              onChange={() => { if (value !== 'auto' || window.confirm(AUTO_WARNING)) set('deliveryMode')(value); }} />
            <span><strong>{label}</strong>: {hint}</span>
          </label>
        ))}
      </Panel>
      <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
        <Button type="submit" variant="primary" disabled={busy || !dirty || 'error' in parsed}>Save settings</Button>
        <Button type="button" disabled={busy || !dirty} onClick={() => setDraft(draftOf(saved))}>Discard changes</Button>
        {'error' in parsed && <span role="alert" className="text-sm text-bad">{parsed.error}</span>}
        {message && <span role="status" className="text-sm text-muted">{message}</span>}
      </div>
      {error !== null && <div className="lg:col-span-2"><ErrorState error={error} context="Settings not saved (only project owners can change settings)" /></div>}
    </form>
    </div>
  );
}
