"""
The recommender.

Design in one paragraph: every book becomes a vector with two blocks — a
measured-style block and a semantic block built from description and subject
text. A reader becomes a weighted centroid of the books they loved, plus a
second centroid of the books they abandoned. A candidate scores well when it
sits near the first centroid and far from the second. Results are then passed
through maximal marginal relevance so the list is not six variations of the
same book.

Reader clustering was scoped out of v1 deliberately. Clusters need a user base
to be meaningful, and a pilot shared with a handful of friends does not have
one. The vector representation here is what clustering would run on later, so
nothing has to be rebuilt to add it.

TF-IDF plus SVD rather than a neural embedding model: it runs offline, needs
no download or API key, and is reproducible. Swapping in sentence embeddings
later means changing one function.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from .models import Book, ReadingEvent, DNF_REASONS
from .style import StyleProfile

# Style dimensions, with the range each is scaled across. Ranges are set from
# what real prose actually does, not from theoretical limits.
STYLE_DIMS: list[tuple[str, float, float]] = [
    ("prose_density", 0, 100),
    ("mean_sentence_length", 6, 34),
    ("sentence_length_sd", 2, 20),
    ("long_sentence_share", 0, 0.35),
    ("short_sentence_share", 0, 0.45),
    ("dialogue_share", 0, 0.55),
    ("lexical_variety", 0.25, 0.65),
    ("subordination_rate", 0, 2.5),
    ("adverb_rate", 0, 40),
    ("comma_rate", 0, 5),
    ("ornament_index", 0, 80),
    ("cliche_rate", 0, 30),
    ("grade_level", 3, 16),
    ("mean_paragraph_sentences", 1, 8),
]

POV_VALUES = ["first", "second", "third", "mixed"]
TENSE_VALUES = ["past", "present", "mixed"]
TIER_VALUES = ["major", "established", "small_press", "academic", "self_published"]
LENGTH_VALUES = ["short", "standard", "long", "very_long"]

# Subjects too broad to mean anything as a reason. "Shares fiction with books
# you loved" is noise; "shares dark academia" is a reason.
BROAD_SUBJECTS = {
    "fiction", "literary", "literature", "novel", "classics", "general",
    "english fiction", "american fiction", "adult", "contemporary",
    "translated", "bestseller", "award winner", "book club",
}

STYLE_BLOCK_WEIGHT = 1.0
SEMANTIC_BLOCK_WEIGHT = 0.85
SEMANTIC_COMPONENTS = 64


def _scale(value: float | None, low: float, high: float) -> float:
    if value is None or high <= low:
        return 0.5
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _one_hot(value: str | None, options: list[str]) -> list[float]:
    return [1.0 if value == opt else 0.0 for opt in options]


def style_vector(book: Book) -> np.ndarray:
    """The measured half of a book's representation."""
    style = book.style or {}
    values: list[float] = []

    for name, low, high in STYLE_DIMS:
        raw = style.get(name)
        values.append(_scale(raw, low, high))

    values += _one_hot(style.get("pov"), POV_VALUES)
    values += _one_hot(style.get("tense"), TENSE_VALUES)
    values += _one_hot((book.imprint or {}).get("tier"), TIER_VALUES)
    values += _one_hot(book.length_band(), LENGTH_VALUES)

    wc = book.estimated_word_count()
    values.append(_scale(math.log1p(wc) if wc else None, math.log1p(20_000), math.log1p(400_000)))
    values.append(1.0 if book.is_translated else 0.0)

    return np.asarray(values, dtype=np.float32)


def semantic_corpus(books: list[Book]) -> list[str]:
    docs = []
    for b in books:
        parts = [
            b.title or "",
            b.author or "",
            " ".join(b.subjects or []),
            b.description or "",
        ]
        docs.append(" ".join(parts).strip() or "untitled")
    return docs


@dataclass
class VectorSpace:
    books: list[Book]
    matrix: np.ndarray                       # rows aligned with books
    style_width: int
    index: dict = field(default_factory=dict)

    def row_for(self, book_id: str) -> np.ndarray | None:
        i = self.index.get(book_id)
        return self.matrix[i] if i is not None else None


def build_space(books: list[Book]) -> VectorSpace:
    """Turn a corpus into one matrix. Rebuilt whenever the corpus changes."""
    if not books:
        return VectorSpace([], np.zeros((0, 1), dtype=np.float32), 0)

    style_block = np.vstack([style_vector(b) for b in books])
    style_block = normalize(style_block) * STYLE_BLOCK_WEIGHT

    docs = semantic_corpus(books)
    vectoriser = TfidfVectorizer(
        stop_words="english",
        max_features=20_000,
        ngram_range=(1, 2),
        min_df=1,
        sublinear_tf=True,
    )
    tfidf = vectoriser.fit_transform(docs)

    n_components = min(SEMANTIC_COMPONENTS, max(2, min(tfidf.shape) - 1))
    if tfidf.shape[0] > 2 and tfidf.shape[1] > 2:
        svd = TruncatedSVD(n_components=n_components, random_state=17)
        semantic_block = svd.fit_transform(tfidf)
    else:
        semantic_block = tfidf.toarray()
    semantic_block = normalize(semantic_block) * SEMANTIC_BLOCK_WEIGHT

    matrix = np.hstack([style_block, semantic_block]).astype(np.float32)
    matrix = normalize(matrix)

    return VectorSpace(
        books=books,
        matrix=matrix,
        style_width=style_block.shape[1],
        index={b.id: i for i, b in enumerate(books)},
    )


# --------------------------------------------------------------------------
# Taste profile
# --------------------------------------------------------------------------

@dataclass
class TasteProfile:
    liked_centroid: np.ndarray | None = None
    disliked_centroid: np.ndarray | None = None
    n_liked: int = 0
    n_disliked: int = 0
    dnf_reason_counts: dict = field(default_factory=dict)
    style_summary: dict = field(default_factory=dict)
    aversions: dict = field(default_factory=dict)

    # Kept so recommendations can point at something specific — a shared
    # subject or a returning author says more than a matched sentence length,
    # and is checkable by the reader.
    loved_subjects: set = field(default_factory=set)
    loved_authors: set = field(default_factory=set)

    @property
    def is_usable(self) -> bool:
        return self.n_liked >= 3


def build_profile(space: VectorSpace, events: list[ReadingEvent]) -> TasteProfile:
    profile = TasteProfile()
    if space.matrix.shape[0] == 0:
        return profile

    liked_rows, liked_weights = [], []
    disliked_rows, disliked_weights = [], []
    style_accumulator: dict[str, list[float]] = {}
    disliked_styles: dict[str, list[float]] = {}
    subject_counts: Counter = Counter()

    for event in events:
        row = space.row_for(event.book_id)
        if row is None:
            continue
        w = event.weight()
        book = space.books[space.index[event.book_id]]

        if w > 0.15:
            liked_rows.append(row)
            liked_weights.append(w)
            for name, _, _ in STYLE_DIMS:
                v = (book.style or {}).get(name)
                if v is not None:
                    style_accumulator.setdefault(name, []).append(float(v))
            subject_counts.update(book.subjects or [])
            if w > 0.5 and book.author:
                profile.loved_authors.add(book.author)
        elif w < -0.15:
            disliked_rows.append(row)
            disliked_weights.append(abs(w))
            for name, _, _ in STYLE_DIMS:
                v = (book.style or {}).get(name)
                if v is not None:
                    disliked_styles.setdefault(name, []).append(float(v))
            for reason in event.dnf_reasons:
                profile.dnf_reason_counts[reason] = (
                    profile.dnf_reason_counts.get(reason, 0) + 1
                )

    if liked_rows:
        arr = np.vstack(liked_rows)
        wts = np.asarray(liked_weights, dtype=np.float32)[:, None]
        profile.liked_centroid = normalize((arr * wts).sum(axis=0, keepdims=True))[0]
        profile.n_liked = len(liked_rows)

    if disliked_rows:
        arr = np.vstack(disliked_rows)
        wts = np.asarray(disliked_weights, dtype=np.float32)[:, None]
        profile.disliked_centroid = normalize((arr * wts).sum(axis=0, keepdims=True))[0]
        profile.n_disliked = len(disliked_rows)

    profile.style_summary = {
        name: round(float(np.mean(vals)), 2)
        for name, vals in style_accumulator.items()
    }

    # A subject only counts as a taste signal if it shows up on more than one
    # loved book. One occurrence is a coincidence, and citing it back as a
    # reason would be the same unearned confidence this app exists to avoid.
    profile.loved_subjects = {s for s, n in subject_counts.items() if n >= 2}

    # Turn DNF reasons into penalties on named style dimensions — but only
    # where the abandoned books actually scored higher on that dimension than
    # the loved ones.
    #
    # Without that check the taxonomy misfires badly. Someone who abandoned
    # two plainly-written romances for "the writing" would be steered away
    # from ornate prose, when their shelf is full of it and the real
    # complaint was stock phrasing. The reason names the axis; the measured
    # gap decides the direction and the strength.
    for reason, count in profile.dnf_reason_counts.items():
        spec = DNF_REASONS.get(reason, {})
        for dim in spec.get("penalises", []):
            if dim == "word_count":
                profile.aversions[dim] = profile.aversions.get(dim, 0.0) + min(1.0, count / 3)
                continue

            disliked_values = disliked_styles.get(dim)
            liked_values = style_accumulator.get(dim)
            if not disliked_values or not liked_values:
                continue

            gap = float(np.mean(disliked_values)) - float(np.mean(liked_values))
            span = next((h - l for n, l, h in STYLE_DIMS if n == dim), None)
            if not span:
                continue

            # Require a gap of at least 8% of the dimension's range before
            # acting. Small differences are noise, especially on a shelf of
            # twenty books.
            relative = gap / span
            if relative > 0.08:
                strength = min(1.0, relative * 2.5) * min(1.0, count / 2)
                profile.aversions[dim] = profile.aversions.get(dim, 0.0) + strength

    return profile


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

@dataclass
class Recommendation:
    book: Book
    score: float
    affinity: float
    penalty: float
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    raw_reasons: list = field(default_factory=list)   # (rank, text), rationed later


def _aversion_penalty(book: Book, profile: TasteProfile) -> tuple[float, list[str]]:
    penalty, notes = 0.0, []
    style = book.style or {}

    for dim, strength in profile.aversions.items():
        if dim == "word_count":
            wc = book.estimated_word_count()
            if wc and wc > 150_000:
                penalty += 0.06 * strength
                notes.append("Long, and you have abandoned books for length.")
            continue

        value = style.get(dim)
        if value is None:
            continue
        for name, low, high in STYLE_DIMS:
            if name == dim:
                scaled = _scale(value, low, high)
                if scaled > 0.5:
                    penalty += (scaled - 0.5) * 0.65 * strength
                    if dim == "cliche_rate":
                        notes.append("Leans on stock phrasing more than most.")
                    elif dim == "ornament_index":
                        notes.append("Heavily figurative — you have bounced off that.")
                    elif dim in ("prose_density", "grade_level", "subordination_rate"):
                        notes.append("Demanding prose, which has lost you before.")
                break

    return penalty, list(dict.fromkeys(notes))


def _reasons_for(book: Book, profile: TasteProfile) -> list[tuple[int, str]]:
    """
    Explain the match in the reader's own terms.

    Each reason carries a rank: 0 is specific to this book, 2 is true of half
    the corpus. The caller spends the specific ones first and rations the
    generic ones, because a column of cards that all say "sentence rhythm
    matches your shelf" explains nothing and reads as filler — which is the
    exact failure this whole app is a reaction against.
    """
    reasons: list[tuple[int, str]] = []
    style = book.style or {}
    summary = profile.style_summary

    # Tier 0 — true of this book and few others
    shared = [
        s for s in (book.subjects or [])
        if s in profile.loved_subjects and s.lower() not in BROAD_SUBJECTS
    ]
    if len(shared) >= 2:
        reasons.append((0, f"Shares {shared[0]} and {shared[1]} with books you loved."))
    elif shared:
        # One shared subject is a weak signal, so it gets rationed like any
        # other generic line rather than appearing on every card.
        reasons.append((1, f"Shares {shared[0]} with books you loved."))

    if book.author and book.author in profile.loved_authors:
        reasons.append((0, f"You rated {book.author} highly before."))

    if book.is_translated and book.translator:
        reasons.append((0, f"Translated by {book.translator}."))

    ratings = book.ratings or {}
    decay = ratings.get("hype_decay")
    if decay is not None and decay <= 0:
        reasons.append((0, "Rating climbed after release rather than falling."))

    # Tier 1 — a real distinguishing property
    tier = (book.imprint or {}).get("tier")
    if tier == "small_press":
        reasons.append((1, "Small press, rarely surfaced by algorithmic lists."))

    pov, tense = style.get("pov"), style.get("tense")
    if pov == "second":
        reasons.append((1, "Second-person narration, which almost nothing does."))
    if tense == "present" and summary.get("prose_density"):
        reasons.append((1, "Present tense throughout."))

    band = book.length_band()
    if band == "short":
        reasons.append((1, "Short enough to finish in a sitting or two."))
    elif band == "very_long":
        reasons.append((1, "A long commitment, above 150k words."))

    density = style.get("prose_density")
    centre = summary.get("prose_density")
    if density is not None and centre is not None and abs(density - centre) <= 6:
        reasons.append((1, f"Density {round(density)}, right at your centre of gravity."))

    # Tier 2 — true, but true of many books
    def close(dim: str, tolerance: float) -> bool:
        a, b = style.get(dim), summary.get(dim)
        return a is not None and b is not None and abs(a - b) <= tolerance

    if close("mean_sentence_length", 4):
        reasons.append((2, "Sentence rhythm close to your shelf average."))
    if close("dialogue_share", 0.08):
        dominant = "dialogue-led" if style.get("dialogue_share", 0) > 0.3 else "narration-led"
        reasons.append((2, f"Same {dominant} balance as books you rated well."))
    if pov and pov != "unknown":
        reasons.append((2, f"{pov.title()}-person narration."))

    return reasons


def recommend(
    space: VectorSpace,
    profile: TasteProfile,
    exclude_ids: set[str],
    limit: int = 12,
    diversity: float = 0.3,
    min_length: int | None = None,
    max_length: int | None = None,
    allowed_pov: set[str] | None = None,
    exclude_flags: set[str] | None = None,
    repulsion: float = 0.45,
) -> list[Recommendation]:
    if profile.liked_centroid is None or space.matrix.shape[0] == 0:
        return []

    affinity = space.matrix @ profile.liked_centroid
    if profile.disliked_centroid is not None:
        affinity = affinity - repulsion * (space.matrix @ profile.disliked_centroid)

    candidates: list[Recommendation] = []
    for i, book in enumerate(space.books):
        if book.id in exclude_ids:
            continue

        wc = book.estimated_word_count()
        if min_length and (not wc or wc < min_length):
            continue
        if max_length and wc and wc > max_length:
            continue
        if allowed_pov:
            pov = (book.style or {}).get("pov")
            if pov not in allowed_pov:
                continue
        if exclude_flags and set(book.content_flags or []) & exclude_flags:
            continue

        penalty, cautions = _aversion_penalty(book, profile)
        candidates.append(
            Recommendation(
                book=book,
                affinity=float(affinity[i]),
                penalty=round(penalty, 3),
                score=float(affinity[i]) - penalty,
                raw_reasons=_reasons_for(book, profile),
                cautions=cautions,
            )
        )

    if not candidates:
        return []

    candidates.sort(key=lambda r: r.score, reverse=True)
    pool = candidates[: max(limit * 4, 40)]

    # Maximal marginal relevance. Without it a list of twelve becomes twelve
    # near-identical books, which is exactly the failure mode of "readers who
    # liked X also liked" widgets.
    selected: list[Recommendation] = []
    remaining = list(pool)
    while remaining and len(selected) < limit:
        if not selected:
            best = max(remaining, key=lambda r: r.score)
        else:
            chosen_rows = np.vstack(
                [space.matrix[space.index[r.book.id]] for r in selected]
            )
            best, best_value = None, -1e9
            for cand in remaining:
                row = space.matrix[space.index[cand.book.id]]
                redundancy = float(np.max(chosen_rows @ row))
                value = (1 - diversity) * cand.score - diversity * redundancy
                if value > best_value:
                    best, best_value = cand, value
        selected.append(best)
        remaining.remove(best)

    # Diversity governs which twelve are chosen; score governs how they are
    # ordered. Showing them in selection order puts lower scores above higher
    # ones, which reads as a bug even when it is not.
    selected.sort(key=lambda r: r.score, reverse=True)
    _ration_reasons(selected)
    return selected


def _ration_reasons(results: list[Recommendation], per_reason_cap: int = 3) -> None:
    """
    Spend specific reasons freely and generic ones sparingly.

    Every card carrying the same line — "sentence rhythm close to your shelf
    average" — is technically true and completely uninformative, and it makes
    the whole list read as generated filler. So a reason phrase that has
    already appeared three times is dropped from later cards, and a card left
    with nothing says so plainly instead of padding.
    """
    used: dict[str, int] = {}

    for result in results:
        kept: list[str] = []
        for rank, text in sorted(result.raw_reasons, key=lambda r: r[0]):
            if len(kept) >= 3:
                break
            if rank == 0:                      # always worth saying
                kept.append(text)
                continue
            seen = used.get(text, 0)
            if seen >= per_reason_cap:
                continue
            used[text] = seen + 1
            kept.append(text)

        if not kept:
            kept = ["Close to your profile overall, without one standout reason."]
        result.reasons = kept
