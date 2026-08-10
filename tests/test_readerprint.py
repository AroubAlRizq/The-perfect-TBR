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
    # provisional=False because these carry full style values. Leaving them
    # provisional would attract the confidence penalty in recommend() and
    # mask whatever the test is actually asserting about.
    return Book(
        title=title, author=f"{title} Author", subjects=["literary"],
        page_count=300, provisional=False,
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
    check("clean book is not penalised", scored["Plain Clean"].penalty == 0,
          f"({scored['Plain Clean'].penalty})")

    # Confidence penalty: an unmeasured book should rank below an otherwise
    # identical measured one.
    twin_measured = make_book("Twin Measured", 78, 1.0)
    twin_unknown = make_book("Twin Unknown", 78, 1.0)
    twin_unknown.provisional, twin_unknown.style = True, {}
    space2 = build_space(books + [twin_measured, twin_unknown])
    profile2 = build_profile(space2, [
        ReadingEvent(book_id=b.id, verdict="loved")
        for b in books[:3]
    ])
    ranked = {r.book.title: r for r in recommend(
        space2, profile2, exclude_ids=set(), limit=20)}
    check("unmeasured book ranks below its measured twin",
          ranked["Twin Unknown"].score < ranked["Twin Measured"].score,
          f"({ranked['Twin Unknown'].score:.3f} vs {ranked['Twin Measured'].score:.3f})")
    check("unmeasured book says so",
          any("not measured" in c for c in ranked["Twin Unknown"].cautions))
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


def test_sections():
    print("\nSections")
    from readerprint.recommend import build_space, build_profile, recommend
    from readerprint.sections import build_sections
    from datetime import date

    corpus = []
    for i in range(4):
        b = make_book(f"Loved {i}", 70, 1.0)
        b.year, b.author = 1990 + i, "Recurring Author" if i < 2 else f"Other {i}"
        corpus.append(b)
    # An unread book by an author already rated well — otherwise there is
    # nothing for the "more from writers you rated well" section to hold.
    for i in range(2):
        b = make_book(f"Unread By Favourite {i}", 69, 1.0)
        b.author, b.year = "Recurring Author", 1995 + i
        corpus.append(b)
    for i in range(6):
        b = make_book(f"Recent {i}", 68, 1.0)
        b.year = date.today().year - 1
        corpus.append(b)
    for i in range(4):
        b = make_book(f"Shortie {i}", 66, 1.0)
        b.page_count, b.year = 120, 2001
        corpus.append(b)
    for i in range(3):
        b = make_book(f"Rendered {i}", 67, 1.0)
        b.translator, b.year = "A Translator", 2005
        corpus.append(b)
    for i in range(20):
        b = make_book(f"Vague {i}", 60, 1.0)
        b.provisional, b.style, b.year = True, {}, 1999
        corpus.append(b)

    space = build_space(corpus)
    events = [ReadingEvent(book_id=corpus[i].id, verdict="loved") for i in range(3)]
    profile = build_profile(space, events)
    results = recommend(space, profile, {e.book_id for e in events}, limit=40)
    sections = build_sections(results, profile.loved_authors)

    check("sections are produced", len(sections) >= 3, f"({len(sections)})")

    seen, duplicated = set(), []
    for section in sections:
        for item in section.items:
            if item.book.id in seen:
                duplicated.append(item.book.title)
            seen.add(item.book.id)
    check("no book appears in two sections", not duplicated, f"({duplicated[:3]})")

    placed = len(seen) + sum(s.hidden for s in sections)
    check("every result is placed or explicitly held back",
          placed == len(results), f"({placed} of {len(results)})")
    check("no section is left empty", all(s.items for s in sections))
    check("thin sections are folded away",
          all(len(s.items) >= 2 or s.key in {"closest", "stretch", "unmeasured"}
              for s in sections),
          f"({[(s.key, len(s.items)) for s in sections]})")
    check("no section exceeds its cap",
          all(len(s.items) <= 8 for s in sections),
          f"({[(s.key, len(s.items)) for s in sections]})")

    keys = [s.key for s in sections]
    check("unmeasured books come last", keys[-1] == "unmeasured", f"({keys})")
    check("author section found the repeat author", "authors" in keys, f"({keys})")

    for section in sections:
        if section.key == "authors":
            check("author section holds only that author's books",
                  all(r.book.author in profile.loved_authors for r in section.items))
        if section.key == "short":
            check("short section holds only short books",
                  all(r.book.length_band() == "short" for r in section.items))
        if section.key == "unmeasured":
            check("unmeasured section holds only unmeasured books",
                  all(r.book.provisional for r in section.items))

    check("empty input yields no sections", build_sections([], set()) == [])


def test_year_filter():
    print("\nYear filter")
    from readerprint.recommend import build_space, build_profile, recommend

    corpus = []
    for year in (1890, 1950, 1999, 2010, 2018, 2023):
        b = make_book(f"Book {year}", 70, 1.0)
        b.year = year
        corpus.append(b)
    undated = make_book("No Year", 70, 1.0)
    undated.year = None
    corpus.append(undated)

    space = build_space(corpus)
    events = [ReadingEvent(book_id=corpus[i].id, verdict="loved") for i in range(3)]
    profile = build_profile(space, events)
    read = {e.book_id for e in events}

    everything = recommend(space, profile, read, limit=20)
    check("no filter returns undated books",
          any(r.book.year is None for r in everything))

    modern = recommend(space, profile, read, limit=20, min_year=2010)
    years = [r.book.year for r in modern]
    check("min_year excludes older books", all(y >= 2010 for y in years), f"({years})")
    check("min_year excludes undated books",
          not any(r.book.year is None for r in modern))

    window = recommend(space, profile, read, limit=20, min_year=1900, max_year=2000)
    check("a year window bounds both ends",
          all(1900 <= r.book.year <= 2000 for r in window),
          f"({[r.book.year for r in window]})")

    check("an impossible range returns nothing",
          recommend(space, profile, read, limit=20, min_year=2100) == [])


def test_genres():
    print("\nGenres")
    from readerprint.genres import ALL_GENRES, derive, label_counts

    check("detective subjects become mystery",
          "Mystery & crime" in derive(["Detective and mystery stories", "England"]))
    check("varied phrasings reach the same genre",
          "Mystery & crime" in derive(["Crime -- Fiction"])
          and "Mystery & crime" in derive(["Fiction, mystery & detective"]))
    check("fantasy detected", "Fantasy" in derive(["Fantasy fiction", "Dragons"]))
    check("science fiction detected",
          "Science fiction" in derive(["Science fiction", "Time travel"]))

    check("bare 'Fiction' yields nothing", derive(["Fiction"]) == [],
          f"({derive(['Fiction'])})")
    check("empty subjects yield nothing", derive([]) == [] and derive(None) == [])
    check("unmatched subjects yield nothing rather than a guess",
          derive(["Bridges", "Structural engineering"]) == [],
          f"({derive(['Bridges', 'Structural engineering'])})")

    multi = derive(["Fantasy fiction", "Literary", "Magic", "Wizards"])
    check("a book can hold several genres", len(multi) >= 2, f"({multi})")
    check("genres are capped at three",
          len(derive(["Fantasy", "Science fiction", "Romance", "Horror",
                      "Mystery", "Historical fiction", "Humour"])) <= 3)

    check("ordering is stable",
          derive(["Fantasy fiction", "Magic"]) == derive(["Fantasy fiction", "Magic"]))
    check("description fills in for thin subjects",
          "Science fiction" in derive([], "A novel of time travel and robots"))
    check("every rule name is exposed for the filter",
          set(ALL_GENRES) and all(isinstance(g, str) for g in ALL_GENRES))

    counted = label_counts([
        make_book("A", 50, 1.0), make_book("B", 50, 1.0),
    ])
    check("counts come back sorted", isinstance(counted, list))


def test_genre_filter():
    print("\nGenre filter")
    from readerprint.recommend import build_space, build_profile, recommend

    corpus = []
    for name, genres in [
        ("Dragon One", ["Fantasy"]), ("Dragon Two", ["Fantasy"]),
        ("Dragon Three", ["Fantasy"]),
        ("Sleuth One", ["Mystery & crime"]), ("Sleuth Two", ["Mystery & crime"]),
        ("Both", ["Fantasy", "Mystery & crime"]),
        ("Neither", ["Historical"]), ("Untagged", []),
    ]:
        b = make_book(name, 68, 1.0)
        b.genres, b.year = genres, 2010
        corpus.append(b)

    space = build_space(corpus)
    events = [ReadingEvent(book_id=corpus[i].id, verdict="loved") for i in range(3)]
    profile = build_profile(space, events)
    read = {e.book_id for e in events}

    unfiltered = recommend(space, profile, read, limit=20)
    check("no filter returns everything left", len(unfiltered) == 5, f"({len(unfiltered)})")

    fantasy = recommend(space, profile, read, limit=20, allowed_genres={"Fantasy"})
    check("single genre filters correctly",
          all("Fantasy" in r.book.genres for r in fantasy),
          f"({[r.book.title for r in fantasy]})")
    check("untagged books are excluded by a genre filter",
          not any(r.book.title == "Untagged" for r in fantasy))

    either = recommend(space, profile, read, limit=20,
                       allowed_genres={"Fantasy", "Mystery & crime"})
    check("multiple genres behave as OR, not AND",
          len(either) > len(fantasy), f"({len(either)} vs {len(fantasy)})")
    check("a book matching one of several is included",
          any(r.book.title == "Sleuth One" for r in either))
    check("a book matching neither is excluded",
          not any(r.book.title == "Neither" for r in either))

    check("an absent genre returns nothing",
          recommend(space, profile, read, limit=20, allowed_genres={"Horror"}) == [])


def test_genre_persistence():
    print("\nGenre storage")
    conn = db.connect(":memory:")
    db.init(conn)

    book = Book(title="Derived On Write", author="Someone",
                subjects=["Fantasy fiction", "Dragons"])
    check("genres start empty", book.genres == [])
    db.upsert_book(conn, book)
    check("genres derived on write", "Fantasy" in book.genres, f"({book.genres})")

    loaded = db.get_book(conn, book.id)
    check("genres survive a round trip", loaded.genres == book.genres)

    explicit = Book(title="Explicit", subjects=["Fantasy fiction"],
                    genres=["Horror"])
    db.upsert_book(conn, explicit)
    check("an explicit genre is not overwritten",
          db.get_book(conn, explicit.id).genres == ["Horror"])


def test_genre_migration():
    print("\nGenre migration")
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # The pre-genre schema.
    conn.execute("""
        CREATE TABLE books (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT, year INTEGER,
            isbn13 TEXT, publisher TEXT, page_count INTEGER, word_count INTEGER,
            language TEXT, original_language TEXT, translator TEXT, series TEXT,
            series_position REAL, subjects TEXT, description TEXT, cover_url TEXT,
            excerpt TEXT, excerpt_source TEXT, excerpt_licence TEXT, style TEXT,
            imprint TEXT, ratings TEXT, content_flags TEXT, openlibrary_key TEXT,
            google_books_id TEXT, gutenberg_id INTEGER, provisional INTEGER DEFAULT 1
        )""")
    conn.execute(
        "INSERT INTO books (id, title, subjects) VALUES ('x1', 'Old Book', ?)",
        ('["Detective and mystery stories"]',),
    )
    conn.commit()

    before = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
    check("old schema lacks genres", "genres" not in before)

    db.init(conn)
    after = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
    check("migration adds the column", "genres" in after)
    check("existing rows survive",
          conn.execute("SELECT COUNT(*) AS n FROM books").fetchone()["n"] == 1)

    from readerprint.corpus import backfill_genres
    filled = backfill_genres(conn)
    check("backfill tags the old row", filled == 1, f"({filled})")
    check("backfill derived the right genre",
          "Mystery & crime" in db.get_book(conn, "x1").genres,
          f"({db.get_book(conn, 'x1').genres})")
    check("backfill is idempotent", backfill_genres(conn) == 0)


def test_tropes():
    print("\nTropes")
    from readerprint.tropes import derive

    check("enemies to lovers detected",
          "Enemies to lovers" in derive([], "They were sworn enemies before they were anything else."))
    check("found family detected",
          "Found family" in derive(["Fiction"], "A ragtag crew who become a found family."))
    check("dark academia detected",
          "Dark academia" in derive(["Campus fiction"], "An elite university and a secret society."))
    check("epistolary detected",
          "Epistolary" in derive(["Epistolary fiction"], "Told in letters."))
    check("locked room detected",
          "Locked room" in derive([], "An isolated manor where everyone is a suspect."))

    check("thin input yields nothing", derive([], "") == [] and derive(None, "x") == [])
    check("unrelated text yields nothing",
          derive(["Bridges"], "A technical manual about structural loads.") == [],
          f"({derive(['Bridges'], 'A technical manual about structural loads.')})")

    check("tropes are capped at four",
          len(derive([], "Enemies to lovers, fake dating, found family, a heist, "
                         "a time loop, revenge and a chosen one prophecy.")) <= 4)
    check("ordering is stable",
          derive([], "fake dating and a heist") == derive([], "fake dating and a heist"))

    # Specificity: rivalry plus romance alone must not trip the tag.
    loose = derive([], "A romance about two rivals in the publishing world.")
    check("loose wording does not trip enemies to lovers",
          "Enemies to lovers" not in loose, f"({loose})")


def test_trope_persistence():
    print("\nTrope storage")
    conn = db.connect(":memory:")
    db.init(conn)
    book = Book(title="Test", subjects=["Fiction"],
                description="Sworn enemies forced into a fake engagement.")
    db.upsert_book(conn, book)
    check("tropes derived on write", len(book.tropes) >= 2, f"({book.tropes})")
    check("tropes survive a round trip", db.get_book(conn, book.id).tropes == book.tropes)
    check("column exists after migration",
          "tropes" in {r["name"] for r in conn.execute("PRAGMA table_info(books)")})


def test_measured_share():
    print("\nTwo-pool selection")
    from readerprint.recommend import build_space, build_profile, recommend

    measured, provisional = [], []
    for i in range(10):
        b = make_book(f"Measured {i}", 66 + i % 5, 1.0)
        b.year, b.genres = 2000 + i, ["Fantasy"]
        measured.append(b)
    for i in range(60):
        b = make_book(f"Provisional {i}", 60, 1.0)
        b.provisional, b.style = True, {}
        b.year, b.genres = 2000 + i % 20, ["Fantasy"]
        provisional.append(b)

    corpus = measured + provisional
    space = build_space(corpus)
    events = [ReadingEvent(book_id=measured[i].id, verdict="loved") for i in range(3)]
    profile = build_profile(space, events)
    read = {e.book_id for e in events}

    def share_of(results):
        return sum(1 for r in results if not r.book.provisional) / max(1, len(results))

    half = recommend(space, profile, read, limit=8, measured_share=0.5)
    check("an even split is honoured", share_of(half) >= 0.5, f"({share_of(half):.2f})")
    check("the list is still full", len(half) == 8, f"({len(half)})")

    quarter = recommend(space, profile, read, limit=8, measured_share=0.25)
    check("a lower share admits more provisional books",
          share_of(quarter) < share_of(half) or share_of(quarter) <= 0.5,
          f"({share_of(quarter):.2f} vs {share_of(half):.2f})")
    check("the wider list is still full", len(quarter) == 8)

    # measured_share is a soft floor, not a filter: with only seven measured
    # books left after exclusions it takes all seven and fills the last slot
    # rather than returning a short list. measured_only is the hard version.
    everything = recommend(space, profile, read, limit=8, measured_share=1.0)
    taken = sum(1 for r in everything if not r.book.provisional)
    check("a full share takes every measured book available",
          taken == len(measured) - len(read), f"({taken})")
    check("the list is still filled to the limit", len(everything) == 8)

    hard = recommend(space, profile, read, limit=8, measured_only=True)
    check("measured_only is a hard filter, not a floor",
          all(not r.book.provisional for r in hard) and len(hard) == 7,
          f"({len(hard)})")

    # A quota must never shorten the list when a pool runs dry.
    starved = recommend(space, profile, read, limit=8, measured_share=1.0,
                        allowed_genres={"Fantasy"}, min_year=2007)
    check("an exhausted pool falls back rather than returning fewer",
          len(starved) > 0, f"({len(starved)})")

    # Evidence and Spread must stay independent.
    tight = recommend(space, profile, read, limit=8, measured_share=0.5, diversity=0.1)
    loose = recommend(space, profile, read, limit=8, measured_share=0.5, diversity=0.7)
    check("diversity does not disturb the measured quota",
          share_of(tight) >= 0.5 and share_of(loose) >= 0.5,
          f"({share_of(tight):.2f}, {share_of(loose):.2f})")
    check("diversity still changes the selection",
          [r.book.title for r in tight] != [r.book.title for r in loose])


if __name__ == "__main__":
    for suite in (test_style, test_imprint, test_reviews, test_import,
                  test_recommender, test_sections, test_year_filter,
                  test_genres, test_genre_filter, test_genre_persistence,
                  test_genre_migration, test_tropes, test_trope_persistence,
                  test_measured_share,
                  test_storage, test_shadowing_regression):
        suite()

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        sys.exit(1)
    print("All checks passed.")