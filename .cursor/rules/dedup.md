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
| **certain** | Same ID, any shared URL (across `url` + `urls[]`), or known duplicate pair (verdict: same); same day + same location + strong name match; **cross-source recurring series** (both recurring + same location + same day-of-week + strong name match) | Auto-merge at ingest |
| **review** | Same normalized name within 24h; same venue + same calendar day; substring or word-overlap name match within 24h; same name but no parseable dates; same venue + strong name match within 7 days (cross-source recurring series) | Routed to `pending.json` for human/agent review |
| **None** | No match signals | Not a duplicate |

Fuzzy name matching never auto-merges. Only hard identity (ID, URL) merges automatically.

## Ingest flow (`add_event`)

1. Reject venue schedule records — those belong in `data/venues.json` only
2. **Latin relevance check** — `styles=["other"]` with no Latin keywords → `rejected.json`
3. Infer location before dedup checks
4. Compare against archive (certain match → reactivate) then active
5. **certain** → merge silently, log to `dedup-log.jsonl`
6. **review** → append to `data/events/pending.json` with `_dedup_candidate_of`
7. No match → add to active

Latin relevance runs **before** dedup. Rejected events never enter active unless
`event_approve_rejected` is called explicitly.

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

A `"same"` verdict is permanent and silent — future occurrences of the pair
auto-merge with no review. Because of that, `approve_pending` **refuses** to merge
a pair that straddles a special-edition boundary (one side anniversary / festival
/ takeover / guest-DJ night, the other the recurring series), returning
`status: blocked_special_edition`. Override with `force=True` only if they truly
are the same event. Audit verdicts with `known_duplicate_list`; undo a wrong one
with `known_duplicate_forget(id_a, id_b)` (this does not un-merge already-merged
events).

## Multi-source events (`urls[]`)

When the same event is scraped from multiple sources (e.g. Eventbrite + ICS
calendar + Lister Events), `merge_event()` accumulates all unique URLs into the
`urls[]` field. The primary `url` stays as the winner's link; extra links are
stored in `urls[]` for display in the frontend.

The `_url_match()` check compares across both `url` and `urls[]` — if any URL
appears in both events, they are a **certain** duplicate.

## Cross-source recurring series

When the same weekly event is published by multiple calendars (e.g. venue
calendar + organizer calendar), occurrence start dates differ but the series is
the same. The dedup system now treats these as **certain** when all four signals
converge:

1. Same location (via alias, coords, or string match)
2. Both events marked as recurring
3. Same day of the week
4. Strong name match (exact, substring, or word-overlap)

This prevents duplicate map pins for events like "The Timba Messengers" at
Wally's appearing from both the Una Bulla and Timba Messengers calendars.

## Location aliases

`LOCATION_ALIASES` in `event_store.py` maps variant venue names to canonical
keys (e.g. "rumba y timbal" and "7 temple st" both resolve to `rumba-y-timbal`).
Add new aliases when you discover the same venue is named differently across
sources.

## Review workflow

After running scrapers or ingesting events:

1. **Rejected queue** — `event_list(status="rejected")` for non-Latin flagged events
   - Dismiss or approve before publishing if any are borderline
2. **Pending dedup** — `npm run review-dedup` (or `python3 scripts/dedup_report.py --pending --json`)
3. For each pending item, compare against its `_dedup_candidate_of` match
4. Same event → `event_approve(event_id)` or `approve_pending(event_id)`
5. Different events → `event_reject(event_id, reason="distinct event")` or `reject_pending(event_id)`

## Quick scan

Run `npm run dedup-report` to scan published events for suspicious pairs that
may have slipped through. Use `--active` to scan the active store instead.
