---
description: Rules for managing boston-latin-dance events, venues, and the scrape pipeline.
globs:
  - "data/events/**"
  - "data/venues.json"
  - "public/events.json"
  - "scripts/event_store.py"
  - "mcp-server/**"
---

# Event Management Rules

## Golden Rule

**Never manually edit `public/events.json` or `data/events-published.json`** — they are generated build artifacts.
Regenerate them by calling the `event_publish` MCP tool or running `npm run publish-events`.

## Data Architecture

```
data/events/active.json      ← source of truth for live/upcoming events
data/events/archive.json     ← past events (for dedup history + reactivation)
data/events/pending.json     ← unreviewed user submissions + review-tier dedup pairs
data/events/rejected.json    ← non-Latin events flagged for agent review (styles=other, no keywords)
data/venues.json             ← permanent weekly venues (Havana Club, Dante's, etc.)
data/events-published.json   ← BUILD ARTIFACT imported by the Next.js app
public/events.json           ← legacy copy of events-published.json (same content)
```

The Next.js app imports from `data/events-published.json` (via `@/data/events-published.json`), not directly from `public/events.json`.

## How to Add Events

Use the `event_add` MCP tool. It automatically:
- Deduplicates against active + archive
- Geocodes the location
- Detects dance styles from name/description
- Extracts cost

Do NOT append to JSON files manually.

## How to Run the Pipeline

1. `event_scrape` — runs all automated scrapers, ingests results, archives past events
2. `event_list(status="rejected")` — review non-Latin events flagged during ingest
3. `event_verify(stale_days=7)` — verify events against sources **before** publishing
4. Review pending dedup pairs — `event_list(status="pending")` for items with `_dedup_candidate_of`
5. `event_publish` — regenerates events-published.json + public/events.json (expand venues, suppress covered, collapse series)

Or scrape a single source: `event_scrape` with `source_id` argument.

Automated source_ids: `beatrice-calendar`, `sensualeros-boston`, `lister-events`,
`eventbrite-boston-latin`, `fiesta-dance-company`, `jandl-events`, `submissions`. Facebook sources require a browser — prefer Cursor's browser MCP; headless Chrome is the fallback.

Active store count ≠ published count because
publish expands venues, suppresses venue-covered scrapes, and collapses recurring series.

## How to Review Submissions and Dedup Pairs

There is no admin web UI. Use MCP tools or read `data/events/pending.json`.

1. `event_list` with `status="pending"` to see the queue
2. Items with `_dedup_candidate_of` are review-tier duplicate matches — compare
   against the active event via `event_get`
3. User submissions: verify the event is real (check URL, confirm venue)
4. `event_approve` (merge dedup pair or approve submission) or `event_reject` with a reason

## Big Events (`special: true`)

`special: true` drives the site's **Big Events** filter and the gold map pin.
Publish auto-flags some one-offs (name keywords: festival / annual /
anniversary / congress / weekender / gala / cruise / benefit / fundraiser /
solidarity / encuentro; or description language like "benefit concert" /
fundraiser / solidarity). **Plain-named marquees still need a human/agent
flag** via `event_edit(event_id, updates_json='{"special": true}')`.

**Do flag** (err toward yes on unique one-offs the scene plans around):
- Unique branded nights that aren't a weekly series ("Baila por Venezuela")
- Benefit / fundraiser / solidarity / relief dance events
- Multi-org or stacked multi-artist community lineups (not one guest DJ)
- Citywide / outdoor / festival-scale parties even with a short title
  ("Salsa at the Shell")

**Do not flag:** regular guest-DJ nights, holiday-theme bar socials, weekly
series, or festival pre/after-parties (those stay ordinary pins unless
explicitly overridden).

When flagging, also fix `styles=["other"]` to real Latin styles if people
are dancing salsa/bachata/merengue/etc. Suppress a wrong auto-flag with
`{"special": false}`.

## How to Review Rejected (Non-Latin) Events

Events with `styles=["other"]` and no Latin dance keywords in name/description are
queued in `data/events/rejected.json` instead of being added to active.

**Latin keywords:** salsa, bachata, kizomba, zouk, merengue, latin, cumbia, reggaeton,
timba, son/songo, cha cha, mambo, rumba, guaguanco, cubana, tropical, rueda, casino

Events with a recognized style (salsa, bachata, etc.) always pass — only `other`-only
events need keyword matching.

1. `event_list(status="rejected")` to see the queue
2. `event_get(event_id)` for full details
3. Decide:
   - **Keep off map** → `event_dismiss_rejected(event_id, reason="not Latin dance")`
   - **Add anyway** → `event_approve_rejected(event_id)` (bypasses Latin filter)
4. **Remove from active** → `event_remove(event_id, reason="...")` moves it to rejected

Re-scraping updates existing rejected entries (same ID). Do not manually edit `rejected.json`.

After resolving rejected items, run `event_publish()` if you changed active.

## Venues vs Events

- **Venues** (`data/venues.json`): permanent weekly spots with a `schedule[]` array.
  Expanded into concrete dated events during `event_publish`.
  Can be edited directly or via `venue_add` MCP tool.
- **Events** (`data/events/active.json`): one-off or scraped events with `startDate`/`endDate`.
  Managed exclusively through MCP tools.

### Venue Requirements

Every venue entry MUST have:
- A `url` field linking to the organizer's Facebook page or website where dates can be verified
- A correct `schedule[].note` that the recurrence engine can parse (see patterns below)
- A description that mentions where to check for exact dates if the pattern is approximate

Recognized schedule note patterns:
- `"1st Sunday of each month"` → generates 1st Sunday only
- `"2nd Saturday of each month"` → generates 2nd Saturday only
- `"Every other Friday"` → alternating weeks. Add `"anchor": "YYYY-MM-DD"` (a date
  the night actually happens) to the schedule entry to set which weeks are on;
  without it the phase falls back to the historical reference date (2026-01-02),
  so existing venues are unchanged. Two venues on opposite fortnights just carry
  anchors one week apart. `validate_venue_schedule` rejects an anchor that is
  not a date or falls on a different weekday than `dayOfWeek`.
- `"Lesson + social (18+)"` → every week (no filter)

Schedule entries are `{dayOfWeek, time?, note?, anchor?}`; `venue_add` validates
them with `event_store.validate_venue_schedule` before writing.

If a venue doesn't follow a reliable pattern, prefer scraping their FB page over
guessing. Add them as a source in `data/sources.json` instead of (or in addition to)
a venue entry.

## Event Lifecycle

```
Discovery (scraper/submission/manual)
    ↓
Latin relevance check
    ├─ fail → rejected.json (agent review)
    └─ pass → dedup check
                  ├─ certain duplicate → merge into active
                  ├─ review duplicate → pending.json
                  └─ new → active.json
                                    ↓
                              Archive (when past)
                                    ↓
                          Reactivation (if event recurs)
```

## Review queues (two separate pools)

| Queue | File | Purpose | Resolve with |
|-------|------|---------|--------------|
| **Rejected** | `rejected.json` | Non-Latin events (`styles=other`, no keywords) | `event_approve_rejected` / `event_dismiss_rejected` |
| **Pending** | `pending.json` | Uncertain dedup pairs + user submissions | `event_approve` / `event_reject` |

Do not confuse them — rejected is about **relevance**, pending is about **duplicates**.

## Unreliable sources (`"unreliable": true`)

Some calendars stay **enabled for scraping** (research / cross-check) but must
**never ship map pins**. Mark them in `data/sources.json` with
`"unreliable": true` (and usually a high `noise.score`). Ingest skips their
scraped events; publish also filters any leftover active rows from that source.

Current: `unabulla-cuban-boston` — cadence/claims drift (El Bonche "4th
Saturday", Dante pattern guesses, Sunday class listings). Prefer organizer
Eventbrite/Facebook and Beatrice for Cuban nights. Do not re-approve Una Bulla
rows onto the map without a human override of the flag.

## Source Priority (lower rank = wins a dedup merge)

`SOURCE_PRIORITY` in `scripts/event_store.py` is the authority; this is a summary.
Venue hubs (records with a `schedule[]`, expanded from `data/venues.json`) always
win. Then:

- 0 manual, 1 submissions, 2 recurring-venues
- 10 beatrice-calendar, sensualeros-boston, unabulla-cuban-boston
- 11 eventbrite-boston-latin, timba-messengers
- 12 lister-events, fiesta-dance-company, mato-lawn-on-d, nlf-events
- 13 bobas, dantes-salsa, sabor-latino, lowell-sitp, lous-live, jandl-events
- 14 pr-festival-ma, eastboston-events, harvardsquare
- 50 any source not listed

The winner keeps its description and primary `url`; the loser's URLs go to
`urls[]`. Description length never decides.

## Store integrity

- One lock for the whole store (`data/events/store.lock`, re-entrant). Every
  lifecycle function takes it; CLIs and the MCP server wrap any direct
  `load_* / modify / save_*` sequence in `event_store.store_lock()`.
- Every JSON write is atomic (`scripts/atomic_io.py`: unique temp file, fsync,
  rename); JSONL logs are appended with O_APPEND.
- A corrupt or empty store file raises `CorruptJSONError`. Never catch it and
  proceed as if the file were empty — restore it from git.
- Moves between files write the destination before removing the source, so an
  interrupted move leaves a duplicate (dedup catches it) rather than a gap.
- `event_store` writes nothing to stdout; progress and warnings go to stderr.
- Publish rolls a live series' `startDate`/`endDate` forward to its next
  occurrence in the published copy only (`firstStartDate` keeps the original)
  and cuts archived descriptions to 300 characters; the stored records are
  untouched.
- Store-level helpers for the MCP server: `archive_event(event_id, reason)`,
  `validate_venue_schedule(schedule)`, `add_venue(venue)`, `add_source(source)`.

## Important Files

- `scripts/event_store.py` — core lifecycle logic (shared by MCP server)
- `scripts/scraper_utils.py` — geocoding, style detection, cost extraction
- `mcp-server/server.py` — MCP tool definitions
- `data/events/changelog.jsonl` — append-only audit log of all mutations
