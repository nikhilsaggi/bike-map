import { test, expect, gotoMap, openSection, chip } from './helpers.js';
import { buildFixture, CITIBIKE_BLOCK } from './fixture.js';

const SECTION = 'stat-citibike';

test.describe('Citi Bike dock trips', () => {
  test('leads with the docks that only work one way', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const rows = page.locator(`#${SECTION} .cb-flow-row`);

    // Three docks, so the top-3 and bottom-2 slices overlap and the list is
    // deduped to one row each -- most one-way first.
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0).locator('.cb-flow-name')).toHaveText('Terminal Dock & 42 St');
    await expect(rows.nth(0).locator('.cb-flow-val')).toHaveText('+4');
    await expect(rows.nth(0).locator('.cb-flow-sub')).toHaveText('4/0');
    await expect(rows.nth(1).locator('.cb-flow-val')).toHaveText('0');
    await expect(rows.nth(2).locator('.cb-flow-name')).toHaveText('Park Dock & 5 Ave');
    await expect(rows.nth(2).locator('.cb-flow-val')).toHaveText('-4');
  });

  test('reports the totals and says what the data cannot claim', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);

    await expect(section).toContainText('11');       // trips
    await expect(section).toContainText('2 h');      // hours, rounded
    await expect(section).toContainText('9 min');    // median trip
    // The number that says this supplements the GPS map rather than replacing it.
    await expect(section).toContainText('4 of 6');
    await expect(section).toContainText('drawn as a route or counted toward coverage');
    await expect(section).toContainText('1 unlocks re-docked');
    await expect(section).toContainText('2 of 8 bikes');
    await expect(section).toContainText('at least 3 ebike trips');
    await expect(section).toContainText('$5.00 paid of $12.00 charged');
  });

  test('the dock layer is off until asked for, and never joins the edges', async ({ page }) => {
    await gotoMap(page);
    const toggle = page.locator('#cb-check');
    await expect(toggle).toBeVisible();
    await expect(toggle).not.toBeChecked();

    const onMap = () => page.evaluate(() => map.hasLayer(citibikeLayer));
    expect(await onMap()).toBe(false);

    await toggle.check();
    expect(await onMap()).toBe(true);
    // Three docks and three desire lines, in their own group -- the drawn
    // edges are still exactly the three features the payload shipped.
    expect(await page.evaluate(() => citibikeLayer.getLayers().length)).toBe(6);
    expect(await page.evaluate(() => {
      let n = 0;
      geoLayer.eachLayer(() => n++);
      return n;
    })).toBe(3);

    await toggle.uncheck();
    expect(await onMap()).toBe(false);
  });

  test('the dock layer survives a date-filter restack', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#cb-check').check();
    // applyFilter calls bringToFront on every visible edge; the docks share a
    // canvas with them and must be lifted back above.
    await page.evaluate(() => applyFilter());
    expect(await page.evaluate(() => map.hasLayer(citibikeLayer))).toBe(true);
  });

  test('section and chip stay hidden when the payload has no citibike block', async ({ page }) => {
    await gotoMap(page, buildFixture({ citibike: null }));
    await expect(page.locator(`#${SECTION}`)).toBeHidden();
    await expect(chip(page, SECTION)).toBeHidden();
    await expect(page.locator('#cb-toggle')).toBeHidden();
  });

  test('a block with no docks draws no layer', async ({ page }) => {
    await gotoMap(page, buildFixture({
      citibike: { ...CITIBIKE_BLOCK, stations: [], pairs: [] },
    }));
    // The counts are still worth showing; the map layer has nothing to draw.
    await expect(chip(page, SECTION)).toBeVisible();
    await expect(page.locator('#cb-toggle')).toBeHidden();
  });
});
