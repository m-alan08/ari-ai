import { describe, expect, it } from 'vitest';
import { affected, componentStates, coreComponent, isRedTeam, issueComponent, issueComponents, plainStep, plainToolName, redTeamEnd, refutedComponents, stepComponent, ticks } from '@/lib/demo/agent-map';
import { DEMO_REPOS } from '@/lib/demo/data';
import { analyze } from '@/lib/demo/scenarios';

const repo = DEMO_REPOS.find(r => r.name === 'payments-api')!;
const commit = repo.commits.find(c => c.scenario === 'authz')!;
const analysis = analyze(repo, commit);

describe('agent map', () => {
  it('pins every agent step to a part of the app, in orchestrator order', () => {
    const all = ticks(analysis, repo);
    expect(all).toHaveLength(analysis.agents.reduce((n, a) => n + a.steps.length, 0));
    expect(all[0]!.agent).toBe('scout');
    expect(all.every(t => repo.components.some(c => c.id === t.component))).toBe(true);
  });

  it('routes test users to auth and blocked outbound calls to the edge', () => {
    const base = { result: '', ms: 1, outcome: 'ok' as const };
    expect(stepComponent({ ...base, tool: 'fixture.identities', input: 'cus_a, cus_b' }, repo)).toBe('auth');
    expect(stepComponent({ ...base, tool: 'http.request', input: 'GET https://api.stripe.com', outcome: 'denied' }, repo)).toBe('edge');
    expect(stepComponent({ ...base, tool: 'sandbox.start', input: 'x' }, repo)).toBe(coreComponent(repo));
  });

  it('leaves untouched parts out and ends with the changed service fixed', () => {
    const all = ticks(analysis, repo);
    const touched = affected(all, repo);
    const end = componentStates(all, all.length, repo, analysis, true);
    expect(end[coreComponent(repo)]).toBe('fixed');
    for (const c of repo.components) if (!touched.has(c.id)) expect(end[c.id]).toBe('outside');
    const start = componentStates(all, 0, repo, analysis, true);
    expect(Object.values(start).every(s => s === 'waiting' || s === 'outside')).toBe(true);
    const mid = componentStates(all, 3, repo, analysis, true, all[3]);
    expect(Object.values(mid)).toContain('checking');
  });

  it('turns the part red exactly when Probe confirms, then Trace and the blue team work only on it', () => {
    const all = ticks(analysis, repo);
    const issue = issueComponent(analysis, repo)!;
    const verdict = redTeamEnd(all);
    expect(all[verdict - 1]!.agent).toBe('probe');
    // Before Probe's last step: nothing is red, parts looked at wait for proof.
    const before = componentStates(all, verdict - 1, repo, analysis, true);
    expect(Object.values(before)).not.toContain('issue');
    expect(Object.values(before)).toContain('scanned');
    expect(Object.values(before)).not.toContain('clear');
    // Probe's verdict: the confirmed part is red, the other parts it looked at are clear.
    const after = componentStates(all, verdict, repo, analysis, true);
    expect(after[issue]).toBe('issue');
    expect(Object.values(after)).not.toContain('scanned');
    // Every step after the red team (Trace, Diagnose, Patch, Fortify, Verify) is on a red part.
    const red = new Set(issueComponents(analysis, repo));
    const later = all.slice(verdict);
    expect(later.map(t => t.agent)).toEqual(expect.arrayContaining(['trace', 'diagnose', 'patch', 'fortify', 'verify']));
    expect(later.every(t => red.has(t.component))).toBe(true);
    // It stays red while they work, even when it is the part being worked on.
    for (let p = verdict; p < all.length; p++) expect(componentStates(all, p, repo, analysis, true, all[p])[issue]).toBe('issue');
    // A fix that Verify did not accept leaves it red.
    expect(componentStates(all, all.length, repo, analysis, false)[issue]).toBe('issue');
  });

  it('shows two confirmed issues and one false alarm on the ownership commit, then two fixes', () => {
    const all = ticks(analysis, repo);
    const verdict = redTeamEnd(all);
    const after = componentStates(all, verdict, repo, analysis, true);
    expect(Object.entries(after).filter(([, s]) => s === 'issue').map(([id]) => id).sort()).toEqual(['api', 'logs']);
    expect(Object.entries(after).filter(([, s]) => s === 'refuted').map(([id]) => id)).toEqual(['edge']);
    expect(refutedComponents(analysis, repo)).toEqual(['edge']);
    const end = componentStates(all, all.length, repo, analysis, true);
    expect(end.api).toBe('fixed');
    expect(end.logs).toBe('fixed');
    expect(end.edge).toBe('refuted'); // a false alarm stays a false alarm
    // Only suspected parts are visited: everything else is left alone.
    for (const id of ['auth', 'db', 'refunds']) expect(end[id]).toBe('outside');
  });

  it('stops after the red team on a clean commit: nothing turns red and the blue team never runs', () => {
    for (const scenario of ['clean', 'dependency'] as const) {
      const clean = repo.commits.find(c => c.scenario === scenario)!;
      const a = analyze(repo, clean);
      const all = ticks(a, repo);
      expect(issueComponent(a, repo)).toBeNull();
      expect(all.every(t => isRedTeam(t.agent))).toBe(true);
      const end = componentStates(all, all.length, repo, a, false);
      expect(Object.values(end)).not.toContain('issue');
      expect(Object.values(end)).not.toContain('fixed');
      // A suspicion Probe refuted reads as a false alarm; with nothing to test it is simply clear.
      expect(end[coreComponent(repo)]).toBe(scenario === 'clean' ? 'refuted' : 'clear');
    }
  });

  it('describes tool steps in plain words', () => {
    expect(plainStep({ tool: 'http.request', input: '', result: '', ms: 1, outcome: 'ok' })).toBe('Sent a test request');
    expect(plainStep({ tool: 'unknown.tool', input: '', result: '', ms: 1, outcome: 'ok' })).toBe('unknown.tool');
  });

  it('plain-speaks persisted tool names recorded with ns__tool separators', () => {
    expect(plainStep({ tool: 'code__read_span', input: '', result: '', ms: 1, outcome: 'ok' })).toBe('Read the code');
    expect(plainStep({ tool: 'worktree__write_file', input: '', result: '', ms: 1, outcome: 'ok' })).toBe('Edited a scratch copy');
    expect(plainStep({ tool: 'checks__run_template', input: '', result: '', ms: 1, outcome: 'ok' })).toBe('Ran a check in the sandbox');
    expect(plainStep({ tool: 'submit_result', input: '', result: '', ms: 1, outcome: 'ok' })).toBe('Reported its result');
    expect(stepComponent({ tool: 'worktree__write_file', input: 'x', result: '', ms: 1, outcome: 'ok' }, repo)).toBe(coreComponent(repo));
  });

  it('plain-speaks persisted tool names in the recorded tool calls disclosure', () => {
    expect(plainToolName('read_file')).toBe('Read the code');
    expect(plainToolName('submit_result')).toBe('Reported its result');
    expect(plainToolName('sandbox__run_tests')).toBe('Ran the tests');
    expect(plainToolName('some.internal_tool')).toBe('some.internal_tool');
  });
});
