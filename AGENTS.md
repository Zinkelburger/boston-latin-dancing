# Boston Latin Dancing agent guide

- Use the project skills in `.agents/skills/` for scraping, geocoding, and adding sources.
- Treat `data/sources.json` as the source registry. Do not rely on hard-coded source lists in docs.
- Use the Boston Latin Dance MCP tools or `scripts/event_store.py` APIs for event and venue mutations. Do not hand-edit generated `data/events-published.json` or `public/events.json`.
- Preserve stable event IDs and merge existing records instead of creating replacement IDs.
- Facebook refreshes require a timestamped evidence envelope; a bare empty JSON array is not proof that a page has no upcoming events.
- Before publishing, resolve doctor blockers: scraper failures, stale Facebook evidence, pending reviews, missing coordinates, verification failures, duplicate active events, venue conflicts, and publish tripwire risk.
- `rejected.json` is an audit queue, not necessarily a release blocker; review new or unexplained entries.
- Run `npm run doctor`, the relevant Python tests, `npm test`, `npm run typecheck`, and `npm run build` before handing off changes that affect the pipeline or site.
- Preserve unrelated working-tree changes. Do not commit or push unless the user asks.

