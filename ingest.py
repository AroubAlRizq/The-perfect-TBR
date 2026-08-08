"""
Reading history import.

The hardest problem for a recommender built on a whole reading profile is that
nobody wants to type in two hundred books. Everyone who reads seriously
already has that list somewhere, and Goodreads and StoryGraph both hand it
over as a CSV on request. One upload and the profile is populated.

The importer is deliberately forgiving about column names, because both
exports have changed shape over the years and a friend's five-year-old export
should still work.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from .imprint import normalise_isbn
from .models import Book, ReadingEvent, new_id

# Column aliases, lowercased and stripped of punctuation for matching.
COLUMN_ALIASES = {
    "title": ["title", "book title"],
    "author": ["author", "authors", "primary author", "author l f"],
    "isbn13": ["isbn13", "isbn 13"],
    "isbn": ["isbn", "isbn10"],
    "rating": ["my rating", "rating", "star rating"],
    "shelf": ["exclusive shelf", "read status", "status"],
    "shelves": ["bookshelves", "tags", "shelves"],
    "date_read": ["date read", "last date read", "read date"],
    "publisher": ["publisher"],
    "pages": ["number of pages", "pages", "page count"],
    "year": ["year published", "original publication year", "publication year"],
    "review": ["my review", "review"],
    "format": ["binding", "format"],
}

DNF_SHELF_MARKERS = {
    "dnf", "did-not-finish", "did not finish", "abandoned", "unfinished",
    "gave-up", "quit", "put-down", "dnfed", "didnt-finish",
}


def _normalise_header(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).strip()


def _build_column_map(fieldnames: list[str]) -> dict:
    lookup = {_normalise_header(f): f for f in fieldnames if f}
    mapping = {}
    for key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                mapping[key] = lookup[alias]
                break
    return mapping


def _clean_isbn(raw: str | None) -> str | None:
    """Goodreads wraps ISBNs as ="0439023483" to stop Excel eating them."""
    if not raw:
        return None
    stripped = raw.strip().strip('="').strip('"')
    return normalise_isbn(stripped)


def _parse_date(raw: str | None) -> date | None:
    if not raw or not raw.strip():
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _verdict_from(rating: float | None, shelf: str, shelves: str) -> tuple[str, list[str]]:
    """
    Map an export row onto a verdict.

    Goodreads does not record abandonment, so DNF has to be inferred from
    custom shelf names. Where it can be inferred, the reason is left empty and
    the interface asks — an unexplained DNF is worth much less than one with a
    cause attached.
    """
    combined = f"{shelf} {shelves}".lower()
    is_dnf = any(marker in combined for marker in DNF_SHELF_MARKERS)

    if is_dnf:
        return "dnf", []

    if rating is None or rating == 0:
        return "unrated", []
    if rating >= 5:
        return "loved", []
    if rating >= 4:
        return "liked", []
    if rating >= 3:
        return "fine", []
    return "disliked", []


def parse_export(content: str | bytes) -> dict:
    """
    Read a CSV export into books and events.

    Returns books, events, and a report. Nothing touches the database here —
    the caller decides what to keep, so a bad file cannot corrupt a profile.
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return {"books": [], "events": [], "report": {"error": "No columns found."}}

    columns = _build_column_map(reader.fieldnames)
    if "title" not in columns:
        return {
            "books": [],
            "events": [],
            "report": {
                "error": "No title column. Expected a Goodreads or StoryGraph export."
            },
        }

    books: list[Book] = []
    events: list[ReadingEvent] = []
    report = {
        "rows": 0, "imported": 0, "skipped_to_read": 0,
        "skipped_unrated": 0, "dnf_found": 0, "malformed": 0,
    }

    def value(row: dict, key: str) -> str:
        column = columns.get(key)
        return (row.get(column) or "").strip() if column else ""

    for row in reader:
        report["rows"] += 1
        title = value(row, "title")
        if not title:
            report["malformed"] += 1
            continue

        shelf = value(row, "shelf").lower()
        shelves = value(row, "shelves").lower()

        if "to-read" in shelf or shelf == "to read":
            report["skipped_to_read"] += 1
            continue
        if "currently" in shelf:
            report["skipped_to_read"] += 1
            continue

        try:
            rating = float(value(row, "rating") or 0)
        except ValueError:
            rating = 0.0

        verdict, reasons = _verdict_from(rating, shelf, shelves)
        if verdict == "unrated":
            report["skipped_unrated"] += 1
            continue
        if verdict == "dnf":
            report["dnf_found"] += 1

        author = value(row, "author")
        # "Le Guin, Ursula K." from the l-f column reads better reversed.
        if "," in author and columns.get("author", "").lower().endswith("l f"):
            surname, _, forename = author.partition(",")
            author = f"{forename.strip()} {surname.strip()}".strip()

        try:
            pages = int(float(value(row, "pages") or 0)) or None
        except ValueError:
            pages = None
        try:
            year = int(float(value(row, "year") or 0)) or None
        except ValueError:
            year = None

        book = Book(
            title=title,
            author=author,
            isbn13=_clean_isbn(value(row, "isbn13")) or _clean_isbn(value(row, "isbn")),
            publisher=value(row, "publisher") or None,
            page_count=pages,
            year=year,
            provisional=True,
        )

        event = ReadingEvent(
            id=new_id(),
            book_id=book.id,
            verdict=verdict,
            rating=rating or None,
            dnf_reasons=reasons,
            finished_on=_parse_date(value(row, "date_read")),
            note=value(row, "review")[:2000] or None,
        )

        books.append(book)
        events.append(event)
        report["imported"] += 1

    report["needs_dnf_reasons"] = report["dnf_found"]
    return {"books": books, "events": events, "report": report}


def import_into(conn, parsed: dict, user_id: str = "local") -> dict:
    """Persist a parsed export, reusing existing book records where they match."""
    from . import db

    matched, created = 0, 0
    for book, event in zip(parsed["books"], parsed["events"]):
        existing = None
        if book.isbn13:
            row = conn.execute(
                "SELECT * FROM books WHERE isbn13 = ?", (book.isbn13,)
            ).fetchone()
            if row:
                existing = db._row_to_book(row)
        if existing is None:
            existing = db.find_book(conn, book.title, book.author)

        if existing:
            event.book_id = existing.id
            matched += 1
        else:
            db.upsert_book(conn, book)
            event.book_id = book.id
            created += 1

        event.user_id = user_id
        db.upsert_event(conn, event)

    report = dict(parsed["report"])
    report["matched_existing"] = matched
    report["added_new"] = created
    return report
