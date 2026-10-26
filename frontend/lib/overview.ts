import { bucketCounts } from '@/lib/findings';

const RUNNING = new Set([
  'CREATED', 'SCOPE_CHECK', 'INDEXING', 'FUNCTIONAL_REVIEW', 'RECON', 'VALIDATION',
  'ANALYSIS', 'DIAGNOSIS', 'REMEDIATION', 'HARDENING', 'REPORTING',
]);


export interface OverviewInput {
  projectId: string;
  repoCount: number;
  /** Null while scopes are loading or failed to load; never treated as zero. */
  activeScopeCount: number | null;
  monitoringEnabled: boolean;
  assessments: readonly { id: string; state: string; created_at: string }[];
  findings: readonly { status: string; kind: string }[];
}

export interface Overview {
  headline: string;
  detail: string;
  codeOpen: number;
  securityOpen: number;
  /** Suspicions no test has confirmed or ruled out (functional and security). */
  untested: number;
  /** Tests that ran but could not decide (functional and security). */
  inconclusive: number;
  verifiedFixes: number;
  monitoring: 'enabled' | 'disabled';
  action: { label: string; href: string };
}

/** One next step and three counts, taken only from records the page already loaded. */
export function projectOverview(input: OverviewInput): Overview {
  const projectBase = `/projects/${input.projectId}`;
  const latest = [...input.assessments].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
  const all = bucketCounts(input.findings);
  const codeOpen = bucketCounts(input.findings, 'functional').confirmed;
  const securityOpen = bucketCounts(input.findings, 'security').confirmed;
  const verifiedFixes = all.fixed;
  const base = { codeOpen, securityOpen, untested: all.untested, inconclusive: all.inconclusive, verifiedFixes, monitoring: input.monitoringEnabled ? 'enabled' as const : 'disabled' as const };

  if (input.repoCount === 0) {
    return {
      ...base,
      headline: 'No repository is connected yet.',
      detail: 'Connect a repository before any check can run.',
      action: { label: 'Connect a repository', href: `${projectBase}/settings#connect-repo` },
    };
  }
  if (input.activeScopeCount === null) {
    return {
      ...base,
      headline: 'A repository is connected. Scopes did not load.',
      detail: 'Whether a safe test environment is approved is unknown until scopes load.',
      action: { label: 'Open scopes', href: `/projects/${input.projectId}/scopes` },
    };
  }
  if (input.activeScopeCount === 0) {
    return {
      ...base,
      headline: 'A repository is connected. No safe test environment is approved.',
      detail: 'Security checks stay blocked until a project owner approves a scope. Owners can approve their own sandbox-only scope.',
      action: { label: 'Approve a safe test environment', href: `/projects/${input.projectId}/scopes` },
    };
  }
  if (!latest) {
    return {
      ...base,
      headline: 'Nothing has been checked yet.',
      detail: 'A scope is approved. The next step is to run a review of one commit.',
      action: { label: 'Run a review', href: `${projectBase}/commits#start-assessment` },
    };
  }
  if (RUNNING.has(latest.state)) {
    return {
      ...base,
      headline: 'A check is running.',
      detail: 'Open it to see which agents have a recorded run. Nothing here is guessed ahead of those records.',
      action: { label: 'View the run', href: `/projects/${input.projectId}/assessments/${latest.id}` },
    };
  }
  if (latest.state === 'AWAITING_APPROVAL') {
    return {
      ...base,
      headline: 'A check is paused for a human decision.',
      detail: 'Approving lets a candidate fix be built and re-tested. Nothing is merged or deployed.',
      action: { label: 'Review the proposed fix', href: `/projects/${input.projectId}/approvals` },
    };
  }
  if (latest.state === 'FAILED' || latest.state === 'BUDGET_EXHAUSTED' || latest.state === 'CANCELLED') {
    return {
      ...base,
      headline: latest.state === 'FAILED' ? 'The latest check failed.' : `The latest check is ${latest.state === 'CANCELLED' ? 'cancelled' : 'out of budget'}.`,
      detail: 'Open the run for the recorded error. This is not a clean result.',
      action: { label: 'View the run', href: `/projects/${input.projectId}/assessments/${latest.id}` },
    };
  }
  const open = codeOpen + securityOpen;
  const unsure = [
    base.untested > 0 ? `${base.untested} untested suspicion${base.untested === 1 ? '' : 's'}` : null,
    base.inconclusive > 0 ? `${base.inconclusive} inconclusive test result${base.inconclusive === 1 ? '' : 's'}` : null,
  ].filter(Boolean).join(' and ');
  return {
    ...base,
    headline: open > 0
      ? `The latest check finished. ${open} confirmed open finding${open === 1 ? '' : 's'} still need a look${unsure ? `, plus ${unsure}` : ''}.`
      : unsure ? `The latest check finished. No confirmed open findings, but ${unsure} remain.`
      : 'The latest check finished. No open findings are in this list.',
    detail: 'Verified means independent tests accepted a candidate fix. It does not mean the whole application is secure.',
    action: { label: 'View results', href: `/projects/${input.projectId}/findings` },
  };
}
