"""
Genre derivation.

Subjects arriving from Open Library and Gutenberg are free text written by
thousands of different cataloguers over decades. The same book might carry
"Detective and mystery stories", "Fiction, mystery & detective, traditional",
"Crime -- Fiction", or just "Mystery". None of that can be put in a dropdown.

So subjects are matched against a fixed taxonomy and reduced to a handful of
genres a reader would actually recognise. The taxonomy is deliberately small:
a filter with sixty options is a filter nobody uses.

Two rules that keep this honest:

  * A book can hold several genres. Forcing one loses the fact that Piranesi
    is both fantasy and literary, which is most of why someone picks it up.
  * A book that matches nothing gets no genre rather than a guess. "Unsorted"
    is a truthful answer; inventing a genre from a thin subject list is not.
"""

from __future__ import annotations

import re

# Order matters only for readability — every rule is tested against every
# book. Patterns are matched against the joined, lowercased subject list.
GENRE_RULES: dict[str, list[str]] = {
    "Fantasy": [
        r"\bfantasy\b", r"\bmagic\b", r"\bdragons?\b", r"\bwizards?\b",
        r"\bfairy tales?\b", r"\bmythology\b", r"\bfolklore\b", r"\bquests?\b",
        r"\bimaginary (places|wars)\b", r"\bsword and sorcery\b", r"\bfae\b",
        r"\bwitch(es|craft)?\b", r"\bepic fantasy\b", r"\bromantasy\b",
    ],
    "Science fiction": [
        r"\bscience fiction\b", r"\bspace\b", r"\bdystopia\b", r"\butopia\b",
        r"\btime travel\b", r"\brobots?\b", r"\bartificial intelligence\b",
        r"\bextraterrestrial\b", r"\baliens?\b", r"\bspeculative\b",
        r"\bcyberpunk\b", r"\bpost-?apocalyptic\b", r"\bhard sf\b",
        r"\bfuture\b", r"\binterstellar\b",
    ],
    "Mystery & crime": [
        r"\bmystery\b", r"\bdetective\b", r"\bcrime\b", r"\bmurder\b",
        r"\bnoir\b", r"\bwhodunn?it\b", r"\bpolice\b", r"\binvestigat",
        r"\bthriller\b", r"\bsuspense\b", r"\bespionage\b", r"\bspies\b",
        r"\bheist\b", r"\bcosy mystery\b", r"\bcozy mystery\b",
    ],
    "Horror": [
        r"\bhorror\b", r"\bghost\b", r"\bhaunt", r"\bvampires?\b",
        r"\bmonsters?\b", r"\bsupernatural\b", r"\bterror\b", r"\boccult\b",
        r"\bzombie", r"\bcosmic horror\b", r"\bgothic horror\b",
    ],
    "Romance": [
        r"\bromance\b", r"\blove stories\b", r"\bcourtship\b",
        r"\bmarriage\b", r"\bfake dating\b", r"\benemies to lovers\b",
        r"\bcontemporary romance\b", r"\bnew adult\b", r"\berotic",
    ],
    "Historical": [
        r"\bhistorical fiction\b", r"\bhistorical\b", r"\bhistory -- fiction\b",
        r"\bwar\b", r"\bmedieval\b", r"\bvictorian\b", r"\bregency\b",
        r"\btudor\b", r"\bancient\b", r"\bcivil war\b", r"\bworld war\b",
        r"\b19th century\b", r"\b18th century\b",
    ],
    "Literary": [
        r"\bliterary\b", r"\bliterary fiction\b", r"\bpsychological fiction\b",
        r"\bdomestic fiction\b", r"\bmodernism\b", r"\bstream of consciousness\b",
        r"\bexperimental\b", r"\bmetafiction\b", r"\bautofiction\b",
        r"\bbildungsroman\b", r"\bcoming of age\b",
    ],
    "Classics": [
        r"\bclassics?\b", r"\bharvard classics\b", r"\bbest books ever\b",
        r"\bcanon\b", r"\bworld literature\b",
    ],
    "Gothic": [
        r"\bgothic\b", r"\bdecadence\b", r"\bruins?\b", r"\bmoors\b",
    ],
    "Adventure": [
        r"\badventure\b", r"\bsea stories\b", r"\bpirates?\b", r"\bsurvival\b",
        r"\bexploration\b", r"\bvoyages?\b", r"\bwestern\b", r"\bwhaling\b",
        r"\btreasure\b", r"\bshipwreck",
    ],
    "Short stories": [
        r"\bshort stories\b", r"\bcollections?\b", r"\bnovellas?\b",
        r"\banthology\b", r"\btales\b",
    ],
    "Young adult": [
        r"\byoung adult\b", r"\bteen", r"\bjuvenile fiction\b",
        r"\bchildren'?s? (literature|fiction|stories)\b", r"\bmiddle grade\b",
        r"\bschool stories\b",
    ],
    "Humour": [
        r"\bhumou?r\b", r"\bcomic\b", r"\bsatire\b", r"\bparody\b",
        r"\bwit\b", r"\bcomedy\b",
    ],
    "Translated": [
        r"\btranslat", r"\brussian literature\b", r"\bfrench literature\b",
        r"\bjapanese literature\b", r"\bgerman literature\b",
        r"\bkorean\b", r"\bitalian literature\b", r"\bspanish literature\b",
    ],
}

COMPILED = {
    genre: [re.compile(pattern, re.I) for pattern in patterns]
    for genre, patterns in GENRE_RULES.items()
}

ALL_GENRES = sorted(GENRE_RULES)

# Some subjects are so broad they would tag half the corpus. "Fiction" on its
# own says only that it is not a manual.
IGNORED = re.compile(r"^\s*(fiction|general|adult|literature|novel|books?)\s*$", re.I)


def derive(subjects: list[str] | None, extra: str = "", limit: int = 3) -> list[str]:
    """
    Reduce a subject list to at most a few recognisable genres.

    `extra` lets a description be folded in for books whose subjects are thin,
    which is common for recent titles.

    Capped at three. A book tagged with eight genres is not filterable — it
    appears under everything, which is the same as appearing under nothing.
    """
    if not subjects and not extra:
        return []

    usable = [s for s in (subjects or []) if s and not IGNORED.match(s)]
    haystack = " ; ".join(usable).lower()
    if extra:
        haystack += " ; " + extra[:600].lower()

    if not haystack.strip():
        return []

    scored: list[tuple[int, str]] = []
    for genre, patterns in COMPILED.items():
        hits = sum(1 for pattern in patterns if pattern.search(haystack))
        if hits:
            scored.append((hits, genre))

    # Most matches first, then alphabetically so the result is stable across
    # runs rather than depending on dict ordering.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [genre for _, genre in scored[:limit]]


def label_counts(books) -> list[dict]:
    """Genres present in a corpus, with counts, for building the filter."""
    counts: dict[str, int] = {}
    for book in books:
        for genre in (book.genres or []):
            counts[genre] = counts.get(genre, 0) + 1
    return [
        {"genre": genre, "count": count}
        for genre, count in sorted(counts.items(), key=lambda p: (-p[1], p[0]))
    ]