# Garmin Access

How ride ingest authenticates, and what to do when it stops working.
`bike_routes/ingest/garmin_sync.py` is the only thing here that talks to
Garmin; the rest of the pipeline never leaves the machine.

## Why there is no API key

Garmin has no personal-use API — the Connect Developer Program only accepts
legal entities — so `garmin_sync` uses the endpoints the Connect web UI uses,
via [python-garminconnect](https://github.com/cyberjunky/python-garminconnect).
Logging in once leaves a token behind:

```bash
pip install '.[garmin]'
python -c "
from garminconnect import Garmin
Garmin(input('email: '), input('password: '),
       prompt_mfa=lambda: input('MFA code: ')).login('~/.garminconnect')
"
```

After that `garmin_sync` reads `~/.garminconnect` on its own. The token is
good for about a year; when it expires the script fails loudly with a re-mint
message rather than silently fetching nothing. To keep tokens elsewhere, set
`GARMINTOKENS` to a path — or, if you ever do want this in CI, to the token
JSON itself.

Rides are saved as `garmin_<activityId>.gpx`, so the activity id is the dedup
key and re-runs only fetch what's missing. Indoor and virtual rides are
skipped — they carry no usable GPS track.

## If the login returns 429 ("rate limited")

Garmin throttles its SSO endpoints per *account*, keyed on the account email
— so changing network or VPN does not help, and every retry re-arms the
block. Stop retrying, confirm you are on `garminconnect>=0.3.2` (earlier
releases lack the `widget+cffi` strategy, which is the one that gets through
while an account is throttled), and if all five strategies still 429, wait it
out — reports range from under an hour to about two days.
`logging.basicConfig(level=logging.DEBUG)` before the login shows which
strategies were actually tried.

## Why this runs locally, not in CI

The ride CSVs and the ~260 MB OSM graph cache already live on the owner's
machine, and Garmin's login sits behind Cloudflare TLS fingerprinting that
tends to block datacenter IPs. A previous Dropbox-based `update-map.yml`
workflow failed all 8 of its scheduled runs and was removed rather than
fixed.
