"""
Project Gutenberg ingestion.

This is the only source that yields real style measurements at scale. The
texts are public domain and downloadable in full, which means every book from
here arrives measured rather than provisional — no placeholders, no pasting.

The catalogue is a single small CSV listing every title. Texts are fetched
individually, or read straight off a local rsync mirror if there is one:

    rsync -av --del ftp@aleph.gutenberg.org::gutenberg-epub /your/mirror

The mirror is the right answer for tens of thousands of books. For a few
hundred, HTTP with a throttle is fine and needs no setup. Both paths feed the
same measurement code.
"""

from __future__ import annotations

import csv
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .. import db
from ..corpus import measure_from_text
from ..imprint import classify
from ..models import Book
from .download import USER_AGENT, Progress, download

CATALOG_URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"
CATALOG_NAME = "pg_catalog.csv"

# Bookshelves and subjects that indicate prose fiction. Gutenberg's own
# Bookshelves field is far cleaner than its LoC subjects, so it is checked
# first and trusted more.
FICTION_SHELF = re.compile(
    r"\b(fiction|novel|romance|adventure|gothic|detective|mystery|"
    r"science fiction|fantasy|horror|humor|humour|children's literature|"
    r"best books ever|harvard classics|short stories)\b", re.I,
)

FICTION_SUBJECT = re.compile(
    r"\b(fiction|novel|short stories|romance|adventure stories|"
    r"detective and mystery stories|science fiction|fantasy fiction|"
    r"horror tales|love stories|historical fiction)\b", re.I,
)

# Anything that is not continuous prose skews every measurement in style.py:
# a play is nearly all dialogue, a poetry collection has no sentences to
# speak of, and a dictionary has no narration at all.
EXCLUDE_SUBJECT = re.compile(
    r"\b(poetry|poems|drama|plays|periodicals|dictionaries|encyclopedias|"
    r"indexes|bibliography|hymns|songs|essays|letters|speeches|sermons|"
    r"catalogs|almanacs|readers|primers|cookbooks|manuals)\b", re.I,
)

_throttle_lock = threading.Lock()
_last_request = [0.0]


def _polite(min_gap: float) -> None:
    """Gutenberg is a charity running on donated bandwidth. Do not hammer it."""
    with _throttle_lock:
        wait = min_gap - (time.monotonic() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()


def fetch_catalog(data_dir: Path, force: bool = False) -> Path:
    return download(CATALOG_URL, data_dir / CATALOG_NAME, force=force)


def read_catalog(path: Path, english_only: bool = True) -> list[dict]:
    """
    Select prose fiction from the catalogue.

    Columns are Text#, Type, Issued, Title, Language, Authors, Subjects,
    LoCC, Bookshelves.
    """
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("Type") or "").strip() != "Text":
                continue
            if english_only and (row.get("Language") or "").strip() != "en":
                continue

            title = (row.get("Title") or "").strip()
            if not title:
                continue

            subjects = row.get("Subjects") or ""
            shelves = row.get("Bookshelves") or ""
            if EXCLUDE_SUBJECT.search(subjects) or EXCLUDE_SUBJECT.search(shelves):
                continue
            if not (FICTION_SHELF.search(shelves) or FICTION_SUBJECT.search(subjects)):
                continue

            try:
                text_id = int((row.get("Text#") or "").strip())
            except ValueError:
                continue

            author = (row.get("Authors") or "").strip()
            # Catalogue authors are "Surname, Forename, 1812-1870".
            author = re.sub(r",\s*\d{4}\??\s*-\s*\d{0,4}\??", "", author).strip(" ,")
            if "," in author and ";" not in author:
                surname, _, forename = author.partition(",")
                author = f"{forename.strip()} {surname.strip()}".strip()

            year = None
            match = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", row.get("Issued") or "")
            if match:
                year = int(match.group(1))

            rows.append({
                "id": text_id,
                "title": re.sub(r"\s+", " ", title.replace("\n", " ")),
                "author": author,
                "subjects": _clean_subjects(subjects, shelves),
                "year": year,
            })
    return rows


def _clean_subjects(subjects: str, shelves: str) -> list[str]:
    parts: list[str] = []
    for blob in (shelves, subjects):
        for piece in re.split(r"[;\n]", blob or ""):
            piece = piece.strip().strip("-").strip()
            # Drop LoC subdivisions, which are cataloguing artefacts.
            piece = re.sub(r"\s*--\s*", " ", piece)
            if 2 < len(piece) < 60:
                parts.append(piece.lower())
    seen, out = set(), []
    for part in parts:
        if part not in seen:
            seen.add(part)
            out.append(part)
    return out[:20]


# --------------------------------------------------------------------------
# Text retrieval
# --------------------------------------------------------------------------

BOILERPLATE_START = re.compile(
    r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.I | re.S
)
BOILERPLATE_END = re.compile(
    r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.I | re.S
)


def strip_boilerplate(text: str) -> str:
    """
    Remove the licence header and footer.

    Measuring them would describe Project Gutenberg's legal notice rather
    than the author, and the notice is long enough to move every number.
    """
    start = BOILERPLATE_START.search(text)
    if start:
        text = text[start.end():]
    end = BOILERPLATE_END.search(text)
    if end:
        text = text[:end.start()]

    text = text.strip()
    # Skip front matter — contents pages, dedications, transcriber notes.
    if len(text) > 12_000:
        text = text[4_000:]
    return text


def read_from_mirror(text_id: int, mirror: Path) -> str | None:
    """Read from a local rsync mirror, trying the usual layouts."""
    candidates = [
        mirror / "cache" / "epub" / str(text_id) / f"pg{text_id}.txt",
        mirror / str(text_id) / f"pg{text_id}.txt",
        mirror / f"{text_id}.txt",
        mirror / "files" / str(text_id) / f"{text_id}-0.txt",
    ]
    for path in candidates:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return None


def fetch_text(text_id: int, min_gap: float = 1.0, timeout: int = 60) -> str | None:
    for url in (
        f"https://www.gutenberg.org/cache/epub/{text_id}/pg{text_id}.txt",
        f"https://www.gutenberg.org/files/{text_id}/{text_id}-0.txt",
    ):
        _polite(min_gap)
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        except requests.RequestException:
            continue
        if response.status_code == 200 and len(response.text) > 5_000:
            return response.text
    return None


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def ingest(
    conn,
    catalog_path: Path,
    limit: int | None = None,
    workers: int = 3,
    mirror: Path | None = None,
    min_gap: float = 1.0,
    cache_dir: Path | None = None,
) -> dict:
    """
    Fetch, measure, and store Gutenberg texts.

    Downloads run in a small thread pool because they are IO bound and the
    throttle is global, so more threads means less idle waiting rather than
    more load on Gutenberg. Measurement happens on the worker thread; the
    database write happens on the main thread, since SQLite would rather not
    be written from several at once.
    """
    entries = read_catalog(catalog_path)
    report = {"catalogue": len(entries), "measured": 0, "skipped": 0, "failed": 0}

    # Skip anything already measured so a rerun costs nothing.
    known = {
        row["gutenberg_id"]
        for row in conn.execute(
            "SELECT gutenberg_id FROM books WHERE gutenberg_id IS NOT NULL "
            "AND provisional = 0"
        )
    }
    pending = [e for e in entries if e["id"] not in known]
    report["already_done"] = len(entries) - len(pending)

    if limit:
        pending = pending[:limit]
    if not pending:
        print("  nothing new in the catalogue")
        return report

    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    def load(entry: dict):
        text_id = entry["id"]
        if mirror:
            raw = read_from_mirror(text_id, mirror)
            if raw:
                return entry, raw
        if cache_dir:
            cached = cache_dir / f"{text_id}.txt"
            if cached.exists():
                return entry, cached.read_text(encoding="utf-8", errors="replace")
        raw = fetch_text(text_id, min_gap=min_gap)
        if raw and cache_dir:
            (cache_dir / f"{text_id}.txt").write_text(raw, encoding="utf-8", errors="replace")
        return entry, raw

    bar = Progress("gutenberg", total=len(pending))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(load, entry) for entry in pending]
        for future in as_completed(futures):
            bar.advance()
            try:
                entry, raw = future.result()
            except Exception:  # noqa: BLE001 — one bad text must not end the run
                report["failed"] += 1
                continue

            if not raw:
                report["failed"] += 1
                continue

            body = strip_boilerplate(raw)
            if len(body) < 20_000:
                # Too short to measure reliably, or the fetch returned an
                # error page that happened to be long enough to pass earlier.
                report["skipped"] += 1
                continue

            existing = db.find_book(conn, entry["title"], entry["author"])
            book = existing or Book(
                title=entry["title"],
                author=entry["author"],
                year=entry["year"],
                subjects=entry["subjects"],
            )
            book.gutenberg_id = entry["id"]
            if not book.subjects:
                book.subjects = entry["subjects"]
            if not book.year:
                book.year = entry["year"]

            try:
                book = measure_from_text(book, body)
            except Exception:  # noqa: BLE001
                report["failed"] += 1
                continue

            book.imprint = classify(
                book.publisher, book.isbn13, book.title, book.author
            ).as_dict()
            db.upsert_book(conn, book)
            report["measured"] += 1

    bar.close(f"({report['measured']:,} measured)")
    return report
