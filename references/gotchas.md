# Gotchas

Every item here is a bug that was hit, diagnosed, and fixed in a real session. Most of
them fail **silently** — wrong numbers, not crashes — so they are expensive to
rediscover. Read this before touching the scrapers.

## Prices

**A list price is not a bookable price.** This is the whole reason the verification step
exists. Two separate mechanisms:

- Google Flights hands off to third-party OTAs. Its displayed fare can vanish on
  click-through. Never alert on a Google price.
- Trip.com's `Checked baggage included` badge on the results list fires when *any* leg
  has a bag. Measured: list showed HK$3,451 + badge; the `Select Fare` panel showed
  `1 × 23 kg (departure), None (return)`, and the cheapest both-legs fare was HK$3,800.

The `Select Fare` panel (`.is-fareok-card`) is ground truth — see the wording note below
for how to read its baggage line.

**Baggage wording in the fare panel has many forms.** Observed: `Included`, `20 kg`,
`From 15 kg`, `1 × 23 kg (departure), 1 × 20 kg (return)`, `1 × 23 kg (departure),
None (return)`, `View details`. An early parser recognised only the first and the
explicit per-direction form, so `From 15 kg` and a bare `20 kg` were classified
`unknown` and **silently discarded** — entire itineraries were reported as
"could not verify" when the panel had opened and shown perfectly good fares. Check for
`None` first (a per-direction string contains both `kg` and `None`), then `Included`,
then any `\d+\s*kg`.

**Distinguish the failure modes when verification returns nothing.** "The panel never
opened", "the panel opened but had no fare cards", and "the panel opened and no fare met
the baggage requirement" are completely different situations — the last is not a failure
at all. Collapsing them into a bare `except: pass` and a `return None` makes the cause
undiagnosable after the fact. Return a reason string and log it.

**Judge list truncation by the number of *qualifying* flights, not the total.** Sorting
by cheapest puts red-eyes and excluded-layover itineraries first, so a truncated list is
disproportionately full of unusable flights. A pass that scraped 19 flights looked
healthy by total count but yielded only 2 usable candidates, missing the cheapest
bookable option entirely.

**Currency symbol is part of the price regex.** `CURRENCY_SYMBOL` must match what the
page actually renders. A mismatch matches nothing and yields zero flights with no error.

## Google Flights

- **Two-thirds of flights are hidden** behind `View more flights`. Without expanding you
  see ~19 of ~59. Cheaper carriers (Jeju, Air Premia, T'way) live in the hidden part.
- **Stale DOM handles after `go_back`.** Element handles captured before navigation throw
  on click afterwards. If that exception is swallowed by a bare `except: continue`, the
  loop silently probes only the first candidate — `MAX_OUTBOUND_PROBES` becomes a lie.
  Re-scrape and re-match by a stable key after every navigation.
- **No checked-baggage filter exists** on this route's Bags popup — only carry-on. Hence
  the estimated `BAGGAGE_FEE` table for the Google path.
- Result cards duplicate: a compact version and a verbose one collapsed to a single
  line. Filter by line count (`< 6` lines → skip) and dedupe by key.
- Layovers appear as IATA codes inside the card text (`3 hr 20 min TPE`). The
  `HKG–ICN` segment line must be skipped or it is misread as a layover.
- Overnight arrivals are marked `+1`. Not filtering these silently accepts red-eyes.

## Trip.com

- Plain HTTP fetches hit a bot-verification wall; a real headless browser with
  `--disable-blink-features=AutomationControlled` and a `navigator.webdriver` shim loads
  fine.
- **Lazy loading must settle.** Scrolling once and scraping yields a partial list — the
  same query returned 11 flights on one pass and 28 on another, a swing worth over a
  thousand dollars. Wait until the card count is unchanged for three consecutive polls.
- **`go_back` restores only part of the list.** Re-navigate to the search URL between
  candidates instead. Slower, but deterministic.
- The **UI filters are unreliable.** `Checked baggage included` reports a successful
  click while not applying, and `Direct` is reset on the return-leg page. Worse, checking
  "did at least half the cards show a bag?" as verification is fooled by fares that
  natively include one. Do the filtering in code instead. `Direct` also removes cheap
  non-excluded layovers, so prefer filtering by layover city name.
- Baggage badges have several wordings: `Checked baggage included`, `Checked baggage 20 kg`.
  Matching only one variant misclassifies fares.
- Layovers are **city names** (`4h 10m in Manila`), not IATA codes — hence a separate
  exclusion list from Google's.
- The outbound card's actionable control is a `button` inside the card; clicking the card
  itself does nothing. On the return list that button says `View Details` and opens the
  fare panel rather than navigating.

## Platform

- **AppleScript strings must be double-quoted.** Passing Python's `repr()` produces single
  quotes and `osascript` fails with a syntax error — the notification silently never
  appears, which defeats the entire tool. Always test the notification path end to end.
- `display notification` is a transient banner. For something that cannot be missed,
  combine it with repeated `afplay`, `say`, and a modal `display alert ... as critical`
  launched detached so it does not block the scrape.
- **zsh does not word-split unquoted parameter expansions.** `for d in "a b"; do cmd $d`
  passes `"a b"` as one argument, unlike bash. This quietly ran the same date twice during
  testing and produced results that looked stable but were not.
- launchd agents in `~/Library/LaunchAgents` reload at login, so `launchctl unload` alone
  is not a permanent stop — remove the plist.

## Concurrency

**launchd fires on a fixed interval regardless of whether the previous run finished.**
A pass that runs long, or any manual run alongside the scheduled one, produces two
processes appending to the same log — interleaved lines that make timings and phases
unreadable, duplicate rows in the history CSV, and enough memory pressure that one
instance gets killed mid-run. Take a non-blocking `flock` at startup and exit quietly
if another pass holds it.

## Data integrity

**A CSV header is written once; the columns are written every run.** Adding a field to
the row writer without touching the existing file leaves every subsequent row wider than
the header, and anything parsing by header name silently reads the wrong column. One
morning's 100 rows were unusable this way — a query for verified fares returned 1 result
when the truth was 35. Compare the on-disk header against the expected columns on every
write and roll the file over when they differ.

**Patching source with `sed` or string replacement fails silently when the pattern has
drifted.** A `route` key was added to one result-builder that way and not to the other,
because an earlier edit had already changed the surrounding text. The mismatch surfaced
half an hour later as a `KeyError` at the final write, discarding the whole pass — and
the ground-transport surcharge it was meant to add had never been applied to the main
data source at all. Verify the replacement actually landed, and validate result objects
where they are produced rather than where they are consumed.

**OTAs sell ground transport as flights.** Searching from a secondary airport returns
itineraries operated by ferry and coach companies that route the traveller back through
the primary airport before flying — defeating the entire point of using the alternative
airport. Exclude the home city as a connection point when departing from a nearby one.
