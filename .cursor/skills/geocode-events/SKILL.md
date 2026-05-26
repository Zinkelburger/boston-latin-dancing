---
name: geocode-events
description: >-
  Geocode dance events for the boston-latin-dance map. Use when fixing bad
  coordinates, adding coordinates to events with missing locations, running
  the scrape pipeline, or when an event appears in the wrong place on the map.
---

# Geocode Events

Strict priority workflow for getting correct coordinates. Never guess -- either
get a verified address or flag for manual resolution.

## Priority Order

1. **Check the known-venue table** in `scripts/scraper_utils.py` (`VENUE_COORDS`)
2. **Scrape Eventbrite** (ground truth) -- if the event has an Eventbrite URL
3. **Clean and geocode the location string** via Nominatim with fallback variants
4. **Flag for manual lookup** -- never accept a bad coordinate

## Step 1: Known Venue Table

The file `scripts/scraper_utils.py` contains the single `VENUE_COORDS` dict for
venues that only have a name (no street address). When adding new venues:

```python
VENUE_COORDS = {
    "lili latin dance": (42.336527, -71.047731),
    "shipyard park": (42.3731772, -71.0526574),
    # ... add new venues here
}
```

This is the ONLY place venue coords live. Do NOT add them elsewhere.

## Step 2: Scrape Eventbrite for Venue Data

Eventbrite embeds `schema.org/Place` JSON-LD in every public event page. No API
key or auth needed.

```bash
curl -s "EVENTBRITE_URL" \
  -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" \
  | grep -oP '"location":\s*\{[^}]+\}'
```

Use the `streetAddress` field as the geocoding input.

## Step 3: Geocode via Nominatim

Query Nominatim with the address. Rate limit: max 1 request per second.

### Build query variants (try in order until one works)

1. **Deduplicate** repeated city/state segments
2. **Full cleaned address** (with street number)
3. **Without street number** -- parks and venues often don't resolve with a number
4. **Venue name + city + state** -- e.g. "Shipyard Park, Charlestown, Boston, MA"

### Validation rule

**Any coordinate must be within 50km of downtown Boston (42.36, -71.06).**

If all variants fail validation or return nothing, flag for manual lookup.

## Step 4: If Nothing Works

If geocoding fails for an event:
- Check Google Maps manually for the venue/address
- Use web search to find the venue's coordinates
- Add the venue to `VENUE_COORDS` in `scripts/scraper_utils.py`
- Never leave a bad coordinate -- either fix it or set `lat`/`lng` to `null`

## After Fixing

Re-run `npm run publish-events` to regenerate `public/events.json`.
