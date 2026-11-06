"""PMCA Composer - communication as outward composition.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's
architecture.

Purpose (Jonathan, 2026-10-22): the system must COMMUNICATE,
and communication here is composition in the outward direction.
An answer is assembled ONLY from accumulated, stored structure:
the write-in store's subcoordinates and the dictionary tier's
definition on record. Nothing is invented between the stored
parts beyond fixed sentence templates.

Every composed record is checked against the five Ws and how:

- who: the originator (for a binding, the subject; for a
  what-is record, the entry itself);
- what: the verb and target of a binding, or the is_a value
  of an entry;
- where: the store entry and its distribution address
  (an address locates; it never implies relatedness);
- when: the attested event numbers, position in the store's
  event order only, never wall-clock time;
- why: the provenance of the stored value (source + evidence);
- how: the readback trace, the lookup steps actually taken.

Verified structure may be asserted plainly (verdict
ANSWER). Candidate structure is labeled in the sentence
itself AND carries the distinct verdict CANDIDATE, never
the bare ANSWER verdict (2026-10-26, D14; claims-05 F2:
a consumer keying on the verdict must not receive an
unverified claim as an answer). The verdict vocabulary
is fail-closed (PROM-1/C-2, 2026-10-29): _verdict_for
asserts ANSWER only for status exactly "verified"; any
other status composes as CANDIDATE, with the status
passed through verbatim and the candidate sentence
prefix applied. Any slot the store cannot
fill is named in the record's gaps list. A
definition from the dictionary tier is reported as a sourced
record line (definition_on_record); it is never woven into a
composed sentence, because stored definition text is meaning
on record, not a relationship the system has attested.

Recorded conflicts surface (2026-10-29): a conflicting value
never overwrites a stored one — the write-in path records it
on the entry (stored value, attempted value, provenance).
When a composition reads an entry/identifier carrying such
a conflict, the composed record names it in a "conflicts"
field: one item per recorded conflict, copying the stored
record's fields. The field appears only when non-empty
(precedent: the HOLD binding record omits "sentence_form"),
so conflict-free records are unchanged. Surfacing changes
no verdict: an ANSWER over a conflicted fact and the
conflict it stands on are both on the record.

Composition never writes. Every compose function reads the
store through pmca_write_in's store-only read paths and
leaves the store byte-identical.

Deterministic. Stdlib only at module level; sibling modules
are imported inside the compose functions and the check
battery, never at import time.
"""

import json

_CANDIDATE_PREFIX = "Candidate (not yet verified): "
_ARTICLES = ("the", "a", "an")


def _canonical(obj):
    """Canonical JSON for a composed record: sorted keys,
    tight separators. Byte-identical for identical inputs."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _where(word):
    """The where slot for an entry: its store key and its
    distribution address, when pmca_graphspace is importable.
    The address is returned as a list so the record is plain
    JSON data."""
    address = None
    try:
        import pmca_graphspace
        address = list(pmca_graphspace.dist_address(word))
    except Exception:
        address = None
    return {"store_entry": word, "address": address}


def _subcoordinate(store, word, identifier):
    """One stored subcoordinate record (a copy), or None.
    Reads through pmca_write_in.entry_view only."""
    import pmca_write_in
    entry = pmca_write_in.entry_view(store, word)
    if not isinstance(entry, dict):
        return None
    subs = entry.get("subcoordinates")
    if not isinstance(subs, dict):
        return None
    sub = subs.get(identifier)
    return sub if isinstance(sub, dict) else None


def _recorded_conflicts(store, reads):
    """The recorded conflicts on the entries/identifiers a
    composition reads. `reads` is a list of (word, identifier)
    pairs; an identifier ending in "::*" matches by prefix.
    Each returned item copies the stored conflict record:
    {entry, identifier, stored_value, attempted_value,
    provenance, seq}. Reads through pmca_write_in.entry_view
    only; order is the reads order, then stored order."""
    import pmca_write_in
    found = []
    for word, identifier in reads:
        entry = pmca_write_in.entry_view(store, word)
        if not isinstance(entry, dict):
            continue
        conflicts = entry.get("conflicts")
        if not isinstance(conflicts, list):
            continue
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            cid = conflict.get("identifier")
            if identifier.endswith("::*"):
                if not (isinstance(cid, str)
                        and cid.startswith(identifier[:-1])):
                    continue
            elif cid != identifier:
                continue
            found.append({
                "entry": word,
                "identifier": cid,
                "stored_value": conflict.get("stored_value"),
                "attempted_value": conflict.get("attempted_value"),
                "provenance": conflict.get("provenance"),
                "seq": conflict.get("seq"),
            })
    return found


def _verdict_for(status):
    """The verdict for a composed record over a stored fact
    of the given status (PROM-1/C-2, 2026-10-29): the
    assertion gate is fail-closed — ANSWER only when the
    status is exactly "verified"; any other status
    ("candidate", "bogus", "", None, "advanced", anything)
    composes as CANDIDATE."""
    return "ANSWER" if status == "verified" else "CANDIDATE"


def _surface_form(store, subject, verb, target):
    """The verb's surface form for the composed sentence,
    taken from stored data only: the original encounter
    sentence held in the binding's provenance. The verb token
    is the first non-article token after the subject and
    before the target in that sentence. Returns (form,
    fell_back). On any failure of extraction, the base form
    is used and fell_back is True."""
    sub = _subcoordinate(store, subject, "originates::" + verb)
    provenance = (sub or {}).get("provenance") or {}
    sentence = provenance.get("sentence")
    if not isinstance(sentence, str) or not sentence:
        return verb, True
    tokens = []
    for raw in sentence.strip().lower().split():
        tok = raw.strip(".,;:!?()[]{}\"'")
        if tok:
            tokens.append(tok)
    try:
        start = tokens.index(subject)
    except ValueError:
        return verb, True
    if target:
        end = None
        for i in range(start + 1, len(tokens)):
            if tokens[i] == target:
                end = i
                break
        if end is None:
            return verb, True
        segment = tokens[start + 1:end]
    else:
        segment = tokens[start + 1:start + 4]
    segment = [t for t in segment if t not in _ARTICLES]
    if not segment:
        return verb, True
    return segment[0], False


def compose_binding(store, subject, verb):
    """Compose the stored binding of subject as originator of
    verb (base form, as the write-in store keys it). Reads the
    binding through pmca_write_in.binding_for and its
    provenance through entry_view; both are store-only reads.

    Found: a record whose sentence is built from the stored
    subject, the surface verb form extracted from the
    provenance sentence, and the stored target — verdict
    ANSWER when the binding is verified, CANDIDATE when it
    is still a candidate (the sentence is labeled either
    way). Unknown binding: a HOLD record with who/what
    named as not stored in gaps."""
    import pmca_write_in
    sources = [
        {"store/module": "pmca_write_in", "entry": subject,
         "identifier": "originates::" + verb},
        {"store/module": "pmca_write_in", "entry": verb,
         "identifier": "acts_on::" + subject},
    ]
    binding = pmca_write_in.binding_for(store, subject, verb)
    conflicts = _recorded_conflicts(store, [
        (subject, "originates::" + verb),
        (verb, "acts_on::" + subject),
    ])
    if binding is None:
        record = {
            "kind": "binding",
            "verdict": "HOLD",
            "status": "not_stored",
            "sentence": None,
            "slots": {
                "who": None,
                "what": {"verb": verb, "target": None},
                "where": _where(subject),
                "when": [],
                "why": None,
                "how": [{"lookup": "store"}, {"entry": subject},
                        {"identifier": "originates::" + verb},
                        {"value": None}],
            },
            "gaps": ["who: not stored", "what: not stored"],
            "sources": sources,
        }
        if conflicts:
            record["conflicts"] = conflicts
        return record
    sub = _subcoordinate(store, subject,
                         "originates::" + verb) or {}
    provenance = sub.get("provenance") or {}
    events = list(sub.get("attested_events") or [])
    status = binding.get("status")
    target = binding.get("target")
    surface, fell_back = _surface_form(store, subject, verb, target)
    if fell_back:
        sources.append({"store/module": "pmca_composer",
                        "entry": subject,
                        "identifier": "sentence_form",
                        "sentence_form": "base_form_fallback"})
    if target:
        core = "The %s %s the %s." % (subject, surface, target)
    else:
        core = "The %s %s." % (subject, surface)
    sentence = ((_CANDIDATE_PREFIX if status != "verified" else "")
                + core)
    why = None
    if provenance.get("evidence") is not None \
            or provenance.get("source") is not None:
        why = {"evidence": provenance.get("evidence"),
               "source": provenance.get("source")}
    where = _where(subject)
    gaps = []
    if where["address"] is None:
        gaps.append("where: address unavailable")
    if not events:
        gaps.append("when: not stored")
    if why is None or why["evidence"] is None \
            or why["source"] is None:
        gaps.append("why: not stored")
    if target is None:
        gaps.append("what: target not stored")
    record = {
        "kind": "binding",
        "verdict": _verdict_for(status),
        "status": status,
        "sentence": sentence,
        "sentence_form": ("base_form_fallback" if fell_back
                          else "provenance_sentence"),
        "slots": {
            "who": subject,
            "what": {"verb": verb, "target": target},
            "where": where,
            "when": events,
            "why": why,
            "how": [{"lookup": "store"}, {"entry": subject},
                    {"identifier": "originates::" + verb},
                    {"value": verb},
                    {"entry": verb},
                    {"identifier": "acts_on::" + subject},
                    {"value": target}],
        },
        "gaps": gaps,
        "sources": sources,
    }
    if conflicts:
        record["conflicts"] = conflicts
    return record


def compose_what_is(store, word):
    """Compose what the store holds about a word's kind: its
    is_a subcoordinate, when one is stored. The dictionary
    tier's first definition for the word is reported
    separately as definition_on_record (a sourced record
    line, never part of the composed sentence), whether or
    not an is_a is stored. No is_a on record: HOLD, with the
    kind gap named."""
    import pmca_dictionary
    sources = [{"store/module": "pmca_write_in", "entry": word,
                "identifier": "is_a"}]
    sub = _subcoordinate(store, word, "is_a")
    conflicts = _recorded_conflicts(store, [(word, "is_a")])
    gaps = []
    definition = None
    looked = None
    try:
        looked = pmca_dictionary.lookup(word)
    except Exception:
        looked = None
    sources.append({"store/module": "pmca_dictionary",
                    "entry": word, "identifier": "definition"})
    if looked and looked.get("definitions"):
        definition = looked["definitions"][0]
    else:
        gaps.append("definition: not stored")
    where = _where(word)
    if where["address"] is None:
        gaps.append("where: address unavailable")
    if sub is None:
        record = {
            "kind": "what_is",
            "verdict": "HOLD",
            "status": "not_stored",
            "sentence": None,
            "slots": {
                "who": word,
                "what": {"is_a": None, "status": "not_stored"},
                "where": where,
                "when": [],
                "why": None,
                "how": [{"lookup": "store"}, {"entry": word},
                        {"identifier": "is_a"}, {"value": None}],
            },
            "gaps": ["kind: not stored"] + gaps,
            "sources": sources,
            "definition_on_record": definition,
        }
        if conflicts:
            record["conflicts"] = conflicts
        return record
    value = sub.get("value")
    status = sub.get("status")
    provenance = sub.get("provenance") or {}
    events = list(sub.get("attested_events") or [])
    why = None
    if provenance.get("evidence") is not None \
            or provenance.get("source") is not None:
        why = {"evidence": provenance.get("evidence"),
               "source": provenance.get("source")}
    if not events:
        gaps.append("when: not stored")
    if why is None or why["evidence"] is None \
            or why["source"] is None:
        gaps.append("why: not stored")
    core = "A %s is a %s." % (word, value)
    sentence = ((_CANDIDATE_PREFIX if status != "verified" else "")
                + core)
    record = {
        "kind": "what_is",
        "verdict": _verdict_for(status),
        "status": status,
        "sentence": sentence,
        "slots": {
            "who": word,
            "what": {"is_a": value, "status": status},
            "where": where,
            "when": events,
            "why": why,
            "how": [{"lookup": "store"}, {"entry": word},
                    {"identifier": "is_a"}, {"value": value}],
        },
        "gaps": gaps,
        "sources": sources,
        "definition_on_record": definition,
    }
    if conflicts:
        record["conflicts"] = conflicts
    return record


def _originators_of(store, verb, target):
    """Every subject holding a subject-scoped binding of
    verb onto target (Pack F+B, 2026-10-26): the verb
    entry's acts_on::<subject> facts whose value is target,
    subject names read off the identifier keys, in sorted
    (deterministic) order — enumeration over stored keys
    only, no inference. NO first-writer-wins: every true
    originator is visible. Legacy fallback: the
    pre-restructure single pair (acts_on / originated_by),
    read only when no scoped slot matches."""
    import pmca_write_in
    entry = pmca_write_in.entry_view(store, verb)
    subjects = []
    if isinstance(entry, dict):
        subs = entry.get("subcoordinates") or {}
        for ident in sorted(subs):
            if not ident.startswith("acts_on::"):
                continue
            fact = subs[ident]
            if isinstance(fact, dict) \
                    and fact.get("value") == target:
                subjects.append(ident[len("acts_on::"):])
        if not subjects:
            acts = subs.get("acts_on") or {}
            orig = subs.get("originated_by") or {}
            if acts.get("value") == target \
                    and orig.get("value"):
                subjects.append(orig["value"])
    return subjects


def compose_who(store, verb, target):
    """Compose who originates verb upon target, by
    enumerating the store's subject-scoped bindings
    (Pack F+B, 2026-10-26): every subject whose
    acts_on::<subject> on the verb entry names this target.
    Exactly one originator: the same record compose_binding
    returns for that subject. Several: one record naming
    them all (slots.who is the sorted list; the sentence
    joins them), status candidate unless every matched
    binding is verified, verdict by the D14 rule. None:
    HOLD, with who named as not stored."""
    sources = [
        {"store/module": "pmca_write_in", "entry": verb,
         "identifier": "acts_on::*"},
    ]
    subjects = _originators_of(store, verb, target)
    if not subjects:
        record = {
            "kind": "binding",
            "verdict": "HOLD",
            "status": "not_stored",
            "sentence": None,
            "slots": {
                "who": None,
                "what": {"verb": verb, "target": target},
                "where": _where(verb),
                "when": [],
                "why": None,
                "how": [{"lookup": "store"}, {"entry": verb},
                        {"identifier": "acts_on::*"},
                        {"value": target},
                        {"originators": []}],
            },
            "gaps": ["who: not stored"],
            "sources": sources,
        }
        who_conflicts = _recorded_conflicts(
            store, [(verb, "acts_on::*")])
        if who_conflicts:
            record["conflicts"] = who_conflicts
        return record
    if len(subjects) == 1:
        return compose_binding(store, subjects[0], verb)
    facts = {}
    for s in subjects:
        facts[s] = _subcoordinate(store, s,
                                  "originates::" + verb) or {}
        sources.append({"store/module": "pmca_write_in",
                        "entry": s,
                        "identifier": "originates::" + verb})
    if all(f.get("status") == "verified"
           for f in facts.values()):
        status = "verified"
    else:
        # Fail-closed (PROM-1/C-2): a non-verified,
        # non-candidate status among the matched keeps the
        # composition CANDIDATE and is named verbatim as
        # the recorded status; candidate/verified mixes
        # otherwise record "candidate" as before.
        other = [facts[s].get("status") for s in subjects
                 if facts[s].get("status")
                 not in ("verified", "candidate")]
        status = other[0] if other else "candidate"
    events = sorted({e for f in facts.values()
                     for e in (f.get("attested_events") or [])})
    provenance = facts[subjects[0]].get("provenance") or {}
    why = None
    if provenance.get("evidence") is not None \
            or provenance.get("source") is not None:
        why = {"evidence": provenance.get("evidence"),
               "source": provenance.get("source")}
    surface, fell_back = _surface_form(store, subjects[0],
                                       verb, target)
    if fell_back:
        sources.append({"store/module": "pmca_composer",
                        "entry": subjects[0],
                        "identifier": "sentence_form",
                        "sentence_form": "base_form_fallback"})
    parts = ["the " + s for s in subjects]
    joined = (", ".join(parts[:-1]) + " and " + parts[-1])
    joined = joined[0].upper() + joined[1:]
    core = "%s %s the %s." % (joined, surface, target)
    sentence = ((_CANDIDATE_PREFIX if status != "verified"
                 else "") + core)
    where = _where(verb)
    gaps = []
    if where["address"] is None:
        gaps.append("where: address unavailable")
    if not events:
        gaps.append("when: not stored")
    if why is None or why["evidence"] is None \
            or why["source"] is None:
        gaps.append("why: not stored")
    record = {
        "kind": "binding",
        "verdict": _verdict_for(status),
        "status": status,
        "sentence": sentence,
        "sentence_form": ("multi_originator_base_form_fallback"
                          if fell_back
                          else "multi_originator"),
        "slots": {
            "who": subjects,
            "what": {"verb": verb, "target": target},
            "where": where,
            "when": events,
            "why": why,
            "how": [{"lookup": "store"}, {"entry": verb},
                    {"identifier": "acts_on::*"},
                    {"value": target},
                    {"originators": subjects}],
        },
        "gaps": gaps,
        "sources": sources,
    }
    who_conflicts = _recorded_conflicts(
        store,
        [(s, "originates::" + verb) for s in subjects]
        + [(verb, "acts_on::" + s) for s in subjects])
    if who_conflicts:
        record["conflicts"] = who_conflicts
    return record


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_composer.py
# Builds stores with the REAL duplicate-tree finders and the
# real dictionary, composes from them, and prints the composed
# records canonically so two runs in separate processes can be
# compared byte for byte.
# ---------------------------------------------------------------------

def run_checks():
    import os

    import pmca_sentence_analyzer as _analyzer
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    _analyzer.configure_dictionary(db)

    import pmca_noun_finder as noun_finder
    import pmca_verb_finder as verb_finder
    import pmca_write_in as wi

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    records = {}
    s_wolf = "The wolf eats the sheep."

    # 1. Binding after two IDENTICAL encounters. Per D13
    # (2026-10-26; claims-05 F3b) a replayed encounter does
    # not verify: the binding stays candidate, and per D14
    # its composed record carries the CANDIDATE verdict with
    # the labeled sentence. (Pre-fix this check asserted a
    # verified plain ANSWER — the behaviour the audit
    # contradicted.)
    store_v = wi.new_store()
    wi.from_finder_results(store_v, s_wolf,
                           noun_finder.find(s_wolf),
                           verb_finder.find(s_wolf))
    wi.from_finder_results(store_v, s_wolf,
                           noun_finder.find(s_wolf),
                           verb_finder.find(s_wolf))
    hash_v = wi.store_hash(store_v)
    rec1 = compose_binding(store_v, "wolf", "eat")
    records["check1_binding"] = rec1
    print("CHECK1_RECORD " + _canonical(rec1))
    check("binding_replay_candidate_verdict_labeled",
          rec1["verdict"] == "CANDIDATE"
          and rec1["status"] == "candidate"
          and rec1["sentence"]
          == "Candidate (not yet verified): "
          "The wolf eats the sheep.",
          repr(rec1["sentence"]))
    check("binding_slots_who_what_when",
          rec1["slots"]["who"] == "wolf"
          and rec1["slots"]["what"] == {"verb": "eat",
                                         "target": "sheep"}
          and rec1["slots"]["when"] == [1, 2])
    check("binding_why_and_gaps",
          rec1["slots"]["why"] == {"evidence": "finder_parse",
                                    "source": "finders"}
          and rec1["gaps"] == [])
    check("binding_where_filled",
          rec1["slots"]["where"]["store_entry"] == "wolf"
          and rec1["slots"]["where"]["address"] is not None)

    # 3. compose_who on the replayed store: one originator,
    # so exactly the compose_binding record.
    rec3 = compose_who(store_v, "eat", "sheep")
    records["check3_who"] = rec3
    check("who_finds_wolf_same_sentence",
          rec3["slots"]["who"] == "wolf"
          and rec3["sentence"]
          == "Candidate (not yet verified): "
          "The wolf eats the sheep."
          and rec3 == rec1)
    check("verified_store_unmutated_by_compose",
          wi.store_hash(store_v) == hash_v)

    # 2. Candidate binding: a fresh store, one encounter.
    store_c = wi.new_store()
    wi.from_finder_results(store_c, s_wolf,
                           noun_finder.find(s_wolf),
                           verb_finder.find(s_wolf))
    hash_c = wi.store_hash(store_c)
    rec2 = compose_binding(store_c, "wolf", "eat")
    records["check2_candidate"] = rec2
    check("binding_candidate_prefix_and_status",
          rec2["verdict"] == "CANDIDATE"
          and rec2["status"] == "candidate"
          and rec2["sentence"] == "Candidate (not yet verified): "
          "The wolf eats the sheep."
          and rec2["slots"]["when"] == [1],
          repr(rec2["sentence"]))
    check("candidate_store_unmutated_by_compose",
          wi.store_hash(store_c) == hash_c)

    # 4. What-is verified under the PROM-1 class rule
    # (2026-10-29): promotion requires attestation across
    # distinct provenance CLASSES, not distinct records
    # within one class — two verifier write_fills (the
    # pre-PROM-1 D13 fixture) no longer verify. The first
    # fill stands (verifier class); the second attestation
    # is a dictionary-class attestation stamped through
    # the shared _attest engine with another door's
    # provenance (no public door writes is_a under a
    # second class), so the fact verifies on two classes
    # while its standing provenance remains the verifier's
    # and its attested_events remain [1, 2].
    store_k = wi.new_store()
    wi.write_fill(store_k, "wolf", "is_a", "canine",
                  "dictionary_note")
    seq = wi._next_seq(store_k)
    wi._attest(store_k, "wolf", "is_a", "canine",
               {"source": "dictionary",
                "evidence": "seed_exemplar"}, seq)
    wi._append_self_record(store_k, {
        "seq": seq, "kind": "cross_attestation",
        "outcome": "reattested", "word": "wolf",
        "identifier": "is_a"})
    hash_k = wi.store_hash(store_k)
    rec4 = compose_what_is(store_k, "wolf")
    records["check4_what_is_wolf"] = rec4
    print("CHECK4_RECORD " + _canonical(rec4))
    check("what_is_wolf_verified",
          rec4["verdict"] == "ANSWER"
          and rec4["status"] == "verified"
          and rec4["sentence"] == "A wolf is a canine."
          and rec4["slots"]["what"] == {"is_a": "canine",
                                         "status": "verified"}
          and rec4["slots"]["when"] == [1, 2])
    check("what_is_wolf_definition_on_record",
          isinstance(rec4["definition_on_record"], str)
          and len(rec4["definition_on_record"]) > 0)
    check("kind_store_unmutated_by_compose",
          wi.store_hash(store_k) == hash_k)

    # 5 + 6. What-is on an empty store: ball (in the
    # dictionary, no kind stored) and a nonsense word (in
    # neither). Determined from the actual dictionary build
    # this tree carries: ball DOES resolve (headword "ball",
    # form "other", 5 definitions), so the definition is on
    # record and no definition gap is named.
    store_e = wi.new_store()
    hash_e = wi.store_hash(store_e)
    rec5 = compose_what_is(store_e, "ball")
    records["check5_what_is_ball"] = rec5
    print("BALL_DEFINITION_ON_RECORD "
          + ("True" if rec5["definition_on_record"] else "False")
          + " (ball resolves in this dictionary build; the "
          "definition is on record and only the kind is missing)")
    check("what_is_ball_hold_kind_gap_definition_present",
          rec5["verdict"] == "HOLD"
          and rec5["sentence"] is None
          and "kind: not stored" in rec5["gaps"]
          and rec5["definition_on_record"] is not None
          and "definition: not stored" not in rec5["gaps"])
    rec6 = compose_what_is(store_e, "flibberty")
    records["check6_what_is_flibberty"] = rec6
    check("what_is_nonsense_hold_both_gaps",
          rec6["verdict"] == "HOLD"
          and rec6["sentence"] is None
          and rec6["definition_on_record"] is None
          and "kind: not stored" in rec6["gaps"]
          and "definition: not stored" in rec6["gaps"])
    check("empty_store_unmutated_by_compose",
          wi.store_hash(store_e) == hash_e)

    # Unknown binding: HOLD, who/what named as not stored.
    rec7 = compose_binding(store_e, "wolf", "eat")
    records["check7_unknown_binding"] = rec7
    check("unknown_binding_hold",
          rec7["verdict"] == "HOLD"
          and rec7["sentence"] is None
          and "who: not stored" in rec7["gaps"]
          and "what: not stored" in rec7["gaps"])

    print("COMPOSED_RECORDS_JSON " + _canonical(records))
    passed = sum(1 for r in results if r)
    print("Passed %d of %d checks" % (passed, len(results)))
    print("ALL CHECKS PASS" if passed == len(results)
          else "SOME CHECKS FAILED")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(run_checks())
