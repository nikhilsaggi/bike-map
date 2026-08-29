import { test as base, expect } from '@playwright/test';
import { gzipSync } from 'node:zlib';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildFixture, CENTER } from './fixture.js';

const LEAFLET_DIST = fileURLToPath(new URL('../../node_modules/leaflet/dist', import.meta.url));

// 1x1 transparent PNG served for every basemap tile request.
const TILE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);

// Hermetic network: Leaflet comes from node_modules instead of unpkg, and
// tile requests get a blank PNG. The data route is installed by gotoMap.
export const test = base.extend({
  context: async ({ context }, use) => {
    await context.route('**/unpkg.com/leaflet@1.9.4/dist/leaflet.js', (route) =>
      route.fulfill({ path: join(LEAFLET_DIST, 'leaflet.js'), contentType: 'text/javascript' }),
    );
    await context.route('**/unpkg.com/leaflet@1.9.4/dist/leaflet.css', (route) =>
      route.fulfill({ path: join(LEAFLET_DIST, 'leaflet.css'), contentType: 'text/css' }),
    );
    await context.route('**/basemaps.cartocdn.com/**', (route) =>
      route.fulfill({ contentType: 'image/png', body: TILE_PNG }),
    );
    await use(context);
  },
});

export { expect };

/** Serve `data` as rides.geojson.gz, load the page, wait for it to render. */
export async function gotoMap(page, data = buildFixture()) {
  await page.route('**/rides.geojson.gz', (route) =>
    route.fulfill({ contentType: 'application/gzip', body: gzipSync(JSON.stringify(data)) }),
  );
  await page.goto('/');
  await expect(page.locator('#stat-rides')).not.toHaveText('—');
}

/**
 * Locator for a stats-panel section chip, and a click that opens its section.
 * The panel shows one section at a time, so every section assertion has to
 * open its own chip first.
 */
export function chip(page, section) {
  return page.locator(`#stat-chips .chip[data-section="${section}"]`);
}

export async function openSection(page, section) {
  await chip(page, section).click();
  await expect(page.locator(`#${section}`)).toBeVisible();
}

/**
 * Viewport pixel position of (lat, lng), valid while the map is at its
 * initial center/zoom. Uses Leaflet's own projection in the page, so tests
 * can click canvas-rendered streets without a handle on the map object.
 */
export async function edgePoint(page, lat, lng = CENTER.lng) {
  return page.evaluate(
    ([lat, lng, center]) => {
      const crs = L.CRS.EPSG3857;
      const zoom = 13;
      const p = crs.latLngToPoint(L.latLng(lat, lng), zoom);
      const c = crs.latLngToPoint(L.latLng(center.lat, center.lng), zoom);
      return {
        x: window.innerWidth / 2 + (p.x - c.x),
        y: window.innerHeight / 2 + (p.y - c.y),
      };
    },
    [lat, lng, CENTER],
  );
}

/**
 * Hover a street and return the tooltip locator (Leaflet shows it sticky).
 *
 * Two Leaflet canvas quirks require care: the hover handler drops mousemove
 * events within 32ms of the last processed one (lossy throttle, no trailing
 * call), and jumping straight from one street to another can leave the first
 * street's sticky tooltip orphaned. So detour through a neutral point until
 * the previous tooltip is gone, and nudge 1px after each stop so at least
 * one move is processed there.
 */
export async function hoverEdge(page, lat) {
  const pt = await edgePoint(page, lat);
  await page.mouse.move(200, 200);
  await page.waitForTimeout(50);
  await page.mouse.move(201, 200);
  await expect(page.locator('.leaflet-tooltip')).toHaveCount(0);
  await page.mouse.move(pt.x, pt.y);
  await page.waitForTimeout(50);
  await page.mouse.move(pt.x + 1, pt.y);
  return page.locator('.leaflet-tooltip');
}

/** Click a street to open its ride popup. */
export async function clickEdge(page, lat) {
  const pt = await edgePoint(page, lat);
  await page.mouse.click(pt.x, pt.y);
}
