import { test, expect, gotoMap, openSection, chip } from './helpers.js';
import { buildFixture } from './fixture.js';

const SECTION = 'stat-citibike';

/** Turn the dock layer on and wait for the markers to land. */
async function showDocks(page) {
  await page.locator('#cb-check').check();
  await expect.poll(() => page.evaluate(() => map.hasLayer(dockLayer))).toBe(true);
}

const visibleDocks = (page) => page.evaluate(() => dockLayer.getLayers().length);

test.describe('Citi Bike dock layer', () => {
  test('is off until asked for, then draws one marker per placed dock', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#cb-check')).not.toBeChecked();
    expect(await page.evaluate(() => map.hasLayer(dockLayer))).toBe(false);

    await showDocks(page);
    // Three of the four docks have coordinates; Ghost Dock has none and so
    // gets no marker, while still counting everywhere else.
    expect(await visibleDocks(page)).toBe(3);
    expect(await page.evaluate(() => dockMarkers[3])).toBe(null);
    expect(await page.evaluate(() => cbData.docks.length)).toBe(4);
  });

  test('resizes with the same date slider that filters the edges', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    expect(await visibleDocks(page)).toBe(3);

    // Ride dates run 2023-04-01 .. 2024-07-04; pull the upper handle back to
    // 2023-06-15. Only the 2023-04-01 trip survives, so Terminal Dock -- whose
    // only trips are in 2025 -- drops off the map entirely.
    await page.evaluate(() => {
      const hi = document.getElementById('range-hi');
      hi.value = '1';
      hi.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await expect.poll(() => visibleDocks(page)).toBe(2);
    expect(await page.evaluate(() => dockLayer.hasLayer(dockMarkers[2]))).toBe(false);

    // And back: the layer follows the slider in both directions.
    await page.evaluate(() => {
      const hi = document.getElementById('range-hi');
      hi.value = '3';
      hi.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await expect.poll(() => visibleDocks(page)).toBe(3);
  });

  test('a marker is bigger when it carries more of the range', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    // Home and Park see 4 trips each, Terminal only 2.
    const [home, terminal] = await page.evaluate(() =>
      [dockMarkers[0].getRadius(), dockMarkers[2].getRadius()]);
    expect(home).toBeGreaterThan(terminal);
  });

  test('clicking a dock draws its own trips and nothing else', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    expect(await page.evaluate(() => dockLinks)).toBe(null);

    await page.evaluate(() => dockMarkers[0].openPopup());
    const popup = page.locator('.leaflet-popup-content');
    await expect(popup).toBeVisible();
    // Home reaches Park (2 out, 1 back) and Terminal (1 out): two lines, not
    // one per trip, and never a line between docks it does not join.
    expect(await page.evaluate(() => dockLinks.getLayers().length)).toBe(2);
    await expect(popup).toContainText('Home Dock & Main St');
    await expect(popup).toContainText('4 trips');
    await expect(popup).toContainText('3 out / 1 in');
    await expect(popup).toContainText('2 docks reached');
    await expect(popup.locator('.ride-row').first()).toContainText('Park Dock & 5 Ave');

    // Closing the popup takes the lines with it.
    await page.evaluate(() => map.closePopup());
    await expect.poll(() => page.evaluate(() => dockLinks)).toBe(null);
    expect(await page.evaluate(() => dockSelected)).toBe(null);
  });

  test('a selected dock redraws when the date range changes under it', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    expect(await page.evaluate(() => dockLinks.getLayers().length)).toBe(2);

    // Back to 2023 only: Home's single trip that year went to Park, so the
    // Terminal line has to go rather than linger from the wider range.
    await page.evaluate(() => {
      const hi = document.getElementById('range-hi');
      hi.value = '1';
      hi.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await expect.poll(() => page.evaluate(() => dockLinks?.getLayers().length ?? 0)).toBe(1);
  });

  test('turning the layer off clears the map', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    await page.locator('#cb-check').uncheck();
    expect(await page.evaluate(() => map.hasLayer(dockLayer))).toBe(false);
    expect(await page.evaluate(() => dockLinks)).toBe(null);
  });

  test('says when the docks start, so an empty range reads as empty not broken', async ({ page }) => {
    await gotoMap(page);
    // The ride history opens years before the first dock trip, so most of the
    // slider has no docks in it at all.
    await expect(page.locator('#cb-range')).toHaveText('(Apr 2023 on)');
  });

  test('the drawn edges are untouched by any of it', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    const edges = () => page.evaluate(() => {
      let n = 0;
      geoLayer.eachLayer(() => n++);
      return n;
    });
    expect(await edges()).toBe(3);
    await page.evaluate(() => dockMarkers[0].openPopup());
    expect(await edges()).toBe(3);
  });

  test('the stats section reports the totals and the caveat', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);

    await expect(section).toContainText('5');        // trips, from the array
    await expect(section).toContainText('2 h');
    await expect(section).toContainText('9 min');
    await expect(section).toContainText('2 of 3');   // same_day of days
    await expect(section).toContainText('0 of 4 docks');
    await expect(section).toContainText('drawn as a route or counted toward coverage');
    await expect(section).toContainText('$5.00 paid of $12.00 charged');

    const rows = section.locator('.cb-flow-row');
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0).locator('.cb-flow-val')).toHaveText('+4');
    await expect(rows.nth(2).locator('.cb-flow-val')).toHaveText('-4');
  });

  test('section, chip and toggle stay hidden without a citibike block', async ({ page }) => {
    await gotoMap(page, buildFixture({ citibike: null }));
    await expect(page.locator(`#${SECTION}`)).toBeHidden();
    await expect(chip(page, SECTION)).toBeHidden();
    await expect(page.locator('#cb-toggle')).toBeHidden();
  });
});
