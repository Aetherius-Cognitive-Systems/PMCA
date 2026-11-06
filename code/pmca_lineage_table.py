"""pmca_lineage_table — the GOVERNED source -> lineage table
(Candidacy Rule Set C-3, ruled 2026-10-31 by Alder under
delegation; built 2026-11-01, WG1 fix wave, 04 Forge;
WG1b fix-pass 2026-11-01: canonicalization + gap records,
R-1).

C-3 text of record: "Classes are read through the governed
source->lineage table (shared data; the ingest locus's table
is its seed); a source absent from the table is recorded as
an ungoverned-source gap; a MISSING source counts as NO
class, never as a class."

This module is that shared data, plus the one resolution
rule every provenance-class counter in the tree reads:

  CANONICALIZATION (WG1b, R-1 — a stated deterministic
  rule, not a longer variant list): before any lookup, a
  spelling is reduced to its canonical form by
    (1) case-folding (str.casefold), and
    (2) deleting every character that is not a letter or a
        digit (spaces and punctuation are spelling
        decoration: "Webster 1913", "WEBSTER 1913",
        "Webster1913", "webster-1913" all reduce to
        "webster1913").
  Lookup is by canonical form against the canonical forms
  of the governed spellings in SOURCE_TO_LINEAGE, so ANY
  case/space/punctuation variant of a governed spelling
  resolves to that spelling's lineage — variants never
  split a class.

  lineage_class(value):
    - value missing / None / not a string / empty or
      whitespace-only  ->  None  (NO class; the record
      contributes nothing to any class count — Ledger D-3).
    - value's canonical form is governed
      ->  its lineage class.
    - any other non-empty spelling  ->  the spelling
      itself, AS WRITTEN (exact self-identity; an
      ungoverned source is never merged with any other
      spelling) — AND the resolution is recorded as an
      ungoverned-source GAP (C-3's gap clause, WG1b):
      one record per (spelling, context) per process is
      appended to the gap store (see record_ungoverned_gap).

  class_for_record(record):
    A provenance record may carry its class under "lineage"
    (genus-store records) or under "source" (write-in /
    stored-once records). The "lineage" value governs when
    present and non-empty; otherwise the "source" value
    governs; otherwise NO class. Both keys resolve through
    lineage_class, so a spelling split on either key
    collapses to one class (Ledger D-2 + WG1b R-1).

  provenance_classes(records):
    The set of classes over an iterable of records, with
    NO-class records contributing nothing.

Counters keep their own signatures; they pass a `context`
string naming the counter, which is carried into any gap
record their resolutions produce.

THE GAP STORE: GAP_STORE_PATH, by default
`lineage_gaps.jsonl` beside this module (in the tree,
beside the stores). A gap record is
  {"kind": "ungoverned_source_gap",
   "spelling": <as written>, "canonical": <canonical form>,
   "context": <counter/context string>, "seq": <int>}
appended under the tree's ATOM-2 single-writer lock
(pmca_persistence.acquire_lock on the gap file's
directory; the lock is retried briefly and the append
never happens unlocked — a still-held lock raises loud
rather than writing unguarded). `seq` is the record's
1-based position in the gap file, counted under the lock.
Within one process, a (spelling, context) pair is logged
once (the gap is a standing fact about the spelling at
that counter, not a per-call event log); a new process
resolving the same spelling at the same context appends
a fresh record with the next seq.

Seed of the table: the ingest-verification locus's fixed
source -> lineage assignments (pmca_ingest_verify, pack of
2026-10-28): ECDICT -> ECDICT, Open English WordNet ->
WordNet, Wiktionary -> Wiktionary, Webster 1913 ->
Webster, Wordset -> WordNet (that locus asserts Wordset's
lineage is WordNet, "never a distinct lineage"). The
Webster family is extended with the other Webster
spellings actually present in the tree's stores/fixtures
("Webster", "webster1913"). Every remaining spelling in
SOURCE_TO_LINEAGE is a spelling actually present in the
tree's stores/fixtures (grep inventory in the WG1 build
paper), mapped to itself: a writing door's identity is
its own class (PROM-1 vocabulary). Dynamic spellings not
listed (e.g. "book:<book_id>" for books other than the one
of record, "book_ingest:<book_id>") are ungoverned by the
table: they resolve by exact self-identity — each distinct
book is its own lineage — and their resolution is recorded
as an ungoverned-source gap under the rule above.

NOT governed here (never class-counted anywhere in the
tree, excluded by design): seed harm-edge provenance
strings ("intrinsic", "curator", "rule:*",
"dataset:3-stage-pipeline", "unvalued"), teaching
provenance strings ("teaching:<event_id>" — TEACH-V gates
on the replay verdict, not on class counts), and the
analyzer's trace provenance strings ("own", "analyzer").

Deterministic. Stdlib only at import; the gap writer
imports pmca_persistence lazily (the tree's lock), so
importing this module never imports the tree.
"""

import json
import os
import time
from pathlib import Path

# Governed spelling -> lineage class. See module docstring
# for provenance of every family.
SOURCE_TO_LINEAGE = {
    # --- Webster family: ONE lineage (Ledger D-2). ---
    "Webster": "Webster",
    "Webster 1913": "Webster",
    "webster1913": "Webster",
    # --- WordNet family (ingest-verify seed table). ---
    "Open English WordNet": "WordNet",
    "WordNet": "WordNet",
    "Wordset": "WordNet",
    # --- Other dictionary lineages (ingest-verify seed). ---
    "ECDICT": "ECDICT",
    "Wiktionary": "Wiktionary",
    # --- The book lineage of record in today's stores ---
    # (genus store gid-0003: source spelling stamped by the
    # original builder; the title spelling is the carried
    # form the fixed builder stamps from the candidate's
    # own provenance).
    "A Civic Biology: Presented in Problems (Hunter, 1914)":
        "book:a-civic-biology-hunter-1914",
    "A Civic Biology: Presented in Problems":
        "book:a-civic-biology-hunter-1914",
    "book:a-civic-biology-hunter-1914":
        "book:a-civic-biology-hunter-1914",
    # --- Writing-door identities: each door its own class
    # (PROM-1). Spellings attested in tree code/fixtures. ---
    "finders": "finders",
    "verifier": "verifier",
    "morphology": "morphology",
    "dictionary": "dictionary",
    "dictionary_note": "dictionary_note",
    "entry_store": "entry_store",
    "definition_overlap": "definition_overlap",
    "explicit": "explicit",
    "enrichment": "enrichment",
    "encounter": "encounter",
    "suite": "suite",
    "teaching": "teaching",
    "forged": "forged",
}


def canonical_form(value):
    """The canonical form of a spelling, under the stated
    deterministic rule: case-fold, then delete every
    character that is not a letter or a digit."""
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _build_canonical_map():
    cmap = {}
    for spelling, lineage in SOURCE_TO_LINEAGE.items():
        key = canonical_form(spelling)
        if key in cmap and cmap[key] != lineage:
            raise ValueError(
                "canonical collision between governed spellings: "
                "%r and another spelling both reduce to %r with "
                "different lineages" % (spelling, key))
        cmap[key] = lineage
    return cmap


# Canonical form -> lineage class, for every governed
# spelling. Lookup is through this map ONLY, so variant
# spellings of a governed source can never split a class.
_CANONICAL_TO_LINEAGE = _build_canonical_map()


# ---------------------------------------------------------------------
# Ungoverned-source gap records (C-3 gap clause; WG1b R-1)
# ---------------------------------------------------------------------

# The named gap store: beside this module, in the tree,
# beside the stores. Tests may repoint this module global
# to a scratch path.
GAP_STORE_PATH = Path(__file__).resolve().parent / "lineage_gaps.jsonl"

# (gap-store path, spelling, context) pairs already logged
# in this process — a standing gap is logged once per
# process per counter, not once per resolution call.
_LOGGED_GAPS = set()


def record_ungoverned_gap(spelling, context):
    """Append one ungoverned-source gap record for
    `spelling` (as written) resolved in `context` to the
    gap store, under the ATOM-2 single-writer lock for the
    gap file's directory. Returns the record written, or
    None when this (spelling, context) was already logged
    in this process. A lock that stays held past the retry
    budget raises (pmca_persistence.WriterLockHeld): the
    append never happens unlocked, and the failure is loud,
    never a silent gap about a gap."""
    key = (str(GAP_STORE_PATH), spelling, context)
    if key in _LOGGED_GAPS:
        return None
    import pmca_persistence
    path = Path(GAP_STORE_PATH)
    directory = str(path.parent)
    lock_path = None
    last_exc = None
    for _ in range(100):
        try:
            lock_path = pmca_persistence.acquire_lock(directory)
            break
        except pmca_persistence.WriterLockHeld as exc:
            last_exc = exc
            time.sleep(0.01)
    if lock_path is None:
        raise last_exc
    try:
        seq = 1
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                seq = sum(1 for line in fh if line.strip()) + 1
        record = {"kind": "ungoverned_source_gap",
                  "spelling": spelling,
                  "canonical": canonical_form(spelling),
                  "context": context,
                  "seq": seq}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        pmca_persistence.release_lock(lock_path)
    _LOGGED_GAPS.add(key)
    return record


def lineage_class(value, context="lineage_class"):
    """Resolve one source/lineage spelling to its governed
    lineage class, or None when the spelling carries NO
    class (missing, None, non-string, empty/whitespace).
    Resolution is by canonical form (see the module
    docstring): any case/space/punctuation variant of a
    governed spelling resolves to that spelling's lineage.
    A spelling whose canonical form is ungoverned resolves
    to itself AS WRITTEN (self-identity, never merged) and
    is recorded as an ungoverned-source gap in `context`."""
    if not isinstance(value, str):
        return None
    if not value.strip():
        return None
    cls = _CANONICAL_TO_LINEAGE.get(canonical_form(value))
    if cls is not None:
        return cls
    record_ungoverned_gap(value, context)
    return value


def class_for_record(record, context="class_for_record"):
    """The lineage class of one provenance record, or None
    (NO class). "lineage" governs when present and
    non-empty; else "source"; else no class. Non-dict
    records carry no key and contribute no class."""
    if not isinstance(record, dict):
        return None
    cls = lineage_class(record.get("lineage"), context=context)
    if cls is not None:
        return cls
    return lineage_class(record.get("source"), context=context)


def provenance_classes(records, context="provenance_classes"):
    """The set of lineage classes over provenance records;
    records with NO class contribute nothing. `context`
    names the counter doing the resolving and is carried
    into any ungoverned-source gap record produced."""
    classes = set()
    for record in records or []:
        cls = class_for_record(record, context=context)
        if cls is not None:
            classes.add(cls)
    return classes
