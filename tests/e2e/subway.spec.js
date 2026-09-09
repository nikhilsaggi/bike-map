import { test, expect, gotoMap, edgePoint } from './helpers.js';
import { buildFixture, SUBWAY_BLOCK } from './fixture.js';

/** Click the station at (lat, lng) through Leaflet's own projection. */
async function clickStation(page, lat, lng) {
  const pt = await edgePoint(page, lat, lng);
  await page.mouse.click(pt.x, pt.y);
}

/** The stroke colour of every street currently on the map. */
const strokes = (page) =>
  page.evaluate(() => {
    const out = [];
    geoLayer.eachLayer((l) => {
      if (map.hasLayer(l)) out.push(l.options.color);
    });
    return out;
  });

const CENTER_STN = { lat: 40.7375, lng: -73.96 };
const SOUTH_STN = { lat: 40.7325, lng: -73.96 };

test.describe('dream subway layer', () => {
  test('the row stays hidden until the payload carries the block', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#sw-toggle')).toHaveClass(/hidden/);
  });

  test('the row appears with the block and starts off', async ({ page }) => {
    await gotoMap(page, buildFixture({ subway: SUBWAY_BLOCK }));
    await expect(page.locator('#sw-toggle')).not.toHaveClass(/hidden/);
    await expect(page.locator('#sw-check')).not.toBeChecked();
    // Off means off: nothing of the layer is on the map yet.
    expect(await page.evaluate(() => swLayer !== null && map.hasLayer(swLayer))).toBe(false);
  });

  test('switching it on draws a casing and a chord per line, plus every station',
    async ({ page }) => {
      await gotoMap(page, buildFixture({ subway: SUBWAY_BLOCK }));
      await page.locator('#sw-check').check();
      // 2 casings + 2 chords + 4 station markers
      expect(await page.evaluate(() => swLayer.getLayers().length)).toBe(8);
    });

  test('the network drops to outline underneath it, and comes back when it goes',
    async ({ page }) => {
      await gotoMap(page, buildFixture({ subway: SUBWAY_BLOCK }));
      const lit = await strokes(page);
      expect(lit.every((c) => c === '#555')).toBe(false);

      await page.locator('#sw-check').check();
      await expect.poll(async () => (await strokes(page)).every((c) => c === '#555')).toBe(true);

      await page.locator('#sw-check').uncheck();
      await expect.poll(async () => strokes(page)).toEqual(lit);
    });

  test('a station opens the inspector with its lines and what it reaches',
    async ({ page }) => {
      await gotoMap(page, buildFixture({ subway: SUBWAY_BLOCK }));
      await page.locator('#sw-check').check();
      await clickStation(page, CENTER_STN.lat, CENTER_STN.lng);

      await expect(page.locator('#inspector')).toBeVisible();
      await expect(page.locator('#inspector-title')).toHaveText('Center Station');
      await expect(page.locator('#inspector')).toHaveClass(/kind-subway/);
      await expect(page.locator('.sw-sub').first()).toHaveText('40 ride ends here');
      await expect(page.locator('.sw-row')).toHaveCount(2);
      await expect(page.locator('.sw-row .sw-name').first()).toHaveText('Cross Line');
      // Both lines pass through, so every other station is one ride away.
      await expect(page.locator('.sw-reach')).toHaveText('Direct to 3 of 3 other stations');
    });

  test('a single-line station reaches only its own line', async ({ page }) => {
    await gotoMap(page, buildFixture({ subway: SUBWAY_BLOCK }));
    await page.locator('#sw-check').check();
    await clickStation(page, SOUTH_STN.lat, SOUTH_STN.lng);
    await expect(page.locator('#inspector-title')).toHaveText('South End');
    await expect(page.locator('.sw-row')).toHaveCount(1);
    await expect(page.locator('.sw-reach')).toHaveText('Direct to 1 of 3 other stations');
  });

  test('a panel row picks its line out of the network, and picks it back in',
    async ({ page }) => {
      await gotoMap(page, buildFixture({ subway: SUBWAY_BLOCK }));
      await page.locator('#sw-check').check();
      await clickStation(page, CENTER_STN.lat, CENTER_STN.lng);

      const opacities = () => page.evaluate(() => swChords.map(([, c]) => c.options.opacity));
      expect(await opacities()).toEqual([0.9, 0.9]);

      await page.locator('.sw-row').first().click();
      const [one, two] = await opacities();
      expect(one).toBeGreaterThan(two);
      await expect(page.locator('.sw-row').first()).toHaveClass(/on/);

      // Toggling, so the reader is never stranded in a picked-out network.
      await page.locator('.sw-row').first().click();
      expect(await opacities()).toEqual([0.9, 0.9]);
    });

  test('switching the layer off closes its panel but leaves another kind alone',
    async ({ page }) => {
      await gotoMap(page, buildFixture({ subway: SUBWAY_BLOCK }));
      await page.locator('#sw-check').check();
      await clickStation(page, CENTER_STN.lat, CENTER_STN.lng);
      await expect(page.locator('#inspector')).toBeVisible();

      await page.locator('#sw-check').uncheck();
      await expect(page.locator('#inspector')).toBeHidden();
    });
});
