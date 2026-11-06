# PMCA: Dictionary Stocks and Their Sources (Strongly Noted)

The build tree reads its dictionary stock at runtime from a `data/` folder beside the scripts: in this repository that is `code/data/` (in the Zenodo release archive the same folder sits at `code/build-tree/data/`).
**The datasets themselves are NOT included in this release.** They are
third-party datasets, and too big a file to ship: the stock as used runs to
roughly 3.8 GB, most of it the Wiktionary extract. They are publicly
available from their own sources, named here exactly, so that anyone can
rebuild the same stock and replicate the released figures.

The stock is fourfold. These are the dictionaries, and this is where each
one comes from:

1. **Wiktionary, via Kaikki.org.** The Kaikki English Wiktionary extract
   (`kaikki-english.jsonl`), and the SQLite database derived from it in
   this project (`wiktionary.db`). Source: Kaikki.org's extract of English
   Wiktionary. This is the stock used as the identifier source for the
   R-1 attestation test.
2. **Webster 1913.** Webster's Revised Unabridged Dictionary, 1913
   edition (`webster1913.db`; the StarDict form `web1913-stardict.zip`).
   Source: the 1913 Webster's Revised Unabridged Dictionary, public
   domain.
3. **WordNet.** The WordNet lexical database, read by the tree as one of
   the four stocks.
4. **The seed.** PMCA's own seed graph. Unlike the three stocks above,
   the seed is part of this work: it ships with the code in this release,
   in the tree's seed module and its stored state. The seed stands as the
   fixed baseline; only words carrying deterministic values are added
   to it.

An English word list (`words_alpha.txt`) also sits in the data directory
as a working list alongside the stocks.

To run the build tree as released: obtain the three external stocks from
the sources above, place them under `code/data/` in this repository, and the tree
runs against the same stock it was verified with.
