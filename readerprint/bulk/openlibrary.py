"""
Open Library bulk ingestion.

Open Library asks that heavy users take the monthly dumps rather than hammer
the search API, which is also far faster: one streamed pass beats a hundred
thousand polite HTTP requests.

The files are large — roughly 9 GB compressed for editions, 3 GB for works,
expanding to several times that. Nothing here decompresses to disk or holds a
dump in memory. Each file is streamed line by line straight out of gzip, and
intermediate results go into a staging table so the process runs in a
near-constant few hundred megabytes regardless of dump size.

Three passes, in this order:

  1. works    — find fiction works, record title and subjects
  2. editions — pick the single best edition for each of those works
  3. promote  — turn staged rows into Book records

Works first because it is the smaller file, and it lets pass 2 discard the
overwhelming majority of editions immediately.
"""

from __future__ import annotations

import gzip
import io
import json
import time
import re
import sqlite3
from pathlib import Path

import requests

from .. import db
from ..imprint import classify, normalise_isbn
from ..models import Book
from .download import USER_AGENT, Progress, download

BASE = "https://openlibrary.org/data"
FILES = {
    "works": "ol_dump_works_latest.txt.gz",
    "editions": "ol_dump_editions_latest.txt.gz",
    "ratings": "ol_dump_ratings_latest.txt.gz",
    "authors": "ol_dump_authors_latest.txt.gz",
}

STAGING = """
CREATE TABLE IF NOT EXISTS ol_works (
    work_key TEXT PRIMARY KEY,
    title TEXT,
    subjects TEXT,
    first_year INTEGER,
    description TEXT,
    author_keys TEXT,
    -- best edition found so far
    edition_key TEXT,
    edition_score INTEGER DEFAULT -1,
    author TEXT,
    isbn13 TEXT,
    publisher TEXT,
    pages INTEGER,
    year INTEGER,
    cover INTEGER,
    translator TEXT,
    promoted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ol_promoted ON ol_works(promoted);

CREATE TABLE IF NOT EXISTS ol_authors (
    author_key TEXT PRIMARY KEY,
    name TEXT
);
"""

# A work counts as fiction if any subject matches. Open Library subjects are
# free text and inconsistent, so this is generous on purpose — a false
# positive costs one irrelevant book, a false negative loses it entirely.
FICTION_MARKERS = re.compile(
    r"\b(fiction|novel|novels|fantasy|science fiction|mystery|romance|thriller|"
    r"horror|literary|short stories|detective|adventure|gothic|dystopia|"
    r"speculative|crime|suspense|western|saga|fairy tales|graphic novel)\b",
    re.I,
)

# Subjects that mean the record is about fiction rather than being fiction.
NONFICTION_TRAP = re.compile(
    r"\b(criticism|history and criticism|bibliography|study and teaching|"
    r"biography|encyclopedias|dictionaries|textbooks|handbooks|study guides|"
    r"literary criticism|reader's guide|examinations)\b",
    re.I,
)

ENGLISH = {"/languages/eng"}


def open_staging(conn: sqlite3.Connection) -> None:
    conn.executescript(STAGING)

    # CREATE TABLE IF NOT EXISTS silently leaves an existing table alone, so
    # a database built by an earlier version keeps the old shape. Add any
    # column it is missing rather than making people rebuild.
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(ol_works)")
    }
    for column, definition in (("author_keys", "TEXT"),):
        if column not in existing:
            conn.execute(f"ALTER TABLE ol_works ADD COLUMN {column} {definition}")

    conn.commit()


def dump_lines(path: Path, label: str, limit: int | None = None):
    """
    Yield the JSON payload of each dump row.

    Rows are tab separated as type, key, revision, last_modified, JSON. Only
    the last column is of any use here, and a handful of rows in every dump
    are malformed, so bad lines are skipped rather than allowed to end a
    six-hour run.
    """
    size = path.stat().st_size
    bar = Progress(label, total=size, unit="B")
    seen = 0

    # Progress is tracked against the compressed file position rather than
    # decompressed bytes read. Estimating from line lengths overshoots badly
    # on repetitive JSON, which compresses far better than plain text.
    with open(path, "rb") as raw:
        handle = io.TextIOWrapper(
            gzip.GzipFile(fileobj=raw), encoding="utf-8", errors="replace"
        )
        for line in handle:
            bar.count = raw.tell()
            bar.advance(0)
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            try:
                record = json.loads(parts[4])
            except json.JSONDecodeError:
                continue
            yield record
            seen += 1
            if limit and seen >= limit:
                break

    bar.count = min(bar.count, size)
    bar.close(f"({seen:,} records parsed)")


def _year_from(value) -> int | None:
    if not value:
        return None
    match = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", str(value))
    return int(match.group(1)) if match else None


def _text_of(value) -> str | None:
    """Descriptions arrive either as a plain string or as a typed object."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("value")
    return None


# --------------------------------------------------------------------------
# Pass 1: works
# --------------------------------------------------------------------------

def ingest_works(conn, path: Path, limit: int | None = None) -> int:
    open_staging(conn)
    kept = 0
    batch = []

    for record in dump_lines(path, "works", limit):
        key = record.get("key")
        title = (record.get("title") or "").strip()
        if not key or not title:
            continue

        subjects = [s for s in (record.get("subjects") or []) if isinstance(s, str)]
        blob = " ; ".join(subjects)
        if not FICTION_MARKERS.search(blob) or NONFICTION_TRAP.search(blob):
            continue

        # Works reference authors by key, not by name. The names live in a
        # separate dump, so keys are stored here and resolved at promote
        # time. Skipping this step was the bug that produced a corpus of
        # books by "Unknown".
        author_keys = []
        for entry in record.get("authors") or []:
            if isinstance(entry, dict):
                ref = entry.get("author")
                if isinstance(ref, dict) and ref.get("key"):
                    author_keys.append(ref["key"])
                elif isinstance(ref, str):
                    author_keys.append(ref)

        batch.append((
            key, title, json.dumps(subjects[:25]),
            _year_from(record.get("first_publish_date")),
            (_text_of(record.get("description")) or "")[:2000] or None,
            json.dumps(author_keys[:3]),
        ))
        kept += 1

        if len(batch) >= 5000:
            _flush_works(conn, batch)
            batch.clear()

    if batch:
        _flush_works(conn, batch)
    return kept


def _flush_works(conn, batch) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO ol_works "
        "(work_key, title, subjects, first_year, description, author_keys) "
        "VALUES (?,?,?,?,?,?)",
        batch,
    )
    conn.commit()


# --------------------------------------------------------------------------
# Pass 2: editions
# --------------------------------------------------------------------------

def _score_edition(record: dict) -> int:
    """
    How complete an edition record is.

    Works have many editions and most are sparse. Rather than take the first,
    keep whichever carries the most usable metadata — an edition with an
    ISBN, a page count and a cover is worth far more downstream than a bare
    reprint with a title.
    """
    score = 0
    if record.get("isbn_13") or record.get("isbn_10"):
        score += 4
    if record.get("number_of_pages"):
        score += 3
    if record.get("publishers"):
        score += 2
    if record.get("covers"):
        score += 2
    if record.get("publish_date"):
        score += 1
    if record.get("description"):
        score += 1
    if record.get("contributions"):
        score += 1
    return score


def _isbn_of(record: dict) -> str | None:
    for field in ("isbn_13", "isbn_10"):
        for raw in record.get(field) or []:
            normalised = normalise_isbn(raw)
            if normalised:
                return normalised
    return None


def _translator_of(record: dict) -> str | None:
    for entry in record.get("contributions") or []:
        if isinstance(entry, str) and "translat" in entry.lower():
            return re.sub(r"\s*\(?[Tt]ranslat\w*\)?", "", entry).strip(" ,")
    if record.get("translated_from"):
        return "Translator not named"
    return None


def ingest_editions(conn, path: Path, limit: int | None = None) -> int:
    open_staging(conn)
    matched = 0
    batch = []

    for record in dump_lines(path, "editions", limit):
        works = record.get("works") or []
        if not works:
            continue
        work_key = (works[0] or {}).get("key")
        if not work_key:
            continue

        languages = {l.get("key") for l in (record.get("languages") or []) if isinstance(l, dict)}
        if languages and not (languages & ENGLISH):
            continue

        score = _score_edition(record)
        publishers = record.get("publishers") or []
        covers = record.get("covers") or []

        batch.append((
            record.get("key"), score,
            _isbn_of(record),
            publishers[0] if publishers else None,
            record.get("number_of_pages"),
            _year_from(record.get("publish_date")),
            covers[0] if covers else None,
            _translator_of(record),
            (_text_of(record.get("description")) or "")[:2000] or None,
            work_key, score,
        ))
        matched += 1

        if len(batch) >= 5000:
            _flush_editions(conn, batch)
            batch.clear()

    if batch:
        _flush_editions(conn, batch)
    return matched


def _flush_editions(conn, batch) -> None:
    # Only overwrite when this edition beats what is already stored, and only
    # for works pass 1 kept. Editions of non-fiction works update nothing.
    conn.executemany(
        """
        UPDATE ol_works SET
            edition_key = ?, edition_score = ?,
            isbn13 = COALESCE(?, isbn13),
            publisher = COALESCE(?, publisher),
            pages = COALESCE(?, pages),
            year = COALESCE(?, year),
            cover = COALESCE(?, cover),
            translator = COALESCE(?, translator),
            description = COALESCE(description, ?)
        WHERE work_key = ? AND edition_score < ?
        """,
        batch,
    )
    conn.commit()


# --------------------------------------------------------------------------
# Pass 3: promote into the book table
# --------------------------------------------------------------------------

def ingest_authors(conn, path: Path, limit: int | None = None) -> int:
    open_staging(conn)
    batch, kept = [], 0
    for record in dump_lines(path, "authors", limit):
        key, name = record.get("key"), record.get("name")
        if key and name:
            batch.append((key, name))
            kept += 1
        if len(batch) >= 5000:
            conn.executemany("INSERT OR IGNORE INTO ol_authors VALUES (?,?)", batch)
            conn.commit()
            batch.clear()
    if batch:
        conn.executemany("INSERT OR IGNORE INTO ol_authors VALUES (?,?)", batch)
        conn.commit()
    return kept


def resolve_authors(conn, keys_json: str | None) -> str:
    """Turn stored author keys into a display name."""
    if not keys_json:
        return ""
    try:
        keys = json.loads(keys_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    names = []
    for key in keys[:2]:
        row = conn.execute(
            "SELECT name FROM ol_authors WHERE author_key = ?", (key,)
        ).fetchone()
        if row and row["name"]:
            names.append(row["name"].strip())
    return ", ".join(names)


# Titles in the dump sometimes carry the author, a series note, or an edition
# label appended after a dash or bracket. Left in place they break author
# matching and look wrong on a card.
TITLE_NOISE = re.compile(
    r"\s*[-–—]\s*(by\s+)?[A-Z][a-z]+(\s+[A-Z][a-z.]+){1,3}\s*$|"
    r"\s*[\(\[][^)\]]*(edition|annotated|illustrated|unabridged|classics|"
    r"reprint|translated|volume|vol\.?|complete)[^)\]]*[\)\]]\s*$",
    re.I,
)


def clean_title(title: str, author: str = "") -> str:
    cleaned = TITLE_NOISE.sub("", title).strip(" -–—:;,")
    # Only accept the trim if something substantial survives.
    if len(cleaned) < 3:
        return title.strip()
    if author:
        surname = author.split()[-1].lower() if author.split() else ""
        if surname and cleaned.lower().endswith(surname):
            trimmed = cleaned[: -len(surname)].strip(" -–—:;,by")
            if len(trimmed) >= 3:
                cleaned = trimmed
    return cleaned


def promote(conn, min_score: int = 1, limit: int | None = None,
            require_author: bool = True) -> int:
    """
    Turn staged rows into Book records.

    Rows with no usable edition are left staged rather than promoted — a
    title with no ISBN, publisher, page count or cover adds noise to the
    recommender and gives the reader nothing to act on. The same now applies
    to rows with no resolvable author: a shelf full of books by "Unknown" is
    worse than a smaller shelf.
    """
    open_staging(conn)

    have_authors = conn.execute(
        "SELECT COUNT(*) AS n FROM ol_authors"
    ).fetchone()["n"]
    if require_author and not have_authors:
        print("  no author names loaded — run the authors stage first, or")
        print("  pass require_author=False to promote without them")
        return 0

    query = (
        "SELECT * FROM ol_works WHERE promoted = 0 AND edition_score >= ? "
        "ORDER BY edition_score DESC"
    )
    params: list = [min_score]
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()
    bar = Progress("promoting", total=len(rows))
    added = skipped = 0

    for row in rows:
        bar.advance()
        author = resolve_authors(conn, row["author_keys"])
        if require_author and not author:
            # Leave it staged. If the authors dump is loaded later, a rerun
            # will pick it up rather than having discarded it.
            skipped += 1
            continue

        try:
            subjects = json.loads(row["subjects"] or "[]")
        except (json.JSONDecodeError, TypeError):
            subjects = []

        book = Book(
            title=clean_title(row["title"], author),
            author=author,
            year=row["year"] or row["first_year"],
            isbn13=row["isbn13"],
            publisher=row["publisher"],
            page_count=row["pages"],
            subjects=subjects,
            description=row["description"],
            translator=row["translator"],
            openlibrary_key=row["work_key"],
            provisional=True,
        )
        if row["cover"]:
            book.cover_url = f"https://covers.openlibrary.org/b/id/{row['cover']}-M.jpg"

        book.imprint = classify(
            book.publisher, book.isbn13, book.title, book.author
        ).as_dict()

        db.upsert_book(conn, book)
        conn.execute(
            "UPDATE ol_works SET promoted = 1 WHERE work_key = ?", (row["work_key"],)
        )
        added += 1

        if added % 2000 == 0:
            conn.commit()

    conn.commit()
    note = f"({added:,} added"
    if skipped:
        note += f", {skipped:,} held back for missing authors"
    bar.close(note + ")")
    return added


def backfill_authors_via_api(conn, min_gap: float = 0.4) -> int:
    """
    Fetch missing author names one work at a time from the API.

    The authors dump is around 780 MB. Downloading all of it to name a few
    hundred books is the wrong trade by a wide margin — a few hundred small
    requests finish in minutes over a connection that would need hours for
    the dump, and they work even when the staging tables are missing or were
    written by an older version that never stored author keys.

    The dump remains the right tool above a few thousand missing books,
    which is what stage_repair decides between.
    """
    rows = conn.execute(
        "SELECT id, title, openlibrary_key FROM books "
        "WHERE (author IS NULL OR author = '') AND openlibrary_key IS NOT NULL"
    ).fetchall()
    if not rows:
        return 0

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    name_cache: dict[str, str] = {}
    last = [0.0]

    def polite_get(url: str):
        wait = min_gap - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
        try:
            response = session.get(url, timeout=30)
        except requests.RequestException:
            return None
        return response.json() if response.status_code == 200 else None

    def author_name(key: str) -> str | None:
        if key in name_cache:
            return name_cache[key]
        data = polite_get(f"https://openlibrary.org{key}.json")
        name = (data or {}).get("name")
        if name:
            name_cache[key] = name
        return name

    bar = Progress("resolving", total=len(rows))
    fixed = 0

    for row in rows:
        bar.advance()
        work = polite_get(f"https://openlibrary.org{row['openlibrary_key']}.json")
        if not work:
            continue

        names = []
        for entry in (work.get("authors") or [])[:2]:
            ref = entry.get("author") if isinstance(entry, dict) else None
            key = ref.get("key") if isinstance(ref, dict) else (
                ref if isinstance(ref, str) else None
            )
            if key:
                name = author_name(key)
                if name:
                    names.append(name.strip())

        if not names:
            continue

        author = ", ".join(names)
        conn.execute(
            "UPDATE books SET author = ?, title = ? WHERE id = ?",
            (author, clean_title(row["title"], author), row["id"]),
        )
        fixed += 1
        if fixed % 25 == 0:
            conn.commit()

    conn.commit()
    bar.close(f"({fixed:,} authors resolved)")
    return fixed


def backfill_authors(conn) -> int:
    """
    Repair books already promoted without an author.

    Needed because an earlier version of this pipeline never loaded the
    authors dump, so an existing corpus can be full of books by "Unknown"
    with no way to fix them short of rebuilding.
    """
    open_staging(conn)
    rows = conn.execute(
        "SELECT b.id, b.title, w.author_keys FROM books b "
        "JOIN ol_works w ON w.work_key = b.openlibrary_key "
        "WHERE (b.author IS NULL OR b.author = '')"
    ).fetchall()

    if not rows:
        return 0

    bar = Progress("backfill", total=len(rows))
    fixed = 0
    for row in rows:
        bar.advance()
        author = resolve_authors(conn, row["author_keys"])
        if not author:
            continue
        conn.execute(
            "UPDATE books SET author = ?, title = ? WHERE id = ?",
            (author, clean_title(row["title"], author), row["id"]),
        )
        fixed += 1
        if fixed % 2000 == 0:
            conn.commit()

    conn.commit()
    bar.close(f"({fixed:,} authors restored)")
    return fixed


# --------------------------------------------------------------------------
# Ratings
# --------------------------------------------------------------------------

def ingest_ratings(conn, path: Path) -> int:
    """
    Fold the ratings dump into books already in the corpus.

    Columns are work key, edition key, rating, date. Ratings are aggregated
    per work and then run through the same shrinkage as any other source, so
    a work with nine ratings does not outrank one with nine thousand.
    """
    from ..reviews import summarise_ratings

    open_staging(conn)
    keys = {
        row["openlibrary_key"]: row["id"]
        for row in conn.execute(
            "SELECT id, openlibrary_key FROM books WHERE openlibrary_key IS NOT NULL"
        )
    }
    if not keys:
        print("  no Open Library keys in the corpus yet — run the openlibrary stage first")
        return 0

    totals: dict[str, list[float]] = {}
    size = path.stat().st_size
    bar = Progress("ratings", total=size, unit="B")

    with open(path, "rb") as raw:
        handle = io.TextIOWrapper(
            gzip.GzipFile(fileobj=raw), encoding="utf-8", errors="replace"
        )
        for line in handle:
            bar.count = raw.tell()
            bar.advance(0)
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            work_key, rating = parts[0], parts[2]
            if work_key not in keys:
                continue
            try:
                totals.setdefault(work_key, []).append(float(rating))
            except ValueError:
                continue

    bar.close(f"({len(totals):,} rated works matched)")

    updated = 0
    for work_key, values in totals.items():
        if len(values) < 3:
            continue
        book = db.get_book(conn, keys[work_key])
        if not book:
            continue
        summary = summarise_ratings(
            {"openlibrary": {"mean": sum(values) / len(values), "count": len(values)}}
        )
        book.ratings = summary.as_dict()
        db.upsert_book(conn, book)
        updated += 1

    conn.commit()
    return updated


def fetch_dump(name: str, data_dir: Path, force: bool = False) -> Path:
    filename = FILES[name]
    return download(f"{BASE}/{filename}", data_dir / filename, force=force)