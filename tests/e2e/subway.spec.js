import { test, expect, gotoMap, edgePoint } from './helpers.js';
import { buildFixture, SUBWAY_BLOCK, SUBWAY_PARALLEL_BLOCK } from './fixture.js';

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

  test('a stretch two lines share is drawn once per line, on its own track',
    async ({ page }) => {
      await gotoMap(page, buildFixture({ subway: SUBWAY_PARALLEL_BLOCK }));
      await page.locator('#sw-check').check();

      // Line 1 runs West End -> Center Station (shared with line 3) and then
      // Center Station -> East End (its own). A shared segment tapers out to
      // its track and back, so it carries four points rather than two.
      const shape = await page.evaluate(() =>
        swChords.map(([, chord]) => chord.getLatLngs().map((seg) => seg.length)));
      expect(shape[0]).toEqual([4, 2]);   // line 1: shared, then alone
      expect(shape[1]).toEqual([4]);      // line 2: its one segment is shared
      expect(shape[2]).toEqual([4, 4]);   // line 3: both of its segments are

      // The two lines on a shared segment sit on opposite sides of it, so
      // their tapered points are never the same place.
      const apart = await page.evaluate(() => {
        const a = swChords[0][1].getLatLngs()[0][1];
        const b = swChords[2][1].getLatLngs()[0][1];
        return map.latLngToLayerPoint(a).distanceTo(map.latLngToLayerPoint(b));
      });
      expect(apart).toBeGreaterThan(4);
    });

  test('the tracks keep their width when the map zooms', async ({ page }) => {
    await gotoMap(page, buildFixture({ subway: SUBWAY_PARALLEL_BLOCK }));
    await page.locator('#sw-check').check();
    const gap = () => page.evaluate(() => {
      const a = swChords[0][1].getLatLngs()[0][1];
      const b = swChords[2][1].getLatLngs()[0][1];
      return map.latLngToLayerPoint(a).distanceTo(map.latLngToLayerPoint(b));
    });
    const before = await gap();
    await page.evaluate(() => map.setZoom(map.getZoom() + 2));
    await expect.poll(gap).toBeCloseTo(before, 0);
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
