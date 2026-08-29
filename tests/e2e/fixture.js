// Synthetic rides.geojson payload matching the schema produced by
// bike_routes/export.py:_export_geojson. Small enough that every number the
// UI derives from it can be asserted exactly.
//
// Geometry: three horizontal street segments near the map's initial center
// (40.735, -73.96) at zoom 13, so each is on screen and clickable at a
// pixel position computable from lat/lng (see edgePoint in helpers.js).

export const CENTER = { lat: 40.735, lng: -73.96 }; // map initial view
export const EDGES = {
  // rides [0,1,2,3] -> count 4 (the hottest street, through screen center)
  center: { lat: 40.735, rides: [0, 1, 2, 3] },
  // rides [0,1] -> 2023 only
  north: { lat: 40.7395, rides: [0, 1] },
  // ride [3] -> 2024 only
  south: { lat: 40.7305, rides: [3] },
};

// Direction-split speed corridors, ranked by the pipeline. Speeds are km/h
// (the page converts to mph): 24.14 km/h = 15.0 mph, 8.05 = 5.0, so the
// first row reads "15.0 vs 5.0" with a 10.0 mph gap -- all hand-computable.
export const SPEED_BLOCK = {
  corridors: [
    {
      name: 'Crest Bridge', gap: 16.09, fast: 24.14, slow: 8.05,
      dir: 'E', m: 800, n: 12, at: [-73.99, 40.7405],
    },
    {
      name: 'Crest Bridge', gap: 8.05, fast: 24.14, slow: 16.09,
      dir: 'W', m: 400, n: 9, at: [-73.98, 40.7405],
    },
    {
      name: 'Flat Street', gap: 3.22, fast: 19.31, slow: 16.09,
      dir: 'N', m: 250, n: 3, at: [-73.97, 40.7305],
    },
  ],
  measured: 42,
  split_n: 3,
  min_m: 250.0,
};

const line = (lat) => ({
  type: 'LineString',
  coordinates: [
    [-73.98, lat],
    [-73.94, lat],
  ],
});

// Dates: 2023-04-01 Sat, 2023-06-15 Thu, 2024-05-01 Wed, 2024-07-04 Thu.
export function buildFixture(propertyOverrides = {}) {
  return {
    type: 'FeatureCollection',
    properties: {
      total_rides: 4,
      total_edges: 3,
      max_count: 4,
      total_km: 100.0,
      rides_per_year: { 2023: 2, 2024: 2 },
      riding: {
        // hours 8, 18, 9, 14 / weekdays Sat, Thu, Wed, Thu (Mon-first)
        by_hour: [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0],
        by_weekday: [0, 0, 1, 2, 0, 1, 0],
        km_by_year: { 2023: 35.0, 2024: 52.5 },
        total_km: 87.5,
        total_h: 8.0,
        avg_kmh: 10.9,
        longest_km: 40.0,
      },
      coverage: {
        pct: 12.3,
        ridden_km: 45.5,
        network_km: 370,
        new_km_by_year: { 2023: 30.0, 2024: 15.5 },
        // Longest-first, as the export emits it; the caption names the top 3
        // and falls back to the raw tag for anything it has no label for.
        excluded_km: { footway: 120.0, service: 40.0, motorway: 12.0, funicular: 1.0 },
      },
      // share = this band's % of rides (bar), expected = its % of days
      // (tick). Temp is deliberately skewed -- >60°F takes 60% of rides on
      // 30% of days, ratio 2.0 -- and rain deliberately is not: both shares
      // sit within 5% of their expected, so the null-result note fires.
      weather: {
        temp: [
          { label: '<40°F', share: 10.0, expected: 30.0, pct: 8.0, days: 30, ride_days: 5, avg_mi: 5.0 },
          { label: '40–60°F', share: 30.0, expected: 40.0, pct: 18.8, days: 40, ride_days: 15, avg_mi: 9.1 },
          { label: '>60°F', share: 60.0, expected: 30.0, pct: 60.0, days: 30, ride_days: 30, avg_mi: 12.0 },
        ],
        rain: [
          { label: 'dry', share: 72.0, expected: 70.0, pct: 51.4, days: 70, ride_days: 36, avg_mi: 10.0 },
          { label: 'wet', share: 28.0, expected: 30.0, pct: 46.7, days: 30, ride_days: 14, avg_mi: 6.0 },
        ],
      },
      dates: ['2023-04-01', '2023-06-15', '2024-05-01', '2024-07-04'],
      // [date_index, "HH:MM", distance_km]
      rides: [
        [0, '08:30', 10.0],
        [1, '18:05', 25.0],
        [2, '09:15', 12.5],
        [3, '14:45', 40.0],
      ],
      updated: '2026-07-01',
      // The busiest drawn feature, named. Placed on the north edge so a click
      // is checkable against a known centre.
      top_segment: { name: 'Center Street', at: [-73.96, 40.745] },
      speed: SPEED_BLOCK,
      ...propertyOverrides,
    },
    // Sorted by ride count ascending, like the exporter.
    features: [
      { type: 'Feature', geometry: line(EDGES.south.lat), properties: { rides: EDGES.south.rides } },
      { type: 'Feature', geometry: line(EDGES.north.lat), properties: { rides: EDGES.north.rides } },
      { type: 'Feature', geometry: line(EDGES.center.lat), properties: { rides: EDGES.center.rides } },
    ],
  };
}
