import type { Metadata } from 'next';
import Link from 'next/link';
import { BRAND_NAME } from '@/components/brand';
import { Faq } from '@/components/faq';
import { Reveal } from '@/components/reveal';
import { RollText } from '@/components/ui';
import { DEMO_ONLY } from '@/lib/deployment';
import { perAssessment, PLANS, usd } from '@/lib/pricing';

const START = DEMO_ONLY ? '/access' : '/projects';

export const metadata: Metadata = {
  title: `Pricing · ${BRAND_NAME}`,
  description: `${BRAND_NAME} plans: Founder, Growth, Scale and Enterprise, priced by full assessments and assessed source lines per month.`,
};

/* Preview pricing: the layout follows kairo.security's pricing page; plan contents describe
   what ARI does. Prices are proposals; nothing can be purchased. */
const INCLUDED = [
  'Intent-aware review of every pull request against what the code is meant to do',
  'A failing test generated to reproduce each functional defect it reports',
  'Seven security agents probing only inside the sandbox scope you approve',
  'Fixes proposed only after human approval, and closed only when independent tests pass',
  'Signed GitHub monitoring that re-checks every push for regressions',
  'Evidence for every claim: source spans, test reports and agent transcripts',
];

const ENTERPRISE_ADDS = [
  'Custom assessment and source-line volumes',
  'SARIF and rules-of-engagement reports for every assessment',
  'Findings mapped to CWE, CVSS, OWASP LLM Top 10, MITRE ATT&CK and D3FEND',
  'Scope and rules of engagement agreed in writing before any testing',
  'Two-person approval for containment and incident closure workflows',
  'A persisted audit trail of every approval and decision',
];

const FAQ = [
  { q: 'What is a full assessment?', a: 'One run on one commit: intent review, the seven security agents inside your approved scope, and independent verification of any fix.' },
  { q: 'What counts as assessed source lines?', a: 'In this proposal, the source lines ARI reads across all full assessments in a month. Assessing a 50,000-line repository three times counts as 150,000 lines.' },
  { q: 'Does ARI ever change our code on its own?', a: 'No. Patches are proposed only after a person approves remediation, and a fix only counts when independent, executed tests agree.' },
  { q: 'What will ARI test?', a: 'Only the scope you approve, such as sandbox builds of your own repositories. It never targets third-party systems.' },
  { q: 'Is the Defense Lab live network defense?', a: 'No. It is a policy trained and evaluated in an isolated test network, shown as decision support. Nothing it recommends is applied automatically.' },
  { q: 'Can I buy a plan today?', a: 'Not yet. This is a hackathon preview: the plans above are proposals and nothing can be purchased. You can open the console and try the product.' },
];

function Check({ dark }: { dark?: boolean }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className={`mt-0.5 h-4 w-4 shrink-0 ${dark ? 'text-white/70' : 'text-brand'}`}>
      <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Cta({ href, children, tone }: { href: string; children: string; tone: 'light' | 'dark' }) {
  return (
    <Link href={href} className={`group label-mono inline-flex h-10 items-center rounded-md px-4 transition-colors duration-[250ms] ${tone === 'light' ? 'bg-white text-night hover:bg-brand hover:text-on-brand' : 'bg-white/10 text-white shadow-[inset_0_0_0_1px_rgb(255_247_242_/_14%)] hover:bg-brand'}`}>
      <RollText>{children}</RollText>
    </Link>
  );
}

export default function PricingPage() {
  return (
    <div className="pricing marketing-atmosphere">
      <header className="hero-ink bleed-hero -mt-6 px-6 pb-20 pt-16 md:px-12 md:pb-28 md:pt-24" aria-labelledby="pricing-title">
        <div className="mx-auto max-w-7xl">
          <p className="pricing-pill label-mono">Pricing</p>
          <h1 id="pricing-title" className="display mt-5 text-5xl font-semibold md:text-7xl">{BRAND_NAME} pricing</h1>
          <p className="mt-4 max-w-xl text-sm text-white/70 md:text-base">Know what every change does, prove every fix, and keep checking as your code moves.</p>
          <p className="mt-3 text-xs text-white/60" data-testid="pricing-proposal">Hackathon preview: plans are proposals, nothing can be purchased.</p>
          <div className="mt-7"><Cta href={START} tone="light">Open the console</Cta></div>
        </div>
      </header>

      <section aria-label="Plans" className="pricing-plans -mt-12 grid gap-4 sm:grid-cols-2 md:-mt-16 xl:grid-cols-4">
        {PLANS.map(plan => {
          const dark = plan.recommended;
          return (
            <article key={plan.id} className={`pricing-card flex flex-col ${dark ? 'pricing-card-dark' : 'pricing-card-light'}`} aria-labelledby={`plan-${plan.id}`} data-testid={`plan-${plan.id}`}>
              <div className="flex items-start justify-between gap-3">
                <h2 id={`plan-${plan.id}`} className="text-2xl font-semibold">{plan.name}</h2>
                {plan.recommended && <span className="pricing-badge label-mono">Recommended</span>}
              </div>
              <p className={`mt-2 text-sm ${dark ? 'text-white/70' : 'text-muted'}`}>{plan.tagline}</p>
              <p className="mt-8 flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="font-display text-5xl font-semibold tracking-tight">{plan.monthlyUsd === null ? 'Contact us' : usd(plan.monthlyUsd)}</span>
                {plan.monthlyUsd !== null && <span className={`label-mono ${dark ? 'text-white/60' : 'text-faint'}`}>/ month · proposed</span>}
              </p>
              <dl className={`pricing-quota mt-6 ${dark ? 'pricing-quota-dark' : ''}`}>
                <div><dt>Full assessments / month</dt><dd>{plan.assessments ?? 'Custom'}</dd></div>
                <div><dt>Assessed source lines / month</dt><dd>{plan.sourceLines}</dd></div>
                <div><dt>Per included assessment</dt><dd>{perAssessment(plan)}</dd></div>
              </dl>
              <div className="mt-auto pt-7">
                {plan.monthlyUsd === null
                  ? <Cta href="/#how" tone="dark">See how it works</Cta>
                  : <Cta href={START} tone={dark ? 'light' : 'dark'}>Open the console</Cta>}
              </div>
            </article>
          );
        })}
      </section>

      <section aria-label="What every plan includes" className="mt-6 grid gap-4 lg:grid-cols-2">
        <div className="pricing-card pricing-card-light">
          <p className="label-mono text-faint">Every plan includes</p>
          <ul className="mt-4 flex flex-col gap-3 text-sm text-ink">
            {INCLUDED.map(f => <li key={f} className="flex gap-3"><Check />{f}</li>)}
          </ul>
        </div>
        <div className="pricing-card pricing-card-light">
          <p className="label-mono text-faint">Enterprise adds</p>
          <ul className="mt-4 flex flex-col gap-3 text-sm text-ink">
            {ENTERPRISE_ADDS.map(f => <li key={f} className="flex gap-3"><Check />{f}</li>)}
          </ul>
        </div>
      </section>

      <p className="mt-4 text-center text-xs text-faint" data-testid="pricing-preview">Hackathon preview: these plans are proposals and nothing can be purchased yet. Prices in USD.</p>

      <Reveal>
        <section aria-labelledby="faq-title" className="pricing-faq mt-24 grid items-start gap-10 py-16 md:grid-cols-12 md:gap-6 md:py-24">
          <div className="faq-aside md:col-span-4">
            <p className="faq-pill label-mono">FAQ</p>
            <h2 id="faq-title" className="display mt-5 text-4xl font-semibold md:text-5xl">Questions, answered</h2>
            <p className="mt-4 max-w-xs text-sm leading-relaxed text-muted">How plans, assessments and assessed lines would work, and what {BRAND_NAME} will and won’t do to your code.</p>
            <Link href="/#how" className="label-mono mt-6 inline-flex items-center gap-2 text-brand hover:text-brand-deep">See how it works <span aria-hidden="true">→</span></Link>
          </div>
          <div className="md:col-span-8"><Faq items={FAQ} /></div>
        </section>
      </Reveal>

      <Reveal>
      <section aria-labelledby="pricing-cta" className="pricing-cta mb-16 mt-16 px-6 py-14 text-center md:px-12 md:py-20">
        <h2 id="pricing-cta" className="display mx-auto max-w-3xl text-3xl font-semibold md:text-5xl">See what your code really does before an attacker does.</h2>
        <div className="mt-8 flex flex-wrap justify-center gap-2">
          <Cta href={START} tone="light">Open the console</Cta>
          <Link href="/#how" className="group label-mono inline-flex h-10 items-center rounded-md bg-white/10 px-4 text-white transition-colors duration-[250ms] hover:bg-white/20">
            <RollText>See how it works</RollText>
          </Link>
        </div>
      </section>
      </Reveal>
    </div>
  );
}
