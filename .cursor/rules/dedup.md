---
description: Event deduplication system — tiered confidence, audit log, and review workflow
globs:
  - scripts/event_store.py
  - scripts/dedup_report.py
  - data/events/*.json
  - data/events/*.jsonl
---

# Event Deduplication

## Confidence tiers

`dedup_confidence(a, b)` in `event_store.py` returns one of:

| Tier | Criteria | Action |
|---|---|---|
| **certain** | Same ID, same URL, or exact normalized name + within 4h | Auto-merge, logged to `dedup-log.jsonl` |
| **likely** | Exact name + within 24h; or substring name + same location + within 24h | Auto-merge, logged |
| **uncertain** | Substring name + within 24h (different locations); word overlap ≥50% + same location + within 24h; same name but no parseable dates | Routed to `pending.json` for human/agent review |
| **None** | No match signals | Not a duplicate |

## Audit log

Every dedup decision is appended to `data/events/dedup-log.jsonl` with:
- `action`: skip_certain, auto_merge, pending_review, force_merge, merge, reactivate
- `confidence`: certain, likely, uncertain
- `reason`: machine-readable string like `exact_name+within_24h+same_location`
- `kept_id`, `kept_name`, `candidate_id`, `candidate_name`

## Location aliases

`LOCATION_ALIASES` in `event_store.py` maps variant venue names to canonical
keys (e.g. "rumba y timbal" and "7 temple st" both resolve to `rumba-y-timbal`).
Add new aliases when you discover the same venue is named differently across
sources. This is the primary way to improve cross-source dedup without AI.

## Review workflow

After running scrapers or ingesting events:

1. Check `data/events/pending.json` for items with `_dedup_candidate_of` — these
   are uncertain matches waiting for review.
2. For each, compare the pending event against the existing event it matched.
3. If they're the same event: approve with `event_approve(event_id)` or use
   `add_event(event, force=True)` to force-merge.
4. If they're different events: `event_reject(event_id, reason="distinct event")`
   and then re-add without the dedup metadata.

## Quick check

Run `npm run dedup-report` (or `python3 scripts/dedup_report.py`) to scan
published events for suspicious pairs. Use `--active` to scan the active store
instead, and `--log` to also print recent audit log entries.

## Adding new sources

When a new source is added, dedup quality depends on:
- Name normalization: dates, ordinals, and "Vol.N" are stripped automatically
- Location aliases: add entries to `LOCATION_ALIASES` for known venue variants
- Stopwords: `_STOPWORDS` filters common words from overlap calculations

If a source consistently produces uncertain matches for the same event, add a
location alias or improve the name normalization rather than lowering thresholds.
