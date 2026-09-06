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
    // Two lines, not one: on a busy dock this line was the widest thing in
    // the popup and set the width for every row under it.
    await expect(popup.locator('.dock-sub > div')).toHaveCount(2);
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
    await popup.locator('.ride-row', { hasText: 'Terminal Dock' }).locator('.dock-name').click();
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

  test('a row reads name first, counts and route under it', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const park = page.locator('.leaflet-popup-content .ride-row', { hasText: 'Park Dock' });

    // Home sent two trips to Park and got one back. Down-right out, up-right
    // home, on their own line under the name: three things on one line made
    // the popup as wide as the stats panel beside it.
    await expect(park.locator('.dock-name')).toHaveText('Park Dock & 5 Ave');
    await expect(park.locator('.dock-meta .dock-count')).toHaveText('\u21982 \u21971');
    await expect(park.locator('.dock-meta .dock-trace')).toHaveText('\u25b8 2 routes');
  });

  test('a row offers the recording of the trip, and only where there is one', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const popup = page.locator('.leaflet-popup-content');

    // Home reaches Park on three trips, two of which were recorded (rides 3
    // and 1), so the row offers both.
    const park = popup.locator('.ride-row', { hasText: 'Park Dock' });
    await expect(park.locator('.dock-trace')).toHaveText('\u25b8 2 routes');
    // Home -> Terminal was never recorded, so nothing is offered: a link that
    // has nothing to show must not look like one.
    const terminal = popup.locator('.ride-row', { hasText: 'Terminal Dock' });
    await expect(terminal.locator('.dock-trace')).toHaveCount(0);
  });

  test('showing a recording keeps the popup, the line and the ride view', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const popup = page.locator('.leaflet-popup-content');
    await popup.locator('.ride-row', { hasText: 'Park Dock' }).locator('.dock-trace').click();

    // Newest first: ride 3 rather than ride 1.
    expect(await page.evaluate(() => rideView)).toBe(3);
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    // The popup is how a reader walks the network, so tracing a pair must not
    // close it the way a street popup's own ride row does.
    await expect(popup.locator('.dock-head')).toHaveText('Home Dock & Main St');
    expect(await page.evaluate(() => dockSelected)).toBe(0);
    // Home reaches two docks, but the links are the same cyan as the route:
    // while a pair's ride is up, that pair's straight line is the only one
    // drawn, so the route sits over its own trip and nothing else.
    expect(await page.evaluate(() => dockLinks.getLayers().length)).toBe(1);
  });

  test('says when the recording holds more than the trip that was clicked', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const popup = page.locator('.leaflet-popup-content');
    const trace = popup.locator('.ride-row', { hasText: 'Park Dock' }).locator('.dock-trace');

    // Ride 3 covers two Citibike trips, and ride view draws all of it, so the
    // extra leg on screen has to be accounted for rather than read as part of
    // this hop.
    await trace.click();
    await expect(popup.locator('.dock-trace-note'))
      .toHaveText('whole recording \u2014 it also covers 1 other Citibike trip');

    // Ride 1 covers only its own trip, so there is nothing to explain.
    await trace.click();
    expect(await page.evaluate(() => rideView)).toBe(1);
    await expect(trace).toHaveText('\u25b8 route 2/2');
    await expect(popup.locator('.dock-trace-note')).toBeHidden();
  });

  test('clicking past the last recording puts the map back', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const popup = page.locator('.leaflet-popup-content');
    const trace = popup.locator('.ride-row', { hasText: 'Park Dock' }).locator('.dock-trace');

    await trace.click();
    await trace.click();
    await trace.click();
    expect(await page.evaluate(() => rideView)).toBe(null);
    await expect(page.locator('#ride-view-bar')).toBeHidden();
    await expect(trace).toHaveText('\u25b8 2 routes');
    // The dock is a dock again: every partner it reaches, drawn once more.
    expect(await page.evaluate(() => dockLinks.getLayers().length)).toBe(2);
    // ... and the row still hops docks afterwards, which is what it is for.
    // The name, not the row's centre: that now lands on the meta line, where
    // the route chip deliberately keeps its clicks to itself.
    await popup.locator('.ride-row', { hasText: 'Park Dock' }).locator('.dock-name').click();
    await expect
      .poll(() => page.locator('.leaflet-popup-content .dock-head').count())
      .toBe(1);
    await expect(page.locator('.leaflet-popup-content .dock-head'))
      .toHaveText('Park Dock & 5 Ave');
  });

  test('escape takes the route and the dock behind it together', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const trace = page.locator('.leaflet-popup-content .ride-row', { hasText: 'Park Dock' })
      .locator('.dock-trace');
    await trace.click();
    await expect(trace).toHaveText('\u25b8 route 1/2');

    // Leaflet's own keyboard handler closes the popup and the page's clears
    // ride view, so one key backs all the way out of the detour rather than
    // leaving a dock selected with nothing on it.
    await page.keyboard.press('Escape');
    await expect.poll(() => page.evaluate(() => rideView)).toBe(null);
    expect(await page.evaluate(() => [dockSelected, dockLinks])).toEqual([null, null]);

    // Reopening the dock starts from the top of the cycle with every link
    // back, because the popup is rebuilt from scratch each time.
    await page.evaluate(() => dockMarkers[0].openPopup());
    await expect(page.locator('.leaflet-popup-content .ride-row', { hasText: 'Park Dock' })
      .locator('.dock-trace')).toHaveText('\u25b8 2 routes');
    expect(await page.evaluate(() => dockLinks.getLayers().length)).toBe(2);
  });

  test('the arrow keys step the cycle, and the bar says where it is', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const trace = page.locator('.leaflet-popup-content .ride-row', { hasText: 'Park Dock' })
      .locator('.dock-trace');
    const bar = page.locator('#ride-view-label .rv-step');

    // Home reaches Park on two recordings, newest first: rides 3 then 1.
    await trace.click();
    expect(await page.evaluate(() => rideView)).toBe(3);
    await expect(bar).toHaveText('\u2191\u2193 route 1/2');

    // Down goes on through the list, which runs newest first.
    await page.keyboard.press('ArrowDown');
    await expect.poll(() => page.evaluate(() => rideView)).toBe(1);
    await expect(bar).toHaveText('\u2191\u2193 route 2/2');
    // The row it came from follows the keyboard, since both read one cycle.
    await expect(trace).toHaveText('\u25b8 route 2/2');

    // Off either end it wraps rather than dropping out of ride view: leaving
    // is what Escape and the bar's button are for.
    await page.keyboard.press('ArrowDown');
    await expect.poll(() => page.evaluate(() => rideView)).toBe(3);
    await page.keyboard.press('ArrowUp');
    await expect.poll(() => page.evaluate(() => rideView)).toBe(1);
  });

  test('the arrows belong to the cycle, not to the map under it', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    const center = () => page.evaluate(() => {
      const c = map.getCenter();
      return [c.lat.toFixed(4), c.lng.toFixed(4)];
    });
    const before = await center();

    // Leaflet pans on the arrow keys from its own listener on the map
    // container, so with a cycle running the page has to get there first.
    await page.evaluate(() => dockMarkers[0].openPopup());
    await page.locator('.leaflet-popup-content .ride-row', { hasText: 'Park Dock' })
      .locator('.dock-trace').click();
    await page.evaluate(() => map.getContainer().focus());
    await page.keyboard.press('ArrowDown');
    await expect.poll(() => page.evaluate(() => rideView)).toBe(1);
    expect(await center()).toEqual(before);

    // With no cycle running the map keeps its own keys.
    await page.evaluate(() => exitRideView());
    await page.keyboard.press('ArrowDown');
    await expect.poll(center).not.toEqual(before);
  });

  test('a pair with one recording gets no position in the bar', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    // Terminal -> Ghost has a single recording, so there is no cycle to
    // report and the bar stays about the ride.
    await page.evaluate(() => dockMarkers[2].openPopup());
    await page.locator('.leaflet-popup-content .dock-row-flat .dock-trace').click();
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    await expect(page.locator('#ride-view-label .rv-step')).toHaveCount(0);
  });

  test('a ride shown from anywhere else takes no arrow keys with it', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    await page.locator('.leaflet-popup-content .ride-row', { hasText: 'Park Dock' })
      .locator('.dock-trace').click();

    // viewRide from a street popup or a year link is not a dock pair's cycle,
    // so the keys go back to the map with it.
    await page.evaluate(() => viewRide(2));
    expect(await page.evaluate(() => dockTrace)).toBe(null);
    await expect(page.locator('#ride-view-label .rv-step')).toHaveCount(0);
    await page.keyboard.press('ArrowDown');
    await expect.poll(() => page.evaluate(() => rideView)).toBe(2);
  });

  test('a recording is offered even where the far dock cannot be placed', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    // Terminal -> Ghost was recorded; Ghost has no coordinates, so the row
    // stays flat, but the ride is on the map either way.
    await page.evaluate(() => dockMarkers[2].openPopup());
    const ghost = page.locator('.leaflet-popup-content .dock-row-flat');
    await expect(ghost.locator('.dock-trace')).toHaveText('\u25b8 route');
    await ghost.locator('.dock-trace').click();
    expect(await page.evaluate(() => rideView)).toBe(3);
  });

  test('a payload from before the trips carried a ride offers no routes', async ({ page }) => {
    const cb = buildFixture().properties.citibike;
    await gotoMap(page, buildFixture({
      citibike: { ...cb, trips: cb.trips.map(t => t.slice(0, 3)) },
    }));
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    await expect(page.locator('.leaflet-popup-content .dock-trace')).toHaveCount(0);
    // The rest of the layer is untouched by the missing element.
    await expect(page.locator('.leaflet-popup-content .ride-row')).toHaveCount(2);
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

    // Back to 2023 only: both of Home's trips that year went to Park, so the
    // Terminal line has to go rather than linger from the wider range.
    await page.evaluate(() => {
      const hi = document.getElementById('range-hi');
      hi.value = '1';
      hi.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await expect.poll(() => page.evaluate(() => dockLinks?.getLayers().length ?? 0)).toBe(1);
  });

  // The colour of every edge currently drawn. Ghosted edges are all #555;
  // a live heatmap is plasma, and ride view adds one cyan.
  const edgeColors = (page) => page.evaluate(() => {
    const out = [];
    geoLayer.eachLayer(l => { if (map.hasLayer(l)) out.push(l.options.color); });
    return out;
  });

  const allGhosted = (colors) => colors.length > 0 && colors.every(c => c === '#555');

  test('a dock in focus ghosts the heatmap, and closing it paints it back',
    async ({ page }) => {
      await gotoMap(page);
      await showDocks(page);
      expect(allGhosted(await edgeColors(page))).toBe(false);

      // A handful of straight cyan lines over the whole plasma network is a
      // haystack; the network stays drawn, in outline.
      await page.evaluate(() => dockMarkers[0].openPopup());
      const focused = await edgeColors(page);
      expect(focused).toHaveLength(3);
      expect(allGhosted(focused)).toBe(true);

      await page.evaluate(() => map.closePopup());
      await expect.poll(async () => allGhosted(await edgeColors(page))).toBe(false);
    });

  test('the ghost survives the slider moving under an open dock', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());

    // The slider and the time-lapse still move the network while a dock is
    // focused -- they just move it in outline.
    await page.evaluate(() => {
      const hi = document.getElementById('range-hi');
      hi.value = '1';
      hi.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await expect.poll(async () => allGhosted(await edgeColors(page))).toBe(true);
  });

  test('leaving a traced route goes back to the ghost, not the heatmap', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    const popup = page.locator('.leaflet-popup-content');
    const trace = popup.locator('.ride-row', { hasText: 'Park Dock' }).locator('.dock-trace');

    // Ride view owns the styling while it is up: one cyan route, the rest
    // ghosted by ride view's own rule.
    await trace.click();
    expect((await edgeColors(page)).filter(c => c === '#00e5ff')).toHaveLength(2);

    // Two more clicks walk off the end of the cycle and clear ride view. The
    // dock is still open, so the network goes back to outline.
    await trace.click();
    await trace.click();
    expect(await page.evaluate(() => rideView)).toBe(null);
    expect(allGhosted(await edgeColors(page))).toBe(true);
  });

  test('closing the dock while its route is up leaves ride view alone', async ({ page }) => {
    await gotoMap(page);
    await showDocks(page);
    await page.evaluate(() => dockMarkers[0].openPopup());
    await page.locator('.leaflet-popup-content .ride-row', { hasText: 'Park Dock' })
      .locator('.dock-trace').click();

    await page.evaluate(() => map.closePopup());
    // The route is still the subject, so the popup closing must not repaint
    // the heatmap over it.
    expect(await page.evaluate(() => rideView)).toBe(3);
    expect((await edgeColors(page)).filter(c => c === '#00e5ff')).toHaveLength(2);
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
    await expect(cells.nth(4)).toHaveText('4');       // days out
    await expect(cells.nth(5)).toHaveText('2');
    // Whole minutes on both sides: 9.0 and 41.0 round, they do not print .0
    await expect(cells.nth(6)).toHaveText('9 min');
    await expect(cells.nth(7)).toHaveText('41 min');
  });

  test('keeps the fast facts and drops the explanatory prose', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);
    // Bikes met again -- not the 5 that were simply picked back up off the
    // dock they were parked at (findings/bike-reencounters.md). The count
    // heads the re-encounter list rather than trailing the panel: it is what
    // that list is a list of.
    await expect(section.locator('.cb-lead')).toHaveText(
      '2 unlocks were on a bike ridden before');
    await expect(section).not.toContainText('just parked');
    // The one-way dock rows and the paragraphs that explained them are gone,
    // and so are the fare and re-dock counts.
    await expect(section.locator('.cb-flow-row')).toHaveCount(0);
    await expect(section).not.toContainText('re-docked');
    await expect(section).not.toContainText('paid of');
  });

  test('lists the bikes met again, and says what a meeting is', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);
    await expect(section).toContainText('Bike re-encounters');
    // Named columns: "3x" and "300, 99 d" do not describe themselves, and a
    // per-row tooltip only reaches one row at a time.
    const head = section.locator('.cb-met-head');
    await expect(head.locator('.cb-met-id')).toHaveText('bike');
    await expect(head.locator('.cb-met-n')).toHaveText('times');
    await expect(head.locator('.cb-met-gap')).toHaveText('days apart');
    const rows = section.locator('.cb-met-row');
    await expect(rows).toHaveCount(4);
    // Sticky inside the scroll box, not above it: the reserved scrollbar
    // gutter lives inside that box, so a header outside it sits a gutter's
    // width out of true with the rows. Its columns must line up with theirs.
    await expect(section.locator('.cb-met > .cb-met-head')).toHaveCount(1);
    await expect(head).toHaveCSS('position', 'sticky');
    const headGap = await head.locator('.cb-met-gap').boundingBox();
    const rowGap = await rows.nth(0).locator('.cb-met-gap').boundingBox();
    expect(Math.abs((headGap.x + headGap.width) - (rowGap.x + rowGap.width))).toBeLessThan(1.5);
    // Sorted by encounters, so both 3x rows come first even though the second
    // of them has nothing to play -- the list ranks on how often the bike
    // turned up, not on what is clickable.
    await expect(rows.nth(0).locator('.cb-met-id')).toHaveText('800-1234');
    await expect(rows.nth(1).locator('.cb-met-id')).toHaveText('800-0000');
    await expect(rows.nth(0).locator('.cb-met-n')).toHaveText('3×');
    await expect(rows.nth(2).locator('.cb-met-n')).toHaveText('2×');
    // One gap per pair of encounters: three encounters show two numbers.
    await expect(rows.nth(0).locator('.cb-met-gap')).toHaveText('40, 38 d');
    await expect(rows.nth(2).locator('.cb-met-gap')).toHaveText('648 d');
    // Occasions, not trips. Counting legs overstated every bike whose repeat
    // was a round trip -- 266-5628 read as 3 rides, 2 after it had moved on,
    // when it was unlocked twice 648 days apart.
    await expect(rows.nth(0)).toHaveAttribute(
      'title', 'Unlocked on 3 separate occasions, 40 and 38 days apart. ' +
      '2 recordings — click to play them');
    await expect(rows.nth(2)).toHaveAttribute(
      'title', 'Unlocked on 2 separate occasions, 648 days apart. ' +
      '1 recording — click to show it');
    // A bike met again with nothing recorded keeps its row and says why it
    // does nothing, rather than being dropped to make the list all clickable.
    await expect(rows.nth(3)).toHaveClass(/dim/);
    await expect(rows.nth(3)).toHaveAttribute('title', /No GPS recording covers any of its trips/);
    // The (?) carries the rule, which is not inferable from the words. Both
    // branches have to be in it: a different dock, OR the same dock after 48h.
    // Only the second ever fires on the real export, but stating one branch
    // would describe a different rule. Scoped to the list: the panel carries
    // a (?) per chart, so a bare .cb-help is ambiguous.
    const help = section.locator('.cb-help').nth(0);
    await expect(help).toHaveAttribute(
      'title', 'Bikes ridden more than once (picked up either from a different ' +
      'dock or from the same dock 48+ hours later)');
  });

  test('a bike chip plays its recordings, and the arrows step them', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);
    const chip = section.locator('.cb-met-row').nth(0);   // 800-1234, rides [3, 1]

    await chip.click();
    // Newest first, so the first click lands on ride 3 -- the same order a
    // dock popup's route link uses.
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    await expect(page.locator('#ride-view-label')).toContainText('2024-07-04');
    await expect(chip).toHaveClass(/on/);
    await expect(chip.locator('.cb-met-id')).toHaveText('800-1234 1/2');

    // Down steps to the older recording, and the ride-view bar counts along.
    await page.keyboard.press('ArrowDown');
    await expect(page.locator('#ride-view-label')).toContainText('2023-06-15');
    await expect(chip.locator('.cb-met-id')).toHaveText('800-1234 2/2');
    await expect(page.locator('.rv-step')).toContainText('route 2/2');

    // Up wraps rather than falling out of the cycle.
    await page.keyboard.press('ArrowUp');
    await expect(chip.locator('.cb-met-id')).toHaveText('800-1234 1/2');

    // Clicking walks the cycle and then off the end of it, so the chip that
    // started the detour can end it.
    await chip.click();
    await expect(chip.locator('.cb-met-id')).toHaveText('800-1234 2/2');
    await chip.click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
    await expect(chip).not.toHaveClass(/on/);
    await expect(chip.locator('.cb-met-id')).toHaveText('800-1234');
  });

  test('a bike with one recording shows it and puts the map back', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const chip = page.locator(`#${SECTION} .cb-met-row`).nth(2);  // 800-5678, [1]
    await chip.click();
    await expect(page.locator('#ride-view-label')).toContainText('2023-06-15');
    // One recording is no cycle, so the chip stays a bare id and the bar
    // carries no step counter.
    await expect(chip.locator('.cb-met-id')).toHaveText('800-5678');
    await expect(page.locator('.rv-step')).toHaveCount(0);
    await chip.click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
  });

  test('a bike with no recording is inert', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const chip = page.locator(`#${SECTION} .cb-met-row`).nth(3);  // 800-9999, []
    await chip.click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
    await expect(chip).not.toHaveClass(/on/);
  });

  test('leaving ride view any other way clears the chip too', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const chip = page.locator(`#${SECTION} .cb-met-row`).nth(0);
    await chip.click();
    await expect(chip).toHaveClass(/on/);
    // Escape, not a second click: the chip reads its state from rideView, so
    // every other exit has to clear it as well.
    await page.keyboard.press('Escape');
    await expect(page.locator('#ride-view-bar')).toBeHidden();
    await expect(chip).not.toHaveClass(/on/);
  });

  test('the list is absent when no bike was ever met again', async ({ page }) => {
    const cb = { ...buildFixture().properties.citibike, met: [] };
    await gotoMap(page, buildFixture({ citibike: cb }));
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);
    await expect(section.locator('.cb-met-row')).toHaveCount(0);
    await expect(section).not.toContainText('Bike re-encounters');
    // The count heads that block, so it goes with it: no bike met again is
    // nothing to count, and a "0 unlocks" line would be noise.
    await expect(section.locator('.cb-lead')).toHaveCount(0);
    // The rest of the panel is untouched by its absence.
    await expect(section.locator('.cb-cmp .v').first()).toHaveText('5');
    await expect(section.locator('.cb-type .cb-stack-row')).toHaveCount(1);
    await expect(section.locator('.cb-gen .cb-stack-row')).toHaveCount(3);
  });

  test('splits the trips by bike type, and bounds both shares', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const chart = page.locator(`#${SECTION} .cb-type`);
    await expect(chart).toContainText('Bike type');
    const row = chart.locator('.cb-stack-row');
    await expect(row).toHaveCount(1);
    // 3 of 5 trips carry an ebike charge. The bound on each side is
    // load-bearing and rides on the number rather than on a caption: a free
    // ebike ride carries no such charge, so 60% is a floor and the classic
    // 40% is a ceiling.
    await expect(row.locator('.cb-stack-val.first')).toHaveText('\u226440%');
    await expect(row.locator('.cb-stack-val:not(.first)')).toHaveText('\u226560%');
    await expect(row).toHaveAttribute('title', 'at least 3 of 5 trips on an ebike');
    await expect(chart.locator('.cb-help')).toHaveAttribute('title', /this is a floor/);
    // Widths carry the split, so the ebike segment is the larger of the two.
    const classic = await row.locator('.cb-type-classic').boundingBox();
    const ebike = await row.locator('.cb-type-ebike').boundingBox();
    expect(ebike.width).toBeGreaterThan(classic.width);
    const key = chart.locator('.cb-stack-key');
    await expect(key).toContainText('classic');
    await expect(key).toContainText('ebike');
  });

  test('stacks each year by which generation of bike was ridden', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);
    await expect(section).toContainText('Fleet generation');
    const rows = section.locator('.cb-gen .cb-stack-row');
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0).locator('.cb-stack-label')).toHaveText('2023');
    // A share each side, older on the left against its own segment: 1 of 4 and
    // 3 of 4, then 2 and 2, then none and all.
    await expect(rows.nth(0).locator('.cb-stack-val.first')).toHaveText('25%');
    await expect(rows.nth(0).locator('.cb-stack-val:not(.first)')).toHaveText('75%');
    await expect(rows.nth(1).locator('.cb-stack-val.first')).toHaveText('50%');
    await expect(rows.nth(1).locator('.cb-stack-val:not(.first)')).toHaveText('50%');
    await expect(rows.nth(2).locator('.cb-stack-val.first')).toHaveText('0%');
    await expect(rows.nth(2).locator('.cb-stack-val:not(.first)')).toHaveText('100%');
    await expect(rows.nth(0)).toHaveAttribute(
      'title', '2023: 3 of 4 trips on the newer fleet, 1 on the older');
    // A year with none of a generation draws one segment, not a zero-width
    // second one that the 2px gap would still show as a sliver.
    await expect(rows.nth(2).locator('.cb-gen-old')).toHaveCount(0);
    await expect(rows.nth(2).locator('.cb-gen-new')).toHaveCount(1);
    // Segment widths carry the split, so the 50% year's two are equal and the
    // 75% year's newer segment is the larger.
    const even = rows.nth(1);
    const a = await even.locator('.cb-gen-old').boundingBox();
    const b = await even.locator('.cb-gen-new').boundingBox();
    expect(Math.abs(a.width - b.width)).toBeLessThan(1.5);
    const skew = rows.nth(0);
    const oldW = (await skew.locator('.cb-gen-old').boundingBox()).width;
    const newW = (await skew.locator('.cb-gen-new').boundingBox()).width;
    expect(newW).toBeGreaterThan(oldW * 2);
    // Two series, so the legend is not optional, and the caption has to say
    // the share is of one rider's unlocks rather than of the fleet.
    const key = section.locator('.cb-gen .cb-stack-key');
    await expect(key).toContainText('older');
    await expect(key).toContainText('newer');
    // The (?) says where the labels come from, because no published source
    // maps a bike number to a model, and that the share is of one rider's
    // unlocks -- the caveat that decides what the chart claims. It used to be
    // a caption under the bars as well, saying the same thing twice.
    const help = section.locator('.cb-gen .cb-help');
    await expect(help).toHaveAttribute('title', /five digits \(16825\) is the older fleet/);
    await expect(help).toHaveAttribute('title', /publishes no number-to-model mapping/);
    await expect(help).toHaveAttribute('title', /the docks I used, not the fleet/);
    await expect(section).not.toContainText('Share of my own unlocks');
  });

  test('the generation chart is absent when the export has no years', async ({ page }) => {
    const cb = { ...buildFixture().properties.citibike, gen: [] };
    await gotoMap(page, buildFixture({ citibike: cb }));
    await openSection(page, SECTION);
    const section = page.locator(`#${SECTION}`);
    await expect(section.locator('.cb-gen')).toHaveCount(0);
    await expect(section).not.toContainText('Fleet generation');
    await expect(section).toContainText('Bike re-encounters');
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
