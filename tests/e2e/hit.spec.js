import { test, expect, gotoMap, edgePoint } from './helpers.js';
import { EDGES } from './fixture.js';

// A drawn line answers a click past its own stroke by --hit-slop: 6px for a
// mouse, 16px for a coarse pointer. The fixture's streets are ~34.6px apart
// at the initial zoom; Center Street (4 passes) is 5.5px wide, North Street
// (2 passes) 4.33px, so their reaches are 2.75 + slop and 2.17 + slop.

const title = (page) => page.locator('#inspector-title');

test.describe('mouse hit slop', () => {
  test('a click a few pixels off a street opens it', async ({ page }) => {
    await gotoMap(page);
    const pt = await edgePoint(page, EDGES.center.lat);
    await page.mouse.click(pt.x, pt.y - 7);  // 7 < 2.75 + 6
    await expect(title(page)).toHaveText('Center Street');
  });

  test('a click well clear of every street opens nothing', async ({ page }) => {
    await gotoMap(page);
    const pt = await edgePoint(page, EDGES.center.lat);
    await page.mouse.click(pt.x, pt.y - 12);  // 12 > 2.75 + 6, 22.6 > 2.17 + 6
    await expect(page.locator('#inspector')).toBeHidden();
  });
});

test.describe('touch hit slop', () => {
  test.use({ hasTouch: true });

  test('a tap is a coarse pointer and gets the wide slop', async ({ page }) => {
    await gotoMap(page);
    expect(await page.evaluate(() => renderer.options.tolerance)).toBe(16);
  });

  test('a tap well off a street still opens it', async ({ page }) => {
    await gotoMap(page);
    const pt = await edgePoint(page, EDGES.center.lat);
    await page.touchscreen.tap(pt.x, pt.y - 14);  // 14 < 2.75 + 16
    await expect(title(page)).toHaveText('Center Street');
  });

  // Both streets reach this point, and Center Street is drawn after North
  // Street, so Leaflet's own rule (last drawn wins) would open Center. The
  // nearer stroke is North's: measured from each line's painted edge, using
  // the page's own rounded projection, the tap sits 1px inside Center's reach.
  test('where two streets both reach, the nearer one wins', async ({ page }) => {
    await gotoMap(page);
    const y = await page.evaluate(([c, n]) => {
      const yc = map.latLngToContainerPoint([c, -73.96]).y;
      const yn = map.latLngToContainerPoint([n, -73.96]).y;
      const tap = yc - (2.75 + 16 - 1);             // 17.75 from Center's line
      const dc = (yc - tap) - 2.75;                 // 15.0 to Center's stroke
      const dn = (tap - yn) - 4.33 / 2;             // ~14.7 to North's stroke
      return dn < dc ? tap : null;
    }, [EDGES.center.lat, EDGES.north.lat]);
    expect(y).not.toBeNull();
    const pt = await edgePoint(page, EDGES.center.lat);
    await page.touchscreen.tap(pt.x, y);
    await expect(title(page)).toHaveText('North Street');
  });
});
