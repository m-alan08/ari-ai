'use client';

import { ApiError, type MeResponse } from '@hackatl/frontend-transport';
import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { notifySessionChanged } from '@/lib/session-events';
import { AuthStage, LockIcon } from '@/components/auth-stage';
import { BRAND_NAME } from '@/components/brand';
import { GitHubMark } from '@/components/demo/ui';
import { INTRO_KEY } from '@/components/intro-loader';
import { ErrorState, Loading } from '@/components/ui';

type SessionState =
  | { status: 'loading' }
  | { status: 'signed-out' }
  | { status: 'signed-in'; me: MeResponse }
  | { status: 'error'; error: unknown };

interface SessionValue { me: MeResponse; refresh: () => void; signOut: () => Promise<void> }
const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession outside SessionGate');
  return value;
}

/** The session if one is known here, else null. For public pages that only decorate a signed-in view. */
export function useOptionalSession(): SessionValue | null {
  return useContext(SessionContext);
}

/**
 * Public pages: render children immediately for everyone. In the background, look
 * for an existing session and, only if there is one, provide it (so a "signed in as"
 * bar can appear). Signed-out visitors, or a backend that is down, see the page as is.
 */
export function OptionalSession({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<MeResponse | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api().session({ signal: controller.signal }).then(setMe, () => { /* public page: no session is fine */ });
    return () => controller.abort();
  }, []);
  const refresh = useCallback(() => { api().session().then(setMe, () => setMe(null)); }, []);
  const signOut = useCallback(async () => {
    await api().logout();
    notifySessionChanged(null);
    try { window.sessionStorage.removeItem(INTRO_KEY); } catch { /* storage unavailable */ }
    setMe(null);
  }, []);
  if (!me) return <>{children}</>;
  return <SessionContext.Provider value={{ me, refresh, signOut }}>{children}</SessionContext.Provider>;
}

const devLoginVisible = process.env.NEXT_PUBLIC_DEV_LOGIN === '1';

/** Children render only for a backend-confirmed session; offline is an explicit error. */
export function SessionGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: 'loading' });
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce(n => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    // The API restarts briefly on every deploy: retry a few times before showing an error.
    const attempt = (left: number) => api().session({ signal: controller.signal }).then(
      me => setState({ status: 'signed-in', me }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) return setState({ status: 'signed-out' });
        if (left > 0 && (!(error instanceof ApiError) || error.status >= 500)) {
          timer = setTimeout(() => void attempt(left - 1), 2000);
          return;
        }
        setState({ status: 'error', error });
      },
    );
    void attempt(4);
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [nonce]);

  const signOut = useCallback(async () => {
    await api().logout();
    notifySessionChanged(null);
    try { window.sessionStorage.removeItem(INTRO_KEY); } catch { /* storage unavailable */ }
    setState({ status: 'signed-out' });
  }, []);

  if (state.status === 'loading') return <div className="mx-auto max-w-md p-10"><Loading label="Checking session" /></div>;
  if (state.status === 'error') {
    return (
      <div className="mx-auto max-w-lg p-10">
        <ErrorState error={state.error} onRetry={refresh} context="Session check failed" />
      </div>
    );
  }
  if (state.status === 'signed-out') return <SignIn onSignedIn={me => {
    setState({ status: 'signed-in', me });
    notifySessionChanged(me);
  }} />;
  return <SessionContext.Provider value={{ me: state.me, refresh, signOut }}>{children}</SessionContext.Provider>;
}

function SignIn({ onSignedIn }: { onSignedIn: (me: MeResponse) => void }) {
  const [login, setLogin] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { onSignedIn(await api().devLogin({ github_login: login.trim(), display_name: null })); }
    catch (err) { setError(err); }
    finally { setBusy(false); }
  };
  return (
    <AuthStage
      title="Sign in to your workspace"
      subtitle="Connect GitHub to pick an organization, a repository and the commits to assess."
      testId="signin"
      footnote={<><LockIcon /><span>Read-only GitHub access. Sessions are issued by the {BRAND_NAME} control plane; no token is kept in your browser.</span></>}
    >
      {/* OAuth must be a full browser navigation to the backend (proxied), not client routing. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/api/v1/auth/github/login" className="auth-btn auth-btn-light bg-white">
        <GitHubMark className="h-[18px] w-[18px]" /><span>Continue with GitHub</span>
      </a>
      <div className="auth-divider">or</div>
      <Link href="/access" className="auth-btn auth-btn-ghost" data-testid="access-link">Use an access code</Link>
      {devLoginVisible && (
        <form onSubmit={submit} className="mt-6 border-t border-white/10 pt-5" aria-label="Development login">
          <p className="label-mono mb-3 text-[11px] text-[#ffb8a6]">Development login · local backend only</p>
          <label htmlFor="dev-login" className="auth-label">GitHub login</label>
          <input id="dev-login" className="auth-input" value={login} onChange={e => setLogin(e.target.value)} required pattern="(?:[A-Za-z0-9]|-){1,39}" autoComplete="username" name="github_login" />
          <button type="submit" className="auth-btn auth-btn-ghost mt-3" disabled={busy || !login.trim()}>Development sign-in</button>
          {error !== null && <div className="mt-3"><ErrorState error={error} context="Sign-in failed" /></div>}
        </form>
      )}
    </AuthStage>
  );
}
