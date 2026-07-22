#!/usr/bin/env python3
"""
Source noisiness ranking.

Every source in ``data/sources.json`` carries a hand-set ``noise`` block::

    "noise": { "score": 0-100, "note": "why" }

The score is a human judgment ("you can tell from the way it looks"): 0 means a
dedicated, on-topic feed where nearly everything is a real Latin social; 100
means a giant general calendar where almost everything is noise. This module
turns that one number into a tier + a concrete ACTION so a downstream reviewer
(often a small/cheap model) doesn't have to reason about trust -- it's told:

    trusted  -> auto-publish new finds
    mixed    -> publish, but eyeball new finds
    noisy    -> quarantine new finds to pending for review (see ingest_scraped)

Run ``python3 scripts/source_signal.py`` (or ``npm run source-signal``) to print
the ranked table.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import load_sources

# Band thresholds (inclusive lower bound). Ordered high-noise first.
TIERS = [
    (67, "noisy",   "🔴", "review_new",  "quarantine new finds for review"),
    (34, "mixed",   "🟡", "spot_check",  "publish, but eyeball new finds"),
    (0,  "trusted", "🟢", "auto_publish", "auto-publish new finds"),
]
DEFAULT_SCORE = 40  # unranked source -> treat as mixed, not blindly trusted


def _band(score: int):
    for lo, tier, emoji, action, guidance in TIERS:
        if score >= lo:
            return tier, emoji, action, guidance
    return TIERS[-1][1:]


def signal_for(source: dict) -> dict:
    """Return the resolved signal for one source dict."""
    noise = source.get("noise") or {}
    score = int(noise.get("score", DEFAULT_SCORE))
    tier, emoji, action, guidance = _band(score)
    return {
        "id": source.get("id"),
        "name": source.get("name", source.get("id")),
        "score": score,
        "tier": tier,
        "emoji": emoji,
        "action": action,
        "guidance": guidance,
        "note": noise.get("note", ""),
    }


def all_signals() -> list[dict]:
    """Signals for every source, ranked noisiest-first."""
    sigs = [signal_for(s) for s in load_sources()]
    sigs.sort(key=lambda s: s["score"], reverse=True)
    return sigs


def noisy_source_ids() -> set:
    """Source ids whose new finds should be quarantined for review."""
    return {s["id"] for s in all_signals() if s["action"] == "review_new"}


def _bar(score: int, width: int = 10) -> str:
    filled = round(score / 100 * width)
    return "▉" * filled + "▏" * (width - filled)


def render_table() -> str:
    """Human/LLM-readable ranked table."""
    rows = all_signals()
    idw = max((len(s["id"]) for s in rows), default=6)
    lines = [
        "SOURCE NOISE RANKING  (higher = more junk; trust accordingly)",
        "",
    ]
    for s in rows:
        lines.append(
            f"  {s['id']:<{idw}}  {_bar(s['score'])} {s['score']:>3}  "
            f"{s['emoji']} {s['tier'].upper():<7} → {s['guidance']}"
        )
        if s["note"]:
            lines.append(f"  {'':<{idw}}  {s['note']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_table())
