#!/usr/bin/env python3
"""Build the is_a genus store (ISA-1/2/3, ruled 2026-10-28).

Run with the dictionary probe's wn venv python (provides `wn`/OEWN):
  .../dictionary-probe-2026-10-26/oewn/venv/bin/python build_isa.py

Feeds, both CANDIDATE-first, promotion only at >= 2 distinct
provenance LINEAGES (ISA-2):
  (a) the OEWN hypernym chain for wolf: wolf -> the one synset with
      lemmas [canine, canid] -> carnivore;
  (b) the book-ingest definition candidates, cross-checked against
      the dictionary sources under the strict membership rule
      sealed in expectations.md.
Deterministic: same inputs -> byte-identical genus_store.json.
Writes genus_store.json + build_log.json in this pack dir. Writes
nothing anywhere else.

IN-TREE COPY (2026-11-01, WG1 fix wave, 04 Forge): copied
verbatim from
~/workspace/goals/pmca-simulation/hidden_files/isa-build-2026-10-28/build_isa.py
(md5 c5647fbb710b4820af90415d66e0c2c6, sha256
c2fa02dd7c0b946faf1a2aabe3b708ffab53b455f41a55a23ebcb8cfbaca70a4;
the original outside the tree is untouched), then fixed in
the copy only: status_of reads the governed source->lineage
table (D-2/D-3); book provenance is carried from the
candidate's own ingested record and bound to it by full
record_id + sentence hash (M-1/M-2); genus filing uses the
candidate's own genus, with a recorded gap instead of the
animal bucket for a genus-less candidate (M-1).

WG1b completion (2026-11-01, M-2): the WG1 binding covered
the candidate's identity fields only — genus_key and the
sentence text were not in the record identity, so altering
either still bound (paired check, executed). The binding
now covers content as well:
  (1) pmca_book_ingest stamps each candidate at creation
      with a content_binding over {record_id, genus_key,
      sentence_sha256}; book_provenance recomputes it, so a
      genus_key flip or a sentence-text flip is rejected.
      (Candidates filed before the binding existed carry
      none: they bind by identity only, and the filed
      provenance records that state in its detail —
      "content_binding": "absent_legacy_identity_only" —
      never silently.)
  (2) In main(), each candidate's sentence is also
      cross-checked against the sentence_outcome record for
      the same (book_id, chapter, sentence_index) in the
      same store — the filed sentence hash is compared
      against the ingested record's stored hash, not merely
      computed at filing time.
  (3) Book-level binding: pmca_book_ingest creates a
      book_source record from the ingested text (identity =
      full-text hash); a caller-supplied book_id is an
      assertion on that record. book_source_registry /
      book_level_check below enforce it: a candidate whose
      asserted book_id is registered to a DIFFERENT source
      record than the one it carries HOLDs — decision
      rejected_book_assertion_conflict, recorded — and is
      filed under neither identity. RULE RELIED ON:
      HOLD-on-ambiguity (which of two texts the asserted id
      denotes is undecidable from the records, so nothing
      files).
"""
import csv
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

HOME = Path("/home/hatch")
TREE = HOME / "workspace/pmca_argon_test"
PACK = HOME / "workspace/goals/pmca-simulation/hidden_files/isa-build-2026-10-28"
PROBE = HOME / "workspace/goals/pmca-simulation/hidden_files/dictionary-probe-2026-10-26"
BOOK = HOME / "workspace/goals/pmca-simulation/hidden_files/book-ingest-2026-10-28"
WEBSTER_DB = HOME / "workspace/pmca/data/webster1913.db"

sys.path.insert(0, str(TREE))
from pmca_graphspace import dist_address  # noqa: E402
import pmca_dictionary  # noqa: E402
import pmca_lineage_table  # noqa: E402

pmca_dictionary.configure(str(TREE / "data" / "webster1913.db"))

GENUS_RES = [
    re.compile(r"belonging to the genus\s+\{?([A-Za-z]+)\}?"),
    re.compile(r"of the genus\s+\{?([A-Za-z]+)\}?"),
    re.compile(r"the genus\s+\{?([A-Za-z]+)\}?"),
]


def canon(obj):
    return json.dumps(obj, sort_keys=True, indent=1,
                      ensure_ascii=False) + "\n"


def coord(word):
    return list(dist_address(word))


def webster_defs(word):
    looked = pmca_dictionary.lookup(word)
    if not looked:
        return None, []
    return looked["headword"], looked["definitions"]


def genus_names_in(definitions):
    """Genus names attested by genus constructions in stored
    definition text (the spec's extraction constructions)."""
    found = []
    seen = set()
    for i, d in enumerate(definitions):
        for rx in GENUS_RES:
            for m in rx.finditer(d):
                key = (i, m.group(1))
                if key in seen:
                    continue  # one record per attested fact
                seen.add(key)
                found.append({"sense": i, "construction": m.group(0),
                              "genus_name": m.group(1)})
    return found


def status_of(provenances):
    """VERIFIED iff the provenances span >= 2 distinct
    provenance lineage CLASSES, read through the governed
    source->lineage table (Candidacy Rule Set C-3;
    pmca_lineage_table): spelling variants of one lineage
    are ONE class (Ledger D-2, fixed 2026-11-01), and a
    record with a missing lineage/source is NO class
    (Ledger D-3, fixed 2026-11-01; pre-fix this read
    p["lineage"] directly — one class per spelling, and a
    KeyError on a missing lineage)."""
    classes = set()
    for p in provenances or []:
        cls = pmca_lineage_table.class_for_record(
            p, context="pmca_build_isa.status_of")
        if cls is not None:
            classes.add(cls)
    return "VERIFIED" if len(classes) >= 2 else "CANDIDATE"


def _candidate_record_id(candidate):
    """Recompute a definition candidate's record_id from
    its identity fields, in pmca_book_ingest's canonical
    form (sha256 of the canonical identity {book_id,
    chapter, sentence_index, kind, term_key,
    construction}). The M-2 binding check: a candidate
    whose stored record_id does not recompute was asserted
    or altered after ingestion and is not bound to the
    ingested source."""
    prov = candidate.get("provenance") or {}
    identity = {"book_id": prov.get("book_id"),
                "chapter": prov.get("chapter"),
                "sentence_index": prov.get("sentence_index"),
                "kind": "definition_candidate",
                "term_key": candidate.get("term_key"),
                "construction": candidate.get("construction")}
    blob = json.dumps(identity, sort_keys=True,
                      separators=(",", ":"),
                      ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _candidate_content_binding(candidate):
    """Recompute a definition candidate's content binding
    from its presented fields, in pmca_book_ingest's
    canonical form: sha256 of the canonical {record_id,
    genus_key, sentence_sha256}, where sentence_sha256 is
    the sha256 of the candidate's sentence TEXT itself.
    This is the WG1b extension of the binding: genus_key
    and the sentence text are covered, so tampering with
    either breaks the recompute."""
    sentence = candidate.get("sentence") or ""
    identity = {
        "record_id": candidate.get("record_id"),
        "genus_key": candidate.get("genus_key"),
        "sentence_sha256": hashlib.sha256(
            sentence.encode("utf-8")).hexdigest()}
    blob = json.dumps(identity, sort_keys=True,
                      separators=(",", ":"),
                      ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def book_source_registry(records):
    """The book-level registry over a candidate store's
    records: asserted book_id -> the ingest-created
    book_source record of record for that id. The FIRST
    source record seen for an asserted id is the registered
    one; a later source record asserting the same id for a
    DIFFERENT text (different record_id) is a conflict and
    is returned in the conflicts list, recorded — the
    registry keeps the first, and candidates carrying the
    conflicting source id HOLD under book_level_check.
    Returns (registry, conflicts)."""
    registry = {}
    conflicts = []
    for rec in records or []:
        if not isinstance(rec, dict) \
                or rec.get("kind") != "book_source":
            continue
        asserted = rec.get("asserted_book_id")
        if asserted is None:
            asserted = (rec.get("provenance") or {}).get("book_id")
        if asserted is None:
            continue
        if asserted not in registry:
            registry[asserted] = rec
        elif registry[asserted].get("record_id") \
                != rec.get("record_id"):
            conflicts.append({
                "asserted_book_id": asserted,
                "registered_source_id":
                    registry[asserted].get("record_id"),
                "conflicting_source_id": rec.get("record_id"),
                "conflicting_text_sha256":
                    rec.get("text_sha256")})
    return registry, conflicts


def book_level_check(candidate, registry):
    """Check one candidate against the book-level registry.
    Returns (True, None) when the candidate's book binding
    is in order, or (False, reason) when it must HOLD:
      - the candidate carries a book_source_id, but no
        book_source record for its asserted book_id is
        registered (the source record it claims was not
        created by any ingest in this store);
      - the registered source record for its asserted
        book_id is a DIFFERENT record than the one it
        carries (assertion conflict: two texts claim one
        book_id — HOLD-on-ambiguity, filed under neither);
      - a source record IS registered for its asserted
        book_id but the candidate carries none (the
        candidate did not come from that ingest).
    A candidate carrying no book_source_id in a store with
    no registry entry for its id is the legacy shape
    (pre-WG1b stores): (True, None), bound by the
    record-level checks in book_provenance only."""
    prov = candidate.get("provenance") or {}
    asserted = prov.get("book_id")
    carried = prov.get("book_source_id")
    registered = (registry or {}).get(asserted)
    if carried is not None:
        if registered is None:
            return False, ("no book_source record registered "
                           "for asserted book_id %r "
                           "(book-level unbound)" % (asserted,))
        if registered.get("record_id") != carried:
            return False, ("book_id assertion conflict: "
                           "asserted book_id %r is registered "
                           "to a different ingest-created "
                           "source record (HOLD)"
                           % (asserted,))
        return True, None
    if registered is not None:
        return False, ("candidate carries no book_source_id "
                       "but a book_source record is "
                       "registered for its asserted book_id "
                       "(book-level unbound)")
    return True, None


def content_crosscheck(candidate, outcomes):
    """Cross-check a candidate's sentence against the
    sentence_outcome record ingested for the same
    (book_id, chapter, sentence_index): the candidate's
    sentence TEXT and its sha256 must equal the outcome
    record's stored sentence and sentence_sha256. Returns
    (True, None) or (False, reason). `outcomes` maps
    (book_id, chapter, sentence_index) -> outcome record."""
    prov = candidate.get("provenance") or {}
    key = (prov.get("book_id"), prov.get("chapter"),
           prov.get("sentence_index"))
    outcome = (outcomes or {}).get(key)
    if outcome is None:
        return False, ("no sentence_outcome record for the "
                       "candidate's (book_id, chapter, "
                       "sentence_index) in the store "
                       "(content unbound)")
    sentence = candidate.get("sentence") or ""
    if outcome.get("sentence") != sentence:
        return False, ("candidate sentence does not match "
                       "the ingested sentence_outcome record "
                       "(content unbound)")
    if outcome.get("sentence_sha256") != hashlib.sha256(
            sentence.encode("utf-8")).hexdigest():
        return False, ("candidate sentence hash does not "
                       "match the sentence_outcome record's "
                       "stored hash (content unbound)")
    return True, None


def book_provenance(candidate):
    """The genus-filing provenance for one book candidate,
    CARRIED from the candidate's own ingested record
    (M-1/M-2, fixed 2026-11-01) — never re-stamped: source
    is the book's own recorded title, lineage is
    "book:" + its own book_id. Returns (provenance, None),
    or (None, gap_reason) when the candidate cannot be
    bound to its source: no recorded book provenance, or a
    stored record_id that does not recompute from the
    candidate's identity (a caller-asserted book_id in
    conflict with the bound source is rejected, and the
    caller records the rejection as a gap decision — the
    asserted value is never carried). The bound record
    carries the FULL record_id (pre-fix the join key was
    a 12-char truncation, Atlas T-5) and the sha256 of the
    ingested sentence text."""
    prov_in = candidate.get("provenance")
    if not isinstance(prov_in, dict) \
            or not prov_in.get("book_id") \
            or not prov_in.get("title"):
        return None, "candidate carries no book provenance"
    if candidate.get("record_id") != _candidate_record_id(candidate):
        return None, ("record_id does not recompute from the "
                      "candidate's identity "
                      "(provenance unbound)")
    # WG1b content binding: when the candidate carries the
    # binding pmca_book_ingest stamps at creation, it must
    # recompute over the presented record_id + genus_key +
    # sentence text — a genus_key flip or a sentence-text
    # flip is rejected here. A candidate with no binding is
    # a pre-WG1b (legacy) record: it binds by identity only
    # and the filed detail records that state, never
    # silently.
    binding = candidate.get("content_binding")
    if binding is not None:
        if binding != _candidate_content_binding(candidate):
            return None, ("content_binding does not "
                          "recompute from the candidate's "
                          "record_id, genus_key and sentence "
                          "text (content unbound)")
        binding_state = "verified"
    else:
        binding_state = "absent_legacy_identity_only"
    sentence = candidate.get("sentence") or ""
    prov = {
        "source": prov_in["title"],
        "lineage": "book:" + prov_in["book_id"],
        "license": prov_in.get("license"),
        "kind": "definition_candidate",
        "detail": {
            "construction": candidate.get("construction"),
            "genus_phrase": candidate.get("genus_key"),
            "record_id": candidate.get("record_id"),
            "sentence_sha256": hashlib.sha256(
                sentence.encode("utf-8")).hexdigest(),
            "content_binding": binding_state,
            "book_id": prov_in["book_id"],
            "asserted_book_id": prov_in["book_id"],
            "book_source_id": prov_in.get("book_source_id"),
            "author": prov_in.get("author"),
            "source_url": prov_in.get("source_url"),
            "chapter": prov_in.get("chapter"),
            "sentence_index": prov_in.get("sentence_index"),
            "char_offset": prov_in.get("char_offset")}}
    return prov, None


def genus_decision(candidate):
    """The genus a candidate files under: its OWN recorded
    genus_key (M-1). An empty or missing genus yields None:
    the caller records a gap decision, and the candidate
    is never filed under a default genus."""
    genus = candidate.get("genus_key")
    if isinstance(genus, str) and genus.strip():
        return genus
    return None


# ---------------------------------------------------------------- sources

def load_headword_sets():
    db = sqlite3.connect(f"file:{WEBSTER_DB}?mode=ro", uri=True)
    webster = {r[0].casefold()
               for r in db.execute("SELECT word FROM defs")}
    db.close()
    ecdict = set()
    with open(PROBE / "ecdict" / "ecdict.csv", newline="",
              encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ecdict.add(row["word"].casefold())
    return webster, ecdict


def attested_headword(phrase, webster, ecdict, wn):
    """Dictionary attestation of a recorded phrase AS a headword:
    exact casefold membership in a source's headword set, or an
    OEWN word form. No fuzzy join."""
    cf = phrase.casefold()
    if cf in webster or cf in ecdict:
        return True
    return bool(wn.words(form=phrase)) or bool(wn.words(form=cf))


# ---------------------------------------------------------------- build

def main():
    import wn
    webster, ecdict = load_headword_sets()
    log = {"feeds": {}, "decisions": []}
    genera = {}
    identifiers = {}
    seq = [0]

    def mint(forms, coordinate):
        seq[0] += 1
        gid = "gid-%04d" % seq[0]
        identifiers[gid] = {"forms": forms, "coordinate": coordinate}
        return gid

    def add_genus(forms, definition, alternates, coordinate_word):
        gid = mint(forms, coord(coordinate_word))
        genera[gid] = {
            "id": gid, "forms": forms,
            "coordinate": coord(coordinate_word),
            "definition": definition,
            "definition_attestations": alternates,
            "members": {},
        }
        return gid

    def webster_definition(forms, gloss_alt):
        """Sealed rule: Webster sense-1 of the first form (in
        stored order) that is a Webster headword; else the OEWN
        gloss. Returns (definition_record, alternates)."""
        for form in forms:
            head, defs = webster_defs(form)
            if defs:
                primary = {
                    "text": defs[0],
                    "provenance": {
                        "source": "Webster 1913",
                        "lineage": "Webster",
                        "headword": head,
                        "license": "Public domain"}}
                alts = []
                if gloss_alt:
                    alts.append(gloss_alt)
                return primary, alts
        return gloss_alt, []

    # ---- feed (a): OEWN hypernym chain for wolf ----------------
    wolf_syn = None
    for s in wn.synsets("wolf", pos="n"):
        for h in s.hypernyms():
            if {"canine", "canid"} <= {l.casefold()
                                       for l in h.lemmas()}:
                wolf_syn, h1 = s, h
    assert wolf_syn is not None, "ISA-1 synset not found for wolf"
    h2 = None
    for h in h1.hypernyms():
        if "carnivore" in {l.casefold() for l in h.lemmas()}:
            h2 = h
    assert h2 is not None, "carnivore hypernym not found"

    # The genus name Webster attests for this entry: the name in
    # wolf's own stored definition's genus construction, which is
    # also a Webster headword ("canis").
    _, wolf_defs = webster_defs("wolf")
    wolf_genus_names = genus_names_in(wolf_defs)
    canis_form = None
    for hit in wolf_genus_names:
        if hit["genus_name"].casefold() in webster:
            canis_form = hit["genus_name"]
    forms1 = ([canis_form] if canis_form else []) + list(h1.lemmas())
    gloss1 = {"text": h1.definition(),
              "provenance": {"source": "Open English WordNet",
                             "lineage": "WordNet",
                             "synset": h1.id,
                             "license": "CC BY 4.0"}}
    def1, alts1 = webster_definition(forms1, gloss1)
    g1 = add_genus(forms1, def1, alts1, forms1[0].casefold())
    log["feeds"]["oewn_chain"] = {
        "wolf_synset": wolf_syn.id, "genus_synset": h1.id,
        "genus_lemmas": list(h1.lemmas()), "upper_synset": h2.id,
        "upper_lemmas": list(h2.lemmas()),
        "webster_genus_names_in_wolf": wolf_genus_names}

    wolf_provs = [
        {"source": "Open English WordNet", "lineage": "WordNet",
         "license": "CC BY 4.0", "kind": "hypernym_edge",
         "detail": {"from_synset": wolf_syn.id,
                    "to_synset": h1.id, "member_lemma": "wolf",
                    "genus_lemmas": list(h1.lemmas())}},
    ]
    for hit in wolf_genus_names:
        if hit["genus_name"].casefold() in \
                {f.casefold() for f in forms1}:
            wolf_provs.append({
                "source": "Webster 1913", "lineage": "Webster",
                "license": "Public domain",
                "kind": "definition_genus_construction",
                "detail": {"headword": "wolf",
                           "sense": hit["sense"],
                           "construction": hit["construction"],
                           "genus_name": hit["genus_name"]}})
    genera[g1]["members"]["wolf"] = {
        "member_kind": "word", "word": "wolf",
        "coordinate": coord("wolf"),
        "status": status_of(wolf_provs), "provenances": wolf_provs}

    gloss2 = {"text": h2.definition(),
              "provenance": {"source": "Open English WordNet",
                             "lineage": "WordNet",
                             "synset": h2.id,
                             "license": "CC BY 4.0"}}
    def2, alts2 = webster_definition(list(h2.lemmas()), gloss2)
    g2 = add_genus(list(h2.lemmas()), def2, alts2,
                   list(h2.lemmas())[0].casefold())
    up_provs = [
        {"source": "Open English WordNet", "lineage": "WordNet",
         "license": "CC BY 4.0", "kind": "hypernym_edge",
         "detail": {"from_synset": h1.id, "to_synset": h2.id,
                    "member_lemma": list(h1.lemmas())[0],
                    "genus_lemmas": list(h2.lemmas())}}]
    genera[g2]["members"]["genus:" + g1] = {
        "member_kind": "genus", "genus": g1,
        "coordinate": genera[g1]["coordinate"],
        "status": status_of(up_provs), "provenances": up_provs}

    # ---- feed (b): book-ingest definition candidates -----------
    all_records = []
    candidates = []
    for line in open(BOOK / "book_ingest_store.jsonl",
                     encoding="utf-8"):
        r = json.loads(line)
        all_records.append(r)
        if r.get("kind") == "definition_candidate":
            candidates.append(r)
    # WG1b book-level + content cross-checks: the registry
    # of ingest-created book_source records (assertion
    # conflicts are recorded in the build log as decisions),
    # and the sentence_outcome map each candidate's sentence
    # is cross-checked against before filing.
    registry, reg_conflicts = book_source_registry(all_records)
    for conflict in reg_conflicts:
        log["decisions"].append({
            "term": conflict["asserted_book_id"],
            "genus_phrase": "",
            "decision": "book_assertion_conflict",
            "reason": ("asserted book_id registered to source "
                       "record %s; a different source record "
                       "%s asserts the same id (HOLD)"
                       % (conflict["registered_source_id"],
                          conflict["conflicting_source_id"])),
            "detail": conflict})
    outcomes = {}
    for r in all_records:
        if r.get("kind") == "sentence_outcome":
            rp = r.get("provenance") or {}
            outcomes[(rp.get("book_id"), rp.get("chapter"),
                      rp.get("sentence_index"))] = r
    if not outcomes:
        log["decisions"].append({
            "term": "", "genus_phrase": "",
            "decision": "content_crosscheck_unavailable",
            "reason": "the store carries no sentence_outcome "
                      "records; candidate sentences are "
                      "bound by content_binding only"})
    genus_gids = {}
    filed_pairs = set()
    for c in candidates:
        term, genus_phrase = c["term_key"], c["genus_key"]
        dec = {"term": term, "genus_phrase": genus_phrase,
               "construction": c["construction"]}
        term_ok = attested_headword(term, webster, ecdict, wn)
        genus_ok = attested_headword(genus_phrase, webster, ecdict,
                                     wn)
        dec["term_attested"] = term_ok
        dec["genus_phrase_attested"] = genus_ok
        if not (term_ok and genus_ok):
            dec["decision"] = "rejected_no_referral"
            dec["reason"] = (
                "term not a dictionary headword" if not term_ok
                else "genus phrase not a dictionary headword")
            log["decisions"].append(dec)
            continue
        # M-1: the candidate files under its OWN genus; a
        # candidate with no genus is a recorded gap, never
        # the animal bucket. M-2: its provenance is carried
        # from its own ingested record and must bind to it
        # (book_provenance); an unbound candidate is
        # rejected and recorded, never filed.
        genus_phrase = genus_decision(c)
        if genus_phrase is None:
            dec["decision"] = "rejected_no_genus"
            dec["reason"] = ("candidate carries no genus; "
                             "recorded as a gap, never filed "
                             "under a default genus")
            log["decisions"].append(dec)
            continue
        book_prov, bind_gap = book_provenance(c)
        if book_prov is None:
            dec["decision"] = "rejected_provenance_unbound"
            dec["reason"] = bind_gap
            log["decisions"].append(dec)
            continue
        # WG1b: book-level binding (asserted book_id vs the
        # ingest-created source record) and the sentence
        # cross-check against the store's sentence_outcome
        # record. Either failure HOLDs the candidate,
        # recorded, never filed.
        level_ok, level_reason = book_level_check(c, registry)
        if not level_ok:
            dec["decision"] = "rejected_book_assertion_conflict"
            dec["reason"] = level_reason
            log["decisions"].append(dec)
            continue
        if outcomes:
            content_ok, content_reason = content_crosscheck(
                c, outcomes)
            if not content_ok:
                dec["decision"] = "rejected_content_unbound"
                dec["reason"] = content_reason
                log["decisions"].append(dec)
                continue
        if (term, genus_phrase) in filed_pairs:
            dec["decision"] = "rejected_duplicate_pair"
            dec["reason"] = "stored-once: pair already filed"
            log["decisions"].append(dec)
            continue
        filed_pairs.add((term, genus_phrase))
        # genus entry for the candidate's own phrase
        # (placed on first filing for that phrase)
        gid = genus_gids.get(genus_phrase)
        if gid is None:
            gloss = None
            syns = wn.synsets(genus_phrase, pos="n")
            if syns:
                gloss = {
                    "text": syns[0].definition(),
                    "provenance": {
                        "source": "Open English WordNet",
                        "lineage": "WordNet",
                        "synset": syns[0].id,
                        "license": "CC BY 4.0"}}
            defg, altsg = webster_definition([genus_phrase],
                                             gloss)
            gid = add_genus([genus_phrase], defg, altsg,
                            genus_phrase)
            genus_gids[genus_phrase] = gid
            if defg is None:
                log["decisions"].append({
                    "term": genus_phrase,
                    "decision": "genus_definition_gap",
                    "reason": "no Webster definition and no "
                              "OEWN gloss for the genus "
                              "phrase; genus entry filed "
                              "with no stored definition"})
        provs = [book_prov]
        # strict membership cross-checks (sealed rule)
        genus = genera[gid]
        form_set = {f.casefold() for f in genus["forms"]}
        _, term_defs = webster_defs(term)
        names = genus_names_in(term_defs)
        check_a = any(h["genus_name"].casefold() in form_set
                      for h in names)
        hyp_lemma_sets = []
        check_b = False
        for s in wn.synsets(term, pos="n"):
            for h in s.hypernyms():
                lems = {l.casefold() for l in h.lemmas()}
                hyp_lemma_sets.append(sorted(lems))
                if lems & form_set:
                    check_b = True
        dec["membership_checks"] = {
            "webster_genus_construction": {
                "names_found": names, "attests": check_a},
            "oewn_direct_hypernym": {
                "direct_hypernym_lemma_sets": hyp_lemma_sets,
                "attests": check_b}}
        if check_a:
            provs.append({
                "source": "Webster 1913", "lineage": "Webster",
                "license": "Public domain",
                "kind": "definition_genus_construction",
                "detail": {"headword": term,
                           "names": names}})
        if check_b:
            provs.append({
                "source": "Open English WordNet",
                "lineage": "WordNet", "license": "CC BY 4.0",
                "kind": "hypernym_edge",
                "detail": {"term": term,
                           "genus_forms": genus["forms"]}})
        genera[gid]["members"][term] = {
            "member_kind": "word", "word": term,
            "coordinate": coord(term),
            "status": status_of(provs), "provenances": provs}
        dec["decision"] = "referral_filed"
        dec["genus_id"] = gid
        dec["status"] = status_of(provs)
        log["decisions"].append(dec)
    log["feeds"]["book_candidates"] = len(candidates)

    store = {"schema": "pmca_genus_store/v1",
             "rulings": ["ISA-1", "ISA-2", "ISA-3"],
             "identifiers": identifiers, "genera": genera}

    # self-audit: promotion discipline over the whole store
    for gid, genus in genera.items():
        for key, ref in genus["members"].items():
            assert ref["status"] == status_of(ref["provenances"])
            assert ref["status"] != "VERIFIED" or len(
                pmca_lineage_table.provenance_classes(
                    ref["provenances"],
                    context="pmca_build_isa.self_audit")) >= 2

    (PACK / "genus_store.json").write_text(canon(store),
                                           encoding="utf-8")
    (PACK / "build_log.json").write_text(canon(log),
                                         encoding="utf-8")
    print(json.dumps({
        "genera": {g: genera[g]["forms"] for g in genera},
        "members": {g: {k: v["status"] for k, v in
                        genera[g]["members"].items()}
                    for g in genera},
        "decisions": [(d["term"], d["genus_phrase"],
                       d["decision"]) for d in log["decisions"]],
    }, indent=1))


if __name__ == "__main__":
    main()
