---
name: flight-price-monitor
description: Set up a persistent background monitor that scrapes round-trip flight prices from Trip.com and Google Flights, filters by arrival-time windows / same-day arrival / excluded layover airports / checked-baggage requirements, verifies the fare is actually bookable on the order page, and sends a loud desktop notification when the real price drops to a target. Use when someone asks to watch, track, or be alerted about airfare for specific dates and a route — "监控机票", "盯着票价", "watch flights", "alert me when tickets drop to X".
---

# Flight price monitor

Builds a self-contained monitor in a directory of the user's choosing, installs it as a
macOS launchd agent, and alerts when a **verified bookable** fare hits the target.

## The core problem this solves

A price on a search-results page is frequently not a price you can book. Two traps,
both confirmed by real scraping on this codebase:

1. **Bait pricing across sites.** Google Flights shows an OTA fare (Agoda, etc.) that
   disappears when you click through to the order page.
2. **Partial baggage inside one site.** Trip.com's list shows `Checked baggage included`
   when only *one* leg carries a bag. Measured case: list said HK$3,451 with the badge;
   the fare panel showed `1 × 23 kg (departure), None (return)`, and the cheapest fare
   with a bag on *both* legs was HK$3,800 — 10% higher.

So the monitor **never trusts a list price**. For Trip.com it clicks through to the
`Select Fare` panel and reads the per-direction baggage line. Only a fare that panel
confirms counts as `verified`, and **only verified fares fire the notification**.
Google Flights results are scraped and reported, but marked `未验证` and never alert,
because Google hands off to third-party OTAs whose prices it cannot guarantee.

## Setting one up

1. **Collect requirements.** Ask only for what you cannot infer. You need:
   - Route (IATA for Google, plus Trip.com's own lowercase city code — `sel` covers all
     Seoul airports; get it from the `dcity`/`acity` params of a manual Trip.com search)
   - Candidate outbound and return dates, and whether a fixed trip length is wanted
     (`TRIP_NIGHTS = 4` means five-days-four-nights and narrows the date cross-product)
   - Arrival-time constraints. Listen carefully: "下午就可以抵达" is an *upper* bound
     (arrive by afternoon), not a requirement to arrive in the afternoon. Getting this
     backwards changes the answer by hundreds. Ask if ambiguous.
   - Whether red-eyes are acceptable, and any layover airports to exclude
   - Target price, currency, and whether a checked bag is required

2. **Install.** Copy `scripts/` into the working directory, write `config.py` from
   `config_template.py`, then run `bash setup.sh`. It creates the venv, installs
   Playwright + Chromium (~95 MB), writes and loads the launchd plist, and symlinks a
   `flightmon` control command into `~/bin`.

3. **Verify before declaring success.** Run `./.venv/bin/python monitor.py` once and read
   the output. A run that reports `符合条件 0 班` for every combination means the filters
   are wrong or a site changed — investigate, do not report success. `probe3.py` prints
   which rule rejected each flight; `probe4.py` dumps the full Google list.

4. **Hand over the control command**, not raw `launchctl` incantations:
   `flightmon` (status) · `stop` · `start` · `off` · `now` · `log`

## Adapting to other routes

Everything route-specific lives in `config.py`. Two things need real care:

- `EXCLUDE_LAYOVER_AIRPORTS` (Google, IATA codes) and `EXCLUDE_LAYOVER_CITIES`
  (Trip.com, city names) are **separate lists** because the two sites label layovers
  differently. Update both.
- `BAGGAGE_FEE` is only used for the Google path, is route-specific, and is mostly
  estimated. Re-check the numbers for a new route, or treat Google's totals as
  indicative only.

## Reporting results to the user

Quote prices in the user's own currency, converting if a source publishes in another.
Always say which figures were verified on an order page and which were not — and never
present a scraped list price as if it were the bookable price.

Read `references/gotchas.md` before modifying the scrapers. It records the specific
failure modes already hit and fixed; several are silent and cost hours to rediscover.
