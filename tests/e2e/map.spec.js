import { test, expect, gotoMap, hoverEdge, clickEdge, edgePoint } from './helpers.js';
import { buildFixture, EDGES } from './fixture.js';

test.describe('street tooltips and detail', () => {
  test('hovering a street shows its name and pass count', async ({ page }) => {
    await gotoMap(page);
    const tooltip = await hoverEdge(page, EDGES.center.lat);
    await expect(tooltip).toHaveText('Center Street \u00b7 4 passes');

    // Unnamed, so the count stands alone -- and one ride ridden both ways is
    // two passes, not one.
    const single = await hoverEdge(page, EDGES.south.lat);
    await expect(single).toHaveText('2 passes');
  });

  test('clicking a street opens the inspector listing each ride', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await expect(page.locator('#inspector')).toBeVisible();
    // Named in OSM, so the name is the heading and the count sits under it.
    await expect(page.locator('#inspector-title')).toHaveText('Center Street');
    await expect(page.locator('#inspector .edge-sub')).toHaveText('4 passes');
    const rows = page.locator('#inspector .ride-row');
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText('2023-04-01');
    await expect(rows.nth(0)).toContainText('8:30am');
    await expect(rows.nth(3)).toContainText('2024-07-04');
    await expect(rows.nth(3)).toContainText('2:45pm');
  });

  // The two hours a 12-hour clock gets wrong if it just subtracts 12:
  // 00:xx is 12am, not 0am, and 12:xx is 12pm, not 0pm.
  test('midnight and noon read as 12am and 12pm', async ({ page }) => {
    await gotoMap(page, buildFixture({
      rides: [
        [0, '00:05', 10.0, -1],
        [1, '12:00', 25.0, -1],
        [2, '09:15', 12.5, -1],
        [3, '23:59', 40.0, -1],
      ],
    }));
    await clickEdge(page, EDGES.center.lat);
    const rows = page.locator('#inspector .ride-row');
    await expect(rows.nth(0)).toContainText('12:05am');
    await expect(rows.nth(1)).toContainText('12:00pm');
    await expect(rows.nth(2)).toContainText('9:15am');
    await expect(rows.nth(3)).toContainText('11:59pm');
  });

  test('a repeated ride is one row marked with its pass count', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.south.lat);
    // Unnamed, so the count is the heading and there is no line under it.
    await expect(page.locator('#inspector-title')).toHaveText('2 passes across 1 ride');
    await expect(page.locator('#inspector .edge-sub')).toHaveCount(0);
    const rows = page.locator('#inspector .ride-row');
    await expect(rows).toHaveCount(1);
    await expect(rows.nth(0)).toHaveText('2024-07-04 · 2:45pm ×2 · 2 Citibike trips');
  });
});

test.describe('single-ride view', () => {
  test('clicking a ride row enters ride view; Escape exits', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await page.locator('#inspector .ride-row').first().click();

    const bar = page.locator('#ride-view-bar');
    await expect(bar).toBeVisible();
    await expect(page.locator('#ride-view-label')).toHaveText('2023-04-01 8:30am');
    // The panel covers nothing, so it stays open and says which of its rows
    // is the ride on screen.
    await expect(page.locator('#inspector')).toBeVisible();
    await expect(page.locator('#inspector .ride-row.on')).toHaveCount(1);
    await expect(page.locator('#inspector .ride-row').first()).toHaveClass(/\bon\b/);

    // Escape unwinds the ride first, leaving the list that produced it.
    await page.keyboard.press('Escape');
    await expect(bar).toBeHidden();
    await expect(page.locator('#inspector')).toBeVisible();
    await expect(page.locator('#inspector .ride-row.on')).toHaveCount(0);

    // ... and again to put the panel away.
    await page.keyboard.press('Escape');
    await expect(page.locator('#inspector')).toBeHidden();
  });

  test('clicking the marked row again takes the ride back off', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const row = page.locator('#inspector .ride-row').first();
    await row.click();
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    await row.click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
  });

  // The bar used to be centred at the top of the screen on every viewport,
  // which on a phone is where the stats panel already is: the panel keeps its
  // 236px and its 12px right margin, so on a 430px screen the two boxes
  // shared 126px of the top row. Under the breakpoint the bar joins the left
  // rail's flow instead, above the sheet.
  test('on a phone the bar clears the stats panel', async ({ page }) => {
    await page.setViewportSize({ width: 430, height: 932 });
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await page.locator('#inspector .ride-row').first().click();
    await expect(page.locator('#ride-view-bar')).toBeVisible();

    const box = (sel) => page.locator(sel).evaluate((el) => el.getBoundingClientRect().toJSON());
    const bar = await box('#ride-view-bar');
    for (const sel of ['#stats', '#legend', '#inspector']) {
      const other = await box(sel);
      const overlaps = bar.left < other.right && other.left < bar.right
        && bar.top < other.bottom && other.top < bar.bottom;
      expect(overlaps, `${sel} overlaps the ride-view bar`).toBe(false);
    }
    // In the rail, not floating over the middle of the map.
    expect(bar.bottom).toBeGreaterThan(932 * 0.6);

    // A desktop viewport still gets the centred bar.
    await page.setViewportSize({ width: 1280, height: 800 });
    const wide = await box('#ride-view-bar');
    expect(Math.round(wide.left + wide.width / 2)).toBe(640);
    expect(Math.round(wide.top)).toBe(12);
  });

  test('the exit button leaves ride view', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await page.locator('#inspector .ride-row').last().click();
    await expect(page.locator('#ride-view-label')).toHaveText('2024-07-04 2:45pm · 2 Citibike trips');

    await page.locator('#ride-view-exit').click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
  });
});

// applyFilter is deferred to the next frame, so every assertion after this
// leans on Playwright's own retrying matchers.
async function setRangeHi(page, i) {
  await page.evaluate((v) => {
    const hi = document.getElementById('range-hi');
    hi.value = String(v);
    hi.dispatchEvent(new Event('input', { bubbles: true }));
  }, i);
}

async function setRangeLo(page, i) {
  await page.evaluate((v) => {
    const lo = document.getElementById('range-lo');
    lo.value = String(v);
    lo.dispatchEvent(new Event('input', { bubbles: true }));
  }, i);
}

test.describe('inspector panel', () => {
  // The panel replaced a Leaflet popup that opened on top of the feature it
  // described. What these assert is the reason for the change: it is off the
  // map, it says which feature it is about, and it stays true while the map
  // keeps being used.

  test('the panel and the controls take opposite edges', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const panel = await page.locator('#inspector').boundingBox();
    const view = page.viewportSize();

    expect(panel.x).toBeLessThan(40);                     // hard against the left
    expect(panel.x + panel.width).toBeLessThan(view.width / 3);
    for (const other of ['#legend', '#stats', '#stats-toggle']) {
      const b = await page.locator(other).boundingBox();
      const overlaps = panel.x < b.x + b.width && b.x < panel.x + panel.width
        && panel.y < b.y + b.height && b.y < panel.y + panel.height;
      expect(overlaps, `#inspector overlaps ${other}`).toBe(false);
    }
  });

  // The regression this replaced: the rail used `justify-content:
  // space-between`, which parks a lone item at the *top*. With the panel
  // hidden the legend was the only item, so it rode at the top of the window
  // and dropped to the bottom the moment anything was clicked.
  test('the legend holds the bottom right whether or not a panel is open', async ({ page }) => {
    await gotoMap(page);
    const legend = page.locator('#legend');
    const view = page.viewportSize();

    const closed = await legend.boundingBox();
    expect(closed.x).toBeGreaterThan(view.width / 2);              // right half
    expect(closed.y + closed.height).toBeGreaterThan(view.height * 0.8);  // bottom

    await clickEdge(page, EDGES.center.lat);
    await expect(page.locator('#inspector')).toBeVisible();
    expect(await legend.boundingBox()).toEqual(closed);

    await page.locator('#inspector-close').click();
    await expect(page.locator('#inspector')).toBeHidden();
    expect(await legend.boundingBox()).toEqual(closed);
  });

  // The panel used to be a fixed 272px column, which is wider than any of the
  // three kinds needs and spends the difference covering map.
  test('the panel is sized to its rows, not to a column', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const box = await page.locator('#inspector').boundingBox();
    expect(box.width).toBeGreaterThanOrEqual(180);   // the floor
    expect(box.width).toBeLessThan(272);             // ... and under the cap
  });

  // Same rule on a phone, where it used to be suspended: the panel went
  // full-bleed under 640px, so a box whose widest kind measures ~250px took
  // the whole of a 360px portrait screen and none of the map was left beside
  // it. All three kinds are checked because all three open the one panel.
  test('a portrait phone keeps the panel sized to its rows', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    await gotoMap(page);

    const box = () => page.locator('#inspector').boundingBox();
    const open = [
      // Not clickEdge: edgePoint projects at the desktop zoom, and fitBounds
      // lands a 360px-wide map somewhere else. This is what the click handler
      // itself calls.
      ['street', () => page.evaluate(() => {
        const layer = geoLayer.getLayers().find((l) => l._filteredCount > 0);
        selectEdge(layer, layer.feature.properties.rides, layer.getBounds().getCenter());
      })],
      ['dock', async () => {
        await page.locator('#cb-check').check();
        await page.evaluate(() => selectDock(0));
      }],
      ['area', async () => {
        await page.locator('#nb-check').check();
        await page.evaluate(() => selectArea(0));
      }],
    ];

    for (const [kind, show] of open) {
      await show();
      await expect(page.locator('#inspector')).toBeVisible();
      // The panel slides in, so its box is 8px off until the transition ends.
      await page.locator('#inspector')
        .evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
      const b = await box();
      expect(b.width, `${kind} is under the cap`).toBeLessThanOrEqual(272);
      expect(b.width, `${kind} leaves map beside it`).toBeLessThan(360 - 24);
      expect(b.x, `${kind} keeps its left margin`).toBe(12);
      // Anchored to the bottom, clear of the attribution, and never more than
      // 45vh of a screen that has to show the feature as well.
      expect(b.height).toBeLessThanOrEqual(780 * 0.45);
      expect(b.y + b.height).toBeLessThanOrEqual(780 - 28);
      await page.locator('#inspector-close').click();
    }
  });

  // On a phone the two rails stack into one column, so #stats staying at full
  // height while the inspector opens crowds the map exactly as much as its
  // width would suggest -- the same "one thing open" #stats-sections already
  // does between its own sections.
  test('on a phone the inspector collapses stats and restores it on close', async ({ page }) => {
    await page.setViewportSize({ width: 430, height: 932 });
    await gotoMap(page);
    const stats = page.locator('#stats');
    await expect(stats).not.toHaveClass(/collapsed/);

    await clickEdge(page, EDGES.center.lat);
    await expect(stats).toHaveClass(/collapsed/);
    await page.locator('#inspector-close').click();
    await expect(stats).not.toHaveClass(/collapsed/);

    // A reader's own collapse is a different thing and outlives the panel.
    await page.locator('#stats-toggle').click();
    await expect(stats).toHaveClass(/collapsed/);
    await clickEdge(page, EDGES.center.lat);
    await expect(stats).toHaveClass(/collapsed/);
    await page.locator('#inspector-close').click();
    await expect(stats).toHaveClass(/collapsed/);
    await page.locator('#stats-toggle').click();  // back open, for the next case

    // Never happens on a desktop viewport: the two rails have their own
    // columns and neither needs to give way to the other.
    await page.setViewportSize({ width: 1280, height: 800 });
    await clickEdge(page, EDGES.center.lat);
    await expect(stats).not.toHaveClass(/collapsed/);
  });

  test('the close button, Escape and a click on empty map each close it', async ({ page }) => {
    await gotoMap(page);
    const panel = page.locator('#inspector');

    await clickEdge(page, EDGES.center.lat);
    await expect(panel).toBeVisible();
    await page.locator('#inspector-close').click();
    await expect(panel).toBeHidden();

    await clickEdge(page, EDGES.center.lat);
    await expect(panel).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();

    await clickEdge(page, EDGES.center.lat);
    await expect(panel).toBeVisible();
    await page.mouse.click(900, 600);   // map, but no street there
    await expect(panel).toBeHidden();
  });

  test('the clicked street is highlighted, and released on close', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    // Canvas-rendered, so the style is read off the layer rather than the DOM.
    // Hang on to the layer so it can be re-read after it is deselected.
    expect(await page.evaluate(() => {
      window._sel = selectedEdge;
      return selectedEdge.options.color;
    })).toBe('#fff');

    await page.locator('#inspector-close').click();
    expect(await page.evaluate(() => selectedEdge)).toBe(null);
    // Back on the plasma ramp, not left white.
    expect(await page.evaluate(() => window._sel.options.color)).not.toBe('#fff');
  });

  test('a street behind the rail is panned clear of it', async ({ page }) => {
    await gotoMap(page);
    const pt = await edgePoint(page, EDGES.center.lat);
    // Slide the street under the rail, then click it there.
    await page.evaluate(() => map.panBy([420, 0], { animate: false }));
    const before = await page.evaluate(() => map.getCenter().lng);

    await page.mouse.click(pt.x - 420, pt.y);
    await expect(page.locator('#inspector')).toBeVisible();

    // Panned west, which walks the street back out from behind the panel.
    const after = await page.evaluate(() => map.getCenter().lng);
    expect(after).toBeLessThan(before);
  });

  test('a street already in the clear is not panned', async ({ page }) => {
    await gotoMap(page);
    const before = await page.evaluate(() => [map.getCenter().lat, map.getCenter().lng]);
    await clickEdge(page, EDGES.center.lat);
    await expect(page.locator('#inspector')).toBeVisible();
    expect(await page.evaluate(() => [map.getCenter().lat, map.getCenter().lng]))
      .toEqual(before);
  });

  test('the open panel follows the date filter', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await expect(page.locator('#inspector .edge-sub')).toHaveText('4 passes');
    await expect(page.locator('#inspector .ride-row')).toHaveCount(4);

    // Four ride dates; pull the upper handle down to the first, leaving
    // 2023-04-01.
    await setRangeHi(page, 0);

    // The name is fixed; the count under it is what the filter moves.
    await expect(page.locator('#inspector-title')).toHaveText('Center Street');
    await expect(page.locator('#inspector .edge-sub')).toHaveText('1 pass');
    await expect(page.locator('#inspector .ride-row')).toHaveCount(1);
    await expect(page.locator('#inspector .ride-row')).toContainText('2023-04-01');
  });

  test('a street with nothing left in range says so instead of emptying', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.south.lat);   // ride 3 only, 2024-07-04
    await expect(page.locator('#inspector .ride-row')).toHaveCount(1);

    await setRangeHi(page, 0);                 // 2023-04-01 only

    await expect(page.locator('#inspector')).toBeVisible();
    await expect(page.locator('#inspector-title')).toHaveText('No passes in range');
    await expect(page.locator('#inspector .inspector-empty')).toBeVisible();
  });

  // A named street keeps its name when the filter empties it: the name is the
  // street, not the passes, so it is the count under it that gives way.
  test('an emptied street keeps its name as the heading', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.north.lat);   // rides 0 and 1, 2023 only
    await expect(page.locator('#inspector-title')).toHaveText('North Street');

    await setRangeLo(page, 2);                 // 2024 onwards

    await expect(page.locator('#inspector-title')).toHaveText('North Street');
    await expect(page.locator('#inspector .edge-sub')).toHaveText('No passes in range');
    await expect(page.locator('#inspector .inspector-empty')).toBeVisible();
  });
});
