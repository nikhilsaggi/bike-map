import { test, expect, gotoMap, openSection } from './helpers.js';
import { buildFixture } from './fixture.js';

/** Turn the neighbourhood layer on and wait for the polygons to land. */
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

test.describe('Neighbourhood layer', () => {
  test('is off until asked for, then draws one polygon per area', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#nb-check')).not.toBeChecked();
    expect(await page.evaluate(() => map.hasLayer(nbLayer))).toBe(false);
    await expect(page.locator('#nb-note')).toHaveText(/2 tabulation areas/);

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

  test('a popup gives the area its own coverage and passes', async ({ page }) => {
    await gotoMap(page);
    await showAreas(page);
    await page.evaluate(() => nbShapes[0].openPopup());
    const popup = page.locator('.nb-popup');
    await expect(popup.locator('.nb-head')).toHaveText('Downtown Flats');
    await expect(popup.locator('.nb-sub')).toHaveText('Brooklyn');
    // 5,000 m of 20,000 m in miles, and the share of its own network.
    await expect(popup).toContainText('3.1 of 12.4 mi');
    await expect(popup).toContainText('25%');
    // Passes counts only the features tagged to this area: the centre street
    // (4 passes) and the south one (2), never the north street's 2.
    await expect(popup.locator('.nb-row').nth(2)).toContainText('6');
  });

  test('a popup counts passes within the date range only', async ({ page }) => {
    await gotoMap(page);
    await showAreas(page);
    // To 2023-06-15: the centre street keeps rides 0 and 1, the south street's
    // round trip is in 2024 and drops out entirely.
    await setHi(page, 1);
    await page.evaluate(() => nbShapes[0].openPopup());
    await expect(page.locator('.nb-popup .nb-row').nth(2)).toContainText('2');
    await expect(page.locator('.nb-popup')).toContainText('Ridden by 2023-06-15');
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
