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
`eventbrite-boston-latin`, `fiesta-dance-company`, `submissions`. Facebook sources require browser MCP.

Active store count ≠ published count because
publish expands venues, suppresses venue-covered scrapes, and collapses recurring series.

## How to Review Submissions and Dedup Pairs

There is no admin web UI. Use MCP tools or read `data/events/pending.json`.

1. `event_list` with `status="pending"` to see the queue
2. Items with `_dedup_candidate_of` are review-tier duplicate matches — compare
   against the active event via `event_get`
3. User submissions: verify the event is real (check URL, confirm venue)
4. `event_approve` (merge dedup pair or approve submission) or `event_reject` with a reason

## How to Review Rejected (Non-Latin) Events

Events with `styles=["other"]` and no Latin dance keywords in name/description are
queued in `data/events/rejected.json` instead of being added to active.

**Latin keywords:** salsa, bachata, kizomba, zouk, merengue, latin, cumbia, reggaeton,
timba, son/songo, cha cha, mambo, rumba, guaguanco, cubana, tropical

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

## Source Priority (lower = higher priority for dedup conflicts)

0. beatrice-calendar (Google Calendar ICS)
1. recurring-venues (expanded from data/venues.json)
2. eventbrite-boston-latin
3. lister-events
4. bobas (Facebook — dantes-salsa disabled; Dante's covered by venues.json)
5. sensualeros-boston (Google Calendar ICS)
6. submissions
7. manual

## Important Files

- `scripts/event_store.py` — core lifecycle logic (shared by MCP server)
- `scripts/scraper_utils.py` — geocoding, style detection, cost extraction
- `mcp-server/server.py` — MCP tool definitions
- `data/events/changelog.jsonl` — append-only audit log of all mutations
