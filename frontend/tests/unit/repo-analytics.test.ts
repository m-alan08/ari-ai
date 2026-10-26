import type { Assessment, Finding } from '@hackatl/frontend-transport';
import { describe, expect, it } from 'vitest';
import { findingsForRepo, repoAnalytics } from '@/lib/repo-analytics';

const assessment = (overrides: Partial<Assessment>): Assessment => ({
  id: 'assessment-1',
  project_id: 'project-1',
  snapshot_id: null,
  commit_sha: 'abc123',
  repo_id: 'repo-1',
  scope_id: 'scope-1',
  trigger: 'manual',
  reassessment_request_id: null,
  state: 'COMPLETED',
  budget: {} as Assessment['budget'],
  usage: {} as Assessment['usage'],
  error: null,
  created_by: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  completed_at: '2026-10-01T00:04:12Z',
  ...overrides,
});

const finding = (overrides: Partial<Finding>): Finding => ({
  id: 'finding-1',
  assessment_id: 'assessment-1',
  snapshot_id: null,
  commit_sha: 'abc123',
  kind: 'functional',
  title: 'Open finding',
  status: 'validated',
  severity: { level: 'high' } as Finding['severity'],
  subject_refs: [],
  expectation_ids: [],
  evidence: [],
  confidence: 0.9,
  produced_by_run_id: null,
  history: [],
  standards: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-02T00:00:00Z',
  ...overrides,
});

describe('repoAnalytics', () => {
  it('counts only the selected repo and reuses Findings status groups', () => {
    const assessments = [
      assessment({ id: 'a1', commit_sha: 'sha-1', created_at: '2026-10-25T00:00:00Z', completed_at: '2026-10-25T00:04:12Z' }),
      assessment({ id: 'a2', commit_sha: 'sha-1', state: 'AWAITING_APPROVAL', created_at: '2026-10-26T00:00:00Z', completed_at: null }),
      assessment({ id: 'a3', repo_id: 'repo-2', commit_sha: 'other-sha', created_at: '2026-10-26T00:00:00Z' }),
    ];
    const findings = [
      finding({ id: 'open', assessment_id: 'a1', snapshot_id: null }),
      finding({ id: 'fixed', assessment_id: 'a2', status: 'verified', severity: null }),
      finding({ id: 'closed', assessment_id: 'a3', status: 'closed' }),
      finding({ id: 'snapshot', assessment_id: 'other', snapshot_id: 'selected-snapshot', status: 'hypothesis' }),
      finding({ id: 'other-snapshot', assessment_id: 'other', snapshot_id: 'other-snapshot' }),
    ];
    const result = repoAnalytics({
      repoId: 'repo-1',
      assessments,
      findings,
      snapshotIds: ['selected-snapshot'],
      now: new Date('2026-10-27T12:00:00Z'),
    });
    expect(result.commitsChecked).toBe(1);
    expect(result.checksRun).toBe(2);
    expect(result.openFindings).toBe(1);
    expect(result.fixesVerified).toBe(1);
    expect(result.byStatusGroup).toEqual({ open: 1, fixed: 1, closed: 0 });
    expect(result.bySeverity).toMatchObject({ high: 2, unassessed: 1 });
    expect(result.medianDurationMs).toBe(252000);
    expect(result.checksPerDay).toHaveLength(30);
    expect(result.checksPerDay.at(-2)).toMatchObject({ date: '2026-10-26', active: 1 });
    expect(result.topOpen.map(item => item.id)).toEqual(['open']);
  });

  it('returns a zeroed 30-day result for empty records', () => {
    const result = repoAnalytics({
      repoId: 'repo-1',
      assessments: [],
      findings: [],
      snapshotIds: [],
      now: new Date('2026-10-27T12:00:00Z'),
    });
    expect(result).toMatchObject({
      commitsChecked: 0,
      checksRun: 0,
      openFindings: 0,
      fixesVerified: 0,
      byStatusGroup: { open: 0, fixed: 0, closed: 0 },
      medianDurationMs: null,
      topOpen: [],
    });
    expect(result.checksPerDay).toHaveLength(30);
  });

  it('filters by the selected assessment or saved snapshot ids', () => {
    const items = [
      finding({ id: 'assessment-match', assessment_id: 'repo-1-assessment' }),
      finding({ id: 'snapshot-match', assessment_id: 'other', snapshot_id: 'repo-1-snapshot' }),
      finding({ id: 'other-repo', assessment_id: 'repo-2-assessment', snapshot_id: 'repo-2-snapshot' }),
    ];
    expect(findingsForRepo(
      'repo-1',
      [assessment({ id: 'repo-1-assessment' }), assessment({ id: 'repo-2-assessment', repo_id: 'repo-2' })],
      items,
      ['repo-1-snapshot'],
    ).map(item => item.id)).toEqual(['assessment-match', 'snapshot-match']);
  });
});
