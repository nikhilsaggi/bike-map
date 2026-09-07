import { test, expect, gotoMap, hoverEdge, clickEdge } from './helpers.js';
import { buildFixture, EDGES, CITIBIKE_BLOCK, NEIGHBORHOOD_BLOCK } from './fixture.js';

/**
 * How many of the drawn network's streets are currently on the map.
 *
 * Polled, not read once: the slider defers applyFilter to the next animation
 * frame, so the dates on screen are already right while the map still holds
 * the streets the drag took out.
 */
const expectDrawn = (page, n) =>
  expect
    .poll(() =>
      page.evaluate(() => {
        let count = 0;
        geoLayer.eachLayer((l) => {
          if (map.hasLayer(l)) count += 1;
        });
        return count;
      }),
    )
    .toBe(n);

/** The stroke colour of each street that is on the map, in layer order. */
const strokes = (page) =>
  page.evaluate(() => {
    const out = [];
    geoLayer.eachLayer((l) => {
      if (map.hasLayer(l)) out.push(l.options.color);
    });
    return out;
  });

test.describe('layer switcher', () => {
  test('the network starts on and the two optional layers start off', async ({ page }) => {
    await gotoMap(page, buildFixture({
      citibike: CITIBIKE_BLOCK,
      neighborhoods: NEIGHBORHOOD_BLOCK,
    }));
    await expect(page.locator('#rf-check')).toBeChecked();
    await expect(page.locator('#cb-check')).not.toBeChecked();
    await expect(page.locator('#nb-check')).not.toBeChecked();
    await expectDrawn(page, 3);
  });

  test('switching the network off takes every street off the map, and back on restores them',
    async ({ page }) => {
      await gotoMap(page);
      await page.locator('#rf-check').uncheck();
      await expectDrawn(page, 0);
      // Nothing left to hover: the streets are off the map, not just dimmed.
      await hoverEdge(page, EDGES.center.lat);
      await expect(page.locator('.leaflet-tooltip')).toHaveCount(0);

      await page.locator('#rf-check').check();
      await expectDrawn(page, 3);
      const tooltip = await hoverEdge(page, EDGES.center.lat);
      await expect(tooltip).toHaveText('4 passes');
    });

  test('coming back on obeys the date filter rather than redrawing everything',
    async ({ page }) => {
      await gotoMap(page);
      // 2023 only: the 2024-only south street drops out.
      await page.locator('#range-hi').focus();
      await page.keyboard.press('ArrowLeft');
      await page.keyboard.press('ArrowLeft');
      await expect(page.locator('#date-hi')).toHaveText('2023-06-15');
      await expectDrawn(page, 2);

      await page.locator('#rf-check').uncheck();
      await expectDrawn(page, 0);
      await page.locator('#rf-check').check();
      await expectDrawn(page, 2);
    });

  test('the slider still runs while the network is off, and off it stays', async ({ page }) => {
    await gotoMap(page);
    await page.locator('#rf-check').uncheck();
    await page.locator('#range-hi').focus();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#date-hi')).toHaveText('2023-06-15');
    // The legend keeps describing the layer it is the legend for -- and it is
    // renormalized by the same applyFilter, so waiting for it is what proves
    // the frame below ran and left the network off rather than never running.
    await expect(page.locator('#legend-max')).toHaveText('2');
    await expectDrawn(page, 0);
  });

  test("switching off closes a street's panel", async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await expect(page.locator('#inspector')).toBeVisible();
    await page.locator('#rf-check').uncheck();
    await expect(page.locator('#inspector')).toBeHidden();
    expect(await page.evaluate(() => selectedEdge)).toBe(null);
  });

  test("switching off leaves a neighborhood's panel alone", async ({ page }) => {
    await gotoMap(page, buildFixture({ neighborhoods: NEIGHBORHOOD_BLOCK }));
    await page.locator('#nb-check').check();
    await page.locator('#nb-check').focus();
    // Open an area from its row in the stats panel rather than by clicking the
    // map, so the assertion does not depend on where the polygon landed.
    await page.evaluate(() => selectArea(0));
    await expect(page.locator('#inspector')).toBeVisible();
    await page.locator('#rf-check').uncheck();
    await expect(page.locator('#inspector')).toBeVisible();
  });

  test('a ride on screen survives the switch, drawn with nothing behind it', async ({ page }) => {
    await gotoMap(page);
    await page.evaluate(() => viewRide(3));
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    // Ride 3 is on the centre and south streets; the north one is ghosted.
    expect(await strokes(page)).toEqual(['#00e5ff', '#555', '#00e5ff']);

    await page.locator('#rf-check').uncheck();
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    // The ghosts go with the network; the ride itself is the explicit request
    // and stays, the way it stays outside the date filter.
    expect(await strokes(page)).toEqual(['#00e5ff', '#00e5ff']);

    // Leaving ride view with the switch off leaves an empty map.
    await page.keyboard.press('Escape');
    await expect(page.locator('#ride-view-bar')).toBeHidden();
    await expectDrawn(page, 0);
  });

  test('the three toggles sit together in the legend, clear of the stats panel',
    async ({ page }) => {
      await gotoMap(page, buildFixture({
        citibike: CITIBIKE_BLOCK,
        neighborhoods: NEIGHBORHOOD_BLOCK,
      }));
      const rows = page.locator('#layers label:not(.hidden)');
      await expect(rows).toHaveCount(3);
      await expect(rows.nth(0)).toContainText('Pass frequency');
      await expect(rows.nth(1)).toContainText('Citibike docks');
      await expect(rows.nth(2)).toContainText('Neighborhoods');

      // The rails are flex columns, so the group cannot ride over the panel
      // above it however tall either grows -- with every stats section open in
      // turn, the legend still starts below the panel's bottom edge.
      for (const section of ['stat-years', 'stat-riding', 'stat-streets', 'stat-places']) {
        await page.locator(`#stat-chips .chip[data-section="${section}"]`).click();
        const clears = await page.evaluate(() => {
          const s = document.getElementById('stats').getBoundingClientRect();
          const g = document.getElementById('layers').getBoundingClientRect();
          return g.top >= s.bottom && g.bottom <= window.innerHeight;
        });
        expect(clears, `the switcher clears the panel with #${section} open`).toBe(true);
      }
    });
});
