import { test, expect, gotoMap, openSection, clickEdge } from './helpers.js';
import { buildFixture, EDGES } from './fixture.js';

// The date index: a month grid per year that opens into the rides in a month.
// The fixture's four rides are 2023-04-01 (Sat), 2023-06-15 (Thu),
// 2024-05-01 (Wed) and 2024-07-04 (Thu), one to a month, so every cell it
// shades holds exactly one ride.
const cell = (page, ym) => page.locator(`#stat-calendar .cal-cell[data-month="${ym}"]`);

test.describe('ride calendar', () => {
  test('draws a row per year and shades only the months with rides', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-calendar');

    // A blank corner over the year labels, then the twelve month initials.
    const head = page.locator('#stat-calendar .cal-head span');
    await expect(head).toHaveCount(13);
    await expect(head.nth(1)).toHaveText('J');
    await expect(head.nth(12)).toHaveText('D');
    await expect(head.nth(1)).toHaveAttribute('title', 'January');

    const rows = page.locator('#stat-calendar .cal-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0).locator('.cal-year')).toHaveText('2023');
    await expect(rows.nth(1).locator('.cal-year')).toHaveText('2024');
    await expect(page.locator('#stat-calendar .cal-cell')).toHaveCount(24);

    // Four rides, four months, and nothing clickable in the other twenty.
    await expect(page.locator('#stat-calendar .cal-cell.has')).toHaveCount(4);
    for (const ym of ['2023-04', '2023-06', '2024-05', '2024-07']) {
      await expect(cell(page, ym)).toHaveClass(/\bhas\b/);
    }
    // 10 km is 6 mi; an empty month says so rather than saying nothing.
    await expect(cell(page, '2023-04'))
      .toHaveAttribute('title', 'April 2023 · 1 ride · 6 mi');
    await expect(rows.nth(0).locator('.cal-cell').nth(0))
      .toHaveAttribute('title', 'January 2023 · no rides');
  });

  test('the busiest month sits at the top of the ramp', async ({ page }) => {
    // Four rides in July 2024 against one in June 2023, so the shade of the
    // quiet month lands on a palette stop exactly: sqrt(1/4) is 0.5, and
    // 0.2 + 0.8 * 0.5 is 0.6, which is the ramp's [220, 75, 85].
    await gotoMap(page, buildFixture({
      dates: ['2023-06-15', '2024-07-01', '2024-07-04', '2024-07-11', '2024-07-18'],
      rides: [
        [0, '18:05', 25.0, -1],
        [1, '10:00', 10.0, -1],
        [2, '14:45', 40.0, -1],
        [3, '10:00', 10.0, -1],
        [4, '10:00', 10.0, -1],
      ],
    }));
    await openSection(page, 'stat-calendar');

    await expect(cell(page, '2024-07'))
      .toHaveAttribute('title', 'July 2024 · 4 rides · 43 mi'); // 70 km
    await expect(cell(page, '2024-07')).toHaveCSS('background-color', 'rgb(240, 249, 33)');
    await expect(cell(page, '2023-06')).toHaveCSS('background-color', 'rgb(220, 75, 85)');
  });

  test('a month opens into its rides, and a row puts one on the map', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-calendar');

    const detail = page.locator('#stat-calendar .cal-detail[data-year="2024"]');
    await expect(detail).toBeHidden();

    await cell(page, '2024-07').click();
    await expect(detail).toBeVisible();
    await expect(detail.locator('.cal-month')).toHaveText('July 2024 · 1 ride · 25 mi');

    const rides = detail.locator('.cal-ride');
    await expect(rides).toHaveCount(1);
    // The month is on the row above, so the ride names the day and the clock.
    await expect(rides.nth(0).locator('.yd-link')).toHaveText('Thu 4 · 2:45pm · 2 Citibike trips');
    await expect(rides.nth(0).locator('.v')).toHaveText('25 mi'); // 40 km

    await rides.nth(0).locator('.yd-link').click();
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    await expect(page.locator('#ride-view-label'))
      .toHaveText('2024-07-04 2:45pm · 2 Citibike trips');
  });

  test('a ride with no Citibike evidence is left untagged', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-calendar');

    // Source -1 is unknown, which is not own bike, so it carries no tag.
    await cell(page, '2023-04').click();
    const row = page.locator('#stat-calendar .cal-detail[data-year="2023"] .cal-ride');
    await expect(row.locator('.yd-link')).toHaveText('Sat 1 · 8:30am');
    await expect(row.locator('.src-tag')).toHaveCount(0);

    // 0 is own bike and says so.
    await cell(page, '2024-05').click();
    await expect(page.locator('#stat-calendar .cal-detail[data-year="2024"] .yd-link'))
      .toHaveText('Wed 1 · 9:15am · own bike');
  });

  test('one month is open at a time, across years', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-calendar');
    const y23 = page.locator('#stat-calendar .cal-detail[data-year="2023"]');
    const y24 = page.locator('#stat-calendar .cal-detail[data-year="2024"]');

    await expect(cell(page, '2023-04')).toHaveAttribute('aria-expanded', 'false');
    await cell(page, '2023-04').click();
    await expect(y23).toBeVisible();
    await expect(cell(page, '2023-04')).toHaveClass(/\bopen\b/);
    await expect(cell(page, '2023-04')).toHaveAttribute('aria-expanded', 'true');

    await cell(page, '2024-07').click();
    await expect(y23).toBeHidden();
    await expect(y24).toBeVisible();
    await expect(cell(page, '2023-04')).not.toHaveClass(/\bopen\b/);
    await expect(cell(page, '2023-04')).toHaveAttribute('aria-expanded', 'false');

    // A second click on the open month closes it.
    await cell(page, '2024-07').click();
    await expect(y24).toBeHidden();
    await expect(cell(page, '2024-07')).not.toHaveClass(/\bopen\b/);
  });

  test('a cell opens from the keyboard', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-calendar');
    await cell(page, '2023-06').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#stat-calendar .cal-detail[data-year="2023"] .cal-month'))
      .toHaveText('June 2023 · 1 ride · 16 mi'); // 25 km
  });

  test('the row and its month read their state from ride view', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-calendar');
    await cell(page, '2024-07').click();
    const link = page.locator('#stat-calendar .cal-detail[data-year="2024"] .yd-link');

    await expect(link).not.toHaveClass(/\bon\b/);
    await link.click();
    await expect(link).toHaveClass(/\bon\b/);
    await expect(cell(page, '2024-07')).toHaveClass(/\bon\b/);

    // Left by the exit button, not by the link: the highlight is ride view's.
    await page.locator('#ride-view-exit').click();
    await expect(link).not.toHaveClass(/\bon\b/);
    await expect(cell(page, '2024-07')).not.toHaveClass(/\bon\b/);

    // And a second click on the row it came from puts the map back.
    await link.click();
    await expect(page.locator('#ride-view-bar')).toBeVisible();
    await link.click();
    await expect(page.locator('#ride-view-bar')).toBeHidden();
    await expect(cell(page, '2024-07')).not.toHaveClass(/\bon\b/);
  });

  test('a ride reached from a street marks its month', async ({ page }) => {
    await gotoMap(page);
    // The south street is ride 3 twice, so its one row is 2024-07-04.
    await clickEdge(page, EDGES.south.lat);
    await page.locator('.ride-popup .ride-row').first().click();
    await expect(page.locator('#ride-view-bar')).toBeVisible();

    await openSection(page, 'stat-calendar');
    await expect(cell(page, '2024-07')).toHaveClass(/\bon\b/);
    await expect(cell(page, '2024-05')).not.toHaveClass(/\bon\b/);
  });

  test('the calendar is all-time, and opens a ride the filter excludes', async ({ page }) => {
    await gotoMap(page);
    // Filter 2024 out of the map entirely.
    await page.locator('#range-hi').focus();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#legend-max')).toHaveText('2');

    await openSection(page, 'stat-calendar');
    await expect(page.locator('#stat-calendar .cal-cell.has')).toHaveCount(4);
    await expect(cell(page, '2024-07'))
      .toHaveAttribute('title', 'July 2024 · 1 ride · 25 mi');

    await cell(page, '2024-07').click();
    await page.locator('#stat-calendar .cal-detail[data-year="2024"] .yd-link').click();
    await expect(page.locator('#ride-view-label'))
      .toHaveText('2024-07-04 2:45pm · 2 Citibike trips');
  });

  test('a year with no rides keeps its row', async ({ page }) => {
    // The gap is the thing worth seeing, so the years run end to end.
    await gotoMap(page, buildFixture({
      dates: ['2023-04-01', '2026-01-05'],
      rides: [[0, '08:30', 10.0, -1], [1, '14:45', 40.0, -1]],
    }));
    await openSection(page, 'stat-calendar');

    const rows = page.locator('#stat-calendar .cal-row');
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0).locator('.cal-year')).toHaveText('2023');
    await expect(rows.nth(3).locator('.cal-year')).toHaveText('2026');
    await expect(page.locator('#stat-calendar .cal-cell.has')).toHaveCount(2);
  });

  test('no chip without rides', async ({ page }) => {
    await gotoMap(page, buildFixture({ dates: [], rides: [] }));
    await expect(page.locator('#stat-chips .chip[data-section="stat-calendar"]'))
      .toHaveClass(/\bhidden\b/);
  });
});
