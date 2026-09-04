# Scope, sources, and scraper health

## What belongs on the map

Use one practical test: could someone show up and dance?

Include partner-dance socials, parties, DJ and live-music dance nights, outdoor
events, festivals, and benefits with dancing. Reggaeton, dembow, and Latin-pop
nights may use the `other` style; that is not a reason to exclude them. A short
lesson followed by a social is still a dance event.

Exclude instruction-only classes, workshops, drills, lessons, fitness sessions,
and sit-down performances where the audience watches instead of dances. Judge
the full description, not a single word such as “concert” or “benefit.” When a
thin listing is genuinely borderline, prefer inclusion and record the best
available evidence.

## Source registry

`data/sources.json` is authoritative for enabled state, source type, URLs,
reliability, filters, and scraper configuration. Do not duplicate a source table
in this skill because it becomes stale.

Curated Latin calendars can set `latin_by_default: true`. General calendars
must filter before ingest. Sources marked `unreliable: true` may be scraped for
research, but ingest and publish must not turn them into map pins.

## Prefer generic feed shapes

Before creating a scraper, inspect the page for these reusable shapes:

1. Schema.org Event JSON-LD: configure `scrape_jsonld.py`.
2. A public iCalendar feed: configure the ICS scraper.
3. The Events Calendar/Tribe: use its generic calendar path.

Only write a bespoke scraper when the site provides none of these. Preserve any
existing `id_prefix` during a migration so refreshed events merge instead of
duplicating.

## Health semantics

Scrapers record the number of raw structures found before filtering.

- `ok`: the parser found raw structures and completed.
- `skipped`: a source-specific evidence contract explicitly established there
  was nothing to ingest, or the source was intentionally not runnable.
- `structure_missing`: the page was reachable but expected event structures were
  absent. Assume markup or parser drift until proven otherwise.
- `fetch_error`: the source could not be fetched or parsed reliably.

An empty output file alone is never enough to distinguish “no events” from a
broken scraper. Use `scraper_health()` or `npm run doctor` for the consolidated
view.

