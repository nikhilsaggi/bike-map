import { test, expect, gotoMap, chip, openSection } from './helpers.js';
import { buildFixture, SPEED_BLOCK } from './fixture.js';

const STREETS = 'stat-streets';

// Facts about the network rather than about the rides: the totals that used
// to live only in hero tooltips, and the direction-split ranking.
test.describe('streets', () => {
  test('section is closed until its chip is clicked', async ({ page }) => {
    await gotoMap(page);
    await expect(chip(page, STREETS)).toBeVisible();
    await expect(page.locator('#stat-streets')).toBeHidden();
    await expect(chip(page, STREETS)).toHaveAttribute('aria-expanded', 'false');

    await openSection(page, STREETS);
    await expect(page.locator('#speed-list')).toBeVisible();
    await expect(chip(page, STREETS)).toHaveAttribute('aria-expanded', 'true');

    await chip(page, STREETS).click();
    await expect(page.locator('#stat-streets')).toBeHidden();
  });

  test('shows the network totals', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    const rows = page.locator('#streets-totals .r-row');
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText('Drawn');
    await expect(rows.nth(0)).toContainText('62 mi'); // 100 km
    await expect(rows.nth(1)).toContainText('Segments');
    await expect(rows.nth(1)).toContainText('3');
    await expect(rows.nth(2)).toContainText('Of rideable NYC');
    await expect(rows.nth(2)).toContainText('12.3%');
    await expect(rows.nth(3)).toContainText('Most-ridden segment');
    await expect(rows.nth(3)).toContainText('4×');
  });

  test('the coverage percentage carries its own numerator and denominator', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    // 12.3% is measured over a narrower network than the 62 drawn miles, so
    // the two must not read as one division. 45.5 km of 370 km.
    await expect(page.locator('#streets-totals .r-sub'))
      .toHaveText('28 of 230 mi, paths excluded');
  });

  test('lists corridors in rank order with mph converted from km/h', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);

    const names = await page.locator('.sp-name').allTextContents();
    expect(names).toEqual(['Crest Bridge', 'Crest Bridge', 'Flat Street']);

    // 16.09 km/h -> 10.0 mph, 8.05 -> 5.0, 3.22 -> 2.0.
    const gaps = await page.locator('.sp-gap').allTextContents();
    expect(gaps).toEqual(['10.0', '5.0', '2.0']);

    const dirs = await page.locator('.sp-dir').allTextContents();
    expect(dirs).toEqual(['E', 'W', 'N']);
  });

  test('each row shows both directions, length, and pass count', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    // 24.14 km/h -> 15.0 mph, 8.05 -> 5.0; 800 m -> 0.50 mi.
    await expect(page.locator('.sp-detail').first())
      .toHaveText('15.0 vs 5.0 mph over 0.50 mi, 12+ passes each way');
  });

  test('the ranking says how many stretches it was drawn from', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    // speed.measured was exported and rendered nowhere before.
    await expect(page.locator('#speed-lead')).toContainText('Of 42 stretches measured');
    await expect(page.locator('#speed-lead')).toContainText('820 ft+, ridden 3+ times each way');
  });

  test('the same bridge appears once per direction, never as one row', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    const rows = page.locator('.sp-row').filter({ hasText: 'Crest Bridge' });
    await expect(rows).toHaveCount(2);
    // Opposite directions: the crest flip, not a duplicate of one stretch.
    await expect(rows.nth(0).locator('.sp-dir')).toHaveText('E');
    await expect(rows.nth(1).locator('.sp-dir')).toHaveText('W');
  });

  test('clicking a row flies to the corridor and marks it', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    const before = await page.evaluate(() => map.getZoom());
    await page.locator('.sp-row').first().click();
    await page.waitForTimeout(900);
    const after = await page.evaluate(() => ({
      zoom: map.getZoom(),
      center: map.getCenter(),
      marked: speedMarker !== null,
    }));
    expect(after.marked).toBe(true);
    expect(after.zoom).toBeGreaterThanOrEqual(before);
    expect(after.center.lat).toBeCloseTo(SPEED_BLOCK.corridors[0].at[1], 2);
    expect(after.center.lng).toBeCloseTo(SPEED_BLOCK.corridors[0].at[0], 2);
  });

  test('closing the section clears the corridor marker', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await page.locator('.sp-row').first().click();
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    await chip(page, STREETS).click();
    expect(await page.evaluate(() => speedMarker !== null)).toBe(false);
  });

  test('switching to another section clears the corridor marker', async ({ page }) => {
    // The ring belongs to the corridor list; leaving it behind would strand a
    // cyan circle on the map with nothing on screen explaining it.
    await gotoMap(page);
    await openSection(page, STREETS);
    await page.locator('.sp-row').first().click();
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    await openSection(page, 'stat-weather');
    expect(await page.evaluate(() => speedMarker !== null)).toBe(false);
  });

  test('every section fits a short viewport', async ({ page }) => {
    // Stacking all the sections at once pushed the panel past 700px, so on a
    // short window the last one fell off the bottom. One section at a time
    // plus a bounded section height is what keeps this true.
    await page.setViewportSize({ width: 1280, height: 560 });
    await gotoMap(page);

    for (const section of ['stat-years', 'stat-riding', 'stat-weather', STREETS]) {
      await openSection(page, section);
      const fits = await page.evaluate(() => {
        const s = document.getElementById('stats').getBoundingClientRect();
        return s.bottom <= window.innerHeight;
      });
      expect(fits, `#${section} keeps the panel on screen`).toBe(true);
      // Both the way in and the way out stay reachable without scrolling.
      await expect(page.locator('#stat-chips')).toBeInViewport();
      await expect(page.locator('#stats-toggle')).toBeInViewport();
    }
  });

  test('the ranking drops out but the totals keep the section', async ({ page }) => {
    await gotoMap(page, buildFixture({ speed: null }));
    await expect(chip(page, STREETS)).toBeVisible();
    await openSection(page, STREETS);
    await expect(page.locator('#speed-block')).toBeHidden();
    await expect(page.locator('#streets-totals .r-row')).toHaveCount(4);
  });

  test('an empty corridor list drops the ranking too', async ({ page }) => {
    await gotoMap(page, buildFixture({
      speed: { corridors: [], measured: 0, split_n: 3, min_m: 250.0 },
    }));
    await openSection(page, STREETS);
    await expect(page.locator('#speed-block')).toBeHidden();
  });

  test('chip stays hidden when there is nothing about streets to show', async ({ page }) => {
    await gotoMap(page, buildFixture({
      speed: null, total_km: null, total_edges: null, coverage: null, max_count: null,
    }));
    await expect(chip(page, STREETS)).toBeHidden();
    await expect(page.locator('#stat-streets')).toBeHidden();
  });
});
