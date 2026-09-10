import { test, expect, gotoMap, chip, openSection } from './helpers.js';
import { buildFixture, SPEED_BLOCK } from './fixture.js';

const STREETS = 'stat-streets';

// One list, three rankings: Fastest and Slowest rank a stretch against the
// network, Faster one way ranks it against its own opposite direction.
// Fastest is what a reader lands on, so every corridor assertion asks for
// its tab first.
const tab = (page, label) => page.locator('#speed-tabs .seg-btn', { hasText: label }).click();

// The fixture ships three corridors, which is short enough to fit. A ranking
// long enough to overflow is built from it rather than hand-written, so the
// row shape stays the one every other test in this file reads.
function longSpeedBlock(n) {
  const corridors = Array.from({ length: n }, (_, i) => ({
    ...SPEED_BLOCK.corridors[i % SPEED_BLOCK.corridors.length],
    name: `Street ${i + 1}`,
    at: [-73.99 + i * 0.002, 40.7405],
  }));
  return { ...SPEED_BLOCK, corridors };
}

// Facts about the network rather than about the rides: the totals that used
// to live only in hero tooltips, and the direction-split ranking.
test.describe('streets', () => {
  test('section is closed until its chip is clicked', async ({ page }) => {
    await gotoMap(page);
    await expect(chip(page, STREETS)).toBeVisible();
    await expect(page.locator('#stat-streets')).toBeHidden();
    await expect(chip(page, STREETS)).toHaveAttribute('aria-expanded', 'false');

    await openSection(page, STREETS);
    await expect(page.locator('#speed-list')).toBeVisible();
    await expect(chip(page, STREETS)).toHaveAttribute('aria-expanded', 'true');

    await chip(page, STREETS).click();
    await expect(page.locator('#stat-streets')).toBeHidden();
  });

  test('shows the network totals', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    const rows = page.locator('#streets-totals .r-row');
    await expect(rows).toHaveCount(5);
    await expect(rows.nth(0)).toContainText('Drawn');
    await expect(rows.nth(0)).toContainText('62 mi'); // 100 km
    await expect(rows.nth(1)).toContainText('Segments');
    await expect(rows.nth(1)).toContainText('3');
    // Both denominators, narrower first: the city, then the box that runs
    // past it. 6,600 of 30,000 m against coverage.pct's 12.3%.
    await expect(rows.nth(2)).toContainText('Of rideable NYC');
    await expect(rows.nth(2)).toContainText('22.0%');
    await expect(rows.nth(3)).toContainText('Of the whole box');
    await expect(rows.nth(3)).toContainText('12.3%');
    await expect(rows.nth(4)).toContainText('Most-ridden segment');
    await expect(rows.nth(4)).toContainText('4×');
    // The count says how often; the name says what.
    await expect(page.locator('#streets-totals .r-link')).toHaveText('Center Street');
  });

  test('the most-ridden segment can be shown on the map and cleared', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    const link = page.locator('#streets-totals .r-link');

    await link.click();
    await page.waitForTimeout(900);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    await expect(link).toHaveClass(/\bon\b/);
    expect(await page.evaluate(() => map.getCenter().lat)).toBeCloseTo(40.745, 2);

    await link.click();
    expect(await page.evaluate(() => speedMarker !== null)).toBe(false);
    await expect(link).not.toHaveClass(/\bon\b/);
  });

  test('the segment link and a corridor row share one marker', async ({ page }) => {
    // Both place the same circle, so selecting one must release the other.
    await gotoMap(page);
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    await page.locator('#streets-totals .r-link').click();
    await page.waitForTimeout(900);
    await page.locator('#speed-list .sp-row').first().click();
    await page.waitForTimeout(900);
    await expect(page.locator('#streets-totals .r-link')).not.toHaveClass(/\bon\b/);
    await expect(page.locator('#speed-list .sp-row.on')).toHaveCount(1);
  });

  test('an unnamed top segment leaves the count on its own', async ({ page }) => {
    await gotoMap(page, buildFixture({ top_segment: { name: null, at: [-73.96, 40.745] } }));
    await openSection(page, STREETS);
    await expect(page.locator('#streets-totals .r-row').nth(4)).toContainText('4×');
    await expect(page.locator('#streets-totals .r-link')).toHaveCount(0);
  });

  test('the coverage percentage qualifies itself without a second mileage', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    // 12.3% is measured over a narrower network than the 62 drawn miles, so
    // neither side of it may appear next to them: 28 mi ridden read as
    // contradicting the drawn miles, and 230 mi recovers it by multiplying.
    // The exclusions define "rideable" and carry no arithmetic, so they stay.
    await expect(page.locator('#streets-totals .r-sub').first())
      .toHaveText('sidewalks, service roads and motorways excluded');
  });

  test('the exclusion caption follows the export, not hardcoded prose', async ({ page }) => {
    // It used to claim paths were excluded when sidewalks are. The wording is
    // built from coverage.excluded_km so editing COVERAGE_EXCLUDE moves it.
    await gotoMap(page, buildFixture({
      coverage: {
        pct: 12.3, ridden_km: 45.5, network_km: 370,
        new_km_by_year: { 2023: 30.0 },
        excluded_km: { motorway: 90.0, footway: 5.0 },
      },
    }));
    await openSection(page, STREETS);
    await expect(page.locator('#streets-totals .r-sub').first())
      .toHaveText('motorways and sidewalks excluded');
  });

  test('an unlabelled exclusion tag renders as itself', async ({ page }) => {
    // A tag added to COVERAGE_EXCLUDE with no label must show up, not vanish.
    await gotoMap(page, buildFixture({
      coverage: {
        pct: 12.3, ridden_km: 45.5, network_km: 370,
        new_km_by_year: { 2023: 30.0 },
        excluded_km: { bus_guideway: 90.0 },
      },
    }));
    await openSection(page, STREETS);
    await expect(page.locator('#streets-totals .r-sub').first())
      .toHaveText('bus guideway excluded');
  });

  test('coverage with no exclusions gets no caption at all', async ({ page }) => {
    await gotoMap(page, buildFixture({
      coverage: { pct: 12.3, ridden_km: 45.5, network_km: 370, new_km_by_year: {} },
    }));
    await openSection(page, STREETS);
    // Nothing to qualify and no mileage to fall back on, so the coverage row
    // is bare. The two remaining sub-lines belong to the wider denominator
    // and to the most-ridden segment.
    const subs = page.locator('#streets-totals .r-sub');
    await expect(subs).toHaveCount(2);
    await expect(subs.nth(0)).toHaveText('which reaches past the city');
    await expect(subs.locator('.r-link')).toHaveCount(1);
  });

  test('lists corridors in rank order with mph converted from km/h', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');

    const names = await page.locator('#speed-list .sp-name').allTextContents();
    expect(names).toEqual(['Crest Bridge', 'Crest Bridge', 'Flat Street']);

    // 16.09 km/h -> 10.0 mph, 8.05 -> 5.0, 3.22 -> 2.0, each with its unit.
    const gaps = await page.locator('#speed-list .sp-gap').allTextContents();
    expect(gaps).toEqual(['10.0mph', '5.0mph', '2.0mph']);

    const dirs = await page.locator('#speed-list .sp-dir').allTextContents();
    expect(dirs).toEqual(['E', 'W', 'N']);
  });

  test('each row shows both directions, length, and pass count', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    // 24.14 km/h -> 15.0 mph, 8.05 -> 5.0; 800 m -> 0.50 mi.
    await expect(page.locator('#speed-list .sp-detail').first())
      .toHaveText('15.0 vs 5.0 mph over 0.50 mi, 12+ passes each way');
  });

  // The rule the rows are ranked by is on the heading's (?), the same
  // affordance the Citibike tab's charts use -- not three lines of caption
  // spent every time the tab is open.
  test('the ranking says how many stretches it was drawn from', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    // speed.measured was exported and rendered nowhere before.
    const help = page.locator('#speed-title .cb-help');
    await expect(help).toHaveAttribute('title', /Of 42 stretches measured/);
    await expect(help).toHaveAttribute('title', /820 ft\+, ridden 3\+ times each way/);
    // On the (?) and nowhere else: the section itself no longer carries it.
    await expect(page.locator('#stat-streets')).not.toContainText('stretches measured');
  });

  // Ten stretches at two lines each outgrow the stats section, so the ranking
  // is capped and scrolls inside itself -- the same treatment the Citibike
  // tab's re-encounter list gets. Without it the Network rows above scrolled
  // away with it and the tab became one long column.
  test('a long ranking scrolls inside its own box', async ({ page }) => {
    await gotoMap(page, buildFixture({ speed: longSpeedBlock(10) }));
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    await expect(page.locator('#speed-list .sp-row')).toHaveCount(10);

    const box = await page.locator('#speed-list').evaluate(el => ({
      client: el.clientHeight,
      scroll: el.scrollHeight,
      overflow: getComputedStyle(el).overflowY,
    }));
    expect(box.client).toBeLessThanOrEqual(150);
    expect(box.scroll).toBeGreaterThan(box.client);
    expect(box.overflow).toBe('auto');
  });

  // The heading carries the rule, so it sits outside the box: scrolling to the
  // tenth stretch must not scroll away what the numbers on it mean.
  test('the ranking heading stays put while the rows scroll', async ({ page }) => {
    await gotoMap(page, buildFixture({ speed: longSpeedBlock(10) }));
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    const outside = await page.evaluate(() =>
      !document.getElementById('speed-list').contains(document.getElementById('speed-title')));
    expect(outside).toBe(true);

    const before = await page.locator('#speed-title').boundingBox();
    await page.locator('#speed-list').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(page.locator('#speed-title .cb-help'))
      .toHaveAttribute('title', /Of 42 stretches measured/);
    const after = await page.locator('#speed-title').boundingBox();
    expect(after.y).toBeCloseTo(before.y, 0);
  });

  // A row below the fold is still a control: it scrolls into reach and marks
  // itself like any other.
  test('a row below the fold still opens its corridor', async ({ page }) => {
    await gotoMap(page, buildFixture({ speed: longSpeedBlock(10) }));
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    await page.locator('#speed-list .sp-row').nth(9).click();
    await expect(page.locator('#speed-list .sp-row.on')).toHaveCount(1);
    await expect(page.locator('#speed-list .sp-row').nth(9)).toHaveClass(/on/);
  });

  test('the same bridge appears once per direction, never as one row', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    const rows = page.locator('#speed-list .sp-row').filter({ hasText: 'Crest Bridge' });
    await expect(rows).toHaveCount(2);
    // Opposite directions: the crest flip, not a duplicate of one stretch.
    await expect(rows.nth(0).locator('.sp-dir')).toHaveText('E');
    await expect(rows.nth(1).locator('.sp-dir')).toHaveText('W');
  });

  test('clicking a row flies to the corridor and marks it', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    const before = await page.evaluate(() => map.getZoom());
    await page.locator('#speed-list .sp-row').first().click();
    await page.waitForTimeout(900);
    const after = await page.evaluate(() => ({
      zoom: map.getZoom(),
      center: map.getCenter(),
      marked: speedMarker !== null,
    }));
    expect(after.marked).toBe(true);
    expect(after.zoom).toBeGreaterThanOrEqual(before);
    expect(after.center.lat).toBeCloseTo(SPEED_BLOCK.corridors[0].at[1], 2);
    expect(after.center.lng).toBeCloseTo(SPEED_BLOCK.corridors[0].at[0], 2);
  });

  test('clicking the marked row again takes the circle back off', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    const row = page.locator('#speed-list .sp-row').first();

    await row.click();
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    await expect(row).toHaveClass(/\bon\b/);

    await row.click();
    expect(await page.evaluate(() => speedMarker !== null)).toBe(false);
    await expect(row).not.toHaveClass(/\bon\b/);
  });

  test('clicking a different row moves the circle rather than toggling', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await tab(page, 'Faster one way');
    const rows = page.locator('#speed-list .sp-row');

    await rows.nth(0).click();
    await page.waitForTimeout(700);
    await rows.nth(1).click();
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    // Exactly one row is ever marked.
    await expect(page.locator('#speed-list .sp-row.on')).toHaveCount(1);
    await expect(rows.nth(1)).toHaveClass(/\bon\b/);
    expect(await page.evaluate(() => map.getCenter().lat))
      .toBeCloseTo(SPEED_BLOCK.corridors[1].at[1], 2);
  });

  test('closing the section clears the corridor marker', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await page.locator('#speed-list .sp-row').first().click();
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    await chip(page, STREETS).click();
    expect(await page.evaluate(() => speedMarker !== null)).toBe(false);
    await expect(page.locator('#speed-list .sp-row.on')).toHaveCount(0);
  });

  test('switching to another section clears the corridor marker', async ({ page }) => {
    // The ring belongs to the corridor list; leaving it behind would strand a
    // cyan circle on the map with nothing on screen explaining it.
    await gotoMap(page);
    await openSection(page, STREETS);
    await page.locator('#speed-list .sp-row').first().click();
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    await openSection(page, 'stat-weather');
    expect(await page.evaluate(() => speedMarker !== null)).toBe(false);
    await expect(page.locator('#speed-list .sp-row.on')).toHaveCount(0);
  });

  test('every section fits a short viewport', async ({ page }) => {
    // Stacking all the sections at once pushed the panel past 700px, so on a
    // short window the last one fell off the bottom. One section at a time
    // plus a bounded section height is what keeps this true.
    await page.setViewportSize({ width: 1280, height: 560 });
    await gotoMap(page);

    for (const section of ['stat-years', 'stat-riding', 'stat-weather', STREETS, 'stat-citibike']) {
      await openSection(page, section);
      const fits = await page.evaluate(() => {
        const s = document.getElementById('stats').getBoundingClientRect();
        return s.bottom <= window.innerHeight;
      });
      expect(fits, `#${section} keeps the panel on screen`).toBe(true);
      // ... and off the legend below it: they share the right rail, so the
      // open section gives up height rather than the two overlapping.
      const clears = await page.evaluate(() => {
        const s = document.getElementById('stats').getBoundingClientRect();
        const l = document.getElementById('legend').getBoundingClientRect();
        return s.bottom <= l.top + 1;
      });
      expect(clears, `#${section} keeps the panel off the legend`).toBe(true);
      // Both the way in and the way out stay reachable without scrolling.
      await expect(page.locator('#stat-chips')).toBeInViewport();
      await expect(page.locator('#stats-toggle')).toBeInViewport();
    }
  });

  test('the ranking drops out but the totals keep the section', async ({ page }) => {
    await gotoMap(page, buildFixture({ speed: null }));
    await expect(chip(page, STREETS)).toBeVisible();
    await openSection(page, STREETS);
    await expect(page.locator('#speed-block')).toBeHidden();
    await expect(page.locator('#streets-totals .r-row')).toHaveCount(5);
  });

  test('an empty block drops the ranking, tabs and all', async ({ page }) => {
    await gotoMap(page, buildFixture({
      speed: {
        corridors: [], fastest: [], slowest: [],
        measured: 0, split_n: 3, min_m: 250.0, stretch_n: 5,
      },
    }));
    await openSection(page, STREETS);
    await expect(page.locator('#speed-block')).toBeHidden();
  });

  test('a tab with nothing in it is not offered', async ({ page }) => {
    // The corridor ranking needs passes both ways and can come back empty on
    // rides that never doubled back; the pace ranking still has something to
    // say, so the block stays with two tabs rather than three.
    await gotoMap(page, buildFixture({ speed: { ...SPEED_BLOCK, corridors: [] } }));
    await openSection(page, STREETS);
    expect(await page.locator('#speed-tabs .seg-btn').allTextContents())
      .toEqual(['Fastest', 'Slowest']);
  });

  test('chip stays hidden when there is nothing about streets to show', async ({ page }) => {
    await gotoMap(page, buildFixture({
      speed: null, total_km: null, total_edges: null, coverage: null,
      max_count: null, top_segment: null,
    }));
    await expect(chip(page, STREETS)).toBeHidden();
    await expect(page.locator('#stat-streets')).toBeHidden();
  });
});

// The two rankings that ask about a stretch on its own: absolute speed, one
// direction, which is the only question a one-way street can answer. They are
// what a reader lands on, so the corridor ranking is a tab away rather than
// the other way round.
test.describe('stretch pace', () => {
  test('lands on the fastest ranking, with the corridor list a tab away',
    async ({ page }) => {
      await gotoMap(page);
      await openSection(page, STREETS);
      expect(await page.locator('#speed-tabs .seg-btn').allTextContents())
        .toEqual(['Fastest', 'Slowest', 'Faster one way']);
      await expect(page.locator('#speed-tabs .seg-btn').first()).toHaveClass(/\bon\b/);
      expect(await page.locator('#speed-list .sp-name').allTextContents())
        .toEqual(['Steady Street', 'Gusty Street']);
    });

  test('lists the fastest stretches, mph and swing converted from km/h', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    expect(await page.locator('#speed-list .sp-dir').allTextContents()).toEqual(['E', 'N']);
    // 1.61 km/h -> 1.0 mph, 400 m -> 0.25 mi.
    await expect(page.locator('#speed-list .sp-detail').first())
      .toHaveText('\u00b11.0 mph swing over 0.25 mi, 9+ passes');
  });

  test('the number on the row carries its unit and is the average it ranks by',
    async ({ page }) => {
      // 24.14 km/h -> 15.0 mph on both, which is why the fixture's two rows
      // are separated by the swing alone -- the tie-break, not the ranking.
      await gotoMap(page);
      await openSection(page, STREETS);
      expect(await page.locator('#speed-list .sp-gap').allTextContents())
        .toEqual(['15.0mph', '15.0mph']);

      await tab(page, 'Slowest');
      // 8.05 km/h -> 5.0, then 24.14 -> 15.0: ascending, as printed.
      expect(await page.locator('#speed-list .sp-gap').allTextContents())
        .toEqual(['5.0mph', '15.0mph']);
    });

  test('the slowest tab reads the other array, not a re-sort of this one',
    async ({ page }) => {
      await gotoMap(page);
      await openSection(page, STREETS);
      await tab(page, 'Slowest');
      expect(await page.locator('#speed-list .sp-name').allTextContents())
        .toEqual(['Slow Lane', 'Gusty Street']);
      // 250 m -> 0.16 mi: a stretch the fastest tab never showed.
      await expect(page.locator('#speed-list .sp-detail').first())
        .toHaveText('\u00b11.0 mph swing over 0.16 mi, 5+ passes');
      await expect(page.locator('#speed-tabs .seg-btn').first()).not.toHaveClass(/\bon\b/);
    });

  test('a stretch row places the one marker the corridor rows use', async ({ page }) => {
    await gotoMap(page);
    await openSection(page, STREETS);
    await page.locator('#speed-list .sp-row').first().click();
    await page.waitForTimeout(900);
    expect(await page.evaluate(() => speedMarker !== null)).toBe(true);
    expect(await page.evaluate(() => map.getCenter().lat)).toBeCloseTo(40.7305, 2);

    await tab(page, 'Faster one way');
    await page.locator('#speed-list .sp-row').first().click();
    await page.waitForTimeout(900);
    await expect(page.locator('.sp-row.on')).toHaveCount(1);
  });

  test('switching tabs takes the circle off with the row that placed it',
    async ({ page }) => {
      await gotoMap(page);
      await openSection(page, STREETS);
      await page.locator('#speed-list .sp-row').first().click();
      await page.waitForTimeout(900);
      expect(await page.evaluate(() => speedMarker !== null)).toBe(true);

      await tab(page, 'Slowest');
      expect(await page.evaluate(() => speedMarker !== null)).toBe(false);
      await expect(page.locator('.sp-row.on')).toHaveCount(0);
    });

  test('the rule on the (?) changes with the tab', async ({ page }) => {
    // Three tabs, three questions: one help text would have to describe the
    // ranking a reader is not looking at.
    await gotoMap(page);
    await openSection(page, STREETS);
    const help = page.locator('#speed-title .cb-help');
    await expect(help).toHaveAttribute('title', /The fastest stretches/);
    await expect(help).toHaveAttribute('title', /820 ft\+, ridden 5\+ times/);
    await expect(help).toHaveAttribute('title', /waiting at lights included/);

    await tab(page, 'Slowest');
    await expect(help).toHaveAttribute('title', /The slowest stretches/);

    await tab(page, 'Faster one way');
    await expect(help).toHaveAttribute('title', /Of 42 stretches measured/);
    // One mark, not one per render.
    await expect(page.locator('#speed-title .cb-help')).toHaveCount(1);
  });
});
