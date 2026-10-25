'use client';

import { LandingFooter } from '@/components/landing-footer';

import Link from 'next/link';
import { useState, type CSSProperties } from 'react';
import { BRAND_HEADLINE, BRAND_HEADLINE_REST, BRAND_TAGLINE } from '@/components/brand';
import { HowItWorks, ProblemScene, ProofStats, Walkthrough } from '@/components/story';
import { ASSESSMENT_LOOP, HeroStage, useLoopCursor } from '@/components/hero';
import { IntroLoader } from '@/components/intro-loader';
import { OptionalUserBar } from '@/components/user-bar';
import { ExplainerVideo } from '@/components/explainer-video';
import { DEMO_ONLY } from '@/lib/deployment';
import { RollText } from '@/components/ui';

const LOOP = ASSESSMENT_LOOP;

// A demo-only build has no backend, so the calls to action open the access-code sign-in.
const START = DEMO_ONLY ? { href: '/access', hero: 'Sign in' } : { href: '/projects', hero: 'Go to projects' };

const i = (n: number) => ({ '--i': n }) as CSSProperties;

export default function HomePage() {
  const active = useLoopCursor(LOOP.length);
  const [revealed, setRevealed] = useState(false);

  return (
    <div className="home-deep">
      <IntroLoader onReveal={() => setRevealed(true)} />
      <HeroStage className="hero-full px-6 pb-14 pt-44 md:px-12 md:pt-32" labelledBy="hero-title" play={revealed} colony>
        <div className="mx-auto w-full max-w-7xl text-center">
          <p className="kicker kicker-dark hero-in mx-auto" style={i(0)}>Seven agents · one shared brain</p>
          <h1 id="hero-title" className="hero-headline display mt-7 font-bold hero-in" style={i(1)}>
            <span data-spotlight className="hero-line">{BRAND_HEADLINE}</span><br />
            <span data-spotlight className="hero-line hero-line-dim">{BRAND_HEADLINE_REST}</span>
          </h1>
          <p className="hero-in mx-auto mt-7 max-w-md text-sm text-white/75 md:text-base" style={i(2)}>{BRAND_TAGLINE}</p>
          <div className="hero-in mt-9 flex flex-wrap justify-center gap-3" style={i(3)}>
            <Link href={START.href} className="group label-mono inline-flex h-11 items-center rounded-md bg-white px-4 text-ink transition-colors duration-[250ms] hover:bg-brand hover:text-on-brand">
              <RollText>{START.hero}</RollText>
            </Link>
            <a href="#how" className="group label-mono inline-flex h-11 items-center rounded-md bg-white/10 px-4 text-white backdrop-blur transition-colors duration-[250ms] hover:bg-white/20">
              <RollText>How It Works</RollText>
            </a>
          </div>
          <ol aria-label="Assessment loop" className="hero-in mx-auto mt-14 flex max-w-4xl flex-wrap justify-center gap-1.5" style={i(4)}>
            {LOOP.map((step, n) => (
              <li key={step} data-active={active === n || undefined} className="hero-step label-mono flex items-center gap-1.5 rounded-md bg-white/10 px-2.5 py-1 text-white/85 backdrop-blur transition-colors duration-[250ms] hover:bg-white/20">
                <span className="hero-step-num">{String(n + 1).padStart(2, '0')}</span>{step}
              </li>
            ))}
          </ol>
        </div>
      </HeroStage>

      <div className="hero-cover -mb-6 pt-6">
        {!DEMO_ONLY && <OptionalUserBar />}

        <ExplainerVideo />
        <ProblemScene />
        <HowItWorks />
        <Walkthrough />
        <ProofStats />
        <LandingFooter />


      </div>
    </div>
  );
}
