import { ApiError, ProtocolError, type CheckCategory, type ScopeCreate } from '@hackatl/frontend-transport';
import { describe, expect, it } from 'vitest';
import { describeError } from '@/lib/api';
import { parseUnifiedDiff } from '@/lib/diff';
import { duration, githubLink, relativeTime, shortSha } from '@/lib/format';
import { domain, episodeStory, metricRows, rewardTotals, sampleSize, stepObservation } from '@/lib/lab';
import type { DefenseLabBundle, LabEpisode } from '@/lib/lab-types';
import { matchExpectationStatements } from '@/lib/expectations';
import { findingStory, repoFullNameForEvidence } from '@/lib/finding-story';
import { latestAssessmentEvent, projectJourney, statesFromLog } from '@/lib/journey';
import { projectOverview } from '@/lib/overview';
import { SCOPE_CHECK_CATEGORIES } from '@/lib/scope';
import { AGENTS, ASSESSMENT_STATE, effectiveScopeStatus, FINDING_STATUS, agentRunSummary, handoffNote, providerLabel, railSteps } from '@/lib/status';

describe('lab episode story', () => {
  it('uses only the recorded actions and outcome', () => {
    const episode = {
      policy: 'ddqn', scenario: 's1', episode_seed: 1, training_seed: 0, total_return: -1,
      compromised: true, impact_nodes: ['db'],
      steps: [
        { t: 0, action: 'isolate:db', reward: -0.2, reward_components: {}, nodes: [] },
        { t: 1, action: 'noop', reward: -0.1, reward_components: {}, nodes: [] },
      ],
    } satisfies LabEpisode;
    const text = episodeStory(episode);
    expect(text).toContain('isolate:db');
    expect(text).toContain('compromised');
    expect(text).toContain('db');
    expect(text).toContain('not a live network');
  });
});

describe('lab step observation', () => {
  const node = (name: string, alert: number, hidden: 'none' | 'impact') => ({
    node: name, alert_rate: alert, anomaly_score: 0, monitoring_level: 0,
    rate_limited: false, isolated: false, reassessment_pending: false, hidden_stage_ground_truth: hidden,
  });
  it('names the highest alert and the recorded action, not the hidden stage', () => {
    const text = stepObservation({
      t: 3, action: 'rate_limit:api', reward: 0, reward_components: {},
      nodes: [node('db', 0.01, 'impact'), node('api', 0.14, 'none'), node('auth', 0.14, 'none')],
    });
    expect(text).toContain('0.14 on api, auth');
    expect(text).toContain('rate-limit api in this test environment');
    expect(text).not.toContain('impact');
    expect(text).not.toContain('db');
  });
  it('keeps an unknown action as the recorded string', () => {
    expect(stepObservation({ t: 0, action: 'teleport:db', reward: 0, reward_components: {}, nodes: [] }))
      .toContain('teleport:db');
  });
});

describe('finding expectations', () => {
  it('keeps an id visible when the snapshot has no matching statement', () => {
    const rows = matchExpectationStatements(
      ['exp_known', 'exp_missing'],
      [{ id: 'exp_known', statement: 'A customer can only read their own payment.', status: 'confirmed' }],
    );
    expect(rows[0]).toEqual({ id: 'exp_known', statement: 'A customer can only read their own payment.', status: 'confirmed' });
    expect(rows[1]).toEqual({ id: 'exp_missing', statement: null, status: null });
  });
});

describe('finding story', () => {
  it('does not claim a fix worked without a verification verdict', () => {
    const lines = Object.fromEntries(findingStory({
      title: 'Another user could read a payment',
      status: 'patch_proposed',
      severityRationale: null,
      validationOutcome: 'confirmed',
      patchRationale: 'Check the payment owner.',
      verification: null,
    }).map(line => [line.label, line.text]));
    expect(lines['Did it work?']).toMatch(/has not recorded a verdict/);
    expect(lines['Why it matters']).toMatch(/No impact statement/);
    expect(lines['What happens next?']).toMatch(/has not been independently accepted/);
  });
  it('uses the scope target only when that scope covers one repository', () => {
    const repos = [{ id: 'rep_a', full_name: 'acme/a' }, { id: 'rep_b', full_name: 'acme/b' }];
    expect(repoFullNameForEvidence(repos, [{ repo_id: 'rep_b' }])).toBe('acme/b');
    expect(repoFullNameForEvidence(repos, [{ repo_id: 'rep_a' }, { repo_id: 'rep_b' }])).toBeUndefined();
    // A multi-repository scope resolves through the assessment's own repository (0.11).
    expect(repoFullNameForEvidence(repos, [{ repo_id: 'rep_a' }, { repo_id: 'rep_b' }], 'rep_b')).toBe('acme/b');
    // An unknown assessment repo never falls through to a guess among several targets.
    expect(repoFullNameForEvidence(repos, [{ repo_id: 'rep_a' }, { repo_id: 'rep_b' }], 'rep_zzz')).toBeUndefined();
    expect(repoFullNameForEvidence([{ id: 'rep_a', full_name: 'acme/a' }], null)).toBe('acme/a');
  });
});

describe('project overview', () => {
  const base = { projectId: 'prj_1', repoCount: 1, activeScopeCount: 1, monitoringEnabled: true, assessments: [], findings: [] };
  it('asks to connect a repository before anything else', () => {
    const view = projectOverview({ ...base, repoCount: 0 });
    expect(view.action.label).toBe('Connect a repository');
    expect(view.action.href).toBe('/projects/prj_1/settings#connect-repo');
    expect(view.headline).toMatch(/No repository/);
  });
  it('points a new review to the Commits workspace', () => {
    const view = projectOverview(base);
    expect(view.action.href).toBe('/projects/prj_1/commits#start-assessment');
  });
  it('uses the newest assessment and does not treat hypotheses as confirmed exploits', () => {
    const view = projectOverview({
      ...base,
      assessments: [
        { id: 'asm_old', state: 'COMPLETED', created_at: '2026-10-01T00:00:00Z' },
        { id: 'asm_new', state: 'AWAITING_APPROVAL', created_at: '2026-10-17T00:00:00Z' },
      ],
      findings: [
        { kind: 'security', status: 'hypothesis' },
        { kind: 'security', status: 'validated' },
        { kind: 'functional', status: 'diagnosed' },
        { kind: 'security', status: 'verified' },
      ],
    });
    expect(view.action.href).toContain('/approvals');
    expect(view.securityOpen).toBe(1);
    expect(view.codeOpen).toBe(1);
    expect(view.verifiedFixes).toBe(1);
  });
});

describe('project journey', () => {
  const idle = {
    repoCount: 1,
    activeScopeCount: 1,
    current: null,
    visited: [] as const,
    findingStatuses: [] as const,
    monitoringEnabled: false,
    monitoringKnown: true,
    monitoringEventCount: 0,
    monitoringTruncated: false,
    monitoringFailed: false,
    newestMonitoringSimulated: false,
    eventLogTruncated: false,
  };
  const byId = (view: ReturnType<typeof projectJourney>) => Object.fromEntries(view.map(stage => [stage.id, stage]));

  it('blocks understanding until a repository is connected', () => {
    const view = byId(projectJourney({ ...idle, repoCount: 0 }));
    expect(view.understand?.status).toBe('blocked');
    expect(view.security?.status).toBe('not_started');
    expect(view.monitor?.status).toBe('not_applicable');
  });

  it('marks only phases the event log entered, and does not treat a hypothesis as verified', () => {
    const view = byId(projectJourney({
      ...idle,
      current: 'VALIDATION',
      visited: ['CREATED', 'SCOPE_CHECK', 'FUNCTIONAL_REVIEW', 'RECON', 'VALIDATION'],
      findingStatuses: ['hypothesis'],
    }));
    expect(view.understand?.status).toBe('skipped');
    expect(view.review?.status).toBe('completed');
    expect(view.security?.status).toBe('running');
    expect(view.repair?.status).toBe('not_started');
    expect(view.verify?.status).toBe('not_started');
    expect(view.verify?.detail).not.toMatch(/secure/);
  });

  it('a skipped transition is not painted as entered', () => {
    const visited = statesFromLog([
      { type: 'assessment.state_changed', state: 'INDEXING', message: 'state -> INDEXING' },
      { type: 'assessment.state_changed', state: 'RECON', message: 'skipped (no hypotheses); awaiting functional remediation approval' },
    ]);
    expect(visited).toEqual(['INDEXING']);
  });

  it('pauses repair for a human and does not call that a finished fix', () => {
    const view = byId(projectJourney({
      ...idle,
      current: 'AWAITING_APPROVAL',
      visited: ['INDEXING', 'FUNCTIONAL_REVIEW', 'RECON', 'VALIDATION', 'ANALYSIS', 'DIAGNOSIS', 'AWAITING_APPROVAL'],
    }));
    expect(view.repair?.status).toBe('needs_attention');
    expect(view.repair?.detail).toMatch(/Nothing is merged/);
    expect(view.verify?.status).toBe('not_started');
  });

  it('attributes a failure to the last phase the log entered', () => {
    const view = byId(projectJourney({
      ...idle,
      current: 'FAILED',
      visited: ['CREATED', 'SCOPE_CHECK', 'INDEXING'],
    }));
    expect(view.understand?.status).toBe('failed');
    expect(view.review?.status).toBe('not_started');
  });

  it('does not verify a run that never entered repair', () => {
    const view = byId(projectJourney({
      ...idle,
      current: 'COMPLETED',
      visited: ['INDEXING', 'FUNCTIONAL_REVIEW', 'RECON', 'REPORTING', 'COMPLETED'],
    }));
    expect(view.repair?.status).toBe('skipped');
    expect(view.verify?.status).toBe('skipped');
  });

  it('verification comes from a finding verdict, not from the run merely finishing', () => {
    const view = byId(projectJourney({
      ...idle,
      current: 'COMPLETED',
      visited: ['INDEXING', 'FUNCTIONAL_REVIEW', 'RECON', 'VALIDATION', 'ANALYSIS', 'DIAGNOSIS', 'AWAITING_APPROVAL', 'REMEDIATION', 'HARDENING', 'REPORTING', 'COMPLETED'],
      findingStatuses: ['verified'],
    }));
    expect(view.verify?.status).toBe('completed');
    expect(view.verify?.detail).toMatch(/not a claim about the whole application/);
  });

  it('leaves phases unmarked when the event log did not load', () => {
    const view = byId(projectJourney({ ...idle, current: 'COMPLETED', visited: null }));
    expect(view.understand?.status).toBe('not_started');
    expect(view.understand?.detail).toMatch(/event log did not load/);
  });

  it('names the 500-event page when a skipped phase may be beyond it', () => {
    const view = byId(projectJourney({
      ...idle,
      current: 'COMPLETED',
      visited: ['INDEXING', 'FUNCTIONAL_REVIEW', 'RECON', 'REPORTING', 'COMPLETED'],
      eventLogTruncated: true,
    }));
    expect(view.repair?.status).toBe('skipped');
    expect(view.repair?.detail).toMatch(/first 10,000 events/);
    expect(view.repair?.detail).toMatch(/later page may/);
  });

  it('keeps the newest recorded event message and drops an empty one', () => {
    expect(latestAssessmentEvent([
      { seq: 1, message: 'state -> INDEXING', at: '2026-10-17T00:00:00Z' },
      { seq: 3, message: '  ', at: '2026-10-17T00:02:00Z' },
      { seq: 2, message: 'state -> RECON', at: '2026-10-17T00:01:00Z' },
    ])).toEqual({ message: 'state -> RECON', at: '2026-10-17T00:01:00Z' });
  });

  it('labels a simulated monitoring event and a failed one', () => {
    const failed = byId(projectJourney({ ...idle, monitoringEnabled: true, monitoringFailed: true }));
    expect(failed.monitor?.status).toBe('failed');
    const simulated = byId(projectJourney({
      ...idle,
      monitoringEnabled: true,
      monitoringEventCount: 1,
      newestMonitoringSimulated: true,
    }));
    expect(simulated.monitor?.detail).toMatch(/simulated/);
  });
});

describe('state rail', () => {
  it('marks only states the run actually entered as done; jumped-over states are skipped', () => {
    const steps = railSteps('VALIDATION', new Set(['CREATED', 'SCOPE_CHECK', 'FUNCTIONAL_REVIEW', 'RECON', 'VALIDATION']));
    const phase = Object.fromEntries(steps.map(s => [s.state, s.phase]));
    expect(phase.CREATED).toBe('done');
    expect(phase.INDEXING).toBe('skipped');
    expect(phase.VALIDATION).toBe('current');
    expect(phase.ANALYSIS).toBe('upcoming');
    expect(phase.COMPLETED).toBe('upcoming');
  });
  it('a failed run never paints later states done', () => {
    const steps = railSteps('FAILED', new Set(['CREATED', 'SCOPE_CHECK', 'INDEXING']));
    expect(steps.filter(s => s.phase === 'done').map(s => s.state)).toEqual(['CREATED', 'SCOPE_CHECK', 'INDEXING']);
    expect(steps.find(s => s.state === 'COMPLETED')?.phase).toBe('upcoming');
  });
  it('completed is done', () => {
    expect(railSteps('COMPLETED', new Set(['COMPLETED'])).find(s => s.state === 'COMPLETED')?.phase).toBe('done');
  });
});

describe('agent team summary', () => {
  it('does not invent work for an agent that has not run', () => {
    expect(agentRunSummary([])).toBe('Has not run.');
    expect(handoffNote([], [])).toBe('Nothing to hand off: this agent has not run.');
  });
  it('counts only recorded runs and tool calls', () => {
    expect(agentRunSummary([{ status: 'succeeded', tool_calls: [{}, {}] }])).toBe('1 recorded run, 2 tool calls.');
    expect(handoffNote([{ status: 'succeeded', tool_calls: [] }], [])).toMatch(/has not run yet/);
    expect(handoffNote([{ status: 'failed', tool_calls: [], error: { code: 'LLM_UNAVAILABLE', message: 'no' } }], [])).toMatch(/no successful handoff/);
  });
  it('lists the eight runtime agents in orchestrator order', () => {
    expect(AGENTS.map(a => a.name)).toEqual(['reviewer', 'scout', 'probe', 'trace', 'diagnose', 'patch', 'verify', 'fortify']);
    expect(AGENTS.every(a => a.job.length > 0 && a.handsTo.length > 0)).toBe(true);
  });
});

describe('status vocabulary', () => {
  it('every status has a text label and glyph, so colour is never the only signal', () => {
    for (const view of [...Object.values(FINDING_STATUS), ...Object.values(ASSESSMENT_STATE)]) {
      expect(view.label.length).toBeGreaterThan(0);
      expect(view.glyph.length).toBeGreaterThan(0);
    }
  });
  it('uses readable labels for real assessment run states', () => {
    expect(ASSESSMENT_STATE.AWAITING_APPROVAL.label).toBe('Awaiting human approval');
    expect(ASSESSMENT_STATE.COMPLETED.label).toBe('Completed');
  });
  it('only provider_mode=live is labelled live', () => {
    expect(providerLabel('live').label).toBe('Live provider');
    expect(providerLabel('fake').label).toMatch(/scripted/);
    expect(providerLabel('none').label).toBe('Deterministic (no model call)');
    expect(providerLabel('replay').label).toMatch(/Replay/);
    expect(providerLabel('mystery').label).not.toMatch(/Live/);
  });
});

describe('scope check categories', () => {
  it('offers Probe library ids that satisfy ScopeCreate.allowed_check_categories', () => {
    expect([...SCOPE_CHECK_CATEGORIES]).toEqual(['access_control.ownership', 'access_control.authentication', 'input_validation.bounds']);
    const aliases: CheckCategory[] = ['authorization.ownership', 'input.bounds'];
    const body: ScopeCreate = {
      name: 'sandbox',
      targets: [
        {
          kind: 'sandbox_build',
          repo_id: 'rep_01JFIXTURE0000000000000REP',
          build_recipe_ref: 'build_recipe.json',
          exposed_ports: [],
        },
      ],
      allowed_check_categories: [...SCOPE_CHECK_CATEGORIES, ...aliases],
      expires_at: '2026-11-17T00:00:00Z',
      notes: null,
    };
    expect(body.allowed_check_categories).toHaveLength(5);
  });
});

describe('effective scope status', () => {
  const now = new Date('2026-10-26T00:00:00Z');

  it('expires an active scope whose expiry has passed', () => {
    expect(effectiveScopeStatus({ status: 'active', expires_at: '2026-10-25T23:59:59Z' }, now)).toBe('expired');
  });

  it('keeps an active scope whose expiry is in the future', () => {
    expect(effectiveScopeStatus({ status: 'active', expires_at: '2026-10-26T00:00:01Z' }, now)).toBe('active');
  });

  it('expires a pending scope whose expiry has passed', () => {
    expect(effectiveScopeStatus({ status: 'pending', expires_at: '2026-10-25T23:59:59Z' }, now)).toBe('expired');
  });

  it('leaves revoked scopes revoked regardless of expiry', () => {
    expect(effectiveScopeStatus({ status: 'revoked', expires_at: '2026-10-25T23:59:59Z' }, now)).toBe('revoked');
  });

  it('leaves the stored status unchanged when expiry is unparseable', () => {
    expect(effectiveScopeStatus({ status: 'active', expires_at: 'not-a-date' }, now)).toBe('active');
  });
});

describe('unified diff parser', () => {
  it('numbers old/new lines across hunks and counts changes', () => {
    const diff = 'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -10,3 +10,3 @@ def refund\n ctx\n-    if body.amount > p.amount:\n+    if body.amount + refunded > p.amount:\n ctx2\n';
    const [file] = parseUnifiedDiff(diff);
    expect(file?.newPath).toBe('app.py');
    expect(file?.added).toBe(1);
    expect(file?.removed).toBe(1);
    const lines = file?.lines ?? [];
    expect(lines[1]).toMatchObject({ kind: 'context', oldNo: 10, newNo: 10 });
    expect(lines[2]).toMatchObject({ kind: 'del', oldNo: 11, newNo: null });
    expect(lines[3]).toMatchObject({ kind: 'add', oldNo: null, newNo: 11 });
    expect(lines[4]).toMatchObject({ kind: 'context', oldNo: 12, newNo: 12 });
  });
  it('returns nothing for non-diff text instead of guessing', () => {
    expect(parseUnifiedDiff('<script>alert(1)</script>')).toEqual([]);
  });
});

describe('formatting', () => {
  it('builds GitHub links only from valid parts at an exact SHA', () => {
    expect(githubLink('acme/payments-lite', 'a'.repeat(40), 'target/app.py', 3, 5)).toBe(`https://github.com/acme/payments-lite/blob/${'a'.repeat(40)}/target/app.py#L3-L5`);
    expect(githubLink('acme/payments-lite', 'main', 'app.py')).toBeNull();
    expect(githubLink('javascript:alert(1)', 'a'.repeat(40), 'x')).toBeNull();
    expect(githubLink(undefined, 'a'.repeat(40), 'x')).toBeNull();
  });
  it('time helpers are total', () => {
    expect(shortSha(null)).toBe('—');
    expect(relativeTime('not a date')).toBe('—');
    expect(relativeTime('2026-01-01T00:00:00Z', Date.parse('2026-01-01T00:02:00Z'))).toBe('2m ago');
    expect(duration('2026-01-01T00:00:00Z', '2026-01-01T00:00:01.500Z')).toBe('1.5 s');
  });
});

describe('error descriptions', () => {
  it('distinguishes API, protocol and network failures without echoing payloads', () => {
    const api = describeError(new ApiError(403, { code: 'FORBIDDEN', message: 'approver must differ from requester', details: { secret: 'x' }, request_id: 'req_1' }));
    expect(api).toMatchObject({ kind: 'api', title: 'Not permitted', code: 'FORBIDDEN', requestId: 'req_1' });
    expect(JSON.stringify(api)).not.toContain('secret');
    expect(describeError(new ProtocolError('Invalid Finding response')).title).toBe('Backend response rejected');
    expect(describeError(new TypeError('Failed to fetch')).title).toBe('Backend unreachable');
  });
});

const ci = (mean: number, lo: number, hi: number) => ({ mean, ci95: [lo, hi] as [number, number] });
const bundle: DefenseLabBundle = {
  schema_version: '0.1.0', env_id: 'DefenseSimEnv-v0', simulation_only: true, caveat: 'sim', source_run: 'r', git_sha: 'a'.repeat(40),
  checkpoint_sha256: {}, training_seeds: [0, 1, 2, 3, 4], episodes_per_config_per_seed: 100,
  scenarios: [{ name: 's1', config_hash: 'h', split: 'eval', horizon: 200, nodes: [], edges: [] }, { name: 's2', config_hash: 'h', split: 'eval', horizon: 200, nodes: [], edges: [] }],
  metrics: [
    { policy: 'ddqn', pooled: { return: ci(-29, -32, -26) }, per_scenario: { s1: { return: ci(-10, -12, -8) } } },
    { policy: 'noop', pooled: { return: ci(-200, -210, -190) }, per_scenario: {} },
  ],
  episodes: [],
};

describe('Defense Lab helpers', () => {
  it('copies means and CIs verbatim and reports missing values as null', () => {
    expect(metricRows(bundle, 'return', null)).toEqual([{ policy: 'ddqn', mean: -29, lo: -32, hi: -26 }, { policy: 'noop', mean: -200, lo: -210, hi: -190 }]);
    expect(metricRows(bundle, 'return', 's1')[1]).toEqual({ policy: 'noop', mean: null, lo: null, hi: null });
  });
  it('sample size is seeds × episodes (× scenarios when pooled)', () => {
    expect(sampleSize(bundle, null)).toBe(1000);
    expect(sampleSize(bundle, 's1')).toBe(500);
  });
  it('domain always contains zero and every CI bound', () => {
    expect(domain(metricRows(bundle, 'return', null))).toEqual([-210, 0]);
    expect(domain([])).toEqual([0, 1]);
  });
  it('reward totals sum named components up to the current step', () => {
    const ep: LabEpisode = { policy: 'ddqn', scenario: 's1', episode_seed: 1, training_seed: 0, total_return: -0.5, compromised: false, impact_nodes: [], steps: [
      { t: 0, action: 'noop', reward: -0.2, reward_components: { action_cost: -0.2, impact: 0 }, nodes: [] },
      { t: 1, action: 'noop', reward: -0.3, reward_components: { action_cost: -0.1, impact: -0.2 }, nodes: [] },
    ] };
    expect(rewardTotals(ep, 1)).toEqual({ action_cost: -0.2, impact: 0 });
    const all = rewardTotals(ep);
    expect(all.action_cost).toBeCloseTo(-0.3);
    expect(all.impact).toBeCloseTo(-0.2);
  });
});
