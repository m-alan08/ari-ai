import { CONTRACT_SCHEMA_VERSION } from '../../../../shared/generated/ts/contracts';
import { deployPair, type DeploySide } from '@/lib/deploy-version';
import { backendOrigin } from '@/lib/backend-proxy';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** GET /api/frontend-version — deployed frontend/backend identity and contract pairing.
 *
 * Ops/deployment tooling calls this to verify a deploy pair is compatible instead
 * of assuming it is (the PR #516 failure mode: generated-AJV DTOs reject unknown
 * keys, so a newer backend against an older frontend breaks the whole UI).
 * Returns metadata only; never secrets.
 */
export async function GET() {
  const frontend: DeploySide = {
    gitSha: process.env.VERCEL_GIT_COMMIT_SHA ?? null,
    schemaVersion: CONTRACT_SCHEMA_VERSION,
  };

  let backend: { gitSha: string | null; schemaVersion: string | null } | null = null;
  let backendReachable = false;
  try {
    const r = await fetch(`${backendOrigin()}/readyz`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(8000),
    });
    if (r.ok) {
      const j = (await r.json()) as { git_sha?: string | null; schema_version?: string };
      backendReachable = true;
      backend = { gitSha: j.git_sha ?? null, schemaVersion: j.schema_version ?? null };
    }
  } catch {
    // Backend down or BACKEND_ORIGIN misconfigured — status "unknown" below.
  }

  return Response.json(deployPair(frontend, backend, backendReachable));
}
