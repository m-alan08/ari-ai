'use client';

import type { EvidenceCheckStatus, RlReadinessAssessment } from '@hackatl/frontend-transport';
import { SourceTag } from '@/components/live-defense/common';
import { Async, Empty, Mono, Panel, Tag } from '@/components/ui';
import { RL_BLOCKED_COPY, type LiveDefenseMode } from '@/lib/live-defense';
import type { Resource } from '@/lib/use-resource';

const TONE: Record<EvidenceCheckStatus, 'ok' | 'warn' | 'info'> = {
  PASS: 'ok',
  FAIL: 'warn',
  BLOCKED: 'warn',
  'NOT VERIFIED': 'info',
};

export function ReadinessDashboard({
  mode,
  projectId,
  readiness,
}: {
  mode: LiveDefenseMode;
  projectId: string | null;
  readiness?: Resource<RlReadinessAssessment>;
}) {
  if (mode === 'layout-preview') {
    return (
      <Panel title="Sample readiness chrome" actions={<SourceTag mode="layout-preview" />}>
        <p className="text-sm text-ink" data-testid="live-defense-preview-readiness">
          SAMPLE LAYOUT. This panel does not compute readiness. The live tab reads GET /rl-readiness from the backend.
        </p>
      </Panel>
    );
  }
  if (!projectId) return <Empty>Choose a project to load the backend readiness assessment.</Empty>;
  return (
    <div className="flex flex-col gap-4" data-testid="live-defense-readiness">
      <p className="rounded-xl border border-line bg-panel-2 px-4 py-3 text-sm text-ink" data-testid="live-defense-rl-blocked-copy">
        {RL_BLOCKED_COPY}
      </p>
      {readiness ? (
        <Async resource={readiness} label="RL readiness">
          {item => <ReadinessBody assessment={item} />}
        </Async>
      ) : <Empty>Readiness was not requested.</Empty>}
    </div>
  );
}

function ReadinessBody({ assessment }: { assessment: RlReadinessAssessment }) {
  return (
    <>
      <Panel title="Backend verdict" actions={<SourceTag mode="live" />}>
        <p className="text-sm text-ink" data-testid="live-defense-readiness-verdict">
          {assessment.verdict}
        </p>
        <p className="mt-2 text-sm text-ink">
          ready=<Mono>{String(assessment.ready)}</Mono>
          {' · '}infrastructure complete={String(assessment.infrastructure_complete)}
          {' · '}customer infra provisioned={String(assessment.customer_infra_provisioned)}
        </p>
        <p className="mt-2 text-xs text-muted" data-testid="live-defense-readiness-phase6">
          {assessment.phase6_verdict}
        </p>
        <p className="mt-2 text-xs text-muted">{assessment.caveat}</p>
      </Panel>
      <Panel title="Categories A–J">
        <ul className="flex flex-col gap-2" data-testid="live-defense-readiness-categories">
          {assessment.categories.map(item => (
            <li key={item.category} className="rounded-md border border-line bg-panel-2 p-3" data-testid={`live-defense-readiness-${item.letter}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-ink">{item.letter}. {item.title}</p>
                <Tag tone={TONE[item.status]}>{item.status}</Tag>
              </div>
              <p className="mt-1 text-xs text-muted">{item.summary}</p>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Evaluation plan">
        <p className="text-sm text-ink">
          Thresholds are unset pending RL-owner approval. Pass-by-total-reward is forbidden.
        </p>
        <ul className="mt-2 text-xs text-muted">
          {assessment.evaluation_plan.metrics.map(metric => (
            <li key={metric.name}>
              {metric.name}: threshold {metric.threshold === null ? 'unset' : String(metric.threshold)} ({metric.threshold_status})
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Hosted inference">
        <p className="text-sm text-ink">
          v6 passed new criteria: {String(assessment.hosted_inference.v6_passed_new_criteria)}.
          Approved checkpoint present: {String(assessment.hosted_inference.approved_checkpoint_present)}.
        </p>
        <p className="mt-1 text-xs text-muted">Q-values are not probabilities. Missing serving fields stay missing.</p>
      </Panel>
    </>
  );
}
