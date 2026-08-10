#!/usr/bin/env python3
"""
Readerprint corpus builder — the one file to run.

    python build.py                     everything, sensible defaults
    python build.py --quick             a few hundred books, about ten minutes
    python build.py --full              the whole thing, hours and ~60GB
    python build.py --stages gutenberg  just one stage
    python build.py --status            what is done so far

Stages run in order and each records its completion, so an interrupted run
picks up where it stopped. Rerunning a finished stage is a no-op unless you
pass --redo.

    seed        the small curated starter corpus
    gutenberg   public domain texts, measured for real
    openlibrary metadata for tens of thousands of books
    ratings     Open Library reader ratings
    report      what the corpus now contains

Order matters. Gutenberg runs before Open Library because it produces
measured books, and measured books are what make everything downstream work.
Open Library adds breadth but every one of its books arrives provisional.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from readerprint import db  # noqa: E402
from readerprint.bulk import gutenberg, openlibrary  # noqa: E402
from readerprint.bulk.download import (  # noqa: E402
    PipelineState, free_space, head_size, human_size,
)

DATA_DIR = BASE_DIR / "data"
DUMP_DIR = DATA_DIR / "dumps"
CACHE_DIR = DATA_DIR / "gutenberg_cache"
STATE_PATH = DATA_DIR / "build_state.json"

STAGES = ["seed", "gutenberg", "openlibrary", "ratings", "repair", "report"]

PRESETS = {
    "quick": {
        "gutenberg_limit": 300,
        "openlibrary": False,
        "ratings": False,
        "note": "a few hundred measured books, roughly ten minutes",
    },
    "standard": {
        "gutenberg_limit": 3000,
        "openlibrary": True,
        "openlibrary_limit": 400_000,
        "promote_limit": 60_000,
        "ratings": True,
        "note": "a few thousand measured books plus broad metadata",
    },
    "full": {
        "gutenberg_limit": None,
        "openlibrary": True,
        "openlibrary_limit": None,
        "promote_limit": None,
        "ratings": True,
        "note": "everything available; hours of work and tens of gigabytes",
    },
}


def banner(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------

def stage_seed(conn, args, state) -> None:
    banner("Seed corpus")
    from readerprint.corpus import load_seed

    seed_file = DATA_DIR / "seed_books.json"
    if not seed_file.exists():
        print("  seed_books.json missing, generating it")
        import subprocess
        subprocess.run(
            [sys.executable, str(BASE_DIR / "scripts" / "make_seed.py")], check=True
        )

    added = load_seed(conn)
    print(f"  {added} books added, {len(db.all_books(conn))} in corpus")
    state.mark("seed", added=added)


def stage_gutenberg(conn, args, state) -> None:
    banner("Project Gutenberg")
    print("  Public domain texts. These arrive measured, not provisional.")

    catalog = gutenberg.fetch_catalog(DUMP_DIR, force=args.redo)
    entries = gutenberg.read_catalog(catalog)
    print(f"  {len(entries):,} English prose fiction texts in the catalogue")

    limit = args.gutenberg_limit
    if limit:
        print(f"  fetching up to {limit:,} this run")

    if not args.mirror and (limit is None or limit > 2000):
        estimate = (limit or len(entries)) * args.min_gap / max(1, args.workers) / 60
        print(f"  estimated time over HTTP: about {estimate:.0f} minutes")
        print("  a local rsync mirror is much faster for volumes this size:")
        print("    rsync -av --del ftp@aleph.gutenberg.org::gutenberg-epub /your/mirror")
        print("    then rerun with --mirror /your/mirror")
        if not confirm("  continue over HTTP?", args.yes):
            print("  skipped")
            return

    report = gutenberg.ingest(
        conn,
        catalog,
        limit=limit,
        workers=args.workers,
        mirror=Path(args.mirror) if args.mirror else None,
        min_gap=args.min_gap,
        cache_dir=None if args.no_cache else CACHE_DIR,
    )
    print(f"  measured {report['measured']:,}  "
          f"skipped {report['skipped']:,}  failed {report['failed']:,}")

    complete = limit is None and report["failed"] < report["catalogue"] * 0.2
    state.mark("gutenberg", complete=complete, **report)


def stage_openlibrary(conn, args, state) -> None:
    banner("Open Library")
    print("  Metadata at scale. These books arrive provisional until measured.")

    needed = ["works", "editions"]
    total = sum(head_size(f"{openlibrary.BASE}/{openlibrary.FILES[n]}") or 0 for n in needed)
    if total:
        print(f"  downloads total about {human_size(total)}")
        available = free_space(DUMP_DIR)
        # Decompression is streamed, but the compressed files stay on disk.
        if available < total * 1.4:
            print(f"  only {human_size(available)} free — need roughly "
                  f"{human_size(total * 1.4)}")
            if not confirm("  continue anyway?", args.yes):
                print("  skipped")
                return

    if not confirm("  download and ingest?", args.yes):
        print("  skipped")
        return

    # Authors first. Works reference authors by key, and without the names
    # every promoted book ends up credited to nobody.
    authors_path = openlibrary.fetch_dump("authors", DUMP_DIR, force=args.redo)
    names = openlibrary.ingest_authors(conn, authors_path, limit=args.openlibrary_limit)
    print(f"  {names:,} author names loaded")

    works_path = openlibrary.fetch_dump("works", DUMP_DIR, force=args.redo)
    kept = openlibrary.ingest_works(conn, works_path, limit=args.openlibrary_limit)
    print(f"  {kept:,} fiction works staged")

    editions_path = openlibrary.fetch_dump("editions", DUMP_DIR, force=args.redo)
    matched = openlibrary.ingest_editions(conn, editions_path, limit=args.openlibrary_limit)
    print(f"  {matched:,} candidate editions considered")

    added = openlibrary.promote(conn, min_score=args.min_score, limit=args.promote_limit)
    print(f"  {added:,} books promoted into the corpus")

    if not args.keep_dumps:
        for path in (authors_path, works_path, editions_path):
            path.unlink(missing_ok=True)
        print("  dumps removed (pass --keep-dumps to keep them)")

    state.mark("openlibrary", complete=args.openlibrary_limit is None,
               works=kept, editions=matched, promoted=added)


def stage_ratings(conn, args, state) -> None:
    banner("Reader ratings")
    path = openlibrary.fetch_dump("ratings", DUMP_DIR, force=args.redo)
    updated = openlibrary.ingest_ratings(conn, path)
    print(f"  {updated:,} books given an aggregate rating")
    if not args.keep_dumps:
        path.unlink(missing_ok=True)
    state.mark("ratings", updated=updated)


def stage_repair(conn, args, state) -> None:
    """
    Fix books left without an author by an earlier build.

    Cheap to run and a no-op on a healthy corpus, so it sits in the default
    stage list rather than being something you have to know about.
    """
    banner("Repair")
    missing = conn.execute(
        "SELECT COUNT(*) AS n FROM books WHERE author IS NULL OR author = ''"
    ).fetchone()["n"]

    if not missing:
        print("  every book has an author, nothing to do")
        state.mark("repair", fixed=0)
        return

    print(f"  {missing:,} books have no author")
    fixed = 0

    # Two routes to the same answer, with very different costs. The authors
    # dump is ~780 MB; the API is one small request per book. Below the
    # threshold the API wins by a wide margin, and it also works when the
    # staging tables are absent or were written before author keys were
    # stored — which is the situation any corpus built by an earlier version
    # is actually in.
    use_api = args.author_source == "api" or (
        args.author_source == "auto" and missing <= args.api_threshold
    )

    if use_api:
        estimate = missing * 2 * 0.4 / 60
        print(f"  resolving via the Open Library API, roughly {estimate:.0f} minutes")
        print(f"  (pass --author-source dump to download the {'780MB'} dump instead)")
        fixed = openlibrary.backfill_authors_via_api(conn, min_gap=args.min_gap * 0.4)
    else:
        print("  too many to resolve one at a time, using the authors dump")
        staged = conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master "
            "WHERE type='table' AND name='ol_authors'"
        ).fetchone()["n"]
        have_names = 0
        if staged:
            have_names = conn.execute(
                "SELECT COUNT(*) AS n FROM ol_authors"
            ).fetchone()["n"]

        if not have_names:
            path = openlibrary.fetch_dump("authors", DUMP_DIR, force=False)
            have_names = openlibrary.ingest_authors(conn, path)
            print(f"  {have_names:,} author names loaded")
            if not args.keep_dumps:
                path.unlink(missing_ok=True)

        fixed = openlibrary.backfill_authors(conn)

        # The dump route depends on staged author keys, which a corpus built
        # by an older version will not have. Fall back rather than declaring
        # success on nothing.
        if fixed == 0:
            print("  no author keys were staged, falling back to the API")
            fixed = openlibrary.backfill_authors_via_api(
                conn, min_gap=args.min_gap * 0.4
            )

    print(f"  {fixed:,} books repaired")

    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM books WHERE author IS NULL OR author = ''"
    ).fetchone()["n"]

    if remaining:
        print(f"  {remaining:,} still unresolved")
        if args.drop_unfixable:
            cursor = conn.execute(
                "DELETE FROM books WHERE (author IS NULL OR author = '') "
                "AND provisional = 1 AND id NOT IN (SELECT book_id FROM events)"
            )
            conn.commit()
            print(f"  {cursor.rowcount:,} unfixable records removed")
        else:
            print("  pass --drop-unfixable to remove them")

    state.mark("repair", complete=remaining == 0, fixed=fixed, remaining=remaining)


def stage_report(conn, args, state) -> None:
    banner("Corpus")
    books = db.all_books(conn)
    if not books:
        print("  empty")
        return

    measured = [b for b in books if not b.provisional]
    with_isbn = sum(1 for b in books if b.isbn13)
    with_rating = sum(1 for b in books if (b.ratings or {}).get("weighted_score"))
    translated = sum(1 for b in books if b.is_translated)

    print(f"  books                 {len(books):,}")
    print(f"  measured from text    {len(measured):,} "
          f"({len(measured) / len(books) * 100:.1f}%)")
    print(f"  with an ISBN          {with_isbn:,}")
    print(f"  with a rating         {with_rating:,}")
    print(f"  translated            {translated:,}")

    nameless = sum(1 for b in books if not (b.author or "").strip())
    if nameless:
        print(f"  MISSING AN AUTHOR     {nameless:,}  <- run: python build.py --stages repair")

    if measured:
        densities = [b.style.get("prose_density", 0) for b in measured]
        povs: dict[str, int] = {}
        for b in measured:
            key = b.style.get("pov", "unknown")
            povs[key] = povs.get(key, 0) + 1
        print(f"  density range         {min(densities):.0f} to {max(densities):.0f} "
              f"(mean {sum(densities) / len(densities):.0f})")
        print("  narration             " + ", ".join(
            f"{k} {v:,}" for k, v in sorted(povs.items(), key=lambda x: -x[1])
        ))

    if len(measured) < 500:
        print("\n  The measured corpus is still small. Recommendations lean on")
        print("  placeholder values below roughly 500 measured books.")
        print("  Run: python build.py --stages gutenberg --gutenberg-limit 2000")

    state.mark("report", books=len(books), measured=len(measured))


STAGE_FUNCTIONS = {
    "seed": stage_seed,
    "gutenberg": stage_gutenberg,
    "openlibrary": stage_openlibrary,
    "ratings": stage_ratings,
    "repair": stage_repair,
    "report": stage_report,
}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Readerprint corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--quick", action="store_true",
                        help=PRESETS["quick"]["note"])
    parser.add_argument("--full", action="store_true",
                        help=PRESETS["full"]["note"])
    parser.add_argument("--stages", help="comma-separated subset, e.g. gutenberg,report")
    parser.add_argument("--status", action="store_true", help="show progress and exit")
    parser.add_argument("--redo", action="store_true", help="rerun finished stages")
    parser.add_argument("--reset", action="store_true",
                        help="delete the database and start over")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="answer yes to prompts (for unattended runs)")

    group = parser.add_argument_group("tuning")
    group.add_argument("--gutenberg-limit", type=int)
    group.add_argument("--openlibrary-limit", type=int,
                       help="stop after this many dump records (for testing)")
    group.add_argument("--promote-limit", type=int)
    group.add_argument("--min-score", type=int, default=1,
                       help="minimum edition completeness to promote (default 1)")
    group.add_argument("--mirror", help="path to a local Gutenberg rsync mirror")
    group.add_argument("--workers", type=int, default=3)
    group.add_argument("--min-gap", type=float, default=1.0,
                       help="seconds between Gutenberg requests (default 1.0)")
    group.add_argument("--keep-dumps", action="store_true")
    group.add_argument("--author-source", choices=["auto", "api", "dump"],
                       default="auto",
                       help="how repair resolves missing authors (default auto: "
                            "API for small numbers, dump for large)")
    group.add_argument("--api-threshold", type=int, default=3000,
                       help="above this many missing authors, auto uses the dump")
    group.add_argument("--drop-unfixable", action="store_true",
                       help="during repair, delete provisional books that still "
                            "have no author and are not on anyone's shelf")
    group.add_argument("--no-cache", action="store_true",
                       help="do not keep downloaded Gutenberg texts on disk")

    args = parser.parse_args()

    preset = PRESETS["quick"] if args.quick else PRESETS["full"] if args.full else PRESETS["standard"]
    for key in ("gutenberg_limit", "openlibrary_limit", "promote_limit"):
        if getattr(args, key, None) is None:
            setattr(args, key, preset.get(key))
    args.preset = preset
    args.run_openlibrary = preset.get("openlibrary", True)
    args.run_ratings = preset.get("ratings", True)
    return args


def show_status(state: PipelineState, conn) -> None:
    banner("Pipeline status")
    for stage in STAGES:
        entry = state.data.get(stage, {})
        if not entry:
            mark, detail = "  ", "not run"
        elif entry.get("complete"):
            mark, detail = "ok", entry.get("updated", "")
        else:
            mark, detail = "..", f"partial, {entry.get('updated', '')}"
        print(f"  [{mark}] {stage:<12} {detail}")

    books = db.all_books(conn)
    measured = sum(1 for b in books if not b.provisional)
    print(f"\n  {len(books):,} books, {measured:,} measured")


def main() -> int:
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.reset:
        if confirm("Delete the database and all pipeline state?", args.yes):
            (DATA_DIR / "readerprint.db").unlink(missing_ok=True)
            STATE_PATH.unlink(missing_ok=True)
            print("Reset.")
        else:
            return 0

    conn = db.connect()
    db.init(conn)
    state = PipelineState(STATE_PATH)

    if args.status:
        show_status(state, conn)
        return 0

    if args.stages:
        requested = [s.strip() for s in args.stages.split(",") if s.strip()]
        unknown = set(requested) - set(STAGES)
        if unknown:
            print(f"Unknown stage(s): {', '.join(sorted(unknown))}")
            print(f"Available: {', '.join(STAGES)}")
            return 1
        # Keep declared order regardless of how they were typed.
        selected = [s for s in STAGES if s in requested]
    else:
        selected = list(STAGES)
        if not args.run_openlibrary:
            selected.remove("openlibrary")
        if not args.run_ratings and "ratings" in selected:
            selected.remove("ratings")

    print(f"Readerprint corpus builder — {args.preset['note']}")
    print(f"Stages: {' -> '.join(selected)}")

    started = time.monotonic()
    for stage in selected:
        if state.done(stage) and not args.redo and stage != "report":
            print(f"\n{stage}: already complete, skipping (use --redo to rerun)")
            continue
        try:
            STAGE_FUNCTIONS[stage](conn, args, state)
        except KeyboardInterrupt:
            print("\n\nInterrupted. Progress is saved — rerun to continue.")
            return 130
        except Exception as error:  # noqa: BLE001
            print(f"\n  {stage} failed: {type(error).__name__}: {error}")
            state.mark(stage, complete=False, error=str(error)[:200])
            if stage in ("openlibrary", "ratings", "gutenberg"):
                print("  continuing with the remaining stages")
                continue
            return 1

    elapsed = time.monotonic() - started
    print(f"\nDone in {elapsed / 60:.1f} minutes.")
    print("Start the app with: python app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())