export const shortSha = (sha: string | null | undefined): string => (sha ? sha.slice(0, 8) : '—');

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z').replace(/Z$/, ' UTC');
}

export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const secondsFromNow = (now - t) / 1000;
  const s = Math.round(secondsFromNow);
  if (secondsFromNow < 0) {
    const futureSeconds = Math.ceil(-secondsFromNow);
    if (futureSeconds < 60) return `in ${futureSeconds} ${futureSeconds === 1 ? 'second' : 'seconds'}`;
    const minutes = Math.floor(futureSeconds / 60);
    if (futureSeconds < 3600) return `in ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
    const hours = Math.floor(futureSeconds / 3600);
    if (futureSeconds < 86400) return `in ${hours} ${hours === 1 ? 'hour' : 'hours'}`;
    const days = Math.floor(futureSeconds / 86400);
    return `in ${days} ${days === 1 ? 'day' : 'days'}`;
  }
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function duration(startIso: string, endIso: string | null): string {
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return '—';
  const ms = Math.max(0, end - start);
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export const usd = (n: number): string => `$${n.toFixed(n < 1 ? 4 : 2)}`;
export const pct = (n: number): string => `${(n * 100).toFixed(1)}%`;
export const num = (n: number, digits = 2): string => (Number.isFinite(n) ? n.toFixed(digits) : '—');

/** GitHub permalink at the exact snapshot SHA; null when any part is missing. */
export function githubLink(repoFullName: string | undefined, sha: string | null, path: string | null, start?: number, end?: number): string | null {
  if (!repoFullName || !sha || !path || !/^[\w.-]+\/[\w.-]+$/.test(repoFullName) || !/^[0-9a-f]{7,40}$/.test(sha)) return null;
  const lines = start ? `#L${start}${end && end !== start ? `-L${end}` : ''}` : '';
  return `https://github.com/${repoFullName}/blob/${sha}/${path.split('/').map(encodeURIComponent).join('/')}${lines}`;
}
