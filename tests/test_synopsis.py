"""
Offline tests for synopsis resolution.

Providers are faked rather than called. The point of these tests is not that
HTTP works, it is that the **matching** works: the failure mode that matters
is attaching the Tower of Babel to R. F. Kuang's novel, which looks
authoritative and gives the reader no way to tell it is wrong.

Run:  python tests/test_synopsis.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from readerprint import synopsis as syn

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def test_match_scoring():
    print("\nMatch scoring")

    right = syn.match_score(
        "Babel", "R. F. Kuang", "Babel (Kuang novel)",
        "Babel, or the Necessity of Violence is a 2022 novel by R. F. Kuang, "
        "published by Harper Voyager.",
    )
    wrong = syn.match_score(
        "Babel", "R. F. Kuang", "Tower of Babel",
        "The Tower of Babel is an origin myth in the Book of Genesis meant to "
        "explain why the world's peoples speak different languages.",
    )
    check("the right article scores above threshold",
          right >= syn.MIN_CONFIDENCE, f"({right:.2f})")
    check("the wrong article scores below threshold",
          wrong < syn.MIN_CONFIDENCE, f"({wrong:.2f})")
    check("right beats wrong by a clear margin",
          right - wrong > 0.3, f"({right:.2f} vs {wrong:.2f})")

    # Common-word titles are where this gets dangerous.
    emma_book = syn.match_score(
        "Emma", "Jane Austen", "Emma (novel)",
        "Emma is a novel written by Jane Austen, published in 1815.",
    )
    emma_name = syn.match_score(
        "Emma", "Jane Austen", "Emma (given name)",
        "Emma is a feminine given name of Germanic origin, popular across Europe.",
    )
    check("a novel outscores a name article",
          emma_book >= syn.MIN_CONFIDENCE > emma_name,
          f"({emma_book:.2f} vs {emma_name:.2f})")

    disambig = syn.match_score(
        "It", "Stephen King", "It", "It may refer to: a disambiguation page.",
    )
    check("disambiguation pages are rejected",
          disambig < syn.MIN_CONFIDENCE, f"({disambig:.2f})")

    missing_author = syn.match_score(
        "The Waves", "Virginia Woolf", "Wave",
        "In physics, a wave is a propagating dynamic disturbance.",
    )
    check("an absent author counts against the candidate",
          missing_author < syn.MIN_CONFIDENCE, f"({missing_author:.2f})")

    check("scores stay within range",
          all(0.0 <= syn.match_score(*args) <= 1.0 for args in [
              ("A", "B", "A", "B"), ("", "", "", ""),
              ("X" * 200, "Y" * 200, "X" * 200, "Y" * 200),
          ]))


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

def test_clean():
    print("\nCleaning")
    check("html is stripped",
          "<b>" not in syn._clean("<p>A <b>bold</b> claim about the book.</p>"))
    check("whitespace is collapsed",
          syn._clean("one\n\n  two\t three") == "one two three")

    long_text = ("This is a sentence about the book. " * 200)
    trimmed = syn._clean(long_text, limit=300)
    check("long text is trimmed", len(trimmed) <= 340, f"({len(trimmed)})")
    check("trimming lands on a sentence boundary",
          trimmed.endswith("[…]") and ". […]" in trimmed, f"({trimmed[-40:]})")
    check("short text is untouched",
          syn._clean("A short blurb.") == "A short blurb.")


# --------------------------------------------------------------------------
# Wikipedia provider
# --------------------------------------------------------------------------

SEARCH_HITS = {
    "Babel R. F. Kuang novel": ["Tower of Babel", "Babel (Kuang novel)"],
    "Nothing At All zzz novel": ["Unrelated Thing"],
}

PAGES = {
    "Tower of Babel": {
        "type": "standard",
        "title": "Tower of Babel",
        "description": "origin myth in the Book of Genesis",
        "extract": "The Tower of Babel is an origin myth in the Book of Genesis "
                   "meant to explain why the world's peoples speak different "
                   "languages, and why a tower was abandoned in the plain of Shinar.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Tower_of_Babel"}},
    },
    "Babel (Kuang novel)": {
        "type": "standard",
        "title": "Babel (Kuang novel)",
        "description": "2022 novel by R. F. Kuang",
        "extract": "Babel, or the Necessity of Violence is a 2022 historical "
                   "fantasy novel by R. F. Kuang. It follows a Chinese boy "
                   "brought to Oxford to study translation at the Royal "
                   "Institute, and the empire his work sustains.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Babel_(Kuang_novel)"}},
    },
    "Unrelated Thing": {
        "type": "standard",
        "title": "Unrelated Thing",
        "description": "a kind of machinery",
        "extract": "An unrelated thing is a piece of industrial machinery used "
                   "in the manufacture of ball bearings and other components.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Unrelated_Thing"}},
    },
}

calls = []


def fake_get_json(url, params=None):
    calls.append(url)
    if "api.php" in url:
        query = (params or {}).get("srsearch", "")
        titles = SEARCH_HITS.get(query, [])
        return {"query": {"search": [{"title": t} for t in titles]}}
    if "page/summary/" in url:
        slug = url.rsplit("/", 1)[-1].replace("_", " ")
        from urllib.parse import unquote
        return PAGES.get(unquote(slug))
    return None


def test_wikipedia():
    print("\nWikipedia provider")
    original = syn._get_json
    syn._get_json = fake_get_json
    calls.clear()
    try:
        found = syn.from_wikipedia("Babel", "R. F. Kuang", 2022)
        check("a synopsis is returned", found is not None)
        if found:
            check("it is the novel, not the myth",
                  "Kuang" in found.text, f"({found.text[:60]})")
            check("the myth was skipped despite ranking first",
                  "Genesis" not in found.text)
            check("source is recorded", found.source == "wikipedia")
            check("attribution names the article and licence",
                  found.attribution and "CC BY-SA" in found.attribution
                  and "Babel" in found.attribution, f"({found.attribution})")
            check("a link back is included",
                  found.url and found.url.startswith("https://en.wikipedia.org/"))
            check("confidence is reported", 0 < found.confidence <= 1.0)

        nothing = syn.from_wikipedia("Nothing At All", "zzz")
        check("an unmatched book returns nothing rather than a wrong article",
              nothing is None)
    finally:
        syn._get_json = original


# --------------------------------------------------------------------------
# Chain
# --------------------------------------------------------------------------

def test_chain():
    print("\nProvider chain")
    order = []

    def make(name, result):
        def provider(*args, **kwargs):
            order.append(name)
            return result
        return provider

    originals = (syn.from_google_books, syn.from_open_library,
                 syn.from_wikipedia, syn.from_custom_search)
    try:
        # Google Books answers, so nothing further is tried.
        syn.from_google_books = make("google", syn.Synopsis("A blurb.", "google_books"))
        syn.from_open_library = make("openlib", None)
        syn.from_wikipedia = make("wiki", None)
        syn.from_custom_search = make("custom", None)
        result = syn.resolve("Title", "Author")
        check("the first provider that answers wins", result.source == "google_books")
        check("later providers are not called", order == ["google"], f"({order})")

        # Google Books is empty, so it falls through to Wikipedia.
        order.clear()
        syn.from_google_books = make("google", None)
        syn.from_open_library = make("openlib", None)
        syn.from_wikipedia = make("wiki", syn.Synopsis("A plot summary.", "wikipedia"))
        result = syn.resolve("Title", "Author")
        check("the chain falls through to Wikipedia", result.source == "wikipedia")
        check("every earlier provider was tried",
              order == ["google", "openlib", "wiki"], f"({order})")

        # A provider that raises must not end the chain.
        order.clear()

        def explode(*args, **kwargs):
            order.append("boom")
            raise RuntimeError("network on fire")

        syn.from_google_books = explode
        syn.from_open_library = make("openlib", None)
        syn.from_wikipedia = make("wiki", syn.Synopsis("Recovered.", "wikipedia"))
        result = syn.resolve("Title", "Author")
        check("a failing provider is survived", result is not None
              and result.text == "Recovered.")

        # Nothing anywhere.
        syn.from_google_books = make("google", None)
        syn.from_open_library = make("openlib", None)
        syn.from_wikipedia = make("wiki", None)
        syn.from_custom_search = make("custom", None)
        check("no result returns None rather than an empty string",
              syn.resolve("Title", "Author") is None)

        # Explicit ordering is respected.
        order.clear()
        syn.from_wikipedia = make("wiki", syn.Synopsis("Wiki first.", "wikipedia"))
        result = syn.resolve("Title", "Author", providers=["wikipedia"])
        check("a caller can restrict the chain",
              result.source == "wikipedia" and order == ["wiki"], f"({order})")
    finally:
        (syn.from_google_books, syn.from_open_library,
         syn.from_wikipedia, syn.from_custom_search) = originals


def test_custom_off_by_default():
    print("\nOptional search provider")
    import os
    saved = os.environ.pop("READERPRINT_SEARCH_PROVIDER", None)
    try:
        check("custom search is off unless configured",
              syn.from_custom_search("Any Book", "Any Author") is None)
        os.environ["READERPRINT_SEARCH_PROVIDER"] = "brave"
        os.environ.pop("BRAVE_API_KEY", None)
        check("a configured provider without a key stays quiet",
              syn.from_custom_search("Any Book", "Any Author") is None)
        os.environ["READERPRINT_SEARCH_PROVIDER"] = "nonsense"
        check("an unknown provider is ignored",
              syn.from_custom_search("Any Book", "Any Author") is None)
    finally:
        os.environ.pop("READERPRINT_SEARCH_PROVIDER", None)
        if saved:
            os.environ["READERPRINT_SEARCH_PROVIDER"] = saved


def test_storage():
    print("\nSynopsis storage")
    from readerprint import db
    from readerprint.models import Book

    conn = db.connect(":memory:")
    db.init(conn)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
    for column in ("description_source", "description_attribution", "description_url"):
        check(f"{column} exists", column in columns)

    book = Book(title="Stored", author="Someone",
                description="A summary.", description_source="wikipedia",
                description_attribution="From the Wikipedia article “Stored”, CC BY-SA",
                description_url="https://en.wikipedia.org/wiki/Stored")
    db.upsert_book(conn, book)
    loaded = db.get_book(conn, book.id)
    check("attribution survives a round trip",
          loaded.description_attribution == book.description_attribution)
    check("source url survives a round trip",
          loaded.description_url == book.description_url)


if __name__ == "__main__":
    for suite in (test_match_scoring, test_clean, test_wikipedia, test_chain,
                  test_custom_off_by_default, test_storage):
        suite()

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        sys.exit(1)
    print("All synopsis checks passed.")