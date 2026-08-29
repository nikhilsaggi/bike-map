import { test, expect, gotoMap } from './helpers.js';
import { buildFixture, SPEED_BLOCK } from './fixture.js';

test.describe('direction-split speed', () => {
  test('section is collapsed until toggled', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#stat-speed')).toBeVisible();
    await expect(page.locator('#speed-list')).toBeHidden();
    await expect(page.locator('#speed-toggle')).toHaveAttribute('aria-expanded', 'false');

    await page.click('#speed-toggle');
    await expect(page.locator('#speed-list')).toBeVisible();
    await expect(page.locator('#speed-toggle')).toHaveAttribute('aria-expanded', 'true');

    await page.click('#speed-toggle');
    await expect(page.locator('#speed-list')).toBeHidden();
  });

  test('lists corridors in rank order with mph converted from km/h', async ({ page }) => {
    await gotoMap(page);
    await page.click('#speed-toggle');

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
    await page.click('#speed-toggle');
    // 24.14 km/h -> 15.0 mph, 8.05 -> 5.0; 800 m -> 0.50 mi.
    await expect(page.locator('.sp-detail').first())
      .toHaveText('15.0 vs 5.0 mph over 0.50 mi, 12+ passes each way');
  });

  test('the same bridge appears once per direction, never as one row', async ({ page }) => {
    await gotoMap(page);
    await page.click('#speed-toggle');
    const rows = page.locator('.sp-row').filter({ hasText: 'Crest Bridge' });
    await expect(rows).toHaveCount(2);
    // Opposite directions: the crest flip, not a duplicate of one stretch.
    await expect(rows.nth(0).locator('.sp-dir')).toHaveText('E');
    await expect(rows.nth(1).locator('.sp-dir')).toHaveText('W');
  });

  test('clicking a row flies to the corridor and marks it', async ({ page }) => {
    await gotoMap(page);
    await page.click('#speed-toggle');
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

  test('collapsing clears the corridor marker', async ({ page }) => {
    await gotoMap(page);
    await page.click('#speed-toggle');
    await page.locator('.sp-row').first().click();
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    await page.click('#speed-toggle');
    expect(await page.evaluate(() => speedMarker !== null)).toBe(false);
  });

  test('section stays hidden when no corridor qualifies', async ({ page }) => {
    await gotoMap(page, buildFixture({ speed: null }));
    await expect(page.locator('#stat-speed')).toBeHidden();
  });

  test('section stays hidden when the corridor list is empty', async ({ page }) => {
    await gotoMap(page, buildFixture({
      speed: { corridors: [], measured: 0, split_n: 3, min_m: 250.0 },
    }));
    await expect(page.locator('#stat-speed')).toBeHidden();
  });
});
