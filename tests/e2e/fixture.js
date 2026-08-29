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

// sp = [fwd_dkmh, fwd_n, rev_dkmh, rev_n], tenths of km/h, "forward" being
// along each feature's own coordinate order (west -> east here).
// center: 20.0 / 10.0 km/h -> 12.4 / 6.2 mph, both directions over threshold.
// north:  15.0 km/h eastbound only; westbound measured once but unpublished.
// south:  no sp at all -> renders as "not enough data".
export const SPEEDS = {
  center: [200, 6, 100, 4],
  north: [150, 3, 0, 1],
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
      },
      weather: {
        temp: [
          { label: '<40°F', pct: 10.0, avg_mi: 5.0 },
          { label: '40–60°F', pct: 35.5, avg_mi: 9.1 },
          { label: '>60°F', pct: 60.0, avg_mi: 12.0 },
        ],
        rain: [
          { label: 'dry', pct: 50.0, avg_mi: 10.0 },
          { label: 'wet', pct: 12.0, avg_mi: 6.0 },
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
      speed: { lo: 8.0, med: 15.0, hi: 22.0, n: 3, split_n: 3 },
      ...propertyOverrides,
    },
    // Sorted by ride count ascending, like the exporter.
    features: [
      { type: 'Feature', geometry: line(EDGES.south.lat), properties: { rides: EDGES.south.rides } },
      {
        type: 'Feature',
        geometry: line(EDGES.north.lat),
        properties: { rides: EDGES.north.rides, sp: SPEEDS.north },
      },
      {
        type: 'Feature',
        geometry: line(EDGES.center.lat),
        properties: { rides: EDGES.center.rides, sp: SPEEDS.center },
      },
    ],
  };
}
