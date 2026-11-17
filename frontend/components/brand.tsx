/** Product identity. The mark is the owner-supplied ant, cut out of its paper square. */
export const BRAND_NAME = 'ARI';
export const BRAND_HEADLINE = 'FIND THE FLAW.';
export const BRAND_HEADLINE_REST = 'PROVE THE FIX.';
export const BRAND_TAGLINE_HEADLINE = 'Find the flaw. Prove the fix.';
export const BRAND_TAGLINE = 'A repair counts only when independent, executed tests agree.';

/** Original owner-supplied PNG (kept byte-for-byte). The favicon is the mark on an obsidian tile (app/icon.png). */
export const LOGO_SOURCE = '/brand/ari-ant.png';
/** Transparent ant cut out of the original (361×256), drawn as a mask in the current text colour. */
export const LOGO_SRC = '/brand/ari-ant-mark.png';
export const LOGO_ASPECT = 361 / 256;

/**
 * Just the ant, like kairo.security's mark: no tile or background. It is a CSS mask filled with
 * `currentColor`, so it is dark on light surfaces and white on the dark hero, sign-in and intro.
 * `size` is the height in px; the width follows the ant's aspect ratio.
 */
export function LogoMark({ size = 24, className = '', decorative = false }: { size?: number; className?: string; decorative?: boolean; priority?: boolean }) {
  return (
    <span
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : `${BRAND_NAME} logo`}
      aria-hidden={decorative || undefined}
      data-testid="ari-logo"
      data-src={LOGO_SRC}
      style={{ height: size, width: Math.round(size * LOGO_ASPECT) }}
      className={`ari-mark inline-block shrink-0 ${className}`}
    />
  );
}

export function Wordmark({ className = '', priority = false }: { className?: string; priority?: boolean }) {
  return (
    <span className={`ari-wordmark flex items-center gap-2 ${className}`}>
      <LogoMark size={22} decorative priority={priority} />
      <span className="ari-wordmark-text">{BRAND_NAME}</span>
    </span>
  );
}
