---
name: geocode-events
description: >-
  Geocode dance events for the boston-latin-dance map. Use when fixing bad
  coordinates, adding coordinates to events with missing locations, running
  fetch-ics.ts, or when an event appears in the wrong place on the map.
---

# Geocode Events

Strict priority workflow for getting correct coordinates. Never guess -- either
get a verified address or flag for manual resolution.

## Priority Order

1. **Scrape Eventbrite** (ground truth) -- if the event has an Eventbrite URL
2. **Clean and geocode the location string** via Nominatim with fallback variants
3. **Check the known-venue table** in `scripts/fetch-ics.ts`
4. **Flag for manual lookup** -- never accept a bad coordinate

## Step 1: Scrape Eventbrite for Venue Data

Eventbrite embeds `schema.org/Place` JSON-LD in every public event page. No API
key or auth needed.

```bash
curl -s "EVENTBRITE_URL" \
  -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" \
  | grep -oP '"location":\s*\{[^}]+\}'
```

This returns structured data like:

```json
{
  "@type": "Place",
  "name": "Shore Leave",
  "address": {
    "@type": "PostalAddress",
    "addressLocality": "Boston",
    "addressRegion": "MA",
    "addressCountry": "US",
    "streetAddress": "11 William E Mullins Way, Boston, MA 02118"
  }
}
```

Use the `streetAddress` field as the geocoding input. The venue `name` can also
be used as a fallback query.

## Step 2: Geocode via Nominatim

Query Nominatim with the address. Rate limit: max 1 request per second.

```bash
curl -s "https://nominatim.openstreetmap.org/search?q=ENCODED_ADDRESS&format=json&limit=1&countrycodes=us" \
  -H "User-Agent: boston-latin-dance-dev/0.1"
```

### Build query variants (try in order until one works)

Given a raw location string like `"1 Shipyard Park, charlestown, MA 02129, charlestown, MA"`:

1. **Deduplicate** repeated city/state segments
2. **Full cleaned address** (with street number)
3. **Without street number** -- parks and venues often don't resolve with a number
4. **Venue name + city + state** -- e.g. "Shipyard Park, Charlestown, Boston, MA"
5. **Just the street address from Eventbrite** if available

### Validation rule

**Any coordinate must be within 50km of downtown Boston (42.36, -71.06).**

```python
import math
def dist_km(lat1, lng1, lat2, lng2):
    dlat = lat1 - lat2
    dlng = (lng1 - lng2) * math.cos(math.radians(lat1))
    return math.sqrt(dlat**2 + dlng**2) * 111

# Reject if > 50km from Boston
if dist_km(result_lat, result_lng, 42.36, -71.06) > 50:
    # BAD -- reject this result, try next variant
```

If all variants fail validation or return nothing, flag for manual lookup.

## Step 3: ICS geo: Coordinates Are Ignored

The ICS feed contains `X-APPLE-STRUCTURED-LOCATION` fields with embedded
`geo:lat,lng` values. These come from Apple MapKit auto-resolution and are
frequently wrong (e.g. "Shipyard Park, Charlestown" resolved to "Hingham
Shipyard" 30km away).

**`fetch-ics.ts` ignores ICS geo coordinates entirely.** Every event starts
with `lat: null, lng: null` and is geocoded from the location string using
the VENUE_COORDS table and Nominatim. All Nominatim results are validated
against the 50km-from-Boston rule before being accepted.

## Step 4: Known Venue Table

The file `scripts/fetch-ics.ts` contains a `VENUE_COORDS` lookup table for
venues that only have a name (no street address). When adding new venues,
update this table:

```typescript
const VENUE_COORDS: Record<string, { lat: number; lng: number }> = {
  'lili latin dance':        { lat: 42.336527, lng: -71.047731 },
  'shipyard park':           { lat: 42.3731772, lng: -71.0526574 },
  // ... etc
};
```

## Step 5: If Nothing Works

If geocoding fails for an event:
- Check Google Maps manually for the venue/address
- Use web search to find the venue's coordinates
- Add the venue to the `VENUE_COORDS` table in `scripts/fetch-ics.ts`
- Never leave a bad coordinate in `public/events.json` -- either fix it or
  set `lat`/`lng` to `null` (the event won't appear on the map, which is
  better than appearing in the wrong place)

## Updating events.json

After fixing coordinates, update `public/events.json` directly. The format is:

```json
{
  "id": "...",
  "name": "Event Name",
  "lat": 42.3731772,
  "lng": -71.0526574,
  "location": "Venue Name, Street Address, City, MA ZIP",
  ...
}
```

Also update the `location` field if the original was malformed or empty (e.g.
fill in the venue from Eventbrite).
