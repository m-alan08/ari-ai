'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import { useResource } from '@/lib/use-resource';
import { ErrorState } from '@/components/ui';

/** The switch is persisted for this repository only; it never starts browser-side runs. */
export function RepositoryAutomation({ repoId }: { repoId: string }) {
  const resource = useResource(`repository-automation:${repoId}`, signal => api().repository(repoId, { signal }), 10000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const enabled = resource.status === 'ready' ? resource.data.automatic_assessments : null;
  const toggle = async () => {
    if (enabled === null || busy) return;
    setBusy(true); setError(null);
    try {
      await api().updateRepository(repoId, { automatic_assessments: !enabled });
      await resource.reload();
      window.dispatchEvent(new Event('ari:automation-changed'));
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  };
  return <section className="mb-5 rounded-xl border border-line bg-panel px-5 py-4" aria-label="Automatic assessments">
    <div className="flex items-center justify-between gap-5">
      <div><h2 className="text-sm font-semibold">Automatic runs</h2><p className="mt-1 max-w-xl text-sm text-muted">{enabled === null ? 'Loading repository settings…' : enabled ? 'Every eligible new commit runs in the background. Open a commit to see its results.' : 'Off. Choose a commit and click Run when you want an assessment.'}</p></div>
      <button type="button" role="switch" aria-checked={enabled ?? false} aria-label="Automatic runs" disabled={enabled === null || busy || resource.refreshError !== undefined} onClick={() => void toggle()} className={`shrink-0 rounded-lg border border-line-2 px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${enabled ? 'bg-brand text-on-brand' : 'bg-panel-2 text-muted'}`}>{busy ? 'Saving…' : enabled ? 'ON' : 'OFF'}</button>
    </div>
    <p className="mt-2 text-xs text-muted">This repository only. Requires approved sandbox authorization and enabled project monitoring. Eligible commits may wait for available capacity.</p>
    {resource.status === 'error' && <ErrorState error={resource.error} onRetry={resource.reload} />}
    {resource.refreshError !== undefined && <ErrorState error={resource.refreshError} onRetry={resource.reload} />}
    {error !== null && <ErrorState error={error} />}
  </section>;
}
