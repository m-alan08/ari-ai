import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(path.resolve(import.meta.dirname, '../../app/globals.css'), 'utf8');

const token = (name: string) => {
  const m = css.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`token ${name} missing`);
  return m[1]!;
};
const lum = (hex: string) => {
  const c = [1, 3, 5]
    .map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!;
};
const ratio = (a: string, b: string) => {
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};
const over = (opacity: number, fg: string, bg: string) =>
  '#' +
  [1, 3, 5]
    .map(i => Math.round(opacity * parseInt(fg.slice(i, i + 2), 16) + (1 - opacity) * parseInt(bg.slice(i, i + 2), 16)))
    .map(v => v.toString(16).padStart(2, '0'))
    .join('');

describe('WCAG contrast of design tokens', () => {
  it('faint text meets 4.5:1 on white and panel-2 and stays lighter than muted', () => {
    expect(ratio(token('faint'), '#ffffff')).toBeGreaterThanOrEqual(4.5);
    expect(ratio(token('faint'), token('panel-2'))).toBeGreaterThanOrEqual(4.5);
    expect(lum(token('faint'))).toBeGreaterThan(lum(token('muted')));
  });

  it('control border meets 3:1 on white while line stays decorative', () => {
    expect(ratio(token('control'), '#ffffff')).toBeGreaterThanOrEqual(3);
    expect(ratio(token('line'), '#ffffff')).toBeLessThan(3);
  });

  it('hero text over the brand colour keeps 4.5:1', () => {
    const brand = token('brand');
    const body = css.match(/\.hero-ink p[^{]*\{[^}]*rgba\(255,\s*255,\s*255,\s*([\d.]+)\)/);
    const dim = css.match(/\.hero-headline \.hero-line-dim \{[^}]*color:\s*(#[0-9a-fA-F]{6})/);
    expect(body).not.toBeNull();
    expect(dim).not.toBeNull();
    expect(ratio(over(Number(body![1]), '#ffffff', brand), brand)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(dim![1]!, brand)).toBeGreaterThanOrEqual(4.5);
  });

  it('night tokens (every page but the landing) keep text and controls readable', () => {
    const block = css.slice(css.indexOf(':root:not(:has(.hero-full)) {'));
    const night = (name: string) => {
      const m = block.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`));
      if (!m) throw new Error(`night token ${name} missing`);
      return m[1]!;
    };
    for (const surface of [night('bg'), night('panel'), night('panel-2')]) {
      expect(ratio(night('ink'), surface)).toBeGreaterThanOrEqual(7);
      expect(ratio(night('muted'), surface)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(night('faint'), surface)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(night('ember'), surface)).toBeGreaterThanOrEqual(4.5);
      for (const status of ['ok', 'warn', 'bad', 'info']) expect(ratio(night(status), surface)).toBeGreaterThanOrEqual(4.5);
    }
    expect(ratio(night('control'), night('panel'))).toBeGreaterThanOrEqual(3);
    expect(lum(night('faint'))).toBeLessThan(lum(night('muted')));
    // The landing keeps its own palette: night applies only where there is no full hero.
    expect(css).toContain(':root:not(:has(.hero-full)) {');
  });

  it('disables animate-pulse under prefers-reduced-motion', () => {
    expect(css).toMatch(/prefers-reduced-motion: reduce\)\s*\{[^}]*\.animate-pulse\s*\{\s*animation:\s*none/);
  });

  it('keeps the warm headline core above 4.5:1 over the brand background, with no glow', () => {
    expect(ratio('#fff7f2', token('brand'))).toBeGreaterThanOrEqual(4.7);
    const rule = [...css.matchAll(/\.hero-headline \.hero-line\s*\{([^}]*)\}/g)]
      .map(match => match[1]!)
      .find(declarations => declarations.includes('color: #fff7f2;'));
    expect(rule).toContain('color: #fff7f2;');
    // The glow is isolated to an opacity-animated pseudo layer; the text core remains crisp.
    expect(rule).not.toContain('text-shadow');
  });
});


it('keeps the effective second-line headline colors above 4.5:1 on brand', () => {
  const color = css.match(/\.hero-headline \.hero-line-dim \{[^}]*color:\s*(#[0-9a-f]{6})/i)![1]!;
  expect(ratio(color, token('brand'))).toBeGreaterThanOrEqual(4.5);
});

it('uses slow transform-only hero blobs and a static reduced-motion glow', () => {
  const durations = [...css.matchAll(/\.hero-orb-[a-e][^{]*\{[^}]*animation: hero-drift-[a-e] ([\d.]+)s/g)]
    .map(match => Number(match[1]))
    .sort((a, b) => a - b);
  expect(durations).toEqual([28, 32, 38, 42, 45]);
  expect(css).toContain('animation: ember-breathe 6s ease-in-out infinite alternate');
  expect(css).toMatch(/prefers-reduced-motion: reduce\)[\s\S]*?\.hero-headline \.hero-line::before \{ animation: none/);
});
