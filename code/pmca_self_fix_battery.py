"""pmca_self_fix_battery — the standing battery for the
self-fix loop (spec section 7 + the three Protogen derivations;
build WG3, 04 Forge, 2026-11-01).

Ships WITH the build, standing, like the false-lock battery
under DICT-1 Part 2. Run: python3 pmca_self_fix_battery.py
[--results <path>] — exits 0 iff every case passes; per-case
results are printed and, with --results, written as JSON.

Cases: seeded closable failures (taught path + builder-fix
derivation path); false-remedy traps (fail replay, HOLD,
attempt recorded; 2 failed attempts suspend, no third silent
attempt); near-match traps (candidate cause named, nothing
applied); NEVER-CLASS TRAPS (a "remedy" for a provenance
counter refused at step 3 regardless of provenance — one with
impeccable provenance included; registration and scope fences);
conflict case (HOLD, never first-match); rot case (suspension
+ closure listing, closures stand); section 4A aggregation
(counts, struggled marks, prerequisite-gap record, verdict
non-influence); outcome-record fences (an outcome cannot
amend its sealed entry; resolution is replay only, verdict
on both records); certify-once (re-submission not
re-adjudicated; NC-1 across a generation change; self-
agreement certification refused); system-proposal fence;
single-writer lock; registration hook.

DISCIPLINE: every store, world, and atomic root is a TEMP
COPY under a fresh mkdtemp — the battery never mutates the
live tree's stores.
"""

import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pmca_atomic_entries as ae
import pmca_defect_store as ds
import pmca_persistence
import pmca_self_fix as sf

_PASSED = []
_FAILED = []
RESULTS = []


def check(case_id, name, cond, detail=None):
    ok = bool(cond)
    (_PASSED if ok else _FAILED).append(case_id)
    RESULTS.append({"case": case_id, "name": name,
                    "result": "PASS" if ok else "FAIL",
                    "detail": detail})
    print("%s %s %s%s" % ("PASS" if ok else "FAIL", case_id,
                          name,
                          "" if detail is None else
                          " -> %r" % (detail,)))


def make_replay(counter):
    def replay(world, case):
        counter["calls"] += 1
        entry = (world.get("entries") or {}).get(case["entry"])
        completed = entry is not None and all(
            entry.get(field) == value
            for field, value in case["requires"].items())
        return {"completed": completed,
                "verdict": "COMPLETE" if completed else "HOLD"}
    return replay


def make_env(root, generation="gen-wg3"):
    store = ds.DefectStore(os.path.join(root, "defect"))
    counter = {"calls": 0}
    loop = sf.SelfFixLoop(
        store, atomic_root=os.path.join(root, "atomic_root"),
        replay_fn=make_replay(counter),
        regression_fns={
            "content_surface": lambda world: {
                "ok": (world.get("entries") or {}).get(
                    "sentinel", {}).get("definition") == "seed"}},
        calculus_generation=generation)
    return store, loop, counter


def base_world():
    return {"entries": {
        "sentinel": {"definition": "seed"},
        "wolf": {"definition": None, "genus": "canis"},
        "fox": {"definition": None, "genus": "vulpes"},
        "cat": {"definition": None, "genus": "felis"},
        "bear": {"definition": None, "genus": "ursus"},
        "lynx": {"definition": None, "genus": "lynx"},
    }}


SIG_WOLF = {"process": "population", "gap_kind": "empty_slot",
            "missing_item": "definition:wolf",
            "rule_fired": "empty_slot_reflex"}
CASE_WOLF = {"entry": "wolf",
             "requires": {"definition": "a canine mammal"}}
SENTINEL_CASE = {"entry": "sentinel",
                 "requires": {"definition": "seed"}}


def scenario_a(root):
    """Seeded closable failure, teaching path."""
    store, loop, counter = make_env(root)
    world = base_world()
    cases = {"orig_wolf": CASE_WOLF, "sentinel_case": SENTINEL_CASE}
    failure = loop.record_failure(
        "ENT-seed-wolf", SIG_WOLF, "population reflex",
        case_name="orig_wolf")
    before_entry = copy.deepcopy(world["entries"]["wolf"])
    taught = loop.teach_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"}],
        teacher="battery_teacher", claim_id="WG3-TEACH-1",
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"],
        world=world, cases=cases, failure_id=failure["failure_id"])
    proof = taught["proof"] or {}
    check("WG3-01", "seeded_closable_teaching_closes",
          proof.get("outcome") == "CLOSED", proof.get("outcome"))
    check("WG3-02", "seeded_closable_world_filled",
          world["entries"]["wolf"]["definition"]
          == "a canine mammal")
    rec = store.get_failure(failure["failure_id"])
    check("WG3-03", "failure_closed_by_remedy_with_seal_ref",
          rec["status"] == ds.FAILURE_CLOSED_BY_REMEDY
          and rec["closure"]["remedy_id"] == taught["remedy_id"]
          and bool(rec["closure"]["seal_entry_id"]),
          rec["closure"])
    check("WG3-04", "taught_remedy_verified_by_replay_not_assertion",
          taught["status"] == ds.REMEDY_VERIFIED
          and taught["assertion_is_provenance_only"] is True
          and taught["verdict_of_record"] == "CLOSED",
          taught["status"])
    # Sealed atomic entry: reopen and verify.
    astore = ae.EntryStore.open(loop.atomic_root, read_only=True)
    entries = astore.replay()
    check("WG3-05", "seal_one_atomic_entry_chain_verifies",
          len(entries) == 1 and astore.verify_chain() is True,
          len(entries))
    entry = entries[0]
    outcome = entry["outcome"]
    check("WG3-06", "seal_carries_pre_post_hashes_affected_only",
          set(outcome["pre_hashes"]) == {"wolf"}
          and set(outcome["post_hashes"]) == {"wolf"}
          and outcome["pre_hashes"]["wolf"]
          == ds.content_hash(before_entry)
          and outcome["post_hashes"]["wolf"]
          == ds.content_hash(world["entries"]["wolf"])
          and outcome["pre_hashes"]["wolf"]
          != outcome["post_hashes"]["wolf"])
    check("WG3-07", "seal_entry_carries_failure_remedy_operations",
          entry["input"]["failure_id"] == failure["failure_id"]
          and entry["input"]["remedy_id"] == taught["remedy_id"]
          and entry["input"]["operations"]
          and any(s["kind"] == "seal_hashes"
                  for s in entry["steps"]))
    astore.close()
    certified = store.find_certified(failure_id=failure["failure_id"])
    check("WG3-08", "certified_registry_records_repair_once",
          len(certified) == 1
          and certified[0]["certified_once"] is True
          and certified[0]["basis"] == "cross_check_replay")
    return store, loop, counter, world, cases, failure, taught


def scenario_b(root, ctx):
    """Builder-fix derivation path + scope creep fence."""
    store, loop, counter, world, cases, _f, taught = ctx
    sig_fox = {"process": "population", "gap_kind": "empty_slot",
               "missing_item": "definition:fox",
               "rule_fired": "empty_slot_reflex"}
    cases["orig_fox"] = {"entry": "fox",
                         "requires": {"definition": "a canine mammal"}}
    failure = loop.record_failure("ENT-seed-fox", sig_fox,
                                  "population reflex",
                                  case_name="orig_fox")
    derived = loop.derive_remedy(
        taught["remedy_id"], sig_fox,
        [{"op": "derive", "entry": "fox", "field": "definition",
          "from_field": "genus_definition"}],
        fix_claim_id="WG3-FIX-1",
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"])
    world["entries"]["fox"]["genus_definition"] = "a canine mammal"
    check("WG3-09", "derived_remedy_candidate_until_first_proof",
          derived["status"] == ds.REMEDY_CANDIDATE)
    result = loop.run_once(failure["failure_id"], world, cases=cases)
    check("WG3-10", "derived_remedy_closes_on_first_live_proof",
          result.get("outcome") == "CLOSED"
          and store.get_remedy(derived["remedy_id"])["status"]
          == ds.REMEDY_VERIFIED, result.get("outcome"))
    raised = False
    try:
        loop.derive_remedy(
            taught["remedy_id"], sig_fox,
            [{"op": "fill", "entry": "fox", "field": "x",
              "value": 1}],
            fix_claim_id="WG3-FIX-2",
            scope_surfaces=["content_surface", "extra_surface"])
    except ds.DefectStoreError:
        raised = True
    check("WG3-11", "derived_scope_wider_than_parent_refused",
          raised)


def scenario_c(root):
    """False-remedy trap + remedy-loop suspension."""
    store, loop, counter = make_env(root)
    world = base_world()
    sig_cat = {"process": "population", "gap_kind": "empty_slot",
               "missing_item": "definition:cat",
               "rule_fired": "empty_slot_reflex"}
    cases = {"orig_cat": {"entry": "cat", "requires":
                          {"definition": "a feline mammal"}},
             "sentinel_case": SENTINEL_CASE}
    failure = loop.record_failure("ENT-seed-cat", sig_cat,
                                  "population reflex",
                                  case_name="orig_cat")
    taught = loop.teach_remedy(
        sig_cat,
        [{"op": "fill", "entry": "cat", "field": "definition",
          "value": "a canine mammal"}],
        teacher="battery_teacher", claim_id="WG3-TEACH-2",
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"],
        world=world, cases=cases, failure_id=failure["failure_id"])
    check("WG3-12", "false_remedy_fails_replay_hold_stands",
          (taught["proof"] or {}).get("outcome") == "REPLAY_FAILED"
          and taught["verdict_of_record"] == "REPLAY_FAILED",
          (taught["proof"] or {}).get("outcome"))
    rec = store.get_failure(failure["failure_id"])
    check("WG3-13", "false_remedy_attempt_recorded_failure_open",
          rec["status"] == ds.FAILURE_OPEN
          and len(rec["attempts"]) == 1
          and rec["attempts"][0]["outcome"] == "replay_failed"
          and world["entries"]["cat"]["definition"] is None)
    check("WG3-14", "false_remedy_stays_candidate",
          store.get_remedy(taught["remedy_id"])["status"]
          == ds.REMEDY_CANDIDATE)
    second = loop.run_once(failure["failure_id"], world, cases=cases)
    rec = store.get_failure(failure["failure_id"])
    remedy = store.get_remedy(taught["remedy_id"])
    check("WG3-15", "second_failed_attempt_suspends_remedy",
          second.get("outcome") == "REPLAY_FAILED"
          and second.get("remedy_suspended") is True
          and len(rec["attempts"]) == 2
          and remedy["suspended"] is True
          and remedy["status"] == ds.REMEDY_CANDIDATE,
          second.get("outcome"))
    third = loop.run_once(failure["failure_id"], world, cases=cases)
    rec = store.get_failure(failure["failure_id"])
    check("WG3-16", "no_third_silent_attempt",
          third.get("outcome") == "REFUSED_SUSPENDED"
          and len(rec["attempts"]) == 2
          and world["entries"]["cat"]["definition"] is None,
          third.get("outcome"))


def scenario_d(root, ctx):
    """Near-match and no-match traps."""
    store, loop, counter, world, cases, _f, _t = ctx
    sig_near = {"process": "population", "gap_kind": "empty_slot",
                "missing_item": "definition:wolf",
                "rule_fired": "dictionary_gap_v2"}
    failure = loop.record_failure("ENT-near-wolf", sig_near,
                                  "dictionary loop",
                                  case_name="orig_wolf")
    snapshot = copy.deepcopy(world)
    result = loop.run_once(failure["failure_id"], world, cases=cases)
    rec = store.get_failure(failure["failure_id"])
    check("WG3-17", "near_match_candidate_cause_named_nothing_applied",
          result.get("outcome") == "HOLD_NEAR_MATCH"
          and result.get("applied") is False
          and len(rec["candidate_causes"]) >= 1
          and all(c.get("grade") == "CANDIDATE"
                  for c in rec["candidate_causes"])
          and world == snapshot, result.get("outcome"))
    sig_none = {"process": "population", "gap_kind": "empty_slot",
                "missing_item": "definition:quokka",
                "rule_fired": "empty_slot_reflex"}
    failure2 = loop.record_failure("ENT-none", sig_none,
                                   "population reflex")
    result2 = loop.run_once(failure2["failure_id"], world,
                            cases=cases)
    check("WG3-18", "no_match_hold_stands_open",
          result2.get("outcome") == "HOLD_NO_MATCH"
          and store.get_failure(failure2["failure_id"])["status"]
          == ds.FAILURE_OPEN, result2.get("outcome"))


def scenario_e(root):
    """Never-class traps."""
    store, loop, counter = make_env(root)
    world = base_world()
    raised = False
    try:
        store.add_remedy(
            {"process": "provenance_counting",
             "gap_kind": "counter_error",
             "missing_item": "provenance_counter:write_in",
             "rule_fired": "prom1"},
            [{"op": "fill", "entry": "wolf", "field": "x",
              "value": 1}],
            provenance={"source_kind": "builder_fix",
                        "claim_id": "WG3-FIX-NC"})
    except ds.NeverClassRefused:
        raised = True
    check("WG3-19", "never_class_remedy_registration_refused",
          raised)
    raised = False
    try:
        store.add_remedy(
            SIG_WOLF,
            [{"op": "fill", "entry": "wolf", "field": "x",
              "value": 1}],
            provenance={"source_kind": "teaching",
                        "teacher": "battery_teacher"},
            scope_surfaces=["harm_calculus"])
    except ds.NeverClassRefused:
        raised = True
    check("WG3-20", "never_class_scope_unlistable", raised)
    # Loop-level trap with IMPECCABLE provenance.
    sig_nc = {"process": "provenance_counting",
              "gap_kind": "counter_error",
              "missing_item": "provenance_counter:write_in",
              "rule_fired": "prom1"}
    failure = loop.record_failure("ENT-nc", sig_nc,
                                  "write-in promotion counter")
    offered = {
        "remedy_id": "REM-OFFERED-NC",
        "signature": sig_nc,
        "operations": [{"op": "fill", "entry": "wolf",
                        "field": "definition", "value": "rigged"}],
        "provenance": {"source_kind": "builder_fix",
                       "claim_id": "WG3-IMPECCABLE-1",
                       "teacher": "the builder himself"},
        "replay_set": {"battery_cases": []},
        "scope_surfaces": [], "status": "verified",
        "suspended": False}
    snapshot = copy.deepcopy(world)
    result = loop.run_once(failure["failure_id"], world,
                           offered_remedy=offered)
    rec = store.get_failure(failure["failure_id"])
    check("WG3-21", "never_class_refused_at_step3_impeccable_provenance",
          result.get("outcome") == "NEVER_CLASS_REFUSED"
          and result.get("step_reached") == 3
          and rec["status"] == ds.FAILURE_NEVER_CLASS
          and world == snapshot, result.get("outcome"))
    sig_nc2 = {"process": "isa", "gap_kind": "promotion_error",
               "missing_item": "lineage_classes",
               "rule_fired": "isa2"}
    failure2 = loop.record_failure("ENT-nc2", sig_nc2,
                                   "isa lineage counter")
    offered2 = dict(offered)
    offered2["signature"] = sig_nc2
    result2 = loop.run_once(failure2["failure_id"], world,
                            offered_remedy=offered2)
    check("WG3-22", "lineage_counter_never_class_regardless",
          result2.get("outcome") == "NEVER_CLASS_REFUSED"
          and store.get_failure(failure2["failure_id"])["status"]
          == ds.FAILURE_NEVER_CLASS, result2.get("outcome"))


def scenario_f(root):
    """Conflict: two verified remedies, one signature.

    Setup note (recorded, not smoothed): two remedies for one
    signature cannot both be verified in ONE store through the
    loop — once the first is verified, the second's first proof
    meets the conflict fence (that is the fence working). Each
    remedy below is therefore verified genuinely, by replay,
    in its OWN store, and the two verified records are then
    co-registered in the conflict store — the spec section 7
    conflict case: two verified remedies matching one
    signature.

    WG3b setup note: standing in the conflict store is no
    longer granted by a fiat status write — that door is
    closed (WG3-54). Each co-registered remedy is verified
    through the store's resolution-by-reference door,
    presenting the SEALED ENTRY from its own home store's
    genuine replay proof (same operations, same signature);
    the door verifies the seal's content hash against the
    home atomic store before granting VERIFIED."""
    sig_bear = {"process": "population", "gap_kind": "empty_slot",
                "missing_item": "definition:bear",
                "rule_fired": "empty_slot_reflex"}
    verified_in_home = []
    ops_by_value = {}
    seal_proof_by_value = {}
    for i, value in enumerate(("an ursid mammal",
                               "a large mammal")):
        hstore, hloop, _hc = make_env(
            os.path.join(root, "home%d" % i))
        world = base_world()
        cases = {"orig_bear": {"entry": "bear",
                               "requires": {"definition": value}},
                 "sentinel_case": SENTINEL_CASE}
        failure = hloop.record_failure("ENT-bear-%s" % value,
                                       sig_bear,
                                       "population reflex",
                                       case_name="orig_bear")
        ops = [{"op": "fill", "entry": "bear",
                "field": "definition", "value": value}]
        taught = hloop.teach_remedy(
            sig_bear, ops, teacher="battery_teacher",
            replay_set={"battery_cases": ["sentinel_case"]},
            scope_surfaces=["content_surface"],
            world=world, cases=cases,
            failure_id=failure["failure_id"])
        verified_in_home.append(
            hstore.get_remedy(taught["remedy_id"])["status"]
            == ds.REMEDY_VERIFIED)
        ops_by_value[value] = ops
        seal_id = (taught["proof"] or {}).get("seal_entry_id")
        h_ast = ae.EntryStore.open(hloop.atomic_root,
                                   read_only=True)
        h_entry = [e for e in h_ast.replay()
                   if e["entry_id"] == seal_id][0]
        h_ast.close()
        seal_proof_by_value[value] = {
            "entry_id": seal_id,
            "content_hash": ds.content_hash(h_entry),
            "atomic_root": hloop.atomic_root}
    store, loop, counter = make_env(os.path.join(root, "conflict"))
    remedy_ids = []
    for value, ops in ops_by_value.items():
        rec = store.add_remedy(
            sig_bear, ops,
            provenance={"source_kind": "teaching",
                        "teacher": "battery_teacher",
                        "verified_in": "own store (battery setup)"},
            replay_set={"battery_cases": ["sentinel_case"]},
            scope_surfaces=["content_surface"])
        store.set_remedy_status(
            rec["remedy_id"], ds.REMEDY_VERIFIED,
            seal_proof=seal_proof_by_value[value])
        remedy_ids.append(rec["remedy_id"])
    check("WG3-23", "conflict_setup_both_remedies_verified",
          all(verified_in_home)
          and all(store.get_remedy(r)["status"]
                  == ds.REMEDY_VERIFIED for r in remedy_ids),
          verified_in_home)
    world = base_world()
    cases = {"orig_bear": {"entry": "bear", "requires":
                           {"definition": "an ursid mammal"}},
             "sentinel_case": SENTINEL_CASE}
    failure = loop.record_failure("ENT-bear-conflict", sig_bear,
                                  "population reflex",
                                  case_name="orig_bear")
    snapshot = copy.deepcopy(world)
    result = loop.run_once(failure["failure_id"], world, cases=cases)
    rec = store.get_failure(failure["failure_id"])
    check("WG3-24", "conflicting_verified_remedies_hold_never_first_match",
          result.get("outcome") == "HOLD_CONFLICT"
          and result.get("applied") is False
          and len(rec["conflicts"]) == 1
          and sorted(rec["conflicts"][0]["remedy_ids"])
          == sorted(remedy_ids)
          and world == snapshot
          and rec["status"] == ds.FAILURE_OPEN,
          result.get("outcome"))


def scenario_g(root):
    """Rot: suspension + closure listing, closures stand."""
    store, loop, counter = make_env(root)
    world = base_world()
    cases = {"orig_wolf": CASE_WOLF, "sentinel_case": SENTINEL_CASE}
    failure = loop.record_failure("ENT-rot-wolf", SIG_WOLF,
                                  "population reflex",
                                  case_name="orig_wolf")
    taught = loop.teach_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"}],
        teacher="battery_teacher",
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"],
        world=world, cases=cases, failure_id=failure["failure_id"])
    rid = taught["remedy_id"]
    clean = loop.check_rot(rid, world, cases=cases)
    check("WG3-25", "rot_check_clean_remedy_stands",
          clean["rotted"] is False)
    world["entries"]["sentinel"]["definition"] = "corrupted"
    rotted = loop.check_rot(rid, world, cases=cases)
    remedy = store.get_remedy(rid)
    rec = store.get_failure(failure["failure_id"])
    check("WG3-26", "rot_suspends_system_wide_lists_closures",
          rotted["rotted"] is True
          and rotted["suspended_system_wide"] is True
          and rotted["closures_listed"] == [failure["failure_id"]]
          and remedy["status"] == ds.REMEDY_SUSPENDED
          and remedy["suspension"]["system_wide"] is True)
    check("WG3-27", "rot_closures_stand_flagged_for_review",
          rotted["closures_stand"] is True
          and rec["status"] == ds.FAILURE_CLOSED_BY_REMEDY
          and rec["closure_review_flag"]
          == "remedy_suspended_rot:%s" % rid,
          rec["closure_review_flag"])


def scenario_h(root):
    """Section 4A aggregation pass."""
    store, loop, counter = make_env(root)
    world = base_world()
    case = {"entry": "wolf", "requires": {"genus": "canis"}}
    verdict_before = loop.replay_fn(world, case)["verdict"]
    sig_s = {"process": "population", "gap_kind": "empty_slot",
             "missing_item": "definition:elm",
             "rule_fired": "empty_slot_reflex"}
    ids = [loop.record_failure("ENT-elm-%d" % i, sig_s,
                               "population reflex")["failure_id"]
           for i in range(3)]
    sig_p1 = {"process": "population", "gap_kind": "empty_slot",
              "missing_item": "link:oak",
              "rule_fired": "empty_slot_reflex"}
    sig_p2 = {"process": "dictionary_loop", "gap_kind": "missing_variable",
              "missing_item": "link:oak",
              "rule_fired": "variable_fill"}
    p1 = loop.record_failure("ENT-oak-1", sig_p1, "population")
    p2 = loop.record_failure("ENT-oak-2", sig_p2, "dictionary loop")
    summary = store.aggregation_pass()
    check("WG3-28", "aggregation_exact_counts",
          summary["counts_by_signature"][ds.signature_key(sig_s)]
          == 3
          and summary["counts_by_missing_item"]["link:oak"] == 2,
          summary["counts_by_missing_item"])
    marks = [store.get_failure(i)["worklist_marks"]["struggled"]
             for i in ids]
    wl = store.worklist()
    check("WG3-29", "aggregation_struggled_marks_order_only_threshold_proposed",
          all(marks)
          and summary["threshold_status"] == "PROPOSED"
          and summary["struggled_signatures"]
          == [ds.signature_key(sig_s)]
          and [r["failure_id"] for r in wl[:3]] == ids)
    prereqs = store.prereq_gaps()
    check("WG3-30", "aggregation_one_prereq_gap_candidate_linked",
          len(prereqs) == 1
          and prereqs[0]["cause_grade"] == "CANDIDATE"
          and prereqs[0]["member_failure_ids"]
          == sorted([p1["failure_id"], p2["failure_id"]])
          and prereqs[0]["applies_repair"] is False
          and prereqs[0]["closes_nothing"] is True
          and all(store.get_failure(f)["status"] == ds.FAILURE_OPEN
                  for f in (p1["failure_id"], p2["failure_id"])))
    summary2 = store.aggregation_pass()
    check("WG3-31", "aggregation_idempotent_no_second_prereq",
          summary2["prereq_records_created"] == []
          and len(store.prereq_gaps()) == 1)
    verdict_after = loop.replay_fn(world, case)["verdict"]
    check("WG3-32", "aggregation_marks_do_not_influence_verdict",
          verdict_before == verdict_after == "COMPLETE"
          and store.get_failure(ids[0])["worklist_marks"]
          ["struggled"] is True,
          (verdict_before, verdict_after))


def scenario_i(root, ctx):
    """Outcome-record fences + resolution on both records."""
    store, loop, counter, world, cases, failure, taught = ctx
    astore = ae.EntryStore.open(loop.atomic_root, read_only=True)
    sealed_entry = astore.replay()[0]
    sealed_before = ds.canonical(sealed_entry)
    astore.close()
    outcome = loop.record_outcome(
        sealed_entry["entry_id"], responder="external_reviewer",
        response="the wolf answer failed in the field",
        classification="definition_gap", asserts_failure=True)
    check("WG3-33", "outcome_record_stored_verbatim_classification_marked",
          outcome["response"]
          == "the wolf answer failed in the field"
          and outcome["classification"]
          == {"label": "definition_gap",
              "marked_as": "classification_not_fact"}
          and outcome["counts_as_witness"] is False)
    opened = store.get_failure(outcome["opened_failure_id"])
    check("WG3-34", "outcome_assertion_opens_provenance_grade_failure",
          opened is not None
          and opened["cause"]["grade"] == "provenance_only"
          and opened["cause"]["counts_as_witness"] is False
          and opened["status"] == ds.FAILURE_OPEN)
    refusal = loop.apply_outcome_amendment(
        outcome["outcome_id"], {"outcome": {"verdict": "REFUSE"}})
    astore = ae.EntryStore.open(loop.atomic_root, read_only=True)
    sealed_after = ds.canonical(astore.replay()[0])
    astore.close()
    check("WG3-35", "outcome_cannot_amend_sealed_entry",
          refusal["refused"] is True
          and sealed_after == sealed_before)
    # Resolution is section 4 replay only; verdict on both.
    world["entries"]["wolf"]["reviewed"] = None
    res_cases = {"__original__": {"entry": "wolf",
                                  "requires": {"reviewed": True}},
                 "sentinel_case": SENTINEL_CASE}
    taught2 = loop.teach_remedy(
        opened["signature"],
        [{"op": "fill", "entry": "wolf", "field": "reviewed",
          "value": True}],
        teacher="battery_teacher", claim_id="WG3-TEACH-3",
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"],
        world=world, cases=res_cases,
        failure_id=opened["failure_id"])
    out_rec = store.get_outcome(outcome["outcome_id"])
    fail_rec = store.get_failure(opened["failure_id"])
    check("WG3-36", "outcome_resolution_verdict_on_both_records",
          (taught2["proof"] or {}).get("outcome") == "CLOSED"
          and out_rec["resolution"] is not None
          and out_rec["resolution"]["failure_id"]
          == opened["failure_id"]
          and fail_rec["closure"] is not None
          and fail_rec["status"] == ds.FAILURE_CLOSED_BY_REMEDY,
          (taught2["proof"] or {}).get("outcome"))


def scenario_j(root, ctx):
    """Certify-once + self-agreement refusal."""
    store, loop, counter, world, cases, failure, taught = ctx
    astore = ae.EntryStore.open(loop.atomic_root, read_only=True)
    n_entries = len(astore.replay())
    astore.close()
    calls_before = counter["calls"]
    again = loop.run_once(failure["failure_id"], world, cases=cases)
    check("WG3-37", "certified_repair_resubmitted_not_readjudicated",
          again.get("outcome") == "ALREADY_CERTIFIED"
          and again.get("re_adjudicated") is False
          and counter["calls"] == calls_before, again.get("outcome"))
    loop_next = sf.SelfFixLoop(
        store, atomic_root=loop.atomic_root,
        replay_fn=loop.replay_fn,
        regression_fns=loop.regression_fns,
        calculus_generation="gen-next")
    nc1 = loop_next.run_once(failure["failure_id"], world,
                             cases=cases)
    check("WG3-38", "nc1_cross_generation_stands_not_rerun_not_revoked",
          nc1.get("outcome") == "CERTIFIED_STANDS_NC1"
          and nc1.get("code_identity_checked") is True
          and nc1.get("re_adjudicated") is False
          and nc1.get("revoked") is False
          and counter["calls"] == calls_before, nc1.get("outcome"))
    # WG3b (WG3-38 theatre fix): the "code identity" is an
    # actual stored code hash — the certified record carries
    # the seal's code-manifest hash, and the NC-1 result
    # reports the comparison of that stored hash against the
    # current code manifest hash, not a label.
    certified_j = store.find_certified(
        failure_id=failure["failure_id"])
    astore = ae.EntryStore.open(loop.atomic_root, read_only=True)
    seal_entries = {e["entry_id"]: e for e in astore.replay()}
    astore.close()
    seal_entry_j = seal_entries.get(
        certified_j[0]["seal_entry_id"]) if certified_j else None
    check("WG3-56", "nc1_code_identity_is_a_stored_code_hash_compared",
          len(certified_j) == 1
          and seal_entry_j is not None
          and certified_j[0].get("code_manifest_hash")
          == seal_entry_j["environment"]["code_manifest_hash"]
          == sf._code_manifest_hash()
          and nc1.get("code_identity_match") is True
          and nc1.get("code_manifest_hash_stored")
          == certified_j[0].get("code_manifest_hash")
          and nc1.get("code_manifest_hash_current")
          == sf._code_manifest_hash(),
          (certified_j[0].get("code_manifest_hash")
           if certified_j else None))
    astore = ae.EntryStore.open(loop.atomic_root, read_only=True)
    check("WG3-39", "certify_once_no_new_sealed_entry",
          len(astore.replay()) == n_entries)
    astore.close()
    sig_lynx = {"process": "population", "gap_kind": "empty_slot",
                "missing_item": "definition:lynx",
                "rule_fired": "empty_slot_reflex"}
    failure2 = loop.record_failure("ENT-lynx", sig_lynx,
                                   "population reflex",
                                   case_name="orig_lynx")
    offered = {
        "remedy_id": "REM-OFFERED-SA",
        "signature": sig_lynx,
        "operations": [{"op": "fill", "entry": "lynx",
                        "field": "definition",
                        "value": "a feline mammal"}],
        "provenance": {"source_kind": "self_agreement",
                       "certification_claim": "own_class_agreement"},
        "replay_set": {"battery_cases": []},
        "scope_surfaces": [], "status": "verified",
        "suspended": False}
    snapshot = copy.deepcopy(world)
    result = loop.run_once(failure2["failure_id"], world,
                           cases=cases, offered_remedy=offered)
    check("WG3-40", "self_agreement_certification_refused",
          result.get("outcome") == "CERTIFICATION_REFUSED"
          and world == snapshot
          and store.get_failure(failure2["failure_id"])["status"]
          == ds.FAILURE_OPEN, result.get("outcome"))


def scenario_k(root):
    """System proposal fence, lock, registration, misc."""
    store, loop, counter = make_env(root)
    world = base_world()
    sig = {"process": "population", "gap_kind": "empty_slot",
           "missing_item": "definition:wolf",
           "rule_fired": "empty_slot_reflex"}
    failure = loop.record_failure("ENT-prop", sig,
                                  "population reflex",
                                  case_name="orig_wolf")
    loop.propose_remedy(
        sig,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"}],
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"])
    snapshot = copy.deepcopy(world)
    result = loop.run_once(failure["failure_id"], world,
                           cases={"orig_wolf": CASE_WOLF,
                                  "sentinel_case": SENTINEL_CASE})
    check("WG3-41", "system_proposal_candidate_never_self_applied",
          result.get("outcome") == "HOLD_CANDIDATE_NOT_APPLICABLE"
          and world == snapshot
          and store.get_failure(failure["failure_id"])["status"]
          == ds.FAILURE_OPEN, result.get("outcome"))
    # Single-writer lock (ATOM-2 pattern via pmca_persistence).
    lock_path = pmca_persistence.acquire_lock(store.root)
    raised = False
    try:
        store.add_failure("ENT-locked", sig, "lock probe")
    except pmca_persistence.WriterLockHeld:
        raised = True
    finally:
        pmca_persistence.release_lock(lock_path)
    check("WG3-42", "defect_store_single_writer_lock_loud", raised)
    registry = []
    desc = loop.registration_hook(registry)
    check("WG3-43", "registration_hook_hands_capability_as_data",
          len(registry) == 1 and registry[0] is desc
          and desc["id"] == sf.CAPABILITY_ID
          and desc["purpose"] and desc["inputs"]
          and desc["outputs"])
    check("WG3-44", "capability_limits_carry_never_class",
          "provenance_counting"
          in desc["limits"]["never_class"]
          and "harm_calculus" in desc["limits"]["never_class"]
          and "promotion_gate" in desc["limits"]["never_class"]
          and desc["limits"]["max_attempts_per_remedy_per_failure"]
          == 2)
    f2 = loop.record_failure("ENT-intro", sig, "population reflex")
    store.close_by_introduction(f2["failure_id"], "introduction:lynx")
    check("WG3-45", "closed_by_introduction_recorded",
          store.get_failure(f2["failure_id"])["status"]
          == ds.FAILURE_CLOSED_BY_INTRODUCTION)


def scenario_l(root):
    """WG3b operation fences: targets by identity and scope,
    and the respelled self-agreement claim."""
    store, loop, counter = make_env(os.path.join(root, "l1"))
    world = base_world()
    world["entries"]["provenance_counter_state"] = {
        "count": 1, "classes": 1}
    world["entries"]["attestation_log"] = {"records": []}
    cases = {"orig_wolf": CASE_WOLF, "sentinel_case": SENTINEL_CASE}
    failure = loop.record_failure("ENT-wolf-indirect", SIG_WOLF,
                                  "population reflex",
                                  case_name="orig_wolf")
    snapshot = copy.deepcopy(world)
    taught = loop.teach_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"},
         {"op": "fill", "entry": "provenance_counter_state",
          "field": "count", "value": 99},
         {"op": "fill", "entry": "provenance_counter_state",
          "field": "classes", "value": 7}],
        teacher="battery_teacher", claim_id="WG3B-INDIRECT",
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"],
        world=world, cases=cases, failure_id=failure["failure_id"])
    check("WG3-46", "indirect_never_class_target_refused_nothing_applied",
          taught["remedy_id"] is None
          and (taught["proof"] or {}).get("outcome")
          == "NEVER_CLASS_REFUSED"
          and world == snapshot
          and store.get_failure(failure["failure_id"])["status"]
          == ds.FAILURE_OPEN,
          (taught["proof"] or {}).get("outcome"))
    taught2 = loop.teach_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"},
         {"op": "link", "entry": "attestation_log",
          "field": "records",
          "value": {"entry": "wolf",
                    "source": "self_fix_remedy_itself"}}],
        teacher="battery_teacher", claim_id="WG3B-SELFWITNESS",
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"],
        world=world, cases=cases, failure_id=failure["failure_id"])
    check("WG3-47", "self_written_cross_check_target_refused_log_unchanged",
          taught2["remedy_id"] is None
          and (taught2["proof"] or {}).get("outcome")
          == "NEVER_CLASS_REFUSED"
          and world == snapshot,
          (taught2["proof"] or {}).get("outcome"))
    sig_lynx = {"process": "population", "gap_kind": "empty_slot",
                "missing_item": "definition:lynx",
                "rule_fired": "empty_slot_reflex"}
    world["entries"]["lynx"] = {"definition": None,
                                "genus": "lynx"}
    failure2 = loop.record_failure("ENT-lynx-sa", sig_lynx,
                                   "population reflex",
                                   case_name="orig_wolf")
    offered = {
        "remedy_id": "REM-SA-RESPELLED", "signature": sig_lynx,
        "operations": [{"op": "fill", "entry": "lynx",
                        "field": "definition",
                        "value": "a feline mammal"}],
        "provenance": {"source_kind": "peer_agreement",
                       "certification_claim":
                           "agreement_with_own_records"},
        "replay_set": {"battery_cases": ["sentinel_case"]},
        "scope_surfaces": ["content_surface"],
        "status": "verified", "suspended": False}
    snapshot2 = copy.deepcopy(world)
    result = loop.run_once(failure2["failure_id"], world,
                           cases=cases, offered_remedy=offered)
    astore = ae.EntryStore.open(loop.atomic_root, read_only=True)
    n_sealed = len(astore.replay())
    astore.close()
    check("WG3-48", "self_agreement_claim_respelled_refused_not_sealed",
          result.get("outcome") == "CERTIFICATION_REFUSED"
          and world == snapshot2 and n_sealed == 0,
          result.get("outcome"))
    # Scope: a content target that is NOT this failure's entry
    # is refused at operation validation, not permitted.
    store2, loop2, _c2 = make_env(os.path.join(root, "l2"))
    world2 = base_world()
    cases2 = {"orig_wolf": CASE_WOLF,
              "sentinel_case": SENTINEL_CASE}
    failure3 = loop2.record_failure("ENT-wolf-scope", SIG_WOLF,
                                    "population reflex",
                                    case_name="orig_wolf")
    store2.add_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "fox", "field": "definition",
          "value": "a canine mammal"}],
        provenance={"source_kind": "teaching",
                    "teacher": "battery_teacher"},
        replay_set={"battery_cases": ["sentinel_case"]},
        scope_surfaces=["content_surface"])
    snapshot3 = copy.deepcopy(world2)
    result3 = loop2.run_once(failure3["failure_id"], world2,
                             cases=cases2)
    check("WG3-49", "operation_target_outside_failure_scope_refused",
          result3.get("outcome") == "OPERATION_REFUSED"
          and world2 == snapshot3
          and store2.get_failure(failure3["failure_id"])["status"]
          == ds.FAILURE_OPEN, result3.get("outcome"))


def scenario_m(root):
    """WG3b: respelled never-class signatures, canonical."""
    probes = ["counter_of_classes", "prov_counter",
              "provenance tally", "lineage tally",
              "prom0tion_gate", "0mega_lock"]
    flags = {p: ds.is_never_class(
        {"process": p, "gap_kind": "counter_error",
         "missing_item": "x", "rule_fired": "y"})[0]
        for p in probes}
    raised = False
    store, loop, counter = make_env(root)
    try:
        store.add_remedy(
            {"process": "counter_of_classes",
             "gap_kind": "counter_error",
             "missing_item": "classes:write_in",
             "rule_fired": "prom1"},
            [{"op": "fill", "entry": "wolf", "field": "x",
              "value": 1}],
            provenance={"source_kind": "builder_fix",
                        "claim_id": "WG3B-RESPELLED"})
    except ds.NeverClassRefused:
        raised = True
    check("WG3-50", "respelled_never_class_canonical_incl_leet",
          all(flags.values()) and raised, flags)


def scenario_n(root):
    """WG3b: duplicate certification identity (T3c)."""
    store, loop, counter = make_env(root)
    world = base_world()
    cases = {"orig": CASE_WOLF, "sent": SENTINEL_CASE}
    f1 = loop.record_failure("ENT-wolf-1", SIG_WOLF,
                             "population reflex", case_name="orig")
    t1 = loop.teach_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"}],
        teacher="Webster", claim_id="WG3B-SRC-1",
        replay_set={"battery_cases": ["sent"]},
        scope_surfaces=["content_surface"],
        world=world, cases=cases, failure_id=f1["failure_id"])
    first_seal = (t1["proof"] or {}).get("seal_entry_id")
    world["entries"]["wolf"]["definition"] = None
    f2 = loop.record_failure("ENT-wolf-2", SIG_WOLF,
                             "population reflex", case_name="orig")
    dup = store.add_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"}],
        provenance={"source_kind": "teaching",
                    "teacher": "Webster 1913",
                    "claim_id": "WG3B-SRC-1"},
        replay_set={"battery_cases": ["sent"]},
        scope_surfaces=["content_surface"])
    store.set_remedy_status(t1["remedy_id"], ds.REMEDY_SUSPENDED,
                            suspension={"reason": "battery probe"})
    result = loop.run_once(f2["failure_id"], world, cases=cases)
    certs = store.certified()
    check("WG3-51", "same_repair_new_id_respelled_source_already_certified",
          result.get("outcome") == "ALREADY_CERTIFIED"
          and result.get("seal_entry_id") == first_seal
          and len(certs) == 1
          and world["entries"]["wolf"]["definition"] is None
          and store.get_failure(f2["failure_id"])["status"]
          == ds.FAILURE_OPEN,
          (result.get("outcome"), len(certs)))


def scenario_o(root):
    """WG3b: battery-less verification and operations rot."""
    store, loop, counter = make_env(os.path.join(root, "o1"))
    world = base_world()
    cases = {"orig": CASE_WOLF, "sent": SENTINEL_CASE}
    failure = loop.record_failure("ENT-vac", SIG_WOLF,
                                  "population reflex",
                                  case_name="orig")
    taught = loop.teach_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"}],
        teacher="battery_teacher", claim_id="WG3B-VAC",
        replay_set={"battery_cases": []}, scope_surfaces=[],
        world=world, cases=cases, failure_id=failure["failure_id"])
    check("WG3-52", "empty_replay_battery_cannot_verify_or_close",
          (taught["proof"] or {}).get("outcome") == "REPLAY_FAILED"
          and (taught["proof"] or {}).get("proof", {})
          .get("failed_stage") == "remedy_battery_empty"
          and taught["status"] == ds.REMEDY_CANDIDATE
          and world["entries"]["wolf"]["definition"] is None
          and store.get_failure(failure["failure_id"])["status"]
          == ds.FAILURE_OPEN,
          (taught["proof"] or {}).get("outcome"))
    # Tamper: a verified remedy's operations are edited on
    # disk; the stored operations hash convicts it.
    root2 = os.path.join(root, "o2")
    store, loop, counter = make_env(root2)
    world = base_world()
    failure = loop.record_failure("ENT-tamper", SIG_WOLF,
                                  "population reflex",
                                  case_name="orig")
    taught = loop.teach_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"}],
        teacher="battery_teacher", claim_id="WG3B-TAMPER",
        replay_set={"battery_cases": ["sent"]},
        scope_surfaces=["content_surface"],
        world=world, cases=cases, failure_id=failure["failure_id"])
    rid = taught["remedy_id"]
    path = os.path.join(root2, "defect", "defect_store.json")
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    for rec in data["remedies"]:
        if rec["remedy_id"] == rid:
            rec["operations"].append(
                {"op": "fill", "entry": "sentinel",
                 "field": "definition", "value": "pwned"})
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    store2 = ds.DefectStore(os.path.join(root2, "defect"))
    loop2 = sf.SelfFixLoop(
        store2, atomic_root=os.path.join(root2, "atomic_root"),
        replay_fn=loop.replay_fn,
        regression_fns=loop.regression_fns,
        calculus_generation="gen-wg3")
    tampered = store2.get_remedy(rid)
    hash_mismatch = ds.content_hash(tampered["operations"]) \
        != tampered["operations_hash"]
    rot = loop2.check_rot(rid, base_world(), cases=cases)
    world2 = base_world()
    f2 = loop2.record_failure("ENT-tamper-2", SIG_WOLF,
                              "population reflex", case_name="orig")
    result2 = loop2.run_once(f2["failure_id"], world2, cases=cases)
    check("WG3-53", "tampered_operations_rot_suspends_never_applies",
          hash_mismatch and rot["rotted"] is True
          and rot["suspended_system_wide"] is True
          and rot["closures_listed"] == [failure["failure_id"]]
          and result2.get("outcome") == "REFUSED_SUSPENDED"
          and world2["entries"]["sentinel"]["definition"]
          == "seed"
          and world2["entries"]["wolf"]["definition"] is None,
          (rot["rotted"], result2.get("outcome")))


def scenario_p(root):
    """WG3b: the store's fiat doors are closed and recorded."""
    store, loop, counter = make_env(root)
    outcome = loop.record_outcome(
        "ENT-000000", responder="field_user",
        response="this sealed answer is wrong",
        classification="wrong_answer", asserts_failure=True)
    ofid = outcome["opened_failure_id"]
    refused1 = store.set_failure_status(
        ofid, ds.FAILURE_CLOSED_BY_REMEDY,
        closure={"remedy_id": "REM-FORGED", "basis": "assertion"})
    refused2 = store.set_outcome_resolution(
        outcome["outcome_id"],
        {"verdict": "REFUSE", "basis": "assertion"})
    cand = store.add_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "x"}],
        provenance={"source_kind": "teaching",
                    "teacher": "nobody"})
    refused3 = store.set_remedy_status(cand["remedy_id"],
                                       ds.REMEDY_VERIFIED)
    refusals = store.door_refusals()
    check("WG3-54", "store_fiat_doors_refused_loudly_and_recorded",
          refused1.get("door_refused") is True
          and refused2.get("door_refused") is True
          and refused3.get("door_refused") is True
          and store.get_failure(ofid)["status"] == ds.FAILURE_OPEN
          and store.get_outcome(outcome["outcome_id"])["resolution"]
          is None
          and store.get_remedy(cand["remedy_id"])["status"]
          == ds.REMEDY_CANDIDATE
          and {r["door"] for r in refusals}
          == {"set_failure_status", "set_outcome_resolution",
              "set_remedy_status"},
          [r["door"] for r in refusals])


def scenario_q(root):
    """WG3b: an unregistered offered remedy, consistently."""
    store, loop, counter = make_env(root)
    world = base_world()
    cases = {"orig": CASE_WOLF, "sent": SENTINEL_CASE}
    failure = loop.record_failure("ENT-wolf-offered", SIG_WOLF,
                                  "population reflex",
                                  case_name="orig")
    offered = {"remedy_id": "REM-UNREGISTERED",
               "signature": SIG_WOLF,
               "operations": [{"op": "fill", "entry": "wolf",
                               "field": "definition",
                               "value": "a canine mammal"}],
               "provenance": {"source_kind": "teaching",
                              "teacher": "t"},
               "replay_set": {"battery_cases": ["sent"]},
               "scope_surfaces": ["content_surface"],
               "status": "verified", "suspended": False}
    result = loop.run_once(failure["failure_id"], world,
                           cases=cases, offered_remedy=offered)
    rec = store.get_failure(failure["failure_id"])
    check("WG3-55", "offered_unregistered_remedy_registered_then_consistent",
          result.get("outcome") == "CLOSED"
          and world["entries"]["wolf"]["definition"]
          == "a canine mammal"
          and rec["status"] == ds.FAILURE_CLOSED_BY_REMEDY
          and store.get_remedy(result["remedy_id"]) is not None
          and store.get_remedy(result["remedy_id"])["status"]
          == ds.REMEDY_VERIFIED
          and len(store.certified()) == 1,
          result.get("outcome"))


def scenario_r(root):
    """WG3b: seal -> closure reconciliation is idempotent."""
    store, loop, counter = make_env(root)
    world = base_world()
    cases = {"orig": CASE_WOLF, "sent": SENTINEL_CASE}
    failure = loop.record_failure("ENT-recon", SIG_WOLF,
                                  "population reflex",
                                  case_name="orig")
    taught = loop.teach_remedy(
        SIG_WOLF,
        [{"op": "fill", "entry": "wolf", "field": "definition",
          "value": "a canine mammal"}],
        teacher="battery_teacher", claim_id="WG3B-RECON",
        replay_set={"battery_cases": ["sent"]},
        scope_surfaces=["content_surface"],
        world=world, cases=cases, failure_id=failure["failure_id"])
    seal_id = (taught["proof"] or {}).get("seal_entry_id")
    fid = failure["failure_id"]
    # Simulate the T2c crash gap on disk: the seal is durable
    # in the atomic store, but the defect store's closure and
    # certified record never persisted.
    path = os.path.join(root, "defect", "defect_store.json")
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    for rec in data["failures"]:
        if rec["failure_id"] == fid:
            rec["status"] = ds.FAILURE_OPEN
            rec["closure"] = None
    data["certified"] = []
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    store2 = ds.DefectStore(os.path.join(root, "defect"))
    loop2 = sf.SelfFixLoop(
        store2, atomic_root=loop.atomic_root,
        replay_fn=loop.replay_fn,
        regression_fns=loop.regression_fns,
        calculus_generation="gen-wg3")
    reconciled = store2.get_failure(fid)
    loop3 = sf.SelfFixLoop(
        store2, atomic_root=loop.atomic_root,
        replay_fn=loop.replay_fn,
        regression_fns=loop.regression_fns,
        calculus_generation="gen-wg3")
    check("WG3-57", "seal_closure_reconciled_on_open_idempotent",
          reconciled["status"] == ds.FAILURE_CLOSED_BY_REMEDY
          and reconciled["closure"]["seal_entry_id"] == seal_id
          and len(store2.certified()) == 1
          and store2.get_failure(fid)["status"]
          == ds.FAILURE_CLOSED_BY_REMEDY,
          reconciled["status"])


def main():
    root = tempfile.mkdtemp(prefix="pmca_wg3_battery_")
    try:
        ctx_a = scenario_a(os.path.join(root, "a"))
        scenario_b(os.path.join(root, "a"), ctx_a)
        scenario_c(os.path.join(root, "c"))
        scenario_d(os.path.join(root, "a"), ctx_a)
        scenario_e(os.path.join(root, "e"))
        scenario_f(os.path.join(root, "f"))
        scenario_g(os.path.join(root, "g"))
        scenario_h(os.path.join(root, "h"))
        scenario_i(os.path.join(root, "a"), ctx_a)
        scenario_j(os.path.join(root, "a"), ctx_a)
        scenario_k(os.path.join(root, "k"))
        scenario_l(os.path.join(root, "l"))
        scenario_m(os.path.join(root, "m"))
        scenario_n(os.path.join(root, "n"))
        scenario_o(os.path.join(root, "o"))
        scenario_p(os.path.join(root, "p"))
        scenario_q(os.path.join(root, "q"))
        scenario_r(os.path.join(root, "r"))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n%d passed, %d failed"
          % (len(_PASSED), len(_FAILED)))
    if "--results" in sys.argv:
        path = sys.argv[sys.argv.index("--results") + 1]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"passed": len(_PASSED),
                       "failed": len(_FAILED),
                       "cases": RESULTS}, handle, indent=1)
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
