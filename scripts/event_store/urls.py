"""Event links: ranking which URL should be primary, and gathering the
URLs of merged records."""

import re


def url_match(a: dict, b: dict) -> bool:
    """Check if both events share any URL across url + urls[] fields."""
    def _all_urls(ev: dict) -> set[str]:
        result: set[str] = set()
        u = (ev.get("url") or "").rstrip("/").lower()
        if u:
            result.add(u)
        for extra in ev.get("urls") or []:
            norm = extra.rstrip("/").lower()
            if norm:
                result.add(norm)
        return result

    urls_a = _all_urls(a)
    urls_b = _all_urls(b)
    return bool(urls_a & urls_b)


def url_host(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url.lower())
    return m.group(1) if m else ""


# Facebook share wrappers (/events/s/<slug>/<share-id>/, /share/<id>) carry a
# share-story id rather than an event id and do not resolve for logged-out
# visitors, so they are the worst possible primary link.
_SHARE_WRAPPER_RE = re.compile(r"facebook\.com/(?:events/s/|share/)|fb\.me/", re.I)


def url_rank(url: str) -> int:
    """Rank a URL's fitness as the primary (clicked) link. Lower wins.

    Deliberately separate from SOURCE_PRIORITY: that ranks how much we trust a
    source's claim that an event exists, which is unrelated to how good that
    source's links are. beatrice-calendar outranks lister-events on coverage
    but ships Facebook share wrappers, so letting one number decide both
    replaced an organizer's canonical page with a link that 404s for visitors.
    """
    if not url:
        return 100
    lower = url.lower()
    host = url_host(url)
    if _SHARE_WRAPPER_RE.search(lower):
        return 40
    if "facebook.com" in host or "fb.com" in host:
        return 20 if "/events/" in lower else 30
    if "instagram.com" in host:
        return 30
    return 10


def event_url_list(ev: dict) -> list[str]:
    return [u for u in [ev.get("url"), *(ev.get("urls") or [])] if u]


def url_key(url: str) -> str:
    """Comparison form for a URL, matching _collect_urls' dedup rule."""
    return (url or "").rstrip("/").lower()


def dropped_url_list(ev: dict) -> list[str]:
    """URLs a reviewer removed by hand, which re-scrapes must not resurrect."""
    return [u for u in (ev.get("_dropped_urls") or []) if u]


def collect_urls(*events: dict) -> list[str]:
    """Gather unique URLs from the given events, keeping one per domain."""
    seen_hosts: set[str] = set()
    seen_urls: set[str] = set()
    result: list[str] = []
    for ev in events:
        for u in event_url_list(ev):
            normalized = u.rstrip("/").lower()
            if normalized in seen_urls:
                continue
            host = url_host(u)
            if host and host in seen_hosts:
                continue
            seen_urls.add(normalized)
            if host:
                seen_hosts.add(host)
            result.append(u)
    return result
