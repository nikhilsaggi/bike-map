import { test, expect, gotoMap, hoverEdge } from './helpers.js';
import { buildFixture, EDGES } from './fixture.js';

const KM_TO_MI = 0.621371;

test.describe('speed layer', () => {
  test('starts in frequency mode with the speed toggle available', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#mode-freq')).toHaveClass(/on/);
    await expect(page.locator('#mode-speed')).not.toHaveClass(/on/);
    await expect(page.locator('#speed-sub')).toBeHidden();
    // Frequency legend still reads in rides.
    await expect(page.locator('#legend-max')).toHaveText('4');
  });

  test('switching to speed relabels the legend in mph', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#mode-speed').click();
    await expect(page.locator('#mode-speed')).toHaveClass(/on/);
    await expect(page.locator('#legend .title')).toHaveText('Average speed');
    // lo 8.0 km/h -> 5 mph, hi 22.0 km/h -> 14 mph
    await expect(page.locator('#legend-min')).toHaveText('5 mph');
    await expect(page.locator('#legend-max')).toHaveText('14 mph');
    await expect(page.locator('#nodata-key')).toBeVisible();
  });

  test('hides the date filter in speed mode and restores it on the way back', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#filter-row')).toBeVisible();
    await page.locator('#mode-speed').click();
    // Speeds are an all-time aggregate, so the slider must not look live.
    await expect(page.locator('#filter-row')).toBeHidden();
    await expect(page.locator('#filter-dates')).toBeHidden();
    await page.locator('#mode-freq').click();
    await expect(page.locator('#filter-row')).toBeVisible();
  });

  test('tooltip shows both directions with sample counts', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#mode-speed').click();
    const tip = await hoverEdge(page, EDGES.center.lat);
    // 20.0 km/h -> 12.4 mph (6 passes); 10.0 km/h -> 6.2 mph (4 passes).
    // The line runs west->east, so forward is E and reverse is W.
    await expect(tip).toContainText(`${(20.0 * KM_TO_MI).toFixed(1)} mph`);
    await expect(tip).toContainText('(6)');
    await expect(tip).toContainText(`${(10.0 * KM_TO_MI).toFixed(1)} mph`);
    await expect(tip).toContainText('(4)');
    await expect(tip).toContainText('E');
    await expect(tip).toContainText('W');
  });

  test('tooltip dashes a direction that has no published speed', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#mode-speed').click();
    const tip = await hoverEdge(page, EDGES.north.lat);
    await expect(tip).toContainText(`${(15.0 * KM_TO_MI).toFixed(1)} mph`);
    await expect(tip).toContainText('—');
  });

  test('a street with no speed data says so', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#mode-speed').click();
    const tip = await hoverEdge(page, EDGES.south.lat);
    await expect(tip).toHaveText('no speed data');
  });

  test('hovering draws a pair of direction lines', async ({ page }) => {
    await gotoMap(page);
    const before = await page.evaluate(() => document.querySelectorAll('path').length);
    await page.locator('#mode-speed').click();
    await hoverEdge(page, EDGES.center.lat);
    // Two offset polylines are added as SVG on top of the canvas base layer.
    const during = await page.evaluate(() => document.querySelectorAll('path').length);
    expect(during).toBeGreaterThan(before);
  });

  test('asymmetry view switches to the diverging legend', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#mode-speed').click();
    await page.locator('#speed-sub input[value="asym"]').check();
    await expect(page.locator('#legend .title')).toHaveText('Direction difference');
    // ASYM_FULL_KMH 10 -> +/-6 mph
    await expect(page.locator('#legend-min')).toHaveText('-6 mph');
    await expect(page.locator('#legend-max')).toHaveText('+6 mph');
    await expect(page.locator('#speed-note')).toContainText('not why');
  });

  test('stats panel reports the median speed', async ({ page }) => {
    await gotoMap(page);
    // med 15.0 km/h -> 9.3 mph
    await expect(page.locator('#stat-speed')).toContainText(
      `${(15.0 * KM_TO_MI).toFixed(1)} mph`,
    );
    await expect(page.locator('#stat-speed')).toContainText('3');
  });

  test('hides the speed toggle entirely when the payload has no speed block', async ({ page }) => {
    const data = buildFixture();
    delete data.properties.speed;
    for (const f of data.features) delete f.properties.sp;
    await gotoMap(page, data);
    await expect(page.locator('#mode-speed')).toBeHidden();
    await expect(page.locator('#stat-speed')).toBeHidden();
  });
});
