import { bucketCounts } from '@/lib/findings';

/**
 * The product loop as five connected steps, each carrying this project's recorded
 * counts. Words are explanation; numbers come only from loaded records, and a count
 * that did not load is `null` (shown as "not loaded"), never zero.
 */
export type LoopTone = 'empty' | 'active' | 'attention' | 'good' | 'unknown';

export interface LoopNode {
  id: 'understand' | 'review' | 'attack' | 'prove' | 'monitor';
  pillar: 'Shared intelligence' | 'Pillar 1' | 'Pillar 2' | 'Pillar 3';
  title: string;
  question: string;
  status: string;
  tone: LoopTone;
  href: string;
}

export interface LoopInput {
  projectId: string;
  repoCount: number;
  /** Null while scopes are loading or failed to load; never treated as zero. */
  activeScopeCount: number | null;
  assessmentCount: number | null;
  findings: readonly { kind: string; status: string }[] | null;
  monitoringEnabled: boolean;
  monitoringEventCount: number | null;
  reassessmentCount: number | null;
}

const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

export function pillarLoop(input: LoopInput): LoopNode[] {
  const base = `/projects/${input.projectId}`;
  const f = input.findings;
  const fn = f ? bucketCounts(f, 'functional') : null;
  const sec = f ? bucketCounts(f, 'security') : null;
  const functional = fn ? fn.confirmed : null;
  const confirmed = sec ? sec.confirmed : null;
  const suspicions = sec ? sec.untested : null;
  const inconclusive = sec ? sec.inconclusive : null;
  const fixed = f ? bucketCounts(f).fixed : null;
  const checked = input.assessmentCount !== null && input.assessmentCount > 0;

  const understand: LoopNode = {
    id: 'understand', pillar: 'Shared intelligence', title: 'Understand the code',
    question: 'What is this software supposed to do?',
    status: input.repoCount === 0 ? 'No repository connected yet.' : `${plural(input.repoCount, 'repository', 'repositories')} connected${input.assessmentCount === null ? '.' : `, ${plural(input.assessmentCount, 'check')} run.`}`,
    tone: input.repoCount === 0 ? 'empty' : 'good', href: `${base}/settings#connect-repo`,
  };
  const review: LoopNode = {
    id: 'review', pillar: 'Pillar 1', title: 'Review the change',
    question: 'Does the change do what it was meant to?',
    status: fn === null || functional === null ? 'Findings did not load.' : !checked ? 'Not checked yet.'
      : functional === 0 && fn.untested === 0 && fn.inconclusive === 0 ? 'No open problems in how the code behaves.'
      : [
        functional > 0 ? `${plural(functional, 'behaviour problem')} still open` : null,
        fn.untested > 0 ? plural(fn.untested, 'untested suspicion') : null,
        fn.inconclusive > 0 ? plural(fn.inconclusive, 'inconclusive test result') : null,
      ].filter(Boolean).join(', ') + '.',
    tone: fn === null || functional === null ? 'unknown' : !checked ? 'empty' : functional > 0 ? 'attention' : fn.untested > 0 || fn.inconclusive > 0 ? 'active' : 'good',
    href: `${base}/findings`,
  };
  const attack: LoopNode = {
    id: 'attack', pillar: 'Pillar 2', title: 'Test it like an attacker',
    question: 'Could someone break in or misuse it?',
    status: input.activeScopeCount === null ? 'Scopes did not load.'
      : input.activeScopeCount === 0 ? 'Blocked until a person approves a safe test environment.'
      : confirmed === null || suspicions === null || inconclusive === null ? 'Findings did not load.'
      : !checked ? 'Not tested yet.'
      : confirmed === 0 && suspicions === 0 && inconclusive === 0 ? 'No open security problems in this list.'
      : [
        confirmed > 0 ? plural(confirmed, 'confirmed weakness', 'confirmed weaknesses') : null,
        suspicions > 0 ? plural(suspicions, 'untested suspicion') : null,
        inconclusive > 0 ? plural(inconclusive, 'inconclusive test result') : null,
      ].filter(Boolean).join(', ') + '.',
    tone: input.activeScopeCount === null ? 'unknown' : input.activeScopeCount === 0 ? 'attention' : confirmed === null ? 'unknown' : !checked ? 'empty'
      : confirmed > 0 ? 'attention' : (suspicions ?? 0) > 0 || (inconclusive ?? 0) > 0 ? 'active' : 'good',
    href: input.activeScopeCount === null || input.activeScopeCount === 0 ? `${base}/scopes` : `${base}/findings`,
  };
  const prove: LoopNode = {
    id: 'prove', pillar: 'Pillar 2', title: 'Fix it and prove it',
    question: 'Is there a fix that independent tests accept?',
    status: fixed === null ? 'Findings did not load.' : fixed === 0 ? 'No independently verified fix yet.' : `${plural(fixed, 'fix', 'fixes')} accepted by independent tests. Proposed only; nothing was merged or deployed.`,
    tone: fixed === null ? 'unknown' : fixed === 0 ? 'empty' : 'good', href: `${base}/findings`,
  };
  const monitor: LoopNode = {
    id: 'monitor', pillar: 'Pillar 3', title: 'Keep watching',
    question: 'What happens when the code changes again?',
    status: !input.monitoringEnabled ? 'Monitoring is off for this project.'
      : input.monitoringEventCount === null ? 'Monitoring events did not load.'
      : input.monitoringEventCount === 0 ? 'On. No code change has arrived yet.'
      : `On. ${plural(input.monitoringEventCount, 'change event')} received${input.reassessmentCount ? `, ${plural(input.reassessmentCount, 're-check')} requested` : ''}.`,
    tone: !input.monitoringEnabled ? 'empty' : input.monitoringEventCount === null ? 'unknown' : input.monitoringEventCount === 0 ? 'empty' : 'active',
    href: `${base}/monitoring`,
  };
  return [understand, review, attack, prove, monitor];
}
