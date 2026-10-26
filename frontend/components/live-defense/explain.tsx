'use client';

import type { AnalystReview, DefenseRecommendation, LivePolicyDecision, RLEvaluation, RLExplanation } from '@hackatl/frontend-transport';
import Link from 'next/link';
import { useState } from 'react';
import { QRanking, SimulationCaveat, SourceTag } from '@/components/live-defense/common';
import { AnalystReviewForm } from '@/components/live-defense/review';
import { Async, Button, Empty, ErrorState, Loading, Mono, Panel, Tag, Time } from '@/components/ui';
import { api } from '@/lib/api';
import { actionInWords, rankQValues, SIMULATION_CAVEAT, unavailable, type LiveDefenseMode } from '@/lib/live-defense';
import { PREVIEW_RECOMMENDATIONS } from '@/lib/live-defense-preview';
import { num } from '@/lib/format';
import type { Resource } from '@/lib/use-resource';

export function Explainability({
  mode,
  recs,
  decisions,
  evals,
  reviews,
  onReview,
}: {
  mode: LiveDefenseMode;
  recs?: Resource<{ items: DefenseRecommendation[]; label: string }>;
  decisions?: Resource<{ items: LivePolicyDecision[]; label: string }>;
  evals?: Resource<{ items: RLEvaluation[] }>;
  reviews?: Resource<{ items: AnalystReview[] }>;
  onReview?: () => void;
}) {
  if (mode === 'layout-preview') return <PreviewExplain />;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <Panel title="Why a stored recommendation looks the way it does" actions={<SourceTag mode="live" />}>
        <SimulationCaveat />
        <p className="mt-3 text-sm text-muted">
          Simulation Q-values explain a stored Defense Lab recommendation, not a live attack.
          Shadow decisions below are separate: Q-values appear only when frozen v6 inference actually ran.
          Missing fields are shown as unavailable. Held-out scores live in the <Link className="text-accent hover:underline" href="/lab">Defense Lab</Link>.
        </p>
      </Panel>
      <Panel title="Shadow decisions (authenticated ingest)">
        {decisions ? (
          <Async resource={decisions} label="shadow decisions">
            {page => (
              <>
                <p className="mb-3 text-xs text-muted">Backend label: {page.label}</p>
                {page.items.length === 0 ? <Empty>No shadow decision has been produced for this project.</Empty> : (
                  <ul className="flex flex-col gap-3">{page.items.map(item => (
                    <LiveShadowDecision
                      key={item.id}
                      decision={item}
                      reviews={reviews?.status === 'ready' ? reviews.data.items.filter(entry => entry.live_policy_decision_id === item.id) : []}
                      onReview={onReview}
                    />
                  ))}</ul>
                )}
              </>
            )}
          </Async>
        ) : <Empty>Shadow decisions were not requested.</Empty>}
      </Panel>
      <Panel title="Stored simulation recommendations">
        {recs ? (
          <Async resource={recs} label="recommendations">
            {page => (
              <>
                <p className="mb-3 text-xs text-muted">Backend label: {page.label}</p>
                {page.items.length === 0 ? <Empty>No recommendation has been requested for this project.</Empty> : (
                  <ul className="flex flex-col gap-3">{page.items.map(rec => (
                    <LiveRecommendation
                      key={rec.id}
                      rec={rec}
                      reviews={reviews?.status === 'ready' ? reviews.data.items.filter(item => item.recommendation_id === rec.id) : []}
                      onReview={onReview}
                    />
                  ))}</ul>
                )}
              </>
            )}
          </Async>
        ) : <Empty>Recommendations were not requested.</Empty>}
      </Panel>
      <Panel title="Imported RL evaluations">
        {evals ? (
          <Async resource={evals} label="RL evaluations">
            {page => page.items.length === 0 ? (
              <Empty>No evaluation imported into this project. The Defense Lab reads the committed bundle directly.</Empty>
            ) : (
              <ul className="divide-y divide-line text-xs">
                {page.items.map(item => (
                  <li key={item.id} className="py-2 text-ink">
                    <Mono>{item.id}</Mono> · protocol {item.protocol_version} · {item.checkpoint_ids.length} checkpoints · summary sha256 <Mono>{item.summary_sha256.slice(0, 12)}</Mono> · <Time iso={item.created_at} />
                    {item.simulation_derived && <Tag tone="warn">simulation-derived</Tag>}
                  </li>
                ))}
              </ul>
            )}
          </Async>
        ) : <Empty>Evaluations were not requested.</Empty>}
      </Panel>
    </div>
  );
}

function LiveRecommendation({ rec, reviews, onReview }: { rec: DefenseRecommendation; reviews: AnalystReview[]; onReview?: () => void }) {
  const [exp, setExp] = useState<{ status: 'idle' } | { status: 'loading' } | { status: 'ready'; data: RLExplanation } | { status: 'error'; error: unknown }>({ status: 'idle' });
  const ranked = rankQValues(rec.top_actions);
  const explain = async () => {
    setExp({ status: 'loading' });
    try { setExp({ status: 'ready', data: await api().recommendationExplanation(rec.id) }); }
    catch (error) { setExp({ status: 'error', error }); }
  };
  return (
    <li id={rec.id} className="rounded-md border border-line bg-panel-2 p-3" data-testid={`live-defense-rec-${rec.id}`}>
      <p className="flex flex-wrap items-center gap-2 text-sm text-ink">
        <span className="font-semibold">{actionInWords(rec.action, rec.node)}</span>
        {rec.node && <Tag>{rec.node}</Tag>}
        <Tag tone="info">recommendation only</Tag>
        <Tag tone="warn">{rec.simulation_derived ? 'simulation-derived' : 'unavailable provenance'}</Tag>
      </p>
      <dl className="mt-2 grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-3 gap-y-1 text-xs text-ink">
        <dt className="text-muted">Policy</dt><dd><Mono>{unavailable(rec.policy_version)}</Mono></dd>
        <dt className="text-muted">Checkpoint</dt><dd><Mono>{rec.checkpoint_sha256 ? rec.checkpoint_sha256.slice(0, 16) : 'unavailable'}</Mono></dd>
        <dt className="text-muted">Action / target</dt><dd>{rec.action} / {rec.node ?? 'unavailable'}</dd>
        <dt className="text-muted">Q-margin</dt><dd>{num(rec.q_margin, 3)} (score gap, not a probability)</dd>
        <dt className="text-muted">Created</dt><dd><Time iso={rec.created_at} /></dd>
      </dl>
      <p className="mt-1 text-xs text-warn">{rec.caveat || SIMULATION_CAVEAT}</p>
      <div className="mt-3">
        <p className="label-mono mb-2 text-xs text-ink">Stored action scores and alternatives (not live confidence)</p>
        <QRanking actions={ranked} sample={false} />
      </div>
      {exp.status === 'idle' && <Button className="mt-3 !px-2 !py-0.5 text-xs" onClick={() => void explain()}>Load stored explanation</Button>}
      {exp.status === 'loading' && <Loading label="Loading explanation" />}
      {exp.status === 'error' && <ErrorState error={exp.error} onRetry={() => void explain()} context="Explanation" />}
      {exp.status === 'ready' && (
        <div className="mt-3 text-xs text-ink">
          <p className="text-muted">Method {unavailable(exp.data.method)} · runner-up {exp.data.runner_up ?? 'unavailable'} · margin {num(exp.data.margin, 3)}</p>
          <p className="mt-1 text-warn">{exp.data.caveat}</p>
          {exp.data.top_contributions.length === 0 ? <p className="mt-2 text-muted">Occlusion contributions: unavailable</p> : (
            <ul className="mt-2">
              {exp.data.top_contributions.map((item, index) => (
                <li key={`${item.feature}-${index}`} className={item.delta_margin > 0 ? 'text-ok' : 'text-bad'}>
                  {item.delta_margin > 0 ? 'supports' : 'opposes'} · <Mono>{item.node ? `${item.node}.` : ''}{item.feature}</Mono> = {num(item.value, 3)} (Δmargin {num(item.delta_margin, 3)}, {item.kind})
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <AnalystReviewForm recommendationId={rec.id} history={reviews} onSaved={onReview} />
    </li>
  );
}

function LiveShadowDecision({ decision, reviews, onReview }: { decision: LivePolicyDecision; reviews: AnalystReview[]; onReview?: () => void }) {
  const ranked = decision.top_actions ? rankQValues(decision.top_actions) : [];
  return (
    <li id={decision.id} className="rounded-md border border-line bg-panel-2 p-3" data-testid={`live-defense-decision-${decision.id}`}>
      <p className="flex flex-wrap items-center gap-2 text-sm text-ink">
        <span className="font-semibold">{decision.action ? actionInWords(decision.action, decision.node) : 'Abstained'}</span>
        {decision.node && <Tag>{decision.node}</Tag>}
        <Tag tone="info">NOT EXECUTED</Tag>
        <Tag>{decision.producer.replaceAll('_', ' ')}</Tag>
        {decision.simulated ? <Tag tone="warn">simulated source</Tag> : <Tag tone="ok">authenticated</Tag>}
      </p>
      <dl className="mt-2 grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1 text-xs text-ink">
        <dt className="text-muted">Telemetry</dt><dd>{decision.telemetry_source}</dd>
        <dt className="text-muted">Inference</dt><dd>{decision.inference_ran ? 'ran' : decision.inference_status.replaceAll('_', ' ')}</dd>
        <dt className="text-muted">Authorization context</dt><dd>{decision.authorization_context}</dd>
        <dt className="text-muted">Rules</dt><dd>{decision.rule_hits.length ? decision.rule_hits.join(', ') : 'none fired'}</dd>
        <dt className="text-muted">Policy</dt><dd><Mono>{unavailable(decision.policy_version)}</Mono></dd>
        <dt className="text-muted">Checkpoint</dt><dd><Mono>{decision.checkpoint_sha256 ? decision.checkpoint_sha256.slice(0, 16) : 'unavailable'}</Mono></dd>
        <dt className="text-muted">Q-margin</dt><dd>{decision.q_margin === null ? 'unavailable (inference not run)' : `${num(decision.q_margin, 3)} (score gap, not a probability)`}</dd>
        <dt className="text-muted">Created</dt><dd><Time iso={decision.created_at} /></dd>
      </dl>
      {decision.abstention_reason && <p className="mt-1 text-xs text-muted">{decision.abstention_reason}</p>}
      <p className="mt-1 text-xs text-warn">{decision.caveat}</p>
      <div className="mt-3">
        <p className="label-mono mb-2 text-xs text-ink">Stored action scores (only if v6 ran)</p>
        {ranked.length === 0 ? <p className="text-xs text-muted">Q-values were not synthesized because inference did not run.</p> : <QRanking actions={ranked} sample={false} />}
      </div>
      <AnalystReviewForm livePolicyDecisionId={decision.id} history={reviews} onSaved={onReview} />
    </li>
  );
}

function PreviewExplain() {
  const rec = PREVIEW_RECOMMENDATIONS[0];
  if (!rec) return <Empty>No sample recommendation fixture.</Empty>;
  return (
    <div className="flex min-w-0 flex-col gap-6" data-testid="live-defense-preview-explain">
      <Panel title="Sample explainability chrome" actions={<SourceTag mode="layout-preview" />}>
        <SimulationCaveat text={rec.caveat} />
        <p className="mt-3 text-sm text-ink">{actionInWords(rec.action, rec.node)}</p>
        <p className="mt-1 text-xs text-muted">policy <Mono>{rec.policy_version}</Mono> · sample Q-margin {num(rec.q_margin, 3)}</p>
        <div className="mt-3">
          <p className="label-mono mb-2 text-xs">Sample action scores (not a loaded policy)</p>
          <QRanking actions={rec.top_actions} sample />
        </div>
        <ul className="mt-3 text-xs">
          {rec.contributions.map(item => (
            <li key={item.feature} className={item.delta_margin > 0 ? 'text-ok' : 'text-bad'}>
              {item.delta_margin > 0 ? 'supports' : 'opposes'} · <Mono>{item.node ? `${item.node}.` : ''}{item.feature}</Mono> Δmargin {num(item.delta_margin, 3)} ({item.kind})
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
