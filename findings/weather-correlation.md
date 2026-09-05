# Weather Correlation

`tools/weather_correlation.py` joins the exported ride index against
[Open-Meteo](https://open-meteo.com/) historical daily weather (keyless)
and reports how temperature and precipitation affect ride probability and
distance:

```bash
python tools/weather_correlation.py
```

Daily weather is cached in `cache/weather_cache.json`, so a failed API call
falls back to the last good copy.

## What it found

Temperature moves riding; rain barely does. Over the 1,819 days the map
covered as of the 2026-09-05 export (2021-09-04 to 2026-08-27), the share of
days with a ride climbs steadily with the high temperature, and the riding
days themselves get longer:

| Max temp | days with a ride | mi per riding day |
| --- | --- | --- |
| `<32°F` | 22% of 79 | 2.0 |
| `32-50°F` | 34% of 417 | 3.3 |
| `50-65°F` | 40% of 432 | 5.2 |
| `65-80°F` | 47% of 463 | 5.5 |
| `>80°F` | 52% of 428 | 7.1 |

Precipitation splits the same days almost evenly: 42% of dry days, 43% of
light-rain days, 44% of wet ones. The bands are whole-day totals from one
Central Park gauge, and a ride takes an hour — a wet day is mostly a day it
rained at some point, which says little about whether it was raining at 6pm.
What the daily total can still see is length: a wet riding day averages 4.0
miles against 5.9 dry.

## Why there is no chart here

The map's stats panel draws these bands live from `properties.weather`
(`bike_routes/weather.py`), filtered to whatever date range the slider is
showing, with a tick on each bar marking that band's share of all days. A
static two-panel PNG of the same percentages lived here until 2026-09-05;
it was a second copy to keep current, it went stale, and it could not be
narrowed to a season or a year the way the panel can.
