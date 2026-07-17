import { defineConfig } from '@playwright/test';

// E2E regression tests for the docs/ Leaflet map. Tests are hermetic: the
// rides.geojson.gz payload is a synthetic fixture (tests/e2e/fixture.js),
// Leaflet is served from node_modules, and basemap tiles are stubbed.
export default defineConfig({
  testDir: 'tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:8917',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'node tests/e2e/server.mjs',
    url: 'http://127.0.0.1:8917/index.html',
    reuseExistingServer: !process.env.CI,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
