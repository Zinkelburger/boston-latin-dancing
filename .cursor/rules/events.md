---
description: Rules for managing boston-latin-dance events, venues, and the scrape pipeline.
globs:
  - "data/events/**"
  - "data/venues.json"
  - "public/events.json"
  - "scripts/event_store.py"
  - "scripts/merge_events.py"
  - "mcp-server/**"
---

# Event Management Rules

## Golden Rule

**Never manually edit `public/events.json`** — it is a generated build artifact.
Regenerate it by calling the `event_publish` MCP tool.

## Data Architecture

```
data/events/active.json   ← source of truth for live/upcoming events
data/events/archive.json  ← past events (for dedup history + reactivation)
data/events/pending.json  ← unreviewed user submissions
data/venues.json          ← permanent weekly venues (Havana Club, Dante's, etc.)
public/events.json        ← BUILD ARTIFACT consumed by Next.js frontend
```

## How to Add Events

Use the `event_add` MCP tool. It automatically:
- Deduplicates against active + archive
- Geocodes the location
- Detects dance styles from name/description
- Extracts cost

Do NOT append to JSON files manually.

## How to Run the Pipeline

1. `event_scrape` — runs all automated scrapers, ingests results, archives past events
2. `event_publish` — regenerates public/events.json

Or scrape a single source: `event_scrape` with `source_id` argument.

## How to Review Submissions

1. `event_list` with `status="pending"` to see the queue
2. For each: verify the event is real (check URL, confirm venue)
3. `event_approve` or `event_reject` with a reason

## Venues vs Events

- **Venues** (`data/venues.json`): permanent weekly spots with a `schedule[]` array.
  Expanded into concrete dated events during `event_publish`.
  Can be edited directly or via `venue_add` MCP tool.
- **Events** (`data/events/active.json`): one-off or scraped events with `startDate`/`endDate`.
  Managed exclusively through MCP tools.

## Event Lifecycle

```
Discovery (scraper/submission/manual) → Active → Archive (when past)
                                                    ↓
                                    Reactivation (if event recurs next year)
```

## Source Priority (lower = higher priority for dedup conflicts)

0. beatrice-calendar (Google Calendar ICS)
1. recurring-venues (expanded from data/venues.json)
2. eventbrite-boston-latin
3. lister-events
4. bobas / dantes-salsa (Facebook sources)
5. submissions
6. manual

## Important Files

- `scripts/event_store.py` — core lifecycle logic (shared by MCP server)
- `scripts/scraper_utils.py` — geocoding, style detection, cost extraction
- `mcp-server/server.py` — MCP tool definitions
- `data/events/changelog.jsonl` — append-only audit log of all mutations
