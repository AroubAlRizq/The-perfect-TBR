"""
Grouping recommendations into sections.

A single ranked list of twelve is hard to act on: the reader has to hold every
book in mind at once and compare them on nothing in particular. Sections give
each book a reason to be where it is, so someone can scroll to the part they
are in the mood for — a short one, something in translation, a stretch — and
ignore the rest.

The rule that makes this work is that **every book appears in exactly one
section**. Letting a book show up under both "short" and "from your authors"
would double the page length and put the reader back where they started.
Sections are tried in priority order and each book is claimed by the first one
that fits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .models import Book
from .recommend import Recommendation


@dataclass
class Section:
    key: str
    title: str
    blurb: str
    items: list[Recommendation] = field(default_factory=list)
    hidden: int = 0      # matched this section but trimmed by the cap

    def as_dict(self, serialise) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "blurb": self.blurb,
            "count": len(self.items),
            "hidden": self.hidden,
            "recommendations": [serialise(item) for item in self.items],
        }


def _recent_cutoff() -> int:
    return date.today().year - 5


# Each rule is (key, title, blurb, predicate). Order is priority: the first
# rule that matches claims the book.
#
# "Closest to your shelf" deliberately does not come first. If it did it would
# swallow the top of the list and leave every other section with scraps, which
# is the flat ranked list again wearing a hat. It sits after the sections that
# describe something specific about a book, and picks up whatever is left.
def _rules(profile_authors: set[str], top_score: float):
    recent = _recent_cutoff()

    return [
        (
            "authors",
            "More from writers you rated well",
            "Same hand, different book.",
            lambda r: r.book.author and r.book.author in profile_authors,
        ),
        (
            "short",
            "Short enough for one sitting",
            "Under about 45,000 words — a weekend, not a project.",
            lambda r: r.book.length_band() == "short",
        ),
        (
            "translated",
            "In translation",
            "Where a translator is named, the prose you read is partly theirs.",
            lambda r: r.book.is_translated,
        ),
        (
            "offbeat",
            "Off the algorithm",
            "Small presses and books without the marketing budget to reach you.",
            lambda r: (r.book.imprint or {}).get("tier") in {"small_press", "established"},
        ),
        (
            "recent",
            "Published recently",
            f"{recent} onwards.",
            lambda r: r.book.year and r.book.year >= recent,
        ),
        (
            "closest",
            "Closest to your shelf",
            "Strongest matches on measured prose and subject.",
            lambda r: r.score >= top_score * 0.85,
        ),
        (
            "stretch",
            "Worth a stretch",
            "Further from your centre of gravity, still pointing your way.",
            lambda r: not r.book.provisional,
        ),
        (
            "unmeasured",
            "Matched on subject only",
            "The prose here has not been measured yet, so treat these as leads "
            "rather than recommendations.",
            lambda r: r.book.provisional,
        ),
    ]


def build_sections(
    results: list[Recommendation],
    profile_authors: set[str],
    min_items: int = 2,
    max_per_section: int = 8,
) -> list[Section]:
    """
    Assign results to sections, then drop the ones too thin to be worth a
    heading.

    A section holding one book is not a section, it is a book with extra
    scrolling, so anything under min_items is folded back into the general
    match sections rather than shown on its own.
    """
    if not results:
        return []

    top_score = max(r.score for r in results)
    rules = _rules(profile_authors, top_score)

    buckets: dict[str, list[Recommendation]] = {key: [] for key, _, _, _ in rules}
    claimed: set[str] = set()

    for rule_key, _, _, predicate in rules:
        for result in results:
            if result.book.id in claimed:
                continue
            if len(buckets[rule_key]) >= max_per_section:
                continue
            try:
                if predicate(result):
                    buckets[rule_key].append(result)
                    claimed.add(result.book.id)
            except (AttributeError, TypeError):
                continue

    # Anything unclaimed — usually because a section filled up — falls back to
    # the general buckets rather than vanishing.
    leftovers = [r for r in results if r.book.id not in claimed]
    for result in leftovers:
        target = "unmeasured" if result.book.provisional else "stretch"
        buckets[target].append(result)

    sections: list[Section] = []
    orphans: list[Recommendation] = []

    for key, title, blurb, _ in rules:
        items = sorted(buckets[key], key=lambda r: r.score, reverse=True)
        if key in {"closest", "stretch", "unmeasured"}:
            if items:
                sections.append(Section(key, title, blurb, items))
        elif len(items) >= min_items:
            sections.append(Section(key, title, blurb, items))
        else:
            orphans.extend(items)

    # Fold thin sections back in so nothing is lost to the min_items rule.
    if orphans:
        for section in sections:
            if section.key in {"closest", "stretch"}:
                section.items.extend(o for o in orphans if not o.book.provisional)
                section.items.sort(key=lambda r: r.score, reverse=True)
                break
        else:
            sections.append(
                Section("closest", "Closest to your shelf",
                        "Strongest matches on measured prose and subject.",
                        sorted(orphans, key=lambda r: r.score, reverse=True))
            )
        for section in sections:
            if section.key == "unmeasured":
                section.items.extend(o for o in orphans if o.book.provisional)
                break

    # The leftover fold above bypasses max_per_section, which can leave the
    # catch-all sections enormous — a corpus that is mostly unmeasured would
    # produce one heading followed by hundreds of cards, which is the wall of
    # results this whole feature exists to break up.
    # Trimmed books are counted rather than quietly dropped, so the interface
    # can say how many are held back instead of implying the section is all
    # there was.
    for section in sections:
        if len(section.items) > max_per_section:
            section.hidden = len(section.items) - max_per_section
            section.items = section.items[:max_per_section]

    # Put the strongest section first, but always keep unmeasured last: it is
    # the least trustworthy group and should not open the page.
    ordered = [s for s in sections if s.key != "unmeasured"]
    ordered.sort(key=lambda s: max((r.score for r in s.items), default=0), reverse=True)
    ordered += [s for s in sections if s.key == "unmeasured"]

    return [s for s in ordered if s.items]