"""
Synopsis resolution.

A chain of providers, tried cheapest and most reliable first:

  1. stored        already in the database, no request
  2. google books  publisher-supplied description, served for display
  3. open library  community description, CC0
  4. wikipedia     plot summary, CC BY-SA, attributed on screen
  5. custom        an optional search provider, off unless configured

Wikipedia is the one that earns its place. The measured half of the corpus is
almost entirely public domain classics, and those are precisely the books
Google Books and Open Library describe worst — a nineteenth century novel
usually has no jacket copy anywhere, while Wikipedia has a full plot summary.

The hard problem here is not fetching, it is **matching**. Searching Wikipedia
for "Babel" returns the Tower of Babel; for "It" you get the pronoun; for
"Emma" a given name. Attaching the wrong plot summary to a book is worse than
having none, because it looks authoritative and the reader has no way to tell.
So every candidate is scored against the book it claims to describe, and
anything that fails is discarded rather than shown.

Licensing: everything here is either served for display by its own API or
openly licensed. No retailer pages, no review sites, no scraped jacket copy.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, asdict

import requests

USER_AGENT = "Readerprint/0.1 (pilot; contact via repository issues)"
TIMEOUT = 20

_last_call: dict[str, float] = {}


def _throttle(host: str, min_gap: float = 0.4) -> None:
    now = time.monotonic()
    wait = min_gap - (now - _last_call.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def _get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        _throttle(url.split("/")[2])
        response = requests.get(
            url, params=params, timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if response.status_code != 200:
            return None
        return response.json()
    except (requests.RequestException, ValueError):
        return None


@dataclass
class Synopsis:
    text: str
    source: str                    # google_books / open_library / wikipedia / custom
    attribution: str | None = None
    url: str | None = None
    confidence: float = 1.0

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "novel",
    "book", "story", "tale", "vol", "volume", "part", "series", "edition",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def match_score(
    book_title: str,
    book_author: str,
    candidate_title: str,
    candidate_text: str,
    extra: str = "",
) -> float:
    """
    How confident we are that a candidate describes this book, 0 to 1.

    Author surname appearing in the article is the single strongest signal —
    a Wikipedia article about a novel almost always names its author in the
    first sentence, and an article about the biblical Tower of Babel will not
    mention Kuang.
    """
    score = 0.0
    blob = f"{candidate_title} {candidate_text} {extra}".lower()

    wanted = _tokens(book_title)
    got = _tokens(candidate_title)
    if wanted:
        overlap = len(wanted & got) / len(wanted)
        score += overlap * 0.45
        # An exact title match, allowing for a disambiguator in brackets.
        stripped = re.sub(r"\s*\([^)]*\)\s*$", "", candidate_title).strip().lower()
        if stripped == book_title.strip().lower():
            score += 0.15

    if book_author:
        surname = book_author.replace(",", " ").split()[-1].lower() if book_author.split() else ""
        if len(surname) > 2 and surname in blob:
            score += 0.35
        else:
            # No author anywhere in an article that claims to be about their
            # book is a strong negative, not merely a missing bonus.
            score -= 0.15

    # Vocabulary suggesting the article is about a work rather than a place,
    # person, or concept that happens to share the name.
    if re.search(r"\b(novel|novella|book|published|author|fiction|literary|"
                 r"story collection|memoir)\b", blob):
        score += 0.2

    if re.search(r"\b(disambiguation|may refer to)\b", blob):
        score -= 0.6

    return max(0.0, min(1.0, score))


MIN_CONFIDENCE = 0.55


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

def from_google_books(title: str, author: str, isbn: str | None = None) -> Synopsis | None:
    queries = []
    if isbn:
        queries.append(f"isbn:{isbn}")
    if title and author:
        queries.append(f'intitle:"{title}" inauthor:"{author}"')
    if title:
        queries.append(f'intitle:"{title}"')

    for query in queries:
        data = _get_json(
            "https://www.googleapis.com/books/v1/volumes",
            {"q": query, "maxResults": 5, "printType": "books"},
        )
        for item in (data or {}).get("items") or []:
            info = item.get("volumeInfo") or {}
            description = info.get("description")
            if not description or len(description) < 60:
                continue
            confidence = match_score(
                title, author, info.get("title", ""), description,
                " ".join(info.get("authors") or []),
            )
            if confidence >= MIN_CONFIDENCE:
                return Synopsis(
                    text=_clean(description),
                    source="google_books",
                    attribution="Publisher description via Google Books",
                    url=info.get("infoLink"),
                    confidence=round(confidence, 2),
                )
    return None


def from_open_library(title: str, author: str, work_key: str | None = None) -> Synopsis | None:
    def extract(raw) -> str | None:
        if isinstance(raw, dict):
            return raw.get("value")
        return raw if isinstance(raw, str) else None

    if work_key:
        data = _get_json(f"https://openlibrary.org{work_key}.json")
        text = extract((data or {}).get("description"))
        if text and len(text) > 60:
            return Synopsis(
                text=_clean(text),
                source="open_library",
                attribution="Description from Open Library",
                url=f"https://openlibrary.org{work_key}",
                confidence=0.95,   # keyed directly, so identity is not in doubt
            )

    data = _get_json(
        "https://openlibrary.org/search.json",
        {"title": title, "author": author or None, "limit": 3,
         "fields": "key,title,author_name,first_sentence"},
    )
    for doc in (data or {}).get("docs") or []:
        detail = _get_json(f"https://openlibrary.org{doc.get('key')}.json")
        text = extract((detail or {}).get("description"))
        if not text or len(text) < 60:
            continue
        confidence = match_score(
            title, author, doc.get("title", ""), text,
            " ".join(doc.get("author_name") or []),
        )
        if confidence >= MIN_CONFIDENCE:
            return Synopsis(
                text=_clean(text),
                source="open_library",
                attribution="Description from Open Library",
                url=f"https://openlibrary.org{doc.get('key')}",
                confidence=round(confidence, 2),
            )
    return None


def from_wikipedia(title: str, author: str, year: int | None = None) -> Synopsis | None:
    """
    Find the article about this book and take its opening summary.

    Queries are built to disambiguate up front — "Babel" alone finds the
    tower, "Babel R. F. Kuang novel" finds the book — and every candidate is
    still scored before anything is returned.
    """
    attempts = []
    if author:
        attempts.append(f"{title} {author} novel")
        attempts.append(f"{title} {author}")
    attempts.append(f"{title} novel")
    attempts.append(title)

    seen: set[str] = set()

    for query in attempts:
        data = _get_json(
            "https://en.wikipedia.org/w/api.php",
            {
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": 5, "format": "json", "srnamespace": 0,
            },
        )
        for hit in ((data or {}).get("query") or {}).get("search") or []:
            page_title = hit.get("title")
            if not page_title or page_title in seen:
                continue
            seen.add(page_title)

            summary = _get_json(
                "https://en.wikipedia.org/api/rest_v1/page/summary/"
                + requests.utils.quote(page_title.replace(" ", "_"), safe="")
            )
            if not summary:
                continue
            if summary.get("type") == "disambiguation":
                continue

            extract = summary.get("extract") or ""
            if len(extract) < 80:
                continue

            description = summary.get("description") or ""
            confidence = match_score(title, author, page_title, extract, description)

            # A publication year in the article is decent corroboration when
            # the author is missing or the name is common.
            if year and str(year) in extract:
                confidence = min(1.0, confidence + 0.1)

            if confidence >= MIN_CONFIDENCE:
                page_url = (
                    (summary.get("content_urls") or {}).get("desktop") or {}
                ).get("page")
                return Synopsis(
                    text=_clean(extract),
                    source="wikipedia",
                    attribution=f"From the Wikipedia article “{page_title}”, CC BY-SA",
                    url=page_url or f"https://en.wikipedia.org/wiki/{page_title}",
                    confidence=round(confidence, 2),
                )
    return None


def from_custom_search(title: str, author: str) -> Synopsis | None:
    """
    Optional search provider, off unless configured.

    Set READERPRINT_SEARCH_PROVIDER to "brave" or "tavily" and the matching
    API key, and this becomes a last resort. It is deliberately not required:
    the free providers above cover most books, and a pilot that needs a paid
    key before it can describe a book is a pilot nobody runs.

    Only the search engine's own snippet is used. Fetching and reproducing
    jacket copy from retailer or review pages would mean copying text this
    project has no licence to.
    """
    provider = os.environ.get("READERPRINT_SEARCH_PROVIDER", "").lower()
    if not provider:
        return None

    query = f"{title} {author} novel plot summary".strip()

    if provider == "brave":
        key = os.environ.get("BRAVE_API_KEY")
        if not key:
            return None
        try:
            _throttle("api.search.brave.com", 1.0)
            response = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 5},
                headers={"X-Subscription-Token": key, "Accept": "application/json",
                         "User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            )
            if response.status_code != 200:
                return None
            results = (response.json().get("web") or {}).get("results") or []
        except (requests.RequestException, ValueError):
            return None

    elif provider == "tavily":
        key = os.environ.get("TAVILY_API_KEY")
        if not key:
            return None
        try:
            _throttle("api.tavily.com", 1.0)
            response = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": 5},
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
            if response.status_code != 200:
                return None
            results = [
                {"description": r.get("content"), "title": r.get("title"),
                 "url": r.get("url")}
                for r in response.json().get("results") or []
            ]
        except (requests.RequestException, ValueError):
            return None
    else:
        return None

    for result in results:
        snippet = re.sub(r"<[^>]+>", "", result.get("description") or "")
        if len(snippet) < 80:
            continue
        confidence = match_score(title, author, result.get("title", ""), snippet)
        if confidence >= MIN_CONFIDENCE:
            return Synopsis(
                text=_clean(snippet),
                source="custom",
                attribution=f"Search snippet via {provider.title()}",
                url=result.get("url"),
                confidence=round(confidence, 2),
            )
    return None


# --------------------------------------------------------------------------
# Chain
# --------------------------------------------------------------------------

def _clean(text: str, limit: int = 2200) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    # Cut at a sentence boundary rather than mid-clause.
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: stop + 1] if stop > limit * 0.5 else cut).strip() + " […]"


def resolve(
    title: str,
    author: str = "",
    isbn: str | None = None,
    work_key: str | None = None,
    year: int | None = None,
    providers: list[str] | None = None,
) -> Synopsis | None:
    """Walk the provider chain and return the first confident match."""
    order = providers or ["google_books", "open_library", "wikipedia", "custom"]

    for name in order:
        try:
            if name == "google_books":
                found = from_google_books(title, author, isbn)
            elif name == "open_library":
                found = from_open_library(title, author, work_key)
            elif name == "wikipedia":
                found = from_wikipedia(title, author, year)
            elif name == "custom":
                found = from_custom_search(title, author)
            else:
                continue
        except Exception:  # noqa: BLE001 — one bad provider must not end the chain
            continue

        if found and found.text:
            return found

    return None