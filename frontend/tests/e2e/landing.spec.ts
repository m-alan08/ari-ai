import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const steps = ['Understand', 'Review', 'Attack Safely', 'Diagnose', 'Repair', 'Verify', 'Monitor'];

for (const viewport of [{ width: 1440, height: 1000 }, { width: 1024, height: 768 }, { width: 390, height: 844 }, { width: 320, height: 740 }]) {
  test(`floating navigation and minimal hero at ${viewport.width}px`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize(viewport);
    await page.goto('/'); // public: no sign-in needed
    await expect(page).toHaveTitle('ARI Console');
    await expect(page.locator('meta[property="og:site_name"]')).toHaveAttribute('content', 'ARI');
    await expect(page.locator('meta[name="twitter:title"]')).toHaveAttribute('content', 'ARI — Find the flaw. Prove the fix.');
    const heading = page.getByRole('heading', { level: 1 });
    await expect(heading).toHaveText('FIND THE FLAW.PROVE THE FIX.');
    await expect(page.locator('.hero-content p')).toHaveCount(2);
    // The demo account is not linked publicly; the call to action opens the console.
    await expect(page.getByRole('link', { name: 'Go to projects', exact: true }).first()).toHaveAttribute('href', '/projects');
    await expect(page.getByRole('link', { name: /demo/i })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'How It Works', exact: true })).toHaveAttribute('href', '#how');
    await expect(page.locator('[aria-label="Assessment loop"] li')).toHaveText(steps.map((step, i) => `${String(i + 1).padStart(2, '0')}${step}`));
    const nav = page.getByRole('navigation', { name: 'Primary', exact: true });
    const secondary = nav.locator('.nav-secondary');
    await expect(secondary).toHaveAttribute('inert', '');
    await expect(secondary).toHaveCSS('opacity', '0');
    const account = nav.locator('[data-testid="header-signin"], [data-testid="header-account"]');
    await expect(account).toBeVisible();
    const initialAccount = (await account.boundingBox())!;
    await secondary.locator('a').first().evaluate(el => (el as HTMLAnchorElement).focus());
    expect(await page.evaluate(() => document.activeElement?.closest('.nav-secondary') !== null)).toBe(false);
    if (viewport.width < 640) {
      await nav.getByRole('button', { name: 'Menu', exact: true }).click();
      for (const name of ['Projects', 'Defense Lab', 'Pricing']) await expect(nav.getByRole('link', { name, exact: true })).toBeVisible();
      await nav.getByRole('link', { name: 'Projects', exact: true }).focus();
      await page.keyboard.press('Escape');
      await expect(nav.getByRole('button', { name: 'Menu', exact: true })).toBeFocused();
      await expect(secondary).toHaveAttribute('inert', '');
    }
    await expect(page.getByRole('link', { name: 'ARI home', exact: true })).toBeVisible();
    await expect.poll(() => page.locator('.hero-in').evaluateAll(els => els.every(el => Number(getComputedStyle(el).opacity) >= 0.99))).toBe(true);
    await expect(page.locator('header .hdr-logo')).toHaveCSS('color', 'rgb(255, 255, 255)');
    await expect(heading.locator('.hero-line').first()).toHaveCSS('color', 'rgb(255, 247, 242)');
    await expect(heading.locator('.hero-line').first()).toHaveCSS('text-shadow', 'none'); // no glow on the headline
    await expect.poll(() => page.evaluate(() => document.fonts.status)).toBe('loaded');
    const fonts = await page.evaluate(() => ({
      logo: getComputedStyle(document.querySelector('.ari-wordmark-text')!).fontFamily,
      weight: getComputedStyle(document.querySelector('.ari-wordmark-text')!).fontWeight,
      headline: getComputedStyle(document.querySelector('h1')!).fontFamily,
      button: getComputedStyle(document.querySelector('.site-header nav a')!).fontFamily,
    }));
    expect(fonts.logo).toMatch(/chakra/i);
    expect(fonts.weight).toBe('700');
    expect(fonts.headline).not.toMatch(/chakra/i);
    expect(fonts.button).not.toMatch(/chakra/i);
    for (const scrollY of [0, 99, 100, 150, 110, 0, viewport.height + 200, 3000]) {
      await page.evaluate(y => window.scrollTo(0, y), scrollY);
      await expect.poll(() => page.locator('header').evaluate(el => Math.round(el.getBoundingClientRect().top))).toBe(0);
      const styles = await page.locator('header').evaluate(el => ({ bg: getComputedStyle(el).backgroundColor, border: getComputedStyle(el).borderBottomWidth }));
      expect(styles).toEqual({ bg: 'rgba(0, 0, 0, 0)', border: '0px' });
      for (const item of await page.locator('header a:not(.hdr-cta)').all()) {
        await expect(item).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
        await expect(item).toHaveCSS('box-shadow', 'none');
        await expect(item).toHaveCSS('border-top-width', '0px');
      }
      if (scrollY === viewport.height + 200) await expect(page.locator('header .hdr-logo')).toHaveCSS('color', 'rgb(28, 18, 16)');
      await expect(secondary).toHaveCSS('opacity', scrollY >= 100 ? '1' : '0');
      await expect(secondary).toHaveAttribute('aria-hidden', String(scrollY < 100));
      for (const item of await secondary.locator('a').all()) {
        if (scrollY < 100) await expect(item).toBeHidden();
        else await expect(item).toBeVisible();
      }
      // Pricing is styled like its sibling links (#290): no filled call-to-action pill.
      await expect(secondary.locator('a[href="/pricing"]')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
      const currentAccount = (await account.boundingBox())!;
      expect(currentAccount.x).toBe(initialAccount.x);
      expect(currentAccount.y).toBe(initialAccount.y);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
      const boxes = await page.locator('header a').evaluateAll(els => els.map(el => { const r = el.getBoundingClientRect(); return { x: r.x, right: r.right, height: r.height }; }));
      expect(boxes.every(r => r.x >= 0 && r.right <= viewport.width && r.height >= 44)).toBe(true);
      if (process.env.ARI_LANDING_SCREENSHOTS && (scrollY === 0 || scrollY === 100 || scrollY === viewport.height + 200)) {
        await mkdir(process.env.ARI_LANDING_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: `${process.env.ARI_LANDING_SCREENSHOTS}/${viewport.width}-${scrollY === 0 ? 'hero' : scrollY === 100 ? 'revealed' : 'scrolled'}.png` });
      }
    }
    // Move the dark chapter under every row of controls, not only below the anchor offset.
    await page.locator('#how').evaluate(el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY + 200));
    await expect(page.locator('header .hdr-logo')).toHaveCSS('color', 'rgb(255, 255, 255)');
    await nav.getByRole('link', { name: 'Defense Lab', exact: true }).hover();
    await expect(nav.getByRole('link', { name: 'Defense Lab', exact: true })).toHaveCSS('background-color', 'rgba(255, 255, 255, 0.09)');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByRole('link', { name: 'How It Works', exact: true }).click();
    await expect(page).toHaveURL(/#how$/);
    await expect.poll(() => page.locator('#how').evaluate(el => Math.round(el.getBoundingClientRect().top))).toBe(viewport.width < 640 ? 176 : 80);
    await nav.getByRole('link', { name: 'Pricing', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'ARI pricing' })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    for (const name of ['Projects', 'Defense Lab', 'Pricing']) await expect(nav.getByRole('link', { name, exact: true })).toBeVisible();
    await expect(page.locator('body')).not.toContainText('Ari AI');
    await expect(page.locator('nextjs-portal')).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test('reduced motion keeps the hero, logo and menu visible', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/'); // public: no sign-in needed
  await expect(page.locator('.intro')).toHaveCount(0);
  await expect(page.locator('.hero-content')).toHaveCSS('opacity', '1');
  await expect(page.locator('.hero-orb').first()).toHaveCSS('animation-name', 'none');
  await expect(page.locator('.nav-secondary')).toHaveCSS('transition-duration', '0s');
  await expect(page.locator('.nav-secondary')).toHaveAttribute('inert', '');
  await page.evaluate(() => window.scrollTo(0, 100));
  await expect(page.locator('.nav-secondary')).toHaveCSS('opacity', '1');
  await expect(page.locator('.nav-secondary')).toHaveCSS('translate', 'none');
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.locator('.nav-secondary')).toHaveCSS('opacity', '0');
  await expect(page.getByRole('link', { name: 'ARI home', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Go to projects', exact: true }).first().click();
  await expect(page).toHaveURL(/\/projects$/);
});


test('Ari footer fits desktop and mobile', async ({ page }) => {
  await page.goto('/');
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    const footer = page.getByTestId('landing-footer');
    await footer.scrollIntoViewIfNeeded();
    await expect(footer.getByRole('navigation', { name: 'Footer' })).toBeVisible();
    expect((await footer.boundingBox())!.height).toBeLessThan(500);
    if (process.env.ARI_LANDING_SCREENSHOTS) {
      await mkdir(process.env.ARI_LANDING_SCREENSHOTS, { recursive: true });
      await footer.screenshot({ path: `${process.env.ARI_LANDING_SCREENSHOTS}/${width}-footer.png` });
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
});


test('process cards traverse with scrolling again', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  const chapter = page.locator('#how');
  const cards = chapter.locator('.story-card');
  for (const [progress, index] of [[0.08, 0], [0.4, 1], [0.68, 2], [0.94, 3]] as const) {
    await chapter.evaluate((el, p) => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      window.scrollTo(0, top + (el.clientHeight - innerHeight) * p);
    }, progress);
    await expect(cards.nth(index)).toHaveAttribute('data-active', 'true');
  }
});

test('motion accents respond to hover and respect reduced motion', async ({ page }) => {
  await page.goto('/');
  const cta = page.locator('.hero-full .hero-in > a').first();
  await cta.hover();
  await expect(cta).toHaveCSS('translate', '0px -3px');
  const footer = page.getByTestId('landing-footer');
  await footer.scrollIntoViewIfNeeded();
  await expect(footer.locator('.footer-top')).toHaveAttribute('data-shown', 'true');
  const link = footer.getByRole('link', { name: 'Pricing', exact: true });
  await link.hover();
  await expect(link).toHaveCSS('translate', '4px');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(link).toHaveCSS('translate', 'none');
  await expect(footer.locator('.footer-top')).toHaveCSS('transition-duration', '0s');
});
