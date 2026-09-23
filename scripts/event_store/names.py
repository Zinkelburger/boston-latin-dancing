"""Event-name normalisation and the word-overlap tests dedup and series
collapsing are built on."""

import re

from scraper_utils import detect_styles


_STOPWORDS = frozenset({"the", "at", "in", "and", "of", "by", "a", "an", "y"})


def normalize_name(name: str) -> str:
    """Lowercase, drop dates / edition numbers, drop punctuation, squeeze spaces.

    Date and number stripping runs *before* punctuation stripping: "9/12",
    "Vol. 3" and "#4" only exist while the slash, dot and hash are still there.
    The old order removed the punctuation first, so "Salsa Social 9/12" became
    "salsa social 912" and three of these patterns could never match.
    """
    name = name.lower()
    name = re.sub(r"\b\d{1,2}[/\-]\d{1,2}([/\-]\d{2,4})?\b", " ", name)
    name = re.sub(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}\w*\b", " ", name, flags=re.I)
    name = re.sub(r"\bvol\s*\.?\s*\d+\b", " ", name)
    name = re.sub(r"#\d+", " ", name)
    name = re.sub(r"\b\d{1,2}(st|nd|rd|th)\b", " ", name)
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def content_words(name: str) -> set[str]:
    return set(name.split()) - _STOPWORDS


# Words that appear in a large share of event names on a Latin-dance site and
# therefore carry almost no identifying signal. Two events sharing only
# {"salsa", "bachata"} are not evidence of the same event — on this site that
# describes most of the calendar. Overlap on these alone must not drive a merge.
_GENERIC_DANCE_WORDS = frozenset({
    "salsa", "bachata", "kizomba", "zouk", "merengue", "cumbia", "chacha",
    "timba", "rueda", "reggaeton", "dembow", "mambo",
    "latin", "latino", "latina", "afrolatin",
    "dance", "dancing", "dancers", "social", "socials", "party", "parties",
    "night", "nights", "music", "live", "dj", "event", "events",
})

# Tokens shorter than this identify nothing on their own ("w" from "w/ Tina",
# "co" from "Dance Co"), so they cannot serve as the distinguishing word.
_DISTINCTIVE_MIN_LEN = 3


def distinctive_words(words: set[str]) -> set[str]:
    """Words specific enough to identify a particular event."""
    return {w for w in words
            if len(w) >= _DISTINCTIVE_MIN_LEN and w not in _GENERIC_DANCE_WORDS}


# Minimum token length eligible for fuzzy (1-edit) matching. One- and two-
# letter tokens ("dj", "w", "co") are within an edit of almost anything, so
# they must match exactly. Three letters is the floor because real spelling
# variants live there: "Kiz Thursday" and "Kizz Thursday" are the same night.
_FUZZY_MIN_LEN = 3


def _edit_distance_le1(a: str, b: str) -> bool:
    """True if a and b are within one insert/delete/substitute of each other."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = j = edits = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if la == lb:        # substitution
            i += 1
            j += 1
        elif la > lb:       # deletion from a
            i += 1
        else:               # insertion into a
            j += 1
    if i < la or j < lb:    # trailing leftover char
        edits += 1
    return edits <= 1


def shared_word_count(words_a: set[str], words_b: set[str]) -> int:
    """Count shared words, allowing one-character typos on longer tokens.

    e.g. {"kizz","thursday"} vs {"kiz","thursday"} -> 2, so spelling variants
    of the same event name ("Kizz" vs "Kiz") still register as a match.
    """
    remaining = set(words_b)
    unmatched: list[str] = []
    shared = 0
    for w in words_a:
        if w in remaining:
            remaining.discard(w)
            shared += 1
        else:
            unmatched.append(w)
    for w in unmatched:
        if len(w) < _FUZZY_MIN_LEN:
            continue
        match = next(
            (x for x in remaining
             if len(x) >= _FUZZY_MIN_LEN and _edit_distance_le1(w, x)),
            None,
        )
        if match is not None:
            remaining.discard(match)
            shared += 1
    return shared


def name_styles(name: str) -> set[str]:
    """Dance styles named in a title, via the scraper's own keyword list."""
    return {s for s in detect_styles(name or "") if s != "other"}


# Signals that a name is a distinctly-branded special edition rather than a
# regular occurrence of a series: anniversaries, festivals, guest artists
# ("ft"/"featuring"), guest-promoter takeovers, holiday/themed nights, lineups
# ("vs"). Such an event keeps its own map pin instead of being folded into the
# generic series name (or a venue hub). Run against normalize_name() output
# (lowercased, punctuation stripped).
_SPECIAL_EDITION_RE = re.compile(
    r"\b(?:anniversary|anniversaries|\d+\s*year|festival|festiva|edition|"
    r"ft|feat|featuring|takeover|special|halloween|nye|new year|christmas|"
    r"valentine|vs)\b",
    re.I,
)


def is_special_edition(name: str) -> bool:
    return bool(_SPECIAL_EDITION_RE.search(name or ""))


# Collapsing is held to a higher bar than dedup's 0.5: folding two
# occurrences into one pin is invisible to visitors, so the names must share
# the larger part of their words.
_SERIES_OVERLAP_RATIO = 0.6


def names_are_same_series(a: str, b: str) -> bool:
    if a == b:
        return True
    if a in b or b in a:
        return True
    words_a = content_words(a)
    words_b = content_words(b)
    if not words_a or not words_b:
        return False
    shared = shared_word_count(words_a, words_b)
    smaller = min(len(words_a), len(words_b))
    return shared >= max(2, smaller * _SERIES_OVERLAP_RATIO)
