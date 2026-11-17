'use client';

import { LandingFooter } from '@/components/landing-footer';

import Link from 'next/link';
import { useState, type CSSProperties } from 'react';
import { BRAND_NAME, BRAND_TAGLINE_HEADLINE } from '@/components/brand';
import { HowItWorks, ProblemScene, ProofStats, Walkthrough } from '@/components/story';
import { HeroStage } from '@/components/hero';
import { IntroLoader } from '@/components/intro-loader';
import { OptionalUserBar } from '@/components/user-bar';
import { DEMO_ONLY } from '@/lib/deployment';
import { RollText } from '@/components/ui';

// A demo-only build has no backend, so the calls to action open the access-code sign-in.
const START = DEMO_ONLY ? { href: '/access', hero: 'Sign in' } : { href: '/projects', hero: 'Go to projects' };

const i = (n: number) => ({ '--i': n }) as CSSProperties;

export default function HomePage() {
  const [revealed, setRevealed] = useState(false);

  return (
    <div className="home-deep marketing-atmosphere">
      <IntroLoader onReveal={() => setRevealed(true)} />
      <HeroStage className="hero-full px-6 pb-14 pt-36 md:px-12 md:pt-28" labelledBy="hero-title" play={revealed} liquid>
        <div className="hero-center hero-copy mx-auto w-full max-w-5xl text-center">
          <p className="hero-eyebrow hero-in" style={i(0)} data-hero-eyebrow>Code review and repair, verified</p>
          <h1 id="hero-title" className="hero-headline hero-title hero-in" style={i(1)}>
            <span className="hero-line">{BRAND_TAGLINE_HEADLINE}</span>
          </h1>
          <p className="hero-subhead hero-in" style={i(2)} data-hero-subhead>
            {BRAND_NAME} checks your code against what it was meant to do and proves each flaw with a failing test. A separate agent verifies the fix on a fresh checkout, and every fix ships as a pull request that a person merges.
          </p>
          <div className="hero-actions hero-in" style={i(3)}>
            <Link href={START.href} className="group hero-cta hero-cta-primary">
              <RollText>{START.hero}</RollText>
            </Link>
            <a href="#how" className="group hero-cta hero-cta-secondary">
              <RollText>How It Works</RollText>
            </a>
          </div>
        </div>
      </HeroStage>

      <div className="hero-cover -mb-6 pt-6">
        {!DEMO_ONLY && <OptionalUserBar />}

        <ProblemScene />
        <HowItWorks />
        <Walkthrough />
        <ProofStats />
        <LandingFooter />


      </div>
    </div>
  );
}
