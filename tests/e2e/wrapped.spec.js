import { test, expect, gotoMap, hoverEdge } from './helpers.js';
import { EDGES } from './fixture.js';

test.describe('year recap overlay', () => {
  test('opens on the latest year with computed stats and deltas', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#wrapped-btn').click();

    await expect(page.locator('#wrapped-overlay')).toBeVisible();
    await expect(page.locator('#wrapped-year')).toHaveText('2024');
    await expect(page.locator('#wrapped-next')).toBeDisabled();
    await expect(page.locator('#wrapped-prev')).toBeEnabled();

    const rows = page.locator('.wrapped-row');
    await expect(rows).toHaveCount(6);
    await expect(rows.nth(0)).toContainText('Rides');
    await expect(rows.nth(0)).toContainText('2');
    await expect(rows.nth(0)).toContainText('+0%'); // same ride count as 2023
    await expect(rows.nth(1)).toContainText('Distance');
    await expect(rows.nth(1)).toContainText('33 mi'); // 52.5 km
    await expect(rows.nth(1)).toContainText('+50%'); // vs 35 km in 2023
    await expect(rows.nth(2)).toContainText('New streets');
    await expect(rows.nth(2)).toContainText('10 mi'); // 15.5 km
    await expect(rows.nth(3)).toContainText('Longest ride');
    await expect(rows.nth(3)).toContainText('2024-07-04');
    await expect(rows.nth(3)).toContainText('25 mi'); // 40 km
    await expect(rows.nth(4)).toContainText('Biggest month');
    await expect(rows.nth(4)).toContainText('May');
    await expect(rows.nth(5)).toContainText('Favorite time');
    await expect(rows.nth(5)).toContainText('Wednesdays');
    await expect(rows.nth(5)).toContainText('9am');
  });

  test('navigates to the previous year (no deltas without a prior year)', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#wrapped-btn').click();
    await page.locator('#wrapped-prev').click();

    await expect(page.locator('#wrapped-year')).toHaveText('2023');
    await expect(page.locator('#wrapped-prev')).toBeDisabled();
    await expect(page.locator('#wrapped-next')).toBeEnabled();

    const rows = page.locator('.wrapped-row');
    await expect(rows.nth(0)).toContainText('Rides');
    await expect(rows.nth(0)).not.toContainText('%'); // no 2022 to compare against
    await expect(rows.nth(1)).toContainText('22 mi'); // 35 km
    await expect(rows.nth(2)).toContainText('19 mi'); // 30 km of new streets
    await expect(rows.nth(3)).toContainText('2023-06-15');
    await expect(rows.nth(3)).toContainText('16 mi'); // 25 km
    await expect(rows.nth(4)).toContainText('April');
    await expect(rows.nth(5)).toContainText('Thursdays');
    await expect(rows.nth(5)).toContainText('8am');

    await page.locator('#wrapped-next').click();
    await expect(page.locator('#wrapped-year')).toHaveText('2024');
  });

  test('closes via the close button, Escape, and the backdrop', async ({ page }) => {
    await gotoMap(page);
    const overlay = page.locator('#wrapped-overlay');

    await page.locator('#wrapped-btn').click();
    await page.locator('#wrapped-close').click();
    await expect(overlay).toBeHidden();

    await page.locator('#wrapped-btn').click();
    await page.keyboard.press('Escape');
    await expect(overlay).toBeHidden();

    await page.locator('#wrapped-btn').click();
    await overlay.click({ position: { x: 10, y: 10 } }); // outside the card
    await expect(overlay).toBeHidden();
  });

  test('longest-ride link opens ride view even when the date filter excludes it', async ({ page }) => {
    await gotoMap(page);
    // Filter out 2024 entirely: the 2024-only street disappears.
    await page.locator('#range-hi').focus();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#legend-max')).toHaveText('2');
    await hoverEdge(page, EDGES.south.lat);
    await expect(page.locator('.leaflet-tooltip')).toHaveCount(0);

    // The 2024 recap's longest ride (2024-07-04) is out of filter range,
    // but viewing it restores its streets on the map.
    await page.locator('#wrapped-btn').click();
    await page.locator('.wrapped-link').click();
    await expect(page.locator('#wrapped-overlay')).toBeHidden();
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    await expect(page.locator('#ride-view-label')).toHaveText('2024-07-04 14:45');

    const tooltip = await hoverEdge(page, EDGES.south.lat);
    await expect(tooltip).toBeVisible();
  });
});
