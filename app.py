"""
Readerprint API and web server.

Run:  python app.py       then open http://127.0.0.1:8000

Single user by default. Every request uses the user id "local" unless a
?user= parameter is supplied, which is enough for a few friends sharing one
instance and honest about not being real authentication.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from readerprint import db, ingest
from readerprint.imprint import TIER_LABELS, classify
from readerprint.models import (
    CONTENT_FLAGS, DNF_REASONS, LENGTH_BAND_LABELS, POV_LABELS,
    TENSE_LABELS, VERDICTS, Book, ReadingEvent,
)
from readerprint.recommend import build_profile, build_space, recommend
from readerprint.reviews import Review, rank_reviews, summarise_ratings
from readerprint.style import analyse, describe

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# The vector space is rebuilt only when the corpus actually changes. On 90
# books that is milliseconds; on 90,000 it would not be, so the guard is
# there from the start.
_space = None
_space_signature: tuple | None = None


def get_space(conn):
    global _space, _space_signature
    books = db.all_books(conn)
    signature = (len(books), sum(1 for b in books if not b.provisional))
    if _space is None or signature != _space_signature:
        _space = build_space(books)
        _space_signature = signature
    return _space


def invalidate_space():
    global _space, _space_signature
    _space = None
    _space_signature = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.init(conn)
    if not db.all_books(conn):
        from readerprint.corpus import load_seed
        try:
            added = load_seed(conn)
            print(f"Seeded {added} books on first run.")
        except FileNotFoundError:
            print("No seed file. Run: python scripts/make_seed.py")
    app.state.conn = conn
    yield
    conn.close()


app = FastAPI(title="Readerprint", version="0.1.0", lifespan=lifespan)


def conn_of(request: Request):
    return request.app.state.conn


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def book_payload(book: Book, full: bool = False) -> dict:
    style = book.style or {}
    imprint = book.imprint or {}
    payload = {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "year": book.year,
        "cover_url": book.cover_url,
        "publisher": book.publisher,
        "provisional": book.provisional,
        "pov": style.get("pov", "unknown"),
        "pov_label": POV_LABELS.get(style.get("pov", "unknown")),
        "tense": style.get("tense", "unknown"),
        "tense_label": TENSE_LABELS.get(style.get("tense", "unknown")),
        "prose_density": style.get("prose_density"),
        "length_band": book.length_band(),
        "length_label": LENGTH_BAND_LABELS.get(book.length_band()),
        "word_count": book.estimated_word_count(),
        "word_count_estimated": book.word_count is None,
        "tier": imprint.get("tier", "unknown"),
        "tier_label": imprint.get("tier_label", TIER_LABELS["unknown"]),
        "web_origin": imprint.get("wattpad_origin", False),
        "web_origin_note": imprint.get("wattpad_note"),
        "translated": book.is_translated,
        "translator": book.translator,
        "subjects": (book.subjects or [])[:6],
        "rating": (book.ratings or {}).get("weighted_score"),
        "hype_note": (book.ratings or {}).get("hype_note"),
    }
    if full:
        payload.update({
            "description": book.description,
            "excerpt": book.excerpt,
            "excerpt_source": book.excerpt_source,
            "excerpt_licence": book.excerpt_licence,
            "style": style,
            "style_note": describe_from_dict(style),
            "imprint": imprint,
            "ratings": book.ratings or {},
            "content_flags": book.content_flags or [],
            "isbn13": book.isbn13,
            "series": book.series,
            "all_subjects": book.subjects or [],
        })
    return payload


def describe_from_dict(style: dict) -> str:
    """
    Rebuild a StyleProfile from stored values.

    Only declared fields are restored. The stored dict also carries a
    "prose_density" key computed from the method of the same name, and
    assigning it back would shadow the method with a float.
    """
    from dataclasses import fields
    from readerprint.style import StyleProfile

    allowed = {f.name for f in fields(StyleProfile)}
    profile = StyleProfile()
    for key, value in style.items():
        if key in allowed:
            setattr(profile, key, value)
    return describe(profile)


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

@app.get("/api/meta")
def meta():
    return {
        "verdicts": [
            {"key": "loved", "label": "Loved it"},
            {"key": "liked", "label": "Liked it"},
            {"key": "fine", "label": "It was fine"},
            {"key": "disliked", "label": "Disliked it"},
            {"key": "dnf", "label": "Did not finish"},
        ],
        "dnf_reasons": [
            {"key": k, **v} for k, v in DNF_REASONS.items()
        ],
        "content_flags": CONTENT_FLAGS,
        "pov_options": POV_LABELS,
        "length_bands": LENGTH_BAND_LABELS,
        "tiers": TIER_LABELS,
    }


# --------------------------------------------------------------------------
# Books
# --------------------------------------------------------------------------

@app.get("/api/books")
def list_books(
    request: Request,
    q: str = Query("", description="Title or author fragment"),
    limit: int = Query(30, le=200),
):
    conn = conn_of(request)
    if q.strip():
        pattern = f"%{q.strip().lower()}%"
        rows = conn.execute(
            "SELECT * FROM books WHERE LOWER(title) LIKE ? OR LOWER(author) LIKE ? "
            "ORDER BY LENGTH(title) LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM books ORDER BY RANDOM() LIMIT ?", (limit,)
        ).fetchall()
    books = [db._row_to_book(r) for r in rows]
    return {"books": [book_payload(b) for b in books]}


@app.get("/api/books/{book_id}")
def book_detail(request: Request, book_id: str):
    book = db.get_book(conn_of(request), book_id)
    if not book:
        raise HTTPException(404, "No book with that id.")
    return book_payload(book, full=True)


class ExcerptIn(BaseModel):
    text: str = Field(min_length=200, description="At least a few paragraphs.")


@app.post("/api/books/{book_id}/excerpt")
def add_excerpt(request: Request, book_id: str, payload: ExcerptIn):
    """
    Measure a book from prose the reader supplies.

    The fastest way to fill gaps in the corpus: someone with the book in hand
    types or pastes a page, and that title stops being provisional. Text is
    measured and a short sample kept; nothing is redistributed.
    """
    conn = conn_of(request)
    book = db.get_book(conn, book_id)
    if not book:
        raise HTTPException(404, "No book with that id.")

    profile = analyse(payload.text)
    if profile.words_analysed < 120:
        raise HTTPException(
            400, "That sample is too short to measure. Around 300 words works well."
        )

    book.style = profile.as_dict()
    book.style["prose_density"] = profile.prose_density()
    book.provisional = False
    book.excerpt = payload.text.strip()[:1800]
    book.excerpt_source = "user"
    book.excerpt_licence = "Supplied by a reader for private analysis."
    db.upsert_book(conn, book)
    invalidate_space()

    return {"book": book_payload(book, full=True), "measured": profile.as_dict()}


class AnalyseIn(BaseModel):
    text: str = Field(min_length=1)


@app.post("/api/analyse")
def analyse_text(payload: AnalyseIn):
    """
    Measure any prose, attached to no book.

    This is the whole premise in one request: paste a page of something you
    are considering and find out what it does before you commit to it.
    """
    profile = analyse(payload.text)
    data = profile.as_dict()
    data["prose_density"] = profile.prose_density()
    data["summary"] = describe(profile)
    return data


class BookIn(BaseModel):
    title: str
    author: str = ""
    year: int | None = None
    publisher: str | None = None
    page_count: int | None = None


@app.post("/api/books")
def create_book(request: Request, payload: BookIn):
    conn = conn_of(request)
    existing = db.find_book(conn, payload.title, payload.author)
    if existing:
        return {"book": book_payload(existing), "created": False}

    book = Book(**payload.model_dump())
    book.imprint = classify(
        payload.publisher, None, payload.title, payload.author
    ).as_dict()
    db.upsert_book(conn, book)
    invalidate_space()
    return {"book": book_payload(book), "created": True}


# --------------------------------------------------------------------------
# Shelf
# --------------------------------------------------------------------------

@app.get("/api/shelf")
def shelf(request: Request, user: str = "local"):
    conn = conn_of(request)
    events = db.get_events(conn, user)
    items = []
    for event in events:
        book = db.get_book(conn, event.book_id)
        if not book:
            continue
        items.append({
            "book": book_payload(book),
            "event": event.as_dict(),
        })
    items.sort(key=lambda i: i["event"]["weight"], reverse=True)
    return {
        "items": items,
        "counts": {
            "total": len(items),
            "loved": sum(1 for i in items if i["event"]["verdict"] == "loved"),
            "dnf": sum(1 for i in items if i["event"]["verdict"] == "dnf"),
            "dnf_without_reason": sum(
                1 for i in items
                if i["event"]["verdict"] == "dnf" and not i["event"]["dnf_reasons"]
            ),
        },
    }


class EventIn(BaseModel):
    book_id: str
    verdict: str
    rating: float | None = None
    dnf_reasons: list[str] = []
    dnf_point: int | None = None
    note: str | None = None


@app.post("/api/shelf")
def add_to_shelf(request: Request, payload: EventIn, user: str = "local"):
    conn = conn_of(request)
    if payload.verdict not in VERDICTS:
        raise HTTPException(400, f"Verdict must be one of {sorted(VERDICTS)}.")
    if not db.get_book(conn, payload.book_id):
        raise HTTPException(404, "No book with that id.")

    unknown = set(payload.dnf_reasons) - set(DNF_REASONS)
    if unknown:
        raise HTTPException(400, f"Unrecognised reasons: {sorted(unknown)}")

    event = ReadingEvent(user_id=user, **payload.model_dump())
    db.upsert_event(conn, event)
    return {"event": event.as_dict()}


@app.delete("/api/shelf/{book_id}")
def remove_from_shelf(request: Request, book_id: str, user: str = "local"):
    db.delete_event(conn_of(request), user, book_id)
    return {"removed": book_id}


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

@app.post("/api/import")
async def import_export(
    request: Request, file: UploadFile = File(...), user: str = "local"
):
    raw = await file.read()
    if len(raw) > 12_000_000:
        raise HTTPException(413, "File over 12 MB. That is not a reading history.")

    parsed = ingest.parse_export(raw)
    if parsed["report"].get("error"):
        raise HTTPException(400, parsed["report"]["error"])

    report = ingest.import_into(conn_of(request), parsed, user_id=user)
    invalidate_space()
    return {"report": report}


# --------------------------------------------------------------------------
# Profile and recommendations
# --------------------------------------------------------------------------

@app.get("/api/profile")
def profile(request: Request, user: str = "local"):
    conn = conn_of(request)
    space = get_space(conn)
    events = db.get_events(conn, user)
    taste = build_profile(space, events)

    return {
        "usable": taste.is_usable,
        "n_liked": taste.n_liked,
        "n_disliked": taste.n_disliked,
        "style_summary": taste.style_summary,
        "aversions": {k: round(v, 2) for k, v in taste.aversions.items()},
        "dnf_reason_counts": taste.dnf_reason_counts,
        "corpus_size": len(space.books),
        "corpus_measured": sum(1 for b in space.books if not b.provisional),
    }


@app.get("/api/recommendations")
def recommendations(
    request: Request,
    user: str = "local",
    limit: int = Query(12, le=50),
    diversity: float = Query(0.3, ge=0.0, le=0.9),
    min_length: int | None = None,
    max_length: int | None = None,
    pov: str | None = Query(None, description="Comma-separated: first,third"),
    exclude_flags: str | None = None,
):
    conn = conn_of(request)
    space = get_space(conn)
    events = db.get_events(conn, user)
    taste = build_profile(space, events)

    if not taste.is_usable:
        return {
            "recommendations": [],
            "message": (
                f"Rate at least three books to build a profile. "
                f"You have {taste.n_liked}."
            ),
            "usable": False,
        }

    results = recommend(
        space,
        taste,
        exclude_ids={e.book_id for e in events},
        limit=limit,
        diversity=diversity,
        min_length=min_length,
        max_length=max_length,
        allowed_pov=set(pov.split(",")) if pov else None,
        exclude_flags=set(exclude_flags.split(",")) if exclude_flags else None,
    )

    return {
        "usable": True,
        "recommendations": [
            {
                "book": book_payload(r.book, full=True),
                "score": round(r.score, 4),
                "affinity": round(r.affinity, 4),
                "penalty": r.penalty,
                "reasons": r.reasons,
                "cautions": r.cautions,
            }
            for r in results
        ],
    }


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------

class ReviewIn(BaseModel):
    source: str = "unknown"
    text: str
    rating: float | None = None
    votes: int = 0


class ReviewBatch(BaseModel):
    reviews: list[ReviewIn]
    source_stats: dict = {}


@app.post("/api/books/{book_id}/reviews")
def score_reviews(request: Request, book_id: str, payload: ReviewBatch):
    """
    Score and rank a batch of reviews, and store the aggregate on the book.

    Reviews arrive from whatever source the caller has legitimate access to.
    Scoring and ranking are the part worth building; collection is the part
    that depends on which APIs a deployment is permitted to use.
    """
    conn = conn_of(request)
    book = db.get_book(conn, book_id)
    if not book:
        raise HTTPException(404, "No book with that id.")

    reviews = [
        Review(source=r.source, text=r.text, rating=r.rating, votes=r.votes)
        for r in payload.reviews
    ]
    ranked = rank_reviews(reviews, limit=6)

    if payload.source_stats:
        summary = summarise_ratings(payload.source_stats)
        book.ratings = summary.as_dict()
        db.upsert_book(conn, book)

    return {
        "ranked": [r.as_dict() for r in ranked],
        "ratings": book.ratings,
        "considered": len(reviews),
    }


# --------------------------------------------------------------------------
# Static
# --------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Not found."}, status_code=404)
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
