"""
Trope tags.

Genres tell you what shelf a book sits on. Tropes tell you what actually
happens in it, which is closer to how readers talk: nobody says "I want
literary fiction", they say "I want the one where they hate each other first".

An honesty note, because this matters more here than for genres. Genre is
usually stated outright in a book's subject list, so deriving it is mostly
translation. Tropes are almost never catalogued, so these are **inferred**
from subject strings and blurb text. That makes them useful and fallible, and
the interface labels them as detected rather than confirmed.

Two consequences of that:

  * Patterns are written to be specific. "Enemies to lovers" needs the phrase
    or a close equivalent, not merely "rivals" plus "romance" — a loose rule
    would tag half the corpus and be worse than no tag at all.
  * A book with nothing detectable gets no tropes. Silence beats a guess.
"""

from __future__ import annotations

import re

# Each trope maps to patterns matched against subjects joined with the blurb.
# Grouped by what a reader would actually be in the mood for.
TROPE_RULES: dict[str, list[str]] = {
    # Relationship shapes
    "Enemies to lovers": [
        r"\benemies[ -]to[ -]lovers\b", r"\bfrom enemies to\b",
        r"\bsworn enemies\b", r"\bbitter rivals? who\b", r"\bhate[sd]? each other\b",
    ],
    "Friends to lovers": [
        r"\bfriends[ -]to[ -]lovers\b", r"\bbest friends? who fall\b",
        r"\blifelong friends? .{0,20}love\b",
    ],
    "Slow burn": [r"\bslow[ -]burn\b", r"\bsimmering .{0,15}tension\b"],
    "Fake relationship": [
        r"\bfake (dating|relationship|engagement|marriage)\b",
        r"\bpretend(ing)? to be (a )?(couple|married|engaged)\b",
        r"\bmarriage of convenience\b", r"\bconvenient marriage\b",
    ],
    "Forbidden love": [
        r"\bforbidden (love|romance|affair)\b", r"\bstar[- ]crossed\b",
        r"\billicit affair\b", r"\bfamilies? forbid\b",
    ],
    "Love triangle": [r"\blove triangle\b", r"\btorn between two\b"],
    "Second chance": [
        r"\bsecond[- ]chance (romance|love)\b", r"\brekindl",
        r"\breunited (lovers|after)\b", r"\bold flame\b",
    ],

    # Character and cast
    "Found family": [
        r"\bfound family\b", r"\bchosen family\b",
        r"\bmisfits? who become\b", r"\bragtag (crew|band|group)\b",
    ],
    "Chosen one": [
        r"\bchosen one\b", r"\bprophec", r"\bdestined to (save|defeat|rule)\b",
        r"\bthe only one who can\b",
    ],
    "Unreliable narrator": [
        r"\bunreliable narrator\b", r"\bcan(no|')t trust (the|her|his) (narrator|memory)\b",
        r"\bmemory (is|proves) unreliable\b", r"\bnothing is as it seems\b",
    ],
    "Hidden identity": [
        r"\bhidden identity\b", r"\bsecret identity\b", r"\bin disguise\b",
        r"\bimpersonat", r"\bmistaken identity\b", r"\bassumed name\b",
    ],
    "Morally grey lead": [
        r"\banti[- ]?hero\b", r"\bmorally (grey|gray|ambiguous)\b",
        r"\bvillain protagonist\b", r"\bno one is innocent\b",
    ],
    "Coming of age": [
        r"\bcoming[- ]of[- ]age\b", r"\bbildungsroman\b",
        r"\bgrowing up in\b", r"\badolescence\b",
    ],

    # Plot engines
    "Revenge": [r"\brevenge\b", r"\bvengeance\b", r"\bsettle the score\b", r"\bretribution\b"],
    "Quest": [r"\bquest\b", r"\bjourney to (find|reach|recover)\b", r"\bsets? out to find\b"],
    "Heist": [r"\bheist\b", r"\bthe perfect (crime|robbery)\b", r"\bcon artists?\b", r"\bsteal the\b"],
    "Locked room": [
        r"\block(ed)?[- ]room\b", r"\bclosed circle\b",
        r"\bisolated (house|island|manor|hotel)\b", r"\beveryone is a suspect\b",
    ],
    "Survival": [
        r"\bsurvival\b", r"\bstranded\b", r"\bshipwreck", r"\bcast away\b",
        r"\blast (man|woman|people) (alive|on earth)\b",
    ],
    "Secret history": [
        r"\bburied secret\b", r"\blong[- ]buried\b", r"\bfamily secret\b",
        r"\bpast (that|which) returns\b", r"\bunearth",
    ],
    "Time loop": [r"\btime loop\b", r"\brelives? the same\b", r"\bover and over again\b"],
    "Portal fantasy": [
        r"\bportal\b", r"\banother world\b", r"\btransported to\b",
        r"\bstumbles? into a world\b",
    ],

    # Setting and structure
    "Dark academia": [
        r"\bdark academia\b", r"\belite (school|college|university)\b",
        r"\bboarding school\b", r"\bcampus\b", r"\bsecret society\b",
    ],
    "Epistolary": [
        r"\bepistolary\b", r"\btold (in|through) letters\b", r"\bdiary (entries|form)\b",
        r"\bletters and (diaries|documents)\b",
    ],
    "Frame narrative": [
        r"\bframe (narrative|story)\b", r"\bstory within a story\b",
        r"\bnested narrat", r"\bmanuscript found\b",
    ],
    "Multiple timelines": [
        r"\bdual timeline\b", r"\bmultiple timelines\b", r"\btwo timelines\b",
        r"\balternat(es|ing) between (past|two)\b", r"\bthen and now\b",
    ],
    "Multiple narrators": [
        r"\bmultiple (narrators|points? of view|povs?)\b",
        r"\balternating (narrators|perspectives|povs?)\b", r"\bpolyphonic\b",
    ],
    "Cosy": [
        r"\bcosy\b", r"\bcozy\b", r"\blow[- ]stakes\b", r"\bgentle\b",
        r"\bcomfort read\b", r"\bwholesome\b",
    ],
    "Retelling": [
        r"\bretelling\b", r"\breimagin", r"\bmodern take on\b",
        r"\binspired by the (myth|legend|tale)\b", r"\bmyth retold\b",
    ],
}

COMPILED = {
    trope: [re.compile(pattern, re.I) for pattern in patterns]
    for trope, patterns in TROPE_RULES.items()
}

ALL_TROPES = sorted(TROPE_RULES)


def derive(
    subjects: list[str] | None,
    description: str = "",
    title: str = "",
    limit: int = 4,
) -> list[str]:
    """
    Infer trope tags from whatever text is available.

    The blurb does most of the work here — subject lists rarely mention that
    two characters start out hating each other, but jacket copy almost always
    does.

    Capped at four. Beyond that the tags stop narrowing anything and start
    reading as keyword soup.
    """
    parts: list[str] = []
    if subjects:
        parts.extend(s for s in subjects if s)
    if description:
        parts.append(description[:1500])
    if title:
        parts.append(title)

    haystack = " ; ".join(parts).lower()
    if len(haystack) < 12:
        return []

    scored: list[tuple[int, str]] = []
    for trope, patterns in COMPILED.items():
        hits = sum(1 for pattern in patterns if pattern.search(haystack))
        if hits:
            scored.append((hits, trope))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [trope for _, trope in scored[:limit]]


def label_counts(books) -> list[dict]:
    counts: dict[str, int] = {}
    for book in books:
        for trope in (book.tropes or []):
            counts[trope] = counts.get(trope, 0) + 1
    return [
        {"trope": trope, "count": count}
        for trope, count in sorted(counts.items(), key=lambda p: (-p[1], p[0]))
    ]