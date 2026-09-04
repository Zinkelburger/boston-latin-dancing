# Facebook browser evidence

Facebook sources are not ordinary automated feeds. Refresh every enabled source
whose type is `facebook` in `data/sources.json` using an interactive browser.

## Browser procedure

1. Open the configured `facebook_events_url`.
2. Confirm the page identity and switch to its Upcoming events view.
3. Inspect every visible upcoming occurrence. Open details when the card does not
   expose the full date, time, venue, or canonical occurrence URL.
4. Capture only events that satisfy the map scope. Preserve exact occurrence
   URLs and displayed facts; do not infer recurrence from a past cadence.
5. If there are no upcoming cards, explicitly confirm that the page loaded and
   that only Past events (or an empty Upcoming state) are shown.

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
a timestamp without re-checking the page.

A legacy non-empty array can be ingested temporarily but is reported as legacy.
A bare empty array is unsafe: it lacks proof that Facebook loaded successfully
and must produce unhealthy scraper state.

After saving evidence, run the source through `event_scrape(source_id="...")` or
the Facebook scraper entry point, then inspect health and ingest results.

## Failure handling

Login walls, unavailable pages, partial loads, and inaccessible event details do
not mean there are no events. Record the failure, keep the last good normalized
data, and report the source as blocked or unhealthy. Never replace good data with
an unproven empty capture.
