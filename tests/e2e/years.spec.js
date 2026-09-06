import { test, expect, gotoMap, openSection, hoverEdge } from './helpers.js';
import { EDGES } from './fixture.js';

// The per-year recap used to be a modal of its own. It is now the detail a
// year row expands into, so everything it showed is asserted here.
test.describe('per-year table', () => {
  test('labels its columns and aligns each year against them', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');

    const head = page.locator('#stat-years .year-head');
    await expect(head).toContainText('Rides');
    await expect(head).toContainText('Miles');
    await expect(head).toContainText('New mi');

    const rows = page.locator('#stat-years .year-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0).locator('.y-year')).toHaveText('2023');
    await expect(rows.nth(0).locator('.y-n')).toHaveText('2');
    await expect(rows.nth(0).locator('.y-mi')).toHaveText('22'); // 35 km
    await expect(rows.nth(0).locator('.y-new')).toHaveText('+19'); // 30 km
    await expect(rows.nth(1).locator('.y-year')).toHaveText('2024');
    await expect(rows.nth(1).locator('.y-mi')).toHaveText('33'); // 52.5 km
    await expect(rows.nth(1).locator('.y-new')).toHaveText('+10'); // 15.5 km

    // Every value column shares one grid template, so the columns line up.
    const lefts = await rows.evaluateAll((els) =>
      els.map((el) => [...el.querySelectorAll('.y-n, .y-mi, .y-new')]
        .map((s) => Math.round(s.getBoundingClientRect().right))));
    expect(lefts[0]).toEqual(lefts[1]);
  });

  test('a year row expands into its recap', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    const row = page.locator('#stat-years .year-row').nth(1); // 2024
    const detail = page.locator('#stat-years .year-detail[data-year="2024"]');

    await expect(detail).toBeHidden();
    await expect(row).toHaveAttribute('aria-expanded', 'false');

    await row.click();
    await expect(detail).toBeVisible();
    await expect(row).toHaveAttribute('aria-expanded', 'true');
    // +0% rides (2 in both years), +50% mi (52.5 vs 35 km).
    await expect(detail.locator('.yd-vs')).toContainText('vs 2023');
    await expect(detail.locator('.yd-vs')).toContainText('+0% rides');
    await expect(detail.locator('.yd-vs')).toContainText('+50% mi');

    const rows = detail.locator('.yd-row');
    await expect(rows.nth(0)).toContainText('Longest ride');
    await expect(rows.nth(0)).toContainText('2024-07-04');
    await expect(rows.nth(0)).toContainText('25 mi'); // 40 km
    await expect(rows.nth(1)).toContainText('Biggest month');
    await expect(rows.nth(1)).toContainText('May');
    await expect(rows.nth(2)).toContainText('Favorite time');
    await expect(rows.nth(2)).toContainText('Wednesdays');
    await expect(rows.nth(2)).toContainText('9am');

    await row.click();
    await expect(detail).toBeHidden();
  });

  test('the earliest year has no year-over-year comparison', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    const detail = page.locator('#stat-years .year-detail[data-year="2023"]');
    await page.locator('#stat-years .year-row').nth(0).click();

    await expect(detail.locator('.yd-vs')).toHaveCount(0); // no 2022 to compare
    const rows = detail.locator('.yd-row');
    await expect(rows.nth(0)).toContainText('2023-06-15');
    await expect(rows.nth(0)).toContainText('16 mi'); // 25 km
    await expect(rows.nth(1)).toContainText('April');
    await expect(rows.nth(2)).toContainText('Thursdays');
    await expect(rows.nth(2)).toContainText('8am');
  });

  test('several years can be expanded at once', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    await page.locator('#stat-years .year-row').nth(0).click();
    await page.locator('#stat-years .year-row').nth(1).click();
    await expect(page.locator('#stat-years .year-detail[data-year="2023"]')).toBeVisible();
    await expect(page.locator('#stat-years .year-detail[data-year="2024"]')).toBeVisible();
  });

  test('a row expands from the keyboard', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    await page.locator('#stat-years .year-row').nth(1).focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#stat-years .year-detail[data-year="2024"]')).toBeVisible();
  });

  test('the longest-ride link toggles the ride back off', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    await page.locator('#stat-years .year-row').nth(1).click();
    const link = page.locator('.yd-link');

    await link.click();
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    await expect(link).toHaveClass(/\bon\b/);
    await expect(link).toHaveAttribute('title', 'Hide this ride');

    await link.click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
    await expect(link).not.toHaveClass(/\bon\b/);
    await expect(link).toHaveAttribute('title', "Show this ride's route");
  });

  test('leaving ride view any other way clears the link too', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    await page.locator('#stat-years .year-row').nth(1).click();
    const link = page.locator('.yd-link');

    await link.click();
    await expect(link).toHaveClass(/\bon\b/);
    await page.keyboard.press('Escape');
    await expect(page.locator('#ride-view-bar')).toBeHidden();
    await expect(link).not.toHaveClass(/\bon\b/);

    await link.click();
    await expect(link).toHaveClass(/\bon\b/);
    await page.locator('#ride-view-exit').click();
    await expect(link).not.toHaveClass(/\bon\b/);
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

    // 2024's longest ride (2024-07-04) is out of filter range, but viewing it
    // restores its streets on the map.
    await openSection(page, 'stat-years');
    await page.locator('#stat-years .year-row').nth(1).click();
    await page.locator('.yd-link').click();
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    await expect(page.locator('#ride-view-label')).toHaveText('2024-07-04 2:45pm · 2 Citibike trips');

    const tooltip = await hoverEdge(page, EDGES.south.lat);
    await expect(tooltip).toBeVisible();
  });
});
