# Readerprint

A book recommender that reads the prose.

Most recommendation tools work from ratings and co-purchases, which is why
they keep handing you the book everyone was loudest about. Readerprint works
from your whole reading history and from measurements of how books are
actually written — sentence rhythm, dialogue balance, point of view, tense,
and how often the writing reaches for a phrase that has been used a thousand
times before.

It also tracks what you abandoned and why, which no other tool does. That is
the signal this project is really built on.

**Status: pilot.** Working end to end, corpus is small, plenty of rough
edges. Not deployed anywhere; run it locally.

---

## Running it

Requires Python 3.11 or newer.

```bash
git clone <your-repo-url> readerprint
cd readerprint
pip install -r requirements.txt

python build.py --quick                  # corpus, about ten minutes
python app.py                            # http://127.0.0.1:8000
```

`build.py` is the only command you need. It runs every stage in order and
records what finished, so an interrupted run resumes rather than restarting.

```bash
python build.py --quick     a few hundred measured books, ~10 minutes
python build.py             a few thousand measured books plus broad metadata
python build.py --full      everything available; hours, and tens of gigabytes
python build.py --status    what has completed so far
```

### The stages

| Stage | Source | What it gives you |
|---|---|---|
| `seed` | bundled | 90 curated books, instantly |
| `gutenberg` | Project Gutenberg | public domain texts, **measured for real** |
| `openlibrary` | monthly dumps | metadata for tens of thousands of books |
| `ratings` | Open Library | aggregate reader ratings |
| `report` | — | what the corpus now contains |

Gutenberg runs before Open Library on purpose. It is the only source that
produces measured books, and measured books are what make everything
downstream work. Open Library adds breadth, but every book it contributes
arrives provisional until someone measures it.

Run a single stage, or tune one:

```bash
python build.py --stages gutenberg --gutenberg-limit 2000
python build.py --stages openlibrary --promote-limit 50000
python build.py --reset                  # start over
```

### Going large

For tens of thousands of Gutenberg texts, mirror the archive first. It is
far faster than HTTP and much kinder to a charity running on donated
bandwidth:

```bash
rsync -av --del ftp@aleph.gutenberg.org::gutenberg-epub /your/mirror
python build.py --stages gutenberg --mirror /your/mirror --gutenberg-limit 0
```

Open Library asks that bulk users take the monthly dumps rather than hit the
search API, which is what the `openlibrary` stage does. The editions dump is
around 9 GB compressed; nothing is decompressed to disk and no dump is held
in memory, so the stage runs in a few hundred megabytes regardless. Dumps are
deleted after ingestion unless you pass `--keep-dumps`.

---

## Using it

**Measure** — paste any passage and see what the prose does. Works on a
sample chapter, a preview, anything you are deciding about.

**Shelf** — import a Goodreads or StoryGraph CSV export, or add books by
hand. Then set a verdict on each. For anything you abandoned or disliked,
say why: the reason chips are what make the recommendations sharp.

**Recommendations** — appear once three books are rated. Each card explains
its own reasoning. Click through for the full measurements and an excerpt.

Getting the export:

- **Goodreads** — My Books → Import and Export → Export Library
- **StoryGraph** — Manage Account → Export StoryGraph Library

---

## How it works

### Prose measurement (`readerprint/style.py`)

Dialogue is stripped before anything else. Without that step, a third-person
novel with a chatty narrator reads as first person and the tense detector is
a coin flip.

Measured per book: point of view, tense, mean sentence length and variation,
dialogue share, lexical variety (sliding-window type-token ratio, so long
texts and short snippets stay comparable), subordination rate, adverb rate,
an ornament index, a stock-phrase rate, and Flesch-Kincaid grade. These
combine into a single 0–100 **density** figure.

Density is not quality. Plain prose scores low and is a legitimate
preference. The app never says a book is good.

Deterministic and dependency-free — no model downloads, no API keys, same
numbers on every machine.

### Publisher classification (`readerprint/imprint.py`)

The original plan for this project was a Wattpad scraper: check the title,
flag it if found. That is replaced here, for three reasons. Title collisions
in romance are constant, Wattpad blocks automated access, and books get
retitled between web and print. It also measures the wrong thing — Wattpad
origin was a proxy for prose craft, and prose craft is measurable directly.

Instead: publisher tier from the imprint name and the ISBN registration
group (979-8 is overwhelmingly print-on-demand), and web-serial provenance
from `data/provenance.json`, a hand-curated file where every entry carries a
citation. Contributions welcome, with a source.

Origin is disclosed as context, not as a verdict. Several books on that list
were substantially rewritten and professionally edited before print.

### Review scoring (`readerprint/reviews.py`)

Reviews are ranked on **informativeness**, not sentiment. The score rewards
concessive structure ("but", "that said", "my only issue"), craft vocabulary,
comparisons to other books, and middle ratings. It penalises pure reaction,
all-caps, emoji density, and plot summary.

In testing, a three-star review discussing pacing and voice scored 80/100,
while a five-star review with 2,100 community votes reading "OBSESSED!!! I
sobbed" scored zero and was dropped.

Aggregate ratings are shrunk toward a corpus prior weighted by volume, so a
4.8 from thirty readers does not outrank a 4.3 from forty thousand. Where
timestamps are available it also reports **hype decay** — the gap between a
book's first-six-weeks rating and its settled rating.

### The recommender (`readerprint/recommend.py`)

Each book becomes a vector in two blocks: measured style, and TF-IDF over
description and subjects reduced by SVD. A reader becomes a weighted centroid
of loved books minus a centroid of abandoned ones. Results pass through
maximal marginal relevance so the list is not twelve variations of one book.

DNF reasons map to style axes, but only fire where the abandoned books
measurably scored higher on that axis than the loved ones. Without that
check the taxonomy misfires: someone who abandoned two plainly written
romances "for the writing" would be steered away from ornate prose, when
their shelf is full of it and the real complaint was stock phrasing.

TF-IDF rather than neural embeddings because it runs offline and is
reproducible. Swapping in sentence embeddings means changing one function.

---

## What is deliberately not here

**Reader clustering.** Grouping readers into types needs a user base to be
meaningful, and a pilot shared with friends does not have one. The vector
representation is what clustering would run on, so nothing needs rebuilding
to add it later.

**Goodreads scraping.** The API was withdrawn in 2020 and scraping it is an
arms race a hobby project loses. Sources here are Open Library, Google
Books, and Project Gutenberg — all open, all permitted.

**Bundled copyrighted text.** No excerpt from an in-copyright book ships in
this repository. Public domain texts are fetched and measured in full;
for everything else the app uses the publisher-supplied Google Books
snippet at display time, or prose you paste in yourself, which stays local.

---

## Known limitations

- The corpus will be lopsided for a while: thousands of measured public
  domain classics, but contemporary titles arrive as metadata only. Copyright
  makes this unavoidable — nobody publishes prose measurements, and computing
  them needs the text. The classics still earn their place by calibrating the
  style space, so a single pasted page from a 2024 novel lands accurately.
- Style values are placeholders until a book has been measured, and are
  labelled "not yet measured" everywhere they appear.
- POV and tense detection use lexical heuristics, not a POS tagger. They are
  reliable on 300+ words of narration and shaky below that.
- Goodreads exports do not record abandonment, so DNFs are inferred from
  custom shelf names (`dnf`, `did-not-finish`, and similar). Anything else
  needs marking by hand.
- The cliché lexicon is tuned toward English-language romance and web
  fiction. It under-reports stock phrasing in other genres.
- Single user. `?user=` exists but is not authentication.

---

## Layout

```
build.py                   corpus builder — the one file to run
app.py                     FastAPI server and API
readerprint/
  bulk/
    download.py            resumable downloads, progress, pipeline state
    openlibrary.py         dump streaming, fiction filter, edition scoring
    gutenberg.py           catalogue, text retrieval, measurement
  style.py                 prose measurement
  imprint.py               publisher tier and provenance
  reviews.py               informativeness scoring, weighted ratings
  recommend.py             vectors, taste profile, MMR
  ingest.py                Goodreads and StoryGraph CSV import
  sources.py               Open Library, Google Books, Gutenberg
  corpus.py                seed loading and enrichment
  models.py                Book, ReadingEvent, DNF taxonomy
  db.py                    SQLite
static/                    interface, no build step
data/
  seed_books.json          starter corpus
  provenance.json          curated web-serial origins
scripts/make_seed.py       regenerate the seed
```

## Contributing

The most valuable contributions are corpus entries and provenance records.
For provenance, include a source. For books, running `corpus enrich` and
committing measured values helps everyone.

## Tests

```bash
python tests/test_readerprint.py     # core: style, imprint, reviews, import, recommender
python tests/test_bulk.py            # bulk pipeline, against offline fixtures
```

The bulk tests build small files in the real Open Library and Gutenberg
formats and run the parsers over them, so they cover format quirks, the
fiction filters, edition scoring and streaming behaviour without depending on
a multi-gigabyte download.

The core suite has forty-six checks covering prose measurement, publisher classification, review
ranking, CSV import, the recommender, and storage — including guards on two
bugs found during the build: a stored value shadowing a method of the same
name, and DNF penalties once applying in the wrong direction.

## Licence

MIT. The curated provenance records in `data/provenance.json` are factual
statements with citations, not opinions, and corrections are welcome.
