#!/usr/bin/env python3
"""
Broken-link checker for every URL we ship.

A dead link is worse than a missing event: the pin is on the map, someone
plans their night around it, and the tap goes nowhere. This checks every
outbound URL in the published feed plus the venue and source lists.

The hard part is not fetching — it is knowing when a status code means
anything. Measured behaviour, not assumed (see tests/test_link_check.py):

  facebook.com   With a browser UA every request returns 400, live or dead.
                 With `facebookexternalhit/1.1` the og-scraper path answers
                 honestly: 404 for a deleted event, 200 for a real one.
                 A dead *share wrapper* still returns 200, so status alone
                 is not enough — a live page carries a real <title>, a dead
                 one carries none or the bare chrome title "Facebook".
  instagram.com  Returns 200 with a near-identical login wall for real and
                 nonexistent handles alike, so the status is useless — but
                 asked as the og-scraper it still emits an og:title for a
                 real profile ("Noise | Latin luxury elevated (@noise.boston)")
                 and none at all for a handle that does not exist.
  eventbrite     Honest 404s. Trust the status.

Anything we cannot prove dead is never reported as broken — this runs
unattended and a false alarm that pulls a real event is the expensive
failure, not a missed one.

Usage:
    python3 scripts/check_links.py                  # check everything, write report
    python3 scripts/check_links.py --fail-on-broken # exit 1 if any link is broken
    python3 scripts/check_links.py --only-live      # skip archived events
    python3 scripts/check_links.py --url URL        # check a single URL
"""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
PUBLISHED = ROOT / "data" / "events-published.json"
ACTIVE = ROOT / "data" / "events" / "active.json"
VENUES = ROOT / "data" / "venues.json"
SOURCES = ROOT / "data" / "sources.json"
REPORT_PATH = ROOT / "data" / "link-check.json"

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Meta's own og-scraper UA. Facebook answers it honestly; it answers browser
# UAs from datacenter IPs with a blanket 400.
FB_BOT_UA = "facebookexternalhit/1.1"

TIMEOUT = 20
RETRIES = 3
WORKERS = 8

OK = "ok"
BROKEN = "broken"
UNVERIFIABLE = "unverifiable"

# Titles Facebook serves on its generic chrome when there is no real content
# behind the URL.
_FB_EMPTY_TITLES = {"", "facebook", "log in or sign up to view", "log into facebook"}
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)', re.I
)
_SHARE_WRAPPER_RE = re.compile(r"/events/s/|/share/")


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _title(html: str) -> str:
    m = _TITLE_RE.search(html)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


def _og_title(html: str) -> str:
    m = _OG_TITLE_RE.search(html)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


def classify(url: str, status: Optional[int], html: str, error: Optional[str] = None) -> tuple[str, str]:
    """Map a fetch outcome onto (verdict, note).

    Pure function over an already-performed fetch so the policy is testable
    without a network. `status` is None when the request never completed.
    """
    if not url:
        return UNVERIFIABLE, "no url"

    host = _host(url)

    if error and status is None:
        # Transport failure after retries. DNS death is strong evidence the
        # host is gone; a timeout or reset is not.
        if error in ("ConnectionError", "SSLError"):
            return BROKEN, f"unreachable ({error})"
        return UNVERIFIABLE, f"transient network failure ({error})"

    if "instagram.com" in host:
        if status == 404:
            return BROKEN, "HTTP 404"
        if status and status >= 500:
            return UNVERIFIABLE, f"instagram server error (HTTP {status})"
        if status in (401, 403, 429):
            return UNVERIFIABLE, f"instagram rate-limited us (HTTP {status})"
        og = _og_title(html)
        if og:
            return OK, f"live ({og.split(' • ')[0][:60]})"
        # The login wall is served either way; only the absent og:title
        # separates a dead handle from a real one.
        return BROKEN, "no profile metadata — handle does not exist"

    if "facebook.com" in host or "fb.com" in host:
        if status == 404:
            return BROKEN, "HTTP 404"
        if status == 400:
            # Browser-UA blanket rejection leaked through; we learned nothing.
            return UNVERIFIABLE, "facebook refused the request (HTTP 400) — status carries no signal"
        if status and status >= 500:
            return UNVERIFIABLE, f"facebook server error (HTTP {status})"
        title = _title(html).lower()
        if title in _FB_EMPTY_TITLES:
            if _SHARE_WRAPPER_RE.search(url.lower()):
                return BROKEN, "share wrapper resolves to no event (no page title)"
            return UNVERIFIABLE, "no page title — login wall or deleted, cannot distinguish"
        return OK, f"live ({_title(html)[:60]})"

    if status is None:
        return UNVERIFIABLE, "no response"
    if status == 404 or status == 410:
        return BROKEN, f"HTTP {status}"
    if status >= 500:
        return UNVERIFIABLE, f"server error (HTTP {status})"
    if status in (401, 403, 429):
        return UNVERIFIABLE, f"access refused (HTTP {status}) — cannot confirm either way"
    if status >= 400:
        return BROKEN, f"HTTP {status}"
    return OK, f"HTTP {status}"


def fetch(url: str) -> dict:
    """Fetch a URL with the strategy its host actually responds to."""
    host = _host(url)
    # Instagram is Meta's too, and answers the same og-scraper UA with the
    # profile metadata that tells a real handle from a dead one.
    is_meta = any(h in host for h in ("facebook.com", "fb.com", "instagram.com"))
    headers = {"User-Agent": FB_BOT_UA if is_meta else BROWSER_UA}

    status, html, error, final = None, "", None, None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
            status, final = resp.status_code, resp.url
            # Only the Meta hosts are classified on their body.
            html = resp.text if is_meta else ""
            error = None
            break
        except requests.RequestException as exc:
            error = type(exc).__name__
            if attempt < RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))

    verdict, note = classify(url, status, html, error)
    return {
        "url": url,
        "status": status,
        "final_url": final if final and final != url else None,
        "verdict": verdict,
        "note": note,
    }


# ── collecting every URL we ship ──────────────────────────────────────

def collect_targets(only_live: bool = False) -> dict[str, list[str]]:
    """Map each URL to the human-readable places it appears."""
    targets: dict[str, list[str]] = {}

    def add(url: Optional[str], where: str) -> None:
        if not url or not str(url).startswith("http"):
            return
        targets.setdefault(url, [])
        if where not in targets[url]:
            targets[url].append(where)

    if PUBLISHED.exists():
        for ev in json.loads(PUBLISHED.read_text()):
            if only_live and ev.get("archived"):
                continue
            label = f"event: {ev.get('name', ev.get('id', '?'))}"
            add(ev.get("url"), label)
            add(ev.get("artistUrl"), label + " (artist)")
            for u in ev.get("urls") or []:
                add(u, label + " (alt)")

    if VENUES.exists():
        raw = json.loads(VENUES.read_text())
        for v in raw.get("venues", raw) if isinstance(raw, dict) else raw:
            add(v.get("url"), f"venue: {v.get('name', v.get('id', '?'))}")

    if SOURCES.exists():
        raw = json.loads(SOURCES.read_text())
        for s in raw.get("sources", raw) if isinstance(raw, dict) else raw:
            if s.get("enabled") is False:
                continue
            add(s.get("url"), f"source: {s.get('id', '?')}")

    return targets


def manual_check_queue() -> list[dict]:
    """Events a human has to look at, because no automated check can settle them.

    Set on the event as `_needs_manual_check` when the link cannot be resolved
    programmatically — a Facebook share wrapper pointing at a photo has no
    event behind it to verify, and guessing a replacement link is worse than
    saying so. Carried here so it lands in the same report as everything else
    rather than sitting in a field nobody opens.
    """
    if not ACTIVE.exists():
        return []
    queue = []
    for ev in json.loads(ACTIVE.read_text()):
        flag = ev.get("_needs_manual_check")
        if not flag:
            continue
        queue.append({
            "id": ev.get("id"),
            "name": ev.get("name"),
            "startDate": ev.get("startDate"),
            "url": ev.get("url"),
            "reason": flag.get("reason") if isinstance(flag, dict) else str(flag),
            "flagged_at": flag.get("flagged_at") if isinstance(flag, dict) else None,
        })
    return queue


def _guard_against_a_blocked_host(results: list[dict]) -> None:
    """Downgrade a whole host's failures when it has plainly started blocking us.

    Profile metadata is the only thing separating a live Instagram handle from
    a dead one, so the day Instagram decides to stop serving it to datacenter
    IPs, every link we have flips to "broken" at once. Sixteen accounts do not
    vanish overnight; a host-wide block is the likelier story, and acting on it
    would pull real events off the map. Only applies when a host fails
    wholesale — genuine dead links show up among live ones.
    """
    for host_fragment in ("instagram.com", "facebook.com"):
        group = [r for r in results if host_fragment in _host(r["url"])]
        if len(group) < 3:
            continue
        broken = [r for r in group if r["verdict"] == BROKEN]
        if len(broken) == len(group):
            for r in broken:
                r["verdict"] = UNVERIFIABLE
                r["note"] = (f"{host_fragment} returned nothing usable for any of "
                             f"{len(group)} links — treating as blocked, not dead")


def check_all(only_live: bool = False) -> dict:
    targets = collect_targets(only_live)
    urls = sorted(targets)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(fetch, urls))

    _guard_against_a_blocked_host(results)

    for r in results:
        r["appears_in"] = targets[r["url"]]

    counts = {OK: 0, BROKEN: 0, UNVERIFIABLE: 0}
    for r in results:
        counts[r["verdict"]] += 1

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "live" if only_live else "all",
        "totals": {"urls": len(results), **counts},
        "needs_manual_check": manual_check_queue(),
        "broken": [r for r in results if r["verdict"] == BROKEN],
        "unverifiable": [r for r in results if r["verdict"] == UNVERIFIABLE],
        "ok": [r for r in results if r["verdict"] == OK],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Check every shipped URL for breakage.")
    ap.add_argument("--fail-on-broken", action="store_true",
                    help="exit 1 if any link is provably broken")
    ap.add_argument("--only-live", action="store_true",
                    help="skip archived events")
    ap.add_argument("--url", help="check a single URL and exit")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args()

    if args.url:
        r = fetch(args.url)
        print(json.dumps(r, indent=2))
        return 1 if (args.fail_on_broken and r["verdict"] == BROKEN) else 0

    report = check_all(only_live=args.only_live)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")

    if args.json:
        print(json.dumps(report, indent=2))
        return 1 if (args.fail_on_broken and report["broken"]) else 0

    t = report["totals"]
    print(f"checked {t['urls']} urls — {t[OK]} ok, {t[BROKEN]} broken, "
          f"{t[UNVERIFIABLE]} unverifiable")

    if report["broken"]:
        print("\nBROKEN:")
        for r in report["broken"]:
            print(f"  {r['note']:<50} {r['url']}")
            for where in r["appears_in"]:
                print(f"      ↳ {where}")

    if report["needs_manual_check"]:
        print(f"\nVERIFY BY HAND ({len(report['needs_manual_check'])}) — nothing automated can settle these:")
        for item in report["needs_manual_check"]:
            print(f"  {item['name']}")
            print(f"      {item['reason']}")
            if item.get("url"):
                print(f"      current link: {item['url']}")

    if report["unverifiable"]:
        print(f"\nUNVERIFIABLE ({len(report['unverifiable'])}) — not proof of breakage:")
        by_note: dict[str, int] = {}
        for r in report["unverifiable"]:
            key = r["note"].split(" (")[0]
            by_note[key] = by_note.get(key, 0) + 1
        for note, n in sorted(by_note.items(), key=lambda x: -x[1]):
            print(f"  {n:4d}  {note}")

    print(f"\nreport written to {REPORT_PATH.relative_to(ROOT)}")
    return 1 if (args.fail_on_broken and report["broken"]) else 0


if __name__ == "__main__":
    sys.exit(main())
