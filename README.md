# flight-price-monitor

A [Claude Code](https://claude.com/claude-code) skill that builds a background monitor for
round-trip airfare and alerts you when a **fare you can actually book** hits your target.

Works for any route, any dates, any arrival-time constraints.

## Why not just use a price alert?

Because the price on a search-results page is often not the price you can buy. Two traps,
both measured on real data while building this:

**Bait pricing across sites.** Google Flights advertises a fare from a third-party OTA;
click through and it is gone. This tool scrapes Google, reports it, and marks it
`未验证` — and never alerts on it.

**Partial baggage inside one site.** Trip.com's results list shows
`Checked baggage included` when only *one* leg carries a bag:

```
List page:   HK$3,451   ✓ Checked baggage included
Fare panel:  HK$3,451   Checked baggage: 1 × 23 kg (departure), None (return)
             HK$3,800   Checked baggage: Included          ← the real both-legs price
```

So the monitor clicks through to the fare-selection panel and reads the per-direction
baggage line. Only a fare confirmed there counts as `已验证`, and **only verified fares
fire the alert**.

## What it filters on

- Arrival-time windows, separately for outbound and return
- Same-day arrival (rejects overnight `+1` itineraries)
- Excluded layover airports/cities — e.g. skip anything connecting in Taiwan
- A checked bag on **both** legs
- A preferred arrival window, reported alongside the outright cheapest

## Install

Drop the directory into `~/.claude/skills/`, then ask Claude Code to set up a monitor:

> 帮我监控 3 月中旬香港到东京的来回机票，五天四晚，带一件行李，不要在首尔转机,
> 总价到 4000 港币通知我

Claude reads `SKILL.md`, writes a `config.py` for your route, and runs `setup.sh` —
which creates a venv, installs Playwright + Chromium, registers a launchd agent, and
installs a `flightmon` control command.

```
flightmon          status and latest results
flightmon now      run one pass immediately
flightmon stop     pause          flightmon start  resume
flightmon log      live log       flightmon off    remove entirely
```

## Alerts

A hit triggers a banner, three alert tones, a spoken announcement, and a modal critical
alert with a button that opens the booking page. Hard to sleep through, which is the
point.

## Adapting to another route

Everything lives in `config.py`. Four fields need real attention when switching routes:

| Field | Why |
|---|---|
| `TRIP_DEST` / `TRIP_ORIGIN` | Trip.com's own lowercase city codes, not IATA. `sel` covers all Seoul airports. Copy them from `dcity`/`acity` in a manual search URL. |
| `CURRENCY_SYMBOL` | Part of the price regex. A mismatch matches nothing and yields zero flights **with no error**. |
| `TRIP_DOMAIN` | Regional Trip.com site (`hk.trip.com`, `jp.trip.com`, …). |
| `BAGGAGE_FEE` | Per-airline, per-route, and mostly estimated. Only used for the Google path. |

Layover exclusions are two separate lists — Google labels layovers with IATA codes,
Trip.com with city names.

## Caveats

- macOS only (launchd + `osascript` notifications).
- Trip.com's result list is lazily loaded and occasionally returns a truncated set.
  The scraper waits for the count to stabilise and retries, but a given pass can still
  under-report. Running every 30 minutes smooths this out.
- Scrapers break when sites change their markup. `probe3.py` prints which rule rejected
  each flight; `probe4.py` dumps the full Google list. Start there.
- Be considerate with the polling interval.

`references/gotchas.md` documents every failure mode hit while building this. Most fail
silently — wrong numbers rather than exceptions. Read it before changing the scrapers.

## License

MIT
