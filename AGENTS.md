# Boston Latin Dancing agent guide

- Use the project skills in `.agents/skills/` for scraping, geocoding, and adding sources.
- Treat `data/sources.json` as the source registry. Do not rely on hard-coded source lists in docs.
- Use the Boston Latin Dance MCP tools or the `scripts/event_store/` package APIs for event and venue mutations. Do not hand-edit the generated `data/events-published.json`.
- Preserve stable event IDs and merge existing records instead of creating replacement IDs.
- Facebook refreshes require a timestamped evidence envelope; a bare empty JSON array is not proof that a page has no upcoming events. With Chrome installed the envelope is captured headlessly by `scripts/fetch_facebook.py`, which also writes dated posts, album titles and flyer text to `data/facebook-signals.json`; the doctor's `facebook_signals` warnings are dance dates an organizer announced without a Facebook Event.
- The scheduled weekly review is `automation/claude_review.sh`: scripts do every deterministic step and write a worklist of multiple-choice questions (`scripts/weekly_review.py`); the agent only answers them through the MCP review tools. Add a new kind of judgment call as a question type there, not as prose in `automation/agent_prompt.md`.
- Every check that `weekly_review.py finish` blocks publishing on must reach the agent as a question first: in `prepare`, as a required detail or choice at approval time (a new listing with no link has to get a link, `approve_without_link` for trusted calendars, or `no_link_yet`), or through `recheck`, which runs after the agent and turns new verification problems into follow-up questions. A finish blocker that no question could have prevented is a bug in `weekly_review.py`.
- Before asking anyone for a link, `weekly_review.py` looks in our own data (`link_leads`: same-night copies from any scraper or store pool, organizer pages from `data/sources.json`, other nights of the same event) and attaches a lead that passes the link guard without a question. Leads that do not pass go into the question as `evidence.link_leads`.
- Archive an event that is not happening with `archive_event(..., hold=HOLD_CANCELLED)`; a link-less event from a source without `publish_without_link` is held with `HOLD_NO_LINK`. Ingest never reactivates a cancelled hold, and lifts a no-link hold only when the source lists the event with a link. Anything else that ingest reactivates has its old verification cleared so it is checked again.
- Links attached to events must pass `scripts/link_guard.py` (page loads, names the event, states the event's date if any).
- Before publishing, resolve doctor blockers: scraper failures, stale Facebook evidence, pending reviews, missing coordinates, verification failures, duplicate active events, venue conflicts, and publish tripwire risk.
- `rejected.json` is an audit queue, not necessarily a release blocker; review new or unexplained entries.
- Run `npm run doctor`, the relevant Python tests, `npm test`, `npm run typecheck`, and `npm run build` before handing off changes that affect the pipeline or site.
- Preserve unrelated working-tree changes. Do not commit or push unless the user asks.

