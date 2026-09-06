import { test, expect, gotoMap, hoverEdge, clickEdge } from './helpers.js';
import { buildFixture, EDGES } from './fixture.js';

test.describe('street tooltips and popups', () => {
  test('hovering a street shows its pass count', async ({ page }) => {
    await gotoMap(page);
    const tooltip = await hoverEdge(page, EDGES.center.lat);
    await expect(tooltip).toHaveText('4 passes');

    // One ride, ridden both ways: two passes, not one.
    const single = await hoverEdge(page, EDGES.south.lat);
    await expect(single).toHaveText('2 passes');
  });

  test('clicking a street opens a popup listing each ride', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.ride-popup');
    await expect(popup).toBeVisible();
    await expect(popup).toContainText('4 passes');
    const rows = popup.locator('.ride-row');
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
    const rows = page.locator('.ride-popup .ride-row');
    await expect(rows.nth(0)).toContainText('12:05am');
    await expect(rows.nth(1)).toContainText('12:00pm');
    await expect(rows.nth(2)).toContainText('9:15am');
    await expect(rows.nth(3)).toContainText('11:59pm');
  });

  test('a repeated ride is one row marked with its pass count', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.south.lat);
    const popup = page.locator('.ride-popup');
    await expect(popup).toContainText('2 passes across 1 ride');
    const rows = popup.locator('.ride-row');
    await expect(rows).toHaveCount(1);
    await expect(rows.nth(0)).toHaveText('2024-07-04 · 2:45pm ×2 · 2 Citibike trips');
  });
});

test.describe('single-ride view', () => {
  test('clicking a popup ride row enters ride view; Escape exits', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await page.locator('.ride-popup .ride-row').first().click();

    const bar = page.locator('#ride-view-bar');
    await expect(bar).toBeVisible();
    await expect(page.locator('#ride-view-label')).toHaveText('2023-04-01 8:30am');
    // popup closes when entering ride view
    await expect(page.locator('.ride-popup')).toHaveCount(0);

    await page.keyboard.press('Escape');
    await expect(bar).toBeHidden();
  });

  test('the exit button leaves ride view', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await page.locator('.ride-popup .ride-row').last().click();
    await expect(page.locator('#ride-view-label')).toHaveText('2024-07-04 2:45pm · 2 Citibike trips');

    await page.locator('#ride-view-exit').click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
  });
});

test.describe('draggable popups', () => {
  // The same bar is on every popup -- the street popup here and the dock
  // popup in citibike.spec.js are the same Leaflet container.
  async function dragBar(page, dx, dy) {
    const bar = page.locator('.leaflet-popup .popup-drag');
    const box = await bar.boundingBox();
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx, y + dy, { steps: 5 });
    await page.mouse.up();
  }

  test('the grab bar moves a popup off the street it covers', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.leaflet-popup');
    await expect(popup).toBeVisible();
    const before = await popup.boundingBox();

    await dragBar(page, 160, -90);

    const after = await popup.boundingBox();
    expect(Math.round(after.x - before.x)).toBe(160);
    expect(Math.round(after.y - before.y)).toBe(-90);
    // Moved, not reopened: the rides it was showing are still listed, and
    // the tip is gone because it would point at nothing.
    await expect(popup.locator('.ride-row')).toHaveCount(4);
    await expect(popup.locator('.leaflet-popup-tip-container')).toBeHidden();
  });

  test('a moved popup still travels with its street', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.leaflet-popup');
    await dragBar(page, 120, -60);
    const before = await popup.boundingBox();

    await page.evaluate(() => map.panBy([70, 40], { animate: false }));

    const after = await popup.boundingBox();
    expect(Math.round(after.x - before.x)).toBe(-70);
    expect(Math.round(after.y - before.y)).toBe(-40);
  });

  test('the popup cannot be dragged off the map', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.leaflet-popup');
    await dragBar(page, -3000, 3000);
    const box = await popup.boundingBox();
    const view = page.viewportSize();
    expect(box.x + box.width).toBeGreaterThan(0);
    expect(box.y).toBeLessThan(view.height);
    // The bar itself stays reachable, so the popup can be dragged back.
    await expect(page.locator('.leaflet-popup .popup-drag')).toBeInViewport();
  });

  test('the next popup opens back on its street', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.leaflet-popup');
    const home = await popup.boundingBox();
    await dragBar(page, 150, 80);
    await page.locator('.leaflet-popup-close-button').click();
    await expect(popup).toHaveCount(0);

    await clickEdge(page, EDGES.center.lat);
    const again = await popup.boundingBox();
    expect(Math.round(again.x)).toBe(Math.round(home.x));
    expect(Math.round(again.y)).toBe(Math.round(home.y));
    await expect(popup.locator('.leaflet-popup-tip-container')).toBeVisible();
  });
});
