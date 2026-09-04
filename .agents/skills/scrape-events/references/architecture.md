# Scraper and event-store architecture

## Data flow

Source configuration in `data/sources.json` drives scraper commands. Raw and
normalized scraper files live under `data/scraped/`. Ingest validates and merges
records into the event lifecycle files under `data/events/`. Publishing combines
eligible active and historical data, resolves recurring venues, and writes
`data/events-published.json` and `public/events.json` atomically.

The generated files are outputs, not editing surfaces.

## Stable identity and merge behavior

An event ID identifies one occurrence or recurrence series. Scraper migrations
must preserve the existing prefix and slug rules. Merge refreshed source facts
into the same record, preserving lifecycle and verification metadata where the
event fingerprint has not changed.

Deduplication has two jobs: exact/stable matches refresh an existing event;
uncertain similarities enter pending review. Do not weaken duplicate safeguards
to make a scrape appear clean.

## Scraper contract

Each scraper should:

1. Fetch or load its configured source.
2. Count raw event structures before keyword filtering.
3. Normalize through shared event helpers.
4. Record health with enough context to distinguish fetch failure, structure
   drift, filtering, and explicit no-event evidence.
5. Preserve the previous normalized output on failure.

Write focused fixture tests for source parsing and a silent-markup-failure test.
For changes to the store or MCP surface, add both core behavior tests and wrapper
schema/call tests.

## Important modules

- `scripts/scraper_utils.py`: scraper runner, health, normalization helpers.
- `scripts/event_store.py`: lifecycle mutations, ingest, dedup, guarded publish.
- `scripts/verify_events.py`: automated checks and browser attestations.
- `scripts/event_doctor.py`: read-only consolidated preflight.
- `mcp-server/server.py`: tool surface; stdout is reserved for protocol messages.

Keep diagnostics and logs on stderr in the MCP process.
