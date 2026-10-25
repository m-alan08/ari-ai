import type { DemoComponent, DemoRepo } from '@/lib/demo/data';
import type { AgentId, AgentRunView, Analysis, ToolStep } from '@/lib/demo/scenarios';

/**
 * The seven-agent replay as a map: each recorded tool step is pinned to the part of the
 * system it touched, so the page can show which agent is checking which part of the app.
 * Illustrative (ADR 0013): the steps come from the simulated scenario for the commit.
 */

export type Team = AgentRunView['team'];
export const TEAM_COLOR: Record<Team, string> = { Red: '#ff7a5c', Blue: '#6ea8ff', Independent: '#5fd49a' };

export interface Tick {
  agent: AgentId;
  team: Team;
  step: ToolStep;
  index: number;
  component: string;
}

export type ComponentState = 'outside' | 'waiting' | 'checking' | 'scanned' | 'clear' | 'refuted' | 'issue' | 'fixed';

/** Plain-language description of a tool step, for people who do not read tool names. */
const PLAIN: [RegExp, string][] = [
  [/^intel\.changed_symbols/, 'Found what this commit changed'],
  [/^intel\.entry_points/, 'Listed every way in from outside'],
  [/^intel\.call_graph/, 'Followed the code path'],
  [/^intel\.convention/, 'Compared it with similar code'],
  [/^intel\.(path|taint)/, 'Traced where user input goes'],
  [/^intel\.(symbol|data_classes)/, 'Looked for sensitive data'],
  [/^sandbox\.start/, 'Started a private copy of the app'],
  [/^sandbox\.run_tests/, 'Ran the tests'],
  [/^fixture\.identities/, 'Created test users'],
  [/^http\.request/, 'Sent a test request'],
  [/^worktree\.create/, 'Opened a scratch copy of the code'],
  [/^fs\.edit/, 'Edited the code'],
  [/^tests\.generate/, 'Wrote new tests'],
  [/^policy\.suggest_rule/, 'Suggested a guard rule'],
  [/^rl\.recommend/, 'Asked the defense policy'],
  [/^review\.findings/, "Read the reviewer's notes"],
  [/^git\.push/, 'Tried to push code'],
  [/^git\.blame/, 'Checked the history of this code'],
  [/^deps\./, 'Checked the dependencies'],
  [/^score\.cvss/, 'Scored how severe it is'],
  [/^record\.hypothesis/, 'Wrote down what might be wrong'],
  [/^record\.attack_path/, 'Wrote down how far an attacker could get'],
  [/^record\.(patch_candidate|remediation_plan)/, 'Saved the proposed fix'],
  [/^record\.(check|verification)/, 'Saved the result'],
  // Real pipeline tool names (security-agents tools/catalog).
  [/^read_file/, 'Read the code'],
  [/^code\.read_span/, 'Read the code'],
  [/^code\.search/, 'Searched the code'],
  [/^graph\.neighbors/, 'Followed the call graph'],
  [/^expectations\.for_subject/, 'Checked what the code should do'],
  [/^findings\.history/, 'Checked past findings'],
  [/^static\.analyze/, 'Ran static analysis'],
  [/^checks\.list_templates/, 'Listed the checks it can run'],
  [/^checks\.run_template/, 'Ran a check in the sandbox'],
  [/^sandbox\.check_run/, 'Ran a check in the sandbox'],
  [/^sandbox\.tests_run/, 'Ran the tests in the sandbox'],
  [/^tests\.run/, 'Ran the tests'],
  [/^worktree\.(write_file|edit_file)/, 'Edited a scratch copy'],
  [/^worktree\.diff/, 'Prepared the fix diff'],
  [/^patch\.apply_fresh/, 'Re-applied the fix on a clean copy'],
  [/^build_recipe\./, 'Read the build recipe'],
  [/^specialist\.inference/, 'Consulted a specialist model'],
  [/^submit_result$/, 'Reported its result'],
];

/** Persisted pipeline names use `ns__tool`; demo names use `ns.tool`. Normalize for matching. */
const toolName = (tool: string) => tool.replace(/__/g, '.');

export function plainStep(step: ToolStep): string {
  return PLAIN.find(([re]) => re.test(toolName(step.tool)))?.[1] ?? step.tool;
}

/** Plain-language name for a persisted tool id (ns__tool or ns.tool), for non-technical readers. */
export function plainToolName(tool: string): string {
  const normalized = toolName(tool);
  return PLAIN.find(([re]) => re.test(normalized))?.[1] ?? normalized;
}

const KIND_HINTS: [RegExp, DemoComponent['kind'][]][] = [
  [/refund|queue|worker|job/i, ['worker']],
  [/ledger|\bdb\b|sql|query|postgres|store\.|database|lockfile|migration/i, ['data']],
  [/audit|\blog/i, ['logging']],
  [/session|token|auth|login|sso|identit|cus_a|cus_b/i, ['identity']],
  [/cdn|edge|gateway|ingress|rate.?limit|waf/i, ['edge']],
];

function byKind(repo: DemoRepo, kinds: string[]): string | undefined {
  return repo.components.find(c => kinds.includes(c.kind))?.id;
}

/** The main application component (the one a commit to this repository lives in). */
export function coreComponent(repo: DemoRepo): string {
  return byKind(repo, ['service']) ?? byKind(repo, ['site']) ?? repo.components[0]!.id;
}

/** The component of a given kind in this repository (the changed service when there is none). */
export function partOfKind(repo: DemoRepo, kind: string | undefined): string {
  return (kind && byKind(repo, [kind])) || coreComponent(repo);
}

/** Which component a step touched. Unknown → the core component. */
export function stepComponent(step: ToolStep, repo: DemoRepo, previous?: string): string {
  const core = coreComponent(repo);
  if (step.on) return partOfKind(repo, step.on);
  if (step.outcome === 'denied' && /https?:\/\//.test(step.input)) return byKind(repo, ['edge']) ?? core;
  const tool = toolName(step.tool);
  if (/^fixture\.identities/.test(tool)) return byKind(repo, ['identity']) ?? core;
  // A request inside the sandbox hits the changed service's endpoint, whoever it is sent as.
  if (/^http\.request/.test(tool)) return core;
  if (/^(policy\.suggest_rule|rl\.recommend)/.test(tool)) return byKind(repo, ['edge']) ?? core;
  if (/^(record|review)\./.test(tool)) return previous ?? core;
  if (/^(sandbox|worktree|fs|tests|git)\./.test(tool)) return core;
  // Real pipeline tools: edits and sandboxed checks happen inside the changed service; pure
  // reads keep the sector the agent was last on.
  if (/^(patch\.apply_fresh|worktree\.|checks\.|specialist\.inference)/.test(tool)) return core;
  if (/^(code|graph|expectations|findings|static|build_recipe)\./.test(tool)) return previous ?? core;
  if (/^submit_result$/.test(tool)) return previous ?? core;
  const text = `${step.input} ${step.result}`;
  for (const [re, kinds] of KIND_HINTS) {
    const hit = byKind(repo, kinds);
    if (hit && re.test(text)) return hit;
  }
  return core;
}

/** Best-effort component kind for a repository-relative file path. */
export function kindForPath(path: string): string | undefined {
  for (const [re, kinds] of KIND_HINTS) {
    if (re.test(path)) return kinds[0];
  }
  return undefined;
}

/** The red team looks for problems and may touch any part of the change. */
const RED_TEAM: ReadonlySet<AgentId> = new Set(['scout', 'probe']);
export const isRedTeam = (agent: AgentId) => RED_TEAM.has(agent);

/** Every part with a finding Probe confirmed (empty when the red team confirmed nothing). */
export function issueComponents(analysis: Analysis, repo: DemoRepo): string[] {
  const probe = analysis.agents.find(a => a.agent === 'probe');
  if (probe?.status !== 'done') return [];
  return [...new Set(analysis.findings.map(f => partOfKind(repo, f.on)))];
}

/** The main confirmed part (the first finding's), or null when nothing was confirmed. */
export function issueComponent(analysis: Analysis, repo: DemoRepo): string | null {
  return issueComponents(analysis, repo)[0] ?? null;
}

/**
 * Every step of the run, in orchestrator order, pinned to a component. Red-team steps go where
 * they looked; Trace, the blue team and Verify only ever work on a confirmed issue (the one a
 * step names, else the main one).
 */
export function ticks(analysis: Analysis, repo: DemoRepo): Tick[] {
  const issues = issueComponents(analysis, repo);
  const main = issues[0] ?? coreComponent(repo);
  const out: Tick[] = [];
  for (const agent of analysis.agents) {
    let previous: string | undefined;
    agent.steps.forEach((step, index) => {
      const named = step.on ? partOfKind(repo, step.on) : null;
      const component = isRedTeam(agent.agent) ? stepComponent(step, repo, previous) : named && issues.includes(named) ? named : main;
      previous = component;
      out.push({ agent: agent.agent, team: agent.team, step, index, component });
    });
  }
  return out;
}

/** Components the run touched at all: everything else is "not in this change". */
export function affected(all: Tick[], repo: DemoRepo): Set<string> {
  return new Set([coreComponent(repo), ...all.map(t => t.component)]);
}

/** Ticks played when the red team has finished (Probe's verdict is in). */
export function redTeamEnd(all: Tick[]): number {
  let end = 0;
  all.forEach((t, i) => { if (isRedTeam(t.agent)) end = i + 1; });
  return end;
}

/**
 * Parts where a suspicion was raised and Probe refuted it (false alarms). A clean commit whose
 * only hypothesis was refuted counts too: the suspicion was about the changed service.
 */
export function refutedComponents(analysis: Analysis, repo: DemoRepo): string[] {
  const probe = analysis.agents.find(a => a.agent === 'probe');
  if (probe?.status !== 'done') return [];
  const issues = new Set(issueComponents(analysis, repo));
  const named = (analysis.falseAlarms ?? []).map(f => partOfKind(repo, f.on));
  const parts = analysis.findings.length === 0 ? [coreComponent(repo), ...named] : named;
  return [...new Set(parts)].filter(id => !issues.has(id));
}

/**
 * State of each component after `played` ticks (`active` is the step in progress, if any).
 * While the red team works, parts it has looked at wait for Probe's verdict ("scanned"). Once
 * Probe is done, each confirmed part turns to "issue" and stays red while Trace and the blue team
 * work on it; it becomes "fixed" only when Verify accepted the fix. A suspicion Probe refuted is
 * a "refuted" false alarm. A part the red team only passed through is "clear" (checked).
 */
export function componentStates(all: Tick[], played: number, repo: DemoRepo, analysis: Analysis, accepted: boolean, active?: Tick): Record<string, ComponentState> {
  const touched = affected(all, repo);
  const seen = new Set(all.slice(0, played).map(t => t.component));
  const verdictIn = played >= redTeamEnd(all);
  const issues = new Set(verdictIn ? issueComponents(analysis, repo) : []);
  const refuted = new Set(verdictIn ? refutedComponents(analysis, repo) : []);
  const verifyDone = played >= all.length && all.length > 0;
  const states: Record<string, ComponentState> = {};
  for (const c of repo.components) {
    if (!touched.has(c.id)) states[c.id] = 'outside';
    else if (issues.has(c.id)) states[c.id] = verifyDone && accepted ? 'fixed' : 'issue';
    else if (active?.component === c.id) states[c.id] = 'checking';
    else if (!seen.has(c.id)) states[c.id] = 'waiting';
    else if (refuted.has(c.id)) states[c.id] = 'refuted';
    else states[c.id] = verdictIn ? 'clear' : 'scanned';
  }
  return states;
}

export const STATE_LABEL: Record<ComponentState, string> = {
  outside: 'Not touched by this change',
  waiting: 'Queued',
  checking: 'Being tested',
  scanned: 'Tested · waiting for proof',
  clear: 'Checked',
  refuted: 'False alarm',
  issue: 'Issue confirmed',
  fixed: 'Fixed & verified',
};

export interface Phase { label: string; agents: AgentId[] }
export const PHASES: Phase[] = [
  { label: 'Red team', agents: ['scout', 'probe'] },
  { label: 'Trace', agents: ['trace'] },
  { label: 'Blue team', agents: ['diagnose', 'patch', 'fortify'] },
  { label: 'Verify', agents: ['verify'] },
];
