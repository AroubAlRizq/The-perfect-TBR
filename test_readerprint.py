"""
Tests.

Run:  python -m pytest tests/ -q     (or: python tests/test_readerprint.py)

These cover the things that broke while building, plus the claims the README
makes. Two in particular are regression guards:

  * a stored prose_density value once shadowed the method of the same name
  * DNF reasons once applied penalties in the wrong direction
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from readerprint import db
from readerprint.imprint import classify, isbn13_valid, normalise_isbn
from readerprint.ingest import parse_export
from readerprint.models import Book, ReadingEvent
from readerprint.recommend import build_profile, build_space
from readerprint.reviews import Review, rank_reviews, summarise_ratings
from readerprint.style import analyse, describe

PLAIN = """
The bus came at six. Marta got on. She paid the fare and sat near the back.
Rain came in through the window. She did not move. The road bent north past
the mill. She counted the poles. There were forty between the mill and the
bridge. She had counted them before.
""" * 4

ORNATE = """
Evening settled over the valley like a hand closing, and the light that had
lain all afternoon across the barley went out of it slowly, as though the
field itself were reluctant, as though some old covenant between the earth
and the failing sun required a certain ceremony in the parting, a silence
that was not silence at all but the accumulated sound of everything too far
away to name.
""" * 4

STOCK = """
I walked into class and my breath hitched. He was gorgeous. He smirked and I
felt my cheeks burn. I rolled my eyes and bit my lip. My heart hammered. Sparks
flew when our hands brushed and shivers ran down my spine. He ran a hand
through his hair and his jaw clenched. Little did I know he was trouble.
""" * 4

PRESENT = """
She walks to the window and looks out. The street is empty. She thinks about
the letter. She knows what it says. She does not open it. The clock ticks in
the hall and she counts the seconds because counting is something to do.
""" * 4

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


# ── Style ──────────────────────────────────────────────────────────────

def test_style():
    print("\nProse measurement")
    plain, ornate, stock, present = (analyse(t) for t in (PLAIN, ORNATE, STOCK, PRESENT))

    check("plain reads less dense than ornate",
          plain.prose_density() < ornate.prose_density(),
          f"({plain.prose_density()} vs {ornate.prose_density()})")

    check("ornate prose scores high on the ornament index",
          ornate.ornament_index > 40, f"({ornate.ornament_index})")

    check("stock phrasing is caught",
          stock.cliche_rate > 20, f"({stock.cliche_rate})")

    check("clean prose is not flagged for stock phrasing",
          plain.cliche_rate == 0 and ornate.cliche_rate == 0)

    check("first person detected", stock.pov == "first", f"({stock.pov})")
    check("third person detected", plain.pov == "third", f"({plain.pov})")
    check("past tense detected", plain.tense == "past", f"({plain.tense})")
    check("present tense detected", present.tense == "present", f"({present.tense})")

    check("dialogue is excluded from POV detection",
          analyse('He walked home. "I am tired," he said. "I am going to bed." '
                  'He locked the door and climbed the stairs slowly. ' * 8).pov == "third")

    check("short samples are flagged rather than trusted",
          any("under 120 words" in n for n in analyse("Too short. " * 5).notes))

    check("describe() never claims a book is good",
          not any(w in describe(ornate).lower() for w in ("beautiful", "excellent", "poor", "bad")))


# ── Imprint ────────────────────────────────────────────────────────────

def test_imprint():
    print("\nPublisher classification")
    check("ISBN-10 converts to a valid ISBN-13",
          isbn13_valid(normalise_isbn("0439023483")))
    check("malformed ISBNs return nothing", normalise_isbn("not-an-isbn") is None)
    check("Big Five imprint recognised", classify("Vintage Books").tier == "major")
    check("small press recognised", classify("Fitzcarraldo Editions").tier == "small_press")
    check("self-publishing platform recognised",
          classify("Independently published").tier == "self_published")
    # Valid check digit — an invalid one is correctly rejected before the
    # block is ever read, which is the behaviour on the line below.
    check("979-8 block infers self-published",
          classify(None, "9798612345671").tier == "self_published")
    check("ISBNs failing the checksum are ignored",
          classify(None, "9798612345678").registration_group is None)
    check("unknown publisher stays unknown",
          classify("Some Press Nobody Has Heard Of").tier == "unknown")
    check("curated provenance is disclosed",
          classify("Gallery Books", None, "After", "Anna Todd").wattpad_origin)
    check("provenance is not invented for other books",
          not classify("Vintage", None, "Never Let Me Go", "Kazuo Ishiguro").wattpad_origin)


# ── Reviews ────────────────────────────────────────────────────────────

def test_reviews():
    print("\nReview scoring")
    gushing = Review("goodreads", "OBSESSED!!! I sobbed. This wrecked me. No words. "
                                  "Everyone needs to read this. All the stars!!!", 5.0, votes=2100)
    considered = Review("goodreads",
        "The prose is remarkable in the first third and the imagery stays with you. "
        "But the pacing collapses in the middle section, which runs two hundred pages "
        "longer than it needs to. The first-person voice mostly earns itself, though "
        "the final chapter felt tacked on. Worth it if you have patience for a slow middle.",
        3.0, votes=890)
    ranked = rank_reviews([gushing, considered])

    check("balanced craft review outranks a high-vote rave",
          ranked and ranked[0].text.startswith("The prose is remarkable"))
    check("pure reaction scores near zero",
          all(r.informativeness < 20 for r in ranked if r.text.startswith("OBSESSED")))
    check("craft discussion is reported as a signal",
          "discusses craft directly" in ranked[0].signals)

    summary = summarise_ratings({
        "goodreads": {"mean": 4.3, "count": 40000},
        "amazon": {"mean": 4.8, "count": 30},
    })
    check("low-volume high rating does not dominate",
          summary.weighted_score < 4.4, f"({summary.weighted_score})")
    check("total ratings are summed", summary.total_ratings == 40030)


# ── Import ─────────────────────────────────────────────────────────────

CSV = '''Book Id,Title,Author,ISBN13,My Rating,Publisher,Number of Pages,Date Read,Bookshelves,Exclusive Shelf,My Review
1,Piranesi,Susanna Clarke,"=""9781635575637""",5,Bloomsbury,245,2024/06/20,,read,
2,Fourth Wing,Rebecca Yarros,"=""9781649374042""",1,Entangled,517,2024/08/03,dnf,read,Could not get past the prose.
3,Wanted Later,Nobody,"=""""",0,,300,,,to-read,
4,No Verdict,Someone,"=""""",0,,200,2024/01/01,,read,
'''


def test_import():
    print("\nCSV import")
    parsed = parse_export(CSV)
    report = parsed["report"]

    check("read-and-rated rows are imported", report["imported"] == 2, f"({report['imported']})")
    check("to-read rows are skipped", report["skipped_to_read"] == 1)
    check("unrated rows are skipped", report["skipped_unrated"] == 1)
    check("DNF inferred from shelf name", report["dnf_found"] == 1)
    check("Excel-wrapped ISBNs are unwrapped",
          parsed["books"][0].isbn13 == "9781635575637",
          f"({parsed['books'][0].isbn13})")
    check("five stars becomes loved", parsed["events"][0].verdict == "loved")
    check("DNFs are surfaced as needing a reason", report["needs_dnf_reasons"] == 1)
    check("a file with no title column is rejected",
          "error" in parse_export("a,b\n1,2\n")["report"])


# ── Recommender ────────────────────────────────────────────────────────

def make_book(title, density, cliche, pov="third"):
    return Book(
        title=title, author=f"{title} Author", subjects=["literary"],
        page_count=300,
        style={"prose_density": density, "cliche_rate": cliche, "pov": pov,
               "tense": "past", "mean_sentence_length": density / 3,
               "dialogue_share": 0.2, "ornament_index": density * 0.7},
    )


def test_recommender():
    print("\nRecommender")
    books = [
        make_book("Dense One", 80, 1.0), make_book("Dense Two", 78, 1.0),
        make_book("Dense Three", 76, 1.0), make_book("Plain Stock", 30, 22.0),
        make_book("Plain Clean", 30, 0.5), make_book("Dense Stock", 78, 24.0),
    ]
    space = build_space(books)
    index = {b.title: b.id for b in books}

    events = [
        ReadingEvent(book_id=index["Dense One"], verdict="loved"),
        ReadingEvent(book_id=index["Dense Two"], verdict="loved"),
        ReadingEvent(book_id=index["Dense Three"], verdict="liked"),
        ReadingEvent(book_id=index["Plain Stock"], verdict="dnf", dnf_reasons=["prose"]),
    ]
    profile = build_profile(space, events)

    check("profile becomes usable at three liked books", profile.is_usable)
    check("liked authors are remembered", "Dense One Author" in profile.loved_authors)
    check("broad subjects are collected", "literary" in profile.loved_subjects)

    # The regression: the abandoned book had HIGH cliche and LOW density, so
    # cliche should be penalised and density should not.
    check("aversion fires on the axis that actually differed",
          "cliche_rate" in profile.aversions, f"({profile.aversions})")
    check("aversion does not fire on density, which went the other way",
          "prose_density" not in profile.aversions, f"({profile.aversions})")

    from readerprint.recommend import recommend
    results = recommend(space, profile, exclude_ids={e.book_id for e in events}, limit=5)
    scored = {r.book.title: r for r in results}

    check("stock-phrase book is penalised", scored["Dense Stock"].penalty > 0)
    check("clean book is not penalised", scored["Plain Clean"].penalty == 0)
    check("every result explains itself", all(r.reasons for r in results))
    check("read books are excluded",
          not any(r.book.title == "Dense One" for r in results))


# ── Storage ────────────────────────────────────────────────────────────

def test_storage():
    print("\nStorage")
    conn = db.connect(":memory:")
    db.init(conn)

    book = make_book("Round Trip", 50, 2.0)
    book.subjects = ["gothic", "translated"]
    db.upsert_book(conn, book)
    loaded = db.get_book(conn, book.id)

    check("JSON fields survive a round trip", loaded.subjects == ["gothic", "translated"])
    check("style survives a round trip", loaded.style["prose_density"] == 50)
    check("exact title lookup works", db.find_book(conn, "Round Trip") is not None)
    check("series parentheticals are stripped on lookup",
          db.find_book(conn, "Round Trip (Some Series, #2)") is not None)
    check("unrelated titles do not match", db.find_book(conn, "A Different Book") is None)

    db.upsert_event(conn, ReadingEvent(book_id=book.id, verdict="loved"))
    db.upsert_event(conn, ReadingEvent(book_id=book.id, verdict="dnf",
                                       dnf_reasons=["pacing"]))
    events = db.get_events(conn)
    check("one event per book per user", len(events) == 1)
    check("the later verdict wins", events[0].verdict == "dnf")
    check("DNF weight is negative", events[0].weight() < 0)


def test_shadowing_regression():
    """A stored prose_density float once overwrote the method of that name."""
    print("\nRegression guards")
    from dataclasses import fields
    from readerprint.style import StyleProfile

    stored = analyse(PLAIN).as_dict()
    stored["prose_density"] = 42.0          # as persisted by corpus.py

    allowed = {f.name for f in fields(StyleProfile)}
    profile = StyleProfile()
    for key, value in stored.items():
        if key in allowed:
            setattr(profile, key, value)

    check("prose_density stays callable after restore",
          callable(profile.prose_density))
    check("describe() survives a persisted style dict",
          isinstance(describe(profile), str))


if __name__ == "__main__":
    for suite in (test_style, test_imprint, test_reviews, test_import,
                  test_recommender, test_storage, test_shadowing_regression):
        suite()

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        sys.exit(1)
    print("All checks passed.")
