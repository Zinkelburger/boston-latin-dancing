---
description: Event deduplication system — two-tier confidence and review workflow
globs:
  - scripts/event_store.py
  - scripts/dedup_report.py
  - data/events/*.json
  - data/events/*.jsonl
  - data/known_duplicates.json
---

# Event Deduplication

All dedup logic lives in `scripts/event_store.py`. There are two tiers:

| Tier | Criteria | Action |
|---|---|---|
| **certain** | Same ID, same URL, or known duplicate pair (verdict: same) | Auto-merge at ingest |
| **review** | Same normalized name within 24h; same venue + same calendar day; substring or word-overlap name match within 24h; same name but no parseable dates | Routed to `pending.json` for human/agent review |
| **None** | No match signals | Not a duplicate |

Fuzzy name matching never auto-merges. Only hard identity (ID, URL) merges automatically.

## Ingest flow (`add_event`)

1. Reject venue schedule records — those belong in `data/venues.json` only
2. Infer location before dedup checks
3. Compare against archive (certain match → reactivate) then active
4. **certain** → merge silently, log to `dedup-log.jsonl`
5. **review** → append to `data/events/pending.json` with `_dedup_candidate_of`
6. No match → add to active

## Publish flow (`publish`)

No second fuzzy dedup pass. Publish only:

1. `expand_venues()` — generate events from `data/venues.json`
2. `_suppress_venue_covered_events()` — drop scraped events covered by venue hub schedules
3. `deduplicate()` — merge only **certain** (same ID/URL) pairs
4. `collapse_recurring_series()` — collapse same-series occurrences

## Audit log

Every dedup decision is appended to `data/events/dedup-log.jsonl`:

- `action`: certain, review, force, reactivate
- `confidence`: certain or review
- `reason`: machine-readable string like `exact_name+within_24h+same_location`
- `kept_id`, `kept_name`, `candidate_id`, `candidate_name`

## Known duplicates (`data/known_duplicates.json`)

When a human or agent reviews a pending pair:

- **Approve** (`approve_pending`) → persist pair with verdict `"same"` → future matches are **certain**
- **Reject** (`reject_pending`) → persist pair with verdict `"different"` → pair is never flagged again

Format: `[{"id_a": "...", "id_b": "...", "verdict": "same"|"different", "reviewed_at": "..."}]`

## Location aliases

`LOCATION_ALIASES` in `event_store.py` maps variant venue names to canonical
keys (e.g. "rumba y timbal" and "7 temple st" both resolve to `rumba-y-timbal`).
Add new aliases when you discover the same venue is named differently across
sources.

## Review workflow

After running scrapers or ingesting events:

1. Run `npm run review-dedup` (or `python3 scripts/dedup_report.py --pending --json`)
2. For each item, compare the pending event against its `_dedup_candidate_of` match
3. Same event → `event_approve(event_id)` or `approve_pending(event_id)`
4. Different events → `event_reject(event_id, reason="distinct event")` or `reject_pending(event_id)`

## Quick scan

Run `npm run dedup-report` to scan published events for suspicious pairs that
may have slipped through. Use `--active` to scan the active store instead.
