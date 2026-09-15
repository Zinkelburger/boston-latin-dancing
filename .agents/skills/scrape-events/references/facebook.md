# Facebook evidence

Facebook sources are not ordinary automated feeds: nothing structured is
served without a rendered DOM. Every enabled source whose type is `facebook`
in `data/sources.json` is refreshed from a real render of the page.

## Automated capture (the normal path)

`scripts/scrape_facebook.py <source-id>` first runs
`scripts/fetch_facebook.py` when a Chrome/Chromium binary is on PATH (or
named by `BLD_CHROME`). Headless Chrome renders four views of the page:

| view | what is read | where it goes |
|---|---|---|
| Events tab | upcoming cards, then each event page (name, date, time, address, blurb) | `data/scraped/<source-id>-raw.json` (`events`) |
| page root | the newest post's text | `signals.latest_post` |
| Albums tab | album titles with counts ("Tambo Salsa Social 2026/09/11") | `signals.albums` |
| Photos tab | the text Facebook OCRs out of flyer images | `signals.flyers` |

The envelope is written only when the Events tab proves it rendered (the
"Events Upcoming Past More" / "Events Past More" header is present).
`no_upcoming` is written only when the header has no Upcoming tab. A login
wall, a truncated DOM or an unparsable Upcoming tab raises, nothing is
written, and the previous envelope stays in force; the failure is in the
scraper's health note.

Every date the post, albums or flyers state is recorded per source in
`data/facebook-signals.json` (committed). `npm run doctor` /
`event_doctor()` reports each *future* date there that has no matching
event on the map (same day, and either the same source or a shared
organizer/name word) under `facebook_signals` as a warning. Those are the
nights an organizer announced without creating a Facebook Event, which the
Events tab alone would never show. Review them; do not auto-publish them.

Set `BLD_FACEBOOK_BROWSER=0` to skip the capture (tests do this). Running
`scrape_facebook.py --from-file <path>` also skips it.

## Manual browser procedure (fallback)

Use this when no Chrome is available, or when the capture reports a failure
for a page you can open yourself.

1. Open the configured `facebook_events_url`.
2. Confirm the page identity and switch to its Upcoming events view.
3. Inspect every visible upcoming occurrence. Open details when the card does not
   expose the full date, time, venue, or canonical occurrence URL.
4. Capture only events that satisfy the map scope. Preserve exact occurrence
   URLs and displayed facts; do not infer recurrence from a past cadence.
5. If there are no upcoming cards, explicitly confirm that the page loaded and
   that only Past events (or an empty Upcoming state) are shown.
6. Look at the Posts and Photos tabs too: a dated post, album title or flyer
   is evidence of a night even when no Event exists.

## Raw capture contract

Write `data/scraped/<source-id>-raw.json` as an evidence envelope:

```json
{
  "schema_version": 1,
  "checked_at": "2026-09-04T14:30:00-04:00",
  "source_url": "https://www.facebook.com/example/events",
  "status": "captured",
  "events": []
}
```

Allowed statuses:

- `captured`: `events` must be a non-empty array of captured event objects.
- `no_upcoming`: `events` must be empty, and the browser check must have
  explicitly established that no upcoming event exists.

`checked_at` must be a timezone-aware timestamp for the actual browser check.
`source_url` must identify the Facebook page that was inspected. Do not refresh
a timestamp without re-checking the page. Extra keys (`capture`, `signals`)
are ignored by the normalizer.

A legacy non-empty array can be ingested temporarily but is reported as legacy.
A bare empty array is unsafe: it lacks proof that Facebook loaded successfully
and must produce unhealthy scraper state.

After saving evidence, run the source through `event_scrape(source_id="...")` or
`python3 scripts/scrape_facebook.py <source-id> --from-file <path>`, then
inspect health and ingest results.

## Failure handling

Login walls, unavailable pages, partial loads, and inaccessible event details do
not mean there are no events. Record the failure, keep the last good normalized
data, and report the source as blocked or unhealthy. Never replace good data with
an unproven empty capture.
