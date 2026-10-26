'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import type { MeResponse } from '@hackatl/frontend-transport';
import { RollText } from '@/components/ui';
import { api } from '@/lib/api';
import { DEMO_USER } from '@/lib/demo/data';
import { DEMO_ONLY } from '@/lib/deployment';
import { notifySessionChanged, SESSION_CHANGED_EVENT, type SessionChangedEventDetail } from '@/lib/session-events';

const DEMO_HOME_PATH = '/demo/lumen-pay';

/** Header account entry; real sessions use an accessible menu, demo sessions remain links. */
export function AccountLink() {
  const pathname = usePathname();
  const [login, setLogin] = useState<string | null>(null);
  const [realAccount, setRealAccount] = useState(false);
  const [href, setHref] = useState('/projects');
  const [membership, setMembership] = useState<MeResponse['memberships'][number] | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [signOutFailed, setSignOutFailed] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const focusFirstOnOpen = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    const demoAccount = () =>
      fetch('/api/demo-session', { signal: controller.signal, cache: 'no-store' })
        .then(response => response.json() as Promise<{ signedIn?: boolean }>)
        .then(body => {
          if (controller.signal.aborted) return;
          setLogin(body.signedIn ? DEMO_USER.login : null);
          setRealAccount(false);
          setMembership(null);
          setHref(DEMO_HOME_PATH);
        });
    const backend = DEMO_ONLY ? Promise.reject(new Error('no backend')) : api().session({ signal: controller.signal });
    backend.then(
      me => {
        if (controller.signal.aborted) return;
        setLogin(me.user.github_login);
        setRealAccount(true);
        setMembership(me.memberships[0] ?? null);
        setHref('/projects');
      },
      () => {
        if (controller.signal.aborted) return;
        demoAccount().catch(() => {
          if (controller.signal.aborted) return;
          setLogin(null);
          setRealAccount(false);
          setMembership(null);
        });
      },
    );
    const onSessionChanged = (event: Event) => {
      controller.abort();
      const { me } = (event as CustomEvent<SessionChangedEventDetail>).detail;
      setLogin(me?.user.github_login ?? null);
      setRealAccount(me !== null);
      setMembership(me?.memberships[0] ?? null);
      setHref('/projects');
      setMenuOpen(false);
      setSignOutFailed(false);
    };
    window.addEventListener(SESSION_CHANGED_EVENT, onSessionChanged);
    return () => {
      window.removeEventListener(SESSION_CHANGED_EVENT, onSessionChanged);
      controller.abort();
    };
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    if (focusFirstOnOpen.current) {
      focusFirstOnOpen.current = false;
      menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setMenuOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setMenuOpen(false);
        buttonRef.current?.focus();
        return;
      }
      if (event.key !== 'Tab' || !menuRef.current?.contains(event.target as Node)) return;
      const items = [...menuRef.current.querySelectorAll<HTMLElement>('[role="menuitem"]')];
      const focusable = [...document.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), [tabindex]:not([tabindex="-1"])')]
        .filter(element => !element.closest('[hidden]') && element.getAttribute('aria-hidden') !== 'true'
          && getComputedStyle(element).display !== 'none' && getComputedStyle(element).visibility !== 'hidden');
      const edge = event.shiftKey ? items[0] : items.at(-1);
      const edgeIndex = edge ? focusable.indexOf(edge) : -1;
      const next = event.shiftKey ? focusable[edgeIndex - 1] : focusable[edgeIndex + 1];
      event.preventDefault();
      setMenuOpen(false);
      (next ?? buttonRef.current)?.focus();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [menuOpen]);

  const signOut = async () => {
    setBusy(true);
    setSignOutFailed(false);
    try {
      await api().logout();
      notifySessionChanged(null);
      setLogin(null);
      setRealAccount(false);
      setMembership(null);
      setMenuOpen(false);
    } catch {
      setSignOutFailed(true);
      setMenuOpen(true);
    } finally {
      setBusy(false);
    }
  };

  if (login && realAccount && !DEMO_ONLY) {
    return (
      <div className="relative">
        <button
          ref={buttonRef}
          type="button"
          className="group hdr-account label-mono inline-flex h-8 items-center gap-2 rounded-lg px-2 sm:px-3"
          title={`Signed in as ${login}`}
          aria-label={`Signed in as ${login}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          data-testid="header-account"
          onClick={event => {
            if (menuOpen) setMenuOpen(false);
            else { focusFirstOnOpen.current = event.detail === 0; setMenuOpen(true); }
          }}
          onKeyDown={event => {
            if (event.key === 'ArrowDown' || (!menuOpen && (event.key === 'Enter' || event.key === ' '))) {
              event.preventDefault();
              focusFirstOnOpen.current = true;
              setMenuOpen(true);
            }
          }}
        >
          <span aria-hidden="true" className="hdr-avatar">{login.slice(0, 1).toUpperCase()}</span>
          <span className="sr-only max-w-[8rem] truncate normal-case sm:not-sr-only sm:inline-block" data-testid="signed-in-user">{login}</span>
        </button>
        {menuOpen && (
          <div ref={menuRef} role="menu" aria-label="Account" className="account-menu absolute right-0 top-full z-50 mt-2 min-w-56 rounded-xl border border-line-2 bg-panel p-2 shadow-lg">
            <div role="none" className="border-b border-line px-3 py-2 text-xs text-muted">
              <p>Signed in as <span className="text-ink">{login}</span></p>
              {membership && <p className="mt-1">{membership.organization.name} · {membership.role}</p>}
            </div>
            <Link role="menuitem" tabIndex={-1} href="/projects" className="account-menu-item" onClick={() => setMenuOpen(false)}>Projects</Link>
            <button role="menuitem" type="button" className="account-menu-item" disabled={busy} onClick={() => void signOut()}>{busy ? 'Signing out…' : 'Sign out'}</button>
            {signOutFailed && <p role="alert" className="px-3 py-2 text-xs text-bad">Sign out failed; you are still signed in</p>}
          </div>
        )}
      </div>
    );
  }
  return (
    <Link
      href={login ? href : DEMO_ONLY ? '/access' : '/projects'}
      className={`group ${login ? 'hdr-account' : 'hdr-signin'} label-mono inline-flex h-8 items-center rounded-md px-2 sm:px-3`}
      data-testid={login ? 'header-account' : 'header-signin'}
      title={login ? `Signed in as ${login}` : undefined}
      aria-label={login ? `Signed in as ${login}` : undefined}
    >
      {login
        ? <><span aria-hidden="true" className="hdr-avatar">{login.slice(0, 1).toUpperCase()}</span><span className="sr-only max-w-[8rem] truncate normal-case sm:not-sr-only sm:inline-block">{login}</span></>
        : <RollText>Sign in</RollText>}
    </Link>
  );
}
