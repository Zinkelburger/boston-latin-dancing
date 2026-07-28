---
description: Event verification system — checking event details against source URLs
globs:
  - scripts/verify_events.py
  - mcp-server/server.py
  - data/events/verification-report.json
  - data/events/active.json
---

# Event Verification

Correctness is critical. People show up to events based on what we publish.
Every event should be verified against its source before publishing.

## Source of Truth Philosophy

**Scraped event sources (Facebook pages, Google Calendars, Eventbrite) are the
source of truth.** Beatrice's calendar, Boston Salsa Central, and other aggregators
are *pointers* to where the truth lives — they help us find the actual organizer
page, but they are not authoritative themselves.

**Every event and venue MUST have a verifiable link.** If an event has no URL
where a human can confirm it's actually happening, flag it immediately. Do not
publish pattern-generated events without a link to the organizer's page.

### Required for every venue in `data/venues.json`:
- `url` field pointing to the organizer's Facebook page, website, or event calendar
- If you cannot find a URL, add `"_needs_url": true` and log a warning

### Required for every active event:
- `url` field (direct event link) OR `_verification_url` (independent source)
- If neither exists, the event must be flagged with `_verified_status: "no_source"`

### When adding new venues/events:
- ALWAYS search for the organizer's Facebook page or website first
- NEVER assume a recurring pattern is correct without a source to confirm it
- If a pattern is the only info available, note it in the description: "check [source] for exact dates"

### Pattern-based schedules are fallbacks, not truth:
- Organizers change dates (e.g., Fuego y Candela moved from 2nd Saturday to 1st Friday)
- "Every other week" labels are often wrong (e.g., Mambo City is 1st Sunday, not biweekly)
- When a scraped event contradicts a venue pattern, the scraped event wins
- Prefer scraping the organizer's actual page over relying on schedule rules

## Running verification

Use the `event_verify` MCP tool or `npm run verify-events`. This checks each
active event's source URL and produces a report.

```
event_verify()                    # verify all events
event_verify(event_id="...")      # verify one event
event_verify(stale_days=7)        # only events not checked in 7+ days
```

The report is written to `data/events/verification-report.json` and verification
metadata is stored on each event in `active.json` as `_verified_at`,
`_verified_status`, `_verified_notes`, and `_verification_url`.

## Verification statuses

| Status | Meaning | Agent action |
|--------|---------|-------------|
| `confirmed` | Source JSON-LD matches our date **and** location | None |
| `reachable_only` | URL is live but had no JSON-LD — date/location unverified | None required; not fully confirmed |
| `location_mismatch` | Source shows different location | Investigate; use `event_set_location_override` or `event_edit` to fix |
| `date_mismatch` | Source JSON-LD `startDate` is a different Boston day (`our_date`/`source_date`) | Source wins — use `event_edit` to fix. Highest-stakes flag. |
| `cancelled` | Source says event cancelled | Present to user; they decide whether to archive |
| `page_gone` | URL returns 404 or error | Present to user; may need new URL or removal |
| `needs_review` | Page has "cancelled"/"postponed" text | Present to user with the flagged text |
| `needs_browser` | Facebook event/page URL | Prefer Cursor browser MCP (navigate, close login, snapshot); headless Chrome is a fallback |
| `no_source` | Event has no URL | Web search `"{name} {location} boston"` to find source |
| `unverifiable` | Instagram or social link | Flag for user to manually check |

## Handling `needs_browser` events

Prefer **Cursor with browser MCP** for Facebook (login walls + Upcoming tab).
Headless Chrome dump-dom is the fallback when browser MCP is unavailable.

For Facebook event URLs:
1. `browser_navigate` to the URL
2. Close the login dialog (find Close button in snapshot)
3. `browser_snapshot` and look for: date, location, "Event ended", "Cancelled"
4. Compare against our stored data

For Facebook page URLs (like Dante's Salsa Inferno):
1. Navigate to the page's `/events` tab
2. Close login dialog
3. Check for upcoming events
4. If no upcoming events, flag as "may be on hiatus"

## Location overrides

When an event's source has the wrong location (e.g., ICS feed is outdated):
1. Verify the correct location via web search or the organizer's site
2. Use `event_set_location_override(event_id, correct_location)` to fix it
3. This sets `_location_override` which prevents `merge_event()` from reverting
   the fix on re-ingest

## Workflow after scraping

After running scrapers (`event_scrape`), always:

1. `event_list(status="rejected")` — review non-Latin events flagged during ingest
2. Run `event_verify(stale_days=7)` to check events not verified recently
3. Review the flagged events report
4. For `needs_browser` items, use browser MCP to check each one
5. Present all flagged items to the user before publishing
6. Never auto-remove active events — use `event_remove` and queue in rejected for review

## Always save the source

When you verify an event — whether via web search, browser MCP, or user
confirmation — **always** write the independent source URL and notes back to
the event using `event_edit` or the batch update pattern:

```python
ev["_verified_at"] = now_iso
ev["_verified_status"] = "confirmed"  # or needs_review, etc.
ev["_verified_notes"] = "Boston Salsa Central confirms location and schedule"
ev["_verification_url"] = "https://bostonsalsacentral.com/dance-socials/"
```

This way, the next time verification runs, the agent can re-check that same
URL instead of starting from scratch. The `_verification_url` is the
independent source (NOT the event's own URL), so the agent can compare
the event URL against the verification URL for cross-referencing.

For events discovered via Instagram or Facebook with no organizer website,
note the social handle and any aggregator that confirmed it (e.g.,
stayhappening.com, allevents.in, happeningnext.com, thebostoncalendar.com,
bostonsalsacentral.com, social-dance.today, danceplace.com).

## Potential future sources

These organizer pages may yield new events or help verify existing ones:
- Moves & Vibes Dance Co: https://www.eventbrite.com/o/moves-vibes-dance-co-426132190 / https://movesandvibes.com/events
- Boston Salsa Central: https://bostonsalsacentral.com/dance-socials/
- Havana Club: https://www.havanaclubsalsa.com/
- Bachata Room: https://www.bachataroomboston.com/
- Fiesta Dance Company: https://fiestadancecompany.com/upcoming-socials / https://www.instagram.com/fiestadancecompany/ (source: `fiesta-dance-company`)
- Lister Events: https://www.listerevents.com/events
- Mambo Pa Ti: https://www.mambopati.com/
- Fuego y Candela: https://www.facebook.com/FuegoyCandelaSalsa/events (source: `dantes-salsa`)
- Mambo City / Rob Suave: https://www.facebook.com/profile.php?id=100064636639498
- Black Mamba Dance Co: https://blackmambasalsa.com/events / WhatsApp group for notifications
- Dante's Salsa Inferno (venue): https://www.facebook.com/DantesSalsaInferno/events

## Internal fields

All verification metadata uses `_` prefix and is stripped from `public/events.json`:
- `_verified_at` — ISO timestamp of last verification
- `_verified_status` — latest status from verification
- `_verified_notes` — human-readable notes including independent source
- `_verification_url` — independent source URL used for cross-referencing
- `_location_override` — prevents location from being overwritten on merge
