import { test, expect, gotoMap, openSection, chip } from './helpers.js';
import { buildFixture } from './fixture.js';

/** Turn the neighborhood layer on and wait for the polygons to land. */
async function showAreas(page) {
  await page.locator('#nb-check').check();
  await expect.poll(() => page.evaluate(() => map.hasLayer(nbLayer))).toBe(true);
}

/**
 * Assert one area's fill opacity, which is what carries its coverage.
 * `ramp` is the square root of its covered share, which is what the fill
 * runs on; the fixture's shares are perfect squares so it stays exact.
 *
 * Polled: the date handles restyle on the next animation frame, so a bare
 * read straight after moving one can land before the redraw.
 */
function expectFill(page, i, ramp) {
  return expect
    .poll(() => page.evaluate((i) => nbShapes[i].options.fillOpacity, i))
    .toBeCloseTo(0.02 + 0.45 * ramp, 5);
}

/** Move the upper date handle, the way the filter tests do. */
async function setHi(page, value) {
  await page.evaluate((v) => {
    const hi = document.getElementById('range-hi');
    hi.value = String(v);
    hi.dispatchEvent(new Event('input', { bubbles: true }));
  }, value);
}

test.describe('Neighborhood layer', () => {
  test('is off until asked for, then draws one polygon per area', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#nb-check')).not.toBeChecked();
    expect(await page.evaluate(() => map.hasLayer(nbLayer))).toBe(false);
    // What the fill means lives on the control, not in a caption under it.
    await expect(page.locator('#nb-toggle')).toHaveAttribute('title', /2 NYC tabulation areas/);

    await showAreas(page);
    expect(await page.evaluate(() => nbLayer.getLayers().length)).toBe(2);
  });

  test('fills each area by the share of its streets ridden', async ({ page }) => {
    await gotoMap(page);
    await showAreas(page);
    // Downtown 5,000 of 20,000 m = 25%, Uptown 1,600 of 10,000 = 16%; fill is
    // 0.02 + 0.45 * sqrt(share).
    await expectFill(page, 0, 0.5);
    await expectFill(page, 1, 0.4);
  });

  test('fills in as the date slider moves, like the edges and the docks', async ({ page }) => {
    await gotoMap(page);
    await showAreas(page);

    // Back to the first ride day: Downtown has its first 1,250 m (6.25%, so
    // 0.25 of the ramp), Uptown has gained nothing yet.
    await setHi(page, 0);
    await expectFill(page, 0, 0.25);
    await expectFill(page, 1, 0);

    // Uptown's 1,600 m land on the second day.
    await setHi(page, 1);
    await expectFill(page, 1, 0.4);
    // Downtown's remaining 3,750 m land on the third.
    await setHi(page, 2);
    await expectFill(page, 0, 0.5);
  });

  test('a popup gives the area its own coverage and rides', async ({ page }) => {
    await gotoMap(page);
    await showAreas(page);
    await page.evaluate(() => nbShapes[0].openPopup());
    const popup = page.locator('.nb-popup');
    await expect(popup.locator('.nb-head')).toHaveText('Downtown Flats');
    await expect(popup.locator('.nb-sub')).toHaveText('Brooklyn');
    // 5,000 m of 20,000 m in miles, and the share of its own network.
    await expect(popup).toContainText('3.1 of 12.4 mi');
    await expect(popup).toContainText('25%');
    // Rides, not passes: the centre street carries rides 0-3 and the south one
    // ride 3 twice, so four distinct rides touched this area -- never the
    // north street's, which belongs to Uptown. Summing passes would say 6.
    await expect(popup.locator('.nb-row').nth(2)).toContainText('Rides through');
    await expect(popup.locator('.nb-row').nth(2)).toContainText('4');
    // 8,000 measured metres ridden on Downtown's 5,000 m of street: the row
    // is distance, so it is larger than the "3.1 of 12.4 mi" above it.
    await expect(popup.locator('.nb-row').nth(3)).toContainText('Distance here');
    await expect(popup.locator('.nb-row').nth(3)).toContainText('5.0 mi');
    // 1,800 measured seconds on those streets, all-time.
    await expect(popup.locator('.nb-row').nth(4)).toContainText('Time here');
    await expect(popup.locator('.nb-row').nth(4)).toContainText('30 min');
  });

  test('the stats section rolls the areas up by borough', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-places');
    const cells = page.locator('#stat-places .pl-grid > span');
    // Header is three cells, then one row of three per borough, Brooklyn
    // first because it holds the most ridden metres.
    await expect(cells.nth(3)).toHaveText('Brooklyn');
    await expect(cells.nth(4)).toHaveText('25.0%');   // 5,000 of 20,000 m
    await expect(cells.nth(5)).toHaveText('30 min');  // 1,800 s
    await expect(cells.nth(6)).toHaveText('Manhattan');
    await expect(cells.nth(7)).toHaveText('16.0%');   // 1,600 of 10,000 m
    await expect(cells.nth(8)).toHaveText('1.5 h');   // 5,400 s
  });

  test('a row in the section puts its neighborhood on the map', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-places');
    await expect(page.locator('#stat-places .pl-name').first()).toHaveText('Uptown Heights');
    // The layer is off; clicking a row turns it on and opens that popup.
    await expect(page.locator('#nb-check')).not.toBeChecked();
    await page.locator('#stat-places .pl-row').first().click();
    await expect(page.locator('#nb-check')).toBeChecked();
    await expect(page.locator('.nb-popup .nb-head')).toHaveText('Uptown Heights');
  });

  test('each tab ranks the list by the number in its own column', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-places');
    const names = page.locator('#pl-list .pl-name');
    const vals = page.locator('#pl-list .v');

    // Ridden, the default: distance ridden with every pass counted, so it
    // reads dist_m -- 16,000 m against 8,000 -- and not the 1,600 m and
    // 5,000 m of street those passes were made on, which rank the other way.
    await expect(names).toHaveText(['Uptown Heights', 'Downtown Flats']);
    await expect(vals).toHaveText(['9.9 mi', '5.0 mi']);

    // Time keeps that order -- Uptown holds 5,400 s against Downtown's 1,800
    // -- because both tabs measure the riding rather than the network.
    await page.locator('#pl-tabs .seg-btn', { hasText: 'Time' }).click();
    await expect(names).toHaveText(['Uptown Heights', 'Downtown Flats']);
    await expect(vals).toHaveText(['1.5 h', '30 min']);

    // Explored is the one that asks about the network, and it reverses them:
    // 5,000 of 20,000 against 1,600 of 10,000, each carrying the network it
    // is a share of.
    await page.locator('#pl-tabs .seg-btn', { hasText: 'Explored' }).click();
    await expect(names).toHaveText(['Downtown Flats', 'Uptown Heights']);
    await expect(vals.nth(0)).toHaveText('25% of 12.4 mi');
    await expect(vals.nth(1)).toHaveText('16% of 6.2 mi');
  });

  test('a row still opens its own polygon after a re-rank', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-places');
    // Rows carry their index into the areas array, not their place in the
    // list. Explored and Ridden rank the two areas in opposite orders, so
    // after that round trip the second row is area 0 -- a list that clicked
    // by position would open the other polygon.
    await page.locator('#pl-tabs .seg-btn', { hasText: 'Explored' }).click();
    await page.locator('#pl-tabs .seg-btn', { hasText: 'Ridden' }).click();
    await page.locator('#pl-list .pl-row').nth(1).click();
    await expect(page.locator('.nb-popup .nb-head')).toHaveText('Downtown Flats');
  });

  test('no block means no section', async ({ page }) => {
    await gotoMap(page, buildFixture({ neighborhoods: null }));
    await expect(chip(page, 'stat-places')).toBeHidden();
  });

  test('a popup counts rides within the date range only', async ({ page }) => {
    await gotoMap(page);
    await showAreas(page);
    // To 2023-06-15: the centre street keeps rides 0 and 1, the south street's
    // round trip is in 2024 and drops out entirely.
    await setHi(page, 1);
    await page.evaluate(() => nbShapes[0].openPopup());
    await expect(page.locator('.nb-popup .nb-row').nth(2)).toContainText('2');
    await expect(page.locator('.nb-popup .nb-row').nth(2)).toContainText('Rides through');
    // Coverage is still a running total to the upper date, so it keeps
    // Downtown's first 1,250 m rather than following the range at both ends.
    await expect(page.locator('.nb-popup .nb-row').nth(0)).toContainText('0.8 of 12.4 mi');
  });

  test('the coverage tile reports NYC, not the whole graph', async ({ page }) => {
    await gotoMap(page);
    // The block's own totals: 6,600 of 30,000 m = 22.0%, against the 12.3%
    // the graph-wide coverage figure gives for a denominator half of which is
    // not in the city.
    await expect(page.locator('#stat-coverage')).toHaveText('22.0%');
    await openSection(page, 'stat-streets');
    await expect(page.locator('#streets-totals')).toContainText('Of rideable NYC');
    await expect(page.locator('#streets-totals')).toContainText('12.3%');
  });

  test('without the block there is no layer and coverage falls back', async ({ page }) => {
    await gotoMap(page, buildFixture({ neighborhoods: null }));
    await expect(page.locator('#nb-toggle')).toHaveClass(/hidden/);
    expect(await page.evaluate(() => nbLayer)).toBe(null);
    await expect(page.locator('#stat-coverage')).toHaveText('12.3%');
  });
});
