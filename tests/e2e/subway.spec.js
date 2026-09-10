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

/**
 * How far apart lines 1 and 3 run along the stretch they share, in pixels.
 * Measured at the midpoint of West End -> Center Station, on whichever drawn
 * point of each line comes nearest it.
 */
const trackGap = (page) =>
  page.evaluate(() => {
    const target = map.latLngToLayerPoint(L.latLng(40.7375, -73.9675));
    const nearest = (chord) =>
      chord
        .getLatLngs()
        .map((ll) => map.latLngToLayerPoint(ll))
        .reduce((best, p) => (p.distanceTo(target) < best.distanceTo(target) ? p : best));
    return nearest(swChords[0][1]).distanceTo(nearest(swChords[2][1]));
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
      // Lines 1 and 3 both run West End -> Center Station, so along that
      // stretch they sit either side of the centreline rather than on top of
      // each other.
      const gap = await trackGap(page);
      expect(gap).toBeGreaterThan(4);
      expect(gap).toBeLessThan(10);
    });

  test('the tracks keep their width when the map zooms', async ({ page }) => {
    await gotoMap(page, buildFixture({ subway: SUBWAY_PARALLEL_BLOCK }));
    await page.locator('#sw-check').check();
    const before = await trackGap(page);
    await page.evaluate(() => map.setZoom(map.getZoom() + 2));
    await expect.poll(() => trackGap(page)).toBeCloseTo(before, 0);
  });

  test('a line bends through a curve, never a point', async ({ page }) => {
    await gotoMap(page, buildFixture({ subway: SUBWAY_PARALLEL_BLOCK }));
    await page.locator('#sw-check').check();
    // Line 3 turns a right angle at Center Station. Drawn as a corner it
    // would show one 90-degree deflection; drawn as an arc no single step
    // turns far.
    // map.project, not latLngToLayerPoint: the latter rounds to whole pixels,
    // and on samples a couple of pixels apart that quantises every angle to a
    // multiple of 45 degrees whatever the real geometry does.
    const worst = await page.evaluate(() => {
      const z = map.getZoom();
      const pts = swChords[2][1].getLatLngs().map((ll) => map.project(ll, z));
      let out = 0;
      for (let i = 1; i < pts.length - 1; i++) {
        const v1 = [pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y];
        const v2 = [pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y];
        const m1 = Math.hypot(v1[0], v1[1]);
        const m2 = Math.hypot(v2[0], v2[1]);
        if (m1 < 0.01 || m2 < 0.01) continue;
        const cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (m1 * m2);
        out = Math.max(out, (Math.acos(Math.max(-1, Math.min(1, cos))) * 180) / Math.PI);
      }
      return out;
    });
    expect(worst).toBeLessThan(20);
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
