"""PMCA Population - population on encounter, enrichment in place.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's
architecture.

Ruling (Jonathan, 2026-10-22): "The word definitely needs to
populate the graph. If it's an unknown word, the relationship
has to structure through the system, learning, both the word
or finding more on the word... The word is, put in a graph
into a coordinate. And when it comes across a definition, it
adds that information to that coordinate through its sub
coordinates."

Shape of the ruling:

- EVERY encountered word populates the graph. An unknown
  word is placed immediately as a BARE entry at its
  coordinate: placement only. No subcoordinate values, no
  relationships, no harm. Placement precedes understanding
  (the dictionary loop's principle, at encounter scale) and
  extends the 2026-10-15 HOLD-populates-words ruling: bare
  entries, relationships never built on the placement itself.
- Relationships structure LATER, through the system's
  learning: when a definition is found, that information is
  added TO THE SAME ENTRY through its subcoordinates. The
  coordinate never moves; enrichment accretes in place.
- This slice stores, on enrichment, the definition record
  and the attested variables (noun / verb, from the
  dictionary tier's own tests) ONLY. It writes NO is_a and
  no other relationship: relationships structure through
  the other mechanisms (write-in attestations, derivation,
  teaching), not through placement or definition arrival.
- Population is not knowledge. A placed_bare word with no
  definition still HOLDs in the composer with its gaps
  named; the entry's existence records only that the system
  met the word, never what the word means.

This module operates on a pmca_write_in store (a plain
dict) and uses the write-in's own event sequencing, so
population, enrichment, encounter, fill, and gap events all
share the store's single sequence numbering, and its
SELF_KEY entry accumulates the population and enrichment
records alongside the encounter and gap records. Because
pmca_write_in exposes its sequencing and entry helpers as
module-level functions (underscore-named but stable), this
module calls them rather than re-implementing the store's
internals: one store, one numbering, one entry shape.

Deterministic. Stdlib only at module level; sibling modules
are imported inside the functions and the check battery,
never at import time.
"""

import copy
import re

# The finders' cleaning, mirrored: sentence text is lowered,
# tokens are split on whitespace, and each token is stripped
# of surrounding punctuation (the composer's _surface_form
# strips the same set; the finders additionally drop a
# trailing sentence-final ".!?" before splitting).
_PUNCT = ".,;:!?()[]{}\"'"
_LABEL_RE = re.compile(r"^\s*(\([^)]*\)\s*)+")
_SENSE_MARK_RE = re.compile(r"^\s*\([a-z]\)\s*")


def _tokens(sentence):
    """The sentence's tokens, in first-occurrence order:
    lowercase, whitespace split, surrounding punctuation
    stripped, empties and repeats dropped."""
    seen = set()
    out = []
    for raw in sentence.strip().lower().split():
        tok = raw.strip(_PUNCT)
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    # P0: genitive normalization through the shared screen
    # (pmca_parse_guards), so a possessive token places its
    # stem and its head as words, never the clitic form itself.
    # An unverifiable genitive leaves the raw stream untouched:
    # placement is by encounter, and the finders' HOLD governs
    # binding.
    import pmca_parse_guards as _guards
    scr = _guards.screen(out, _guards.dict_noun_capable,
                         _guards.dict_verb_capable,
                         guards=("genitive",))
    if scr["hold"] is None:
        seen = set()
        norm = []
        for tok in scr["tokens"]:
            if tok and tok not in seen:
                seen.add(tok)
                norm.append(tok)
        return norm
    return out


def _is_verb_sense(defn):
    """A stored definition is a verb sense under the ONE
    shared cleaned decoding (pmca_verb_capable.is_verb_sense,
    Candidacy Rule Set C-4; WG2 2026-11-01 — this module's
    mirrored CLEANED copy is RETIRED as a mechanism). The
    import is function-level, honouring this module's
    stdlib-only-at-module-level discipline."""
    import pmca_verb_capable as _vc
    return _vc.is_verb_sense(defn)


def _self(store):
    import pmca_write_in
    return store["entries"][pmca_write_in.SELF_KEY]


# ---------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------

def populate_word(store, word, source):
    """Place word in the store as a BARE entry, if it has
    none. The entry is created in pmca_write_in's entry
    shape and carries a "population" record: {status:
    "placed_bare", placed_event: <event seq>, source:
    source}. One event seq is consumed and a population
    record is appended to the self entry's encounters.

    A word that already has an entry is changed in no way:
    no event is consumed and the report names the original
    placed_event (None when the entry was created by a
    write path rather than by population). The reserved
    self key is never a word entry: it reports as already
    present, since the self entry itself is what is there."""
    import pmca_write_in as wi
    word = word.strip().lower()
    if word == wi.SELF_KEY:
        return {"outcome": "already_present", "word": word,
                "placed_event": None}
    existing = store["entries"].get(word)
    if existing is not None:
        pop = existing.get("population") or {}
        return {"outcome": "already_present", "word": word,
                "placed_event": pop.get("placed_event")}
    entry = wi._word_entry(store, word)
    seq = wi._next_seq(store)
    entry["population"] = {"status": "placed_bare",
                            "placed_event": seq,
                            "source": source}
    _self(store)["encounters"].append(
        {"seq": seq, "kind": "population", "word": word,
         "outcome": "placed_bare", "source": source})
    return {"outcome": "placed_bare", "word": word,
            "placed_event": seq, "source": source}


def encounter(store, sentence, noun_result, verb_result):
    """One sentence encounter with population first. Every
    token of the sentence that lacks an entry is placed
    bare (source "encounter"); then the encounter is
    delegated unchanged to pmca_write_in.from_finder_results.
    Population is placement only: on a HOLD the delegated
    write-in still writes no bindings, but the bare entries
    placed by this call stand."""
    import pmca_write_in as wi
    populated = []
    already_present = []
    for tok in _tokens(sentence):
        report = populate_word(store, tok, "encounter")
        if report["outcome"] == "placed_bare":
            populated.append(tok)
        else:
            already_present.append(tok)
    record = wi.from_finder_results(store, sentence,
                                     noun_result, verb_result)
    return {"populated": populated,
            "already_present": already_present,
            "write_in": record}


# ---------------------------------------------------------------------
# Enrichment: definition and variables, in place
# ---------------------------------------------------------------------

def enrich_from_dictionary(store, word):
    """Add a found definition to word's entry, in place.
    The entry is populated first (source "enrichment") when
    it has none. Uses pmca_dictionary.lookup (configured by
    the caller).

    Lookup fails: a gap record is appended to the self
    entry through pmca_write_in.record_gap (word,
    "definition", "not_in_dictionary"), the entry itself
    is unchanged, and the report is held.

    Lookup succeeds: on the SAME entry, (a) "definition" =
    {text: the first definition string, definitions_count:
    n, evidence: "dictionary", event: <seq>}; (b)
    "variables" = the sorted list of attested variables
    from the tier's own tests over the stored definitions:
    "noun" when any definition does not begin "To " after
    the label stripping, "verb" when any does; (c) the
    population record's status becomes "enriched" with
    enriched_event = <seq> (a population record is created
    for the entry if a write path made the entry first).
    One event seq is consumed by the enrichment and an
    enrichment record is appended to the self entry. NO
    relationship of any kind is written here."""
    import pmca_dictionary
    import pmca_write_in as wi
    word = word.strip().lower()
    if store["entries"].get(word) is None:
        populate_word(store, word, "enrichment")
    looked = None
    try:
        looked = pmca_dictionary.lookup(word)
    except Exception:
        looked = None
    if not looked or not looked.get("definitions"):
        wi.record_gap(store, word, "definition",
                      "not_in_dictionary")
        return {"outcome": "held", "word": word,
                "reason": "not_in_dictionary"}
    definitions = looked["definitions"]
    entry = store["entries"][word]
    seq = wi._next_seq(store)
    entry["definition"] = {
        "text": definitions[0],
        "definitions_count": len(definitions),
        "evidence": "dictionary",
        "event": seq,
    }
    variables = set()
    for defn in definitions:
        if _is_verb_sense(defn):
            variables.add("verb")
        else:
            variables.add("noun")
    entry["variables"] = sorted(variables)
    pop = entry.get("population")
    if pop is None:
        pop = {"status": "placed_bare", "placed_event": None,
               "source": "enrichment"}
        entry["population"] = pop
    pop["status"] = "enriched"
    pop["enriched_event"] = seq
    _self(store)["encounters"].append(
        {"seq": seq, "kind": "enrichment", "word": word,
         "outcome": "enriched"})
    return {"outcome": "enriched", "word": word, "event": seq,
            "variables": list(entry["variables"]),
            "definitions_count": len(definitions)}


# ---------------------------------------------------------------------
# Read-only summary
# ---------------------------------------------------------------------

def entry_state(store, word):
    """A read-only summary of one word's population state:
    presence, population status (None when the entry exists
    without a population record, e.g. created by a write
    path), subcoordinate count, definition presence, and
    the attested variables list. The reserved self key is
    not a word entry and always reports absent."""
    import pmca_write_in as wi
    word = word.strip().lower()
    entry = None
    if word != wi.SELF_KEY:
        entry = store["entries"].get(word)
    if entry is None:
        return {"present": False, "population_status": None,
                "subcoordinates_count": 0,
                "definition_present": False, "variables": []}
    pop = entry.get("population") or {}
    return {"present": True,
            "population_status": pop.get("status"),
            "subcoordinates_count":
                len(entry.get("subcoordinates") or {}),
            "definition_present": "definition" in entry,
            "variables": list(entry.get("variables") or [])}


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_population.py
# Uses the REAL duplicate-tree finders, the real dictionary,
# the real write-in, and the real composer.
# ---------------------------------------------------------------------

def run_checks():
    import os

    import pmca_sentence_analyzer as _analyzer
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    _analyzer.configure_dictionary(db)

    import pmca_composer as composer
    import pmca_noun_finder as noun_finder
    import pmca_verb_finder as verb_finder
    import pmca_write_in as wi

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    # -- 1. Banana headline. Determined from the actual
    # dictionary this tree carries: banana RESOLVES (exact
    # headword, 1 definition, "(Bot.) A perennial herbaceous
    # plant..."), so the enrichment path is the truth here.
    store_b = wi.new_store()
    rep = populate_word(store_b, "banana", "suite")
    banana = wi.entry_view(store_b, "banana")
    check("banana_placed_bare",
          rep["outcome"] == "placed_bare"
          and rep["placed_event"] == 1
          and banana is not None
          and banana["subcoordinates"] == {}
          and banana["conflicts"] == []
          and banana["population"]["status"] == "placed_bare")
    enr = enrich_from_dictionary(store_b, "banana")
    banana2 = wi.entry_view(store_b, "banana")
    print("BANANA_TRUTH: resolves in this dictionary build "
          "(1 definition); enrichment path asserted")
    check("banana_enriched_same_entry",
          enr["outcome"] == "enriched"
          and banana2["definition"]["text"].startswith("(Bot.)")
          and banana2["definition"]["definitions_count"] == 1
          and banana2["definition"]["evidence"] == "dictionary"
          and banana2["variables"] == ["noun"]
          and banana2["population"]["status"] == "enriched"
          and banana2["population"]["placed_event"]
          < banana2["population"]["enriched_event"]
          and "is_a" not in banana2["subcoordinates"])
    state_b = entry_state(store_b, "banana")
    check("banana_entry_state",
          state_b == {"present": True,
                      "population_status": "enriched",
                      "subcoordinates_count": 0,
                      "definition_present": True,
                      "variables": ["noun"]})

    # -- 2. Idempotence: a second population changes
    # nothing and consumes no event.
    seq_before = store_b["seq"]
    rep2 = populate_word(store_b, "banana", "suite")
    check("populate_idempotent_no_event",
          rep2["outcome"] == "already_present"
          and rep2["placed_event"] == 1
          and store_b["seq"] == seq_before)

    # -- 3. Held encounter: placement stands, no bindings.
    store_h = wi.new_store()
    s_hold = "The cup was broken by the man."
    n_hold = noun_finder.find(s_hold)
    v_hold = verb_finder.find(s_hold)
    check("held_sentence_finder_sanity",
          n_hold["verdict"] == "HOLD"
          and n_hold["hold_reason"]
          == "passive_or_auxiliary_out_of_scope")
    out_h = encounter(store_h, s_hold, n_hold, v_hold)
    held_tokens = ["the", "cup", "was", "broken", "by", "man"]
    word_entries = [w for w in store_h["entries"]
                    if w != wi.SELF_KEY]
    self_h = wi.entry_view(store_h, wi.SELF_KEY)
    pop_records = [r for r in self_h["encounters"]
                   if r["kind"] == "population"]
    check("held_encounter_populated_all_tokens",
          out_h["populated"] == held_tokens
          and sorted(word_entries) == sorted(held_tokens)
          and len(pop_records) == len(held_tokens))
    check("held_encounter_no_bindings",
          all(store_h["entries"][w]["subcoordinates"] == {}
              for w in word_entries)
          and out_h["write_in"]["outcome"] == "held"
          and self_h["encounters"][-1] == out_h["write_in"]
          and self_h["encounters"][-1]["hold_reason"]
          == "passive_or_auxiliary_out_of_scope")

    # -- 4. Verified encounter interplay.
    store_v = wi.new_store()
    s_wolf = "The wolf eats the sheep."
    out_v1 = encounter(store_v, s_wolf, noun_finder.find(s_wolf),
                       verb_finder.find(s_wolf))
    binding1 = wi.binding_for(store_v, "wolf", "eat")
    out_v2 = encounter(store_v, s_wolf, noun_finder.find(s_wolf),
                       verb_finder.find(s_wolf))
    binding2 = wi.binding_for(store_v, "wolf", "eat")
    check("verified_encounter_populates_then_binds",
          "wolf" in out_v1["populated"]
          and "sheep" in out_v1["populated"]
          and binding1 is not None
          and binding1["status"] == "candidate")
    # D13 (2026-10-26; claims-05 F3b): the second encounter
    # is a replay of the same sentence under the same
    # provenance — it attests again but does NOT promote.
    # (This check asserted verified before the fix; that
    # was the contradicted behaviour.)
    check("second_encounter_no_population_stays_candidate",
          out_v2["populated"] == []
          and binding2 is not None
          and binding2["status"] == "candidate"
          and binding2["attestations"] == 2)

    # -- 5. Dictionary-unknown word: held enrichment, gap
    # recorded, entry stays bare.
    store_f = wi.new_store()
    populate_word(store_f, "flibberty", "suite")
    enr_f = enrich_from_dictionary(store_f, "flibberty")
    flib = wi.entry_view(store_f, "flibberty")
    gaps_f = wi.entry_view(store_f, wi.SELF_KEY)["gaps"]
    state_f = entry_state(store_f, "flibberty")
    check("flibberty_enrich_held_gap_recorded",
          enr_f["outcome"] == "held"
          and enr_f["reason"] == "not_in_dictionary"
          and len(gaps_f) == 1
          and gaps_f["slot::flibberty::definition"]["kind"]
          == "slot"
          and gaps_f["slot::flibberty::definition"]["reason"]
          == "not_in_dictionary"
          and gaps_f["slot::flibberty::definition"]["status"]
          == "OPEN"
          and flib["population"]["status"] == "placed_bare"
          and "definition" not in flib
          and state_f["definition_present"] is False
          and state_f["variables"] == [])

    # -- 6. Composer honesty after population: placement is
    # not knowledge.
    rec_f = composer.compose_what_is(store_f, "flibberty")
    check("composer_flibberty_still_holds_both_gaps",
          rec_f["verdict"] == "HOLD"
          and "kind: not stored" in rec_f["gaps"]
          and "definition: not stored" in rec_f["gaps"])
    rec_b = composer.compose_what_is(store_b, "banana")
    check("composer_banana_holds_kind_definition_present",
          rec_b["verdict"] == "HOLD"
          and "kind: not stored" in rec_b["gaps"]
          and "definition: not stored" not in rec_b["gaps"]
          and rec_b["definition_on_record"] is not None)

    # -- 7. Determinism store: a fixed exercise across all
    # three verbs of this module, printed canonically so
    # two processes can be compared byte for byte.
    store_d = wi.new_store()
    populate_word(store_d, "banana", "determinism")
    enrich_from_dictionary(store_d, "banana")
    encounter(store_d, s_wolf, noun_finder.find(s_wolf),
              verb_finder.find(s_wolf))
    populate_word(store_d, "flibberty", "determinism")
    enrich_from_dictionary(store_d, "flibberty")
    print("FINAL_STORE_SHA256 " + wi.store_hash(store_d))
    print("FINAL_STORE_JSON " + wi.canonical(store_d))

    passed = sum(1 for r in results if r)
    print("Passed %d of %d checks" % (passed, len(results)))
    print("ALL CHECKS PASS" if passed == len(results)
          else "SOME CHECKS FAILED")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(run_checks())
