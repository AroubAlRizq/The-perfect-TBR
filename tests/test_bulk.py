"""
Offline tests for the bulk pipeline.

The dumps are gigabytes and live behind hosts a test suite should not depend
on, so these build small files in the real formats and run the parsers over
them. That covers everything except the HTTP transfer itself: format quirks,
the fiction filter, edition scoring, memory-safe streaming, and the Gutenberg
mirror path.

Run:  python tests/test_bulk.py
"""

import gzip
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from readerprint import db
from readerprint.bulk import gutenberg, openlibrary

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def write_dump(path: Path, records: list[tuple[str, dict]]) -> None:
    """Write rows in Open Library's tab-separated dump format."""
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for kind, record in records:
            handle.write(
                f"{kind}\t{record.get('key', '')}\t1\t2026-01-01T00:00:00\t"
                f"{json.dumps(record)}\n"
            )


WORKS = [
    ("/type/work", {
        "key": "/works/OL1W", "title": "The Measured Hour",
        "subjects": ["Fiction", "Psychological fiction", "London"],
        "first_publish_date": "1961",
        "description": {"type": "/type/text", "value": "A novel about time."},
    }),
    ("/type/work", {
        "key": "/works/OL2W", "title": "Salt and Iron",
        "subjects": ["Historical fiction", "Sea stories"],
        "first_publish_date": "March 1888",
        "description": "A plain string description.",
    }),
    ("/type/work", {
        "key": "/works/OL3W", "title": "A History of Bridges",
        "subjects": ["Engineering", "Bridges", "History"],
    }),
    ("/type/work", {
        "key": "/works/OL4W", "title": "Dickens: A Critical Study",
        "subjects": ["Fiction", "History and criticism", "Literary criticism"],
    }),
    ("/type/work", {"key": "/works/OL5W", "subjects": ["Fiction"]}),  # no title
    ("/type/work", {"key": "/works/OL6W", "title": "Untagged Book"}),  # no subjects
]

EDITIONS = [
    # Sparse edition of work 1 — arrives first, should lose to the fuller one.
    ("/type/edition", {
        "key": "/books/OL10M", "title": "The Measured Hour",
        "works": [{"key": "/works/OL1W"}],
        "languages": [{"key": "/languages/eng"}],
        "publish_date": "1975",
    }),
    # Rich edition of work 1 — should win.
    ("/type/edition", {
        "key": "/books/OL11M", "title": "The Measured Hour",
        "works": [{"key": "/works/OL1W"}],
        "languages": [{"key": "/languages/eng"}],
        "isbn_13": ["9780141441146"],
        "publishers": ["Fitzcarraldo Editions"],
        "number_of_pages": 312,
        "publish_date": "2004",
        "covers": [8899],
        "contributions": ["Ann Goldstein (Translator)"],
    }),
    ("/type/edition", {
        "key": "/books/OL20M", "title": "Salt and Iron",
        "works": [{"key": "/works/OL2W"}],
        "languages": [{"key": "/languages/eng"}],
        "isbn_10": ["0439023483"],
        "publishers": ["Independently published"],
        "number_of_pages": 240,
        "publish_date": "2019",
    }),
    # French edition — should be filtered out by language.
    ("/type/edition", {
        "key": "/books/OL21M", "title": "Sel et Fer",
        "works": [{"key": "/works/OL2W"}],
        "languages": [{"key": "/languages/fre"}],
        "isbn_13": ["9782070409181"],
        "publishers": ["Gallimard"],
        "number_of_pages": 999,
        "covers": [1], "publish_date": "2001",
    }),
    # Edition of a non-fiction work — should update nothing.
    ("/type/edition", {
        "key": "/books/OL30M", "title": "A History of Bridges",
        "works": [{"key": "/works/OL3W"}],
        "languages": [{"key": "/languages/eng"}],
        "isbn_13": ["9780262035613"], "number_of_pages": 500,
    }),
    # No work link at all.
    ("/type/edition", {"key": "/books/OL40M", "title": "Orphan Edition"}),
]

CATALOG = '''Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves
1342,Text,1998-06-01,Pride and Prejudice,en,"Austen, Jane, 1775-1817","England -- Fiction; Love stories; Domestic fiction",PR,"Best Books Ever Listings; Harvard Classics"
2701,Text,2001-07-01,"Moby Dick; Or, The Whale",en,"Melville, Herman, 1819-1891","Whaling -- Fiction; Sea stories",PS,"Adventure; Best Books Ever Listings"
1112,Text,1998-11-01,Romeo and Juliet,en,"Shakespeare, William, 1564-1616","Tragedies; Drama; Plays",PR,"Plays"
1065,Text,1997-02-01,The Raven,en,"Poe, Edgar Allan, 1809-1849","Poetry; American poetry",PS,"Poetry"
9999,Text,2005-01-01,Bridge Engineering Manual,en,"Someone, A.","Engineering; Handbooks",TA,""
8888,Text,2003-01-01,Les Miserables,fr,"Hugo, Victor, 1802-1885","Historical fiction",PQ,"Historical Fiction"
7777,Audio,2010-01-01,An Audiobook,en,"Nobody","Fiction",PS,"Fiction"
'''

SAMPLE_TEXT = """The Project Gutenberg eBook of A Test Novel

This ebook is for the use of anyone anywhere at no cost and with almost
no restrictions whatsoever. You may copy it, give it away or re-use it.

*** START OF THE PROJECT GUTENBERG EBOOK A TEST NOVEL ***

CONTENTS

Chapter I
Chapter II

DEDICATION

To nobody in particular.

CHAPTER I

""" + (
    "The morning came slowly over the water and the boatman waited. He had "
    "waited many mornings and had learned that waiting was most of the work. "
    "She came down the path with the letter in her hand and did not look at "
    "him. The river was high and brown after the rain and the reeds bent "
    "under it. He took the letter and put it in his coat without reading it. "
    "\"You will want payment,\" she said. \"Not today,\" he answered. They "
    "crossed in silence and the far bank rose grey before them, and neither "
    "spoke of the thing that had brought her out so early in the cold. "
) * 200 + """

*** END OF THE PROJECT GUTENBERG EBOOK A TEST NOVEL ***

This and all associated files of various formats will be found in
the Project Gutenberg licence appears here and must not be measured.
"""


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_openlibrary(tmp: Path):
    print("\nOpen Library dumps")
    works_path, editions_path = tmp / "works.txt.gz", tmp / "editions.txt.gz"
    write_dump(works_path, WORKS)
    write_dump(editions_path, EDITIONS)

    conn = db.connect(tmp / "test.db")
    db.init(conn)

    kept = openlibrary.ingest_works(conn, works_path)
    check("fiction works are kept", kept == 2, f"(kept {kept})")

    staged = {r["work_key"] for r in conn.execute("SELECT work_key FROM ol_works")}
    check("non-fiction is excluded", "/works/OL3W" not in staged)
    check("criticism about fiction is excluded", "/works/OL4W" not in staged)
    check("untitled works are excluded", "/works/OL5W" not in staged)
    check("untagged works are excluded", "/works/OL6W" not in staged)

    openlibrary.ingest_editions(conn, editions_path)
    row = conn.execute(
        "SELECT * FROM ol_works WHERE work_key = '/works/OL1W'"
    ).fetchone()

    check("the fuller edition wins", row["edition_key"] == "/books/OL11M",
          f"({row['edition_key']})")
    check("ISBN captured", row["isbn13"] == "9780141441146")
    check("page count captured", row["pages"] == 312)
    check("translator parsed from contributions",
          row["translator"] == "Ann Goldstein", f"({row['translator']})")

    salt = conn.execute(
        "SELECT * FROM ol_works WHERE work_key = '/works/OL2W'"
    ).fetchone()
    check("non-English editions are skipped",
          salt["publisher"] == "Independently published", f"({salt['publisher']})")
    check("ISBN-10 is converted to 13", salt["isbn13"].startswith("978"))

    added = openlibrary.promote(conn)
    check("both fiction works promoted", added == 2, f"({added})")

    books = {b.title: b for b in db.all_books(conn)}
    check("small press classified from the dump",
          books["The Measured Hour"].imprint["tier"] == "small_press")
    check("self-published classified from the dump",
          books["Salt and Iron"].imprint["tier"] == "self_published")
    check("cover URL built", "8899" in (books["The Measured Hour"].cover_url or ""))
    check("subjects carried across",
          "Psychological fiction" in books["The Measured Hour"].subjects)
    check("year falls back to the work when the edition lacks one",
          books["Salt and Iron"].year in (1888, 2019))

    # Idempotence: a second run must not duplicate anything.
    openlibrary.ingest_works(conn, works_path)
    openlibrary.ingest_editions(conn, editions_path)
    again = openlibrary.promote(conn)
    check("rerunning promotes nothing new", again == 0, f"({again})")
    check("no duplicate books", len(db.all_books(conn)) == 2)

    return conn, tmp


def test_ratings(conn, tmp: Path):
    print("\nRatings dump")
    path = tmp / "ratings.txt.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for value in (5, 4, 5, 4, 3, 5, 4, 4):
            handle.write(f"/works/OL1W\t/books/OL11M\t{value}\t2026-01-01\n")
        handle.write("/works/OL2W\t\t5\t2026-01-01\n")          # too few to count
        handle.write("/works/OL99W\t\t5\t2026-01-01\n")          # not in corpus
        handle.write("malformed line without tabs\n")

    updated = openlibrary.ingest_ratings(conn, path)
    check("well-rated book updated", updated == 1, f"({updated})")

    books = {b.title: b for b in db.all_books(conn)}
    score = books["The Measured Hour"].ratings.get("weighted_score")
    check("a rating was stored", score is not None)
    check("eight ratings are shrunk toward the prior",
          score is not None and 3.9 < score < 4.4, f"({score})")
    check("books below the threshold are untouched",
          not books["Salt and Iron"].ratings.get("weighted_score"))


def test_gutenberg(tmp: Path):
    print("\nGutenberg catalogue")
    catalog = tmp / "pg_catalog.csv"
    catalog.write_text(CATALOG, encoding="utf-8")

    entries = gutenberg.read_catalog(catalog)
    titles = {e["title"] for e in entries}

    check("prose fiction kept", "Pride and Prejudice" in titles)
    check("sea story kept", any("Moby Dick" in t for t in titles))
    check("plays excluded", "Romeo and Juliet" not in titles)
    check("poetry excluded", "The Raven" not in titles)
    check("engineering manual excluded", "Bridge Engineering Manual" not in titles)
    check("non-English excluded", "Les Miserables" not in titles)
    check("audiobooks excluded", "An Audiobook" not in titles)

    austen = next(e for e in entries if e["title"] == "Pride and Prejudice")
    check("author name reordered and dates stripped",
          austen["author"] == "Jane Austen", f"({austen['author']})")
    check("year parsed from Issued", austen["year"] == 1998)
    check("subjects cleaned", all("--" not in s for s in austen["subjects"]))

    print("\nGutenberg text handling")
    body = gutenberg.strip_boilerplate(SAMPLE_TEXT)
    check("licence header removed", "no restrictions whatsoever" not in body)
    check("licence footer removed", "must not be measured" not in body)
    check("narrative retained", "the boatman waited" in body)

    # Mirror path, which is how a serious run gets its texts.
    mirror = tmp / "mirror"
    (mirror / "cache" / "epub" / "1342").mkdir(parents=True)
    (mirror / "cache" / "epub" / "1342" / "pg1342.txt").write_text(
        SAMPLE_TEXT, encoding="utf-8"
    )
    check("mirror read succeeds",
          gutenberg.read_from_mirror(1342, mirror) is not None)
    check("mirror miss returns nothing",
          gutenberg.read_from_mirror(2701, mirror) is None)

    print("\nGutenberg ingestion")
    conn = db.connect(tmp / "gut.db")
    db.init(conn)
    report = gutenberg.ingest(conn, catalog, mirror=mirror, workers=1, cache_dir=None)

    check("one book measured", report["measured"] == 1, f"({report})")
    books = db.all_books(conn)
    check("book stored", len(books) == 1)

    book = books[0]
    check("no longer provisional", not book.provisional)
    check("gutenberg id recorded", book.gutenberg_id == 1342)
    check("real measurement, not a placeholder",
          book.style.get("words_analysed", 0) > 5000,
          f"({book.style.get('words_analysed')})")
    check("POV detected from real text", book.style.get("pov") == "third",
          f"({book.style.get('pov')})")
    check("tense detected from real text", book.style.get("tense") == "past",
          f"({book.style.get('tense')})")
    check("density computed", 0 < book.style.get("prose_density", 0) < 100)
    check("excerpt kept", book.excerpt and len(book.excerpt) > 200)
    check("excerpt licence recorded", "public domain" in (book.excerpt_licence or "").lower())

    rerun = gutenberg.ingest(conn, catalog, mirror=mirror, workers=1, cache_dir=None)
    check("measured books are not refetched",
          rerun["measured"] == 0 and rerun["already_done"] == 1, f"({rerun})")


def test_streaming(tmp: Path):
    """The parsers must not hold a dump in memory."""
    print("\nStreaming behaviour")
    import tracemalloc

    big = tmp / "big.txt.gz"
    records = []
    for i in range(20_000):
        records.append(("/type/work", {
            "key": f"/works/OL{i}W", "title": f"Book Number {i}",
            "subjects": ["Fiction", "Science fiction"],
            "description": "x" * 400,
        }))
    write_dump(big, records)
    on_disk = big.stat().st_size

    conn = db.connect(tmp / "stream.db")
    db.init(conn)

    tracemalloc.start()
    kept = openlibrary.ingest_works(conn, big)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    check("all records ingested", kept == 20_000, f"({kept})")
    check("peak memory stays modest",
          peak < 60 * 1024 * 1024,
          f"(peak {peak / 1024 / 1024:.1f}MB for a {on_disk / 1024 / 1024:.1f}MB file)")

    limited = openlibrary.ingest_works(conn, big, limit=100)
    check("limit stops the stream early", limited <= 100, f"({limited})")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        conn, tmp = test_openlibrary(tmp)
        test_ratings(conn, tmp)
        test_gutenberg(tmp)
        test_streaming(tmp)

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        sys.exit(1)
    print("All bulk checks passed.")
