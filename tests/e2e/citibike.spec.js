import { test, expect, gotoMap, openSection, chip, clickEdge } from './helpers.js';
import { buildFixture, EDGES } from './fixture.js';

const SECTION = 'stat-citibike';

/** Turn the dock layer on and wait for the markers to land. */
async function showDocks(page) {
  await page.locator('#cb-check').check();
  await expect.poll(() => page.evaluate(() => map.hasLayer(dockLayer))).toBe(true);
}

const visibleDocks = (page) => page.evaluate(() => dockLayer.getLayers().length);

test.describe('Citibike dock layer', () => {
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

  test('a popup row jumps to that dock, so the network is walkable', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const popup = page.locator('.leaflet-popup-content');
    await expect(popup.locator('.dock-head')).toHaveText('Home Dock & Main St');

    // The rows carry the page's link styling, so they have to actually go
    // somewhere -- looking clickable and doing nothing is the bug this covers.
    await popup.locator('.ride-row', { hasText: 'Terminal Dock' }).click();
    // Leaflet holds the outgoing popup in the DOM through its fade, so wait
    // for it to go before reading "the" heading.
    await expect
      .poll(() => page.locator('.leaflet-popup-content .dock-head').count())
      .toBe(1);
    await expect(page.locator('.leaflet-popup-content .dock-head'))
      .toHaveText('Terminal Dock & 42 St');
    expect(await page.evaluate(() => dockSelected)).toBe(2);
    // Terminal's own trips now, not the ones it was reached by: it reaches
    // Home, and Ghost Dock which cannot be drawn.
    expect(await page.evaluate(() => dockLinks.getLayers().length)).toBe(1);
  });

  test('a dock with no coordinates is listed but not offered as a link', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    // Terminal reaches Home (placed, so a link) and Ghost Dock (not placed).
    await page.evaluate(() => dockMarkers[2].openPopup());
    const popup = page.locator('.leaflet-popup-content');
    await expect(popup.locator('.dock-row-flat')).toHaveCount(1);
    await expect(popup.locator('.dock-row-flat')).toContainText('Ghost Dock & Gone St');
    await expect(popup.locator('.ride-row')).toHaveCount(1);
    await expect(popup.locator('.ride-row')).toContainText('Home Dock & Main St');
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

  test('says when the docks start later than the rides do', async ({ page }) => {
    // A stretch of slider with no docks in it would otherwise read as broken.
    await gotoMap(page, buildFixture({
      citibike: { ...buildFixture().properties.citibike, from: '2024-05-01' },
    }));
    await expect(page.locator('#cb-range')).toHaveText('(May 2024 on)');
  });

  test('and says nothing when they cover the whole ride history', async ({ page }) => {
    // Default fixture: the first dock trip and the first ride share a date,
    // so there is no empty stretch to explain and the hint is noise.
    await gotoMap(page);
    await expect(page.locator('#cb-range')).toHaveText('');
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

  test('sets the Citibike totals against the own-bike ones', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const cells = page.locator(`#${SECTION} .cb-cmp .v`);

    // Two columns, four rows, Citibike first.
    await expect(page.locator(`#${SECTION} .cb-cmp .head`)).toHaveText(['Citibike', 'Own bike']);
    await expect(cells).toHaveCount(8);
    await expect(cells.nth(0)).toHaveText('5');       // trips
    await expect(cells.nth(1)).toHaveText('3');       // own rides
    await expect(cells.nth(2)).toHaveText('2 h');
    await expect(cells.nth(3)).toHaveText('4 h');
    await expect(cells.nth(4)).toHaveText('3');       // days out
    await expect(cells.nth(5)).toHaveText('2');
    // Whole minutes on both sides: 9.0 and 41.0 round, they do not print .0
    await expect(cells.nth(6)).toHaveText('9 min');
    await expect(cells.nth(7)).toHaveText('41 min');
  });

  test('keeps the oddities line and drops the explanatory prose', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);
    await expect(section).toContainText('1 unlocks re-docked');
    await expect(section).toContainText('$5.00 paid of $12.00 charged');
    // The one-way dock rows and the paragraphs that explained them are gone.
    await expect(section.locator('.cb-flow-row')).toHaveCount(0);
    await expect(section).not.toContainText('one way');
  });

  test('section, chip and toggle stay hidden without a citibike block', async ({ page }) => {
    await gotoMap(page, buildFixture({ citibike: null }));
    await expect(page.locator(`#${SECTION}`)).toBeHidden();
    await expect(chip(page, SECTION)).toBeHidden();
    await expect(page.locator('#cb-toggle')).toBeHidden();
  });
});

test.describe('ride source', () => {
  // EDGES.center carries rides [0,1,2,3]: one of each source, and ride 3 is a
  // two-trip Citibike ride listed twice on EDGES.south.
  test('labels a ride wherever it is already named', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.ride-popup');
    await expect(popup).toBeVisible();
    // Ride 0 is outside the Citibike history, so it gets no label at all
    // rather than being guessed at.
    const rows = popup.locator('.ride-row');
    await expect(rows.nth(0)).not.toContainText('Citibike');
    await expect(rows.nth(0)).not.toContainText('my bike');
    await expect(rows.nth(1).locator('.src-tag.cb')).toHaveText('Citibike');
    await expect(rows.nth(2).locator('.src-tag.own')).toHaveText('own bike');
    // A ride spanning two Citibike trips says so.
    await expect(rows.nth(3).locator('.src-tag.cb')).toHaveText('2 Citibike trips');
  });

  test('names the source in ride view too', async ({ page }) => {
    await gotoMap(page);
    await page.evaluate(() => viewRide(1));
    await expect(page.locator('#ride-view-label .src-tag.cb')).toHaveText('Citibike');
    await page.evaluate(() => viewRide(2));
    await expect(page.locator('#ride-view-label .src-tag.own')).toHaveText('own bike');
  });

  const drawn = (page) => page.evaluate(() => {
    let n = 0;
    geoLayer.eachLayer(l => { if (map.hasLayer(l)) n++; });
    return n;
  });

  test('filters the drawn network by source', async ({ page }) => {
    await gotoMap(page);
    expect(await drawn(page)).toBe(3);

    // Citibike rides are 1 and 3. Ride 1 is on center+north, ride 3 on
    // center+south, so all three edges survive.
    await page.click('.src-btn[data-src="citibike"]');
    await expect.poll(() => drawn(page)).toBe(3);

    // Own-bike is ride 2 alone, which only touches the center edge.
    await page.click('.src-btn[data-src="own"]');
    await expect.poll(() => drawn(page)).toBe(1);

    await page.click('.src-btn[data-src="all"]');
    await expect.poll(() => drawn(page)).toBe(3);
  });

  test('says what a source filter is hiding', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#src-note')).toHaveText('');
    await page.click('.src-btn[data-src="own"]');
    // Ride 0 has an unknown source and belongs to neither side.
    await expect(page.locator('#src-note')).toContainText('1 ride falls outside');
    await page.click('.src-btn[data-src="all"]');
    await expect(page.locator('#src-note')).toHaveText('');
  });

  test('the popup lists only the rides the filter kept', async ({ page }) => {
    await gotoMap(page);
    await page.click('.src-btn[data-src="own"]');
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.ride-popup');
    await expect(popup.locator('.ride-row')).toHaveCount(1);
    await expect(popup.locator('.src-tag.own')).toHaveText('own bike');
  });

  test('the control is absent when no ride has a known source', async ({ page }) => {
    await gotoMap(page, buildFixture({
      rides: [
        [0, '08:30', 10.0, -1],
        [1, '18:05', 25.0, -1],
        [2, '09:15', 12.5, -1],
        [3, '14:45', 40.0, -1],
      ],
    }));
    await expect(page.locator('#src-row')).toBeHidden();
  });
});
