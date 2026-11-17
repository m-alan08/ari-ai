import { expect, test, type Locator } from '@playwright/test';
import { mkdir } from 'node:fs/promises';


async function expectContrastAtLeastAA(locator: Locator, label: string) {
  const ratio = await locator.evaluate(element => {
    const parseColor = (value: string) => {
      const values = value.match(/[\d.]+/g)?.map(Number) ?? [];
      const scale = value.startsWith('color(') ? 255 : 1;
      const channels = values.slice(0, 3).map(channel => channel * scale);
      return {
        color: [channels[0] ?? 0, channels[1] ?? 0, channels[2] ?? 0] as [number, number, number],
        alpha: values[3] ?? 1,
      };
    };
    const backgrounds: { color: [number, number, number]; alpha: number }[] = [];
    for (let current: Element | null = element; current; current = current.parentElement) {
      const background = parseColor(getComputedStyle(current).backgroundColor);
      if (background.alpha === 0) continue;
      backgrounds.push(background);
      if (background.alpha >= 1) break;
    }
    let background = [0, 0, 0];
    for (const layer of backgrounds.reverse()) {
      background = layer.color.map((channel, i) => {
        const behind = background[i] ?? 0;
        return channel * layer.alpha + behind * (1 - layer.alpha);
      });
    }
    const text = parseColor(getComputedStyle(element).color);
    const foreground = text.color.map((channel, i) => channel * text.alpha + (background[i] ?? 0) * (1 - text.alpha));
    const luminance = (color: number[]) => color
      .map(channel => {
        const value = channel / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      })
      .reduce((sum, value, i) => sum + value * (i === 0 ? 0.2126 : i === 1 ? 0.7152 : 0.0722), 0);
    const textLuminance = luminance(foreground);
    const backgroundLuminance = luminance(background);
    return (Math.max(textLuminance, backgroundLuminance) + 0.05) / (Math.min(textLuminance, backgroundLuminance) + 0.05);
  });
  expect(ratio, `${label} contrast`).toBeGreaterThanOrEqual(4.5);
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 1024, height: 768 }, { width: 390, height: 844 }, { width: 320, height: 740 }]) {
  test(`night navigation stays legible and contained at ${viewport.width}px`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize(viewport);
    await page.goto('/'); // public: no sign-in needed
    await expect(page).toHaveTitle('ARI');
    await expect(page.locator('meta[property="og:site_name"]')).toHaveAttribute('content', 'ARI');
    await expect(page.locator('meta[name="twitter:title"]')).toHaveAttribute('content', 'ARI — Find the flaw. Prove the fix.');
    const heading = page.getByRole('heading', { level: 1 });
    await expect(heading).toHaveText('Find the flaw. Prove the fix.');
    await expect(heading).toHaveAccessibleName('Find the flaw. Prove the fix.');
    await expect(page.getByRole('region', { name: 'Find the flaw. Prove the fix.', exact: true })).toBeVisible();
    await expect(page.locator('[data-hero-eyebrow]')).toBeVisible();
    await expect(page.locator('[data-hero-subhead]')).toContainText('failing test');
    await expect(page.locator('[data-hero-subhead]')).toContainText('fresh checkout');
    await expect(page.locator('[data-hero-subhead]')).toContainText('pull request');
    const hero = page.locator('.hero-content');
    for (const name of ['Go to projects', 'How It Works']) {
      await expect(hero.getByRole('link', { name, exact: true })).toHaveCount(1);
      await expect(hero.getByRole('link', { name, exact: true })).toHaveAccessibleName(name);
    }
    await expectContrastAtLeastAA(page.getByRole('link', { name: 'Go to projects', exact: true }).first(), 'hero CTA');
    await expectContrastAtLeastAA(hero.locator('.hero-cta-secondary .roll > span').first(), 'secondary hero CTA');
    await expectContrastAtLeastAA(page.locator('[data-hero-subhead]'), 'hero subhead');
    await expectContrastAtLeastAA(page.locator('[data-hero-eyebrow]'), 'hero eyebrow');
    for (const note of await page.locator('.illustration-note').all()) await expectContrastAtLeastAA(note, 'illustration note');
    await expectContrastAtLeastAA(page.getByTestId('problem-count'), 'problem count');
    await expectContrastAtLeastAA(page.getByTestId('story-events-line'), 'events card line');
    await expectContrastAtLeastAA(page.getByTestId('story-timeline-capsule'), 'timeline capsule');
    // The demo account is not linked publicly; the call to action opens the console.
    await expect(page.getByRole('link', { name: 'Go to projects', exact: true }).first()).toHaveAttribute('href', '/projects');
    await expect(page.getByRole('link', { name: /demo/i })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'How It Works', exact: true })).toHaveAttribute('href', '#how');
    const nav = page.getByRole('navigation', { name: 'Primary', exact: true });
    const secondary = nav.locator('.nav-secondary');
    await expect(secondary).toHaveAttribute('inert', '');
    await expect(secondary).toHaveCSS('opacity', '0');
    const account = nav.locator('[data-testid="header-signin"], [data-testid="header-account"]');
    await expect(account).toBeVisible();
    const initialAccount = (await account.boundingBox())!;
    const headerBounds = (await page.locator('header').boundingBox())!;
    const heroBounds = (await page.locator('.hero-content').boundingBox())!;
    expect(headerBounds.y + headerBounds.height).toBeLessThanOrEqual(heroBounds.y);
    await secondary.locator('a').first().evaluate(el => (el as HTMLAnchorElement).focus());
    expect(await page.evaluate(() => document.activeElement?.closest('.nav-secondary') !== null)).toBe(false);
    if (viewport.width < 640) {
      await nav.getByRole('button', { name: 'Menu', exact: true }).click();
      for (const name of ['Projects', 'Pricing']) await expect(nav.getByRole('link', { name, exact: true })).toBeVisible();
      await nav.getByRole('link', { name: 'Projects', exact: true }).focus();
      await page.keyboard.press('Escape');
      await expect(nav.getByRole('button', { name: 'Menu', exact: true })).toBeFocused();
      await expect(secondary).toHaveAttribute('inert', '');
    }
    await expect(page.getByRole('link', { name: 'ARI home', exact: true })).toBeVisible();
    await expect.poll(() => page.locator('.hero-in').evaluateAll(els => els.every(el => Number(getComputedStyle(el).opacity) >= 0.99))).toBe(true);
    await expect(page.locator('header .hdr-logo')).toHaveCSS('color', 'rgb(237, 243, 245)');
    await expect(heading.locator('.hero-line').first()).toHaveCSS('color', 'rgb(237, 243, 245)');
    await expect(page.locator('.hero-orb')).toHaveCount(5);
    const orbDurations = await page.locator('.hero-orb').evaluateAll(elements => elements.slice(0, 5).map(el => getComputedStyle(el).animationDuration));
    expect(orbDurations).toEqual(['26s', '31s', '34s', '23s', '37s']);
    await expect(heading.locator('.hero-line').first()).not.toHaveAttribute('data-glow');
    await expect(heading.locator('.hero-line').first()).toHaveCSS('text-shadow', 'none'); // glow is isolated to an opacity-animated pseudo layer
    await expect.poll(() => page.evaluate(() => document.fonts.status)).toBe('loaded');
    const fonts = await page.evaluate(() => ({
      logo: getComputedStyle(document.querySelector('.ari-wordmark-text')!).fontFamily,
      weight: getComputedStyle(document.querySelector('.ari-wordmark-text')!).fontWeight,
      headline: getComputedStyle(document.querySelector('h1')!).fontFamily,
      headlineWeight: getComputedStyle(document.querySelector('h1')!).fontWeight,
      headlineTransform: getComputedStyle(document.querySelector('h1')!).textTransform,
      subhead: getComputedStyle(document.querySelector('[data-hero-subhead]')!).fontFamily,
      eyebrow: getComputedStyle(document.querySelector('[data-hero-eyebrow]')!).fontFamily,
      button: getComputedStyle(document.querySelector('.site-header nav a')!).fontFamily,
    }));
    expect(fonts.logo).toMatch(/chakra/i);
    expect(fonts.weight).toBe('700');
    expect(fonts.headline).toMatch(/^'?spaceGrotesk/i);
    expect(fonts.headlineWeight).toBe('600');
    expect(fonts.headlineTransform).toBe('none');
    expect(fonts.subhead).toMatch(/^'?inter/i);
    expect(fonts.eyebrow).toMatch(/^'?inter/i);
    expect(fonts.button).not.toMatch(/chakra/i);
    for (const scrollY of [0, 99, 100, 150, 110, 0, viewport.height + 200, 3000]) {
      await page.evaluate(y => window.scrollTo(0, y), scrollY);
      await expect.poll(() => page.locator('header').evaluate(el => Math.round(el.getBoundingClientRect().top))).toBe(0);
      const styles = await page.locator('header').evaluate(el => ({ bg: getComputedStyle(el).backgroundColor, border: getComputedStyle(el).borderBottomWidth }));
      expect(styles).toEqual(viewport.width < 640
        ? { bg: 'rgba(13, 20, 24, 0.94)', border: '1px' }
        : { bg: 'rgba(0, 0, 0, 0)', border: '0px' });
      for (const item of await page.locator('header a:not(.hdr-cta)').all()) {
        await expect(item).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
        await expect(item).toHaveCSS('box-shadow', 'none');
        await expect(item).toHaveCSS('border-top-width', '0px');
      }
      await expect(page.locator('header .hdr-logo')).toHaveCSS('color', 'rgb(237, 243, 245)');
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
    await expect(page.locator('header .hdr-logo')).toHaveCSS('color', 'rgb(237, 243, 245)');
    await nav.getByRole('link', { name: 'Pricing', exact: true }).hover();
    await expect(nav.getByRole('link', { name: 'Pricing', exact: true })).toHaveCSS('background-color', 'rgba(237, 243, 245, 0.09)');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByRole('link', { name: 'How It Works', exact: true }).click();
    await expect(page).toHaveURL(/#how$/);
    // The animated anchor can land a few pixels from its CSS scroll margin under CI's renderer.
    await expect.poll(
      () => page.locator('#how').evaluate(el => {
        const scrollMargin = parseFloat(getComputedStyle(el).scrollMarginTop) || 0;
        return Math.abs(Math.round(el.getBoundingClientRect().top) - scrollMargin);
      }),
      { timeout: 20_000 },
    ).toBeLessThanOrEqual(5);
    await nav.getByRole('link', { name: 'Pricing', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'ARI pricing' })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    for (const name of ['Projects', 'Pricing']) await expect(nav.getByRole('link', { name, exact: true })).toBeVisible();
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
  await expect(page.locator('.hero-line').first()).toHaveCSS('animation-name', 'none');
  expect(await page.locator('.hero-line').first().evaluate(el => getComputedStyle(el, '::before').animationName)).toBe('none');
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

test('problem chapter eases to the short change-check headline', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await page.locator('.story-track-3').evaluate(el => {
    const top = el.getBoundingClientRect().top + window.scrollY;
    window.scrollTo(0, top + (el.clientHeight - window.innerHeight) * 0.9);
  });
  await expect(page.getByText('Changes, checked', { exact: true })).toBeVisible();
  await expect(page.getByTestId('story-timeline-capsule')).toHaveCSS('color', 'rgb(237, 243, 245)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1440);
});

test('pricing keeps glass cards, an unboxed CTA and no horizontal overflow', async ({ page }) => {
  await page.goto('/pricing');
  await expect(page.getByRole('heading', { level: 1, name: 'ARI pricing' })).toBeVisible();
  await expect(page.locator('.pricing-cta')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('.pricing-cta')).not.toHaveClass(/hero-ink/);
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  }
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
  for (const [progress, index] of [[0.08, 0], [0.33, 1], [0.55, 2], [0.94, 3]] as const) {
    await chapter.evaluate((el, p) => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      window.scrollTo(0, top + (el.clientHeight - innerHeight) * p);
    }, progress);
    await expect(cards.nth(index)).toHaveAttribute('data-active', 'true');
  }
});

test('every process card holds for several wheel notches before the chapter moves on', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const chapter = page.locator('#how');
  await chapter.evaluate(el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - innerHeight / 2));
  await page.mouse.move(720, 450);
  const pinnedNotches = [0, 0, 0, 0];
  const order: number[] = [];
  for (let notch = 0; notch < 60; notch++) {
    const before = await page.evaluate(() => scrollY);
    await page.mouse.wheel(0, 100);
    // Read the page only once this notch has landed and the chapter's eased progress has caught
    // up with it. A fixed pause read a stale position on a busy runner, which dropped notches
    // from whichever card was on screen and made the counts depend on timing.
    await chapter.evaluate((el, y0) => new Promise<void>((resolve, reject) => {
      const deadline = performance.now() + 5000;
      const check = () => {
        const atEnd = scrollY >= document.documentElement.scrollHeight - innerHeight - 0.5;
        const landed = scrollY >= y0 + 99.5 || atEnd;
        const r = el.getBoundingClientRect();
        const total = r.height - innerHeight;
        const target = total <= 0 ? 1 : Math.min(1, Math.max(0, -r.top / total));
        const eased = parseFloat(getComputedStyle(el).getPropertyValue('--p'));
        if (landed && Math.abs(eased - target) < 0.002) {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())); // let React commit
        } else if (performance.now() > deadline) {
          reject(new Error(`notch did not settle: scrollY ${scrollY} from ${y0}, progress ${eased} of ${target}`));
        } else {
          requestAnimationFrame(check);
        }
      };
      requestAnimationFrame(check);
    }), before);
    const state = await chapter.evaluate(el => {
      const r = el.getBoundingClientRect();
      return {
        active: [...el.querySelectorAll('.story-card')].findIndex(card => card.hasAttribute('data-active')),
        pinned: r.top <= 0.5 && r.bottom >= innerHeight - 0.5,
        past: r.bottom < innerHeight - 0.5,
      };
    });
    if (state.past) break;
    if (!state.pinned) continue;
    pinnedNotches[state.active] = (pinnedNotches[state.active] ?? 0) + 1;
    if (order.at(-1) !== state.active) order.push(state.active);
  }
  expect(order).toEqual([0, 1, 2, 3]);
  for (const notches of pinnedNotches) expect(notches).toBeGreaterThanOrEqual(3);
  expect(pinnedNotches[3]).toBeGreaterThan(pinnedNotches[1]!);
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
  await expect(link).toHaveCSS('color', 'rgb(237, 243, 245)'); // hover brightens in place, never slides
  await expect(link).toHaveCSS('translate', 'none');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(footer.locator('.footer-top')).toHaveCSS('transition-duration', '0s');
});
