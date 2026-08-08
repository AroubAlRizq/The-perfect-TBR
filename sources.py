"""
External data sources.

Goodreads is not among them. Its API was withdrawn in 2020 and scraping it is
an arms race a hobby project loses. Everything here is either an open API or
public domain text.

  Open Library   metadata, subjects, covers, editions          open API
  Google Books   metadata plus publisher-supplied snippets     open API
  Gutenberg      complete public domain texts                  public domain

Excerpts are the sensitive part. Full text is only ever stored for public
domain works. For books in copyright the app stores nothing and fetches the
publisher-supplied snippet from Google Books at display time, which is what
that endpoint is for. This keeps the repository clean to publish.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests

from .models import Book
from .imprint import classify, normalise_isbn

USER_AGENT = "Readerprint/0.1 (pilot; contact via repository issues)"
TIMEOUT = 20
_last_call: dict[str, float] = {}


def _throttle(host: str, min_gap: float = 0.75) -> None:
    """Be a good citizen. Free APIs stay free when nobody hammers them."""
    now = time.monotonic()
    previous = _last_call.get(host, 0.0)
    wait = min_gap - (now - previous)
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def _get(url: str, params: dict | None = None) -> dict | None:
    host = url.split("/")[2]
    _throttle(host)
    try:
        response = requests.get(
            url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        if response.status_code != 200:
            return None
        return response.json()
    except (requests.RequestException, ValueError):
        return None


# --------------------------------------------------------------------------
# Open Library
# --------------------------------------------------------------------------

def openlibrary_search(title: str, author: str = "", limit: int = 5) -> list[dict]:
    params = {"title": title, "limit": limit}
    if author:
        params["author"] = author
    data = _get("https://openlibrary.org/search.json", params)
    return (data or {}).get("docs", [])


def book_from_openlibrary(doc: dict) -> Book:
    isbns = doc.get("isbn") or []
    isbn13 = None
    for candidate in isbns:
        normalised = normalise_isbn(candidate)
        if normalised:
            isbn13 = normalised
            break

    publishers = doc.get("publisher") or []
    publisher = publishers[0] if publishers else None

    book = Book(
        title=(doc.get("title") or "").strip(),
        author=", ".join(doc.get("author_name") or []) or "",
        year=doc.get("first_publish_year"),
        isbn13=isbn13,
        publisher=publisher,
        page_count=doc.get("number_of_pages_median"),
        subjects=(doc.get("subject") or [])[:25],
        openlibrary_key=doc.get("key"),
        language=(doc.get("language") or ["eng"])[0][:2],
    )

    if doc.get("cover_i"):
        book.cover_url = f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-M.jpg"

    # Translation detection. Open Library records contributors with roles, and
    # a translated work usually names its translator there.
    for contributor in doc.get("contributor") or []:
        if "translat" in contributor.lower():
            book.translator = re.sub(
                r"\s*\(?translat\w*\)?", "", contributor, flags=re.I
            ).strip(" ,")
            break
    if doc.get("original_language"):
        book.original_language = doc["original_language"][0][:2]

    book.imprint = classify(publisher, isbn13, book.title, book.author).as_dict()
    return book


# --------------------------------------------------------------------------
# Google Books
# --------------------------------------------------------------------------

def google_books_lookup(title: str, author: str = "", isbn: str | None = None) -> dict | None:
    if isbn:
        query = f"isbn:{isbn}"
    else:
        query = f'intitle:"{title}"'
        if author:
            query += f' inauthor:"{author}"'
    data = _get(
        "https://www.googleapis.com/books/v1/volumes",
        {"q": query, "maxResults": 5, "printType": "books"},
    )
    items = (data or {}).get("items") or []
    return items[0] if items else None


def enrich_from_google(book: Book, item: dict) -> Book:
    info = item.get("volumeInfo", {})
    book.google_books_id = item.get("id")

    book.description = book.description or info.get("description")
    book.page_count = book.page_count or info.get("pageCount")
    book.publisher = book.publisher or info.get("publisher")

    if not book.year and info.get("publishedDate"):
        match = re.match(r"(\d{4})", info["publishedDate"])
        if match:
            book.year = int(match.group(1))

    if not book.cover_url:
        links = info.get("imageLinks") or {}
        book.cover_url = links.get("thumbnail") or links.get("smallThumbnail")

    for identifier in info.get("industryIdentifiers") or []:
        if identifier.get("type") == "ISBN_13" and not book.isbn13:
            book.isbn13 = identifier.get("identifier")

    # Publisher-supplied snippet. Short by design and served for exactly this
    # purpose, so it is safe to show but never stored in the repository.
    snippet = (item.get("searchInfo") or {}).get("textSnippet")
    if snippet and not book.excerpt:
        book.excerpt = re.sub(r"<[^>]+>", "", snippet)
        book.excerpt_source = "google_books"
        book.excerpt_licence = "Publisher snippet, fetched at display time."

    book.imprint = classify(
        book.publisher, book.isbn13, book.title, book.author
    ).as_dict()
    return book


# --------------------------------------------------------------------------
# Project Gutenberg
# --------------------------------------------------------------------------

def gutenberg_search(title: str, author: str = "") -> list[dict]:
    """Gutendex is a friendly JSON front end over the Gutenberg catalogue."""
    query = f"{title} {author}".strip()
    data = _get("https://gutendex.com/books", {"search": query})
    return (data or {}).get("results", [])[:5]


def gutenberg_text(gutenberg_id: int, max_chars: int = 400_000) -> str | None:
    """
    Fetch a public domain text and trim the licence boilerplate.

    Analysing the header would tell us about Project Gutenberg's legal team
    rather than the author, so the markers are stripped before measurement.
    """
    for url in (
        f"https://www.gutenberg.org/files/{gutenberg_id}/{gutenberg_id}-0.txt",
        f"https://www.gutenberg.org/cache/epub/{gutenberg_id}/pg{gutenberg_id}.txt",
    ):
        _throttle("gutenberg.org", 1.5)
        try:
            response = requests.get(
                url, timeout=45, headers={"User-Agent": USER_AGENT}
            )
        except requests.RequestException:
            continue
        if response.status_code != 200:
            continue

        text = response.text
        start = re.search(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.I)
        end = re.search(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.I)
        if start:
            text = text[start.end():]
        if end:
            body_end = end.start() - (start.end() if start else 0)
            text = text[:body_end]

        text = text.strip()
        # Skip front matter: contents pages and dedications skew every metric.
        if len(text) > 8_000:
            text = text[3_000:]
        return text[:max_chars]
    return None


# --------------------------------------------------------------------------
# Combined
# --------------------------------------------------------------------------

def fetch_book(title: str, author: str = "", isbn: str | None = None) -> Book | None:
    """Best available record for one title, assembled from all three sources."""
    book: Book | None = None

    docs = openlibrary_search(title, author)
    if docs:
        book = book_from_openlibrary(docs[0])

    if book is None:
        book = Book(title=title, author=author, isbn13=normalise_isbn(isbn))

    item = google_books_lookup(title, author, isbn or book.isbn13)
    if item:
        book = enrich_from_google(book, item)

    # Public domain? Then we can measure the real prose instead of a snippet.
    for result in gutenberg_search(title, author):
        result_title = (result.get("title") or "").lower()
        if title.lower()[:25] in result_title:
            book.gutenberg_id = result.get("id")
            break

    if not book.imprint:
        book.imprint = classify(
            book.publisher, book.isbn13, book.title, book.author
        ).as_dict()
    return book
