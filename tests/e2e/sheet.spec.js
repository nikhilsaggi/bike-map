import { test, expect, gotoMap, clickEdge, edgePoint } from './helpers.js';
import { EDGES } from './fixture.js';

// The phone layout. Under the breakpoint the two rails are replaced by one
// bottom sheet showing exactly one thing at a time, and the reader says how
// much of the screen it takes. What is asserted here is that shape: the map
// is not covered, the panes take turns rather than stack, and every path in
// and out of a detail lands in the same place.

const PHONE = { width: 390, height: 844 };

/** The three stops, as the page computes them: peek is measured, the other
 *  two are shares of the window (0.46 and 0.88 of 844 = 388 and 743). */
async function stops(page) {
  return page.evaluate(() => {
    const peek = document.getElementById('sheet-handle').offsetHeight
      + document.getElementById('sheet-nav').offsetHeight;
    return [peek, Math.round(innerHeight * 0.46), Math.round(innerHeight * 0.88)];
  });
}

const sheetHeight = (page) =>
  page.locator('#sheet').evaluate((el) => Math.round(el.getBoundingClientRect().height));

/** Wait out the 220ms height transition. */
async function settled(page) {
  await page.locator('#sheet').evaluate((el) =>
    Promise.all(el.getAnimations().map((a) => a.finished)));
}

test.describe('phone sheet', () => {
  test('replaces the rails under the breakpoint, and hands them back over it',
    async ({ page }) => {
      await page.setViewportSize(PHONE);
      await gotoMap(page);
      await expect(page.locator('#sheet')).toBeVisible();
      for (const sel of ['#right-rail', '#stats', '#legend']) {
        await expect(page.locator(sel)).toBeHidden();
      }
      // The zoom buttons go with them: pinch is the gesture here, and they sat
      // in the one corner a reader has to be able to reach the map through.
      await expect(page.locator('.leaflet-control-zoom')).toBeHidden();

      await page.setViewportSize({ width: 1280, height: 800 });
      await expect(page.locator('#sheet')).toBeHidden();
      await expect(page.locator('#stats')).toBeVisible();
      await expect(page.locator('#legend')).toBeVisible();
      await expect(page.locator('.leaflet-control-zoom')).toBeVisible();
    });

  // A landscape phone passes any width test comfortably and then has nowhere
  // to put a panel that is most of the screen tall, so the breakpoint asks
  // about height as well. Both halves are the same question -- is there room
  // beside the map -- and get the same answer.
  test('a landscape phone gets the sheet on its height, not its width',
    async ({ page }) => {
      await page.setViewportSize({ width: 844, height: 390 });
      await gotoMap(page);
      await expect(page.locator('#sheet')).toBeVisible();
      await expect(page.locator('#stats')).toBeHidden();

      // The switcher shares the panes' column rather than being pinned to the
      // left of a sheet three times as wide as its content.
      const left = (sel) => page.locator(sel).evaluate((el) =>
        Math.round(el.getBoundingClientRect().left));
      expect(await left('#sheet-tabs')).toBe(await left('#pane-stats'));

      // A window that is merely short-ish is still the desktop.
      await page.setViewportSize({ width: 1280, height: 560 });
      await expect(page.locator('#sheet')).toBeHidden();
      await expect(page.locator('#stats')).toBeVisible();
    });

  // The bug this layout was built for: the stats panel and the legend used to
  // stack down the right of a phone and take the top half of the screen, and
  // the busiest street on the map ran underneath them. Clicking it hit the
  // legend. Now nothing but the sheet is over the map, and the sheet is at the
  // bottom, so the middle of the screen belongs to the map.
  test('leaves the middle of the map clickable', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await gotoMap(page);
    const pt = await edgePoint(page, EDGES.center.lat);
    const hit = await page.evaluate(
      ([x, y]) => document.elementFromPoint(x, y).closest('#sheet, #left-rail') === null,
      [pt.x, pt.y],
    );
    expect(hit, 'the chrome is over the centre of the map').toBe(true);

    await clickEdge(page, EDGES.center.lat);
    await expect(page.locator('#sheet-title')).toHaveText('Center Street');
  });

  test('the two tabs take turns, and the open one shows and hides the sheet',
    async ({ page }) => {
      await page.setViewportSize(PHONE);
      await gotoMap(page);
      const [peek, half] = await stops(page);
      const stats = page.locator('.sheet-tab[data-pane="stats"]');
      const filters = page.locator('.sheet-tab[data-pane="legend"]');

      // Opens on the stats, at the middle stop.
      await expect(stats).toHaveClass(/active/);
      await expect(page.locator('#pane-stats')).toBeVisible();
      await expect(page.locator('#pane-legend')).toBeHidden();
      expect(await sheetHeight(page)).toBe(half);

      await filters.click();
      await settled(page);
      await expect(filters).toHaveClass(/active/);
      await expect(stats).not.toHaveClass(/active/);
      await expect(page.locator('#pane-legend')).toBeVisible();
      await expect(page.locator('#pane-stats')).toBeHidden();
      // The legend's ramp and its caption come across with the controls.
      await expect(page.locator('#pane-legend #legend-title')).toBeVisible();
      await expect(page.locator('#pane-legend #range-hi')).toBeVisible();

      // A tap on the tab already showing hands the screen back to the map,
      // and another takes it again.
      await filters.click();
      await settled(page);
      expect(await sheetHeight(page)).toBe(peek);
      await filters.click();
      await settled(page);
      expect(await sheetHeight(page)).toBe(half);
    });

  test('the grab strip drags to a stop and taps to show and hide', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await gotoMap(page);
    const [peek, half, full] = await stops(page);
    const grip = await page.locator('#sheet-handle').boundingBox();

    // Dragged most of the way to the top, it snaps to the tallest stop.
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip.x + grip.width / 2, 60, { steps: 8 });
    await page.mouse.up();
    await settled(page);
    expect(await sheetHeight(page)).toBe(full);

    // ... and dragged back down past the middle, to the lowest.
    const raised = await page.locator('#sheet-handle').boundingBox();
    await page.mouse.move(raised.x + raised.width / 2, raised.y + raised.height / 2);
    await page.mouse.down();
    await page.mouse.move(raised.x + raised.width / 2, PHONE.height - 20, { steps: 8 });
    await page.mouse.up();
    await settled(page);
    expect(await sheetHeight(page)).toBe(peek);

    // A tap is show-and-hide, never a third height: what a reader means by it
    // is "give me the map" or "give me that back".
    await page.locator('#sheet-handle').click();
    await settled(page);
    expect(await sheetHeight(page)).toBe(half);
    await page.locator('#sheet-handle').click();
    await settled(page);
    expect(await sheetHeight(page)).toBe(peek);
  });

  test('a clicked feature takes the sheet over, and Back gives it up',
    async ({ page }) => {
      await page.setViewportSize(PHONE);
      await gotoMap(page);
      await page.locator('.sheet-tab[data-pane="legend"]').click();

      await clickEdge(page, EDGES.center.lat);
      await expect(page.locator('#pane-detail')).toBeVisible();
      await expect(page.locator('#sheet-tabs')).toBeHidden();
      await expect(page.locator('#sheet-back')).toBeVisible();
      await expect(page.locator('#sheet-title')).toHaveText('Center Street');
      await expect(page.locator('#pane-detail .ride-row')).toHaveCount(4);

      // Back, not close: it returns the tab that was open, not the default.
      await page.locator('#sheet-back').click();
      await expect(page.locator('#pane-legend')).toBeVisible();
      await expect(page.locator('#pane-detail')).toBeHidden();
      await expect(page.locator('#sheet-tabs')).toBeVisible();
      await expect(page.locator('#sheet-title')).toBeHidden();
      await expect(page.locator('.sheet-tab[data-pane="legend"]')).toHaveClass(/active/);
    });

  // Escape and a tap on the map are the same door out as Back, so they leave
  // the sheet in the same place rather than each in one of their own.
  test('Escape and a tap on the map leave the detail the way Back does',
    async ({ page }) => {
      await page.setViewportSize(PHONE);
      await gotoMap(page);
      for (const leave of [
        () => page.keyboard.press('Escape'),
        () => page.mouse.click(PHONE.width - 20, 60),  // empty map, clear of the sheet
      ]) {
        await clickEdge(page, EDGES.center.lat);
        await expect(page.locator('#pane-detail')).toBeVisible();
        await leave();
        await expect(page.locator('#pane-stats')).toBeVisible();
        await expect(page.locator('#sheet-tabs')).toBeVisible();
        await expect(page.locator('#sheet-back')).toBeHidden();
      }
    });

  // The regression this layout replaced. Its predecessor collapsed the stats
  // panel when a detail opened and reopened it on close, which put two owners
  // on one piece of state: going from one street to the next runs a close and
  // an open, so the sheet was told two contradictory things per click. Here
  // the pane is a function of what is open, so a second click cannot leave it
  // out of step -- and nothing but the reader moves the height.
  test('street to street never moves the sheet', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await gotoMap(page);
    const [, half, full] = await stops(page);

    await clickEdge(page, EDGES.center.lat);
    await settled(page);
    expect(await sheetHeight(page)).toBe(half);

    // The reader drags it up to read the rows; the next street must not undo
    // that, and must not leave the tabs and the title both showing.
    await page.locator('#sheet-handle').click();  // down
    await settled(page);
    await page.locator('#sheet-handle').click();  // and back up
    await settled(page);
    const before = await sheetHeight(page);

    await clickEdge(page, EDGES.north.lat);
    await settled(page);
    await expect(page.locator('#sheet-title')).toHaveText('North Street');
    await expect(page.locator('#pane-detail')).toBeVisible();
    await expect(page.locator('#sheet-tabs')).toBeHidden();
    expect(await sheetHeight(page)).toBe(before);

    // Dragged to the top, a new detail still does not shrink it: opening one
    // is a reason to show the sheet, never to take it away.
    await page.locator('#sheet-back').click();
    const grip = await page.locator('#sheet-handle').boundingBox();
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip.x + grip.width / 2, 40, { steps: 8 });
    await page.mouse.up();
    await settled(page);
    expect(await sheetHeight(page)).toBe(full);
    await clickEdge(page, EDGES.center.lat);
    await settled(page);
    expect(await sheetHeight(page)).toBe(full);
  });

  // A detail opening while the sheet is out of the way is the one thing that
  // moves it, and only up to the middle.
  test('a detail raises a sheet that was out of the way', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await gotoMap(page);
    const [peek, half] = await stops(page);
    await page.locator('#sheet-handle').click();
    await settled(page);
    expect(await sheetHeight(page)).toBe(peek);

    await clickEdge(page, EDGES.center.lat);
    await settled(page);
    expect(await sheetHeight(page)).toBe(half);
  });

  // All three kinds open the one pane, so all three are checked -- and the
  // sheet's heading takes the layer's colour the way the rail's panel does.
  test('every kind of click opens in the detail pane', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await gotoMap(page);
    // Both layers on first, through the Filters tab that is where the switcher
    // lives on a phone. Once, not per kind: a tap on the tab already showing
    // is the gesture that puts the sheet away, so reaching for it twice would
    // fold the switcher out of reach rather than open it again.
    await page.locator('.sheet-tab[data-pane="legend"]').click();
    await page.locator('#pane-legend #cb-check').check();
    await page.locator('#pane-legend #nb-check').check();

    const open = [
      // Not a map click: this is what the three click handlers themselves
      // call, without depending on where a marker happened to land.
      ['street', 'kind-edge', () => page.evaluate(() => {
        const layer = geoLayer.getLayers().find((l) => l._filteredCount > 0);
        selectEdge(layer, layer.feature.properties.rides, layer.getBounds().getCenter());
      })],
      ['dock', 'kind-dock', () => page.evaluate(() => selectDock(0))],
      ['area', 'kind-area', () => page.evaluate(() => selectArea(0))],
    ];

    for (const [kind, cls, show] of open) {
      await show();
      await expect(page.locator('#pane-detail'), kind).toBeVisible();
      await expect(page.locator('#sheet-title'), kind).not.toBeEmpty();
      await expect(page.locator('#sheet'), kind).toHaveClass(new RegExp(cls));
      // The sheet is the box now, so the panel inside it sets no width of its
      // own: it fills the pane, which is what is capped.
      const pane = await page.locator('#pane-detail').boundingBox();
      expect(pane.width, `${kind} fills the sheet`).toBeLessThanOrEqual(PHONE.width);
      await page.locator('#sheet-back').click();
    }
  });

  // Attribution is not optional, so it clears the sheet at every height rather
  // than sitting under it.
  test('the map attribution stays clear of the sheet', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await gotoMap(page);
    const clear = async () => {
      const a = await page.locator('.leaflet-control-attribution')
        .evaluate((el) => el.getBoundingClientRect().toJSON());
      const s = await page.locator('#sheet')
        .evaluate((el) => el.getBoundingClientRect().toJSON());
      return a.bottom <= s.top + 1;
    };
    expect(await clear(), 'covered at the middle stop').toBe(true);
    await page.locator('#sheet-handle').click();
    await settled(page);
    expect(await clear(), 'covered at the lowest stop').toBe(true);
  });

  // The height and the tab the reader chose are theirs, and outlive the visit
  // -- the same contract the desktop panels' collapse buttons carry.
  test('the chosen tab and height come back on the next visit', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await gotoMap(page);
    const [peek] = await stops(page);
    await page.locator('.sheet-tab[data-pane="legend"]').click();
    await page.locator('#sheet-handle').click();
    await settled(page);

    await gotoMap(page);
    expect(await sheetHeight(page)).toBe(peek);
    await expect(page.locator('.sheet-tab[data-pane="legend"]')).toHaveClass(/active/);
    await expect(page.locator('#pane-legend')).toBeVisible();
  });
});
