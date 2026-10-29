'use client';

import Link from 'next/link';
import { cloneElement, isValidElement, useId, type ButtonHTMLAttributes, type ReactElement, type ReactNode } from 'react';
import { ContractSkewHint } from '@/components/contract-skew';
import { describeError } from '@/lib/api';
import type { Resource } from '@/lib/use-resource';
import type { StatusView, Tone } from '@/lib/status';
import { formatTime, relativeTime } from '@/lib/format';
import { useRevealOnScroll } from '@/components/reveal';

const TONE: Record<Tone, string> = {
  ok: 'border-ok/40 bg-ok/10 text-ok',
  warn: 'border-warn/40 bg-warn/10 text-warn',
  bad: 'border-bad/40 bg-bad/10 text-bad',
  info: 'border-info/40 bg-info/10 text-info',
  accent: 'border-accent/40 bg-accent/10 text-accent',
  neutral: 'border-line bg-panel-2 text-muted',
};

export function Badge({ view, title }: { view: StatusView; title?: string }) {
  const mark = view.glyph === '⏸' ? 'paused' : view.tone === 'bad' ? 'failed' : view.tone === 'ok' ? 'done' : 'pending';
  return (
    <span title={title} className={`status-badge inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${TONE[view.tone]}`}>
      <span aria-hidden="true" className={`status-mark status-mark-${mark}`}>{mark === 'failed' ? '×' : null}</span>
      {view.label}
    </span>
  );
}

export function Tag({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${TONE[tone]}`}>{children}</span>;
}

export function Panel({ title, actions, children, className = '', id }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; id?: string }) {
  const ref = useRevealOnScroll<HTMLElement>();
  return (
    <section ref={ref} id={id} className={`panel min-w-0 rounded-lg border border-line bg-panel ${className}`} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-panel-2/60 px-4 py-2.5">
          {title && <h2 className="label-mono flex items-center gap-2 text-ink"><span aria-hidden="true" className="h-3 w-0.5 rounded-full bg-brand" />{title}</h2>}
          {actions && <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-5 sm:p-6">{children}</div>
    </section>
  );
}

export function Button({ variant = 'secondary', className = '', children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' }) {
  const styles = {
    primary: 'bg-brand text-on-brand hover:bg-brand-deep shadow-[0_6px_16px_-8px_rgba(194,69,45,0.6)] hover:shadow-[0_8px_20px_-8px_rgba(194,69,45,0.5)]',
    secondary: 'bg-brand/[0.07] text-ink hover:bg-brand/[0.14] shadow-sm hover:shadow-md',
    danger: 'border border-bad/50 bg-bad/10 text-bad hover:bg-bad/20 shadow-sm hover:shadow-md',
    ghost: 'text-muted hover:bg-brand/[0.07] hover:text-ink hover:shadow-sm',
  }[variant];
  return (
    <button
      type="button"
      data-variant={variant}
      {...props}
      className={`btn group label-mono inline-flex h-8 items-center justify-center gap-1.5 rounded-md px-3 transition-[background-color,color,box-shadow,transform] duration-[250ms] ease-[var(--ease-smooth)] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0 disabled:active:scale-100 disabled:shadow-none ${styles} ${className}`}
    >
      {typeof children === 'string' ? <RollText>{children}</RollText> : children}
    </button>
  );
}

/** Label that rolls up to a second copy on hover (the copy is hidden from assistive tech). */
export function RollText({ children }: { children: string }) {
  return (
    <span className="roll">
      <span>{children}</span>
      <span aria-hidden="true">{children}</span>
    </span>
  );
}

export function ErrorState({ error, onRetry, context }: { error: unknown; onRetry?: () => void; context?: string }) {
  const e = describeError(error);
  return (
    <div role="alert" data-testid="error-state" className="rounded-md border border-bad/40 bg-bad/5 p-3 text-sm shadow-sm hover:shadow-md transition-shadow duration-300">
      <p className="font-semibold text-bad"><span aria-hidden="true">× </span>{context ? `${context}: ` : ''}{e.title}</p>
      <p className="mt-1 text-muted">{e.detail}</p>
      {e.recovery && <p className="mt-1 text-ink" data-testid="error-recovery"><span className="font-semibold">What to do: </span>{e.recovery}</p>}
      {(e.code || e.requestId) && (
        <p className="mt-1 font-mono text-xs text-faint">
          {e.code && <>code {e.code}</>}{e.requestId && <> · request {e.requestId}</>}
        </p>
      )}
      {e.kind === 'protocol' && <ContractSkewHint />}
      {(onRetry || e.status === 403 || e.status === 404) && (
        <div className="mt-2 flex flex-wrap items-center gap-3">
          {onRetry && <Button onClick={onRetry}>Retry</Button>}
          {(e.status === 403 || e.status === 404) && <Link className="text-sm font-semibold text-brand hover:underline" href="/connect">Back to projects</Link>}
        </div>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty-state rounded-lg border border-dashed border-control/60 bg-panel-2/40 px-4 py-8 text-center text-sm text-muted">{children}</p>;
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return <p role="status" aria-live="polite" className="text-sm text-muted"><span aria-hidden="true" className="loading-dot" />{label}…</p>;
}

/** Render loading / error / ready for a resource with no silent fallbacks. */
export function Async<T>({ resource, label, children }: { resource: Resource<T>; label: string; children: (data: T) => ReactNode }) {
  if (resource.status === 'loading') return <Loading label={`Loading ${label}`} />;
  if (resource.status === 'error') return <ErrorState error={resource.error} onRetry={resource.reload} context={`Could not load ${label}`} />;
  return <>{resource.refreshError !== undefined && <StaleNotice what={label} onRetry={resource.reload} />}{children(resource.data)}</>;
}

/** Shown whenever a background refresh failed and the page is still showing the last good copy. */
export function StaleNotice({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <p role="status" className="mb-3 flex flex-wrap items-center gap-2 text-xs text-warn" data-testid="stale-notice">
      Could not refresh {what}. Showing the last loaded copy, which may be out of date.
      <button type="button" className="font-semibold underline-offset-2 hover:underline" onClick={onRetry}>Retry</button>
    </p>
  );
}

export function Mono({ children, className = '' }: { children: ReactNode; className?: string }) {
  // Long IDs and step keys may break anywhere so they never force sideways scrolling.
  return <code className={`font-mono text-[0.8rem] text-muted [overflow-wrap:anywhere] ${className}`}>{children}</code>;
}

export function Time({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span className="text-faint">—</span>;
  return <time dateTime={iso} title={formatTime(iso)} className="whitespace-nowrap">{relativeTime(iso)}</time>;
}

/** Label and hint are tied to the control by id, so its accessible name is only the label. */
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactElement<{ id?: string; 'aria-describedby'?: string }> }) {
  const id = useId();
  const control = isValidElement(children) ? cloneElement(children, { id, ...(hint ? { 'aria-describedby': `${id}-hint` } : {}) }) : children;
  return (
    <div className="flex flex-col gap-1 text-sm">
      <label htmlFor={id} className="text-muted">{label}</label>
      {control}
      {hint && <span id={`${id}-hint`} className="text-xs text-faint">{hint}</span>}
    </div>
  );
}

export const inputClass = 'max-w-full rounded-md border border-control bg-bg px-2.5 py-1.5 text-sm text-ink placeholder:text-faint transition-[border-color,box-shadow] duration-200 focus:border-brand focus:shadow-[0_0_0_3px_rgba(194,69,45,0.15)] focus:outline-none';

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-line border-t-2 border-t-brand/70 bg-panel px-4 py-3.5">
      <p className="label-mono text-faint">{label}</p>
      <p className="mt-2 font-display text-2xl font-semibold tabular-nums tracking-tight text-ink">{value}</p>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function Crumbs({ items }: { items: Array<{ href?: string; label: string }> }) {
  return (
    <nav aria-label="Breadcrumb" className="label-mono mb-3 text-muted">
      <ol className="flex flex-wrap items-center gap-1">
        {items.map((it, i) => (
          <li key={i} className="flex items-center gap-1">
            {i > 0 && <span aria-hidden="true" className="text-faint">/</span>}
            {it.href ? <Link className="transition-colors hover:text-brand" href={it.href}>{it.label}</Link> : <span className="text-ink">{it.label}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function PageTitle({ title, subtitle, actions, eyebrow }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="page-title mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-line pb-5">
      <div className="min-w-0">
        {eyebrow && <p className="label-mono mb-2 text-brand">{eyebrow}</p>}
        <h1 className="text-3xl font-semibold tracking-[-0.03em] text-ink md:text-4xl">{title}</h1>
        {subtitle && <div className="mt-2 max-w-2xl text-sm text-muted">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
