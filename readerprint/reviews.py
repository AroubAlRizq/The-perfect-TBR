"""
Review handling.

Two jobs, both aimed at the same complaint: that ratings are inflated and the
top reviews are gushing.

1. Score individual reviews on how much they would actually tell a stranger,
   and rank on that. Sentiment is ignored. A five-star review that explains
   what the book does well is worth more than a two-star review that says
   "boring".

2. Aggregate ratings across sources with shrinkage toward the corpus mean, so
   a 4.8 from 30 readers does not outrank a 4.3 from 40,000. Then split the
   ratings by time to expose hype decay — the gap between what a book scored
   in its first month and what it scores a year later.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta

# Words that signal the reviewer is weighing something rather than reacting.
CONCESSIVES = [
    "but ", "although", "though", "however", "that said", "even so",
    "on the other hand", "whereas", "while ", "still,", "yet ", "admittedly",
    "my only", "the problem", "my issue", "falls short", "does not quite",
    "doesn't quite", "where it works", "where it fails", "in fairness",
    "to be fair", "at the same time", "for all its", "despite",
]

# Vocabulary of craft. Reviews that use it are discussing the book as a made
# object, which is what a reader trying to predict their own reaction needs.
CRAFT_TERMS = [
    "prose", "writing style", "pacing", "pace", "structure", "characterisation",
    "characterization", "character work", "dialogue", "voice", "worldbuilding",
    "world-building", "plotting", "plot", "ending", "third act", "first act",
    "chapter", "pov", "point of view", "narrator", "tense", "exposition",
    "foreshadowing", "arc", "subplot", "tone", "register", "imagery",
    "metaphor", "syntax", "sentences", "translation", "translator",
    "editing", "edited", "repetitive", "info dump", "infodump", "purple",
    "melodrama", "melodramatic", "show don't tell", "telling not showing",
]

# Reaction without content. Not bad writing — just not predictive for anyone
# other than the person who wrote it.
HYPE_MARKERS = [
    "obsessed", "i cried", "sobbed", "wrecked me", "destroyed me",
    "changed my life", "everyone needs to read", "read this now",
    "no words", "speechless", "masterpiece", "perfection", "10/10",
    "all the stars", "instant favourite", "instant favorite", "peak fiction",
    "ate and left no crumbs", "i need more", "screaming", "unhinged",
]

SPOILER_MARKERS = ["spoiler", "!!!spoiler", "[spoiler", "ending reveals", "dies at the end"]

EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\u2600-\u27BF" "\U0001F1E6-\U0001F1FF" "]"
)

# Default trust weights per source. Editorial outlets carry more weight per
# review because they are edited; crowd sources carry weight through volume.
SOURCE_WEIGHTS = {
    "goodreads": 1.0,
    "storygraph": 1.15,
    "hardcover": 1.1,
    "amazon": 0.7,
    "librarything": 1.1,
    "openlibrary": 0.9,
    "kirkus": 1.5,
    "publishers_weekly": 1.5,
    "booklist": 1.4,
    "nyt": 1.4,
    "guardian": 1.3,
    "locus": 1.3,
    "unknown": 0.8,
}

CORPUS_PRIOR_MEAN = 3.9   # ratings are inflated everywhere; this is realistic
CORPUS_PRIOR_WEIGHT = 60  # equivalent number of "prior" ratings


@dataclass
class Review:
    source: str
    text: str
    rating: float | None = None      # normalised to a 5-point scale
    posted: date | None = None
    votes: int = 0
    url: str | None = None
    reviewer: str | None = None

    # Filled in by score_review
    informativeness: float = 0.0
    signals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["posted"] = self.posted.isoformat() if self.posted else None
        return d


@dataclass
class RatingSummary:
    weighted_score: float | None = None      # shrunk, cross-source, 0-5
    raw_mean: float | None = None
    total_ratings: int = 0
    per_source: dict = field(default_factory=dict)
    hype_decay: float | None = None          # early mean minus settled mean
    hype_note: str | None = None
    divisiveness: float | None = None        # sd of ratings, 0-2ish

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Individual review scoring
# --------------------------------------------------------------------------

def score_review(review: Review) -> Review:
    """
    Score a review 0-100 on how useful it would be to a stranger.

    Deliberately independent of the star rating attached to it, except for a
    small bonus in the 2-4 band where readers tend to explain themselves.
    """
    text = (review.text or "").strip()
    lower = text.lower()
    signals: list[str] = []

    if len(text) < 40:
        review.informativeness = 0.0
        review.signals = ["too short to be useful"]
        return review

    word_count = len(re.findall(r"\b\w+\b", text))

    # Length, with diminishing returns and a ceiling. Very long reviews are
    # often plot recaps, so the curve flattens rather than climbing.
    length_score = min(1.0, math.log1p(word_count) / math.log1p(320)) * 22

    concessive_hits = sum(1 for c in CONCESSIVES if c in lower)
    concessive_score = min(1.0, concessive_hits / 3) * 26
    if concessive_hits >= 2:
        signals.append("weighs strengths against weaknesses")

    craft_hits = sum(1 for c in CRAFT_TERMS if c in lower)
    craft_score = min(1.0, craft_hits / 5) * 28
    if craft_hits >= 3:
        signals.append("discusses craft directly")

    # Middle ratings carry more information per review.
    band_score = 0.0
    if review.rating is not None:
        if 2.0 <= review.rating <= 4.0:
            band_score = 10.0
            signals.append("middle rating")
        elif review.rating in (1.0, 5.0):
            band_score = 2.0

    # Concrete detail: numbers, page references, named comparisons.
    specificity = 0.0
    if re.search(r"\b(page|chapter|part|act)\s+\d+", lower):
        specificity += 4
    if re.search(r"\bcompared to\b|\breminded me of\b|\bif you liked\b|\bfans of\b", lower):
        specificity += 5
        signals.append("makes comparisons")
    if re.search(r"\bfirst (person|half|hundred)\b|\bthird person\b|\bpresent tense\b", lower):
        specificity += 5

    subtotal = length_score + concessive_score + craft_score + band_score + specificity

    # Penalties
    penalty = 0.0
    hype_hits = sum(1 for h in HYPE_MARKERS if h in lower)
    if hype_hits:
        penalty += min(18, hype_hits * 7)
        if hype_hits >= 2:
            signals.append("mostly reaction")

    caps_tokens = re.findall(r"\b[A-Z]{3,}\b", text)
    if len(caps_tokens) > 3:
        penalty += 6
    exclamations = text.count("!")
    if exclamations > 4:
        penalty += min(8, exclamations)
    emoji = len(EMOJI_RE.findall(text))
    if emoji > 3:
        penalty += min(8, emoji)

    if any(s in lower for s in SPOILER_MARKERS):
        signals.append("may contain spoilers")

    # A review that is only a plot summary tends to have craft terms near zero
    # and length high.
    if craft_hits == 0 and concessive_hits == 0 and word_count > 150:
        penalty += 10
        signals.append("reads as plot summary")

    score = max(0.0, min(100.0, subtotal - penalty))

    # Community votes act as a gentle multiplier, capped so that a single
    # viral joke review cannot dominate.
    if review.votes > 0:
        score *= 1 + min(0.15, math.log1p(review.votes) / 60)
        score = min(100.0, score)

    review.informativeness = round(score, 1)
    review.signals = signals
    return review


def rank_reviews(reviews: list[Review], limit: int = 6) -> list[Review]:
    """
    Rank by informativeness, then force a spread of opinion.

    Ranking on score alone can return six reviews that all agree. The reader
    asked for pros and cons, so the selection guarantees at least a couple of
    critical voices when they exist.
    """
    scored = [score_review(r) for r in reviews]
    scored = [r for r in scored if r.informativeness > 0]
    scored.sort(key=lambda r: r.informativeness, reverse=True)

    if len(scored) <= limit:
        return scored

    positive = [r for r in scored if (r.rating or 3) >= 4]
    critical = [r for r in scored if (r.rating or 3) < 4]

    want_critical = min(len(critical), max(2, limit // 2))
    want_positive = min(len(positive), limit - want_critical)

    chosen = critical[:want_critical] + positive[:want_positive]
    if len(chosen) < limit:
        remaining = [r for r in scored if r not in chosen]
        chosen += remaining[: limit - len(chosen)]

    chosen.sort(key=lambda r: r.informativeness, reverse=True)
    return chosen[:limit]


# --------------------------------------------------------------------------
# Aggregate rating
# --------------------------------------------------------------------------

def summarise_ratings(
    source_stats: dict,
    release_date: date | None = None,
    dated_ratings: list[tuple[date, float]] | None = None,
) -> RatingSummary:
    """
    source_stats maps a source name to {"mean": float, "count": int}, all on a
    5-point scale. Convert before calling.
    """
    summary = RatingSummary()
    if not source_stats:
        return summary

    numerator = CORPUS_PRIOR_MEAN * CORPUS_PRIOR_WEIGHT
    denominator = float(CORPUS_PRIOR_WEIGHT)
    raw_values: list[float] = []
    total = 0

    for source, stats in source_stats.items():
        mean = stats.get("mean")
        count = stats.get("count", 0)
        if mean is None or count <= 0:
            continue
        weight = SOURCE_WEIGHTS.get(source.lower(), SOURCE_WEIGHTS["unknown"])

        # Volume enters logarithmically. The difference between 500 and 5,000
        # ratings is real but far smaller than the raw counts suggest.
        effective = weight * math.log1p(count) * 12
        numerator += mean * effective
        denominator += effective
        raw_values.append(mean)
        total += count
        summary.per_source[source] = {
            "mean": round(mean, 2),
            "count": count,
            "weight": round(weight, 2),
        }

    if denominator > CORPUS_PRIOR_WEIGHT:
        summary.weighted_score = round(numerator / denominator, 2)
    if raw_values:
        summary.raw_mean = round(statistics.fmean(raw_values), 2)
    summary.total_ratings = total

    # Hype decay
    if dated_ratings:
        anchor = release_date or min(d for d, _ in dated_ratings)
        cutoff = anchor + timedelta(days=45)
        settled_from = anchor + timedelta(days=180)

        early = [v for d, v in dated_ratings if d <= cutoff]
        settled = [v for d, v in dated_ratings if d >= settled_from]

        if len(early) >= 8 and len(settled) >= 8:
            delta = statistics.fmean(early) - statistics.fmean(settled)
            summary.hype_decay = round(delta, 2)
            if delta >= 0.35:
                summary.hype_note = (
                    f"Rated {delta:.2f} higher in its first six weeks than after "
                    "six months. Early enthusiasm cooled."
                )
            elif delta <= -0.2:
                summary.hype_note = (
                    f"Rated {abs(delta):.2f} higher after six months than at "
                    "release. Found its readers slowly."
                )
            else:
                summary.hype_note = "Rating held steady after release."

        all_values = [v for _, v in dated_ratings]
        if len(all_values) > 4:
            summary.divisiveness = round(statistics.pstdev(all_values), 2)

    return summary
