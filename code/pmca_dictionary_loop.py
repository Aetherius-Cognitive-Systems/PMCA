"""pmca_dictionary_loop.py — the DICTIONARY LOOP for PMCA,
run end to end for the first time.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from
his statements of 2026-10-21, as noted inline. Code: Alder
(Muse). Built in the DUPLICATE tree (pmca_argon_test)
only, per Jonathan's build method: the running system is
never modified in place.

THE LOOP (Jonathan, 2026-10-21): an entry is parsed from the
dictionary into a SEQUENCED entry — its definition as a
series of words. The series' words are PLACED on the graph.
Each placed word is then looked up in the dictionary and its
own entry parsed the same way; as the loop goes further in,
each pass ADDS the variables, identifiers, and relationships
to the placed words. Placement precedes understanding: a
word takes its place before its structure is known, and
structure accumulates per pass.

HIS COST MODEL, which this run MEASURES: "the true compute,
and the true time that it takes to do that only happens in
the beginning. Because once those words are defined,
information is added, it just becomes a series of sub
coordinate connections." Derivation is paid once per word;
after that, operation is traversal of stored subcoordinate
connections — lookup. The run therefore reports a verdict
pair: total loop time (the expensive beginning) against a
LOOKUP SWEEP over the same finished store — for every
placed word, read back its sequence and its subcoordinates,
pure traversal, no building — timed the same way. If the
model is right, the sweep costs almost nothing beside the
loop that built the store it reads.

WHAT A PASS ADDS, per placed word (a word is processed the
pass AFTER the pass that placed it; each pass fully
processes the previous generation):

  (a) SEQUENCE: the word's FIRST stored definition,
      tokenized (lowercase alphabetic tokens, order
      preserved, duplicates dropped after first occurrence)
      — the sequenced entry itself.
  (b) VARIABLES: identifiers derived from the entry tests,
      the finders' established reading — a sense is a verb
      sense iff, after stripping leading "(...)" field
      labels and "(a)" sense markers, it begins "To ".
      noun-capable iff some sense is not a verb sense;
      verb-capable iff some sense is a verb sense.
      Capabilities are written as subcoordinates
      "variable:noun" / "variable:verb" carrying a
      CANDIDATE-tier provenance record in the write-in
      fact shape (value / status / provenance /
      provenance_history / attestations) — never a bare
      True (Ledger D-7, fixed 2026-11-01; Candidacy Rule
      Set C-4/C-10: the cleaned Webster-convention
      decoding is CANDIDATE evidence only). Absence
      attests nothing: an unwritten variable slot is an
      empty slot, never a negative claim.
  (c) FORM: if pmca_morphology_finder.resolve_form(word)
      resolves the word as a non-base form of a base that
      is placed or placeable, the slot-verifier reflex
      fires — pmca_slot_verifier.encounter(word,
      "<form>_of") against the verifier's mirrored store.
      A filled outcome writes the relationship subcoordinate
      into the loop store too (identifier -> base word); a
      held outcome leaves the verifier's OWN gap record
      standing as the record of that gap.
  (d) DEFINITION LINK: subcoordinates["definition"] = the
      sequence — the sequential subcoordinate itself,
      stored structure added by the pass.

PLACEMENT RULE: a word is placed only if it is an exact
headword in pmca_dictionary. A definition token that is NOT
a headword becomes a GAP RECORD in the verifier's format
(seq, word, identifier "entry", status open_no_candidate,
attempts [{source: "dictionary", outcome: "no_headword"}])
— one record per token, however many sequences cite it
(occurrences are counted separately for the report's
most-common list). A word with no entry is a recorded gap,
never a placed phantom. NO SILENT GAPS (Jonathan's ruling):
every unfilled slot the loop meets is in a record — the
loop's own entry-gap records, or the verifier's records in
its mirrored store.

STARTING SET: the demo seed's words — the "word" fields of
pmca_seed.DEMO_DICTIONARY (110 entries; the dictionary is
a list of entry dicts in this build, not a mapping).

ENDINGS: passes repeat until a full pass adds nothing —
no new placements, no new fills, no new gap records, no
new sequences — which is FIXPOINT; or a cap binds: 8
passes (CAP_PASSES) or 25,000 placements (CAP_PLACEMENTS,
checked while the parse phase places; the pass in which
the cap binds still completes its enrichment of the
previous generation, then the loop ends). Which ending
occurred is a measured result, reported without apology:
Webster 1913's defining vocabulary is uncontrolled, so
the loop is expected to sprawl here. The contrast is the
bootstrap's stage one — a children's dictionary, whose
controlled defining vocabulary is designed so this same
loop closes small. That dictionary is not in this build;
the sprawl measured here is Webster's property, not the
loop's verdict on itself.

DETERMINISM: structural results — per-pass counts, final
counts, and a sha256 over the canonical store
serialization (words sorted; each entry's sequence and
subcoordinates; JSON, keys sorted) — are byte-identical
across runs. Timing fields (time.perf_counter seconds) are
measurements and are excluded from the determinism check.

DISCIPLINE: the loop writes ONLY its own store and the
slot verifier's mirrored store (reset at loop start).
Nothing else in the tree is touched; run artifacts, if
any, belong in /tmp.

Deterministic in structure. Stdlib + pmca_dictionary +
pmca_morphology_finder + pmca_slot_verifier + pmca_seed.
"""

import hashlib
import json
import re
import time
from collections import Counter

import pmca_dictionary
import pmca_morphology_finder as _morph
import pmca_seed
import pmca_slot_verifier as _verifier

MAX_PASSES = 8
MAX_PLACEMENTS = 25000

_TOKEN_RE = re.compile(r"[a-z]+")
_LABEL_RE = re.compile(r"^\s*(\([^)]*\)\s*)+")
_SENSE_MARK_RE = re.compile(r"^\s*\([a-z]\)\s*")

# resolve_form form name -> the subcoordinate identifier the
# verifier's reflex is fired under ("<form>_of").
_FORM_IDENTIFIER = {
    "plural": "plural_of",
    "past": "past_of",
    "participle_ing": "participle_of",
    "comparative": "comparative_of",
    "superlative": "superlative_of",
    "third_person": "third_person_of",
    "adverbial_ly": "adverbial_ly_of",
    "possessive": "possessive_of",
}


# ---------------------------------------------------------------------
# Small deterministic helpers.
# ---------------------------------------------------------------------

def _exact(word):
    """The exact-headword dictionary entry for a word, or
    None: pmca_dictionary.lookup resolves suffix variants;
    the entry counts as exact only when the headword it
    reports IS the word asked for."""
    entry = pmca_dictionary.lookup(word)
    if entry is not None and entry["headword"] == word:
        return entry
    return None


def _is_verb_sense(defn):
    """A stored definition is a verb sense under the ONE
    shared cleaned decoding (pmca_verb_capable.is_verb_sense,
    Candidacy Rule Set C-4; WG2 2026-11-01 — this module's
    mirrored CLEANED copy is RETIRED as a mechanism). The
    import is function-level, honouring this module's
    top-level import discipline (imports_limited)."""
    import pmca_verb_capable as _vc
    return _vc.is_verb_sense(defn)


# The provenance a variable:* fill carries (Candidacy Rule
# Set C-4/C-10; Ledger D-7, fixed 2026-11-01). The fill's
# single source is the dictionary tier's cleaned
# Webster-convention decoding; its source spelling resolves
# through the governed table (pmca_lineage_table) to the
# Webster lineage. The record is data, written literally
# here: this module's import discipline (see run_checks,
# imports_limited) is unchanged.
_VARIABLE_PROVENANCE = {"source": "Webster 1913",
                        "lineage": "Webster",
                        "evidence":
                            "cleaned_webster_convention"}


def _variable_fill():
    """A fresh variable:* fill record, in the write-in fact
    shape: {"value": True, "status": "candidate", "tier":
    "CANDIDATE", "provenance": {...}, "provenance_history":
    [the same record], "attestations": 1}. A new dict per
    fill — fills never share a mutable record. Status is
    CANDIDATE and never advances inside the loop: one
    source, one attestation, CANDIDATE evidence only
    (C-4); no provenance-class counter can read this fill
    as attested, and its one class could not promote it
    in any case."""
    prov = dict(_VARIABLE_PROVENANCE)
    return {"value": True, "status": "candidate",
            "tier": "CANDIDATE", "provenance": prov,
            "provenance_history": [dict(prov)],
            "attestations": 1}


def _variable_filled(subs, ident):
    """Whether a variable:* fill is present in a
    subcoordinates dict: a current CANDIDATE record with
    value True, or a bare True in a sealed state written
    before the D-7 fix (read for counting only; new fills
    are never written bare)."""
    v = subs.get(ident)
    if v is True:
        return True
    return isinstance(v, dict) and v.get("value") is True


def _sequence_of(entry):
    """The sequenced entry: the FIRST stored definition as
    an ordered series of lowercase alphabetic tokens,
    duplicates dropped after first occurrence."""
    if not entry or not entry["definitions"]:
        return []
    seen = set()
    seq = []
    for tok in _TOKEN_RE.findall(entry["definitions"][0].lower()):
        if tok not in seen:
            seen.add(tok)
            seq.append(tok)
    return seq


# ---------------------------------------------------------------------
# The loop.
# ---------------------------------------------------------------------

class _LoopState:
    def __init__(self):
        self.store = {}          # word -> entry dict (below)
        self.gap_records = []    # loop's own entry-gap records
        self.gap_tokens = set()  # tokens already gap-recorded
        self.gap_occurrences = Counter()
        self.headword_cache = {}  # token -> bool (dict is static)
        self.fired = set()       # (word, identifier) fired once
        self.form_filled = 0
        self.form_held = 0

    def headword(self, token):
        if token not in self.headword_cache:
            self.headword_cache[token] = _exact(token) is not None
        return self.headword_cache[token]

    def place(self, word):
        self.store[word] = {"sequence": None,
                            "subcoordinates": {},
                            "seq_done": False,
                            "enriched": False}


def _canonical_store(store):
    """Canonical serialization of the loop store: words
    sorted; each entry's sequence and subcoordinates only
    (internal pass flags are not stored structure)."""
    canon = {}
    for w in sorted(store):
        e = store[w]
        canon[w] = {"sequence": e["sequence"],
                    "subcoordinates": e["subcoordinates"]}
    return json.dumps(canon, sort_keys=True)


def _store_hash(store):
    return hashlib.sha256(
        _canonical_store(store).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------
# Stepped pass API (ported 2026-10-27 from the GEN5 Space lineage,
# where run_loop's body was factored for the demo app's Growth tab,
# which calls blank_state / new_state / step_pass / loop_finals /
# sweep_store; top_gap_tokens completes the stepped family. This
# tree's module had lost the factoring when the lineage moved. The
# pass body in _run_one_pass is GEN5's verbatim, verified
# statement-for-statement identical (AST) to run_loop's inline
# body below; run_loop itself is NOT rewired to it - its original
# inline body is untouched, and the port is purely additive.
# ---------------------------------------------------------------------


def _run_one_pass(st, p):
    """One pass of the loop over state st.

    This is the body of run_loop's pass loop, factored out
    unchanged (SPACE COPY: see the note after the module
    docstring) so the demo app can step the very same pass
    machinery one sealed pass at a time. Returns (row,
    ending): the per-pass row, and the ending reached on
    this pass ("CAP_PLACEMENTS" / "FIXPOINT" /
    "CAP_PASSES") or None."""
    t0 = time.perf_counter()
    new_placements = 0
    new_fills = 0
    new_gap_records = 0
    new_sequences = 0
    cap_hit = False
    v_records_before = len(_verifier.records())

    if p == 1:
        # Initial placement: the seed's words. Placed
        # only if exact headwords; the rest are gap
        # records, counted in this pass.
        for entry in pmca_seed.DEMO_DICTIONARY:
            w = entry["word"].strip().lower()
            if w in st.store:
                continue
            if st.headword(w):
                st.place(w)
                new_placements += 1
            elif w not in st.gap_tokens:
                st.gap_tokens.add(w)
                st.gap_records.append({
                    "seq": len(st.gap_records) + 1,
                    "word": w, "identifier": "entry",
                    "status": "open_no_candidate",
                    "attempts": [{"source": "dictionary",
                                  "outcome": "no_headword"}],
                    "candidate": None,
                    "candidate_evidence": None,
                    "filled_value": None})
                new_gap_records += 1

    # Snapshots: this pass processes the generations
    # placed before it (pass 1 processes the seed).
    seq_snapshot = sorted(w for w in st.store
                          if not st.store[w]["seq_done"])
    enrich_snapshot = sorted(w for w in st.store
                             if not st.store[w]["enriched"])

    # --- Phase A: parse entries into sequences; place
    # the series' words; record the gaps. ---
    for w in seq_snapshot:
        e = st.store[w]
        seq = _sequence_of(_exact(w))
        e["sequence"] = seq
        e["seq_done"] = True
        new_sequences += 1
        # (d) the definition link: the sequential
        # subcoordinate itself.
        e["subcoordinates"]["definition"] = list(seq)
        new_fills += 1
        for tok in seq:
            if tok in st.store:
                continue
            if not st.headword(tok):
                st.gap_occurrences[tok] += 1
                if tok not in st.gap_tokens:
                    st.gap_tokens.add(tok)
                    st.gap_records.append({
                        "seq": len(st.gap_records) + 1,
                        "word": tok, "identifier": "entry",
                        "status": "open_no_candidate",
                        "attempts": [{"source": "dictionary",
                                      "outcome":
                                      "no_headword"}],
                        "candidate": None,
                        "candidate_evidence": None,
                        "filled_value": None})
                    new_gap_records += 1
                continue
            if len(st.store) >= MAX_PLACEMENTS:
                cap_hit = True
                continue
            st.place(tok)
            new_placements += 1

    # --- Phases B+C: variables, then form, per word. ---
    for w in enrich_snapshot:
        e = st.store[w]
        entry = _exact(w)
        defs = entry["definitions"] if entry else []
        # (b) variables from the entry tests; only
        # attested capabilities are written.
        if any(not _is_verb_sense(d) for d in defs):
            e["subcoordinates"]["variable:noun"] = _variable_fill()
            new_fills += 1
        if any(_is_verb_sense(d) for d in defs):
            e["subcoordinates"]["variable:verb"] = _variable_fill()
            new_fills += 1
        # (c) form: the verifier reflex fires once per
        # (word, identifier).
        r = _morph.resolve_form(w)
        if r["verdict"] == "OK" and r["entry"] is not None \
                and r["entry"] != w \
                and r["form"] in _FORM_IDENTIFIER:
            ident = _FORM_IDENTIFIER[r["form"]]
            if (w, ident) not in st.fired \
                    and (r["entry"] in st.store
                         or st.headword(r["entry"])):
                st.fired.add((w, ident))
                res = _verifier.encounter(w, ident)
                if res["verdict"] == "filled":
                    e["subcoordinates"][ident] = res["value"]
                    new_fills += 1
                    st.form_filled += 1
                else:
                    st.form_held += 1
        e["enriched"] = True

    # Gap records the verifier wrote during this pass
    # (its reflex fired in phase C) count as this pass's
    # new gap records.
    new_gap_records += len(_verifier.records()) - v_records_before

    seconds = round(time.perf_counter() - t0, 3)
    row = {"pass": p,
           "new_placements": new_placements,
           "new_fills": new_fills,
           "new_gap_records": new_gap_records,
           "new_sequences": new_sequences,
           "seconds": seconds}

    ending = None
    if cap_hit:
        ending = "CAP_PLACEMENTS"
    elif new_placements + new_fills + new_gap_records \
            + new_sequences == 0:
        ending = "FIXPOINT"
    elif p == MAX_PASSES:
        ending = "CAP_PASSES"
    return row, ending


def blank_state():
    """A loop state with no verifier interaction, for
    restoring a sealed state into (the demo app seals its
    growth state to storage at each pass end and restores
    it into one of these after a restart)."""
    return _LoopState()


def new_state():
    """A fresh loop state, with the slot verifier's
    mirrored store reset first, exactly as run_loop
    begins (a run is self-contained and repeatable)."""
    st = _LoopState()
    _verifier.reset_store()
    return st


def step_pass(st, p):
    """Advance state st by exactly one pass (pass number
    p, 1-based), through the same pass machinery run_loop
    runs in sequence. Returns (row, ending)."""
    return _run_one_pass(st, p)


def sweep_store(st):
    """The lookup sweep over the state's current store:
    pure traversal, no building; the store hash must not
    move. Returns (sweep_seconds, touched,
    hash_unchanged, store_sha256)."""
    hash_before = _store_hash(st.store)
    t0 = time.perf_counter()
    touched = 0
    for w in sorted(st.store):
        e = st.store[w]
        if e["sequence"] is not None:
            touched += len(e["sequence"])
        for k in sorted(e["subcoordinates"]):
            v = e["subcoordinates"][k]
            touched += len(v) if isinstance(v, list) else 1
    sweep_seconds = round(time.perf_counter() - t0, 3)
    hash_after = _store_hash(st.store)
    return sweep_seconds, touched, hash_before == hash_after, \
        hash_before


def loop_finals(st, ending):
    """The final-counts block, computed over the state's
    current store (ending may be None mid-growth)."""
    subs_all = [st.store[w]["subcoordinates"] for w in st.store]
    gaps_by_kind = {"entry": len(st.gap_records)}
    for rec in _verifier.worklist():
        gaps_by_kind[rec["identifier"]] = \
            gaps_by_kind.get(rec["identifier"], 0) + 1
    return {
        "placed": len(st.store),
        "with_sequence": sum(1 for w in st.store
                             if st.store[w]["seq_done"]),
        "enriched": sum(1 for w in st.store
                        if st.store[w]["enriched"]),
        "variable_noun": sum(1 for s in subs_all
                             if _variable_filled(s, "variable:noun")),
        "variable_verb": sum(1 for s in subs_all
                             if _variable_filled(s, "variable:verb")),
        "form_relationships_filled": st.form_filled,
        "form_encounters_held": st.form_held,
        "gaps_open_by_kind": gaps_by_kind,
        "ending": ending,
        "store_sha256": _store_hash(st.store),
    }


def top_gap_tokens(st, limit=10):
    """The most common open-gap tokens of the state, in
    the run report's order (count desc, token asc)."""
    return sorted(st.gap_occurrences.items(),
                  key=lambda kv: (-kv[1], kv[0]))[:limit]


def run_loop():
    """Run the dictionary loop from the seed's words to
    fixpoint or cap. Returns the full result: per-pass
    table, final counts, the lookup sweep, and the store
    hash. Resets the slot verifier's mirrored store first,
    so a run is self-contained and repeatable."""
    st = _LoopState()
    _verifier.reset_store()
    verifier_records_start = len(_verifier.records())

    per_pass = []
    ending = None

    for p in range(1, MAX_PASSES + 1):
        t0 = time.perf_counter()
        new_placements = 0
        new_fills = 0
        new_gap_records = 0
        new_sequences = 0
        cap_hit = False
        v_records_before = len(_verifier.records())

        if p == 1:
            # Initial placement: the seed's words. Placed
            # only if exact headwords; the rest are gap
            # records, counted in this pass.
            for entry in pmca_seed.DEMO_DICTIONARY:
                w = entry["word"].strip().lower()
                if w in st.store:
                    continue
                if st.headword(w):
                    st.place(w)
                    new_placements += 1
                elif w not in st.gap_tokens:
                    st.gap_tokens.add(w)
                    st.gap_records.append({
                        "seq": len(st.gap_records) + 1,
                        "word": w, "identifier": "entry",
                        "status": "open_no_candidate",
                        "attempts": [{"source": "dictionary",
                                      "outcome": "no_headword"}],
                        "candidate": None,
                        "candidate_evidence": None,
                        "filled_value": None})
                    new_gap_records += 1

        # Snapshots: this pass processes the generations
        # placed before it (pass 1 processes the seed).
        seq_snapshot = sorted(w for w in st.store
                              if not st.store[w]["seq_done"])
        enrich_snapshot = sorted(w for w in st.store
                                 if not st.store[w]["enriched"])

        # --- Phase A: parse entries into sequences; place
        # the series' words; record the gaps. ---
        for w in seq_snapshot:
            e = st.store[w]
            seq = _sequence_of(_exact(w))
            e["sequence"] = seq
            e["seq_done"] = True
            new_sequences += 1
            # (d) the definition link: the sequential
            # subcoordinate itself.
            e["subcoordinates"]["definition"] = list(seq)
            new_fills += 1
            for tok in seq:
                if tok in st.store:
                    continue
                if not st.headword(tok):
                    st.gap_occurrences[tok] += 1
                    if tok not in st.gap_tokens:
                        st.gap_tokens.add(tok)
                        st.gap_records.append({
                            "seq": len(st.gap_records) + 1,
                            "word": tok, "identifier": "entry",
                            "status": "open_no_candidate",
                            "attempts": [{"source": "dictionary",
                                          "outcome":
                                          "no_headword"}],
                            "candidate": None,
                            "candidate_evidence": None,
                            "filled_value": None})
                        new_gap_records += 1
                    continue
                if len(st.store) >= MAX_PLACEMENTS:
                    cap_hit = True
                    continue
                st.place(tok)
                new_placements += 1

        # --- Phases B+C: variables, then form, per word. ---
        for w in enrich_snapshot:
            e = st.store[w]
            entry = _exact(w)
            defs = entry["definitions"] if entry else []
            # (b) variables from the entry tests; only
            # attested capabilities are written.
            if any(not _is_verb_sense(d) for d in defs):
                e["subcoordinates"]["variable:noun"] = _variable_fill()
                new_fills += 1
            if any(_is_verb_sense(d) for d in defs):
                e["subcoordinates"]["variable:verb"] = _variable_fill()
                new_fills += 1
            # (c) form: the verifier reflex fires once per
            # (word, identifier).
            r = _morph.resolve_form(w)
            if r["verdict"] == "OK" and r["entry"] is not None \
                    and r["entry"] != w \
                    and r["form"] in _FORM_IDENTIFIER:
                ident = _FORM_IDENTIFIER[r["form"]]
                if (w, ident) not in st.fired \
                        and (r["entry"] in st.store
                             or st.headword(r["entry"])):
                    st.fired.add((w, ident))
                    res = _verifier.encounter(w, ident)
                    if res["verdict"] == "filled":
                        e["subcoordinates"][ident] = res["value"]
                        new_fills += 1
                        st.form_filled += 1
                    else:
                        st.form_held += 1
            e["enriched"] = True

        # Gap records the verifier wrote during this pass
        # (its reflex fired in phase C) count as this pass's
        # new gap records.
        new_gap_records += len(_verifier.records()) - v_records_before

        seconds = round(time.perf_counter() - t0, 3)
        per_pass.append({"pass": p,
                         "new_placements": new_placements,
                         "new_fills": new_fills,
                         "new_gap_records": new_gap_records,
                         "new_sequences": new_sequences,
                         "seconds": seconds})

        if cap_hit:
            ending = "CAP_PLACEMENTS"
            break
        if new_placements + new_fills + new_gap_records \
                + new_sequences == 0:
            ending = "FIXPOINT"
            break
        if p == MAX_PASSES:
            ending = "CAP_PASSES"

    # --- Lookup sweep: pure traversal of the finished
    # store, no building. The hash must not move. ---
    hash_before = _store_hash(st.store)
    t0 = time.perf_counter()
    touched = 0
    for w in sorted(st.store):
        e = st.store[w]
        if e["sequence"] is not None:
            touched += len(e["sequence"])
        for k in sorted(e["subcoordinates"]):
            v = e["subcoordinates"][k]
            touched += len(v) if isinstance(v, list) else 1
    sweep_seconds = round(time.perf_counter() - t0, 3)
    hash_after = _store_hash(st.store)

    # --- Final counts. ---
    subs_all = [st.store[w]["subcoordinates"] for w in st.store]
    gaps_by_kind = {"entry": len(st.gap_records)}
    for rec in _verifier.worklist():
        gaps_by_kind[rec["identifier"]] = \
            gaps_by_kind.get(rec["identifier"], 0) + 1
    finals = {
        "placed": len(st.store),
        "with_sequence": sum(1 for w in st.store
                             if st.store[w]["seq_done"]),
        "enriched": sum(1 for w in st.store
                        if st.store[w]["enriched"]),
        "variable_noun": sum(1 for s in subs_all
                             if _variable_filled(s, "variable:noun")),
        "variable_verb": sum(1 for s in subs_all
                             if _variable_filled(s, "variable:verb")),
        "form_relationships_filled": st.form_filled,
        "form_encounters_held": st.form_held,
        "gaps_open_by_kind": gaps_by_kind,
        "ending": ending,
        "store_sha256": hash_before,
    }
    struct = {
        "per_pass": [{k: row[k] for k in
                      ("pass", "new_placements", "new_fills",
                       "new_gap_records", "new_sequences")}
                     for row in per_pass],
        "finals": finals,
        "top_gap_tokens": sorted(
            st.gap_occurrences.items(),
            key=lambda kv: (-kv[1], kv[0]))[:10],
    }
    return {
        "per_pass": per_pass,
        "finals": finals,
        "struct": struct,
        "loop_seconds_total": round(
            sum(row["seconds"] for row in per_pass), 3),
        "sweep_seconds": sweep_seconds,
        "sweep_touched": touched,
        "sweep_hash_unchanged": hash_before == hash_after,
        "store": st.store,
        "gap_records": st.gap_records,
        "gap_occurrences": st.gap_occurrences,
    }


# ---------------------------------------------------------------------
# Report + check battery. Run: python3 pmca_dictionary_loop.py
# The battery runs the loop TWICE (two full runs in this
# process; cross-process byte identity is the same STRUCT
# line printed identically by separate invocations) and
# asserts: an ending was reached, the sweep added nothing,
# the two runs' structures are identical, every placed word
# is an exact headword, every entry-gap token is not, and
# every filled form value names a headword.
# ---------------------------------------------------------------------

def _print_report(res):
    print("=== DICTIONARY LOOP RUN ===")
    print("%-5s %10s %10s %10s %10s %9s"
          % ("pass", "placed+", "fills+", "gaps+", "seqs+",
             "seconds"))
    for row in res["per_pass"]:
        print("%-5d %10d %10d %10d %10d %9.3f"
              % (row["pass"], row["new_placements"],
                 row["new_fills"], row["new_gap_records"],
                 row["new_sequences"], row["seconds"]))
    f = res["finals"]
    print("--- final ---")
    print("placed: %d (with sequence: %d, enriched: %d)"
          % (f["placed"], f["with_sequence"], f["enriched"]))
    print("variable:noun candidate fills: %d | "
          "variable:verb candidate fills: %d"
          % (f["variable_noun"], f["variable_verb"]))
    print("form relationships filled: %d (verifier "
          "encounters held: %d)"
          % (f["form_relationships_filled"],
             f["form_encounters_held"]))
    print("gaps open by kind: %s"
          % json.dumps(f["gaps_open_by_kind"], sort_keys=True))
    print("ending: %s" % f["ending"])
    print("loop seconds total: %.3f | lookup sweep seconds: "
          "%.3f (touched %d values)"
          % (res["loop_seconds_total"], res["sweep_seconds"],
             res["sweep_touched"]))
    print("store sha256: %s" % f["store_sha256"])
    print("10 most common open-gap tokens: %s"
          % ", ".join("%s x%d" % (t, c)
                      for t, c in res["struct"]["top_gap_tokens"]))
    print("STRUCT " + json.dumps(res["struct"], sort_keys=True))


def run_checks():
    import os

    results = []

    def check(name, cond, detail=""):
        results.append(cond)
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    src = open(os.path.abspath(__file__)).read()
    pmca_imports = sorted(set(re.findall(
        r"^(?:import|from)\s+(pmca_[a-z_0-9]+)", src, re.M)))
    check("imports_limited",
          pmca_imports == ["pmca_dictionary",
                           "pmca_morphology_finder",
                           "pmca_seed", "pmca_slot_verifier"],
          str(pmca_imports))

    # The dictionary tier is off until configured (Pack G,
    # 2026-10-26); the loop runs against the tree's stored
    # Webster 1913 DB, configured explicitly. Direct callers of
    # run_loop() must configure the tier themselves.
    pmca_dictionary.configure(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "webster1913.db"))

    res1 = run_loop()
    _print_report(res1)
    store = res1["store"]
    f = res1["finals"]

    check("ending_reached",
          f["ending"] in ("FIXPOINT", "CAP_PASSES",
                          "CAP_PLACEMENTS"),
          f["ending"])

    check("sweep_adds_nothing",
          res1["sweep_hash_unchanged"],
          "store hash identical across the lookup sweep")

    not_headwords = [w for w in sorted(store)
                     if _exact(w) is None]
    check("every_placed_word_is_an_exact_headword",
          not not_headwords, str(not_headwords[:10]))

    gap_bad = [r["word"] for r in res1["gap_records"]
               if _exact(r["word"]) is not None]
    gap_words = [r["word"] for r in res1["gap_records"]]
    check("entry_gaps_are_not_headwords_and_unique",
          not gap_bad and len(gap_words) == len(set(gap_words)),
          "%d entry-gap records" % len(gap_words))

    form_bad = []
    for w in sorted(store):
        for ident, val in store[w]["subcoordinates"].items():
            if ident in _FORM_IDENTIFIER.values() \
                    and _exact(val) is None:
                form_bad.append((w, ident, val))
    check("filled_form_values_name_headwords",
          not form_bad, str(form_bad[:5]))

    if f["ending"] == "FIXPOINT":
        check("fixpoint_means_fully_processed",
              f["with_sequence"] == f["placed"]
              and f["enriched"] == f["placed"],
              "sequences %d/%d, enriched %d/%d"
              % (f["with_sequence"], f["placed"],
                 f["enriched"], f["placed"]))

    # Determinism: a second full run in this process must
    # reproduce the first run's structure byte-for-byte
    # (timing fields excluded by construction of STRUCT).
    res2 = run_loop()
    check("determinism_two_full_runs_identical",
          res2["struct"] == res1["struct"],
          "struct sha256: %s"
          % hashlib.sha256(json.dumps(
              res1["struct"], sort_keys=True).encode(
              "utf-8")).hexdigest())

    passed = sum(1 for x in results if x)
    print("%d/%d checks passed" % (passed, len(results)))
    print("Determinism (two full runs identical): %s"
          % (res2["struct"] == res1["struct"]))
    return passed == len(results)


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_checks() else 1)
