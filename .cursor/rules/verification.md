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
| `confirmed` | Source matches our data | None |
| `location_mismatch` | Source shows different location | Investigate; use `event_set_location_override` or `event_edit` to fix |
| `date_mismatch` | Source shows different date | Use `event_edit` to fix |
| `cancelled` | Source says event cancelled | Present to user; they decide whether to archive |
| `page_gone` | URL returns 404 or error | Present to user; may need new URL or removal |
| `needs_review` | Page has "cancelled"/"postponed" text | Present to user with the flagged text |
| `needs_browser` | Facebook event/page URL | Visit via browser MCP (navigate, close login, snapshot) |
| `no_source` | Event has no URL | Web search `"{name} {location} boston"` to find source |
| `unverifiable` | Instagram or social link | Flag for user to manually check |

## Handling `needs_browser` events

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
1. Run `event_verify(stale_days=7)` to check events not verified recently
2. Review the flagged events report
3. For `needs_browser` items, use browser MCP to check each one
4. Present all flagged items to the user before publishing
5. Never auto-remove events — always ask the user

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
- Fiesta Dance Company: https://fiestadancecompany.com/
- Lister Events: https://www.listerevents.com/events
- Mambo Pa Ti: https://www.mambopati.com/

## Internal fields

All verification metadata uses `_` prefix and is stripped from `public/events.json`:
- `_verified_at` — ISO timestamp of last verification
- `_verified_status` — latest status from verification
- `_verified_notes` — human-readable notes including independent source
- `_verification_url` — independent source URL used for cross-referencing
- `_location_override` — prevents location from being overwritten on merge
