---
name: scrape-events
description: >-
  Scrape dance events from all sources and update the map. Use when asked to
  refresh events, scrape Facebook pages, run the pipeline, check for new events,
  update data/events-published.json, review the quarantine or pending queue, or update
  the map.
---

# Scrape events and update the map

Use the Boston Latin Dance MCP tools for lifecycle operations. Never manually
edit `data/events-published.json`; it is generated.
Treat `data/sources.json` as the authoritative source list.

## Route to the right reference

- Read [references/scope-and-sources.md](references/scope-and-sources.md) when
  deciding what belongs on the map, adding or diagnosing a source, or interpreting
  scraper health.
- Read [references/facebook.md](references/facebook.md) before any Facebook
  browser refresh or when a Facebook capture is missing, empty, or stale.
- Read [references/review-and-verification.md](references/review-and-verification.md)
  before resolving pending/rejected events, verifying events, or publishing.
- Read [references/architecture.md](references/architecture.md) when changing
  scraper code, ingest/dedup behavior, event IDs, or generated data flow.

## End-to-end workflow

1. Check prerequisites.

   ```bash
   python3 -m pip install -r requirements.txt --quiet
   test -f .env && grep -q BLD_ADMIN_TOKEN .env
   ```

   If the admin token is needed and absent, explain the blocker rather than
   inventing a value.

2. Run automated sources and ingest with `event_scrape()`. Use
   `event_scrape(source_id="...")` for a targeted refresh. Inspect failures,
   structurally missing sources, additions, refreshed duplicates, rejections,
   and pending review items.

3. Facebook sources refresh themselves inside `event_scrape()` when Chrome is
   installed: headless Chrome reads the Events tab into the evidence envelope
   and the newest post, album titles and flyer text into
   `data/facebook-signals.json`. Check the doctor's `facebook_signals`
   warnings for dated claims with no event. Without Chrome, refresh each
   source through a real browser and save one envelope per source. Follow
   `references/facebook.md`; a bare `[]` is ambiguous and unhealthy.

4. Run the unified preflight:

   ```bash
   npm run doctor
   ```

   It consolidates scraper health, Facebook evidence freshness, review queues,
   verification, coordinates, duplicates, venue conflicts, artifact drift, and
   publish-tripwire risk. Fix every blocker and review each warning.

5. Review lifecycle queues and source results. Reject instruction-only events
   and sit-down listening shows. Resolve uncertain duplicates. Preserve a real
   event when evidence supports it; missing a legitimate dance night is worse
   than keeping a sparse but plausible listing.

6. Geocode missing locations and verify active events. For sources that cannot
   be verified headlessly, use browser evidence and record it with
   `event_verify_attest` rather than treating an inaccessible page as confirmed.

7. Publish only after the doctor has no blockers with `event_publish()`. If the
   tripwire blocks a large count drop, investigate; do not bypass it merely to
   make the command succeed.

8. Verify the finished state:

   ```bash
   npm run doctor
   pytest -q
   npm test
   npm run typecheck
   npm run build
   ```

   Report additions, updates, archived/rejected/pending items, health or browser
   evidence problems, verification exceptions, and whether publishing completed.

## Operating principles

- Event identity is durable. Refresh records through merge behavior; do not mint
  a new ID when an existing event is the same occurrence.
- An empty result is evidence only when the source-specific contract proves the
  page was checked successfully.
- `structure_missing` means the page was reachable but the parser found no raw
  event structure. Treat it as a scraper regression, not as “no events.”
- Unreliable sources may be useful research inputs but must not create map pins.
- Prefer generic config-driven feed shapes before writing a bespoke scraper.
- Never publish around unresolved missing coordinates or venue conflicts.

