import { test, expect, gotoMap, hoverEdge, clickEdge } from './helpers.js';
import { EDGES } from './fixture.js';

test.describe('street tooltips and popups', () => {
  test('hovering a street shows its ride count', async ({ page }) => {
    await gotoMap(page);
    const tooltip = await hoverEdge(page, EDGES.center.lat);
    await expect(tooltip).toHaveText('4 rides');

    const single = await hoverEdge(page, EDGES.south.lat);
    await expect(single).toHaveText('1 ride');
  });

  test('clicking a street opens a popup listing each ride', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    const popup = page.locator('.ride-popup');
    await expect(popup).toBeVisible();
    await expect(popup).toContainText('4 rides');
    const rows = popup.locator('.ride-row');
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText('2023-04-01');
    await expect(rows.nth(0)).toContainText('08:30');
    await expect(rows.nth(3)).toContainText('2024-07-04');
    await expect(rows.nth(3)).toContainText('14:45');
  });
});

test.describe('single-ride view', () => {
  test('clicking a popup ride row enters ride view; Escape exits', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await page.locator('.ride-popup .ride-row').first().click();

    const bar = page.locator('#ride-view-bar');
    await expect(bar).toBeVisible();
    await expect(page.locator('#ride-view-label')).toHaveText('2023-04-01 08:30');
    // popup closes when entering ride view
    await expect(page.locator('.ride-popup')).toHaveCount(0);

    await page.keyboard.press('Escape');
    await expect(bar).toBeHidden();
  });

  test('the exit button leaves ride view', async ({ page }) => {
    await gotoMap(page);
    await clickEdge(page, EDGES.center.lat);
    await page.locator('.ride-popup .ride-row').last().click();
    await expect(page.locator('#ride-view-label')).toHaveText('2024-07-04 14:45');

    await page.locator('#ride-view-exit').click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
  });
});
