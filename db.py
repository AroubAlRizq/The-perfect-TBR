"""
SQLite storage.

One file, no server, no migrations framework. A pilot shared with friends
should be runnable by anyone who can clone a repo and type one command.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from .models import Book, ReadingEvent

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "readerprint.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    year INTEGER,
    isbn13 TEXT,
    publisher TEXT,
    page_count INTEGER,
    word_count INTEGER,
    language TEXT,
    original_language TEXT,
    translator TEXT,
    series TEXT,
    series_position REAL,
    subjects TEXT,
    description TEXT,
    cover_url TEXT,
    excerpt TEXT,
    excerpt_source TEXT,
    excerpt_licence TEXT,
    style TEXT,
    imprint TEXT,
    ratings TEXT,
    content_flags TEXT,
    openlibrary_key TEXT,
    google_books_id TEXT,
    gutenberg_id INTEGER,
    provisional INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);
CREATE UNIQUE INDEX IF NOT EXISTS idx_books_isbn ON books(isbn13)
    WHERE isbn13 IS NOT NULL;

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    rating REAL,
    dnf_reasons TEXT,
    dnf_point INTEGER,
    finished_on TEXT,
    note TEXT,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_user_book ON events(user_id, book_id);

CREATE TABLE IF NOT EXISTS review_cache (
    book_id TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT,
    fetched_on TEXT,
    PRIMARY KEY (book_id, source)
);
"""

JSON_FIELDS = {"subjects", "style", "imprint", "ratings", "content_flags"}
LIST_FIELDS = {"subjects", "content_flags"}


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path else DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------
# Books
# --------------------------------------------------------------------------

def _book_to_row(book: Book) -> dict:
    row = book.as_dict()
    for f in JSON_FIELDS:
        row[f] = json.dumps(row.get(f) or ([] if f in LIST_FIELDS else {}))
    row["provisional"] = 1 if book.provisional else 0
    return row


def _row_to_book(row: sqlite3.Row) -> Book:
    data = dict(row)
    for f in JSON_FIELDS:
        try:
            data[f] = json.loads(data.get(f) or ("[]" if f in LIST_FIELDS else "{}"))
        except (json.JSONDecodeError, TypeError):
            data[f] = [] if f in LIST_FIELDS else {}
    data["provisional"] = bool(data.get("provisional", 1))
    return Book(**data)


def upsert_book(conn: sqlite3.Connection, book: Book) -> Book:
    if book.isbn13:
        existing = conn.execute(
            "SELECT id FROM books WHERE isbn13 = ?", (book.isbn13,)
        ).fetchone()
        if existing:
            book.id = existing["id"]

    row = _book_to_row(book)
    columns = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row)
    updates = ", ".join(f"{k}=excluded.{k}" for k in row if k != "id")
    conn.execute(
        f"INSERT INTO books ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        row,
    )
    conn.commit()
    return book


def get_book(conn: sqlite3.Connection, book_id: str) -> Book | None:
    row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return _row_to_book(row) if row else None


def all_books(conn: sqlite3.Connection) -> list[Book]:
    rows = conn.execute("SELECT * FROM books").fetchall()
    return [_row_to_book(r) for r in rows]


def find_book(conn: sqlite3.Connection, title: str, author: str = "") -> Book | None:
    """
    Match on title, narrowed by author surname where given.

    Deliberately conservative. A previous version of this project merged two
    different books with similar titles and produced counts that looked
    plausible and were wrong, so the rule here is: exact-ish or nothing.
    """
    t = title.strip().lower()
    rows = conn.execute(
        "SELECT * FROM books WHERE LOWER(title) = ?", (t,)
    ).fetchall()

    if not rows:
        # Strip a trailing series parenthetical, which Goodreads exports add.
        stripped = t.split("(")[0].strip()
        if stripped and stripped != t:
            rows = conn.execute(
                "SELECT * FROM books WHERE LOWER(title) = ?", (stripped,)
            ).fetchall()

    if not rows:
        return None
    if len(rows) == 1 and not author:
        return _row_to_book(rows[0])

    surname = author.strip().lower().split()[-1] if author.strip() else ""
    for row in rows:
        if surname and surname in (row["author"] or "").lower():
            return _row_to_book(row)
    return _row_to_book(rows[0]) if len(rows) == 1 else None


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

def upsert_event(conn: sqlite3.Connection, event: ReadingEvent) -> ReadingEvent:
    conn.execute(
        """
        INSERT INTO events (id, user_id, book_id, verdict, rating,
                            dnf_reasons, dnf_point, finished_on, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, book_id) DO UPDATE SET
            verdict=excluded.verdict,
            rating=excluded.rating,
            dnf_reasons=excluded.dnf_reasons,
            dnf_point=excluded.dnf_point,
            finished_on=excluded.finished_on,
            note=excluded.note
        """,
        (
            event.id, event.user_id, event.book_id, event.verdict, event.rating,
            json.dumps(event.dnf_reasons), event.dnf_point,
            event.finished_on.isoformat() if event.finished_on else None,
            event.note,
        ),
    )
    conn.commit()
    return event


def get_events(conn: sqlite3.Connection, user_id: str = "local") -> list[ReadingEvent]:
    rows = conn.execute(
        "SELECT * FROM events WHERE user_id = ?", (user_id,)
    ).fetchall()
    events = []
    for row in rows:
        data = dict(row)
        try:
            data["dnf_reasons"] = json.loads(data.get("dnf_reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            data["dnf_reasons"] = []
        if data.get("finished_on"):
            try:
                data["finished_on"] = date.fromisoformat(data["finished_on"])
            except ValueError:
                data["finished_on"] = None
        events.append(ReadingEvent(**data))
    return events


def delete_event(conn: sqlite3.Connection, user_id: str, book_id: str) -> None:
    conn.execute(
        "DELETE FROM events WHERE user_id = ? AND book_id = ?", (user_id, book_id)
    )
    conn.commit()
