import { test, expect, gotoMap, hoverEdge, clickEdge, edgePoint } from './helpers.js';
import { buildFixture, EDGES } from './fixture.js';

test.describe('street tooltips and detail', () => {
  test('hovering a street shows its pass count', async ({ page }) => {
    await gotoMap(page);
    const tooltip = await hoverEdge(page, EDGES.center.lat);
    await expect(tooltip).toHaveText('4 passes');

    // One ride, ridden both ways: two passes, not one.
    const single = await hoverEdge(page, EDGES.south.lat);
    await expect(single).toHaveText('2 passes');
  });

  test('clicking a street opens the inspector listing each ride', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await expect(page.locator('#inspector')).toBeVisible();
    // The features ship no street name, so the count is the heading.
    await expect(page.locator('#inspector-title')).toHaveText('4 passes');
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
    await expect(page.locator('#inspector-title')).toHaveText('2 passes across 1 ride');
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

test.describe('inspector panel', () => {
  // The panel replaced a Leaflet popup that opened on top of the feature it
  // described. What these assert is the reason for the change: it is off the
  // map, it says which feature it is about, and it stays true while the map
  // keeps being used.

  test('it docks at the left edge without covering the legend', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const panel = await page.locator('#inspector').boundingBox();
    const legend = await page.locator('#legend').boundingBox();
    const view = page.viewportSize();

    expect(panel.x).toBeLessThan(40);                     // hard against the left
    expect(panel.x + panel.width).toBeLessThan(view.width / 3);
    expect(panel.y + panel.height).toBeLessThanOrEqual(legend.y + 1);
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
    await expect(page.locator('#inspector-title')).toHaveText('4 passes');
    await expect(page.locator('#inspector .ride-row')).toHaveCount(4);

    // Four ride dates; pull the upper handle down to the first, leaving
    // 2023-04-01.
    await setRangeHi(page, 0);

    await expect(page.locator('#inspector-title')).toHaveText('1 pass');
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
});
