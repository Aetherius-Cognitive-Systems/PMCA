# pmca_fixpack_fb.py
#
# PACK F+B FIXTURE MODULE (2026-10-26): the acceptance
# probes for the event-binding restructure (Part B) and
# the gaps / promotion / candidate-verdict fixes
# (Part F). Evidence: claims-13 (gaps), claims-05
# (composition), synthesis D3/D9/D13/D14, Argon report-2
# item 1 (binding contamination, confirmed by Alder's
# execution). Sealed expectations:
# hidden_files/fix-campaign-2026-10-26/
# pack-FB-expectations.md.
#
# Probes:
#   B1  Argon's two-sentence sequence: no stitching, no
#       cross-subject conflict, compose_who per pair.
#   B2  A third subject of the same verb composes.
#   B3  One subject, same verb, different target: the
#       genuine conflict — recorded, first value stands.
#   B4  Two subjects, same verb, same target: compose_who
#       names BOTH; no conflict anywhere.
#   B5  Legacy single-slot store: read fallback, then
#       upgrade-on-write rewrites scoped on encounter.
#   B6  Intransitive encounter: originates/originated_by
#       only; binding target None; no phantom acts_on.
#   F1  StoredStore compose_what_is records the typed gap
#       and writes through; seq reconciles.
#   F2  Every seq 1..N appears exactly once as a self
#       record across the seq-consuming operations.
#   F3  D13: same-provenance re-attestation never verifies;
#       distinct provenance verifies with the history
#       retained; promote() refuses below the threshold
#       and succeeds on a hand-built qualifying fact.
#       (PROM-1 re-base, 2026-10-29: "distinct" now means
#       distinct provenance CLASSES — the source field —
#       not distinct records; D13's evidence-string counting
#       was C-1. Fixtures below stamp a second door's class
#       where the pre-ruling fixture used a second evidence
#       string under the verifier class.)
#   F4  D14: CANDIDATE verdicts for candidate structure,
#       ANSWER only for verified.
#   F6  The stored_once two-axis validator still guards a
#       history-carrying store: accretion advances,
#       history shrinkage refused by name, layout
#       round-trips canonical-equal.
#
# Deterministic, stdlib only, no wall clock. Runs when this
# file is executed; imports nothing outside the tree.

import copy

import pmca_write_in as wi
import pmca_composer as comp
import pmca_population as pop
import pmca_noun_finder as noun_finder
import pmca_verb_finder as verb_finder


def _enc(store, sentence):
    return pop.encounter(store, sentence,
                         noun_finder.find(sentence),
                         verb_finder.find(sentence))


def _attest_as(store, word, identifier, value, source,
               evidence):
    """One attestation stamped with another door's provenance
    class, through the shared engine (pmca_write_in._attest)
    — the fixture form of a second door attesting the same
    fact (PROM-1: classes, not evidence strings, are what
    promotion counts; no public door writes is_a under a
    second class). Consumes one seq and appends exactly one
    self record, as a door does."""
    seq = wi._next_seq(store)
    out = wi._attest(store, word, identifier, value,
                     {"source": source, "evidence": evidence},
                     seq)
    wi._append_self_record(store, {
        "seq": seq, "kind": "cross_attestation",
        "outcome": out["outcome"], "word": word,
        "identifier": identifier})
    return out


def run_checks():
    # Same dictionary configuration the suites perform:
    # the finders consult the analyzer's dictionary handle.
    import os
    import pmca_sentence_analyzer as _analyzer
    _analyzer.configure_dictionary(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "webster1913.db"))
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond)))
        status = "PASS" if cond else "FAIL"
        print("%s: %s%s" % (status, name,
                             (" -> " + str(detail)) if detail
                             and not cond else ""))

    s_wolf = "The wolf eats the sheep."
    s_man = "The man eats the meat."
    s_goat = "The goat eats the grass."
    s_dog = "The dog eats the sheep."

    # ---- B1: Argon's sequence. ----
    st = wi.new_store()
    _enc(st, s_wolf)
    _enc(st, s_man)
    b_man = wi.binding_for(st, "man", "eat")
    b_wolf = wi.binding_for(st, "wolf", "eat")
    eat = wi.entry_view(st, "eat") or {}
    check("b1_binding_for_man_eat_is_meat",
          b_man is not None and b_man["target"] == "meat"
          and b_man["status"] == "candidate", repr(b_man))
    check("b1_binding_for_wolf_eat_is_sheep",
          b_wolf is not None and b_wolf["target"] == "sheep",
          repr(b_wolf))
    check("b1_no_conflict_from_second_subject",
          all(not (wi.entry_view(st, w) or {})
              .get("conflicts")
              for w in ("wolf", "man", "eat", "sheep", "meat"))
          and set(eat["subcoordinates"]) == {
              "originated_by::wolf", "acts_on::wolf",
              "originated_by::man", "acts_on::man"},
          repr(sorted(eat.get("subcoordinates", {}))))
    check("b1_compose_who_per_pair",
          comp.compose_who(st, "eat", "sheep")["slots"]["who"]
          == "wolf"
          and comp.compose_who(st, "eat", "meat")["slots"]
          ["who"] == "man")

    # ---- B2: third subject. ----
    _enc(st, s_goat)
    rec_goat = comp.compose_binding(st, "goat", "eat")
    check("b2_third_subject_composable",
          rec_goat["slots"]["who"] == "goat"
          and rec_goat["slots"]["what"]["target"] == "grass"
          and rec_goat["verdict"] == "CANDIDATE",
          repr(rec_goat.get("sentence")))

    # ---- B4 (before B3's conflict store): two subjects,
    # same target — compose_who names both. ----
    st4 = wi.new_store()
    _enc(st4, s_wolf)
    _enc(st4, s_dog)
    who_both = comp.compose_who(st4, "eat", "sheep")
    check("b4_compose_who_names_both_originators",
          who_both["slots"]["who"] == ["dog", "wolf"]
          and who_both["sentence"]
          == "Candidate (not yet verified): The dog and "
          "the wolf eats the sheep."
          and all(not (wi.entry_view(st4, w) or {})
                  .get("conflicts")
                  for w in ("wolf", "dog", "eat")),
          repr(who_both.get("sentence")))

    # ---- B3: genuine conflict, same subject+verb. ----
    st3 = wi.new_store()
    _enc(st3, s_wolf)
    _enc(st3, "The wolf eats the goat.")
    eat3 = wi.entry_view(st3, "eat") or {}
    wolf3 = wi.entry_view(st3, "wolf") or {}
    check("b3_same_subject_new_target_conflicts",
          eat3["subcoordinates"]["acts_on::wolf"]["value"]
          == "sheep"
          and len(eat3["conflicts"]) == 1
          and eat3["conflicts"][0]["identifier"]
          == "acts_on::wolf"
          and eat3["conflicts"][0]["attempted_value"]
          == "goat"
          and len(wolf3["conflicts"]) == 0
          and wi.binding_for(st3, "wolf", "eat")["target"]
          == "sheep")

    # ---- B5: legacy store: fallback read, upgrade on
    # write. ----
    legacy = wi.new_store()
    for w in ("wolf", "eat", "sheep"):
        pop.populate_word(legacy, w, "fixture")
    prov = {"source": "finders", "evidence": "finder_parse",
            "sentence": s_wolf}

    def legacy_fact(value):
        return {"value": value, "status": "candidate",
                "provenance": copy.deepcopy(prov),
                "attestations": 1, "attested_events": [1],
                "first_seq": 1, "last_seq": 1}

    legacy["entries"]["wolf"]["subcoordinates"]["originates"] \
        = legacy_fact("eat")
    legacy["entries"]["eat"]["subcoordinates"] \
        ["originated_by"] = legacy_fact("wolf")
    legacy["entries"]["eat"]["subcoordinates"]["acts_on"] \
        = legacy_fact("sheep")
    legacy["entries"]["sheep"]["subcoordinates"] \
        ["target_of"] = legacy_fact("eat")
    b_legacy = wi.binding_for(legacy, "wolf", "eat")
    who_legacy = comp.compose_who(legacy, "eat", "sheep")
    check("b5_legacy_store_reads_through_fallback",
          b_legacy is not None
          and b_legacy["target"] == "sheep"
          and who_legacy["slots"]["who"] == "wolf",
          repr(b_legacy))
    _enc(legacy, s_wolf)
    wolf_l = wi.entry_view(legacy, "wolf") or {}
    eat_l = wi.entry_view(legacy, "eat") or {}
    sheep_l = wi.entry_view(legacy, "sheep") or {}
    check("b5_upgrade_on_write_rewrites_scoped",
          "originates::eat" in wolf_l["subcoordinates"]
          and "originates" not in wolf_l["subcoordinates"]
          and "acts_on::wolf" in eat_l["subcoordinates"]
          and "acts_on" not in eat_l["subcoordinates"]
          and "originated_by" not in eat_l["subcoordinates"]
          and "target_of::eat" in sheep_l["subcoordinates"]
          and wi.binding_for(legacy, "wolf", "eat")["target"]
          == "sheep")

    # ---- B6: intransitive encounter (synthetic OK finder
    # results; the v1 finders do not emit intransitives). ----
    st6 = wi.new_store()
    fake_ok = {"verdict": "OK", "subject": "wolf",
               "verb": "sleeps", "target": None}
    rec6 = wi.from_finder_results(
        st6, "The wolf sleeps.", fake_ok, fake_ok)
    verb6 = wi.entry_view(st6, "sleeps") or {}
    b6 = wi.binding_for(st6, "wolf", "sleeps")
    check("b6_intransitive_scoped_no_phantom_target",
          rec6["outcome"] == "written"
          and set(verb6["subcoordinates"])
          == {"originated_by::wolf"}
          and b6 is not None and b6["target"] is None)

    # ---- F1: StoredStore records the composer HOLD. ----
    import tempfile
    import os
    from pmca_write_in_storage import StoredStore
    import pmca_write_in_storage as wis
    tmp = tempfile.mkdtemp(prefix="pmca_fb_f1_")
    path = os.path.join(tmp, "store.json")
    ss = StoredStore(path)
    rec = ss.compose_what_is("wolf")
    self_ss = ss.entry_view(wi.SELF_KEY) or {}
    gap = self_ss.get("gaps", {}).get("slot::wolf::is_a")
    on_disk = wis.load_store(path)
    check("f1_composer_hold_recorded_and_written_through",
          rec["verdict"] == "HOLD"
          and rec["gaps"] == ["kind: not stored"]
          and os.path.exists(path)
          and gap is not None and gap["status"] == "OPEN"
          and gap["waiting_encounters"] == [ss.store["seq"]]
          and on_disk["seq"] == ss.store["seq"]
          and "slot::wolf::is_a"
          in on_disk["entries"][wi.SELF_KEY]["gaps"],
          repr(gap))

    # ---- F2: seq reconciliation across operations.
    # Every seq-consuming operation (population placement,
    # encounter, write_fill, record_gap, promote) appends
    # exactly one self record carrying its own seq: the
    # self entry's record seqs are exactly 1..store.seq,
    # each once — no silent gaps. ----
    st2 = wi.new_store()
    for w in ("wolf", "eats", "sheep", "the"):
        pop.populate_word(st2, w, "fixture")
    _enc(st2, s_wolf)
    wi.write_fill(st2, "wolf", "is_a", "canine",
                  "dictionary_note")
    wi.record_gap(st2, "wolf", "past_of", "no_base")
    wi.write_fill(st2, "wolf", "is_a", "canine",
                  "seed_exemplar")
    # PROM-1: two verifier-class fills no longer qualify a
    # fact for promotion on their own; a second door's class
    # attests before promote() is called. The attestation
    # consumes one seq and appends exactly one self record,
    # so the reconciliation asserted below is unchanged.
    _attest_as(st2, "wolf", "is_a", "canine", "dictionary",
               "webster1913")
    prom = wi.promote(st2, "wolf", "is_a", "fixture")
    seqs = [r["seq"] for r in
            wi.entry_view(st2, wi.SELF_KEY)["encounters"]]
    check("f2_every_seq_has_exactly_one_self_record",
          prom is not None
          and prom.get("promoted_reason") == "fixture"
          and sorted(seqs) == list(range(1, st2["seq"] + 1))
          and len(seqs) == len(set(seqs)),
          repr(seqs))

    # ---- F3: D13 promotion semantics (PROM-1: counted in
    # provenance classes since 2026-10-29). ----
    stf = wi.new_store()
    wi.write_fill(stf, "cat", "is_a", "animal",
                  "dictionary_note")
    wi.write_fill(stf, "cat", "is_a", "animal",
                  "dictionary_note")
    cat = (wi.entry_view(stf, "cat") or {})["subcoordinates"] \
        ["is_a"]
    refusal = wi.promote(stf, "cat", "is_a", "too_early")
    # PROM-1 re-base: the qualifying second attestation is a
    # distinct CLASS (the dictionary door), not a second
    # evidence string under the verifier class.
    _attest_as(stf, "cat", "is_a", "animal", "dictionary",
               "seed_exemplar")
    cat2 = (wi.entry_view(stf, "cat") or {})["subcoordinates"] \
        ["is_a"]
    # Hand-built qualifying candidate: two distinct
    # provenance CLASSES (PROM-1), no recorded history key.
    hand = wi.new_store()
    pop.populate_word(hand, "owl", "fixture")
    hand["entries"]["owl"]["subcoordinates"]["is_a"] = {
        "value": "bird", "status": "candidate",
        "provenance": {"source": "verifier",
                       "evidence": "dictionary_note"},
        "provenance_history": [
            {"source": "verifier",
             "evidence": "dictionary_note"},
            {"source": "dictionary",
             "evidence": "seed_exemplar"}],
        "attestations": 2, "attested_events": [1, 2],
        "first_seq": 1, "last_seq": 2}
    promoted = wi.promote(hand, "owl", "is_a",
                          "two_distinct_on_record")
    check("f3_distinct_provenance_verifies_replay_does_not",
          cat["status"] == "candidate"
          and cat["attestations"] == 2
          and len(cat["provenance_history"]) == 1
          and refusal["outcome"] == "refused"
          and refusal["refusal"]
          == "promote_requires_two_distinct_provenances"
          and refusal["distinct_provenances"] == 1
          and cat2["status"] == "verified"
          and len(cat2["provenance_history"]) == 2
          and cat2["provenance_history"][0]["evidence"]
          == "dictionary_note"
          and cat2["provenance_history"][1]["evidence"]
          == "seed_exemplar"
          and promoted is not None
          and promoted["status"] == "verified"
          and promoted["promoted_reason"]
          == "two_distinct_on_record")

    # ---- F4: D14 candidate verdicts. ----
    stc = wi.new_store()
    wi.write_fill(stc, "wolf", "is_a", "canine",
                  "dictionary_note")
    cand = comp.compose_what_is(stc, "wolf")
    # PROM-1 re-base: the second attestation is the
    # dictionary door's class, not a verifier evidence
    # string (that was C-1).
    _attest_as(stc, "wolf", "is_a", "canine", "dictionary",
               "seed_exemplar")
    ver = comp.compose_what_is(stc, "wolf")
    check("f4_candidate_verdict_distinct_from_answer",
          cand["verdict"] == "CANDIDATE"
          and cand["sentence"] == "Candidate (not yet "
          "verified): A wolf is a canine."
          and ver["verdict"] == "ANSWER"
          and ver["sentence"] == "A wolf is a canine.",
          repr((cand["verdict"], ver["verdict"])))

    # ---- F6: stored_once guards a history-carrying
    # store. ----
    import pmca_stored_once as so
    layout_root = tempfile.mkdtemp(prefix="pmca_fb_f6_")
    built = wi.new_store()
    wi.write_fill(built, "wolf", "is_a", "canine",
                  "dictionary_note")
    # PROM-1 re-base: the built fact is verified by two
    # distinct classes (verifier + dictionary), not two
    # verifier evidence strings.
    _attest_as(built, "wolf", "is_a", "canine", "dictionary",
               "seed_exemplar")
    so.build_layout(built, layout_root)
    roundtrip = so.load_layout(layout_root)
    # Accretion: a third, again-distinct provenance.
    adv = copy.deepcopy(built)
    wi.write_fill(adv, "wolf", "is_a", "canine",
                  "third_source_note")
    adv_fact = adv["entries"]["wolf"]["subcoordinates"] \
        ["is_a"]
    rep = so.store_entry(layout_root, so.BASE_GRAPH, "wolf",
                         adv["entries"]["wolf"])
    # Shrinkage: the advanced fact with ONLY its history
    # truncated must refuse by the history name (attestation
    # counts are untouched, so no other guard can fire
    # first).
    shrunk = copy.deepcopy(adv["entries"]["wolf"])
    shrunk["subcoordinates"]["is_a"]["provenance_history"] \
        = shrunk["subcoordinates"]["is_a"][
            "provenance_history"][:1]
    err = None
    try:
        so.store_entry(layout_root, so.BASE_GRAPH, "wolf",
                       shrunk)
    except so.StoredOnceError as exc:
        err = str(exc)
    check("f6_history_accretion_guarded_and_roundtrips",
          wi.canonical(roundtrip) == wi.canonical(built)
          and len(roundtrip["entries"]["wolf"]
                  ["subcoordinates"]["is_a"]
                  ["provenance_history"]) == 2
          and rep["outcome"] == "advanced"
          and any(t.startswith(
              "provenance_history_appended:is_a")
              for t in rep["transitions"])
          and adv_fact["status"] == "verified"
          and err is not None
          and "provenance_history_not_append_only:is_a"
          in err,
          repr((rep.get("outcome"), err)))

    total = len(checks)
    passed = sum(1 for _, ok in checks if ok)
    print("Passed %d of %d checks" % (passed, total))
    if passed == total:
        print("ALL CHECKS PASS")
    else:
        print("SOME CHECKS FAILED")
    return passed == total


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_checks() else 1)
