"""PMCA Book Ingest - educational books in, as candidates and records.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's
architecture. Build of 2026-10-28 (book-ingest pack).

What this module IS: the front door for whole educational books.
It segments a book deterministically (chapters, then sentences),
runs every sentence through the existing sentence analyzer,
extracts definitional constructions as CANDIDATE records, and
writes everything it produces into a dedicated candidate store
(book_ingest_store.jsonl). Words are stored-once: a word already
in the stored space/seed is referenced by its coordinate and
never re-placed; a genuinely new word is placed bare, once, via
the existing population-on-encounter path
(pmca_population.populate_word) into the ingest word store, and
every occurrence of it is a record that POINTS at that one entry.

What this module is NOT:

- It never writes the main space or the seed. The analyzer runs
  against a disposable GraphSpace per process; the seed tables
  are consulted read-only. The only stores ingestion writes are
  its candidate store and its own word store.
- It never verifies or promotes anything. Definition candidates
  are CANDIDATE forever here: promotion runs through the
  existing teaching machinery (>= 2 distinct provenance
  records), never through ingestion.
- It does NOT wire definition candidates into any is_a
  mechanism, genus entry, or the seed. The candidates are a
  candidacy feed for a future is_a build, nothing more.
- It does not enrich placed words from the dictionary. The
  analyzer's dictionary tier stays OFF for ingestion: an
  unknown word HOLDs and leaves a gap record, by design.

Determinism: segmentation, extraction, word planning, and
record identity are pure functions of the book text and the
store state at run start. Parallel mode (multiprocessing over
chapters) merges worker output in source order and produces a
byte-identical record stream to serial mode.

Book-level binding (M-2 completion, WG1b 2026-11-01): the
first record of every ingest stream is a `book_source`
record created from the ingested text itself — its identity
(record_id) is the sha256 of the canonical identity
{kind: book_source, text_sha256}, where text_sha256 is the
sha256 of the full normalized book text. The caller's
book_id is stored on that record as an ASSERTION
(asserted_book_id), never as the book's identity; every
other record's provenance carries book_source_id, the
source record's id, so a downstream builder can check an
asserted book_id against the ingest-created source record
of record (pmca_build_isa.book_source_registry /
book_level_check: an assertion that conflicts with the
registered source record for that id HOLDs the candidate,
recorded — it is never filed under either identity).
Each definition candidate also carries a content_binding:
sha256 of the canonical {record_id, genus_key,
sentence_sha256} — the candidate's genus and its sentence
are bound to its record identity at creation, so altering
either after ingest breaks the binding recompute in the
builder. A candidate's own record_id identity is UNCHANGED
(the WG1 form); the binding is an additional field, not a
new identity.

Acceleration shape (honest): the per-sentence parser is
latency-bound CPU rule traversal and stays on CPU. What is
accelerated around it: (a) cross-chapter parallelism, merged
deterministically; (b) dedup by digest-set membership over
record ids (hashlib set). At library scale, (b)'s membership
test is the layer the TPU prep pack's array matcher
(harness_e2_matching: digest/coordinate arrays, integer ops,
byte-identical outcomes against the pure-Python reference)
replaces; the interface is the single function
CandidateStore.contains_all / commit path below, and the
record-id digest set is exactly the array matcher's input
shape (fixed 32-byte digests). No JAX is imported here.
"""

import hashlib
import json
import os
import re
import sys

sys.dont_write_bytecode = True

# ---------------------------------------------------------------------
# Canonical form + record identity
# ---------------------------------------------------------------------


def canonical(obj):
    """The one canonical JSON form used for identities, record
    lines, and store digests in this module."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _jsonsafe(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonsafe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonsafe(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((_jsonsafe(v) for v in obj), key=repr)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _record_id(identity):
    return _sha256_text(canonical(identity))


# ---------------------------------------------------------------------
# Segmentation: book -> chapters -> sentences (deterministic)
# ---------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(CHAPTER\s+\w+|[IVXLCDM]{1,7}\.)\s+\S")
_PART_CHARS = 8000

# Periods that do not end a sentence: initial chains (M.D., U.S.,
# i.e., e.g.) and decimal points are masked by regex; these
# abbreviation tokens are masked by word.
_ABBREV_WORDS = ("dr", "mr", "mrs", "ms", "st", "vs", "fig", "etc",
                 "no", "approx")
_MASK = "\x00"
_CHAIN_RE = re.compile(r"\b(?:[A-Za-z]\.){2,}")
_ABBREV_RE = re.compile(
    r"\b(" + "|".join(_ABBREV_WORDS) + r")\.", re.IGNORECASE)
_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")
_TERMINATOR_RE = re.compile(r"[.!?]+[\"')\]]*(?=\s|$)")


def _is_heading(paragraph):
    if len(paragraph) > 130 or not _HEADING_RE.match(paragraph):
        return False
    letters = [c for c in paragraph if c.isalpha()]
    if not letters:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) >= 0.7


def _mask(text):
    text = _CHAIN_RE.sub(lambda m: m.group(0).replace(".", _MASK), text)
    text = _ABBREV_RE.sub(lambda m: m.group(1) + _MASK, text)
    text = _DECIMAL_RE.sub(_MASK, text)
    return text


def _split_sentences(paragraph, paragraph_offset):
    """Deterministic sentence splitter. Rule: a sentence ends at
    a [.!?] terminator (plus any closing quote/bracket) that is
    followed by whitespace or the paragraph end, after masking
    abbreviation periods, initial chains, and decimals; a
    trailing fragment without a terminator is its own unit.
    Returns [{text, char_offset}] with offsets into the
    normalized book text."""
    masked = _mask(paragraph)
    spans = []
    start = 0
    for m in _TERMINATOR_RE.finditer(masked):
        spans.append((start, m.end()))
        start = m.end()
    if masked[start:].strip():
        spans.append((start, len(masked)))
    out = []
    for s, e in spans:
        piece = masked[s:e]
        stripped = piece.strip()
        if not stripped:
            continue
        lead = len(piece) - len(piece.lstrip())
        out.append({"text": stripped.replace(_MASK, "."),
                    "char_offset": paragraph_offset + s + lead})
    return out


def segment_book(raw_text):
    """Split raw book text into chapters and sentences.
    Paragraphs are blank-line blocks with internal whitespace
    collapsed; the normalized book text is the paragraphs joined
    by blank lines, and every char_offset indexes into it.
    Chapter rule: a paragraph that matches the heading pattern
    (roman numeral or CHAPTER + mostly-uppercase + short) opens
    a chapter. Fallback (no headings anywhere): fixed-size
    parts of ~8000 chars at paragraph boundaries, labelled
    part-N. Returns (book, rule) where book = {"text": ...,
    "chapters": [{"chapter", "title", "sentences": [{...}]}]}"""
    paragraphs = []
    for block in re.split(r"\n\s*\n", raw_text):
        p = " ".join(block.split())
        if p:
            paragraphs.append(p)
    normalized = "\n\n".join(paragraphs)
    offsets = []
    pos = 0
    for p in paragraphs:
        offsets.append(pos)
        pos += len(p) + 2

    heading_idx = [i for i, p in enumerate(paragraphs) if _is_heading(p)]
    chapters = []
    if heading_idx:
        rule = "heading: roman-numeral/CHAPTER title paragraphs"
        starts = heading_idx + [len(paragraphs)]
        for k in range(len(heading_idx)):
            hi = heading_idx[k]
            title = paragraphs[hi]
            label = title.split()[0].strip(".")
            if title.upper().startswith("CHAPTER"):
                label = " ".join(title.split()[:2])
            chapter = {"chapter": label, "title": title,
                       "sentences": []}
            for j in range(hi + 1, starts[k + 1]):
                chapter["sentences"].extend(
                    _split_sentences(paragraphs[j], offsets[j]))
            chapters.append(chapter)
        front = []
        for j in range(0, heading_idx[0]):
            front.extend(_split_sentences(paragraphs[j], offsets[j]))
        if front:
            chapters.insert(0, {"chapter": "front", "title": None,
                                  "sentences": front})
    else:
        rule = "fallback: fixed-size parts (~8000 chars, paragraph boundaries)"
        current = {"chapter": "part-1", "title": None, "sentences": []}
        size = 0
        n = 1
        for j, p in enumerate(paragraphs):
            if size and size + len(p) > _PART_CHARS:
                chapters.append(current)
                n += 1
                current = {"chapter": "part-%d" % n, "title": None,
                           "sentences": []}
                size = 0
            current["sentences"].extend(_split_sentences(p, offsets[j]))
            size += len(p)
        if current["sentences"]:
            chapters.append(current)

    index = 0
    for chapter in chapters:
        for sentence in chapter["sentences"]:
            sentence["sentence_index"] = index
            sentence["chapter"] = chapter["chapter"]
            index += 1
    return {"text": normalized, "chapters": chapters}, rule


# ---------------------------------------------------------------------
# Definition candidates: deterministic pattern extraction
# ---------------------------------------------------------------------

_DEFN_RE = re.compile(
    r"\b(is defined as|is called|refers to|is an|is a)\s+",
    re.IGNORECASE)
_CONSTRUCTIONS = {"is defined as": "is_defined_as",
                  "is called": "is_called",
                  "refers to": "refers_to",
                  "is an": "is_an",
                  "is a": "is_a"}
_GENUS_BOUNDARIES = (",", ";", " — ", " (", " which ", " that ",
                     " because ", " and ", " or ", ": ")
_TERM_LEAD_DROP = {"the", "a", "an", "and", "or", "but", "which",
                   "that", "who", "inasmuch", "although", "because",
                   "since", "when", "if", "though", "as"}


def extract_definitions(sentence):
    """Definitional constructions ONLY, by regular expression
    over the sentence surface: '<term> is a <...>', 'is an',
    'is defined as', 'is called', 'refers to'. The genus phrase
    is the text after the construction up to the first clause
    boundary or 8 tokens. CRUDE by design: the term is the last
    <= 4 tokens of the last pre-construction segment, and no
    linguistic judgment is applied. These are candidacy records
    for a future is_a feed, nothing more. Returns (hits, skips)
    where skips counts matches whose term came out empty."""
    hits = []
    skips = 0
    for m in _DEFN_RE.finditer(sentence):
        construction = _CONSTRUCTIONS[m.group(1).lower()]
        pre = re.split(r"[;:?—,\"]", sentence[:m.start()])[-1].strip()
        toks = pre.split()
        term_toks = toks[-4:]
        while term_toks and (term_toks[0].strip(".,\"'()").lower()
                             in _TERM_LEAD_DROP):
            term_toks = term_toks[1:]
        term = " ".join(term_toks).strip(" .,\"'()")
        if not term or not any(c.isalpha() for c in term):
            skips += 1
            continue
        post = sentence[m.end():]
        cut = len(post)
        for boundary in _GENUS_BOUNDARIES:
            i = post.find(boundary)
            if i != -1:
                cut = min(cut, i)
        genus = " ".join(post[:cut].split()[:8]).strip(" .,;:\"'()")
        hits.append({"term": term, "term_key": term.lower(),
                     "construction": construction,
                     "genus_phrase": genus,
                     "genus_key": genus.lower()})
    return hits, skips


# ---------------------------------------------------------------------
# Words: stored-once plan over seed + ingest word store
# ---------------------------------------------------------------------


def load_word_store(path):
    import pmca_write_in as wi
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.loads(fh.read())
    return wi.new_store()


def save_word_store(path, store):
    import pmca_write_in as wi
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(wi.canonical(store))
        fh.write("\n")


def word_store_hash(store):
    import pmca_write_in as wi
    return wi.store_hash(store)


def _word_ref(word, stored_in, placed_event):
    import pmca_graphspace
    addr = pmca_graphspace.dist_address(word)
    return {"word": word, "coordinate": [addr[0], addr[1], addr[2]],
            "stored_in": stored_in, "placed_event": placed_event}


def plan_words(sentences, word_store):
    """Classify every distinct book word, in first-occurrence
    order, against the stored space/seed and the ingest word
    store. Membership in the seed is the analyzer's own KNOWN
    vocabulary (the same set that decides unknown_word HOLDs):
    a KNOWN word is referenced by coordinate and NEVER placed.
    A word already in the ingest word store is referenced. Any
    other word is genuinely new and is placed bare via
    pmca_population.populate_word, once, at its first
    occurrence. Returns (refs, plan) where refs maps word ->
    word_ref and plan counts the three classes."""
    import pmca_population
    import pmca_sentence_analyzer as analyzer
    import pmca_write_in as wi

    order = []
    seen = set()
    for sentence in sentences:
        for tok in pmca_population._tokens(sentence["text"]):
            if tok not in seen:
                seen.add(tok)
                order.append(tok)

    refs = {}
    new_words = []
    plan = {"seed_referenced": 0, "already_in_store": 0, "placed": 0}
    entries = word_store["entries"]
    for word in order:
        if word in analyzer.KNOWN:
            refs[word] = _word_ref(word, "seed", None)
            plan["seed_referenced"] += 1
        elif word == wi.SELF_KEY or word in entries:
            placed_event = None
            entry = entries.get(word)
            if entry is not None:
                placed_event = (entry.get("population")
                                or {}).get("placed_event")
            refs[word] = _word_ref(word, "ingest_store", placed_event)
            plan["already_in_store"] += 1
        else:
            new_words.append(word)
    return refs, new_words, plan, order


def apply_placements(word_store, new_words, source):
    """Place each genuinely new word bare, once, through the
    existing population-on-encounter path. Mutates word_store.
    Returns {word: placed_event}."""
    import pmca_population
    placed = {}
    for word in new_words:
        report = pmca_population.populate_word(word_store, word, source)
        if report["outcome"] != "placed_bare":
            raise RuntimeError(
                "placement plan violated for %r: %r" % (word, report))
        placed[word] = report["placed_event"]
    return placed


# ---------------------------------------------------------------------
# Parse: per-sentence records (the CPU core; parallelized around)
# ---------------------------------------------------------------------


def _analyze(sentence, space):
    import pmca_sentence_analyzer as analyzer
    try:
        r = analyzer.analyze(sentence, closure=False, space=space)
        return {"verdict": r["verdict"], "omega": r["omega"],
                "harm": _jsonsafe(r["harm"]), "gate": r["gate"],
                "parameters": _jsonsafe(r["parameters"]),
                "hold_reason": r["hold_reason"],
                "lock_reason": r["lock_reason"],
                "gaps": _jsonsafe(r["gaps"]),
                "trace": list(r["trace"]), "error": None}
    except Exception as exc:  # a sentence never vanishes silently
        return {"verdict": "ERROR", "omega": None, "harm": None,
                "gate": None,
                "parameters": {"cause": None, "action": None,
                               "target": None, "consequence": None,
                               "directness": None},
                "hold_reason": None, "lock_reason": None,
                "gaps": [], "trace": [],
                "error": "%s: %s" % (type(exc).__name__, exc)}


def _gap_specs(outcome):
    """Gap records the encounter produced: the analyzer's typed
    gaps, plus one record for the HOLD reason (an unknown_word
    reason becomes an unknown_word gap naming the word). An
    ERROR outcome becomes a parser_error gap."""
    specs = []
    for g in outcome["gaps"]:
        if isinstance(g, dict):
            kind = g.get("kind") or g.get("gap") or "typed_gap"
            specs.append((str(kind), _jsonsafe(g)))
        else:
            specs.append(("typed_gap", {"text": str(g)}))
    if outcome["error"] is not None:
        specs.append(("parser_error", {"error": outcome["error"]}))
    elif outcome["hold_reason"]:
        reason = outcome["hold_reason"]
        if reason.startswith("unknown_word:"):
            specs.append(("unknown_word",
                          {"word": reason.split(":", 1)[1]}))
        else:
            specs.append(("hold", {"reason": reason}))
    return specs


def _provenance(meta, sentence):
    return {"book_id": meta["book_id"], "title": meta["title"],
            "author": meta["author"], "license": meta["license"],
            "source_url": meta["source_url"],
            "chapter": sentence["chapter"],
            "sentence_index": sentence["sentence_index"],
            "char_offset": sentence["char_offset"],
            "book_source_id": meta.get("book_source_id"),
            "book_text_sha256": meta.get("book_text_sha256")}


def book_source_record(book, meta):
    """The book-level source record for one ingest (M-2
    completion, WG1b): created from the ingested text
    itself. Its record_id — the book's derived identity —
    is the sha256 of the canonical identity {kind:
    book_source, text_sha256}, a pure function of the full
    normalized book text. The caller's manifest fields
    (book_id, title, author, license, source_url) are
    stored as ASSERTIONS on the record; the asserted
    book_id in particular is a claim about the text, not
    the text's identity. The record's own provenance
    carries the asserted fields with book-level sentinels
    for the sentence-level keys (chapter "(book)",
    sentence_index -1, char_offset 0), so the complete-
    provenance shape of the stream is uniform."""
    text_sha = _sha256_text(book["text"])
    rid = _record_id({"kind": "book_source",
                      "text_sha256": text_sha})
    prov = {"book_id": meta["book_id"], "title": meta["title"],
            "author": meta["author"], "license": meta["license"],
            "source_url": meta["source_url"],
            "chapter": "(book)", "sentence_index": -1,
            "char_offset": 0,
            "book_source_id": rid, "book_text_sha256": text_sha}
    return {"kind": "book_source", "status": "SOURCE",
            "text_sha256": text_sha,
            "asserted_book_id": meta["book_id"],
            "title": meta["title"], "author": meta["author"],
            "license": meta["license"],
            "source_url": meta["source_url"],
            "provenance": prov, "record_id": rid}


def records_for_sentence(sentence, meta, refs):
    """All records for one sentence, in the fixed emission
    order: sentence_outcome, gap(s), definition_candidate(s),
    word_occurrence(s). Needs a process-local analyzer space;
    see _SPACE."""
    import pmca_population
    prov = _provenance(meta, sentence)
    base = {"book_id": meta["book_id"], "chapter": sentence["chapter"],
            "sentence_index": sentence["sentence_index"]}
    records = []

    outcome = _analyze(sentence["text"], _space())
    sentence_sha = _sha256_text(sentence["text"])
    rec = {"kind": "sentence_outcome", "sentence": sentence["text"],
           "sentence_sha256": sentence_sha,
           "verdict": outcome["verdict"], "omega": outcome["omega"],
           "harm": outcome["harm"], "gate": outcome["gate"],
           "parameters": outcome["parameters"],
           "hold_reason": outcome["hold_reason"],
           "lock_reason": outcome["lock_reason"],
           "gaps": outcome["gaps"], "trace": outcome["trace"],
           "error": outcome["error"], "provenance": prov}
    rec["record_id"] = _record_id(
        dict(base, kind="sentence_outcome",
             sentence_sha256=sentence_sha))
    records.append(rec)

    for ordinal, (gap_kind, detail) in enumerate(_gap_specs(outcome)):
        grec = {"kind": "gap", "gap_kind": gap_kind,
                "gap_ordinal": ordinal, "detail": detail,
                "provenance": prov}
        grec["record_id"] = _record_id(
            dict(base, kind="gap", gap_ordinal=ordinal,
                 gap_kind=gap_kind,
                 detail_sha256=_sha256_text(canonical(detail))))
        records.append(grec)

    hits, _skips = extract_definitions(sentence["text"])
    for hit in hits:
        drec = {"kind": "definition_candidate", "term": hit["term"],
                "term_key": hit["term_key"],
                "construction": hit["construction"],
                "genus_phrase": hit["genus_phrase"],
                "genus_key": hit["genus_key"],
                "extraction": "crude_pattern_v1",
                "status": "CANDIDATE",
                "sentence": sentence["text"], "provenance": prov}
        drec["record_id"] = _record_id(
            dict(base, kind="definition_candidate",
                 term_key=hit["term_key"],
                 construction=hit["construction"]))
        # Content binding (M-2 completion, WG1b): genus and
        # sentence are bound to the record identity at
        # creation. pmca_build_isa recomputes this; altering
        # genus_key or the sentence text after ingest breaks
        # it and the candidate is rejected there, recorded.
        drec["content_binding"] = _sha256_text(canonical({
            "record_id": drec["record_id"],
            "genus_key": hit["genus_key"],
            "sentence_sha256": sentence_sha}))
        records.append(drec)

    for tok in pmca_population._tokens(sentence["text"]):
        orec = {"kind": "word_occurrence", "word_ref": refs[tok],
                "provenance": prov}
        orec["record_id"] = _record_id(
            dict(base, kind="word_occurrence", word=tok))
        records.append(orec)

    return records


_SPACE = None


def _space():
    global _SPACE
    if _SPACE is None:
        import pmca_graphspace
        _SPACE = pmca_graphspace.GraphSpace("book_ingest")
    return _SPACE


# ---------------------------------------------------------------------
# Ingest driver (library entry point)
# ---------------------------------------------------------------------

_WORKER = {}


def _records_for_chapter(chapter_index):
    chapter = _WORKER["chapters"][chapter_index]
    out = []
    for sentence in chapter["sentences"]:
        out.extend(records_for_sentence(
            sentence, _WORKER["meta"], _WORKER["refs"]))
    return out


def ingest_records(book, meta, word_store, mode="serial"):
    """Produce the full record stream for a segmented book.
    Applies word placements to word_store (serial, deterministic)
    before parsing; parsing itself is serial or parallel over
    chapters with an in-source-order merge. Returns (records,
    stats). The stream is a pure function of (book, meta,
    word_store state at entry)."""
    import time
    sentences = [s for ch in book["chapters"] for s in ch["sentences"]]
    refs, new_words, plan, _order = plan_words(sentences, word_store)
    placed = apply_placements(
        word_store, new_words, "book_ingest:" + meta["book_id"])
    for word, event in placed.items():
        refs[word] = _word_ref(word, "ingest_store", event)
    plan["placed"] = len(placed)

    # Book-level binding (M-2 completion, WG1b): the source
    # record is created from the ingested text BEFORE any
    # per-sentence record, and its derived identity is
    # injected into the meta every per-sentence provenance
    # carries. The caller's meta dict is not mutated.
    source_rec = book_source_record(book, meta)
    meta = dict(meta, book_source_id=source_rec["record_id"],
                book_text_sha256=source_rec["text_sha256"])

    start = time.perf_counter()
    if mode == "parallel" and len(book["chapters"]) > 1:
        import multiprocessing
        _WORKER["chapters"] = book["chapters"]
        _WORKER["meta"] = meta
        _WORKER["refs"] = refs
        ctx = multiprocessing.get_context("fork")
        workers = min(os.cpu_count() or 1, len(book["chapters"]))
        with ctx.Pool(processes=workers) as pool:
            per_chapter = pool.map(
                _records_for_chapter, range(len(book["chapters"])))
        records = [r for chunk in per_chapter for r in chunk]
    elif mode in ("serial", "parallel"):
        records = []
        for chapter in book["chapters"]:
            for sentence in chapter["sentences"]:
                records.extend(
                    records_for_sentence(sentence, meta, refs))
    else:
        raise ValueError("mode must be 'serial' or 'parallel'")
    elapsed = time.perf_counter() - start
    records = [source_rec] + records

    stats = {"sentences": len(sentences), "records": len(records),
             "word_plan": plan, "parse_seconds": elapsed,
             "mode": mode,
             "book_source_id": source_rec["record_id"]}
    return records, stats


def stream_bytes(records):
    """The canonical byte stream of a record list: one canonical
    JSON line per record, in stream order."""
    return "".join(canonical(r) + "\n" for r in records).encode("utf-8")


# ---------------------------------------------------------------------
# Candidate store: JSONL, dedup by record-id digest set
# ---------------------------------------------------------------------


class CandidateStore:
    """Append-only JSONL candidate store. A record's identity
    is its record_id (sha256 of its canonical identity form);
    the store keeps the set of ids it already holds and appends
    only records whose id is new, so re-ingesting the same book
    appends zero records. The digest set loaded here is the
    exact input shape the TPU prep pack's array matcher consumes
    (fixed 32-byte digests, set membership); at library scale
    that membership test is the layer the array matcher
    replaces, with identical outcomes.

    Durability (M-3, fixed 2026-11-01; Feeding report §5):
    (a) OPEN tolerates a TORN FINAL LINE: a last line that
    fails to parse (or carries no record_id) is quarantined —
    its raw bytes are preserved verbatim in a quarantine
    record appended to the sidecar file <path>.incomplete.jsonl
    (the incomplete-processes doctrine: an incomplete record
    is stored in a dedicated, searchable file, never silently
    dropped), the torn tail is truncated from the store file,
    and the intact records load normally. A malformed line
    anywhere else is mid-file corruption, not a torn tail,
    and still raises loud. Quarantined records from the most
    recent open are also on self.quarantined.
    (b) COMMIT runs under the tree's single-writer lock —
    pmca_persistence.acquire_lock / release_lock (the ATOM-2
    pattern: O_CREAT|O_EXCL on <dir>/.pmca-writer.lock, loud
    WriterLockHeld refusal, never auto-broken) — and, the
    WG1b completion (R-3, 2026-11-01), AFTER acquiring the
    lock commit RE-READS the store file from disk (reloading
    the committed id set and validating the tail through the
    same scan open uses) before appending anything. A record
    whose id was committed by another writer after this
    instance opened is refused as a duplicate: it is recorded
    in self.refused_duplicates and in the sidecar file
    <path>.duplicates.jsonl, and is never appended a second
    time. (The WG1 docstring claim that the lock alone meant
    committers could "no longer double-append" was falsified
    by the paired check's two-process execution — the lock
    serialized appends but dedup read the stale in-memory
    id set; the re-read under the lock is the mechanism that
    closes it.) Ids this instance already held at open keep
    the plain dedup skip (re-ingest appends zero, no refusal
    recorded). Appends are flushed + fsynced before the lock
    is released."""

    def __init__(self, path):
        self.path = path
        self.ids = set()
        self.quarantined = []
        self.refused_duplicates = []
        self.ids = self._load_ids_from_disk()

    def _load_ids_from_disk(self):
        """Scan the store file and return the committed id
        set, repairing a torn final line exactly as open
        does (quarantine sidecar + truncation; see the class
        docstring). A malformed line anywhere else raises
        loud. Missing file -> empty set. Used by __init__
        and by commit's re-read under the lock."""
        ids = set()
        path = self.path
        if path and os.path.exists(path):
            with open(path, "rb") as fh:
                raw = fh.read()
            segments = raw.split(b"\n")
            offset = 0
            torn = None
            for idx, seg in enumerate(segments):
                seg_start = offset
                offset += len(seg) + 1
                if idx == len(segments) - 1 and seg == b"":
                    break  # trailing newline: terminator, no record
                is_final = idx == len(segments) - 1 or (
                    idx == len(segments) - 2
                    and segments[-1] == b"")
                try:
                    text = seg.decode("utf-8")
                except UnicodeDecodeError as exc:
                    if not is_final:
                        raise
                    torn = (idx + 1, seg.decode("utf-8", "replace"),
                            "torn_final_line: %s" % type(exc).__name__,
                            seg_start)
                    break
                if not text.strip():
                    continue
                try:
                    record = json.loads(text)
                    rid = record["record_id"]
                except (ValueError, KeyError, TypeError) as exc:
                    if not is_final:
                        raise
                    reason = ("torn_final_line: %s"
                              % type(exc).__name__)
                    if isinstance(exc, KeyError):
                        reason = "torn_final_line: missing_record_id"
                    torn = (idx + 1, text, reason, seg_start)
                    break
                ids.add(rid)
            if torn is not None:
                lineno, raw_text, reason, seg_start = torn
                qrec = {"kind": "incomplete_record",
                        "source_store": os.path.basename(str(path)),
                        "line": lineno, "raw": raw_text,
                        "reason": reason}
                qpath = str(path) + ".incomplete.jsonl"
                with open(qpath, "a", encoding="utf-8") as qfh:
                    qfh.write(canonical(qrec) + "\n")
                    qfh.flush()
                    os.fsync(qfh.fileno())
                self.quarantined.append(qrec)
                with open(path, "r+b") as fh:
                    fh.truncate(seg_start)
                    fh.flush()
                    os.fsync(fh.fileno())
        return ids

    def contains_all(self, records):
        return all(r["record_id"] in self.ids for r in records)

    def commit(self, records):
        """Append the records whose ids are still new AT
        COMMIT TIME, under the single-writer lock. After the
        lock is acquired the store file is re-read from disk
        (committed ids reloaded, tail validated — R-3, WG1b):
        a record whose id was committed by another writer
        after this instance opened is refused as a duplicate
        — recorded in self.refused_duplicates and in
        <path>.duplicates.jsonl — and never appended twice.
        Ids already held at open are skipped silently
        (ordinary re-ingest dedup). Returns the number
        appended."""
        import pmca_persistence
        directory = os.path.dirname(os.path.abspath(self.path))
        lock_path = pmca_persistence.acquire_lock(directory)
        try:
            fresh = self._load_ids_from_disk()
            meanwhile = fresh - self.ids
            working = set(fresh)
            refused_now = []
            refused_seen = set()
            appended = 0
            with open(self.path, "a", encoding="utf-8") as fh:
                for rec in records:
                    rid = rec["record_id"]
                    if rid in working:
                        if rid in meanwhile \
                                and rid not in refused_seen:
                            refused_seen.add(rid)
                            refused_now.append({
                                "kind": "duplicate_refused",
                                "record_id": rid,
                                "source_store": os.path.basename(
                                    str(self.path)),
                                "reason": "committed_by_another_"
                                          "writer_after_open"})
                        continue
                    working.add(rid)
                    fh.write(canonical(rec) + "\n")
                    appended += 1
                fh.flush()
                os.fsync(fh.fileno())
            if refused_now:
                dpath = str(self.path) + ".duplicates.jsonl"
                with open(dpath, "a", encoding="utf-8") as dfh:
                    for drec in refused_now:
                        dfh.write(canonical(drec) + "\n")
                    dfh.flush()
                    os.fsync(dfh.fileno())
                self.refused_duplicates.extend(refused_now)
            self.ids = working
            return appended
        finally:
            pmca_persistence.release_lock(lock_path)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="pmca_book_ingest")
    parser.add_argument("book")
    parser.add_argument("--meta", required=True,
                        help="JSON with book_id/title/author/license/source_url")
    parser.add_argument("--mode", default="serial",
                        choices=("serial", "parallel"))
    parser.add_argument("--store", required=True)
    parser.add_argument("--word-store", required=True)
    parser.add_argument("--dump", default=None,
                        help="also write the raw record stream here")
    args = parser.parse_args(argv)

    os.environ.pop("PMCA_DICTIONARY_DB", None)
    with open(args.meta, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    with open(args.book, "r", encoding="utf-8") as fh:
        raw = fh.read()
    book, rule = segment_book(raw)
    word_store = load_word_store(args.word_store)
    records, stats = ingest_records(book, meta, word_store,
                                    mode=args.mode)
    save_word_store(args.word_store, word_store)
    store = CandidateStore(args.store)
    appended = store.commit(records)
    if args.dump:
        with open(args.dump, "wb") as fh:
            fh.write(stream_bytes(records))
    stats["segmentation_rule"] = rule
    stats["appended"] = appended
    stats["store_total"] = len(store.ids)
    print(canonical(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
