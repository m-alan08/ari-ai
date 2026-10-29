'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';

const STEPS = [
  'Connect GitHub',
  'Choose repository',
  'Set up the repository',
  'Run the first review',
] as const;

type StepState = 'done' | 'current' | 'upcoming';

export function SetupSteps({
  current,
  org,
  doneSteps = [],
  onStepChange,
}: {
  current: number;
  org?: string;
  doneSteps?: number[];
  onStepChange?: (step: number) => void;
}) {
  const done = new Set(doneSteps);
  const label = STEPS[current - 1] ?? STEPS[0];
  const renderStep = (step: number) => {
    const state: StepState = step === current ? 'current' : done.has(step) ? 'done' : 'upcoming';
    const href = step === 1 ? '/connect' : step === 2 && org ? `/connect/${encodeURIComponent(org)}` : undefined;
    const className = `flex w-full items-center gap-3 border-l-2 px-3 py-2 text-left text-sm transition-colors duration-[120ms] ${
      state === 'current' ? 'border-brand bg-panel text-ink' : state === 'done' ? 'border-line-3 text-muted hover:text-ink' : 'border-transparent text-faint hover:text-muted'
    }`;
    const content = (
      <>
        <span aria-hidden="true" className="w-6 shrink-0 font-mono text-[10px] tabular-nums">{String(step).padStart(2, '0')}</span>
        <span>{STEPS[step - 1]}</span>
        {state === 'done' && <span className="ml-auto font-mono text-[10px] text-muted">DONE</span>}
      </>
    );
    return (
      <li key={step} data-step-state={state}>
        {href ? (
          <Link href={href} aria-current={state === 'current' ? 'step' : undefined} className={className}>{content}</Link>
        ) : step >= 3 && onStepChange ? (
          <button type="button" aria-current={state === 'current' ? 'step' : undefined} className={className} onClick={() => onStepChange(step)}>{content}</button>
        ) : (
          <span aria-current={state === 'current' ? 'step' : undefined} className={className}>{content}</span>
        )}
      </li>
    );
  };

  return (
    <nav aria-label="Setup steps" className="lg:sticky lg:top-24 lg:self-start">
      <ol className="hidden space-y-1 lg:block">{STEPS.map((_, i) => renderStep(i + 1))}</ol>
      <div className="lg:hidden">
        <p className="font-mono text-xs text-muted">Step {current} of {STEPS.length} · {label}</p>
        <div role="progressbar" aria-label={`Step ${current} of ${STEPS.length}`} aria-valuemin={1} aria-valuemax={STEPS.length} aria-valuenow={current} className="mt-2 h-px bg-panel-2">
          <span className="block h-px bg-brand" style={{ width: `${(current / STEPS.length) * 100}%` }} />
        </div>
      </div>
    </nav>
  );
}

export function SetupLayout({
  current,
  org,
  doneSteps,
  onStepChange,
  children,
}: {
  current: number;
  org?: string;
  doneSteps?: number[];
  onStepChange?: (step: number) => void;
  children: ReactNode;
}) {
  return (
    <div className="setup-layout mx-auto grid w-full max-w-[1600px] grid-cols-1 gap-6 px-4 py-5 sm:px-6 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-8 lg:px-10 lg:py-8">
      <SetupSteps current={current} org={org} doneSteps={doneSteps} onStepChange={onStepChange} />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
