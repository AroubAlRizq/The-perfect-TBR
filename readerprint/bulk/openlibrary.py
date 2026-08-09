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
import re
import sqlite3
from pathlib import Path

from .. import db
from ..imprint import classify, normalise_isbn
from ..models import Book
from .download import Progress, download

BASE = "https://openlibrary.org/data"
FILES = {
    "works": "ol_dump_works_latest.txt.gz",
    "editions": "ol_dump_editions_latest.txt.gz",
    "ratings": "ol_dump_ratings_latest.txt.gz",
}

STAGING = """
CREATE TABLE IF NOT EXISTS ol_works (
    work_key TEXT PRIMARY KEY,
    title TEXT,
    subjects TEXT,
    first_year INTEGER,
    description TEXT,
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

        batch.append((
            key, title, json.dumps(subjects[:25]),
            _year_from(record.get("first_publish_date")),
            (_text_of(record.get("description")) or "")[:2000] or None,
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
        "(work_key, title, subjects, first_year, description) VALUES (?,?,?,?,?)",
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


def promote(conn, min_score: int = 1, limit: int | None = None) -> int:
    """
    Turn staged rows into Book records.

    Rows with no usable edition are left staged rather than promoted — a
    title with no ISBN, publisher, page count or cover adds noise to the
    recommender and gives the reader nothing to act on.
    """
    open_staging(conn)
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
    added = 0

    for row in rows:
        try:
            subjects = json.loads(row["subjects"] or "[]")
        except (json.JSONDecodeError, TypeError):
            subjects = []

        book = Book(
            title=row["title"],
            author=row["author"] or "",
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
        bar.advance()

        if added % 2000 == 0:
            conn.commit()

    conn.commit()
    bar.close(f"({added:,} books added)")
    return added


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
