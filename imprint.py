"""
Publisher and imprint classification.

The original plan was to scrape Wattpad and flag any title found there. That
fails three ways: title collisions are rampant in romance, Wattpad blocks
automated access, and books are routinely retitled between the web and print.
It also measures the wrong thing — Wattpad origin is a proxy for prose craft,
and prose craft is measurable directly (see style.py).

What *is* cleanly knowable is who published the book. That comes free with the
ISBN and the imprint name, it is factual, and it correlates with the editorial
process a manuscript went through. So: publishing route as a structured
signal, prose quality measured separately, and no scraping of anyone.

Wattpad-derived books are still surfaced — via imprints that publish them
openly, and via a curated provenance list contributors can extend. That is a
disclosure, not an accusation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

TIER_ORDER = [
    "major",          # Big Five conglomerate imprint
    "established",    # Substantial independent with a real editorial staff
    "small_press",    # Small independent, often literary or genre specialist
    "academic",
    "self_published",
    "unknown",
]

TIER_LABELS = {
    "major": "Major publisher",
    "established": "Established independent",
    "small_press": "Small press",
    "academic": "Academic press",
    "self_published": "Self-published",
    "unknown": "Publisher unknown",
}

# Big Five imprints. Matched as substrings against a normalised imprint name.
MAJOR_IMPRINTS = {
    # Penguin Random House
    "penguin", "random house", "knopf", "doubleday", "viking", "riverhead",
    "crown", "ballantine", "bantam", "del rey", "dutton", "putnam", "berkley",
    "vintage", "anchor books", "pantheon", "hogarth", "dial press", "avery",
    "portfolio", "ace books", "daw books", "alfred a. knopf", "clarkson potter",
    "rodale", "sentinel", "tarcherperigee", "dk publishing",
    # Hachette
    "hachette", "little, brown", "little brown", "grand central", "orbit",
    "redhook", "basic books", "mulholland", "hyperion", "perseus books",
    "twelve", "algonquin",
    # HarperCollins
    "harpercollins", "harper perennial", "harper voyager", "harperteen",
    "william morrow", "ecco", "avon books", "balzer + bray", "harper collins",
    "harpervia", "amistad", "custom house", "harper business",
    # Simon & Schuster
    "simon & schuster", "simon and schuster", "scribner", "atria",
    "gallery books", "saga press", "atheneum", "free press", "touchstone",
    "howard books", "s&s/", "marysue rucci",
    # Macmillan
    "macmillan", "farrar, straus", "farrar straus", "henry holt", "picador",
    "st. martin", "st martin", "tor books", "tordotcom", "tor.com",
    "flatiron", "celadon", "minotaur", "wednesday books", "first second",
    "bloomsbury usa", "nightfire",
}

ESTABLISHED_INDIES = {
    "bloomsbury", "faber", "canongate", "granta", "serpent's tail",
    "profile books", "verso", "oneworld", "pushkin press", "europa editions",
    "melville house", "new directions", "grove press", "grove atlantic",
    "atlantic books", "counterpoint", "catapult", "soft skull", "graywolf",
    "milkweed", "coffee house press", "tin house", "sourcebooks", "kensington",
    "chronicle books", "workman", "abrams", "quirk books", "titan books",
    "solaris", "rebellion", "angry robot", "subterranean press", "baen",
    "harlequin", "mills & boon", "entangled publishing", "podium publishing",
    "audible originals", "hodder", "headline", "quercus", "wattpad books",
    "frayed pages", "hanover square", "mira books", "park row",
}

SMALL_PRESSES = {
    "and other stories", "fitzcarraldo", "peirene", "charco press",
    "archipelago books", "deep vellum", "two lines press", "restless books",
    "open letter", "sarabande", "copper canyon", "dorothy, a publishing",
    "semiotext", "dalkey archive", "sublunary editions", "influx press",
    "galley beggar", "salt publishing", "comma press", "tilted axis",
    "transit books", "dzanc", "curbstone", "featherproof", "two dollar radio",
    "biblioasis", "coach house", "house of anansi", "invisible publishing",
}

ACADEMIC = {
    "university press", "univ. press", "routledge", "springer", "wiley",
    "elsevier", "palgrave", "sage publications", "de gruyter", "brill",
    "mit press", "princeton university", "harvard university", "yale university",
}

SELF_PUBLISHING_PLATFORMS = {
    "independently published", "createspace", "kindle direct publishing",
    "amazon digital services", "kdp", "lulu", "lulu.com", "authorhouse",
    "xlibris", "iuniverse", "trafford publishing", "publishamerica",
    "draft2digital", "smashwords", "bookbaby", "outskirts press",
    "archway publishing", "balboa press", "page publishing", "dorrance",
    "tellwell", "friesenpress", "blurb", "ingramspark", "self-published",
    "self published", "author's own", "vellum",
}


@dataclass
class ImprintProfile:
    publisher: str | None = None
    tier: str = "unknown"
    tier_label: str = "Publisher unknown"
    isbn13: str | None = None
    registration_group: str | None = None
    isbn_note: str | None = None
    wattpad_origin: bool = False
    wattpad_note: str | None = None
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def normalise_isbn(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"[^0-9Xx]", "", raw).upper()
    if len(digits) == 13:
        return digits
    if len(digits) == 10:
        return isbn10_to_13(digits)
    return None


def isbn10_to_13(isbn10: str) -> str | None:
    if len(isbn10) != 10:
        return None
    core = "978" + isbn10[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    check = (10 - total % 10) % 10
    return core + str(check)


def isbn13_valid(isbn13: str) -> bool:
    if len(isbn13) != 13 or not isbn13.isdigit():
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn13[:12]))
    return (10 - total % 10) % 10 == int(isbn13[12])


def read_isbn(isbn13: str | None) -> tuple[str | None, str | None]:
    """
    Pull what the ISBN itself can tell us.

    Full registrant resolution needs the ISBN International range table, which
    is a large file and a separate concern. What we can say without it is the
    registration group, and one genuinely useful fact: the 979-8 block is
    administered in the United States by Bowker and is where the large
    majority of print-on-demand and self-published titles land.
    """
    if not isbn13 or not isbn13_valid(isbn13):
        return None, None

    if isbn13.startswith("9798"):
        return "979-8", (
            "979-8 registration block. Overwhelmingly print-on-demand and "
            "self-published titles."
        )
    if isbn13.startswith("9790"):
        return "979-0", "Sheet music registration block."

    body = isbn13[3:]
    for glen, label in ((1, None), (2, None), (3, None)):
        group = body[:glen]
        if glen == 1 and group in {"0", "1"}:
            return f"978-{group}", "English-language registration group."
        if glen == 2 and group in {"80", "82", "83", "84", "85", "86", "87", "88", "89", "90", "91", "92", "93", "94"}:
            return f"978-{group}", "Non-English registration group."
    return f"978-{body[:1]}", None


def load_provenance() -> dict:
    """
    Curated list of books with a known web-serial origin.

    A short, sourced, human-maintained file beats a scraper that guesses from
    titles. Contributors add entries with a citation; the app discloses, and
    says nothing it cannot support.
    """
    path = DATA_DIR / "provenance.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _match(name: str, vocabulary: set[str]) -> str | None:
    for entry in vocabulary:
        if entry in name:
            return entry
    return None


def classify(
    publisher: str | None,
    isbn: str | None = None,
    title: str | None = None,
    author: str | None = None,
) -> ImprintProfile:
    profile = ImprintProfile(publisher=publisher)

    isbn13 = normalise_isbn(isbn)
    profile.isbn13 = isbn13
    profile.registration_group, profile.isbn_note = read_isbn(isbn13)

    name = (publisher or "").lower().strip()
    name = re.sub(r"\s+", " ", name)

    if name:
        if _match(name, SELF_PUBLISHING_PLATFORMS):
            profile.tier, profile.confidence = "self_published", 0.95
        elif _match(name, ACADEMIC):
            profile.tier, profile.confidence = "academic", 0.9
        elif _match(name, MAJOR_IMPRINTS):
            profile.tier, profile.confidence = "major", 0.9
        elif _match(name, ESTABLISHED_INDIES):
            profile.tier, profile.confidence = "established", 0.85
        elif _match(name, SMALL_PRESSES):
            profile.tier, profile.confidence = "small_press", 0.85

    # ISBN evidence, used only where the imprint name told us nothing.
    if profile.tier == "unknown" and profile.registration_group == "979-8":
        profile.tier, profile.confidence = "self_published", 0.7

    profile.tier_label = TIER_LABELS[profile.tier]

    # Provenance disclosure
    if title:
        key = f"{title.strip().lower()}|{(author or '').strip().lower()}"
        record = load_provenance().get(key) or load_provenance().get(
            title.strip().lower()
        )
        if record:
            profile.wattpad_origin = True
            profile.wattpad_note = record.get("note")

    return profile
