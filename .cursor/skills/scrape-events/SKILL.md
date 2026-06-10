---
name: scrape-events
description: >-
  Scrape dance events from all sources and update the map. Use when asked to
  refresh events, scrape Facebook pages (BOBAS, etc.), run the pipeline,
  check for new events, update public/events.json, review rejected non-Latin
  events, or "update the map".
---

# Update the Map

Use the **boston-latin-dance MCP tools** for all event operations. Never manually
edit `public/events.json` — it's a build artifact.

## What belongs on the map

This site is for **social dances** — events where people go to dance socially.

**Include:** socials, parties, live-music dance nights, outdoor dance events,
festivals with social dancing. Events that start with a short lesson/intro
before the social are fine (e.g. "lesson at 8 PM, social 9 PM–1 AM").

**Exclude:** pure classes, workshops, technique drills, music lessons, fitness
classes, and recurring class series with no social component. If an event name
contains "class", "classes", "workshop", "technique", or "lesson" without also
mentioning a social/party/dance-night, reject it.

When reviewing ingested events (Steps 3–6), remove or reject anything that is
a class/workshop rather than a social dance. Use `event_remove(event_id,
reason="class, not social dance")` for active events or
`event_dismiss_rejected(event_id, reason="class, not social dance")` for
rejected ones.

## Step 0: Check prerequisites

```bash
pip install -r requirements.txt --quiet
test -f .env && grep -q BLD_ADMIN_TOKEN .env && echo "OK" || echo "MISSING"
```

If `.env` is missing or has no `BLD_ADMIN_TOKEN`, ask the user for the token.

## Step 1: Run automated scrapers + ingest

Call the MCP tool:

```
event_scrape()
```

This runs all enabled automated scrapers, ingests new events into
`data/events/active.json` (with dedup), and auto-archives past events.
It does **not** publish — call `event_publish()` separately (Step 7).

The ingest result includes:
- `added` — new events in active
- `skipped_duplicates` — existing events refreshed (same ID/URL merge)
- `rejected_non_latin` — events queued in `rejected.json` (see Step 3)
- `pending_review` — uncertain dedup pairs (see Step 4)

To scrape a single source:

```
event_scrape(source_id="beatrice-calendar")
```

**Full `event_scrape()` source list** (from `mcp-server/server.py`):

| source_id | Scraper | Notes |
|-----------|---------|-------|
| `beatrice-calendar` | `scrape_ics.py` | Greater Boston Dance Socials (Google Calendar ICS) |
| `sensualeros-boston` | `scrape_ics.py` | Sensualeros Boston Events (Google Calendar ICS) |
| `lister-events` | `scrape_lister.py` | Lister Events (Wix/JSON-LD) |
| `eventbrite-boston-latin` | `scrape_eventbrite.py` | Eventbrite search (salsa/bachata/latin) |
| `unabulla-cuban-boston` | `scrape_ics.py` | Cuban Dance in Boston / Una Bulla (Google Calendar ICS) |
| `fiesta-dance-company` | `scrape_fiesta_dance.py` | Fiesta Dance Company socials |
| `submissions` | `fetch_submissions.py` | User-submitted events from API |

Facebook sources are **not** auto-runnable — they require browser MCP (Step 2).

All registered sources (including disabled ones) are in `data/sources.json`.

## Step 2: Run Facebook scrapers via browser

Only scrape Facebook sources that are **enabled** in `data/sources.json` with
`"type": "facebook"`. Skip disabled sources.

### Which Facebook sources are worth scraping?

| Source ID | Status | Why |
|-----------|--------|-----|
| `bobas` | **Scrape** | Publishes one-off outdoor events with specific dates on Facebook |
| `dantes-salsa` | **Skip (disabled)** | No upcoming Facebook events; weekly schedule is covered by `data/venues.json` (`dantes-tambo`) |

### 2a. Navigate to the Facebook events page

Source URLs are in `data/sources.json` under `facebook_events_url`. Current
enabled Facebook source:

| Source ID | URL |
|-----------|-----|
| bobas | `https://www.facebook.com/profile.php?id=61551665503735&sk=events` |

Use `browser_navigate` to open the URL.

### 2b. Close the login dialog

Facebook shows a login popup. Use `browser_snapshot`, find the "Close" button,
and click it. It may reappear -- close it again.

### 2c. Check for Upcoming events

Look at the snapshot for tab elements:
- If an **"Upcoming"** tab exists and is NOT selected, click it
- If only a **"Past"** tab exists, there are no upcoming events -- write `[]`
- If "Upcoming" is already selected, events below are the upcoming ones

### 2d. Extract event details

For each visible event:

1. Note the event name from the link text
2. Click the event link to open its detail page
3. Close any login dialog that appears
4. Extract from the snapshot:
   - **Date/time** (e.g. "Friday, May 22, 2026 at 8:30 PM -- 1:00 AM EDT")
   - **Name** (heading)
   - **Location** (venue button/link)
   - **URL** (page URL, e.g. `https://www.facebook.com/events/1234567`)
5. Click "See more" if visible to get the full description

### 2e. Write raw JSON and run the Facebook scraper

Write extracted events to `data/scraped/<source-id>-raw.json`:

```json
[
  {
    "name": "Sunset Salsa Bachata on the Docks",
    "date": "May 26, 2026",
    "time": "6:00 PM",
    "end_time": "9:00 PM",
    "location": "Hatch Shell on the Esplanade",
    "url": "https://www.facebook.com/events/123456",
    "description": "Free outdoor social..."
  }
]
```

Then run:

```bash
python3 scripts/scrape_facebook.py <source-id> --from-file data/scraped/<source-id>-raw.json
```

After the scraper writes `data/scraped/<source-id>.json`, ingest it:

```
event_ingest(source_id="<source-id>")
```

## Step 3: Review rejected (non-Latin) events

During ingest, events with `styles=["other"]` and **no Latin dance keywords**
in name/description are **not added to active**. They are queued in
`data/events/rejected.json` for agent review.

This catches events like "West Coast Swing @ Dancing Fools" that appear on
shared calendars (e.g. Sensualeros ICS) but are not salsa/bachata/latin.

### Latin relevance rule (`scripts/event_store.py`)

An event passes automatically if:
- It has a recognized style other than `other` (salsa, bachata, kizomba, zouk, merengue)

Otherwise it must match Latin keywords in name + description:
`salsa`, `bachata`, `kizomba`, `zouk`, `merengue`, `latin`, `cumbia`, `reggaeton`,
`timba`, `son/songo`, `cha cha`, `mambo`, `rumba`, `guaguanco`, `cubana`, `tropical`,
`rueda`, `casino`

### View the rejected queue

```
event_list(status="rejected")
```

Each item has:
- `_rejected_reason` — why it was flagged (e.g. `not Latin dance relevant...`)
- `_rejected_at` — when it was queued
- `_review_type` — always `non_latin` for now

Get full details:

```
event_get(event_id="<rejected-id>")
```

Or inspect the file directly:

```bash
python3 -c "
import json
for e in json.load(open('data/events/rejected.json')):
    print(f\"{e['name'][:50]}\")
    print(f\"  id: {e['id']}\")
    print(f\"  reason: {e.get('_rejected_reason')}\")
    print(f\"  styles: {e.get('styles')}\")
"
```

### Resolve rejected events

| Situation | Action |
|-----------|--------|
| Actually Latin-relevant **social dance** (keywords missed, wrong style tag) | Fix styles/description if needed, then `event_approve_rejected(event_id)` |
| Not Latin dance — keep off map | `event_dismiss_rejected(event_id, reason="not Latin dance")` |
| Class/workshop, not a social dance | `event_dismiss_rejected(event_id, reason="class, not social dance")` |
| Already on map by mistake | `event_remove(event_id, reason="not Latin dance")` — removes from active and queues in rejected |

Before approving a rejected event, check that it is a **social dance**, not a
class or workshop. See "What belongs on the map" above.

**Do not** manually edit `rejected.json`. Use MCP tools.

Re-scraping updates an existing rejected entry (same ID) instead of duplicating it.

## Step 4: Review pending dedup pairs

After ingest, check for uncertain duplicate matches routed to the pending queue.

**There is no admin web UI** for dedup review. Use MCP tools or the JSON file.

### View pending dedup pairs

```
event_list(status="pending")
```

Each item with `_dedup_candidate_of` is an uncertain duplicate of an active event.
Get full details (including dedup metadata) with:

```
event_get(event_id="<pending-id>")
```

Or inspect the file directly:

```bash
python3 -c "
import json
for e in json.load(open('data/events/pending.json')):
    if '_dedup_candidate_of' in e:
        print(f\"{e['name'][:50]}\")
        print(f\"  pending id: {e['id']}\")
        print(f\"  matches active: {e['_dedup_candidate_of']}\")
        print(f\"  reason: {e.get('_dedup_reason')}\")
"
```

Compare the pending event against the existing one:

```
event_get(event_id="<_dedup_candidate_of value>")
```

### Resolve pending dedup pairs

| Situation | Action |
|-----------|--------|
| Same event (merge) | `event_approve(event_id)` — merges into the active duplicate |
| Different event | `event_reject(event_id, reason="distinct event")` then re-add with `event_add` if needed |
| Force merge without review | `add_event(event, force=True)` via Python/MCP internals |

The ingest result from `event_scrape` / `event_ingest` also reports
`pending_review` count and `review_items` when uncertain pairs are found.

### Post-publish suspicious pairs (different tool)

`npm run dedup-report` scans **already published** events for pairs that *look*
like duplicates but weren't merged. This is read-only analysis, not the pending
review queue. Use `--active` to scan the active store instead, `--log` for recent
audit entries.

## Step 5: Verify events against sources

Run verification **before** publishing so flagged items can be fixed first:

```
event_verify(stale_days=7)
```

This produces a report categorizing each event. Handle by status:

| Status | Action |
|--------|--------|
| `confirmed` | No action needed |
| `needs_browser` | Visit the Facebook URL via browser MCP. Navigate, close login dialog, snapshot, and check date/location/status against our data. |
| `no_source` | Web search `"{event name} {location} boston"` to find a source URL. If found, use `event_edit` to add it. |
| `location_mismatch` | Investigate which location is correct. Use `event_set_location_override` to fix. |
| `cancelled` / `page_gone` | Present to user — they decide whether to archive. |
| `needs_review` | Check the flagged text and present to user. |
| `unverifiable` | Flag for user to manually check (Instagram links, etc.) |

Present all flagged items to the user before publishing. Use `event_remove` for
non-Latin events that slipped into active — it queues them in rejected.json for review.

Report is written to `data/events/verification-report.json`.

## Step 6: Review flagged events

Check active events for data quality issues:

```
event_list(status="active")
```

For events with `styles=["other"]`, `cost=null`, or missing coords:
- Use `event_edit` to fix styles, cost, or coordinates
- For missing coords, follow the **geocode-events** skill
- Remove any classes/workshops that slipped through: `event_remove(event_id, reason="class, not social dance")`

Ask the user if unsure about any classification.

## Step 7: Publish

Call the MCP tool:

```
event_publish()
```

This regenerates `data/events-published.json` and `public/events.json` from the
active event store + expanded venues. The Next.js app reads `events-published.json`.

### Active count vs published count

These numbers **will differ** — a smaller published count is normal, not data loss.

Example from a recent scrape: **55 active → 51 published**.

What happens during publish (`scripts/event_store.py` → `publish()`):

1. **Expand venues** — `data/venues.json` weekly schedules become dated events
   (Havana Club, Dante's, Bachata Room, etc.)
2. **Combine** — expanded venue events + all active events
3. **Second dedup pass** — merges **certain** duplicates only (same ID or URL)
   that coexist in the combined pool (e.g. venue hub records duplicated between
   active store and venue expansion)
4. **Recurring-series collapse** — groups same-name/same-location events into
   one entry with a `recurrences[]` array (e.g. multiple Lister workout dates)

Typical math: 55 active + 6 venue-expanded = 61 combined → ~56 after dedup →
~51–52 after collapse.

Venue hub records (those with a `schedule` field) are kept separate from
scraped night-specific series during collapse — they won't merge into each other.

## Step 8: Clear processed submissions

```bash
curl -s -X POST \
  -H "Authorization: Bearer $(grep BLD_ADMIN_TOKEN .env | cut -d= -f2)" \
  https://api.bostonsalsa.org/api/submissions/clear
```

## Step 9: Verify build

```bash
npx next build
```

## Step 10: Commit and push

Ask the user to confirm, then:

```bash
git add public/events.json data/events-published.json data/events/ data/venues.json scripts/ mcp-server/ data/sources.json
git commit -m "Update events $(date +%Y-%m-%d)"
git push
```

---

## Dates, timezones, and day-of-week

Boston events must show the correct **local day and time**, not the UTC calendar day.

### How ICS timestamps work

ICS feeds (Beatrice, Sensualeros) often store times as UTC (`20260611T000000Z`).
That is the same instant as **Wed Jun 10, 8:00 PM** in Boston — Google Calendar
displays the local time correctly. Do not rewrite timestamps to Boston offset;
store the ISO instant as-is.

### Scraper: `dayOfWeek` in Boston time

`scripts/scraper_utils.py` → `make_event()` sets:

```python
"dayOfWeek": DAYS[start.astimezone(NY_TZ).isoweekday() % 7]
```

Re-scraping refreshes `dayOfWeek` on duplicate merges (same ID). If you see a
day mismatch after code changes, run `event_scrape()` then `event_publish()`.

### Frontend: filter uses startDate, not stored dayOfWeek

Map and feed day filters derive the day from `startDate` in local time
(`dayOfWeekFromIso` in `lib/recurrences.ts`), not the stored `dayOfWeek` field.
Popup/card dates also format `startDate` in local time.

If popup says Wednesday but Thursday filter matched, the stored `dayOfWeek` was
stale — re-scrape fixes it; the filter fix prevents the mismatch either way.

---

## Latin relevance filter (before dedup)

During ingest, `add_event()` checks Latin relevance **before** dedup:

| Check | Result |
|-------|--------|
| Has recognized style (not just `other`) | Pass → continue to dedup |
| `styles=["other"]` + Latin keywords in name/description | Pass → continue to dedup |
| `styles=["other"]` + no Latin keywords | **Reject** → queue in `rejected.json` |

Rejected events never reach active. Use `event_approve_rejected` to override.

---

## Deduplication: ingest vs publish

Dedup runs at **two stages** with different behavior.

### Ingest dedup (`add_event()` during `event_ingest` / `event_scrape`)

When a scraped event passes Latin relevance, it is compared against active + archive:

| Confidence | Criteria (simplified) | Action |
|------------|----------------------|--------|
| **certain** | Same ID; any shared URL across `url` + `urls[]`; same day + same location + strong name match; or a human-approved pair in `known_duplicates.json` | Auto-merge into active (refreshes dates/dayOfWeek, accumulates all URLs into `urls[]`) |
| **review** | Exact name + within 24h; same location + same calendar day; substring name + within 24h; word overlap >= 50% (min 2 words) + within 24h; exact name with no parseable dates; same location + strong name match within 7 days (cross-source recurring) | Routed to `pending.json` with `_dedup_candidate_of` for human review |
| **none** | No match | Added as new active event |

Human review outcomes are persisted in `data/known_duplicates.json` (`verdict: "same"` or `"different"`).

All decisions are logged to `data/events/dedup-log.jsonl`.

### Publish dedup (`deduplicate()` + `collapse_recurring_series()`)

During `event_publish()`, the combined pool (active + venue-expanded) gets:

1. **`deduplicate()`** — merges **certain** pairs only (same ID or URL); review-tier
   pairs stay separate unless already approved in `known_duplicates.json`
2. **`collapse_recurring_series()`** — collapses multiple dated instances of the
   same recurring series (same normalized name + location) into one event with
   `recurrences[]`

This is why ingest can add 55 events but publish outputs fewer.

---

## Weekly venues: Dante's and Havana Club

These are **not** dependent on Facebook scraping. Both are permanent weekly
venues in `data/venues.json`, expanded into dated events during publish.

| Venue ID | Name | Schedule | Facebook scrape? |
|----------|------|----------|------------------|
| `dantes-tambo` | Dante's Salsa Fridays | Every other Friday at Dante Alighieri Society | **No** — `dantes-salsa` disabled; FB page has no upcoming events |
| `havana-club` | Havana Club | Mon–Sun nightly schedule | **No** — no Facebook source; site is `havanaclubsalsa.com` |

**Dante's:** The map shows "Dante's Salsa Fridays" from venue expansion. The
Facebook page (`dantes-salsa`) is disabled in `data/sources.json` — do not
scrape it during Step 2.

**Havana Club:** The venue hub covers the full weekly schedule. The Sensualeros
ICS calendar (`sensualeros-boston`) also lists night-specific entries (e.g.
"Bachata Sensual Mondays @ Havana Club") which collapse into recurring series
on publish. These complement the venue hub with per-night URLs and names.
No config change needed — just don't expect a Facebook scrape for Havana.

---

## Architecture Reference

```
data/events/active.json     Source of truth for live events
data/events/archive.json    Past events (dedup history + reactivation)
data/events/pending.json    Unreviewed submissions + uncertain dedup pairs
data/events/rejected.json   Non-Latin events flagged for agent review
data/events/changelog.jsonl Audit log of all mutations
data/events/dedup-log.jsonl Dedup decision audit trail
data/events/verification-report.json Last verification run output
data/venues.json            Permanent weekly venues (Havana Club, Dante's, etc.)
data/sources.json           Config: URLs, source IDs, defaults, enabled flags
data/scraped/*.json         Intermediate scraped data
data/geocode-cache.json     Nominatim results cache
data/events-published.json  BUILD ARTIFACT (imported by Next.js app)
public/events.json          Legacy copy of events-published.json
scripts/event_store.py      Core lifecycle logic (dedup, archive, publish, rejected queue)
scripts/verify_events.py   Event verification engine
scripts/scraper_utils.py    Geocoding, style detection, VENUE_COORDS, dayOfWeek (America/New_York)
scripts/dedup_report.py     Read-only scan for suspicious pairs in published/active
mcp-server/server.py        MCP tool definitions
```

## MCP Tools Quick Reference

| Tool | Purpose |
|------|---------|
| `event_list` | Query active/pending/rejected/archive events |
| `event_get` | Full details of one event by ID (searches all pools) |
| `event_add` | Add event (auto dedup + geocode + style detect + Latin filter) |
| `event_edit` | Update fields on an active event |
| `event_archive` | Move past events to archive |
| `event_approve` | Approve pending submission or merge uncertain dedup pair |
| `event_reject` | Remove pending submission with reason |
| `event_remove` | Remove active event → queue in rejected.json for review |
| `event_approve_rejected` | Promote rejected event to active (bypasses Latin filter) |
| `event_dismiss_rejected` | Permanently drop a rejected event |
| `event_scrape` | Run scrapers + ingest + archive |
| `event_ingest` | Ingest from data/scraped/ without re-scraping |
| `event_publish` | Regenerate events-published.json + public/events.json |
| `venue_list` | List permanent venues |
| `venue_add` | Add a new permanent venue |
| `source_list` | List registered sources |
| `source_add` | Register a new source |
| `event_verify` | Verify events against source URLs |
| `event_set_location_override` | Fix wrong location permanently (survives re-scrape) |

## Full pipeline checklist

```
1. event_scrape()                          → scrape + ingest + archive
2. event_list(status="rejected")           → review non-Latin flagged events
3. event_list(status="pending")            → review uncertain dedup pairs
4. event_verify(stale_days=7)              → verify against sources
5. event_publish()                         → regenerate map data
6. npx next build                          → verify build
7. git commit + push (if user confirms)
```
