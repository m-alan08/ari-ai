import { createHash } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import { signIn } from './helpers';

const sourceHash = '0d4d68e2e5917248016e2368773ac9a40417188737da6623485ecd82da47eeae';

async function loadedMark(page: Page, selector: string) {
  const mark = page.locator(selector).first();
  await expect(mark).toBeVisible();
  // The bare ant is a CSS mask of the transparent mark; confirm the mask resolved to that asset.
  await expect.poll(() => mark.evaluate(element => getComputedStyle(element).maskImage || getComputedStyle(element).webkitMaskImage)).toContain('ari-ant-mark.png');
  const ok = await page.evaluate(async () => (await fetch('/brand/ari-ant-mark.png')).ok);
  expect(ok).toBe(true);
}

async function screenshot(page: Page, name: string) {
  if (!process.env.ARI_LOGO_SCREENSHOTS) return;
  await mkdir(process.env.ARI_LOGO_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: `${process.env.ARI_LOGO_SCREENSHOTS}/${name}.png` });
}

async function settledHero(page: Page) {
  await expect(page.locator('.hero-stage h1')).toBeVisible();
  await expect.poll(() => page.locator('.hero-stage .hero-in').evaluateAll(elements => elements.every(element => Number(getComputedStyle(element).opacity) >= 0.99))).toBe(true);
}

async function settledHeader(page: Page) {
  await loadedMark(page, 'header [data-testid="ari-logo"]');
  await expect.poll(() => page.locator('header .hdr-logo').evaluateAll(elements => elements.every(element => Number(getComputedStyle(element).opacity) >= 0.99))).toBe(true);
}

test('supplied logo loads on sign-in, scrolled header and projects on desktop/mobile', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    // The real session endpoint intentionally returns 401 before development sign-in.
    const signedOut = message.text().includes('401') && message.location().url.endsWith('/api/v1/me');
    if (message.type() === 'error' && !signedOut) errors.push(message.text());
  });
  await page.goto('/projects'); // the sign-in panel; the home page is public
  await expect(page).toHaveTitle('ARI Console');
  await expect(page.getByRole('heading', { name: 'Sign in to your workspace', exact: true })).toBeVisible();
  await settledHeader(page);
  await loadedMark(page, '.hero-ink [data-testid="ari-logo"]');
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute('href', /^\/icon\.png(?:\?|$)/);
  const icon = await page.request.get((await page.locator('link[rel="icon"]').getAttribute('href'))!);
  expect(icon.ok()).toBe(true);
  expect(createHash('sha256').update(await icon.body()).digest('hex')).toBe(sourceHash);
  const asset = await page.request.get('/brand/ari-ant.png');
  expect(asset.ok()).toBe(true);
  expect(createHash('sha256').update(await asset.body()).digest('hex')).toBe(sourceHash);
  const login = page.getByLabel('GitHub login');
  await login.fill('invalid!');
  expect(await login.evaluate((input: HTMLInputElement) => input.validity.patternMismatch)).toBe(true);
  await login.fill('e2e-alice');
  expect(await login.evaluate((input: HTMLInputElement) => input.checkValidity())).toBe(true);
  await screenshot(page, 'desktop-sign-in');
  await signIn(page, 'e2e-alice');
  await page.goto('/'); // sign-in happens on a console page; the hero is the public home
  await settledHero(page);
  await expect(page.locator('.hero-stage [data-testid="ari-logo"]')).toHaveCount(0);
  await screenshot(page, 'desktop-hero');
  await page.evaluate(() => window.scrollTo(0, 120));
  await settledHeader(page);
  await screenshot(page, 'desktop-scrolled-header');
  await page.getByRole('link', { name: 'Projects', exact: true }).click();
  // Projects opens the connected-organization drill-down at /connect.
  await expect(page).toHaveURL(/\/connect$/);
  await loadedMark(page, 'header [data-testid="ari-logo"]');
  await expect(page.getByRole('heading', { name: 'Organizations', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot(page, 'mobile-projects');
  await page.getByRole('link', { name: 'ARI home', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await settledHero(page);
  await expect(page.locator('.hero-stage [data-testid="ari-logo"]')).toHaveCount(0);
  await screenshot(page, 'mobile-hero');
  await page.evaluate(() => window.scrollTo(0, 120));
  await settledHeader(page);
  const headerMark = await page.locator('header [data-testid="ari-logo"]').boundingBox();
  expect(headerMark!.x).toBeGreaterThanOrEqual(0);
  expect(headerMark!.x + headerMark!.width).toBeLessThanOrEqual(390);
  await screenshot(page, 'mobile-scrolled-header');
  await expect(page.locator('nextjs-portal')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('intro uses the supplied mark and exits; reduced motion skips it', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  // Exercise the real intro rather than the normal automation skip.
  await context.addInitScript(() => Object.defineProperty(navigator, 'webdriver', { get: () => false }));
  const page = await context.newPage();
  try {
    await signIn(page, 'e2e-alice');
    await page.goto('/'); // the intro plays on the public home page
    await loadedMark(page, '.intro [data-testid="ari-logo"]');
    await expect.poll(() => page.locator('.intro-mark').evaluate(element => Number(getComputedStyle(element).opacity))).toBe(1);
    await screenshot(page, 'desktop-intro');
    await expect(page.locator('.intro')).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('ari-intro-played'))).toBe('1');
    await page.reload();
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await expect(page.locator('.intro')).toHaveCount(0);
    await page.evaluate(() => sessionStorage.removeItem('ari-intro-played'));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.reload();
    await settledHero(page);
    await expect(page.locator('.intro')).toHaveCount(0);
  } finally {
    await context.close();
  }
});
