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
| **certain** | Same ID, any shared URL (across `url` + `urls[]`), or known duplicate pair (verdict: same); same day + same location + strong name match; **cross-source recurring series** (both recurring + same location + same day-of-week + strong name match — *demoted to review* when the titles name different dance styles or the start times sit more than 2 hours apart, see below) | Auto-merge at ingest |
| **review** | Same normalized name within 24h; same venue + same calendar day; substring or word-overlap name match within 24h; same name but no parseable dates; same venue + strong name match within 7 days (cross-source recurring series) | Routed to `pending.json` for human/agent review |
| **None** | No match signals | Not a duplicate |

Fuzzy name matching *can* auto-merge, but only when other signals converge:
same calendar day + same venue + a strong name match, or the cross-source
recurring tier. A strong name match needs at least half the words shared,
including one *distinctive* word — overlap on generic words alone (`salsa`,
`bachata`, `social`, `night`, ...) never counts. Everything weaker is a
`review` match and goes to `pending.json`.

## Ingest flow (`add_event`)

1. Reject venue schedule records — those belong in `data/venues.json` only
2. **Latin relevance check** — `styles=["other"]` with no Latin keywords → `rejected.json`
   (status `rejected_non_latin`; a re-scrape refreshes the queued row instead of adding a
   second one, and an event already in active/archive is never re-queued). Events from a
   source marked `"latin_by_default": true` skip the check. A malformed `sources.json`
   aborts the run rather than silently un-trusting every source.
3. Infer location before dedup checks
4. Compare against archive (certain match → reactivate) then active
5. **certain** → merge silently, log to `dedup-log.jsonl`
6. **review** → append to `data/events/pending.json` with `_dedup_candidate_of`
7. No match → add to active

Latin relevance runs **before** dedup. Rejected events never enter active unless
`event_approve_rejected` is called explicitly.

Every lifecycle function runs under the single store-wide lock
(`data/events/store.lock`, re-entrant) and writes each file atomically, so the
MCP server, the cron pipeline and the review CLIs serialise. Any move between
two files writes the destination first and removes the source only once the
destination write succeeded: `approve_pending` / `approve_rejected` return
`status: not_approved` (with the underlying `add_status`) and leave the queue
row in place when `add_event` refuses the event. A corrupt or empty store file
raises `CorruptJSONError` instead of reading as "no events" — never catch it
and carry on.

## Publish flow (`publish`)

No second fuzzy dedup pass. Publish only:

1. `expand_venues()` — generate events from `data/venues.json`
2. `_suppress_venue_covered_events()` — drop scraped events covered by venue hub schedules
3. `deduplicate()` — merge only **certain** (same ID/URL) pairs
4. `collapse_recurring_series()` — collapse same-series occurrences (occurrence lists
   are compared as *instants*, so a `+00:00` and a `-04:00` spelling of the same
   night collapse to one, emitted in Eastern time)
5. Live series whose stored `startDate` is in the past are rolled forward to their
   next occurrence in the published copy only (`firstStartDate` keeps the original);
   archived rows ship with `description` cut to 300 characters
6. `publish_guarded()` computes all of the above first and, if the live-event count
   would collapse below 70% of the baseline, writes **nothing** — no published JSON,
   no venue-conflict queue, no slug-registry update

All publish reporting goes to stderr: stdout is the MCP server's JSON-RPC channel.

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
- **Pre-verdict** (`add_event(..., distinct_from=[ids])`) → persist `"different"` at add time,
  before dedup runs — for adding a lookalike that is a genuinely distinct event, including
  force-adds where the guards would drop an un-forced add before it ever reached the queue

Format: `[{"id_a": "...", "id_b": "...", "verdict": "same"|"different", "reviewed_at": "..."}]`

The file is re-read on every check and rewritten under the store lock; there is
no in-process cache, so a long-lived MCP server can never write a stale list
back over verdicts the pipeline recorded meanwhile.

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
the same. The dedup system treats these as **certain** when all four signals
converge:

1. Same location (via alias, coords, or string match)
2. Both events marked as recurring
3. Same day of the week
4. Strong name match (exact or word-overlap; substring matches are excluded
   because "salsa" is a substring of "salsa & bachata social")

This prevents duplicate map pins for events like "The Timba Messengers" at
Wally's appearing from both the Una Bulla and Timba Messengers calendars.

**Demotion rule.** Venue + weekday + shared words is not enough on its own:
"Havana Club Bachata Thursdays" and "Havana Club Salsa Thursdays" clear all
four and are two different nights. The pair is demoted to **review** when
either

- the two titles name different dance styles (one says bachata, the other
  salsa, and neither title's style set contains the other's — detected with
  the scraper's own `detect_styles` keyword list), or
- their wall-clock start times differ by more than 2 hours.

A title with no style word ("Thursdays at Havana Club") never conflicts, and
"Salsa & Bachata Thursdays" vs "Bachata Thursdays" is one night, not two.

## Location aliases

`data/location-aliases.json` maps variant venue names to canonical keys
(e.g. "rumba y timbal" and "7 temple st" both resolve to `rumba-y-timbal`).
Shape: `{"canonical-key": ["alias", "alias", ...]}`; keys starting with `_`
are notes. Aliases are lowercased and matched exactly first, then as
substrings in file order, so put the more specific alias first. The file is
loaded once at import (`event_store.LOCATION_ALIASES`). Add new aliases when
you discover the same venue is named differently across sources.

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
