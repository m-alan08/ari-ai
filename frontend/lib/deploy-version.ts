/** Frontend ↔ backend deploy pairing, reported by GET /api/frontend-version.
 *
 * The frontend only understands the contract schema it was built against
 * (`CONTRACT_SCHEMA_VERSION` from shared/generated — additive bumps are safe,
 * so equality, not ordering, is the gate). Pairing status:
 *
 *   "compatible" — schema versions equal; the frontend can decode every DTO
 *                  the backend emits. Commit equality is reported but NOT
 *                  required: an additive schema bump ships backend-first by
 *                  design, and unrelated commits must not page anyone.
 *   "mismatch"   — schema versions differ: one side shipped contracts without
 *                  the other (the PR #516 class of incident). Visible to ops.
 *   "unknown"    — backend unreachable or older than this contract: its
 *                  /readyz has no schema_version field to compare.
 */
export interface DeploySide {
  gitSha: string | null;
  schemaVersion: string | null;
}

export type PairStatus = 'compatible' | 'mismatch' | 'unknown';

export interface DeployPair {
  frontend: DeploySide;
  backend: DeploySide | null;
  backendReachable: boolean;
  status: PairStatus;
  sameCommit: boolean | null;
}

export function deployPair(
  frontend: DeploySide,
  backend: { gitSha: string | null; schemaVersion: string | null } | null,
  backendReachable: boolean,
): DeployPair {
  if (!backendReachable) {
    return { frontend, backend: null, backendReachable, status: 'unknown', sameCommit: null };
  }
  if (backend == null) {
    // Reachable but no version fields → backend predates deploy identity.
    return {
      frontend,
      backend: { gitSha: null, schemaVersion: null },
      backendReachable,
      status: 'unknown',
      sameCommit: null,
    };
  }
  const status: PairStatus =
    backend.schemaVersion !== null && backend.schemaVersion === frontend.schemaVersion
      ? 'compatible'
      : backend.schemaVersion === null
        ? 'unknown'
        : 'mismatch';
  const sameCommit =
    backend.gitSha !== null && frontend.gitSha !== null ? backend.gitSha === frontend.gitSha : null;
  return { frontend, backend, backendReachable, status, sameCommit };
}
