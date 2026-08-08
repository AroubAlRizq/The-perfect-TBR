"""
Prose style analysis.

This module turns raw prose into a fixed set of measurements. It is the part of
Readerprint that replaces "is this from Wattpad?" with "what does the prose
actually do?" — a question that is both fairer to the book and more useful to
the reader.

Everything here is deterministic and dependency-free. No model downloads, no
API keys. That matters for a pilot: contributors can run it offline and get
identical numbers.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field, asdict
from typing import Iterable

# --------------------------------------------------------------------------
# Lexicons
# --------------------------------------------------------------------------

FIRST_PERSON = {
    "i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "ourselves",
}
SECOND_PERSON = {"you", "your", "yours", "yourself", "yourselves"}
THIRD_PERSON = {
    "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "they", "them", "their", "theirs", "themselves", "it", "its",
}

# Past/present pairs of very high frequency verbs. A full POS tagger would be
# better, but these forms carry most of the tense signal in narration and cost
# nothing to check.
PAST_MARKERS = {
    "was", "were", "had", "did", "said", "went", "came", "looked", "felt",
    "knew", "thought", "saw", "took", "made", "got", "told", "found", "gave",
    "asked", "turned", "began", "seemed", "stood", "held", "kept", "left",
    "brought", "put", "heard", "let", "watched", "walked", "wanted", "tried",
}
PRESENT_MARKERS = {
    "is", "are", "am", "has", "have", "does", "do", "says", "say", "goes",
    "go", "comes", "come", "looks", "look", "feels", "feel", "knows", "know",
    "thinks", "think", "sees", "see", "takes", "take", "makes", "make",
    "gets", "get", "tells", "tell", "finds", "find", "gives", "give",
    "asks", "ask", "turns", "turn", "begins", "seems", "stands", "holds",
    "keeps", "leaves", "hears", "watches", "walks", "wants", "tries",
}

SUBORDINATORS = {
    "because", "although", "though", "while", "whereas", "since", "unless",
    "until", "whenever", "wherever", "whether", "if", "when", "after",
    "before", "as", "which", "who", "whom", "whose", "that",
}

# Abstract, high-register nouns that cluster in ornamental prose. Presence is
# not a fault — it is a flavour. The index reports intensity, not quality.
ORNAMENT_NOUNS = {
    "void", "abyss", "essence", "eternity", "oblivion", "solitude", "longing",
    "yearning", "reverie", "sorrow", "anguish", "rapture", "torment",
    "silence", "shadow", "shadows", "ember", "embers", "ache", "ruin",
    "hollow", "tempest", "chasm", "veil", "requiem", "lament", "wisp",
    "gossamer", "cascade", "expanse", "infinity", "melancholy", "serenity",
}

SIMILE_PATTERNS = [
    r"\blike a\b", r"\blike the\b", r"\bas if\b", r"\bas though\b",
    r"\bas .{1,12} as\b",
]

# Phrases that recur across a very large body of amateur and web-serial
# romance. High rates here are the single most reliable predictor of the
# reading experience the user described as disappointing.
CLICHE_PHRASES = [
    "breath hitched", "breath caught", "heart hammered", "heart pounded",
    "heart raced", "heart skipped", "let out a breath", "breath i didn't know",
    "released a breath", "shivers down", "shiver down", "chills down",
    "electricity shot", "sparks flew", "butterflies in", "stomach dropped",
    "stomach churned", "blood ran cold", "world stopped", "time stood still",
    "everything went black", "smirked", "chuckled darkly", "growled out",
    "he was gorgeous", "she was beautiful", "my breath hitched",
    "i felt my cheeks", "cheeks flushed", "cheeks burned", "bit my lip",
    "bit her lip", "raked a hand through", "ran a hand through his hair",
    "jaw clenched", "jaw ticked", "eyes darkened", "gaze darkened",
    "voice dripping with", "little did i know", "i was in trouble",
    "he was trouble", "toe curling", "scoffed", "rolled my eyes",
]

DIALOGUE_RE = re.compile(r'"[^"]{1,600}"|\u201c[^\u201d]{1,600}\u201d')
SENTENCE_RE = re.compile(r"[^.!?\u2026]+[.!?\u2026]+[\"'\u201d\u2019)\]]*|[^.!?\u2026]+$")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\u2019-]*")

VOWEL_GROUPS = re.compile(r"[aeiouy]+")


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------

@dataclass
class StyleProfile:
    """Measurements for one sample of prose."""

    # Narration
    pov: str = "unknown"                 # first / second / third / mixed
    pov_confidence: float = 0.0
    tense: str = "unknown"               # past / present / mixed
    tense_confidence: float = 0.0

    # Rhythm
    mean_sentence_length: float = 0.0
    sentence_length_sd: float = 0.0
    long_sentence_share: float = 0.0     # share of sentences over 35 words
    short_sentence_share: float = 0.0    # share of sentences under 7 words
    mean_paragraph_sentences: float = 0.0

    # Texture
    dialogue_share: float = 0.0          # share of characters inside quotes
    lexical_variety: float = 0.0         # moving-average type-token ratio
    subordination_rate: float = 0.0      # subordinating conjunctions/sentence
    adverb_rate: float = 0.0             # -ly adverbs per 1000 words
    comma_rate: float = 0.0              # commas per sentence
    dash_rate: float = 0.0               # em dashes + semicolons per 1000 words

    # Register
    ornament_index: float = 0.0          # 0-100, ornamental/lyrical intensity
    cliche_rate: float = 0.0             # stock phrases per 10k words
    grade_level: float = 0.0             # Flesch-Kincaid

    # Bookkeeping
    words_analysed: int = 0
    sentences_analysed: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    def prose_density(self) -> float:
        """
        A single 0-100 summary of how much work the prose asks of the reader.

        Low: short sentences, lots of dialogue, plain register.
        High: long varied sentences, heavy subordination, ornamental register.

        This is the axis most readers actually mean when they say a book was
        "easy" or "dense", so it earns its place as a headline number.
        """
        parts = [
            _scale(self.mean_sentence_length, 8, 30) * 0.30,
            _scale(self.sentence_length_sd, 3, 16) * 0.15,
            _scale(self.subordination_rate, 0.2, 2.0) * 0.20,
            _scale(self.ornament_index, 0, 60) * 0.15,
            (1 - _scale(self.dialogue_share, 0.05, 0.55)) * 0.10,
            _scale(self.grade_level, 4, 14) * 0.10,
        ]
        return round(sum(parts) * 100, 1)


def _scale(value: float, low: float, high: float) -> float:
    """Clamp a value into 0-1 across a stated range."""
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def strip_dialogue(text: str) -> str:
    """
    Remove quoted speech.

    Nearly every measurement here is about *narration*. Leaving dialogue in
    makes a third-person novel with a chatty first-person narrator look
    first-person, and makes a past-tense novel look present-tense. Stripping
    first is the difference between a working detector and a coin flip.
    """
    return DIALOGUE_RE.sub(" ", text)


def sentences(text: str) -> list[str]:
    raw = SENTENCE_RE.findall(text)
    return [s.strip() for s in raw if len(s.strip()) > 1]


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def count_syllables(word: str) -> int:
    w = word.lower().strip("'\u2019-")
    if not w:
        return 0
    groups = VOWEL_GROUPS.findall(w)
    n = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1
    return max(1, n)


def moving_ttr(tokens: list[str], window: int = 500) -> float:
    """
    Type-token ratio over a sliding window.

    Plain TTR punishes long samples: a 100k-word novel will always look less
    varied than a 500-word excerpt. Averaging fixed windows removes the length
    bias so a Gutenberg full text and a Google Books snippet are comparable.
    """
    if not tokens:
        return 0.0
    lowered = [t.lower() for t in tokens]
    if len(lowered) <= window:
        return len(set(lowered)) / len(lowered)
    ratios = []
    for start in range(0, len(lowered) - window + 1, max(1, window // 2)):
        chunk = lowered[start:start + window]
        ratios.append(len(set(chunk)) / len(chunk))
    return statistics.fmean(ratios)


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------

def detect_pov(narration: str) -> tuple[str, float]:
    toks = [w.lower() for w in words(narration)]
    if len(toks) < 40:
        return "unknown", 0.0

    first = sum(1 for t in toks if t in FIRST_PERSON)
    second = sum(1 for t in toks if t in SECOND_PERSON)
    third = sum(1 for t in toks if t in THIRD_PERSON)

    # "it/its" inflate third person in any prose, so discount them slightly.
    it_count = sum(1 for t in toks if t in {"it", "its"})
    third -= int(it_count * 0.6)
    third = max(third, 0)

    total = first + second + third
    if total < 8:
        return "unknown", 0.0

    scores = {"first": first / total, "second": second / total, "third": third / total}
    winner = max(scores, key=scores.get)
    top = scores[winner]
    runner_up = sorted(scores.values())[-2]

    # Second person is rare enough that a modest share is already decisive.
    if scores["second"] > 0.25 and winner != "second":
        return "mixed", round(1 - (top - runner_up), 2)

    if top - runner_up < 0.15:
        return "mixed", round(top, 2)
    return winner, round(top, 2)


def detect_tense(narration: str) -> tuple[str, float]:
    toks = [w.lower() for w in words(narration)]
    if len(toks) < 40:
        return "unknown", 0.0

    past = sum(1 for t in toks if t in PAST_MARKERS)
    present = sum(1 for t in toks if t in PRESENT_MARKERS)

    # Regular -ed verbs, minus common adjectives that end the same way.
    ed_like = sum(
        1 for t in toks
        if t.endswith("ed") and len(t) > 4 and t not in {"red", "bed", "need", "seed", "indeed"}
    )
    past += int(ed_like * 0.7)

    total = past + present
    if total < 10:
        return "unknown", 0.0

    share_past = past / total
    if share_past >= 0.65:
        return "past", round(share_past, 2)
    if share_past <= 0.35:
        return "present", round(1 - share_past, 2)
    return "mixed", round(max(share_past, 1 - share_past), 2)


def ornament_index(narration: str, toks: list[str], sents: list[str]) -> float:
    if not toks:
        return 0.0
    lower = narration.lower()
    n_words = len(toks)

    noun_hits = sum(1 for t in toks if t.lower() in ORNAMENT_NOUNS)
    simile_hits = sum(len(re.findall(p, lower)) for p in SIMILE_PATTERNS)
    punct_hits = lower.count("\u2014") + lower.count(";") + lower.count("...")

    per_1k = lambda n: (n / n_words) * 1000
    score = (
        _scale(per_1k(noun_hits), 0, 14) * 40
        + _scale(per_1k(simile_hits), 0, 12) * 35
        + _scale(per_1k(punct_hits), 0, 20) * 25
    )
    return round(score, 1)


def cliche_rate(text: str, n_words: int) -> float:
    if n_words == 0:
        return 0.0
    lower = text.lower()
    hits = sum(lower.count(p) for p in CLICHE_PHRASES)
    return round((hits / n_words) * 10_000, 2)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def analyse(text: str) -> StyleProfile:
    """Measure one sample of prose. Give it at least ~300 words."""
    profile = StyleProfile()
    if not text or not text.strip():
        profile.notes.append("Empty sample.")
        return profile

    text = re.sub(r"[ \t]+", " ", text)
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    narration = strip_dialogue(text)

    all_sents = sentences(text)
    narr_toks = words(narration)
    all_toks = words(text)

    profile.words_analysed = len(all_toks)
    profile.sentences_analysed = len(all_sents)

    if len(all_toks) < 120:
        profile.notes.append(
            "Sample under 120 words. Numbers are indicative only."
        )

    if not all_sents or not all_toks:
        return profile

    # Narration
    profile.pov, profile.pov_confidence = detect_pov(narration)
    profile.tense, profile.tense_confidence = detect_tense(narration)

    # Rhythm
    lengths = [len(words(s)) for s in all_sents]
    lengths = [n for n in lengths if n > 0]
    if lengths:
        profile.mean_sentence_length = round(statistics.fmean(lengths), 2)
        profile.sentence_length_sd = round(
            statistics.pstdev(lengths) if len(lengths) > 1 else 0.0, 2
        )
        profile.long_sentence_share = round(
            sum(1 for n in lengths if n > 35) / len(lengths), 3
        )
        profile.short_sentence_share = round(
            sum(1 for n in lengths if n < 7) / len(lengths), 3
        )
    if paragraphs:
        per_para = [max(1, len(sentences(p))) for p in paragraphs]
        profile.mean_paragraph_sentences = round(statistics.fmean(per_para), 2)

    # Texture
    quoted_chars = sum(len(m) for m in DIALOGUE_RE.findall(text))
    profile.dialogue_share = round(quoted_chars / max(1, len(text)), 3)
    profile.lexical_variety = round(moving_ttr(all_toks), 3)

    narr_lower = [t.lower() for t in narr_toks]
    sub_hits = sum(1 for t in narr_lower if t in SUBORDINATORS)
    profile.subordination_rate = round(sub_hits / len(all_sents), 2)

    adverbs = sum(1 for t in narr_lower if t.endswith("ly") and len(t) > 4)
    profile.adverb_rate = round((adverbs / max(1, len(narr_toks))) * 1000, 2)
    profile.comma_rate = round(text.count(",") / len(all_sents), 2)
    profile.dash_rate = round(
        ((text.count("\u2014") + text.count(";")) / max(1, len(all_toks))) * 1000, 2
    )

    # Register
    profile.ornament_index = ornament_index(narration, narr_toks, all_sents)
    profile.cliche_rate = cliche_rate(text, len(all_toks))

    syllables = sum(count_syllables(w) for w in all_toks)
    profile.grade_level = round(
        0.39 * (len(all_toks) / len(all_sents))
        + 11.8 * (syllables / len(all_toks))
        - 15.59,
        1,
    )

    return profile


def describe(profile: StyleProfile) -> str:
    """
    Turn measurements into one honest sentence.

    Deliberately not adjectival praise. The reader decides whether dense or
    plain is what they want; the app's job is to say which one this is.
    """
    if profile.words_analysed == 0:
        return "No sample available yet."

    density = profile.prose_density()
    if density < 30:
        base = "Plain, quick-moving prose"
    elif density < 50:
        base = "Clear prose with occasional reach"
    elif density < 70:
        base = "Considered, textured prose"
    else:
        base = "Dense, heavily worked prose"

    bits = []
    if profile.mean_sentence_length:
        bits.append(f"sentences average {profile.mean_sentence_length:.0f} words")
    if profile.sentence_length_sd > 12:
        bits.append("with wide swings in length")
    elif profile.sentence_length_sd < 5 and profile.sentences_analysed > 8:
        bits.append("at an even clip")
    if profile.dialogue_share > 0.35:
        bits.append("carried largely by dialogue")
    elif profile.dialogue_share < 0.08:
        bits.append("with little dialogue")
    if profile.ornament_index > 55:
        bits.append("and a strong taste for figurative language")
    if profile.cliche_rate > 12:
        bits.append("drawing often on stock phrasing")

    return base + ", " + ", ".join(bits) + "." if bits else base + "."
