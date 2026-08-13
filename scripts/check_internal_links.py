#!/usr/bin/env python3
"""
Structural check on the built site: nothing we advertise may 404.

The outbound checker (check_links.py) asks whether other people's pages are
alive. This asks the harder-to-notice question — whether *our own* are. A URL
in sitemap.xml is a URL we handed to Google; when the page behind it stops
existing the listing stays in the index for weeks and every click lands on a
404. That is the failure that took "Battle of the Beats" off the map while it
was still showing in search results.

Three invariants, all checked against the exported `out/` directory so they
describe what actually ships rather than what the data says should:

  1. every <loc> in sitemap.xml resolves to a file
  2. every slug we have ever published resolves to a file (redirect or
     "ended" page — never a 404)
  3. every internal href in every built page resolves to a file

Offline and fast: no network, no guesswork.

Usage:
    python3 scripts/check_internal_links.py          # check, exit 1 on any break
    python3 scripts/check_internal_links.py --quiet  # only report failures
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
REGISTRY_PATH = ROOT / "data" / "slug-registry.json"

_LOC_RE = re.compile(r"<loc>(.*?)</loc>", re.S)
_HREF_RE = re.compile(r'href="(/[^"#?]*)', re.I)

# Emitted by the framework, not routes of ours.
_IGNORE_PREFIXES = ("/_next/", "/api/")


def _path_exists(url_path: str) -> bool:
    """Whether a site-absolute path is served by a real file in out/."""
    clean = unquote(urlparse(url_path).path).strip()
    if not clean.startswith("/"):
        return False
    rel = clean.strip("/")

    if not rel:
        return (OUT / "index.html").is_file()

    candidates = [
        OUT / rel,
        OUT / f"{rel}.html",
        OUT / rel / "index.html",
    ]
    return any(c.is_file() for c in candidates)


def check_sitemap() -> list[str]:
    sitemap = OUT / "sitemap.xml"
    if not sitemap.is_file():
        return ["sitemap.xml was not exported"]

    failures = []
    for loc in _LOC_RE.findall(sitemap.read_text()):
        path = urlparse(loc.strip()).path or "/"
        if not _path_exists(path):
            failures.append(f"sitemap advertises {path} but no page was exported")
    return failures


def check_registry() -> list[str]:
    if not REGISTRY_PATH.is_file():
        return []
    entries = json.loads(REGISTRY_PATH.read_text()).get("entries", {})
    failures = []
    for slug, entry in sorted(entries.items()):
        if not _path_exists(f"/event/{slug}"):
            failures.append(
                f"/event/{slug} was published before ({entry.get('status')}) "
                f"but no page was exported — indexed links to it now 404"
            )
        target = entry.get("target")
        if entry.get("status") == "alias" and target and not _path_exists(f"/event/{target}"):
            failures.append(f"/event/{slug} redirects to /event/{target}, which does not exist")
    return failures


def check_hrefs() -> list[str]:
    failures = []
    seen: set[tuple[str, str]] = set()
    for page in sorted(OUT.rglob("*.html")):
        html = page.read_text(errors="ignore")
        source = str(page.relative_to(OUT))
        for href in _HREF_RE.findall(html):
            if href.startswith(_IGNORE_PREFIXES) or (href, source) in seen:
                continue
            seen.add((href, source))
            if not _path_exists(href):
                failures.append(f"{source} links to {href}, which does not exist")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description="Check that nothing we ship 404s.")
    ap.add_argument("--quiet", action="store_true", help="print only failures")
    args = ap.parse_args()

    if not OUT.is_dir():
        print("out/ not found — run `npm run build` first", file=sys.stderr)
        return 2

    groups = [
        ("sitemap entries", check_sitemap()),
        ("previously-published urls", check_registry()),
        ("internal links", check_hrefs()),
    ]

    total = sum(len(f) for _, f in groups)
    for label, failures in groups:
        if failures:
            print(f"\nBROKEN — {label} ({len(failures)}):")
            for f in failures:
                print(f"  {f}")
        elif not args.quiet:
            print(f"ok — {label}")

    if total:
        print(f"\n{total} broken internal link(s)")
        return 1
    if not args.quiet:
        print("\nnothing we advertise 404s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
