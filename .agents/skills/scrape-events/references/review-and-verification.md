# Review, verification, and publishing

## Lifecycle queues

- Active events are candidates for publishing.
- Pending events need a human decision, usually because deduplication was
  uncertain.
- Rejected events are an audit trail. Non-Latin or out-of-scope events belong
  here; the queue is not automatically a release blocker.
- Archived events remain historical records and may appear in generated output
  according to the publish window.

Use MCP lifecycle tools for decisions so changelog and invariants remain intact.
Reject instruction-only entries with a specific reason. Resolve pending duplicate
pairs against URLs, organizer, date/time, and venue rather than title alone.

## Coordinates

Every publishable active event needs valid coordinates. Prefer venue coordinates
for recurring venue-backed events. Use location overrides only when source text
is consistently ambiguous; document why the override is necessary.

## Verification

Run `event_verify` before publishing. Automated verification statuses other than
`confirmed` and `reachable_only` are blockers. `reachable_only` is a warning that
deserves review for high-risk or changed events.

When a page cannot be verified headlessly, inspect it in a browser and call
`event_verify_attest` with the evidence URL, observed facts, status, and notes.
Attestations expire and are tied to a fingerprint of the event facts, so editing
the occurrence invalidates stale evidence automatically.

## Preflight and publish

Run `npm run doctor`. Fix blockers for:

- failed or structurally missing enabled scrapers;
- missing or stale Facebook browser evidence;
- unresolved pending items;
- missing coordinates;
- verification failures or missing coverage;
- active duplicate pairs;
- recurring-venue schedule conflicts or odd hours;
- generated artifact drift; and
- publish-tripwire risk.

Then call `event_publish()`. A tripwire protects against accidentally replacing a
healthy published set with a sharply smaller one. Investigate the cause instead
of forcing a publish.

