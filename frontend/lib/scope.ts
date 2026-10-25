import type { CheckCategory } from '@hackatl/frontend-transport';

/** Canonical Probe library ids offered when requesting a scope (SEC-005). */
export const SCOPE_CHECK_CATEGORIES: readonly CheckCategory[] = [
  'access_control.ownership',
  'access_control.authentication',
  'input_validation.bounds',
];

/** What Request scope records: a pending authorization, not an active test window. */
export const REQUEST_SCOPE_NOTE =
  'Requesting a scope records a pending authorization for the listed sandbox build targets and check categories. It does not become active until a project owner explicitly approves it (owners may approve their own sandbox-only scopes), and it does not start a check or grant access outside those targets.';

export const SCOPE_SANDBOX_EXPLANATION =
  'Ari uses the build recipe to run checks against an isolated copy of your app; nothing touches your live site.';
