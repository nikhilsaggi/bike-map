// Synthetic rides.geojson payload matching the schema produced by
// bike_routes/export.py:_export_geojson. Small enough that every number the
// UI derives from it can be asserted exactly.
//
// Geometry: three horizontal street segments near the map's initial center
// (40.735, -73.96) at zoom 13, so each is on screen and clickable at a
// pixel position computable from lat/lng (see edgePoint in helpers.js).

export const CENTER = { lat: 40.735, lng: -73.96 }; // map initial view
// A feature's rides array holds one entry per traversal, so a ride that came
// back the same way appears twice and the count the page draws is passes.
export const EDGES = {
  // rides [0,1,2,3] -> 4 passes (the hottest street, through screen center)
  center: { lat: 40.735, rides: [0, 1, 2, 3] },
  // rides [0,1] -> 2 passes, 2023 only
  north: { lat: 40.7395, rides: [0, 1] },
  // ride 3 twice (a round trip) -> 2 passes across 1 ride, 2024 only
  south: { lat: 40.7305, rides: [3, 3] },
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

// Citibike dock trips: dock-to-dock, no trace, so this block carries the
// trips themselves and never a route -- the page filters the dock markers by
// the same slider that filters the edges, and draws a dock's own trips only
// on click. Arithmetic is closed by hand: every trip has one departure and
// one arrival, so both columns sum to 5 (out 3 + 1 + 1, in 1 + 3 + 1).
//
// `days` deliberately straddles the ride dates: the first is also a ride day,
// 2025-01-01 is past the last one, so filtering the slider to 2023 must drop
// the later docks. Coordinates sit near the map centre so a marker is on
// screen; Ghost Dock has none, the way a renamed dock does.
//
// The fourth element of a trip is the ride recorded over it, or -1. It agrees
// with the `rides` array below: ride 1 is marked as covering one trip and
// covers exactly one here, ride 3 covers two, and the two rides no trip cites
// are the ones marked own-bike and unknown.
export const CITIBIKE_BLOCK = {
  trips: [
    [0, 1, 0, -1],  // Home -> Park, 2023-04-01, no recording
    [0, 1, 1, 1],   // Home -> Park, 2023-06-15, recorded as ride 1
    [0, 2, 3, -1],  // Home -> Terminal, 2025-01-01, no recording
    [1, 0, 2, 3],   // Park -> Home, 2024-07-04, recorded as ride 3
    [2, 3, 2, 3],   // Terminal -> Ghost, 2024-07-04, the same recording again
  ],
  days: ['2023-04-01', '2023-06-15', '2024-07-04', '2025-01-01'],
  docks: [
    { name: 'Home Dock & Main St', at: [-73.955, 40.7325], out: 3, in: 1 },
    { name: 'Park Dock & 5 Ave', at: [-73.965, 40.7375], out: 1, in: 3 },
    { name: 'Terminal Dock & 42 St', at: [-73.95, 40.7425], out: 1, in: 1 },
    { name: 'Ghost Dock & Gone St', at: null, out: 0, in: 1 },
  ],
  hours: 2.0,
  from: '2023-04-01',
  to: '2025-01-01',
  same_day: 2,
  median_min: 9.0,
  ebike_min: 3,
  reencounters: 2,
  resumes: 5,
  // Hand-built rather than measured: `again` needs more meetings than this
  // fixture has trips (CHANCE_MIN_MEETINGS in citibike.py), and what the page
  // has to get right is where the two dots land. 48.0 sits mid-band and 1.2
  // sits outside it -- the shape of the real answer. 4.828 km is exactly
  // 3.0 mi, so the miles column cannot pass by a rounding accident.
  again: {
    where: { obs: 4.828, chance: 4.828, lo: 3.219, hi: 6.437, pct: 48.0, n: 20 },
    when: { obs: 300, chance: 420, lo: 360, hi: 480, pct: 1.2, n: 22 },
  },
  once_only: 0,
  // The own-bike column: rides no Citibike trip was found under.
  own: { rides: 3, hours: 4.0, days: 2, median_min: 41.0 },
};

// Neighbourhoods: two areas splitting the three streets between them, so
// every number the layer draws is hand-computable. Uptown Heights holds the
// north street (lat 40.7395), Downtown Flats the centre and south ones --
// a feature is placed by its middle coordinate, which for a two-point line
// is its east end, so the boundary at 40.7375 decides all three.
//
// `new` is [date index, metres first ridden that day]: Downtown gains 1,250 m
// on date 0 and 3,750 more on date 2, Uptown 1,600 m on date 1. The shares
// are perfect squares because the fill runs on their square root -- 5,000 of
// 20,000 is 25% and so 0.5 of the ramp, 1,600 of 10,000 is 16% and so 0.4,
// and pulling the upper handle back to date 0 leaves Downtown at 1,250 of
// 20,000, which is 6.25% and 0.25 of the ramp.
export const NEIGHBORHOOD_BLOCK = {
  source: 'NYC Neighborhood Tabulation Areas 2020 (9nt8-h7nd)',
  net_m: 30000,
  ridden_m: 6600,
  areas: [
    {
      name: 'Downtown Flats',
      boro: 'Bk',
      net_m: 20000,
      ridden_m: 5000,
      new: [[0, 1250], [2, 3750]],
      // polygon -> ring -> point, Leaflet's multi-polygon nesting.
      rings: [[[
        [-74.0, 40.725], [-73.9, 40.725], [-73.9, 40.7375], [-74.0, 40.7375], [-74.0, 40.725],
      ]]],
    },
    {
      name: 'Uptown Heights',
      boro: 'Mn',
      net_m: 10000,
      ridden_m: 1600,
      new: [[1, 1600]],
      rings: [[[
        [-74.0, 40.7375], [-73.9, 40.7375], [-73.9, 40.745], [-74.0, 40.745], [-74.0, 40.7375],
      ]]],
    },
  ],
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
      // [date_index, "HH:MM", distance_km, source]
      // source: -1 outside the Citibike history, 0 own bike, n>=1 the number
      // of Citibike trips the ride overlaps. One of each, plus a two-trip
      // ride, so every branch of the tag and the filter has a case.
      rides: [
        [0, '08:30', 10.0, -1],
        [1, '18:05', 25.0, 1],
        [2, '09:15', 12.5, 0],
        [3, '14:45', 40.0, 2],
      ],
      updated: '2026-07-01',
      // The busiest drawn feature, named. Placed on the north edge so a click
      // is checkable against a known centre.
      top_segment: { name: 'Center Street', at: [-73.96, 40.745] },
      speed: SPEED_BLOCK,
      citibike: CITIBIKE_BLOCK,
      neighborhoods: NEIGHBORHOOD_BLOCK,
      ...propertyOverrides,
    },
    // Sorted by ride count ascending, like the exporter.
    features: [
      { type: 'Feature', geometry: line(EDGES.south.lat), properties: { rides: EDGES.south.rides, n: 0 } },
      { type: 'Feature', geometry: line(EDGES.north.lat), properties: { rides: EDGES.north.rides, n: 1 } },
      { type: 'Feature', geometry: line(EDGES.center.lat), properties: { rides: EDGES.center.rides, n: 0 } },
    ],
  };
}
