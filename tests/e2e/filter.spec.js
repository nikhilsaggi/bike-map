import { test, expect, gotoMap, hoverEdge, clickEdge } from './helpers.js';
import { EDGES } from './fixture.js';

test.describe('date-range filter', () => {
  test('slider handles show the full date range initially', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#filter-row')).toBeVisible();
    await expect(page.locator('#date-lo')).toHaveText('2023-04-01');
    await expect(page.locator('#date-hi')).toHaveText('2024-07-04');
  });

  test('narrowing the upper bound hides filtered-out streets and renormalizes colors', async ({ page }) => {
    await gotoMap(page);
    // Pull the hi handle down to 2023-06-15 (index 1 of 4 dates).
    await page.locator('#range-hi').focus();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#date-hi')).toHaveText('2023-06-15');

    // Legend renormalizes to the filtered max (center street: rides 0,1).
    await expect(page.locator('#legend-max')).toHaveText('2');

    // Center street now reports only the in-range rides.
    const tooltip = await hoverEdge(page, EDGES.center.lat);
    await expect(tooltip).toHaveText('2 rides');

    // The 2024-only street is removed from the map entirely: no tooltip.
    await hoverEdge(page, EDGES.south.lat);
    await expect(page.locator('.leaflet-tooltip')).toHaveCount(0);

    // Popups list only in-range rides.
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.ride-popup');
    await expect(popup).toContainText('2 rides');
    await expect(popup.locator('.ride-row')).toHaveCount(2);
  });

  test('raising the lower bound filters from the other end', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#range-lo').focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('#date-lo')).toHaveText('2023-06-15');
    await expect(page.locator('#legend-max')).toHaveText('3');

    // North street (rides 0,1) keeps only ride 1.
    const tooltip = await hoverEdge(page, EDGES.north.lat);
    await expect(tooltip).toHaveText('1 ride');
  });
});

test.describe('time-lapse playback', () => {
  test('play sweeps the upper bound forward and can be paused', async ({ page }) => {
    await page.clock.install();
    await gotoMap(page);
    const btn = page.locator('#play-btn');

    await btn.click();
    await expect(btn).toHaveText('❚❚'); // pause glyph
    // Color scale freezes at the full-history max during playback.
    await expect(page.locator('#legend-max')).toHaveText('4');

    // 13s of a 25s sweep across 4 dates -> upper bound at index 2.
    await page.clock.runFor(13000);
    await expect(page.locator('#date-hi')).toHaveText('2024-05-01');
    await expect(page.locator('#date-lo')).toHaveText('2023-04-01');

    await btn.click(); // pause
    await expect(btn).toHaveText('▶');
    // Stopping renormalizes to wherever playback stopped (max count now 3).
    await expect(page.locator('#legend-max')).toHaveText('3');
    await expect(page.locator('#date-hi')).toHaveText('2024-05-01');
  });

  test('playback runs to the end and restores the full range', async ({ page }) => {
    await page.clock.install();
    await gotoMap(page);
    const btn = page.locator('#play-btn');

    await btn.click();
    await page.clock.runFor(26000);
    await expect(btn).toHaveText('▶');
    await expect(page.locator('#date-lo')).toHaveText('2023-04-01');
    await expect(page.locator('#date-hi')).toHaveText('2024-07-04');
    await expect(page.locator('#legend-max')).toHaveText('4');
  });
});
