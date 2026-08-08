"""
Generate data/seed_books.json.

Two kinds of entry:

  Public domain  carries a Gutenberg id, so scripts/build_corpus.py can
                 download the real text and measure it properly.
  In copyright   metadata only. No text ships in this repository.

Every entry also carries provisional style values so the app is usable the
moment it is cloned, before anything is fetched. Provisional values are marked
as such everywhere they appear in the interface, and are overwritten the first
time real prose is measured. Point of view and tense are stated facts about
each book; the numeric values are coarse editorial estimates and are treated
as placeholders, not measurements.
"""

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# density, sentence length, ornament, dialogue, cliche
# Coarse priors only. Replaced by measurement on first enrichment.
BANDS = {
    "spare":     dict(prose_density=22, mean_sentence_length=11, sentence_length_sd=6,
                      ornament_index=12, dialogue_share=0.28, cliche_rate=0.5,
                      subordination_rate=0.4, grade_level=5.5, lexical_variety=0.42),
    "plain":     dict(prose_density=36, mean_sentence_length=15, sentence_length_sd=8,
                      ornament_index=22, dialogue_share=0.24, cliche_rate=1.0,
                      subordination_rate=0.7, grade_level=7.5, lexical_variety=0.45),
    "measured":  dict(prose_density=52, mean_sentence_length=19, sentence_length_sd=11,
                      ornament_index=34, dialogue_share=0.18, cliche_rate=0.8,
                      subordination_rate=1.0, grade_level=9.5, lexical_variety=0.48),
    "textured":  dict(prose_density=68, mean_sentence_length=24, sentence_length_sd=14,
                      ornament_index=50, dialogue_share=0.13, cliche_rate=0.6,
                      subordination_rate=1.4, grade_level=11.5, lexical_variety=0.52),
    "dense":     dict(prose_density=84, mean_sentence_length=31, sentence_length_sd=19,
                      ornament_index=66, dialogue_share=0.08, cliche_rate=0.4,
                      subordination_rate=1.9, grade_level=14.0, lexical_variety=0.56),
}

# title, author, year, pov, tense, band, gutenberg_id, subjects
CLASSICS = [
    ("Pride and Prejudice", "Jane Austen", 1813, "third", "past", "measured", 1342,
     ["classics", "romance", "social satire", "regency"]),
    ("Jane Eyre", "Charlotte Brontë", 1847, "first", "past", "textured", 1260,
     ["classics", "gothic", "bildungsroman"]),
    ("Wuthering Heights", "Emily Brontë", 1847, "first", "past", "textured", 768,
     ["classics", "gothic", "frame narrative"]),
    ("Frankenstein", "Mary Shelley", 1818, "first", "past", "textured", 84,
     ["classics", "gothic", "science fiction", "frame narrative"]),
    ("Dracula", "Bram Stoker", 1897, "first", "past", "measured", 345,
     ["classics", "gothic", "horror", "epistolary"]),
    ("The Strange Case of Dr Jekyll and Mr Hyde", "Robert Louis Stevenson", 1886,
     "third", "past", "measured", 43, ["classics", "gothic", "novella"]),
    ("Moby-Dick", "Herman Melville", 1851, "first", "past", "dense", 2701,
     ["classics", "sea", "philosophical", "digressive"]),
    ("Great Expectations", "Charles Dickens", 1861, "first", "past", "textured", 1400,
     ["classics", "bildungsroman", "victorian"]),
    ("A Tale of Two Cities", "Charles Dickens", 1859, "third", "past", "textured", 98,
     ["classics", "historical", "revolution"]),
    ("The Picture of Dorian Gray", "Oscar Wilde", 1890, "third", "past", "textured", 174,
     ["classics", "gothic", "aestheticism"]),
    ("The Adventures of Sherlock Holmes", "Arthur Conan Doyle", 1892, "first", "past",
     "plain", 1661, ["classics", "mystery", "short stories", "detective"]),
    ("The Hound of the Baskervilles", "Arthur Conan Doyle", 1902, "first", "past",
     "plain", 2852, ["classics", "mystery", "detective", "gothic"]),
    ("Heart of Darkness", "Joseph Conrad", 1899, "first", "past", "dense", 219,
     ["classics", "colonialism", "novella", "frame narrative"]),
    ("The Great Gatsby", "F. Scott Fitzgerald", 1925, "first", "past", "textured", 64317,
     ["classics", "jazz age", "american", "tragedy"]),
    ("Alice's Adventures in Wonderland", "Lewis Carroll", 1865, "third", "past",
     "plain", 11, ["classics", "children", "nonsense", "fantasy"]),
    ("The Time Machine", "H. G. Wells", 1895, "first", "past", "measured", 35,
     ["classics", "science fiction", "novella"]),
    ("The War of the Worlds", "H. G. Wells", 1898, "first", "past", "measured", 36,
     ["classics", "science fiction", "invasion"]),
    ("Crime and Punishment", "Fyodor Dostoevsky", 1866, "third", "past", "dense", 2554,
     ["classics", "russian", "psychological", "translated"]),
    ("Anna Karenina", "Leo Tolstoy", 1878, "third", "past", "textured", 1399,
     ["classics", "russian", "translated", "domestic"]),
    ("The Metamorphosis", "Franz Kafka", 1915, "third", "past", "measured", 5200,
     ["classics", "absurdism", "novella", "translated"]),
    ("Dubliners", "James Joyce", 1914, "third", "past", "measured", 2814,
     ["classics", "short stories", "irish", "modernism"]),
    ("The Turn of the Screw", "Henry James", 1898, "first", "past", "dense", 209,
     ["classics", "ghost story", "novella", "unreliable narrator"]),
    ("The Age of Innocence", "Edith Wharton", 1920, "third", "past", "textured", 541,
     ["classics", "society", "american"]),
    ("Emma", "Jane Austen", 1815, "third", "past", "measured", 158,
     ["classics", "romance", "social satire"]),
    ("Persuasion", "Jane Austen", 1817, "third", "past", "measured", 105,
     ["classics", "romance", "second chance"]),
    ("Treasure Island", "Robert Louis Stevenson", 1883, "first", "past", "plain", 120,
     ["classics", "adventure", "pirates", "children"]),
    ("The Adventures of Huckleberry Finn", "Mark Twain", 1884, "first", "past",
     "plain", 76, ["classics", "american", "vernacular", "river"]),
    ("Little Women", "Louisa May Alcott", 1868, "third", "past", "measured", 514,
     ["classics", "domestic", "sisters", "american"]),
    ("The Scarlet Letter", "Nathaniel Hawthorne", 1850, "third", "past", "dense", 33,
     ["classics", "american", "puritan", "allegory"]),
    ("Madame Bovary", "Gustave Flaubert", 1856, "third", "past", "textured", 2413,
     ["classics", "french", "translated", "provincial"]),
    ("The Count of Monte Cristo", "Alexandre Dumas", 1844, "third", "past", "measured",
     1184, ["classics", "adventure", "revenge", "translated"]),
    ("Anne of Green Gables", "L. M. Montgomery", 1908, "third", "past", "plain", 45,
     ["classics", "children", "canadian", "orphan"]),
    ("The Secret Garden", "Frances Hodgson Burnett", 1911, "third", "past", "plain",
     113, ["classics", "children", "garden", "recovery"]),
    ("The Wonderful Wizard of Oz", "L. Frank Baum", 1900, "third", "past", "spare", 55,
     ["classics", "children", "fantasy", "quest"]),
    ("The Mysterious Affair at Styles", "Agatha Christie", 1920, "first", "past",
     "plain", 863, ["classics", "mystery", "detective", "country house"]),
    ("Twenty Thousand Leagues Under the Sea", "Jules Verne", 1870, "first", "past",
     "measured", 164, ["classics", "adventure", "science fiction", "translated"]),
    ("The Jungle Book", "Rudyard Kipling", 1894, "third", "past", "measured", 236,
     ["classics", "children", "animals", "short stories"]),
    ("Ethan Frome", "Edith Wharton", 1911, "first", "past", "measured", 4517,
     ["classics", "novella", "rural", "tragedy"]),
    ("The Awakening", "Kate Chopin", 1899, "third", "past", "textured", 160,
     ["classics", "american", "feminist", "louisiana"]),
    ("Sense and Sensibility", "Jane Austen", 1811, "third", "past", "measured", 161,
     ["classics", "romance", "sisters", "regency"]),
]

# In-copyright titles. Point of view and tense are stated facts; everything
# else is filled in by enrichment from the open APIs.
MODERN = [
    ("Never Let Me Go", "Kazuo Ishiguro", 2005, "first", "past", "measured",
     ["literary", "science fiction", "boarding school", "unreliable narrator"]),
    ("The Remains of the Day", "Kazuo Ishiguro", 1989, "first", "past", "textured",
     ["literary", "butler", "restraint", "england"]),
    ("Beloved", "Toni Morrison", 1987, "third", "past", "dense",
     ["literary", "american", "haunting", "slavery"]),
    ("The Road", "Cormac McCarthy", 2006, "third", "past", "spare",
     ["literary", "post-apocalyptic", "father and son", "minimal punctuation"]),
    ("Blood Meridian", "Cormac McCarthy", 1985, "third", "past", "dense",
     ["literary", "western", "violence", "biblical register"]),
    ("A Little Life", "Hanya Yanagihara", 2015, "third", "past", "textured",
     ["literary", "friendship", "trauma", "long"]),
    ("Normal People", "Sally Rooney", 2018, "third", "present", "spare",
     ["literary", "irish", "relationship", "no quotation marks"]),
    ("Conversations with Friends", "Sally Rooney", 2017, "first", "past", "spare",
     ["literary", "irish", "relationship", "no quotation marks"]),
    ("The Secret History", "Donna Tartt", 1992, "first", "past", "textured",
     ["literary", "dark academia", "campus", "murder"]),
    ("The Goldfinch", "Donna Tartt", 2013, "first", "past", "textured",
     ["literary", "art", "long", "bildungsroman"]),
    ("Piranesi", "Susanna Clarke", 2020, "first", "present", "measured",
     ["fantasy", "literary", "epistolary", "labyrinth"]),
    ("Jonathan Strange & Mr Norrell", "Susanna Clarke", 2004, "third", "past", "textured",
     ["fantasy", "alternate history", "footnotes", "long"]),
    ("The Left Hand of Darkness", "Ursula K. Le Guin", 1969, "first", "past", "measured",
     ["science fiction", "anthropological", "gender", "cold"]),
    ("The Dispossessed", "Ursula K. Le Guin", 1974, "third", "past", "measured",
     ["science fiction", "anarchism", "physics", "dual timeline"]),
    ("A Wizard of Earthsea", "Ursula K. Le Guin", 1968, "third", "past", "measured",
     ["fantasy", "coming of age", "islands", "names"]),
    ("Klara and the Sun", "Kazuo Ishiguro", 2021, "first", "past", "plain",
     ["literary", "science fiction", "artificial intelligence", "child narrator"]),
    ("Station Eleven", "Emily St. John Mandel", 2014, "third", "past", "measured",
     ["literary", "post-apocalyptic", "theatre", "multiple timelines"]),
    ("The Fifth Season", "N. K. Jemisin", 2015, "second", "present", "measured",
     ["fantasy", "science fiction", "second person", "geology"]),
    ("Circe", "Madeline Miller", 2018, "first", "past", "textured",
     ["mythology", "retelling", "greek", "witch"]),
    ("The Song of Achilles", "Madeline Miller", 2011, "first", "past", "textured",
     ["mythology", "retelling", "greek", "romance"]),
    ("Gideon the Ninth", "Tamsyn Muir", 2019, "third", "past", "measured",
     ["science fantasy", "necromancy", "voice-driven", "locked room"]),
    ("Convenience Store Woman", "Sayaka Murata", 2016, "first", "past", "spare",
     ["literary", "japanese", "translated", "novella"]),
    ("The Vegetarian", "Han Kang", 2007, "mixed", "past", "measured",
     ["literary", "korean", "translated", "three parts"]),
    ("Kafka on the Shore", "Haruki Murakami", 2002, "mixed", "past", "measured",
     ["literary", "japanese", "translated", "surreal"]),
    ("My Brilliant Friend", "Elena Ferrante", 2011, "first", "past", "measured",
     ["literary", "italian", "translated", "friendship"]),
    ("Exhalation", "Ted Chiang", 2019, "mixed", "past", "measured",
     ["science fiction", "short stories", "philosophical", "hard sf"]),
    ("Project Hail Mary", "Andy Weir", 2021, "first", "past", "plain",
     ["science fiction", "hard sf", "problem solving", "humour"]),
    ("The Three-Body Problem", "Cixin Liu", 2008, "third", "past", "measured",
     ["science fiction", "chinese", "translated", "hard sf"]),
    ("Wolf Hall", "Hilary Mantel", 2009, "third", "present", "textured",
     ["historical", "tudor", "present tense", "long"]),
    ("Lincoln in the Bardo", "George Saunders", 2017, "mixed", "past", "textured",
     ["literary", "experimental", "polyphonic", "ghosts"]),
    ("The Underground Railroad", "Colson Whitehead", 2016, "third", "past", "measured",
     ["literary", "historical", "american", "allegory"]),
    ("Small Things Like These", "Claire Keegan", 2021, "third", "past", "spare",
     ["literary", "irish", "novella", "restraint"]),
    ("Milkman", "Anna Burns", 2018, "first", "past", "dense",
     ["literary", "irish", "unnamed characters", "long sentences"]),
    ("Ducks, Newburyport", "Lucy Ellmann", 2019, "first", "present", "dense",
     ["literary", "experimental", "stream of consciousness", "very long"]),
    ("The Bee Sting", "Paul Murray", 2023, "mixed", "mixed", "measured",
     ["literary", "irish", "family", "long"]),
    ("Babel", "R. F. Kuang", 2022, "third", "past", "textured",
     ["fantasy", "dark academia", "colonialism", "footnotes"]),
    ("The Priory of the Orange Tree", "Samantha Shannon", 2019, "third", "past", "measured",
     ["fantasy", "dragons", "epic", "very long"]),
    ("Legends & Lattes", "Travis Baldree", 2022, "third", "past", "plain",
     ["fantasy", "cosy", "low stakes", "coffee"]),
    ("The House in the Cerulean Sea", "TJ Klune", 2020, "third", "past", "plain",
     ["fantasy", "cosy", "found family", "whimsical"]),
    ("Fourth Wing", "Rebecca Yarros", 2023, "first", "present", "plain",
     ["romantasy", "dragons", "military school", "romance"]),
    ("A Court of Thorns and Roses", "Sarah J. Maas", 2015, "first", "past", "plain",
     ["romantasy", "fae", "retelling", "romance"]),
    ("It Ends with Us", "Colleen Hoover", 2016, "first", "present", "plain",
     ["contemporary romance", "difficult subject", "bestseller"]),
    ("After", "Anna Todd", 2014, "first", "past", "plain",
     ["contemporary romance", "new adult", "web serial origin"]),
    ("The Love Hypothesis", "Ali Hazelwood", 2021, "third", "past", "plain",
     ["contemporary romance", "academia", "fake dating", "web serial origin"]),
    ("Red, White & Royal Blue", "Casey McQuiston", 2019, "third", "past", "plain",
     ["contemporary romance", "political", "queer", "epistolary elements"]),
    ("Tomorrow, and Tomorrow, and Tomorrow", "Gabrielle Zevin", 2022, "third", "past",
     "measured", ["literary", "video games", "friendship", "decades"]),
    ("Piglet", "Lottie Hazell", 2024, "third", "present", "plain",
     ["literary", "food", "debut", "domestic"]),
    ("Orbital", "Samantha Harvey", 2023, "third", "present", "textured",
     ["literary", "space station", "novella", "lyrical"]),
    ("Demon Copperhead", "Barbara Kingsolver", 2022, "first", "past", "measured",
     ["literary", "appalachia", "retelling", "voice-driven"]),
    ("Trust", "Hernan Diaz", 2022, "mixed", "past", "measured",
     ["literary", "finance", "nested narratives", "unreliable"]),
]


def build_entry(title, author, year, pov, tense, band, subjects, gutenberg_id=None):
    style = dict(BANDS[band])
    style.update(
        pov=pov, tense=tense,
        pov_confidence=0.0, tense_confidence=0.0,
        adverb_rate=12.0, comma_rate=2.2, long_sentence_share=0.1,
        short_sentence_share=0.2, mean_paragraph_sentences=3.5,
        dash_rate=3.0, words_analysed=0, sentences_analysed=0,
        notes=["Provisional values. Not yet measured from text."],
    )
    return {
        "title": title,
        "author": author,
        "year": year,
        "subjects": subjects,
        "style": style,
        "style_band": band,
        "gutenberg_id": gutenberg_id,
        "provisional": True,
    }


def main():
    entries = []
    for title, author, year, pov, tense, band, gid, subjects in CLASSICS:
        entries.append(build_entry(title, author, year, pov, tense, band, subjects, gid))
    for title, author, year, pov, tense, band, subjects in MODERN:
        entries.append(build_entry(title, author, year, pov, tense, band, subjects))

    DATA.mkdir(parents=True, exist_ok=True)
    out = DATA / "seed_books.json"
    out.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")

    public_domain = sum(1 for e in entries if e["gutenberg_id"])
    print(f"Wrote {len(entries)} books to {out}")
    print(f"  {public_domain} with full text available for measurement")
    print(f"  {len(entries) - public_domain} metadata-only until enriched")


if __name__ == "__main__":
    main()
