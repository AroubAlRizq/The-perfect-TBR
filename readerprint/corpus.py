"""
Corpus loading and enrichment.

Loading seeds the database from data/seed_books.json so the app runs offline
straight after cloning. Enrichment goes out to the open APIs and replaces
provisional values with real ones: metadata from Open Library and Google
Books, and — for public domain titles — style measured from the actual text.

Enrichment is incremental and resumable. It will be interrupted, and losing
forty minutes of polite rate-limited fetching to one timeout is a bad evening.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import db
from .imprint import classify
from .models import Book
from .style import analyse

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEED_PATH = DATA_DIR / "seed_books.json"


def load_seed(conn, path: Path | None = None) -> int:
    """Insert seed books that are not already present."""
    source = path or SEED_PATH
    if not source.exists():
        raise FileNotFoundError(
            f"{source} not found. Run: python scripts/make_seed.py"
        )

    entries = json.loads(source.read_text(encoding="utf-8"))
    added = 0

    for entry in entries:
        if db.find_book(conn, entry["title"], entry.get("author", "")):
            continue
        book = Book(
            title=entry["title"],
            author=entry.get("author", ""),
            year=entry.get("year"),
            subjects=entry.get("subjects", []),
            style=entry.get("style", {}),
            gutenberg_id=entry.get("gutenberg_id"),
            provisional=entry.get("provisional", True),
        )
        book.imprint = classify(None, None, book.title, book.author).as_dict()
        db.upsert_book(conn, book)
        added += 1

    return added


def measure_from_text(book: Book, text: str) -> Book:
    """Replace provisional style values with measured ones."""
    profile = analyse(text)
    book.style = profile.as_dict()
    book.style["prose_density"] = profile.prose_density()
    book.provisional = False

    if not book.word_count:
        # A Gutenberg fetch is capped, so only trust the count when the whole
        # text came through.
        approximate = len(text.split())
        if approximate > 30_000:
            book.word_count = approximate

    # Keep a genuinely representative excerpt: a slice from a quarter of the
    # way in, cut at paragraph boundaries. The opening page of a novel is
    # often atypical of the prose that follows.
    if not book.excerpt:
        start = min(len(text) // 4, max(0, len(text) - 3_000))
        chunk = text[start:start + 2_400]
        paragraphs = chunk.split("\n\n")
        if len(paragraphs) > 2:
            chunk = "\n\n".join(paragraphs[1:-1])
        book.excerpt = chunk.strip()
        book.excerpt_source = "gutenberg"
        book.excerpt_licence = "Public domain, via Project Gutenberg."

    return book


def enrich_book(conn, book: Book, fetch_text: bool = True) -> tuple[Book, list[str]]:
    from . import sources

    actions: list[str] = []

    # Metadata
    docs = sources.openlibrary_search(book.title, book.author, limit=3)
    if docs:
        fresh = sources.book_from_openlibrary(docs[0])
        for attribute in (
            "isbn13", "publisher", "page_count", "cover_url",
            "translator", "original_language", "openlibrary_key",
        ):
            if not getattr(book, attribute) and getattr(fresh, attribute):
                setattr(book, attribute, getattr(fresh, attribute))
        if not book.subjects and fresh.subjects:
            book.subjects = fresh.subjects
        if not book.year and fresh.year:
            book.year = fresh.year
        actions.append("open library")

    item = sources.google_books_lookup(book.title, book.author, book.isbn13)
    if item:
        book = sources.enrich_from_google(book, item)
        actions.append("google books")

    book.imprint = classify(
        book.publisher, book.isbn13, book.title, book.author
    ).as_dict()

    # Real prose, where the licence allows it
    if fetch_text and book.provisional:
        gutenberg_id = book.gutenberg_id
        if not gutenberg_id:
            for result in sources.gutenberg_search(book.title, book.author):
                if book.title.lower()[:20] in (result.get("title") or "").lower():
                    gutenberg_id = result.get("id")
                    break

        if gutenberg_id:
            text = sources.gutenberg_text(gutenberg_id)
            # Guard against a wrong id in the seed: a mismatched fetch would
            # attach one book's prose to another book's record.
            if text and len(text) > 5_000:
                book.gutenberg_id = gutenberg_id
                book = measure_from_text(book, text)
                actions.append(f"measured {book.style.get('words_analysed', 0):,} words")

    db.upsert_book(conn, book)
    return book, actions


def enrich_all(conn, limit: int | None = None, only_provisional: bool = True) -> dict:
    books = db.all_books(conn)
    if only_provisional:
        books = [b for b in books if b.provisional]
    if limit:
        books = books[:limit]

    report = {"attempted": 0, "measured": 0, "metadata_only": 0, "failed": 0}

    for i, book in enumerate(books, 1):
        report["attempted"] += 1
        label = f"{book.title[:44]:<44}"
        print(f"[{i}/{len(books)}] {label}", end=" ", flush=True)
        try:
            book, actions = enrich_book(conn, book)
            if not book.provisional:
                report["measured"] += 1
            elif actions:
                report["metadata_only"] += 1
            print("| " + ", ".join(actions) if actions else "| nothing found")
        except Exception as error:  # noqa: BLE001 - one bad book must not stop the run
            report["failed"] += 1
            print(f"| failed: {type(error).__name__}")

    return report


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "load"
    conn = db.connect()
    db.init(conn)

    if command == "load":
        added = load_seed(conn)
        total = len(db.all_books(conn))
        print(f"Seeded {added} new books. Corpus now holds {total}.")
        print("Next: python -m readerprint.corpus enrich")

    elif command == "enrich":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
        report = enrich_all(conn, limit=limit)
        print()
        print(f"  measured from full text : {report['measured']}")
        print(f"  metadata only           : {report['metadata_only']}")
        print(f"  failed                  : {report['failed']}")

    elif command == "status":
        books = db.all_books(conn)
        measured = sum(1 for b in books if not b.provisional)
        with_isbn = sum(1 for b in books if b.isbn13)
        print(f"  books           : {len(books)}")
        print(f"  measured        : {measured}")
        print(f"  provisional     : {len(books) - measured}")
        print(f"  with ISBN       : {with_isbn}")

    else:
        print("Usage: python -m readerprint.corpus [load|enrich [n]|status]")


if __name__ == "__main__":
    main()
