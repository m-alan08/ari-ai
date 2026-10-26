'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { demoSha, VERDICT, type CheckState, type Verdict } from '@/lib/demo/data';
import type { DiffFile, ReviewComment } from '@/lib/demo/scenarios';

/** Always-visible label: everything under /demo is illustrative (ADR 0013). */
export function DemoMarker({ className = '' }: { className?: string }) {
  return (
    <span className={`demo-marker ${className}`} data-testid="demo-marker" title="Illustrative demo: illustrative accounts, repositories, commits and agent runs. No repository is accessed and no model is called.">
      <span aria-hidden="true" className="demo-marker-dot" />Illustrative demo
    </span>
  );
}

const AVATAR_TONES = ['#c2452d', '#8f2f1e', '#2b1d19', '#6b5752', '#a35a00', '#1d5fa8'];

export function Avatar({ login, size = 28 }: { login: string; size?: number }) {
  const tone = AVATAR_TONES[parseInt(demoSha(login).slice(0, 2), 16) % AVATAR_TONES.length];
  const initials = login.split(/[-_]/).map(part => part[0]?.toUpperCase() ?? '').join('').slice(0, 2);
  return (
    <span aria-hidden="true" className="inline-flex shrink-0 items-center justify-center rounded-full font-mono font-semibold text-white" style={{ width: size, height: size, background: tone, fontSize: size * 0.38 }}>
      {initials}
    </span>
  );
}

export function GitHubMark({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className={className} fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export function CiBadge({ state }: { state: CheckState }) {
  const view = { passing: { glyph: '✓', label: 'CI passing', cls: 'text-ok' }, failing: { glyph: '✕', label: 'CI failing', cls: 'text-bad' }, pending: { glyph: '●', label: 'CI running', cls: 'text-warn' } }[state];
  return <span className={`inline-flex items-center gap-1 text-xs ${view.cls}`} title={view.label}><span aria-hidden="true">{view.glyph}</span><span className="sr-only sm:not-sr-only">{view.label}</span></span>;
}

export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const view = VERDICT[verdict];
  return <span className={`demo-chip demo-chip-${view.tone}`} data-testid="demo-verdict">{view.label}</span>;
}

/** Breadcrumb bar shared by the signed-in demo pages. */
export function DemoBar({ crumbs, user, onSignOut }: { crumbs: { href?: string; label: ReactNode }[]; user: { login: string; name: string }; onSignOut: () => void }) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <nav aria-label="Breadcrumb" className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm">
        {crumbs.map((crumb, i) => (
          <span key={i} className="flex min-w-0 items-center gap-1.5">
            {i > 0 && <span aria-hidden="true" className="text-faint">/</span>}
            {crumb.href ? <Link href={crumb.href} className="truncate text-muted transition-colors hover:text-brand">{crumb.label}</Link> : <span className="truncate font-semibold">{crumb.label}</span>}
          </span>
        ))}
      </nav>
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-2 text-sm"><Avatar login={user.login} size={26} /><span className="hidden sm:inline">{user.login}</span></span>
        <button type="button" onClick={onSignOut} className="text-xs text-muted underline-offset-2 hover:text-brand hover:underline">Sign out</button>
      </div>
    </div>
  );
}

/** Diff with review comments anchored under the file they belong to. */
export function DemoDiff({ file, comments = [], label }: { file: DiffFile; comments?: ReviewComment[]; label?: string }) {
  const mine = comments.filter(c => c.path === file.path);
  return (
    <figure className="demo-diff" data-testid="demo-diff">
      <figcaption className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-4 py-2.5">
        <span className="flex min-w-0 items-center gap-2 font-mono text-xs text-white/85">
          {label && <span className="demo-chip demo-chip-info">{label}</span>}<span className="truncate">{file.path}</span>
        </span>
        <span className="font-mono text-xs"><span className="text-[#6fd39b]">+{file.additions}</span> <span className="text-[#ff8a75]">−{file.deletions}</span></span>
      </figcaption>
      <div className="overflow-x-auto">
        {file.hunks.map((hunk, h) => (
          <div key={h}>
            <p className="px-4 py-1.5 font-mono text-[0.72rem] text-white/45">{hunk.header}</p>
            <table className="w-full font-mono text-[0.76rem] leading-relaxed">
              <tbody>
                {hunk.lines.map((line, i) => (
                  <tr key={i} className={line.kind === '+' ? 'demo-add' : line.kind === '-' ? 'demo-del' : ''}>
                    <td aria-hidden="true" className="w-8 select-none pl-4 text-white/35">{line.kind === ' ' ? '' : line.kind === '+' ? '+' : '−'}</td>
                    <td className="whitespace-pre pr-4 text-white/85"><span className="sr-only">{line.kind === '+' ? 'added: ' : line.kind === '-' ? 'removed: ' : ''}</span>{line.text || ' '}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
      {mine.length > 0 && (
        <ul className="flex flex-col gap-2 border-t border-white/10 p-3">
          {mine.map((c, i) => (
            <li key={i} className="demo-comment">
              <p className="flex flex-wrap items-center gap-2 text-xs">
                <span className={`demo-chip ${c.severity === 'blocker' ? 'demo-chip-bad' : c.severity === 'major' ? 'demo-chip-warn' : 'demo-chip-info'}`}>{c.severity}</span>
                <span className="font-mono text-white/50">{c.line}</span>
                <span className="font-semibold text-white">{c.title}</span>
              </p>
              <p className="mt-1.5 text-sm text-white/75">{c.body}</p>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}
