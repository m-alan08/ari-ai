import { ApiClient, ApiError, ProtocolError } from '@hackatl/frontend-transport';

let client: ApiClient | undefined;

/** One browser client per tab: the CSRF token lives only in its memory. */
export function api(): ApiClient {
  if (typeof window === 'undefined') throw new Error('The API client is browser-only');
  client ??= new ApiClient({ baseUrl: `${window.location.origin}/api/v1` });
  return client;
}

/** Test seam: replace the singleton (unit tests inject a fetch double). */
export function setApiClient(next: ApiClient | undefined): void {
  client = next;
}

export interface DescribedError {
  kind: 'api' | 'protocol' | 'network' | 'aborted' | 'unknown';
  title: string;
  detail: string;
  code?: string;
  status?: number;
  requestId?: string | null;
  /** What the person can do next; absent when retrying is the only honest advice. */
  recovery?: string;
}

const RECOVERY_BY_CODE: Record<string, string> = {
  UNAUTHENTICATED: 'Your session ended. Sign in again, then retry.',
  CSRF_FAILED: 'Your session ended. Sign in again, then retry.',
  RATE_LIMITED: 'Too many requests in a short time. Wait a minute, then retry.',
  SUBSYSTEM_UNAVAILABLE: 'A service Ari depends on (such as GitHub) did not respond, so nothing was started. Retry in a minute. If it keeps failing, check that the Ari GitHub App is still installed on this repository.',
  SNAPSHOT_NOT_READY: 'Ari is still indexing this commit. Retry in a minute.',
  SCOPE_DENIED: 'Sandbox testing is not approved for this repository. Approve a sandbox scope in setup or Scopes, then retry.',
  APPROVAL_REQUIRED: 'This step waits for a human decision. Open Approvals to see what is pending.',
  CONFLICT_STATE: 'This changed since the page loaded. Retry to load the latest state.',
  IDEMPOTENCY_CONFLICT: 'This changed since the page loaded. Retry to load the latest state.',
};

function recoveryFor(status: number, code: string | undefined): string | undefined {
  if (code && RECOVERY_BY_CODE[code]) return RECOVERY_BY_CODE[code];
  if (status === 401) return RECOVERY_BY_CODE.UNAUTHENTICATED;
  if (status === 403) return 'Your account is not allowed to do this here. Ask a project owner for access. For a GitHub repository, also check that the Ari GitHub App can still reach it.';
  if (status === 404) return 'It may have been removed, or your account may not have access. Go back to your projects and choose it again.';
  if (status === 429) return RECOVERY_BY_CODE.RATE_LIMITED;
  if (status >= 500) return 'Retry in a minute. If it keeps failing, share the request ID below with your Ari contact.';
  return undefined;
}

/** Turn any thrown value into honest, user-facing text. Never echoes payloads. */
export function describeError(error: unknown): DescribedError {
  if (error instanceof ApiError) {
    return {
      kind: 'api',
      title: error.status === 401 ? 'Not signed in' : error.status === 403 ? 'Not permitted' : error.status === 404 ? 'Not found' : `Request failed (${error.status})`,
      detail: error.error.message,
      code: error.error.code,
      status: error.status,
      requestId: error.error.request_id,
      recovery: recoveryFor(error.status, error.error.code),
    };
  }
  if (error instanceof ProtocolError) {
    return { kind: 'protocol', title: 'Backend response rejected', detail: `${error.message}. The response did not match the shared contract, so it was not rendered.` };
  }
  if (error instanceof Error && error.name === 'AbortError') {
    return { kind: 'aborted', title: 'Request cancelled', detail: 'The request was cancelled before it finished.' };
  }
  if (error instanceof TypeError) {
    return { kind: 'network', title: 'Backend unreachable', detail: 'The control-plane API did not respond. Nothing shown here is cached or simulated.', recovery: 'Check your connection, then retry.' };
  }
  return { kind: 'unknown', title: 'Unexpected error', detail: error instanceof Error ? error.message : 'Unknown failure' };
}

/** A fresh idempotency key per user action; retries of the same action reuse it. */
export function idempotencyKey(action: string): string {
  return `${action}-${crypto.randomUUID()}`;
}
