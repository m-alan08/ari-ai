import type { Metadata } from 'next';
import localFont from 'next/font/local';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { Ambient } from '@/components/ambient';
import { BRAND_NAME, Wordmark } from '@/components/brand';
import { DemoBanner } from '@/components/demo-banner';
import { HeaderContrast } from '@/components/header-contrast';
import { RouteTransition } from '@/components/route-transition';
import { SiteNavigation } from '@/components/site-navigation';
import { SiteFooter } from '@/components/site-footer';
import { SmoothScroll } from '@/components/smooth-scroll';
import './globals.css';
import './interface.css';

// Packaged OFL fonts: no external font service is needed at build or runtime.
const inter = localFont({ src: './fonts/inter-latin-wght-normal.woff2', weight: '400 600', display: 'swap', variable: '--font-inter' });
const spaceGrotesk = localFont({ src: './fonts/space-grotesk-latin-wght-normal.woff2', weight: '500 700', display: 'swap', variable: '--font-space-grotesk' });
const chakraPetch = localFont({ src: './fonts/chakra-petch-latin-700-normal.woff2', weight: '700', display: 'swap', variable: '--font-chakra-petch' });
const sixtyfour = localFont({ src: './fonts/sixtyfour-latin-400.woff2', weight: '400', display: 'swap', variable: '--font-sixtyfour' });
const plexMono = localFont({ src: [
  { path: './fonts/ibm-plex-mono-latin-400-normal.woff2', weight: '400', style: 'normal' },
  { path: './fonts/ibm-plex-mono-latin-500-normal.woff2', weight: '500', style: 'normal' },
  { path: './fonts/ibm-plex-mono-latin-600-normal.woff2', weight: '600', style: 'normal' },
], display: 'swap', variable: '--font-ibm-plex-mono' });

export const metadata: Metadata = {
  title: `${BRAND_NAME} Console`,
  description: 'Code intelligence, functional review, seven-agent security investigation and monitoring — backed by persisted evidence.',
  openGraph: {
    title: `${BRAND_NAME} — Find the flaw. Prove the fix.`,
    siteName: BRAND_NAME,
    description: 'Code intelligence, review, repair and independent verification.',
  },
  twitter: {
    card: 'summary',
    title: `${BRAND_NAME} — Find the flaw. Prove the fix.`,
    description: 'Code intelligence, review, repair and independent verification.',
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${spaceGrotesk.variable} ${plexMono.variable} ${chakraPetch.variable} ${sixtyfour.variable}`}>
      <body className="min-h-screen antialiased">
        <Ambient />
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:rounded focus:bg-accent focus:px-2 focus:py-1 focus:text-bg">Skip to content</a>
        <header className="site-header relative z-40">
          <div className="site-header-inner mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
            <Link href="/" aria-label={`${BRAND_NAME} home`} className="hdr-logo">
              <Wordmark priority />
            </Link>
            <SiteNavigation />
          </div>
        </header>
        <HeaderContrast />
        <SmoothScroll />
        <RouteTransition />
        <DemoBanner />
        <main id="main" className="mx-auto max-w-7xl px-4 py-6">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}

