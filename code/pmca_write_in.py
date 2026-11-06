"""PMCA Write-In - accumulation of attested determinations.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's
architecture.

Problem this module answers (Jonathan, 2026-10-21 late evening):
"It would appear that relationships are not building." The
finders determine relationships per sentence and the results
evaporate; nothing writes a determination into an entry. This
module is that write-in, built in the duplicate tree first.

Design, from Jonathan's recorded rulings:

- Attested determinations are stored into entries as
  SUBCOORDINATE VALUES: identifier -> a SINGLE target word
  (is_a takes a singular target, 2026-10-21; functional
  identifiers here follow the same rule). EVENT bindings are
  the exception in keying, not in spirit (Pack F+B,
  2026-10-26; synthesis D3, Argon item 1): a binding is
  scoped to its subject by composite identifiers —
  originates::<verb> on the subject entry,
  originated_by::<subject> and acts_on::<subject> on the
  verb entry, target_of::<verb> on the target entry — so
  two subjects of one verb never share a slot, never
  stitch each other's targets, and never file as each
  other's conflicts. A conflict remains only when the SAME
  subject+verb is attested with a different target.
- Everything lands on the POTENTIAL side first: a write is a
  "candidate" on first attestation. The SAME identifier+value
  attested again under a DIFFERENT provenance record (source
  + evidence — for finder writes the sentence is part of the
  stored provenance record) promotes it to "verified":
  cross-verification by independent re-attestation
  (2026-10-26, D13; claims-05 F3: replay of the same
  provenance is NOT verification). Every distinct attestation
  provenance is retained on the fact's "provenance_history"
  (the standing "provenance" is the first). An explicit
  promote() may verify only a fact already carrying two
  distinct provenances; below that it refuses by name.
  PROM-1 (ruled 2026-10-29, superseding D13's record
  counting above): promotion requires attestation across
  distinct provenance CLASSES, not distinct records within
  one class. A class is the value of a provenance record's
  "source" field — the writing door's identity ("finders",
  "verifier", "morphology", "enrichment", ...). Evidence
  strings and sentence text are NOT classes: two records
  under one class never promote, however different their
  evidence; records spanning two or more classes do.
  Distinct provenance RECORDS are still all retained in
  "provenance_history"; only the promotion count changed,
  from records to classes.
- A conflicting value for an existing identifier does NOT
  overwrite. It is recorded as a conflict on the entry and the
  stored value stands: HOLD discipline at the data level.
- Every record carries provenance (source + evidence kind, and
  the sentence for finder writes) and sequence numbers only.
  There are NO wall-clock timestamps anywhere in this store.
- SELF ACCUMULATION (second destination): a reserved entry,
  "self_entry", which no dictionary word can collide with.
  Every finder encounter, OK or HOLD, is appended there
  (seq, sentence, outcome, hold reason, write count). Gaps
  live on the self entry as a TYPED INDEX (P1): "gaps" is a
  dict keyed by what is missing — "slot::<word>::<identifier>",
  "word::<token>::<missing_property>", or
  "clause::<guard>::<signature>" — and each entry is
  {"kind", "status": "OPEN", "first_seq",
  "waiting_encounters": [seq, ...], "reason"}. record_gap()
  indexes a slot gap; a held encounter indexes the keys
  derived from its finder hold reasons, waiting on the
  encounter's own seq (indexing consumes no seq); composer
  HOLDs are indexed at this surface by
  record_composer_hold(). Repeated HOLDs append waiters to
  the same entry; no duplicate entries. No silent gaps: what
  the system met and could not store is itself stored.
  Every mutating operation that consumes a seq — encounter,
  write_fill, write_form_relationships, record_gap, promote,
  record_composer_hold — also appends a minimal record
  (seq, kind, outcome) to the self entry's encounters, so
  the store's sequence has no silent gaps either
  (2026-10-26, D9; claims-13 C2, claims-05 F5). Population
  and enrichment append their own records in pmca_population.
- Reads are lookups only. binding_for() and entry_view() read
  the store and nothing else; no finder is ever called from a
  read path. Unknown lookups return None.

Finder entry-point keys use the finders' own reported forms:
subject and target as reported, and the verb BASE as reported
(the noun/verb finders return "eat" for "The wolf eats the
sheep."), so the verb entry is keyed by its base form.

The core store is a plain dict and works without any sibling
module. Sibling finders are imported only inside run_checks(),
where the real duplicate-tree finders and morphology module
exercise this store against the real dictionary.

Deterministic. Stdlib only.
"""

import copy
import hashlib
import json

SELF_KEY = "self_entry"


# ---------------------------------------------------------------------
# Store construction
# ---------------------------------------------------------------------

def new_store():
    """A fresh write-in store: a plain dict. 'entries' maps a
    word to its entry; the reserved SELF_KEY entry holds the
    system's own encounter records and its typed gaps index
    (a dict, keyed by gap key). 'seq' is the last event
    sequence number issued (events are numbered from 1)."""
    return {"seq": 0,
            "entries": {SELF_KEY: {"encounters": [], "gaps": {}}}}


def _next_seq(store):
    store["seq"] += 1
    return store["seq"]


def _word_entry(store, word):
    """Get or create a word entry. The reserved self key can
    never be a word entry."""
    if word == SELF_KEY:
        raise ValueError("reserved key: " + SELF_KEY)
    entry = store["entries"].get(word)
    if entry is None:
        entry = {"subcoordinates": {}, "conflicts": []}
        store["entries"][word] = entry
    return entry


# ---------------------------------------------------------------------
# Typed gaps index (self entry)
# ---------------------------------------------------------------------

def _ensure_gaps_index(store):
    """The self entry's gaps as the typed index (a dict),
    converting the pre-P1 list form deterministically on
    first gap access: each old record {"seq", "word",
    "identifier", "reason"} becomes the entry under
    "slot::<word>::<identifier>" (kind "slot", first_seq and
    sole initial waiter its seq, reason its reason); records
    naming the same slot merge, waiters in record order.
    Nothing is lost. Returns the index dict (the store's own
    object, mutated in place)."""
    self_entry = store["entries"][SELF_KEY]
    gaps = self_entry.get("gaps")
    if isinstance(gaps, dict):
        return gaps
    index = {}
    if isinstance(gaps, list):
        for old in gaps:
            if not isinstance(old, dict):
                continue
            word = old.get("word")
            identifier = old.get("identifier")
            if word is None or identifier is None:
                continue
            key = "slot::%s::%s" % (word, identifier)
            seq = old.get("seq")
            entry = index.get(key)
            if entry is None:
                index[key] = {
                    "kind": "slot", "status": "OPEN",
                    "first_seq": seq,
                    "waiting_encounters":
                        [seq] if seq is not None else [],
                    "reason": old.get("reason")}
            elif seq is not None \
                    and seq not in entry["waiting_encounters"]:
                entry["waiting_encounters"].append(seq)
    self_entry["gaps"] = index
    return index


def _index_gap(store, key, kind, reason, waiting_seq):
    """Create or update the gaps-index entry under key: a new
    entry is {"kind", "status": "OPEN", "first_seq":
    waiting_seq, "waiting_encounters": [waiting_seq],
    "reason"}; an existing entry appends waiting_seq when not
    already present (first_seq and reason stand), and a HOLD
    on a CLOSED entry reopens it (status "OPEN", first_seq
    unchanged). NEVER consumes a store seq. Returns a copy of
    the entry."""
    index = _ensure_gaps_index(store)
    entry = index.get(key)
    if entry is None:
        index[key] = {"kind": kind, "status": "OPEN",
                      "first_seq": waiting_seq,
                      "waiting_encounters": [waiting_seq],
                      "reason": reason}
    else:
        if waiting_seq not in entry["waiting_encounters"]:
            entry["waiting_encounters"].append(waiting_seq)
        if entry.get("status") == "CLOSED":
            entry["status"] = "OPEN"
    return copy.deepcopy(index[key])


def gap_key_for_reason(reason):
    """Derive the typed gap key for one finder/guard hold
    reason: returns (key, kind), deterministically, per the
    P1 expectations table. The fields-missing row is derived
    by the encounter path, which qualifies the reason with
    the missing field ("finder_ok_but_fields_missing:subject"
    / ":verb") before calling this. Any reason not listed
    falls back to "clause::unmapped_hold_reason::<reason>"."""
    if isinstance(reason, str):
        if reason.startswith("unknown_word:"):
            tok = reason[len("unknown_word:"):]
            return "word::%s::dictionary" % tok, "word"
        if reason.startswith("pos_ambiguous:"):
            tok = reason[len("pos_ambiguous:"):]
            return "word::%s::pos" % tok, "word"
        if reason.startswith("unverified_possessor_stem:"):
            stem = reason[len("unverified_possessor_stem:"):]
            return "word::%s's::morphology" % stem, "word"
        if reason.startswith("unverified_plural_possessor:"):
            stem = reason[len("unverified_plural_possessor:"):]
            return "word::%s'::morphology" % stem, "word"
        if reason.startswith("headless_or_non_noun_genitive:"):
            tok = reason[len("headless_or_non_noun_genitive:"):]
            return "word::%s::morphology" % tok, "word"
        if reason.startswith("ambiguous_contraction:"):
            tok = reason[len("ambiguous_contraction:"):]
            return "word::%s::morphology" % tok, "word"
        if reason == "passive_or_auxiliary_out_of_scope":
            return ("clause::passive_geometry_unhandled_in_p0"
                    "::AUX_BE+PAST_PARTICIPLE", "clause")
        if reason == "non_svo_np_geometry":
            return ("clause::non_svo_np_geometry::NON_SVO_NP",
                    "clause")
        if reason == "multi_verb_unsegmented":
            return ("clause::multi_verb_unsegmented"
                    "::MULTI_FINITE_VERB", "clause")
        if reason.startswith("prepositional_barrier:"):
            prep = reason[len("prepositional_barrier:"):]
            return "clause::prepositional_barrier::%s" % prep, \
                "clause"
        if reason.startswith("noun_phrase_head_not_noun:"):
            tok = reason[len("noun_phrase_head_not_noun:"):]
            return ("clause::noun_phrase_head_not_noun::%s"
                    % tok, "clause")
        if reason == "ambiguous_verb_candidates":
            return ("clause::ambiguous_verb_candidates"
                    "::VERB_SPAN", "clause")
        if reason == "genitive_out_of_scope":
            return ("clause::genitive_out_of_scope::GENITIVE",
                    "clause")
        if reason == "finder_ok_but_fields_missing:subject":
            return "clause::fields_missing::subject", "clause"
        if reason == "finder_ok_but_fields_missing:verb":
            return "clause::fields_missing::verb", "clause"
    return ("clause::unmapped_hold_reason::%s" % (reason,),
            "clause")


def gap_entry(store, key):
    """A deep copy of the gaps-index entry under the exact
    key, or None. The lookup is a plain dict lookup (O(1))."""
    entry = _ensure_gaps_index(store).get(key)
    if entry is None:
        return None
    return copy.deepcopy(entry)


def wake_up_for(store, word, identifier):
    """The gaps-index entry waiting on the slot
    (word, identifier): gap_entry() under its exact slot key,
    or None."""
    return gap_entry(store, "slot::%s::%s" % (word, identifier))


def close_gap(store, key):
    """Mark the gaps-index entry under key "CLOSED", its
    history (first_seq, waiting_encounters, reason) intact.
    Returns a copy of the entry, or None when no such entry
    exists."""
    entry = _ensure_gaps_index(store).get(key)
    if entry is None:
        return None
    entry["status"] = "CLOSED"
    return copy.deepcopy(entry)


def _gap_wake(store, word, identifier):
    """The wake-up report for the slot (word, identifier):
    {"key", "waiting_encounters"} when an OPEN gaps-index
    entry waits on its exact slot key, else None. Read-only
    apart from the legacy gaps conversion."""
    key = "slot::%s::%s" % (word, identifier)
    entry = _ensure_gaps_index(store).get(key)
    if entry is None or entry.get("status") != "OPEN":
        return None
    return {"key": key,
            "waiting_encounters":
                list(entry["waiting_encounters"])}


# ---------------------------------------------------------------------
# Core attestation write
# ---------------------------------------------------------------------

def _append_self_record(store, record):
    """Append a minimal record of a seq-consuming operation
    to the self entry's encounters (Pack F+B, 2026-10-26;
    D9): {"seq", "kind", "outcome", ...}. The encounter,
    population, and enrichment paths append fuller records
    of their own; this helper serves the write paths that
    previously consumed seqs in silence."""
    store["entries"][SELF_KEY]["encounters"].append(record)


def _provenance_history(sub):
    """The fact's distinct attestation provenances, in
    attestation order. Facts written before provenance
    history was kept (or built by hand) carry none: their
    standing "provenance" IS their history of one, derived
    here without mutating the fact. The stored list, when
    present, is the fact's own object."""
    history = sub.get("provenance_history")
    if isinstance(history, list):
        return history
    provenance = sub.get("provenance")
    return [provenance] if isinstance(provenance, dict) else []


def _provenance_classes(history):
    """The set of provenance CLASSES in a provenance history
    (PROM-1, ruled 2026-10-29), read through the GOVERNED
    source->lineage table (Candidacy Rule Set C-3, ruled
    2026-10-31; pmca_lineage_table, shared data): spelling
    variants of one lineage ("Webster" / "Webster 1913")
    are ONE class (Ledger D-2, fixed 2026-11-01), and a
    record with a missing or empty source counts as NO
    class, never as a class (Ledger D-3, fixed 2026-11-01;
    this supersedes the pre-fix text here, under which a
    missing source counted as one unnamed class, None).
    Non-dict records carry no key and contribute no class.
    Evidence strings and sentence text are NOT classes."""
    import pmca_lineage_table
    return pmca_lineage_table.provenance_classes(
        history, context="pmca_write_in._provenance_classes")


def _attest(store, word, identifier, value, provenance, event_seq):
    """One attestation of identifier -> value on word's entry,
    as part of the recorded event event_seq. Returns an outcome
    dict. First attestation lands as a candidate; a
    re-attestation of the same value promotes to verified
    only when the fact's provenance history spans two or
    more distinct provenance CLASSES (PROM-1, ruled
    2026-10-29, superseding D13's record counting: distinct
    provenance RECORDS are still all retained in history,
    but promotion counts distinct CLASSES — the "source"
    values; a replay of the same provenance, or a second
    record under the same class, is recorded as an
    attestation but does NOT verify); a different value
    records a conflict and changes nothing. Every distinct
    provenance is retained on the fact's "provenance_history";
    the standing "provenance" remains the first. When the
    slot is newly written or promoted candidate -> verified
    and an OPEN gaps-index entry waits on its exact slot key,
    the outcome carries "gap_wake" naming the key and its
    waiting seqs (return-only; no extra store mutation)."""
    entry = _word_entry(store, word)
    sub = entry["subcoordinates"].get(identifier)
    if sub is None:
        entry["subcoordinates"][identifier] = {
            "value": value,
            "status": "candidate",
            "provenance": provenance,
            "provenance_history": [copy.deepcopy(provenance)],
            "attestations": 1,
            "attested_events": [event_seq],
            "first_seq": event_seq,
            "last_seq": event_seq,
        }
        outcome = {"outcome": "written_new", "word": word,
                   "identifier": identifier, "value": value,
                   "status": "candidate"}
        wake = _gap_wake(store, word, identifier)
        if wake is not None:
            outcome["gap_wake"] = wake
        return outcome
    if sub["value"] == value:
        sub["attestations"] += 1
        if event_seq not in sub["attested_events"]:
            sub["attested_events"].append(event_seq)
        sub["last_seq"] = event_seq
        history = sub.get("provenance_history")
        if not isinstance(history, list):
            # A fact written before history was kept: its
            # standing provenance is its history of one.
            history = _provenance_history(sub)
            sub["provenance_history"] = history
        if provenance not in history:
            history.append(copy.deepcopy(provenance))
        promoted = False
        if (len(_provenance_classes(history)) >= 2
                and sub["status"] == "candidate"):
            sub["status"] = "verified"
            promoted = True
        outcome = {"outcome": "reattested", "word": word,
                   "identifier": identifier, "value": value,
                   "status": sub["status"]}
        if promoted:
            wake = _gap_wake(store, word, identifier)
            if wake is not None:
                outcome["gap_wake"] = wake
        return outcome
    conflict = {"seq": event_seq, "identifier": identifier,
                "stored_value": sub["value"],
                "attempted_value": value,
                "provenance": provenance}
    entry["conflicts"].append(conflict)
    return {"outcome": "conflict", "word": word,
            "identifier": identifier, "value": sub["value"],
            "status": sub["status"], "conflict": conflict}


# ---------------------------------------------------------------------
# Write paths
# ---------------------------------------------------------------------

def _migrate_legacy_slot(store, word, legacy, scoped):
    """Upgrade-on-write for ONE legacy single slot (Pack
    F+B, 2026-10-26): when word's entry still carries the
    pre-restructure identifier `legacy` and not yet
    `scoped`, the legacy fact moves — deep-copied, verbatim —
    under the scoped identifier and the legacy key is
    removed. Nothing is invented and nothing else moves."""
    entry = store["entries"].get(word)
    if not isinstance(entry, dict):
        return
    subs = entry.get("subcoordinates")
    if not isinstance(subs, dict):
        return
    if legacy in subs and scoped not in subs:
        subs[scoped] = copy.deepcopy(subs.pop(legacy))


def _migrate_legacy_event_slots(store, subject, verb, target):
    """Upgrade-on-write for the legacy event-binding shape
    (Pack F+B, 2026-10-26), run before a finder encounter
    writes the scoped slots for (subject, verb, target).
    The subject's legacy "originates" and the target's
    legacy "target_of" move under their scoped keys. On the
    verb, the legacy pair ("originated_by", "acts_on") is
    scoped under the originator the legacy record itself
    names — the pair is preserved as recorded, never
    recombined with anything else. A lone legacy "acts_on"
    with no recorded originator cannot be scoped without
    inventing a subject and is left standing; binding_for's
    legacy fallback can still read it where the legacy pair
    survives."""
    _migrate_legacy_slot(store, subject, "originates",
                         "originates::" + verb)
    if target:
        _migrate_legacy_slot(store, target, "target_of",
                             "target_of::" + verb)
    entry = store["entries"].get(verb)
    if not isinstance(entry, dict):
        return
    subs = entry.get("subcoordinates")
    if not isinstance(subs, dict):
        return
    legacy_ob = subs.get("originated_by")
    if isinstance(legacy_ob, dict) and legacy_ob.get("value"):
        originator = legacy_ob["value"]
        _migrate_legacy_slot(store, verb, "originated_by",
                             "originated_by::" + originator)
        _migrate_legacy_slot(store, verb, "acts_on",
                             "acts_on::" + originator)


def from_finder_results(store, sentence, noun_result, verb_result):
    """Accumulate one finder encounter. Only when BOTH finder
    verdicts are OK are bindings written, under SUBJECT-SCOPED
    composite identifiers (Pack F+B, 2026-10-26): on the
    subject entry originates::<verb> -> verb; on the verb
    entry originated_by::<subject> -> subject and, when a
    target exists, acts_on::<subject> -> target; on the
    target entry target_of::<verb> -> verb. On any HOLD
    nothing is written to word entries. Either way an
    encounter record is appended to the self entry; on a held
    outcome the typed gap keys derived from BOTH finder
    results' distinct hold reasons (or, when both finders are
    OK but a field is missing, from the missing field) are
    then indexed on the self entry, waiting on this
    encounter's seq — indexing consumes no extra seq. Returns
    the encounter record."""
    seq = _next_seq(store)
    ok = (noun_result.get("verdict") == "OK"
          and verb_result.get("verdict") == "OK")
    subject = noun_result.get("subject") or verb_result.get("subject")
    verb = noun_result.get("verb") or verb_result.get("verb")
    target = noun_result.get("target") or verb_result.get("target")
    writes = 0
    conflicts = 0
    hold_reason = None
    if ok and subject and verb:
        _migrate_legacy_event_slots(store, subject, verb,
                                    target)
        provenance = {"source": "finders",
                      "evidence": "finder_parse",
                      "sentence": sentence}
        plan = [(subject, "originates::" + verb, verb),
                (verb, "originated_by::" + subject, subject)]
        if target:
            plan.append((verb, "acts_on::" + subject, target))
            plan.append((target, "target_of::" + verb, verb))
        # P0 (shared genitive rule, pmca_parse_guards): a
        # possessor the primary finders determined in sequence
        # is recorded on its head entry as a candidate binding,
        # through the same attestation path as every other
        # binding from this parse — never from a HOLD.
        for res in (noun_result, verb_result):
            for g in (res.get("possessors") or []):
                pair = (g["head"], "possessor", g["possessor"])
                if pair not in plan:
                    plan.append(pair)
        for word, identifier, value in plan:
            outcome = _attest(store, word, identifier, value,
                              provenance, seq)
            if outcome["outcome"] == "conflict":
                conflicts += 1
            else:
                writes += 1
        outcome_name = "written"
    else:
        outcome_name = "held"
        if ok:
            hold_reason = "finder_ok_but_fields_missing"
        else:
            reasons = [r.get("hold_reason")
                       for r in (noun_result, verb_result)
                       if r.get("verdict") != "OK"]
            hold_reason = next((r for r in reasons if r), None)
    record = {"seq": seq, "kind": "encounter", "sentence": sentence,
              "outcome": outcome_name, "hold_reason": hold_reason,
              "writes": writes, "conflicts": conflicts}
    store["entries"][SELF_KEY]["encounters"].append(record)
    if outcome_name == "held":
        seen_keys = set()
        if ok:
            # Both finders OK but a field is missing: the gap
            # is the missing field itself.
            for field, present in (("subject", subject),
                                   ("verb", verb)):
                if present:
                    continue
                key, kind = gap_key_for_reason(
                    "finder_ok_but_fields_missing:" + field)
                if key not in seen_keys:
                    seen_keys.add(key)
                    _index_gap(store, key, kind,
                               "finder_ok_but_fields_missing", seq)
        else:
            for res in (noun_result, verb_result):
                if res.get("verdict") == "OK":
                    continue
                reason = res.get("hold_reason")
                if not reason:
                    continue
                key, kind = gap_key_for_reason(reason)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                _index_gap(store, key, kind, reason, seq)
    return copy.deepcopy(record)


def write_fill(store, word, identifier, value, evidence):
    """Verifier-style direct write of one attested subcoordinate,
    with an evidence kind string. Returns the outcome dict.
    The event is recorded on the self entry (seq, kind
    "write_fill", outcome), so the sequence reconciles
    (Pack F+B, 2026-10-26; D9)."""
    seq = _next_seq(store)
    provenance = {"source": "verifier", "evidence": evidence}
    outcome = _attest(store, word, identifier, value,
                      provenance, seq)
    _append_self_record(store, {
        "seq": seq, "kind": "write_fill",
        "outcome": outcome["outcome"], "word": word,
        "identifier": identifier})
    return outcome


def write_form_relationships(store, word, family):
    """Write singular form relationships for word from
    morphology-style data. Accepted shapes:
    - the morphology module's form_family() output: its
      "families" map {entry: {form: token}} contributes
      form + "_of" -> entry for every form whose token is word;
    - a plain mapping whose keys already end in "_of":
      identifier -> value, written as given;
    - a plain mapping of form name -> base word
      ({"plural": "wolf"}): written as form + "_of" -> base.
    Returns the list of outcome dicts, in sorted pair order."""
    pairs = []
    if isinstance(family, dict) and "families" in family:
        for entry_word in sorted(family["families"]):
            forms = family["families"][entry_word]
            for form_name in sorted(forms):
                if forms[form_name] == word:
                    pairs.append((form_name + "_of", entry_word))
    elif isinstance(family, dict):
        for key in sorted(family):
            value = family[key]
            if not isinstance(value, str):
                continue
            if key.endswith("_of"):
                pairs.append((key, value))
            else:
                pairs.append((key + "_of", value))
    seq = _next_seq(store)
    provenance = {"source": "morphology", "evidence": "form_family"}
    outcomes = [_attest(store, word, identifier, value,
                        provenance, seq)
                for identifier, value in pairs]
    _append_self_record(store, {
        "seq": seq, "kind": "write_form_relationships",
        "outcome": "written", "word": word,
        "writes": len(outcomes)})
    return outcomes


def promote(store, word, identifier, reason):
    """Verify a stored subcoordinate on explicit review,
    recording the reason — ONLY when the fact's provenance
    history spans two or more distinct provenance CLASSES
    (PROM-1, ruled 2026-10-29, superseding D13's record
    counting; D13, 2026-10-26; claims-05 F3: the one-call
    flip from a single attestation is closed). Two records
    under one class do not meet the threshold, however
    distinct their evidence. Below that threshold the call
    refuses, by name, consumes no seq, and changes nothing:
    {"outcome": "refused", "refusal":
    "promote_requires_two_distinct_provenances", ...}.
    Returns a copy of the subcoordinate record on success,
    or None when there is nothing stored under that
    identifier. A successful promote consumes one seq and
    appends its record to the self entry."""
    entry = store["entries"].get(word)
    if entry is None or identifier not in entry["subcoordinates"]:
        return None
    sub = entry["subcoordinates"][identifier]
    history = _provenance_history(sub)
    distinct = len({json.dumps(p, sort_keys=True)
                    for p in history})
    distinct_classes = len(_provenance_classes(history))
    if distinct_classes < 2:
        return {"outcome": "refused",
                "refusal":
                    "promote_requires_two_distinct_provenances",
                "word": word, "identifier": identifier,
                "distinct_provenances": distinct,
                "distinct_provenance_classes": distinct_classes}
    seq = _next_seq(store)
    sub["status"] = "verified"
    sub["promoted_reason"] = reason
    sub["promoted_seq"] = seq
    _append_self_record(store, {
        "seq": seq, "kind": "promote", "outcome": "verified",
        "word": word, "identifier": identifier})
    return copy.deepcopy(sub)


def record_gap(store, word, identifier, reason):
    """Record a gap on the self entry's typed index: an empty
    slot the system met and could not fill, indexed under
    "slot::<word>::<identifier>" (kind "slot") with this
    event's seq as its waiter. Consumes one seq, as before,
    and appends its minimal record to the self entry's
    encounters, so the sequence reconciles (Pack F+B,
    2026-10-26; D9). Writes nothing to any word entry.
    Returns the gap record, now carrying its "gap_key"."""
    seq = _next_seq(store)
    key = "slot::%s::%s" % (word, identifier)
    _index_gap(store, key, "slot", reason, seq)
    _append_self_record(store, {
        "seq": seq, "kind": "gap", "outcome": "recorded",
        "word": word, "identifier": identifier,
        "gap_key": key})
    record = {"seq": seq, "kind": "gap", "word": word,
              "identifier": identifier, "reason": reason,
              "gap_key": key}
    return copy.deepcopy(record)


def record_composer_hold(store, record):
    """Index a composer record's named gaps on the self
    entry's typed index: the encounter-surface bridge for the
    (pure) composer. Consumes ONE seq as the surfacing event;
    every indexed entry waits on that seq; a minimal
    self-entry record of the surfacing is appended (Pack
    F+B, 2026-10-26; D9), whether or not any gap mapped. The
    mapping is deterministic, from the record's own fields
    only (kind, gaps, slots), and follows the subject-scoped
    binding shape (Pack F+B, Part B):

    - what_is, "kind: not stored" ->
      "slot::<word>::is_a"; "definition: not stored" ->
      "slot::<word>::definition" (word: slots["who"]).
    - binding, "who: not stored" together with
      "what: not stored" -> "slot::<subject>::originates::<verb>"
      (subject: slots["where"]["store_entry"]) and
      "slot::<verb>::acts_on::<subject>".
    - binding with "who: not stored" alone (a compose_who
      HOLD) -> "slot::<verb>::originated_by".
    - binding carrying "what: target not stored" (a stored
      binding with no target, named in an ANSWER/CANDIDATE
      record) -> "slot::<verb>::acts_on::<subject>"
      (subject: slots["who"]).

    Named gaps outside this mapping are not indexed; the
    surfacing record on the self entry still names them.
    Returns {"seq", "gap_keys": [...], "recorded": n}."""
    seq = _next_seq(store)
    gaps = record.get("gaps") or []
    kind = record.get("kind")
    slots = record.get("slots") or {}
    keys = []

    def _add(key, reason):
        if key not in keys:
            keys.append(key)
            _index_gap(store, key, "slot", reason, seq)

    for gap in gaps:
        if kind == "what_is":
            word = slots.get("who") or (
                slots.get("where") or {}).get("store_entry")
            if not word:
                continue
            if gap == "kind: not stored":
                _add("slot::%s::is_a" % word, gap)
            elif gap == "definition: not stored":
                _add("slot::%s::definition" % word, gap)
        elif kind == "binding":
            what = slots.get("what") or {}
            verb = what.get("verb")
            if gap == "who: not stored":
                if "what: not stored" in gaps:
                    subject = (slots.get("where") or {}).get(
                        "store_entry")
                    if subject and verb:
                        _add("slot::%s::originates::%s"
                             % (subject, verb), gap)
                        _add("slot::%s::acts_on::%s"
                             % (verb, subject), gap)
                elif verb:
                    _add("slot::%s::originated_by" % verb, gap)
            elif gap == "what: not stored" and verb:
                subject = (slots.get("where") or {}).get(
                    "store_entry")
                if subject:
                    _add("slot::%s::acts_on::%s"
                         % (verb, subject), gap)
            elif gap == "what: target not stored" and verb:
                subject = slots.get("who")
                if subject:
                    _add("slot::%s::acts_on::%s"
                         % (verb, subject), gap)
    _append_self_record(store, {
        "seq": seq, "kind": "composer_hold",
        "outcome": "recorded" if keys else "unmapped",
        "record_kind": kind, "gap_keys": list(keys),
        "gaps": list(gaps)})
    return {"seq": seq, "gap_keys": keys, "recorded": len(keys)}


# ---------------------------------------------------------------------
# Dossier home-build door (DICT-1 Part 4; WG2c gate wiring,
# 2026-11-01)
# ---------------------------------------------------------------------

def write_dossier(store, dossier):
    """Build one word's HOME entry from a gathered DOSSIER —
    the production home-build door for dossier-shaped data
    (DICT-1 Part 4, RULED 2026-10-31). A dossier is gathered
    data only, {"word": w, "fields": {field: [attestation,
    ...]}}: the starting point, never a home. This door is
    the ONLY production path that turns a dossier into a
    home entry in this store, and it delegates in full to
    the gate, pmca_dossier_gate.build_home_entry: agreeing
    fields cross through _attest carrying their dossier
    starting-point provenance inside the home fact;
    conflicted and unattested fields do not cross and are
    recorded as gaps. The import is function-level because
    the gate module imports this module (its attestation
    engine and gap recorder), so a module-level import here
    would be circular.

    The single-attestation doors of this module
    (write_fill, from_finder_results,
    write_form_relationships, and the _attest engine itself)
    are NOT dossier doors and are not routed through the
    gate: each carries ONE live attestation with its own
    provenance (a verifier fill, a finder encounter, a
    morphology family), never a gathered multi-source field,
    and _attest's own conflict discipline governs them
    (a conflicting value is recorded on the entry and the
    stored value stands). No gathered/dossier-shaped value
    may be crossed into a home entry by calling _attest
    directly with a dossier's first value — that was the
    WG2 battery's BEFORE path; dossiers go through here."""
    import pmca_dossier_gate
    return pmca_dossier_gate.build_home_entry(store, dossier)


# ---------------------------------------------------------------------
# Reads (store only; no finder is ever called here)
# ---------------------------------------------------------------------

def entry_view(store, word):
    """A copy of the stored entry for word, or None. For
    SELF_KEY, the self entry (encounters and gaps)."""
    entry = store["entries"].get(word)
    if entry is None:
        return None
    return copy.deepcopy(entry)


def binding_for(store, subject, verb):
    """The stored binding of subject as originator of verb:
    {"subject", "verb", "target", "status", "attestations"},
    read from the subject's originates::<verb> subcoordinate
    and the verb's SUBJECT-SCOPED acts_on::<subject>
    subcoordinate (Pack F+B, 2026-10-26) — the target read
    is this subject's own, never another subject's. None
    when no such binding is stored. The status shown is the
    originates subcoordinate's: candidate until
    cross-verified, verified after.

    Legacy fallback (upgrade-on-write keeps the old shape
    readable until an encounter rewrites it): a store still
    carrying the pre-restructure single slots is read
    through them — originates / acts_on — with the target
    taken from the legacy acts_on only when the legacy
    originated_by names this subject, or when the subject
    side itself is legacy (the old store's own pairing)."""
    subj = store["entries"].get(subject)
    if subj is None:
        return None
    subs = subj["subcoordinates"]
    sub = subs.get("originates::" + verb)
    legacy_subject = False
    if sub is None:
        old = subs.get("originates")
        if isinstance(old, dict) and old.get("value") == verb:
            sub = old
            legacy_subject = True
        else:
            return None
    target = None
    verb_entry = store["entries"].get(verb)
    if verb_entry is not None:
        vsubs = verb_entry["subcoordinates"]
        acts = vsubs.get("acts_on::" + subject)
        if isinstance(acts, dict):
            target = acts["value"]
        else:
            old_acts = vsubs.get("acts_on")
            old_ob = vsubs.get("originated_by")
            if isinstance(old_acts, dict) and (
                    legacy_subject
                    or (isinstance(old_ob, dict)
                        and old_ob.get("value") == subject)):
                target = old_acts["value"]
    return {"subject": subject, "verb": verb, "target": target,
            "status": sub["status"],
            "attestations": sub["attestations"]}


# ---------------------------------------------------------------------
# Persistence: canonical JSON, sorted keys, no timestamps
# ---------------------------------------------------------------------

def canonical(store):
    """The store's canonical serialization."""
    return json.dumps(store, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True)


def store_hash(store):
    """sha256 of the canonical serialization."""
    return hashlib.sha256(canonical(store).encode("utf-8")).hexdigest()


def save(store, path):
    """Write the canonical serialization to path."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(canonical(store))


def load(path):
    """Read a store back from a canonical save file."""
    with open(path, "r", encoding="utf-8") as f:
        store = json.load(f)
    if (not isinstance(store, dict) or "entries" not in store
            or "seq" not in store
            or SELF_KEY not in store["entries"]):
        raise ValueError("not a write-in store: " + str(path))
    return store


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_write_in.py
# Uses the REAL duplicate-tree finders and morphology module
# against the real dictionary this tree carries.
# ---------------------------------------------------------------------

def run_checks():
    import os
    import sys
    import tempfile

    import pmca_sentence_analyzer as _analyzer
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    _analyzer.configure_dictionary(db)

    import pmca_morphology_finder as morph
    import pmca_noun_finder as noun_finder
    import pmca_verb_finder as verb_finder

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    def cross_attest(store_, word, identifier, value, evidence):
        # PROM-1 fixture helper: a second attestation stamped
        # with the dictionary door's class ("dictionary")
        # through the shared _attest engine, as a door would —
        # consuming a seq and appending a self record with
        # kind "cross_attestation", mirroring a door's
        # bookkeeping. No public door writes is_a under a
        # second class, so fixtures stamp it this way.
        seq = _next_seq(store_)
        outcome = _attest(store_, word, identifier, value,
                          {"source": "dictionary",
                           "evidence": evidence}, seq)
        _append_self_record(store_, {
            "seq": seq, "kind": "cross_attestation",
            "outcome": outcome["outcome"], "word": word,
            "identifier": identifier})
        return outcome

    store = new_store()

    # 1. A parsed sentence writes its bindings as candidates,
    #    with provenance, on the three entries involved.
    s1 = "The wolf eats the sheep."
    rec1 = from_finder_results(store, s1, noun_finder.find(s1),
                               verb_finder.find(s1))
    wolf = entry_view(store, "wolf")
    eat = entry_view(store, "eat")
    sheep = entry_view(store, "sheep")
    check("encounter_1_written", rec1["outcome"] == "written"
          and rec1["writes"] == 4, repr(rec1))
    check("wolf_originates_eat_candidate",
          wolf["subcoordinates"]["originates::eat"]["value"]
          == "eat"
          and wolf["subcoordinates"]["originates::eat"]
          ["status"] == "candidate")
    check("originates_provenance",
          wolf["subcoordinates"]["originates::eat"]
          ["provenance"]
          == {"source": "finders", "evidence": "finder_parse",
              "sentence": s1})
    # Pack F+B (Part B): the verb's binding slots are scoped
    # to the subject — originated_by::wolf, acts_on::wolf —
    # so no second subject can ever share or stitch them.
    check("eat_scoped_originator_and_target_slots",
          eat["subcoordinates"]["originated_by::wolf"]
          ["value"] == "wolf"
          and eat["subcoordinates"]["acts_on::wolf"]
          ["value"] == "sheep")
    check("sheep_target_of_eat",
          sheep["subcoordinates"]["target_of::eat"]["value"]
          == "eat")

    # 2. Re-attestation and promotion (D13, 2026-10-26;
    #    claims-05 F3).
    #
    # NOTE (stated in the open, per the build brief): the
    # second attestation of the wolf/eat/sheep binding is an
    # honest re-encounter of the same sentence as a NEW
    # encounter event (fresh finder runs, a distinct seq in
    # the self entry). Under the pre-D13 rule that promoted
    # the binding; that rule was the defect — replay of the
    # SAME provenance (same source, same evidence, same
    # sentence) is one source repeating itself, not
    # independent evidence. The re-encounter is recorded
    # (attestations 2, both events) and the binding STAYS
    # candidate. What verifies is a second attestation under
    # a DIFFERENT provenance, asserted just below.
    rec2 = from_finder_results(store, s1, noun_finder.find(s1),
                               verb_finder.find(s1))
    wolf2 = entry_view(store, "wolf")
    check("encounter_2_distinct_event",
          rec2["seq"] != rec1["seq"] and rec2["outcome"] == "written")
    check("replay_stays_candidate_not_verified",
          wolf2["subcoordinates"]["originates::eat"]["status"]
          == "candidate"
          and wolf2["subcoordinates"]["originates::eat"]
          ["attestations"] == 2
          and wolf2["subcoordinates"]["originates::eat"]
          ["attested_events"] == [rec1["seq"], rec2["seq"]]
          and len(wolf2["subcoordinates"]["originates::eat"]
                  ["provenance_history"]) == 1)
    # A different source attesting the same value DOES
    # promote.
    # PROM-1 re-base — two verifier fills are one class and
    # no longer promote (that was C-1); the second
    # attestation is stamped with the dictionary door's
    # class through the shared engine, as a door would.
    write_fill(store, "frog", "is_a", "animal", "dictionary_note")
    out = cross_attest(store, "frog", "is_a", "animal",
                       "seed_exemplar")
    check("cross_source_promotion",
          out["outcome"] == "reattested" and out["status"] == "verified")
    # promote() refuses below two distinct provenances
    # (claims-05 F3a: the one-call flip is closed), then
    # records its reason once the threshold is met.
    write_fill(store, "goat", "is_a", "animal", "dictionary_note")
    refused = promote(store, "goat", "is_a", "explicit_review")
    # PROM-1 re-base — two verifier fills are one class and
    # no longer promote (that was C-1); the second
    # attestation is stamped with the dictionary door's
    # class through the shared engine, as a door would.
    cross_attest(store, "goat", "is_a", "animal",
                 "seed_exemplar")
    promoted = promote(store, "goat", "is_a", "explicit_review")
    check("promote_refuses_then_records_reason",
          refused["outcome"] == "refused"
          and refused["refusal"]
          == "promote_requires_two_distinct_provenances"
          and promoted["status"] == "verified"
          and promoted["promoted_reason"] == "explicit_review")

    # 3. Readback from the store only. The finders play no part
    #    in this call; this module's read paths import nothing.
    binding = binding_for(store, "wolf", "eat")
    print("READBACK binding_for(wolf, eat) -> "
          + json.dumps(binding, sort_keys=True))
    check("readback_binding_candidate_scoped_target",
          binding == {"subject": "wolf", "verb": "eat",
                      "target": "sheep", "status": "candidate",
                      "attestations": 2})
    check("readback_unknown_is_none",
          binding_for(store, "wolf", "howls") is None
          and binding_for(store, "nosuchword", "eat") is None
          and entry_view(store, "nosuchword") is None)

    # 4. A HOLD sentence writes nothing to word entries and is
    #    recorded on the self entry as a held encounter.
    before = sorted(w for w in store["entries"] if w != SELF_KEY)
    s_hold = "The cup was broken by the man."
    rec_hold = from_finder_results(store, s_hold,
                                   noun_finder.find(s_hold),
                                   verb_finder.find(s_hold))
    after = sorted(w for w in store["entries"] if w != SELF_KEY)
    self_view = entry_view(store, SELF_KEY)
    check("hold_writes_nothing",
          entry_view(store, "cup") is None
          and entry_view(store, "man") is None and before == after)
    check("hold_recorded_on_self",
          rec_hold["outcome"] == "held"
          and rec_hold["hold_reason"]
          == "passive_or_auxiliary_out_of_scope"
          and self_view["encounters"][-1] == rec_hold)

    # 5. record_gap: a gap in the self entry's typed index,
    #    nothing on the word. (Re-based for P1: the gaps value
    #    is the index dict, not a record list; same facts.)
    gap = record_gap(store, "went", "past_of",
                     "form_entry_without_attested_base")
    gaps_main = entry_view(store, SELF_KEY)["gaps"]
    check("gap_recorded_word_untouched",
          entry_view(store, "went") is None
          and gap["identifier"] == "past_of"
          and gap["gap_key"] == "slot::went::past_of"
          and isinstance(gaps_main, dict)
          and gaps_main.get("slot::went::past_of")
          == {"kind": "slot", "status": "OPEN",
              "first_seq": gap["seq"],
              "waiting_encounters": [gap["seq"]],
              "reason": "form_entry_without_attested_base"})

    # 6. Conflict: a different value for an existing identifier
    #    is recorded and the stored value stands. (Pack F+B:
    #    the identifier is the scoped originates::eat — the
    #    SAME subject+verb attested with a different value is
    #    the one genuine contradiction the scoped shape
    #    still records.)
    out = write_fill(store, "wolf", "originates::eat", "howls",
                     "conflict_probe")
    wolf3 = entry_view(store, "wolf")
    check("conflict_recorded_value_stands",
          out["outcome"] == "conflict"
          and wolf3["subcoordinates"]["originates::eat"]
          ["value"] == "eat"
          and len(wolf3["conflicts"]) == 1
          and wolf3["conflicts"][0]["attempted_value"] == "howls")

    # 7. Morphology: wolves -> plural_of wolf, from the real
    #    morphology module's form_family output.
    fam = morph.form_family(["wolves"])
    outcomes = write_form_relationships(store, "wolves", fam)
    wolves = entry_view(store, "wolves")
    check("morphology_plural_of_written",
          len(outcomes) == 1
          and wolves["subcoordinates"]["plural_of"]["value"] == "wolf"
          and wolves["subcoordinates"]["plural_of"]["status"]
          == "candidate"
          and wolves["subcoordinates"]["plural_of"]["provenance"]
          ["source"] == "morphology")

    # 8. Persistence: save/load round trip preserves the hash,
    #    and the final canonical store is printed so two runs in
    #    separate processes can be compared byte for byte.
    tmp = os.path.join(tempfile.gettempdir(),
                       "pmca_write_in_suite_store.json")
    save(store, tmp)
    reloaded = load(tmp)
    check("save_load_hash_identical",
          store_hash(reloaded) == store_hash(store))
    check("save_load_canonical_identical",
          canonical(reloaded) == canonical(store))
    print("FINAL_STORE_SHA256 " + store_hash(store))
    print("FINAL_STORE_JSON " + canonical(store))

    # 9. Typed gaps index (P1), on fresh stores so the checks
    #    above keep their exact environment.
    #
    # 9a. A held encounter indexes its clause key, waiting on
    #     the encounter's own seq; indexing consumes no seq.
    store_g = new_store()
    s_passive = "The cup was broken by the man."
    g_rec1 = from_finder_results(store_g, s_passive,
                                 noun_finder.find(s_passive),
                                 verb_finder.find(s_passive))
    passive_key = ("clause::passive_geometry_unhandled_in_p0"
                   "::AUX_BE+PAST_PARTICIPLE")
    gaps_g = entry_view(store_g, SELF_KEY)["gaps"]
    check("held_encounter_indexes_passive_clause_key_no_extra_seq",
          isinstance(gaps_g, dict)
          and gaps_g.get(passive_key)
          == {"kind": "clause", "status": "OPEN",
              "first_seq": g_rec1["seq"],
              "waiting_encounters": [g_rec1["seq"]],
              "reason": "passive_or_auxiliary_out_of_scope"}
          and store_g["seq"] == g_rec1["seq"],
          repr(gaps_g))
    # 9b. The same held sentence again: a second waiter on the
    #     SAME entry, no duplicate entry.
    g_rec2 = from_finder_results(store_g, s_passive,
                                 noun_finder.find(s_passive),
                                 verb_finder.find(s_passive))
    gaps_g2 = entry_view(store_g, SELF_KEY)["gaps"]
    check("held_encounter_repeat_appends_waiter_no_duplicate",
          len(gaps_g2) == 1
          and gaps_g2[passive_key]["waiting_encounters"]
          == [g_rec1["seq"], g_rec2["seq"]]
          and gaps_g2[passive_key]["first_seq"] == g_rec1["seq"]
          and store_g["seq"] == g_rec2["seq"])
    # 9c. Wake-up flow: record_gap -> write_fill reports the
    #     wake -> close_gap closes it, history intact.
    store_w = new_store()
    w_gap = record_gap(store_w, "went", "past_of",
                       "form_entry_without_attested_base")
    w_entry = wake_up_for(store_w, "went", "past_of")
    w_out = write_fill(store_w, "went", "past_of", "go",
                       "dictionary_note")
    check("wake_up_reported_on_fill",
          w_entry is not None and w_entry["status"] == "OPEN"
          and w_out.get("gap_wake")
          == {"key": "slot::went::past_of",
              "waiting_encounters": [w_gap["seq"]]},
          repr(w_out))
    w_closed = close_gap(store_w, "slot::went::past_of")
    check("close_gap_closes_history_intact",
          w_closed is not None
          and w_closed["status"] == "CLOSED"
          and w_closed["waiting_encounters"] == [w_gap["seq"]]
          and w_closed["first_seq"] == w_gap["seq"]
          and gap_entry(store_w, "slot::went::past_of")["status"]
          == "CLOSED")
    # 9d. Legacy list-form gaps convert to the index on first
    #     gap access, nothing lost (same-slot records merge).
    store_l = new_store()
    store_l["entries"][SELF_KEY]["gaps"] = [
        {"seq": 1, "kind": "gap", "word": "went",
         "identifier": "past_of", "reason": "legacy_one"},
        {"seq": 2, "kind": "gap", "word": "went",
         "identifier": "past_of", "reason": "legacy_two"}]
    l_entry = gap_entry(store_l, "slot::went::past_of")
    check("legacy_list_gaps_convert_to_index",
          l_entry == {"kind": "slot", "status": "OPEN",
                      "first_seq": 1,
                      "waiting_encounters": [1, 2],
                      "reason": "legacy_one"}
          and isinstance(entry_view(store_l, SELF_KEY)["gaps"],
                         dict),
          repr(l_entry))
    # 9e. Composer bridge, end to end: compose_what_is HOLD ->
    #     bridge indexes slot::wolf::is_a OPEN -> two fills
    #     verify and report the wake -> close_gap -> CLOSED.
    import pmca_composer as composer
    store_cb = new_store()
    c_rec = composer.compose_what_is(store_cb, "wolf")
    c_bridge = record_composer_hold(store_cb, c_rec)
    c_gap = gap_entry(store_cb, "slot::wolf::is_a")
    check("composer_bridge_indexes_what_is_gap",
          c_rec["verdict"] == "HOLD"
          and c_bridge["gap_keys"] == ["slot::wolf::is_a"]
          and c_bridge["recorded"] == 1
          and c_bridge["seq"] == store_cb["seq"]
          and c_gap is not None and c_gap["kind"] == "slot"
          and c_gap["status"] == "OPEN",
          repr(c_bridge))
    write_fill(store_cb, "wolf", "is_a", "canine",
               "dictionary_note")
    # PROM-1 re-base — two verifier fills are one class and
    # no longer promote (that was C-1); the second
    # attestation is stamped with the dictionary door's
    # class through the shared engine, as a door would.
    c_out2 = cross_attest(store_cb, "wolf", "is_a", "canine",
                          "seed_exemplar")
    c_closed = close_gap(store_cb, "slot::wolf::is_a")
    check("composer_bridge_wake_then_close",
          c_out2["status"] == "verified"
          and c_out2.get("gap_wake", {}).get("key")
          == "slot::wolf::is_a"
          and c_closed is not None
          and c_closed["status"] == "CLOSED",
          repr(c_out2))

    passed = sum(1 for r in results if r)
    print("Passed %d of %d checks" % (passed, len(results)))
    print("ALL CHECKS PASS" if passed == len(results)
          else "SOME CHECKS FAILED")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(run_checks())
