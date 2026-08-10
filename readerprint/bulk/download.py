"""
Resumable downloads and pipeline state.

The Open Library editions dump is around 9 GB compressed and the Gutenberg
corpus is tens of thousands of files. Any run long enough to matter will be
interrupted — by a dropped connection, a closed laptop, or a change of mind.
So everything here assumes interruption is normal: downloads resume from a
byte offset, and each stage records what it finished so a rerun skips it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

USER_AGENT = "Readerprint/0.1 (pilot corpus builder; contact via repository issues)"
CHUNK = 1 << 20  # 1 MiB


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

class PipelineState:
    """
    A small JSON file recording which stages have completed.

    Deliberately not in SQLite: if the database is wiped mid-experiment the
    state should be wiped with it, but if a stage crashes the record of the
    stages before it should survive.
    """

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.data = {}

    def done(self, stage: str) -> bool:
        return bool(self.data.get(stage, {}).get("complete"))

    def get(self, stage: str, key: str, default=None):
        return self.data.get(stage, {}).get(key, default)

    def mark(self, stage: str, complete: bool = True, **details) -> None:
        entry = self.data.setdefault(stage, {})
        entry["complete"] = complete
        entry["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        entry.update(details)
        self.save()

    def clear(self, stage: str) -> None:
        self.data.pop(stage, None)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------

@dataclass
class Progress:
    """Single-line progress that works over SSH and in a plain terminal."""

    label: str
    total: int | None = None
    unit: str = ""
    every: float = 0.5
    count: int = 0
    _last: float = field(default=0.0, repr=False)
    _start: float = field(default_factory=time.monotonic, repr=False)

    def advance(self, n: int = 1) -> None:
        self.count += n
        now = time.monotonic()
        if now - self._last < self.every:
            return
        self._last = now
        self._render()

    def _render(self) -> None:
        elapsed = max(0.001, time.monotonic() - self._start)
        rate = self.count / elapsed
        if self.total:
            pct = min(100.0, self.count / self.total * 100)
            bar_width = 24
            filled = int(bar_width * pct / 100)
            bar = "#" * filled + "." * (bar_width - filled)
            remaining = (self.total - self.count) / rate if rate > 0 else 0
            tail = f"{pct:5.1f}%  eta {_human_time(remaining)}"
            body = f"[{bar}] {self.count:,}/{self.total:,}{self.unit}  {tail}"
        else:
            body = f"{self.count:,}{self.unit}  {rate:,.0f}/s  {_human_time(elapsed)} elapsed"
        print(f"\r  {self.label}: {body}   ", end="", flush=True)

    def close(self, note: str = "") -> None:
        self._render()
        elapsed = time.monotonic() - self._start
        print(f"\r  {self.label}: {self.count:,}{self.unit} in {_human_time(elapsed)}"
              f"{'  ' + note if note else ''}" + " " * 30, flush=True)


def _human_time(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def download(url: str, target: Path, force: bool = False, timeout: int = 60,
             attempts: int = 8) -> Path:
    """
    Fetch a file, resuming a partial download where the server allows it.

    Writes to a .part file and renames on success, so an interrupted run can
    never leave a truncated file that looks complete to the next stage.

    A multi-gigabyte transfer over a domestic connection will drop. It is not
    a question of whether. So a broken connection is retried automatically
    from the byte already on disk rather than being raised at the caller, who
    can only start again from zero.
    """
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return _download_once(url, target, force and attempt == 1, timeout)
        except (requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.HTTPError) as error:
            # A 4xx will never succeed on retry; only wait out transient ones.
            status = getattr(getattr(error, "response", None), "status_code", None)
            if status and 400 <= status < 500 and status not in (408, 429):
                raise

            last_error = error
            if attempt == attempts:
                break

            pause = min(60, 2 ** attempt)
            print(f"\n  connection dropped ({type(error).__name__}), "
                  f"retry {attempt}/{attempts - 1} in {pause}s")
            time.sleep(pause)

    raise RuntimeError(
        f"Gave up on {target.name} after {attempts} attempts. "
        f"The partial file is kept, so rerunning resumes from there. "
        f"Last error: {last_error}"
    )


def _download_once(url: str, target: Path, force: bool, timeout: int) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")

    if target.exists() and not force:
        print(f"  {target.name} already present ({human_size(target.stat().st_size)}), skipping")
        return target

    existing = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": USER_AGENT}
    if existing:
        headers["Range"] = f"bytes={existing}-"
        print(f"  resuming {target.name} from {human_size(existing)}")

    response = requests.get(url, headers=headers, stream=True, timeout=timeout)

    if existing and response.status_code == 200:
        # Server ignored the range request, so start over rather than
        # appending fresh bytes onto a partial file and corrupting it.
        print("  server does not support resume, restarting")
        existing = 0
        part.unlink(missing_ok=True)
    elif existing and response.status_code == 416:
        # Already have the whole thing.
        part.rename(target)
        return target

    response.raise_for_status()

    declared = response.headers.get("Content-Length")
    total = (int(declared) + existing) if declared else None

    bar = Progress(target.name, total=total, unit="B")
    bar.count = existing

    mode = "ab" if existing else "wb"
    with open(part, mode) as handle:
        for chunk in response.iter_content(CHUNK):
            if chunk:
                handle.write(chunk)
                bar.advance(len(chunk))
    bar.close()

    if total and part.stat().st_size < total:
        raise requests.exceptions.ChunkedEncodingError(
            f"got {part.stat().st_size} of {total} bytes"
        )

    part.rename(target)
    return target


def head_size(url: str) -> int | None:
    try:
        response = requests.head(
            url, headers={"User-Agent": USER_AGENT}, timeout=30, allow_redirects=True
        )
        length = response.headers.get("Content-Length")
        return int(length) if length else None
    except (requests.RequestException, ValueError):
        return None


def free_space(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize