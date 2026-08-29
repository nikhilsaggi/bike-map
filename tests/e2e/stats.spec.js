import { test, expect, gotoMap, chip, openSection } from './helpers.js';
import { buildFixture } from './fixture.js';

test.describe('stats panel', () => {
  test('shows hero totals from the geojson properties', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#stat-rides')).toHaveText('4');
    // 87.5 km ridden -> miles, rounded
    await expect(page.locator('#stat-ridden')).toHaveText('54');
    // 100 km of drawn street -> miles, rounded
    await expect(page.locator('#stat-km')).toHaveText('62');
    await expect(page.locator('#stat-coverage')).toHaveText('12.3%');
    await expect(page.locator('#stat-updated')).toHaveText('2026-07-01');
  });

  test('hero tooltips carry the denominators the numbers omit', async ({ page }) => {
    await gotoMap(page);
    // total_edges lost its own row; it survives here.
    await expect(page.locator('#tile-km')).toHaveAttribute('title', '3 drawn street segments');
    // 12.3% is measured over a different subset than the 62 drawn miles, so
    // the tooltip has to spell out what it is a percentage of.
    await expect(page.locator('#tile-coverage'))
      .toHaveAttribute('title', /28 of 230 mi of rideable NYC street/);
  });

  test('shows per-year ride counts with distance and new-street miles', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    const rows = page.locator('#stat-years .year-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText('2023');
    await expect(rows.nth(0)).toContainText('2');
    await expect(rows.nth(0)).toContainText('22 mi'); // 35 km
    await expect(rows.nth(0)).toContainText('+19 new'); // 30 km
    await expect(rows.nth(1)).toContainText('2024');
    await expect(rows.nth(1)).toContainText('33 mi'); // 52.5 km
    await expect(rows.nth(1)).toContainText('+10 new'); // 15.5 km
  });

  test('shows the riding summary with hour and weekday histograms', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-riding');
    const riding = page.locator('#stat-riding');
    const rows = riding.locator('.r-row');
    await expect(rows.nth(0)).toContainText('Distance');
    await expect(rows.nth(0)).toContainText('54 mi'); // 87.5 km
    await expect(rows.nth(1)).toContainText('Time');
    await expect(rows.nth(1)).toContainText('8 h');
    await expect(rows.nth(2)).toContainText('Avg speed');
    await expect(rows.nth(2)).toContainText('6.8 mph'); // 10.9 km/h
    await expect(rows.nth(3)).toContainText('Longest ride');
    await expect(rows.nth(3)).toContainText('25 mi'); // 40 km
    await expect(riding.locator('.histo').nth(0).locator('div')).toHaveCount(24);
    await expect(riding.locator('.histo').nth(1).locator('div')).toHaveCount(7);
  });

  test('shows weather ride-probability bars', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-weather');
    const weather = page.locator('#stat-weather');
    await expect(weather).toContainText('Ride days by temperature');
    await expect(weather).toContainText('Ride days by precipitation');
    const rows = weather.locator('.weather-bar-row');
    await expect(rows).toHaveCount(5); // 3 temp bands + 2 rain bands
    await expect(rows.nth(0)).toContainText('<40°F');
    await expect(rows.nth(0).locator('.weather-bar-val')).toHaveText('10%');
    await expect(rows.nth(1).locator('.weather-bar-fill')).toHaveAttribute('style', /width:35\.5%/);
  });

  test('opens one section at a time', async ({ page }) => {
    await gotoMap(page);
    // Nothing is open on a first visit: the panel is a header plus chips.
    await expect(page.locator('#stats-sections')).toBeHidden();
    await expect(page.locator('#stat-chips .chip.active')).toHaveCount(0);

    await openSection(page, 'stat-riding');
    await expect(page.locator('#stat-chips .chip.active')).toHaveCount(1);
    await expect(chip(page, 'stat-riding')).toHaveAttribute('aria-expanded', 'true');

    await openSection(page, 'stat-weather');
    await expect(page.locator('#stat-riding')).toBeHidden();
    await expect(page.locator('#stat-chips .chip.active')).toHaveCount(1);
    await expect(chip(page, 'stat-riding')).toHaveAttribute('aria-expanded', 'false');
  });

  test('clicking the open section chip closes it', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-years');
    await chip(page, 'stat-years').click();
    await expect(page.locator('#stat-years')).toBeHidden();
    await expect(page.locator('#stats-sections')).toBeHidden();
    await expect(chip(page, 'stat-years')).toHaveAttribute('aria-expanded', 'false');
  });

  test('the open section persists across reload', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-weather');
    await page.reload();
    await expect(page.locator('#stat-rides')).not.toHaveText('—');
    await expect(page.locator('#stat-weather')).toBeVisible();
    await expect(chip(page, 'stat-weather')).toHaveClass(/active/);
  });

  test('a remembered section whose data is gone opens nothing', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, 'stat-weather');
    await gotoMap(page, buildFixture({ weather: null }));
    await expect(page.locator('#stats-sections')).toBeHidden();
    await expect(page.locator('#stat-chips .chip.active')).toHaveCount(0);
  });

  test('the panel is shorter with nothing open', async ({ page }) => {
    await gotoMap(page);
    const height = () => page.locator('#stats').evaluate((el) => el.getBoundingClientRect().height);
    const closed = await height();
    await openSection(page, 'stat-riding');
    expect(await height()).toBeGreaterThan(closed);
  });

  test('legend shows the max ride count and a gradient bar', async ({ page }) => {
    await gotoMap(page);
    await expect(page.locator('#legend-max')).toHaveText('4');
    await expect(page.locator('#legend .labels span').first()).toHaveText('1');
    const bg = await page.locator('#legend-bar').evaluate((el) => el.style.background);
    expect(bg).toContain('linear-gradient');
  });

  test('collapse toggle hides the body and persists across reload', async ({ page }) => {
    await gotoMap(page);
    const stats = page.locator('#stats');
    const body = page.locator('#stats-body');
    await expect(body).toBeVisible();

    await page.locator('#stats-toggle').click();
    await expect(stats).toHaveClass(/collapsed/);
    await expect(body).toBeHidden();
    await expect(page.locator('#stats-collapsed-label')).toBeVisible();
    await expect(page.locator('#stats-toggle')).toHaveText('+');

    await page.reload();
    await expect(page.locator('#stat-rides')).not.toHaveText('—');
    await expect(stats).toHaveClass(/collapsed/);
    await expect(body).toBeHidden();

    await page.locator('#stats-toggle').click();
    await expect(stats).not.toHaveClass(/collapsed/);
    await expect(body).toBeVisible();
    await expect(page.locator('#stats-toggle')).toHaveText('–');
  });

  test('optional sections and their chips stay hidden when data is absent', async ({ page }) => {
    await gotoMap(
      page,
      buildFixture({
        total_rides: 1,
        total_edges: 3,
        total_km: null,
        rides_per_year: {},
        riding: null,
        coverage: null,
        weather: null,
        dates: ['2023-04-01'],
        rides: [[0, '08:30', 10.0]],
      }),
    );
    await expect(page.locator('#stat-rides')).toHaveText('1');
    // Hero tiles hide one by one rather than leaving an empty cell.
    await expect(page.locator('#tile-ridden')).toBeHidden();
    await expect(page.locator('#tile-coverage')).toBeHidden();

    for (const section of ['stat-years', 'stat-riding', 'stat-weather']) {
      await expect(page.locator(`#${section}`)).toBeHidden();
      await expect(chip(page, section)).toBeHidden();
    }
    await expect(page.locator('#wrapped-btn')).toBeHidden();
    // fewer than 2 distinct dates -> no date filter UI
    await expect(page.locator('#filter-row')).toBeHidden();
    await expect(page.locator('#filter-dates')).toBeHidden();
  });

  test('shows an error message when the data fails to load', async ({ page }) => {
    await page.route('**/rides.geojson.gz', (route) => route.abort());
    await page.goto('/');
    await expect(page.locator('#stat-rides')).toHaveText('Error loading data');
  });
});
