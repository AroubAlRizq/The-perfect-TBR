"""
Data models.

The one non-obvious choice here is that a reading event records *why* a book
failed, not just that it did. Every recommender on the market trains on what
people finished and liked. Disappointment is the signal this app exists to
capture, so it gets first-class structure rather than a thumbs-down.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import date

# --------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------

VERDICTS = {
    "loved": 1.0,
    "liked": 0.55,
    "fine": 0.0,
    "disliked": -0.7,
    "dnf": -1.0,
}

# Why a book was abandoned or disliked. Each maps to the style axes it should
# push the recommender away from, which is what makes it more than a label.
DNF_REASONS = {
    "prose": {
        "label": "The writing itself",
        "hint": "Sentence-level. Clumsy, overwrought, or flat.",
        "penalises": ["cliche_rate", "ornament_index"],
    },
    "pacing": {
        "label": "Too slow or too rushed",
        "hint": "Nothing happening, or everything happening at once.",
        "penalises": ["mean_sentence_length", "long_sentence_share"],
    },
    "characters": {
        "label": "Characters",
        "hint": "Flat, unpleasant, or impossible to care about.",
        "penalises": ["cliche_rate"],
    },
    "premise": {
        "label": "Premise did not deliver",
        "hint": "The book was not the book the blurb promised.",
        "penalises": [],
    },
    "tropes": {
        "label": "Tropes or plot patterns",
        "hint": "Familiar beats you have had enough of.",
        "penalises": ["cliche_rate"],
    },
    "ending": {
        "label": "The ending",
        "hint": "Worked until it didn't.",
        "penalises": [],
    },
    "density": {
        "label": "Too dense to stay with",
        "hint": "Real effort required, more than you wanted to give.",
        "penalises": ["prose_density", "grade_level", "subordination_rate"],
    },
    "content": {
        "label": "Content I did not want",
        "hint": "Subject matter, explicitness, or cruelty.",
        "penalises": [],
    },
    "translation": {
        "label": "The translation",
        "hint": "Stiff or tin-eared in English.",
        "penalises": [],
    },
    "length": {
        "label": "Length",
        "hint": "Ran out of patience before pages.",
        "penalises": ["word_count"],
    },
    "other": {"label": "Something else", "hint": "", "penalises": []},
}

CONTENT_FLAGS = [
    "explicit_sex", "graphic_violence", "sexual_violence", "self_harm",
    "animal_harm", "child_harm", "addiction", "suicide", "eating_disorder",
    "racism_depicted", "cliffhanger_ending", "unresolved_series",
]

POV_LABELS = {
    "first": "First person",
    "second": "Second person",
    "third": "Third person",
    "mixed": "Mixed / multiple",
    "unknown": "Not determined",
}

TENSE_LABELS = {
    "past": "Past tense",
    "present": "Present tense",
    "mixed": "Mixed tense",
    "unknown": "Not determined",
}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------
# Book
# --------------------------------------------------------------------------

@dataclass
class Book:
    id: str = field(default_factory=new_id)
    title: str = ""
    author: str = ""
    year: int | None = None
    isbn13: str | None = None
    publisher: str | None = None
    page_count: int | None = None
    word_count: int | None = None

    language: str = "en"
    original_language: str | None = None
    translator: str | None = None

    series: str | None = None
    series_position: float | None = None
    subjects: list[str] = field(default_factory=list)
    description: str | None = None
    cover_url: str | None = None

    excerpt: str | None = None
    excerpt_source: str | None = None      # gutenberg / google_books / user
    excerpt_licence: str | None = None

    style: dict = field(default_factory=dict)
    imprint: dict = field(default_factory=dict)
    ratings: dict = field(default_factory=dict)
    content_flags: list[str] = field(default_factory=list)

    openlibrary_key: str | None = None
    google_books_id: str | None = None
    gutenberg_id: int | None = None

    provisional: bool = True   # True until style came from real text

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def is_translated(self) -> bool:
        return bool(
            self.translator
            or (self.original_language and self.original_language != self.language)
        )

    def estimated_word_count(self) -> int | None:
        """
        Word count beats page count, which is an artefact of edition and font.
        Where a real count is missing, 275 words a page is the standard trade
        approximation and is flagged as an estimate everywhere it is shown.
        """
        if self.word_count:
            return self.word_count
        if self.page_count:
            return int(self.page_count * 275)
        return None

    def length_band(self) -> str:
        wc = self.estimated_word_count()
        if not wc:
            return "unknown"
        if wc < 45_000:
            return "short"
        if wc < 90_000:
            return "standard"
        if wc < 150_000:
            return "long"
        return "very_long"


LENGTH_BAND_LABELS = {
    "short": "Short (under 45k words)",
    "standard": "Standard (45-90k)",
    "long": "Long (90-150k)",
    "very_long": "Very long (150k+)",
    "unknown": "Length unknown",
}


# --------------------------------------------------------------------------
# Reading history
# --------------------------------------------------------------------------

@dataclass
class ReadingEvent:
    id: str = field(default_factory=new_id)
    user_id: str = "local"
    book_id: str = ""
    verdict: str = "fine"                  # see VERDICTS
    rating: float | None = None            # optional 1-5 from the user
    dnf_reasons: list[str] = field(default_factory=list)
    dnf_point: int | None = None           # percentage through when abandoned
    finished_on: date | None = None
    note: str | None = None

    def weight(self) -> float:
        """
        How hard this event pulls the taste profile.

        A book abandoned at 8% says less about the book than one abandoned at
        70%, but more about a hard aversion — someone bounced off it fast.
        Both matter, so early DNFs keep full weight while mid-book DNFs get a
        slight boost for being considered judgements.
        """
        base = VERDICTS.get(self.verdict, 0.0)
        if self.verdict == "dnf" and self.dnf_point:
            if self.dnf_point >= 50:
                base *= 1.15
        if self.rating is not None:
            nudge = (self.rating - 3.0) / 2.0
            base = (base * 0.7) + (nudge * 0.3)
        return max(-1.2, min(1.2, base))

    def as_dict(self) -> dict:
        d = asdict(self)
        d["finished_on"] = self.finished_on.isoformat() if self.finished_on else None
        d["weight"] = round(self.weight(), 3)
        return d
