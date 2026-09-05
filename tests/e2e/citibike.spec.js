import { test, expect, gotoMap, openSection, chip } from './helpers.js';
import { buildFixture } from './fixture.js';

const SECTION = 'stat-citibike';

test.describe('Citi Bike dock trips', () => {
  test('leads with the docks that only work one way', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const rows = page.locator(`#${SECTION} .cb-flow-row`);

    // Ranked by the pipeline, both ends of the range, no dock twice.
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
    await expect(section).toContainText('0 of 3 docks were used once');
    await expect(section).toContainText('drawn on the map or counted toward coverage');
    await expect(section).toContainText('1 unlocks re-docked');
    await expect(section).toContainText('2 of 8 bikes');
    await expect(section).toContainText('at least 3 ebike trips');
    await expect(section).toContainText('$5.00 paid of $12.00 charged');
  });

  test('puts nothing on the map and adds no layer control', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    // Dock trips have no trace, so the drawn layer is still exactly the three
    // features the payload shipped, and the legend gained no toggle.
    expect(await page.evaluate(() => {
      let n = 0;
      geoLayer.eachLayer(() => n++);
      return n;
    })).toBe(3);
    await expect(page.locator('#legend input[type="checkbox"]')).toHaveCount(0);
    expect(await page.evaluate(() => typeof citibikeLayer)).toBe('undefined');
  });

  test('section and chip stay hidden when the payload has no citibike block', async ({ page }) => {
    await gotoMap(page, buildFixture({ citibike: null }));
    await expect(page.locator(`#${SECTION}`)).toBeHidden();
    await expect(chip(page, SECTION)).toBeHidden();
  });
});
