import { test, expect, gotoMap, edgePoint, openSection } from './helpers.js';
import { buildFixture, BIKE_LANE_BLOCK, LANE_LAT, LANES } from './fixture.js';

const withLanes = () => buildFixture({ bike_lanes: BIKE_LANE_BLOCK });

/** Indices into BIKE_LANE_BLOCK.lines of the lanes on the map, ascending. */
const drawnLanes = (page) =>
  page.evaluate(() =>
    blLines.map((l, i) => (map.hasLayer(l) ? i : -1)).filter((i) => i >= 0));

/** The stroke colour of every street currently on the map. */
const strokes = (page) =>
  page.evaluate(() => {
    const out = [];
    geoLayer.eachLayer((l) => {
      if (map.hasLayer(l)) out.push(l.options.color);
    });
    return out;
  });

/** Pull the slider's later handle back `n` dates from the last. */
async function stepBack(page, n) {
  await page.locator('#range-hi').focus();
  for (let i = 0; i < n; i++) await page.keyboard.press('ArrowLeft');
}

async function clickLane(page, lng) {
  const pt = await edgePoint(page, LANE_LAT, lng);
  await page.mouse.click(pt.x, pt.y);
}

test.describe('bike lane layer', () => {
  test('the chip stays hidden until the payload carries the block', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#bl-toggle')).toHaveClass(/hidden/);
  });

  test('the chip appears with the block and starts off', async ({ page }) => {
    await gotoMap(page, withLanes());
    await expect(page.locator('#bl-toggle')).not.toHaveClass(/hidden/);
    await expect(page.locator('#bl-check')).not.toBeChecked();
    await expect(page.locator('#bl-key')).toBeHidden();
    expect(await page.evaluate(() => bikeLanesOn())).toBe(false);
  });

  test('on, it draws the lanes standing on the later date and ghosts the network',
    async ({ page }) => {
      await gotoMap(page, withLanes());
      const lit = await strokes(page);
      await page.locator('#bl-check').check();
      // Kent Avenue and the protected Grand Street; the painted lane it
      // replaced is gone by 2024-07-04.
      expect(await drawnLanes(page)).toEqual([0, 2]);
      await expect.poll(async () => (await strokes(page)).every((c) => c === '#555')).toBe(true);
      await expect(page.locator('#bl-key')).toBeVisible();
      await expect(page.locator('#bl-ridden')).toHaveText('1.0 of 1.0 mi ridden by 2024-07-04');

      await page.locator('#bl-check').uncheck();
      expect(await drawnLanes(page)).toEqual([]);
      await expect(page.locator('#bl-key')).toBeHidden();
      await expect.poll(async () => strokes(page)).toEqual(lit);
    });

  test('the slider swaps a lane for the one it replaced, and dims what was not yet ridden',
    async ({ page }) => {
      await gotoMap(page, withLanes());
      await page.locator('#bl-check').check();
      await stepBack(page, 2);
      await expect(page.locator('#date-hi')).toHaveText('2023-06-15');
      await expect.poll(() => drawnLanes(page)).toEqual([0, 1]);
      // Kent Avenue ridden by date 1, the painted Grand Street never.
      await expect(page.locator('#bl-ridden')).toHaveText('0.5 of 1.0 mi ridden by 2023-06-15');
      const opacity = (i) => page.evaluate((i) => blLines[i].options.opacity, i);
      expect(await opacity(0)).toBe(0.95);
      expect(await opacity(1)).toBe(0.22);

      // One more back and Kent Avenue had not been ridden yet either.
      await page.keyboard.press('ArrowLeft');
      await expect(page.locator('#bl-ridden')).toHaveText('0.0 of 1.0 mi ridden by 2023-04-01');
      expect(await opacity(0)).toBe(0.22);
    });

  test('with the pass frequency off every lane is drawn alike', async ({ page }) => {
    await gotoMap(page, withLanes());
    await page.locator('#bl-check').check();
    await stepBack(page, 3);
    await expect(page.locator('#date-hi')).toHaveText('2023-04-01');
    const opacities = () => page.evaluate(() =>
      blLines.filter((l) => map.hasLayer(l)).map((l) => l.options.opacity));
    // Nothing ridden yet by the first date: both lanes dim.
    expect(await opacities()).toEqual([0.22, 0.22]);

    await page.locator('#rf-check').uncheck();
    await expect.poll(opacities).toEqual([0.95, 0.95]);
    await expect(page.locator('#bl-ridden')).toHaveText('1.0 mi of lanes on 2023-04-01');

    await page.locator('#rf-check').check();
    await expect.poll(opacities).toEqual([0.22, 0.22]);
    await expect(page.locator('#bl-ridden')).toHaveText('0.0 of 1.0 mi ridden by 2023-04-01');
  });

  test('the key wraps inside the legend rather than widening it', async ({ page }) => {
    await gotoMap(page, withLanes());
    const width = () => page.locator('#legend').evaluate((el) => el.getBoundingClientRect().width);
    const before = await width();
    await page.locator('#bl-check').check();
    await expect(page.locator('#bl-key')).toBeVisible();
    expect(await width()).toBe(before);
  });

  test('a lane opens its panel, with what it replaced', async ({ page }) => {
    await gotoMap(page, withLanes());
    await page.locator('#bl-check').check();
    await clickLane(page, LANES.grand);
    const panel = page.locator('#inspector');
    await expect(panel).toBeVisible();
    await expect(page.locator('#inspector-title')).toHaveText('Grand Street');
    await expect(panel).toContainText('Protected or off-street');
    await expect(panel).toContainText('Protected, one way');
    await expect(panel).toContainText('BuiltMay 2024');
    await expect(panel).toContainText('Riddenby Jul 2024');
    const before = page.locator('.bl-hist .bl-row');
    await expect(before).toHaveText(['Conventional2010–2024', 'Signed Route1999–2010']);
    expect(await page.evaluate(() => blSelected)).toBe(2);
  });

  test('a replaced lane says so, and one never ridden says that', async ({ page }) => {
    await gotoMap(page, withLanes());
    await page.locator('#bl-check').check();
    await stepBack(page, 2);
    await expect.poll(() => drawnLanes(page)).toEqual([0, 1]);
    await clickLane(page, LANES.grand);
    const panel = page.locator('#inspector');
    await expect(panel).toContainText('Painted lane');
    await expect(panel).toContainText('Conventional, one way');
    await expect(panel).toContainText('ReplacedMay 2024');
    await expect(panel).toContainText('RiddenNot yet');
  });

  test('Escape closes the panel, and the layer going off takes its panel with it',
    async ({ page }) => {
      await gotoMap(page, withLanes());
      await page.locator('#bl-check').check();
      await clickLane(page, LANES.kent);
      await expect(page.locator('#inspector-title')).toHaveText('Kent Avenue');
      await page.keyboard.press('Escape');
      await expect(page.locator('#inspector')).toBeHidden();
      expect(await page.evaluate(() => blSelected)).toBe(null);

      await clickLane(page, LANES.kent);
      await expect(page.locator('#inspector')).toBeVisible();
      await page.locator('#bl-check').uncheck();
      await expect(page.locator('#inspector')).toBeHidden();
    });

  test('the Years table gains an On lane column, with protected in its title',
    async ({ page }) => {
      await gotoMap(page, withLanes());
      await openSection(page, 'stat-years');
      await expect(page.locator('#stat-years .year-head span').last()).toHaveText('On lane');
      const cells = page.locator('#stat-years .year-row .y-lane');
      await expect(cells).toHaveText(['70%', '50%']);
      await expect(cells.nth(1)).toHaveAttribute('title',
        "50% of 2024's NYC riding on a bike lane standing at the time, 30% on a protected one");
    });

  test('the Riding section gives the all-time shares', async ({ page }) => {
    await gotoMap(page, withLanes());
    await openSection(page, 'stat-riding');
    const rows = page.locator('#stat-riding .r-row');
    await expect(rows.filter({ hasText: 'On a bike lane' })).toContainText('60%');
    await expect(rows.filter({ hasText: 'On a protected lane' })).toContainText('40%');
  });

  test('without the lane block neither appears', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    await expect(page.locator('#stat-years .y-lane')).toHaveCount(0);
    await expect(page.locator('#stat-years .year-head span')).toHaveCount(5);
    await openSection(page, 'stat-riding');
    await expect(page.locator('#stat-riding')).not.toContainText('bike lane');
  });
});
