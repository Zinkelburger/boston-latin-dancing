"""/event/<slug> URLs for published events."""

import hashlib
import re
from unicodedata import normalize as unicode_normalize

import atomic_io


def slug_base(name: str) -> str:
    base = unicode_normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60]


def slugify(name: str, event_id: str) -> str:
    return f"{slug_base(name)}-{event_id[:8].lower()}"


def resolve_slug_collisions(events: list[dict]) -> list[tuple[str, str]]:
    """Give every published event a /event/ URL of its own.

    slugify() suffixes the name with the first 8 characters of the id, and
    whole families of ids share those 8 characters ("fiesta-2026...",
    "bobas-2026..."). Colliding events all answered on one URL, and the site's
    findBySlug() returns whichever comes first in the published list, so the
    shipped URL for one Fiesta night rendered a different night at a different
    venue and the rest were unreachable.

    The slug the registry already bound to an id stays with that id — that URL
    is public — and every other member of the collision falls back to a hash of
    its id, which is stable across runs. Returns the (id, slug) pairs moved.
    """
    by_slug: dict[str, list[dict]] = {}
    for ev in events:
        if ev.get("slug"):
            by_slug.setdefault(ev["slug"], []).append(ev)
    collisions = {slug: evs for slug, evs in by_slug.items() if len(evs) > 1}
    if not collisions:
        return []

    # A missing registry just means no slug has shipped yet. A corrupt one
    # raises: rebuilding "who owns this URL" from nothing would silently
    # reassign public URLs.
    from slug_registry import REGISTRY_PATH
    registry = atomic_io.read_json(REGISTRY_PATH, default={"entries": {}})
    entries = registry.get("entries") or {}
    shipped = {slug: e["id"] for slug, e in entries.items() if e.get("id")}

    moved: list[tuple[str, str]] = []
    for slug, group in collisions.items():
        ids = sorted(ev.get("id") or "" for ev in group)
        keeper = shipped.get(slug)
        if keeper not in ids:
            keeper = ids[0]
        for ev in group:
            if ev.get("id") == keeper:
                continue
            digest = hashlib.md5((ev.get("id") or ev["slug"]).encode()).hexdigest()[:8]
            ev["slug"] = f"{slug_base(ev.get('name', ''))}-{digest}"
            moved.append((ev.get("id") or "?", ev["slug"]))
    return moved
