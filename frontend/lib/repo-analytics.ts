import type { Assessment, Finding, SeverityLevel } from '@hackatl/frontend-transport';
import { findingGroup } from '@/lib/findings';

const FAILED = new Set(['FAILED', 'CANCELLED', 'BUDGET_EXHAUSTED']);
const SEVERITY_ORDER: Record<SeverityLevel | 'unassessed', number> = {
  critical: 5,
  high: 4,
  medium: 3,
  low: 2,
  info: 1,
  unassessed: 0,
};

export interface ChecksPerDay {
  date: string;
  completed: number;
  active: number;
  failed: number;
}

export interface RepoAnalytics {
  commitsChecked: number;
  checksRun: number;
  openFindings: number;
  fixesVerified: number;
  checksPerDay: ChecksPerDay[];
  bySeverity: Record<SeverityLevel | 'unassessed', number>;
  byStatusGroup: { open: number; fixed: number; closed: number };
  medianDurationMs: number | null;
  topOpen: Finding[];
}

export function findingsForRepo(
  repoId: string,
  assessments: readonly Assessment[],
  findings: readonly Finding[],
  snapshotIds: readonly string[],
): Finding[] {
  const repoAssessmentIds = new Set(
    assessments.filter(assessment => assessment.repo_id === repoId).map(assessment => assessment.id),
  );
  const repoSnapshotIds = new Set(snapshotIds);
  return findings.filter(finding =>
    repoAssessmentIds.has(finding.assessment_id)
    || (finding.snapshot_id !== null && repoSnapshotIds.has(finding.snapshot_id)),
  );
}

function outcome(state: string): 'completed' | 'active' | 'failed' {
  if (state === 'COMPLETED') return 'completed';
  if (FAILED.has(state)) return 'failed';
  return 'active';
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  values.sort((a, b) => a - b);
  const middle = Math.floor(values.length / 2);
  return values.length % 2 === 1
    ? values[middle]!
    : (values[middle - 1]! + values[middle]!) / 2;
}

export function repoAnalytics({
  repoId,
  assessments,
  findings,
  snapshotIds,
  now,
}: {
  repoId: string;
  assessments: readonly Assessment[];
  findings: readonly Finding[];
  snapshotIds: readonly string[];
  now: Date;
}): RepoAnalytics {
  const repoAssessments = assessments.filter(assessment => assessment.repo_id === repoId);
  const repoFindings = findingsForRepo(repoId, repoAssessments, findings, snapshotIds);
  const statusCounts = { open: 0, fixed: 0, closed: 0 };
  const bySeverity: Record<SeverityLevel | 'unassessed', number> = {
    info: 0,
    low: 0,
    medium: 0,
    high: 0,
    critical: 0,
    unassessed: 0,
  };

  for (const finding of repoFindings) {
    const group = findingGroup(finding.status);
    if (group !== 'hypothesis') statusCounts[group]++;
    bySeverity[finding.severity?.level ?? 'unassessed']++;
  }

  const end = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const dates = Array.from({ length: 30 }, (_, index) => new Date(end - (29 - index) * 86400000).toISOString().slice(0, 10));
  const checksPerDay = dates.map(date => ({ date, completed: 0, active: 0, failed: 0 }));
  const dayIndexes = new Map(checksPerDay.map((day, index) => [day.date, index]));
  for (const assessment of repoAssessments) {
    const date = new Date(assessment.created_at).toISOString().slice(0, 10);
    const index = dayIndexes.get(date);
    if (index === undefined) continue;
    checksPerDay[index]![outcome(assessment.state)]++;
  }

  const durations = repoAssessments
    .filter(assessment => assessment.state === 'COMPLETED' && assessment.completed_at !== null)
    .map(assessment => Date.parse(assessment.completed_at!) - Date.parse(assessment.created_at))
    .filter(duration => Number.isFinite(duration) && duration >= 0);
  const topOpen = repoFindings
    .filter(finding => findingGroup(finding.status) === 'open')
    .sort((a, b) =>
      SEVERITY_ORDER[b.severity?.level ?? 'unassessed'] - SEVERITY_ORDER[a.severity?.level ?? 'unassessed']
      || b.updated_at.localeCompare(a.updated_at),
    )
    .slice(0, 5);

  return {
    commitsChecked: new Set(repoAssessments.map(assessment => assessment.commit_sha).filter(Boolean)).size,
    checksRun: repoAssessments.length,
    openFindings: statusCounts.open,
    fixesVerified: statusCounts.fixed,
    checksPerDay,
    bySeverity,
    byStatusGroup: statusCounts,
    medianDurationMs: median(durations),
    topOpen,
  };
}
