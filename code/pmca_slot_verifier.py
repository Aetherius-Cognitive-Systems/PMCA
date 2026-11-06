"""pmca_slot_verifier.py — the EMPTY-SLOT VERIFIER for PMCA.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings of 2026-10-21, as noted inline. Code: Alder (Muse).
Built in the DUPLICATE tree (pmca_argon_test) only, per
Jonathan's build method: the running system is never modified
in place.

What this is: a deliberately small proof slice of the mechanism
Jonathan designed on 2026-10-21. In his data model, a dictionary
pointing note ("children: plural of child", "went: past of go")
is a RELATIONSHIP held inside a word's entry as a subcoordinate:
an identifier (plural_of, past_of, is_a, ...) plus ONE coordinate
value, the target word. The book prints subcoordinates as text;
the system stores them as values. An entry whose slot for an
identifier is empty has a GAP, and Jonathan's ruling is absolute:
NO SILENT GAPS — every gap is recorded.

The verifier is a reflex expressed as an IF-THEN rule (Jonathan:
"An if then rule is a reflex"): IF an encounter finds an empty
subcoordinate slot or a missing variable, THEN a build attempt
runs from the dictionary, the morphology, and the entry store.
encounter(word, identifier) is that rule:

  (1) IF the entry already holds the slot -> verdict "lookup",
      value returned, NO record written (zero-cost retraversal:
      a recorded answer is read, never re-derived).
  (2) ELSE a gap record is appended FIRST (the gap exists in the
      record before any attempt runs), the attempts run in fixed
      order, and the first VERIFIED candidate fills the slot in
      the store -> verdict "filled".
  (3) If no verified candidate exists but a weak candidate does,
      the candidate is recorded, labeled UNVERIFIED, and the
      slot stays EMPTY -> verdict "held".
  (4) If nothing is found -> the record stands open_no_candidate
      -> verdict "held".

GOVERNING VERIFICATION STANDARD (the build's rule, stated here
as in the brief): a candidate fill is VERIFIED iff attested by
  (a) a dictionary text note naming the target (the pointing
      note itself, in the stored definitions), or
  (b) a morphology rule whose base resolves as a headword
      (pmca_morphology_finder.resolve_form attesting the word as
      that form of the base), or
  (c) a relationship already stored in the entry store.
Weaker evidence — definition identity or containment, the
overlap the morphology module reports as "covering entries" —
produces a recorded CANDIDATE that is NEVER promoted. A fill
written on overlap alone would be a guess wearing a rule's
clothes; the cook/eat derivation errors are what that path
produces.

DATA MODEL (v1 proof slice): an in-memory entry store,
word -> {"subcoordinates": {identifier: target_word}}, plus an
append-only list of gap records {seq, word, identifier,
gap_key ("slot::<word>::<identifier>"), status,
attempts, candidate, candidate_evidence, filled_value} with
statuses open_no_candidate | open_with_candidate | filled.
Records carry sequence numbers only — NO wall-clock timestamps —
so two runs of the same encounters produce byte-identical
records. worklist() returns the records still open: the gaps
are a countable, revisit-able worklist, closable later against
the record that stands open with the word's name on it.

PERSISTENCE CHOICE (stated per the brief): records and store
are SEPARATE artifacts, with separate dump/load functions.
dump_records()/load_records() round-trip the gap history;
dump_store()/load_store() round-trip the entries with their
filled slots. A deployment persists both: loading records
without the store does not resurrect fills, and loading the
store without the records keeps the fills but loses the gap
history that explains them. The battery proves the pair: after
a round-trip of both, a filled slot still resolves by lookup
and writes no new record.

DIRECTION ASYMMETRY (disclosed): v1 fills BACK-pointers on form
words — encounter(wolves, plural_of) fills because morphology
attests wolves as the plural OF wolf. A FORWARD pointer on a
base word — encounter(child, plural_of), the slot naming the
base's own plural form — is never FILLED in v1: no v1 source
attests it (the inverse morphology direction is out of scope).
The overlap channel may still record an unverified candidate
for it (children, by definition identity); filling forward
pointers awaits a form_family pass over the store.

THE OVERLAP CHANNEL (weak candidates), selection rule: only
single-word headword entries are considered (phrase entries
whose stored text is identical — "child's play", "to be with
child" — are copies onto idioms, not form relatives). (i) If
exactly one such entry's stored definitions are IDENTICAL to
the word's, it is the candidate (definition-identity). (ii)
Else, if the overlapping entries jointly cover every stored
definition of the word, the candidate is the largest-share
entry, provided exactly one of the largest-share entries
resolves in morphology as its own base (definition-
containment). Otherwise no candidate: partial overlap is
quotation noise and mints nothing.

MEASURED FACTS about this dictionary build (all verified
against the stored text; see pmca_morphology_finder for the
full account): the extraction replaced GCIDE's cross-reference
entries with verbatim copies of base entries' definitions, so
attestation channel (a), the text note, fires zero times on
this battery's vocabulary — the notes were lost with the text.
went's five stored definitions are wend's two and go's three,
verbatim; children's five are identical to child's. Those facts
are why went and children HOLD here with recorded candidates
instead of filling: the relationships are real English, and
this text does not attest them.

Scope v1: fills land ONLY in this module's test store. No
wiring into the finders or the analyzer, no forward-pointer
filling, no writes to the live seed graph.

Deterministic. Stdlib + pmca_dictionary +
pmca_morphology_finder only.
"""

import copy
import json
import re
import sqlite3

import pmca_dictionary
import pmca_morphology_finder as _morph


# The form identifiers the morphology channel can attest, mapped
# to the resolve_form form name that attests them.
_FORM_OF_IDENTIFIER = {
    "plural_of": "plural",
    "past_of": "past",
    "participle_of": "participle_ing",
    "comparative_of": "comparative",
}

# The entry-store neighbor channel: a stored inverse relationship
# attests the same link from the other side. v1's inverse names.
_INVERSE_OF = {
    "plural_of": "plural_form_of",
    "past_of": "past_form_of",
    "participle_of": "participle_form_of",
    "comparative_of": "comparative_form_of",
}

# Pointing-note patterns in stored definition text, mirroring
# pmca_morphology_finder's text-attestation machinery (its
# _ATTEST_RE), plus the plain-language forms named in the brief
# ("plural of", "past of"). Maps a matched form label to the
# identifier it attests.
_NOTE_FORM_TO_IDENTIFIER = {
    "pl": "plural_of", "plural": "plural_of",
    "imp": "past_of", "pret": "past_of", "p. p": "past_of",
    "past": "past_of",
    "pres. p": "participle_of",
}
_NOTE_RE = re.compile(
    r"\b(pl|imp|pret|p\. p|pres\. p)\.\s+of\s+\{?([A-Za-z]+)\}?"
    r"|\b(plural|past)\s+of\s+\{?([A-Za-z]+)\}?")


# ---------------------------------------------------------------------
# Entry store + gap records (module state; reset_store() restores
# the battery fixture).
# ---------------------------------------------------------------------

_FIXTURE = {
    "wolf": {"subcoordinates": {"is_a": "canine"}},
    "child": {"subcoordinates": {}},
    "go": {"subcoordinates": {}},
    "went": {"subcoordinates": {}},
    "children": {"subcoordinates": {}},
    "wolves": {"subcoordinates": {}},
}

_STORE = {}
_RECORDS = []


def reset_store():
    """Restore the battery fixture store and an empty record
    list. The fixture seeds wolf with one stored relationship
    (is_a: canine) and five words with empty entries, so both
    the lookup path and every gap path are exercised."""
    global _STORE, _RECORDS
    _STORE = copy.deepcopy(_FIXTURE)
    _RECORDS = []


reset_store()


def _store_entry(word):
    return _STORE.get(word)


def store():
    """A deep copy of the entry store."""
    return copy.deepcopy(_STORE)


def records():
    """A deep copy of all gap records, in seq order."""
    return copy.deepcopy(_RECORDS)


def worklist():
    """The gap records still open (status != filled), in seq
    order: the system's own list of what it is missing."""
    return [copy.deepcopy(r) for r in _RECORDS
            if r["status"] != "filled"]


def dump_records():
    """The gap records as canonical JSON (one artifact)."""
    return json.dumps(_RECORDS, sort_keys=True)


def load_records(text):
    """Replace the gap records from dump_records() output.
    The store is untouched (separate artifact)."""
    global _RECORDS
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("records artifact must be a JSON list")
    _RECORDS = parsed


def dump_store():
    """The entry store as canonical JSON (one artifact)."""
    return json.dumps(_STORE, sort_keys=True)


def load_store(text):
    """Replace the entry store from dump_store() output.
    The records are untouched (separate artifact)."""
    global _STORE
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("store artifact must be a JSON object")
    _STORE = parsed


# ---------------------------------------------------------------------
# Evidence channels.
# ---------------------------------------------------------------------

def _exact_entry(word):
    """The exact-headword dictionary entry for a word, or None
    (pmca_dictionary.lookup resolves suffix variants; the entry
    counts as exact only when the headword it reports IS the
    word asked for — the morphology module's _exact discipline,
    mirrored)."""
    entry = pmca_dictionary.lookup(word)
    if entry is not None and entry["headword"] == word:
        return entry
    return None


def _attempt_note(word, identifier, defs):
    """Channel (a): the dictionary text note. Scan the word's
    stored definitions for a pointing note naming a target for
    this identifier. Returns (fill_value | None, attempt)."""
    if defs is None:
        return None, {"source": "dictionary_note",
                      "evidence": "word has no dictionary entry",
                      "outcome": "no_note"}
    for d in defs:
        m = _NOTE_RE.search(d)
        if not m:
            continue
        label = m.group(1) or m.group(3)
        target = (m.group(2) or m.group(4)).lower()
        frag = d[max(0, m.start() - 10):m.end() + 10]
        if _NOTE_FORM_TO_IDENTIFIER.get(label) != identifier:
            return None, {
                "source": "dictionary_note",
                "evidence": "stored text carries a note for a "
                            "different identifier: " + frag,
                "outcome": "note_for_other_identifier"}
        if _exact_entry(target) is None:
            return None, {
                "source": "dictionary_note",
                "evidence": "note names a target that is not a "
                            "headword: " + frag,
                "outcome": "note_target_unattested"}
        return target, {
            "source": "dictionary_note",
            "evidence": "pointing note in stored definition: "
                        + frag,
            "outcome": "verified_fill"}
    return None, {"source": "dictionary_note",
                  "evidence": "no pointing-note pattern in %d "
                              "stored definitions" % len(defs),
                  "outcome": "no_note"}


def _attempt_morphology(word, identifier):
    """Channel (b): morphology. Direct direction only —
    resolve_form(word) must attest the word as the identifier's
    form of a base that resolves as a headword. Returns
    (fill_value | None, attempt)."""
    wanted = _FORM_OF_IDENTIFIER.get(identifier)
    if wanted is None:
        return None, {"source": "morphology",
                      "evidence": "identifier is not a form "
                                  "identifier morphology attests",
                      "outcome": "not_a_form_identifier"}
    r = _morph.resolve_form(word)
    if r["verdict"] == "OK" and r["form"] == wanted \
            and r["entry"] is not None and r["entry"] != word:
        return r["entry"], {
            "source": "morphology",
            "evidence": "resolve_form attests %s as %s of %s: %s"
                        % (word, r["form"], r["entry"],
                           r["evidence"]),
            "outcome": "verified_fill"}
    if r["verdict"] == "OK":
        detail = ("resolve_form reads %s as %s of %s, not %s"
                  % (word, r["form"], r["entry"], wanted))
    else:
        detail = ("resolve_form HOLD %s: %s"
                  % (r["hold_reason"], r["evidence"]))
    return None, {"source": "morphology", "evidence": detail,
                  "outcome": "no_attestation"}


def _attempt_store(word, identifier):
    """Channel (c): the entry store. A stored inverse
    relationship attests the same link from the other side.
    Returns (fill_value | None, attempt)."""
    inverse = _INVERSE_OF.get(identifier)
    if inverse is None:
        return None, {"source": "entry_store",
                      "evidence": "no inverse relationship name "
                                  "defined for this identifier",
                      "outcome": "no_stored_link"}
    for other in sorted(_STORE):
        subs = _STORE[other]["subcoordinates"]
        if subs.get(inverse) == word:
            return other, {
                "source": "entry_store",
                "evidence": "stored inverse relationship: '%s' "
                            "holds %s: %s" % (other, inverse, word),
                "outcome": "verified_fill"}
    return None, {"source": "entry_store",
                  "evidence": "no stored %s relationship names %s"
                              % (inverse, word),
                  "outcome": "no_stored_link"}


def _covers(d, o):
    """Stored definition d is covered by another entry's
    definition o: equal, or contained either way with the
    contained text substantial (>= 30 chars). Mirrors
    pmca_morphology_finder._covers."""
    if d == o:
        return True
    if len(d) >= 30 and d in o:
        return True
    if len(o) >= 30 and o in d:
        return True
    return False


def _candidate_defs(word):
    """Exact stored definitions for a headword read straight
    from the DB (the morphology covering search reads the same
    file read-only), or None."""
    path = pmca_dictionary.db_path()
    if path is None:
        return None
    try:
        con = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute("SELECT defs FROM defs WHERE word=?",
                          (word,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if row is None:
        return None
    try:
        defs = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    if not isinstance(defs, list):
        return None
    return [d for d in defs if isinstance(d, str)]


def _overlap_entries(word, defs):
    """Single-word headword entries whose stored definitions
    overlap the word's: {entry: {"share", "identical", "defs"}}
    where share counts the word's definitions covered by the
    entry's. Bounded fragment search over the same DB file,
    read-only — the morphology module's covering-search
    machinery, mirrored and disclosed in the module docstring."""
    path = pmca_dictionary.db_path()
    if path is None:
        return {}
    try:
        con = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    found = set()
    try:
        for d in defs:
            frag = d[:40]
            cut = len(frag)
            for bad in ('"', "\\"):
                idx = frag.find(bad)
                if idx != -1:
                    cut = min(cut, idx)
            frag = frag[:cut]
            if len(frag) < 15:
                continue
            rows = con.execute(
                "SELECT word FROM defs WHERE instr(defs, ?) > 0"
                " ORDER BY word", (frag,)).fetchall()
            for (w,) in rows:
                if w != word and " " not in w and "'" not in w:
                    found.add(w)
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    out = {}
    for w in sorted(found):
        edefs = _candidate_defs(w)
        if edefs is None:
            continue
        share = sum(1 for d in defs
                    if any(_covers(d, o) for o in edefs))
        if share:
            out[w] = {"share": share, "identical": edefs == defs,
                      "defs": edefs}
    return out


def _attempt_overlap(word, identifier, defs):
    """The weak channel: definition identity / containment.
    NEVER fills — returns (candidate | None, evidence | None,
    attempt). Selection rule as disclosed in the docstring."""
    if defs is None:
        return None, None, {
            "source": "definition_overlap",
            "evidence": "word has no dictionary entry; no "
                        "definitions to overlap",
            "outcome": "no_overlap"}
    overlap = _overlap_entries(word, defs)
    if not overlap:
        return None, None, {
            "source": "definition_overlap",
            "evidence": "no single-word entry's stored "
                        "definitions overlap this word's",
            "outcome": "no_overlap"}
    identical = sorted(w for w, o in overlap.items()
                       if o["identical"])
    if len(identical) == 1:
        cand = identical[0]
        ev = ("definition-identity (UNVERIFIED): the stored "
              "definitions of '%s' are identical to the '%s' "
              "entry (%d definitions; this build's extraction "
              "copies base definitions into form and phrase "
              "entries, and single-word matches only are "
              "considered). Identity names a linked entry; it "
              "does not attest %s and never promotes."
              % (word, cand, len(defs), identifier))
        return cand, ev, {"source": "definition_overlap",
                          "evidence": ev,
                          "outcome": "candidate_unverified"}
    # Containment: the overlapping entries must jointly cover
    # every stored definition, else the overlap is noise.
    joint = all(
        any(_covers(d, o) for w in overlap
            for o in overlap[w]["defs"])
        for d in defs)
    if not joint:
        return None, None, {
            "source": "definition_overlap",
            "evidence": "overlapping entries do not jointly "
                        "cover all %d stored definitions; "
                        "partial overlap mints no candidate"
                        % len(defs),
            "outcome": "no_overlap"}
    max_share = max(o["share"] for o in overlap.values())
    tops = sorted(w for w, o in overlap.items()
                  if o["share"] == max_share)
    bases = []
    for w in tops:
        r = _morph.resolve_form(w)
        if r["verdict"] == "OK" and r["entry"] == w \
                and r["form"] in ("base", "base_or_plural"):
            bases.append(w)
    if len(bases) == 1:
        cand = bases[0]
        ev = ("definition-containment (UNVERIFIED): '%s' covers "
              "%d of %d stored definitions of '%s' (largest "
              "share; tied at that share: %s); of the "
              "largest-share entries only '%s' resolves in "
              "morphology as its own base. Definition overlap "
              "is not attestation of %s and never promotes."
              % (cand, max_share, len(defs), word,
                 ", ".join(tops), cand, identifier))
        return cand, ev, {"source": "definition_overlap",
                          "evidence": ev,
                          "outcome": "candidate_unverified"}
    return None, None, {
        "source": "definition_overlap",
        "evidence": "largest-share entries (%s) at %d of %d "
                    "definitions do not reduce to one base entry"
                    % (", ".join(tops), max_share, len(defs)),
        "outcome": "no_overlap"}


# ---------------------------------------------------------------------
# The IF-THEN rule itself.
# ---------------------------------------------------------------------

def _result(word, identifier, verdict, value, record_seq):
    return {"word": word, "identifier": identifier,
            "verdict": verdict, "value": value,
            "record_seq": record_seq}


def encounter(word, identifier):
    """IF the entry for WORD holds a value under IDENTIFIER,
    return it by lookup. ELSE record the gap, attempt the build
    (dictionary note, morphology, entry store — first verified
    attestation fills), record any weak candidate unverified,
    and HOLD. See the module docstring."""
    w = word.strip().lower()
    entry = _store_entry(w)
    if entry is not None and identifier in entry["subcoordinates"]:
        return _result(w, identifier, "lookup",
                       entry["subcoordinates"][identifier], None)

    # The gap is recorded BEFORE any attempt runs: no silent gaps.
    record = {"seq": len(_RECORDS) + 1, "word": w,
              "identifier": identifier,
              "gap_key": "slot::%s::%s" % (w, identifier),
              "status": "open_no_candidate",
              "attempts": [], "candidate": None,
              "candidate_evidence": None, "filled_value": None}
    _RECORDS.append(record)

    dict_entry = _exact_entry(w)
    defs = dict_entry["definitions"] if dict_entry else None

    fill = None
    for attempt_fn in (_attempt_note, _attempt_morphology,
                       _attempt_store):
        if attempt_fn is _attempt_note:
            value, attempt = attempt_fn(w, identifier, defs)
        else:
            value, attempt = attempt_fn(w, identifier)
        record["attempts"].append(attempt)
        if value is not None:
            fill = value
            break

    if fill is not None:
        _STORE.setdefault(w, {"subcoordinates": {}})[
            "subcoordinates"][identifier] = fill
        record["status"] = "filled"
        record["filled_value"] = fill
        return _result(w, identifier, "filled", fill,
                       record["seq"])

    cand, cand_ev, attempt = _attempt_overlap(w, identifier, defs)
    record["attempts"].append(attempt)
    if cand is not None:
        record["status"] = "open_with_candidate"
        record["candidate"] = cand
        record["candidate_evidence"] = cand_ev
    return _result(w, identifier, "held", None, record["seq"])


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_slot_verifier.py
# Every battery case from the build brief is asserted against the
# real dictionary this tree carries and the morphology module's
# measured facts. Where a case's outcome is a measured property
# of the stored text, the check asserts the MEASURED outcome and
# its detail says so in the open.
# ---------------------------------------------------------------------

_ENCOUNTERS = [("wolf", "is_a"), ("wolves", "plural_of"),
               ("wolves", "plural_of"), ("went", "past_of"),
               ("children", "plural_of"), ("child", "plural_of")]


def _scenario():
    """Run the battery encounters from the fixture and return
    the full transcript: results, records, worklist, store."""
    reset_store()
    results = [encounter(w, i) for (w, i) in _ENCOUNTERS]
    return {"results": results, "records": records(),
            "worklist": worklist(), "store": store()}


def run_checks():
    import os

    results = []

    def check(name, cond, detail=""):
        results.append(cond)
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    # The dictionary tier is off until configured (Pack G,
    # 2026-10-26); this battery runs against the tree's stored
    # Webster 1913 DB, configured explicitly.
    pmca_dictionary.configure(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "webster1913.db"))

    # Import discipline: stdlib + pmca_dictionary +
    # pmca_morphology_finder only (no analyzer, no other pmca
    # module is consulted anywhere in this file).
    src = open(os.path.abspath(__file__)).read()
    pmca_imports = sorted(set(re.findall(
        r"^(?:import|from)\s+(pmca_[a-z_0-9]+)", src, re.M)))
    check("imports_limited_to_dictionary_and_morphology",
          pmca_imports == ["pmca_dictionary",
                           "pmca_morphology_finder"],
          str(pmca_imports))

    sc = _scenario()
    res = sc["results"]
    recs = sc["records"]
    st = sc["store"]

    # 1. Stored relationship resolves by lookup; NO record is
    # written (zero-cost retraversal).
    check("case1_wolf_is_a_lookup_no_record",
          res[0] == {"word": "wolf", "identifier": "is_a",
                     "verdict": "lookup", "value": "canine",
                     "record_seq": None}
          and len(recs) == 4,
          str(res[0]) + " | total records after all six "
          "encounters: %d (cases 1 and 3 wrote none)" % len(recs))

    # 2. wolves plural_of FILLS by morphology attestation:
    # resolve_form attests wolves as plural of wolf (rule whose
    # base resolves as a headword). The dictionary-note attempt
    # runs first and finds no note — the extraction lost it.
    rec1 = recs[0]
    check("case2_wolves_plural_filled_by_morphology",
          res[1]["verdict"] == "filled"
          and res[1]["value"] == "wolf"
          and res[1]["record_seq"] == 1
          and rec1["status"] == "filled"
          and rec1["filled_value"] == "wolf"
          and [a["source"] for a in rec1["attempts"]]
          == ["dictionary_note", "morphology"]
          and rec1["attempts"][0]["outcome"] == "no_note"
          and rec1["attempts"][1]["outcome"] == "verified_fill"
          and st["wolves"]["subcoordinates"].get("plural_of")
          == "wolf",
          str(res[1]) + " | attempts: "
          + str([(a["source"], a["outcome"])
                 for a in rec1["attempts"]]))

    # 3. The same encounter again: lookup now, compounding —
    # the recorded answer is read; record count unchanged.
    check("case3_wolves_second_encounter_lookup",
          res[2]["verdict"] == "lookup"
          and res[2]["value"] == "wolf"
          and res[2]["record_seq"] is None,
          str(res[2]))

    # 4. went past_of: HELD. The text note is gone from this
    # extraction and morphology attests no base, so the slot is
    # NEVER promoted — but the overlap channel records its
    # candidate: go, by definition-containment, UNVERIFIED
    # (went's stored definitions are wend's two and go's three,
    # verbatim copies). The store must be unchanged.
    rec2 = recs[1]
    check("case4_went_held_candidate_go_unverified",
          res[3]["verdict"] == "held" and res[3]["value"] is None
          and rec2["status"] == "open_with_candidate"
          and rec2["candidate"] == "go"
          and "definition-containment" in rec2["candidate_evidence"]
          and "UNVERIFIED" in rec2["candidate_evidence"]
          and rec2["filled_value"] is None
          and st["went"]["subcoordinates"] == {},
          str(res[3]) + " | candidate: " + str(rec2["candidate"])
          + " | " + str(rec2["candidate_evidence"])[:150])

    # 5. children plural_of: HELD, candidate child by
    # definition-identity (the two entries' stored definitions
    # are identical in this build), UNVERIFIED, store unchanged.
    rec3 = recs[2]
    check("case5_children_held_candidate_child_unverified",
          res[4]["verdict"] == "held"
          and rec3["status"] == "open_with_candidate"
          and rec3["candidate"] == "child"
          and "definition-identity" in rec3["candidate_evidence"]
          and "UNVERIFIED" in rec3["candidate_evidence"]
          and st["children"]["subcoordinates"] == {},
          str(res[4]) + " | candidate: " + str(rec3["candidate"]))

    # 6. child plural_of — the FORWARD pointer on the base word.
    # Measured outcome on this data: HELD, open_with_candidate
    # naming children via the same definition-identity channel,
    # and the slot is NOT filled. Disclosed asymmetry: v1 fills
    # back-pointers on form words through verified sources only;
    # a forward pointer is recorded as an unverified candidate
    # at best and awaits a form_family pass.
    rec4 = recs[3]
    check("case6_child_forward_pointer_held_with_candidate",
          res[5]["verdict"] == "held"
          and rec4["status"] == "open_with_candidate"
          and rec4["candidate"] == "children"
          and "UNVERIFIED" in rec4["candidate_evidence"]
          and st["child"]["subcoordinates"] == {},
          str(res[5]) + " | measured: forward pointer recorded "
          "as unverified candidate, never filled in v1")

    # 7. The worklist after the battery: exactly the open gaps,
    # in seq order, each with its record.
    wl = sc["worklist"]
    check("case7_worklist_exact",
          [r["word"] for r in wl] == ["went", "children", "child"]
          and all(r["status"] == "open_with_candidate" for r in wl)
          and [r["seq"] for r in wl] == [2, 3, 4],
          str([(r["seq"], r["word"], r["status"]) for r in wl]))

    # 8. Persistence: records and store are separate artifacts.
    # Round-trip BOTH, then the filled wolves slot still
    # resolves by lookup and writes no new record.
    saved_records = records()
    dr = dump_records()
    ds = dump_store()
    reset_store()
    load_records(dr)
    load_store(ds)
    r = encounter("wolves", "plural_of")
    check("case8_json_round_trip_and_lookup_after_load",
          records() == saved_records
          and r["verdict"] == "lookup" and r["value"] == "wolf"
          and r["record_seq"] is None
          and len(records()) == 4,
          "records equal after round-trip: %s; post-load "
          "encounter: %s" % (records() == saved_records, str(r)))

    # 9. Determinism inside the process: the whole scenario
    # re-run produces an identical transcript (records carry
    # seq numbers only, no timestamps). Two-process byte
    # identity is checked by running this file twice.
    dump1 = json.dumps(_scenario(), sort_keys=True)
    dump2 = json.dumps(_scenario(), sort_keys=True)
    check("case9_determinism_two_scenarios_identical",
          dump1 == dump2, dump1[:60])

    passed = sum(1 for x in results if x)
    print("%d/%d checks passed" % (passed, len(results)))
    print("Determinism (two scenarios identical): %s"
          % (dump1 == dump2))
    return passed == len(results)


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_checks() else 1)
