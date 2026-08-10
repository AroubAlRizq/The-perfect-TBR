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
        "key": "/works/OL1W", "title": "The Measured Hour - Elena Vance",
        "authors": [{"author": {"key": "/authors/OL1A"},
                     "type": {"key": "/type/author_role"}}],
        "subjects": ["Fiction", "Psychological fiction", "London"],
        "first_publish_date": "1961",
        "description": {"type": "/type/text", "value": "A novel about time."},
    }),
    ("/type/work", {
        "key": "/works/OL2W", "title": "Salt and Iron",
        "authors": [{"author": {"key": "/authors/OL2A"}}],
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

AUTHORS = [
    ("/type/author", {"key": "/authors/OL1A", "name": "Elena Vance"}),
    ("/type/author", {"key": "/authors/OL2A", "name": "Tomas Reyes"}),
    ("/type/author", {"key": "/authors/OL9A", "name": "Nobody Relevant"}),
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

    # Reproduces the reported failure: promoting before author names exist.
    premature = openlibrary.promote(conn)
    check("promote refuses to run without author names", premature == 0,
          f"({premature})")

    authors_path = tmp / "authors.txt.gz"
    write_dump(authors_path, AUTHORS)
    names = openlibrary.ingest_authors(conn, authors_path)
    check("author names loaded", names == 3, f"({names})")

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
    check("no book is left without an author",
          all(b.author for b in db.all_books(conn)),
          f"({[b.title for b in db.all_books(conn) if not b.author]})")
    check("author resolved from the authors dump",
          books["The Measured Hour"].author == "Elena Vance",
          f"({books['The Measured Hour'].author})")
    check("author name stripped from the title",
          "Elena Vance" not in books["The Measured Hour"].title,
          f"({books['The Measured Hour'].title})")
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


def test_author_repair(tmp: Path):
    """The reported bug: a corpus promoted before author names were loaded."""
    print("\nAuthor repair")
    works_path, editions_path = tmp / "rw.txt.gz", tmp / "re.txt.gz"
    write_dump(works_path, WORKS)
    write_dump(editions_path, EDITIONS)

    conn = db.connect(tmp / "repair.db")
    db.init(conn)
    openlibrary.ingest_works(conn, works_path)
    openlibrary.ingest_editions(conn, editions_path)

    # Promote the old, broken way — no authors loaded.
    added = openlibrary.promote(conn, require_author=False)
    check("books promoted without authors", added == 2, f"({added})")
    check("they are indeed nameless",
          all(not b.author for b in db.all_books(conn)))

    # Now repair, exactly as `build.py --stages repair` does.
    authors_path = tmp / "ra.txt.gz"
    write_dump(authors_path, AUTHORS)
    openlibrary.ingest_authors(conn, authors_path)
    fixed = openlibrary.backfill_authors(conn)

    check("backfill repairs them", fixed == 2, f"({fixed})")
    books = {b.title: b for b in db.all_books(conn)}
    check("author restored in place",
          books["The Measured Hour"].author == "Elena Vance")
    check("title cleaned during repair",
          "Elena Vance" not in books["The Measured Hour"].title)
    check("repair is idempotent", openlibrary.backfill_authors(conn) == 0)


def test_provisional_ranking(tmp: Path):
    """Unmeasured books must not bury measured ones."""
    print("\nProvisional ranking")
    from readerprint.models import Book, ReadingEvent
    from readerprint.recommend import build_profile, build_space, recommend

    measured = [
        Book(title=f"Measured {i}", author=f"Author {i}", page_count=300,
             subjects=["gothic", "literary"], provisional=False,
             style={"prose_density": 65 + i, "cliche_rate": 1.0, "pov": "third",
                    "tense": "past", "mean_sentence_length": 20,
                    "dialogue_share": 0.2, "ornament_index": 45})
        for i in range(5)
    ]
    # A flood of metadata-only books, as a large Open Library import produces.
    flood = [
        Book(title=f"Provisional {i}", author=f"Someone {i}", page_count=300,
             subjects=["gothic", "literary"], provisional=True, style={})
        for i in range(200)
    ]

    space = build_space(measured + flood)
    events = [ReadingEvent(book_id=b.id, verdict="loved") for b in measured[:3]]
    profile = build_profile(space, events)

    results = recommend(space, profile, {e.book_id for e in events}, limit=10)
    top_measured = sum(1 for r in results[:5] if not r.book.provisional)
    check("measured books are not buried by the flood", top_measured >= 1,
          f"({top_measured} of the top 5)")
    check("unmeasured books carry a caution",
          all(any("not measured" in c for c in r.cautions)
              for r in results if r.book.provisional))

    only = recommend(space, profile, {e.book_id for e in events},
                     limit=10, measured_only=True)
    check("measured_only filters the rest out",
          only and all(not r.book.provisional for r in only), f"({len(only)})")
    check("measured_only respects what is left", len(only) == 2, f"({len(only)})")


def test_download_retry(tmp: Path):
    """A dropped connection must resume, not restart."""
    print("\nDownload resilience")
    import requests
    from readerprint.bulk import download as dl

    payload = b"x" * 60_000
    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, body, status=200, total=None, start=0):
            self.status_code = status
            self._body = body
            self.headers = {"Content-Length": str(total if total is not None else len(body))}
            self._start = start

        def raise_for_status(self):
            if self.status_code >= 400:
                error = requests.exceptions.HTTPError(f"{self.status_code}")
                error.response = self
                raise error

        def iter_content(self, size):
            # First call dies a third of the way through.
            if calls["n"] == 1:
                yield self._body[: len(self._body) // 3]
                raise requests.exceptions.ChunkedEncodingError("connection broken")
            yield self._body

    def fake_get(url, headers=None, stream=False, timeout=None):
        calls["n"] += 1
        start = 0
        status = 200
        if headers and "Range" in headers:
            start = int(headers["Range"].split("=")[1].split("-")[0])
            # A server honouring a range request answers 206, not 200. A 200
            # to a ranged request means the range was ignored and the body is
            # the whole file, which download() handles by restarting.
            status = 206
        return FakeResponse(payload[start:], status=status,
                            total=len(payload) - start, start=start)

    original, dl.time.sleep = requests.get, lambda _: None
    requests.get = fake_get
    try:
        target = tmp / "retry.bin"
        result = dl.download("https://example.invalid/f.bin", target, attempts=4)
        check("download completes despite a dropped connection", result.exists())
        check("resumed rather than restarted", calls["n"] == 2, f"({calls['n']} requests)")
        check("bytes are intact", result.read_bytes() == payload,
              f"({result.stat().st_size} of {len(payload)})")
        check("no .part file left behind",
              not target.with_suffix(target.suffix + ".part").exists())
    finally:
        requests.get = original

    # A 404 should fail immediately rather than retrying eight times.
    calls["n"] = 0

    def not_found(url, headers=None, stream=False, timeout=None):
        calls["n"] += 1
        return FakeResponse(b"", status=404)

    requests.get = not_found
    try:
        try:
            dl.download("https://example.invalid/missing", tmp / "missing.bin", attempts=4)
            check("client errors are not retried", False, "(no error raised)")
        except requests.exceptions.HTTPError:
            check("client errors are not retried", calls["n"] == 1, f"({calls['n']})")
    finally:
        requests.get = original


def test_schema_migration(tmp: Path):
    """A staging table from an older version must gain the new column."""
    print("\nSchema migration")
    conn = db.connect(tmp / "old.db")
    db.init(conn)

    # The pre-fix schema, without author_keys.
    conn.execute("""
        CREATE TABLE ol_works (
            work_key TEXT PRIMARY KEY, title TEXT, subjects TEXT,
            first_year INTEGER, description TEXT, edition_key TEXT,
            edition_score INTEGER DEFAULT -1, author TEXT, isbn13 TEXT,
            publisher TEXT, pages INTEGER, year INTEGER, cover INTEGER,
            translator TEXT, promoted INTEGER DEFAULT 0
        )""")
    conn.execute("INSERT INTO ol_works (work_key, title) VALUES ('/works/OLXW', 'Old Row')")
    conn.commit()

    before = {r["name"] for r in conn.execute("PRAGMA table_info(ol_works)")}
    check("old table lacks author_keys", "author_keys" not in before)

    openlibrary.open_staging(conn)
    after = {r["name"] for r in conn.execute("PRAGMA table_info(ol_works)")}
    check("migration adds the column", "author_keys" in after)
    check("existing rows survive",
          conn.execute("SELECT COUNT(*) AS n FROM ol_works").fetchone()["n"] == 1)

    openlibrary.open_staging(conn)
    check("migration is idempotent",
          len({r["name"] for r in conn.execute("PRAGMA table_info(ol_works)")}) == len(after))


def test_api_backfill(tmp: Path):
    """Resolving authors from the API, the route a small repair takes."""
    print("\nAPI author backfill")
    import requests
    from readerprint.models import Book

    conn = db.connect(tmp / "api.db")
    db.init(conn)
    for i in range(3):
        db.upsert_book(conn, Book(
            title=f"Nameless {i} - Iris Wren", author="",
            openlibrary_key=f"/works/OL{i}W", provisional=True,
        ))
    db.upsert_book(conn, Book(title="Has One", author="Already Named",
                              openlibrary_key="/works/OL9W"))

    responses = {
        **{f"https://openlibrary.org/works/OL{i}W.json":
           {"authors": [{"author": {"key": "/authors/OLAA"}}]} for i in range(3)},
        "https://openlibrary.org/authors/OLAA.json": {"name": "Iris Wren"},
    }
    hits = {"n": 0}

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200 if payload else 404
            self._payload = payload

        def json(self):
            return self._payload

    class FakeSession:
        headers = {}

        def get(self, url, timeout=None):
            hits["n"] += 1
            return FakeResponse(responses.get(url))

    original = requests.Session
    requests.Session = FakeSession
    try:
        fixed = openlibrary.backfill_authors_via_api(conn, min_gap=0)
    finally:
        requests.Session = original

    check("all nameless books resolved", fixed == 3, f"({fixed})")
    books = {b.title: b for b in db.all_books(conn)}
    check("author written", any(b.author == "Iris Wren" for b in books.values()))
    check("author name stripped from title",
          all("Iris Wren" not in t for t in books if t != "Has One"),
          f"({list(books)})")
    check("author names are cached, not refetched",
          hits["n"] == 4, f"({hits['n']} requests for 3 books sharing one author)")
    check("books that already had an author are untouched",
          books["Has One"].author == "Already Named")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        conn, tmp = test_openlibrary(tmp)
        test_ratings(conn, tmp)
        test_gutenberg(tmp)
        test_streaming(tmp)
        test_author_repair(tmp)
        test_provisional_ranking(tmp)
        test_download_retry(tmp)
        test_schema_migration(tmp)
        test_api_backfill(tmp)

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        sys.exit(1)
    print("All bulk checks passed.")