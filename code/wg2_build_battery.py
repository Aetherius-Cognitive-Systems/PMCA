"""wg2_build_battery — WG2 (DICT-1 builds) verification battery.

Seat 04 Forge build battery for WORK GROUP 2, 2026-11-01.
Covers, by execution:
  A. Shared verb_capable shape (C-1/C-4/C-6): tier/value/
     provenance classes/gap; cleaned decoding at CANDIDATE
     tier only; UNKNOWN + gap for a non-headword; Part 3
     block answers UNMEASURED and blocks POS/forms
     lineage-counted promotion.
  B. Per-field attestation distinctness (C-4): POS evidence
     never counts for genus and genus never for POS.
  C. Read precedence (docket:280 operative text): a stored
     CLEANED fact stays readable and stands under the shared
     verb_capable until re-derived; re-derivation replaces
     it. Read path: pmca_population.entry_state (stored
     variables are served as stored; the read does not
     re-derive and does not mutate the store).
  D. Part 4 dossier -> home gate: before/after pair — the
     pre-gate direct attestation crosses a conflicted value
     into a home entry; through pmca_dossier_gate the same
     dossier's conflicted and unattested fields HOLD at the
     gate with gaps recorded, and the agreeing field crosses
     carrying its starting-point (dossier) provenance inside
     the home fact.
  E. Retirement scan: no in-tree verdict path outside
     pmca_verb_capable (and the listed orphan archive copies
     pmca_category_derive_v1..v4) contains a "To "-test
     expression; the analyzer carries no seed-oracle OR in
     its guard wrapper.

Run: python3 wg2_build_battery.py  (exit 0 iff all pass)
Deterministic. Stdlib + tree modules.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pmca_dictionary

_HERE = os.path.dirname(os.path.abspath(__file__))
pmca_dictionary.configure(os.path.join(_HERE, "data",
                                       "webster1913.db"))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name
          + (" -> " + str(detail) if detail else ""))


def main():
    import pmca_dossier_gate as gate
    import pmca_population as population
    import pmca_verb_capable as vc
    import pmca_write_in as wi

    # --- A. Candidacy answer shape --------------------------
    ans = vc.pos_answer("adeem")
    check("A1_candidate_tier_for_cleaned_only_word",
          ans["tier"] == "CANDIDATE"
          and ans["value"] == {"verb_capable": True,
                               "noun_capable": False}
          and ans["provenance_classes"] == ["Webster"]
          and ans["gap"] is None,
          str(ans["tier"]) + " " + str(ans["value"]))
    # "adeem": RAW test says no verb sense; the cleaned
    # decoding (apparatus stripped) finds one. Under the set
    # that value is CANDIDATE, never attested.
    ans = vc.pos_answer("zorblat")
    check("A2_unknown_carries_gap",
          ans["tier"] == "UNKNOWN" and ans["value"] is None
          and ans["gap"]["reason"] == "no_headword",
          str(ans["gap"]))
    check("A3_part3_block_unmeasured",
          vc.lineage_independence("POS") == "UNMEASURED"
          and vc.lineage_independence("forms") == "UNMEASURED"
          and vc.promotion_blocked("POS") is True
          and vc.promotion_blocked("forms") is True)
    check("A4_boolean_projection_matches_value",
          vc.verb_capable("jump") is True
          and vc.noun_capable("jump") is False
          and vc.verb_capable("cat") is False
          and vc.noun_capable("cat") is True
          and vc.verb_capable("zorblat") is False)

    # --- B. Per-field attestation distinctness --------------
    recs = [
        vc.attestation("POS", True,
                       {"source": "Webster 1913",
                        "lineage": "Webster"}, "CANDIDATE"),
        vc.attestation("POS", True,
                       {"source": "Open English WordNet",
                        "lineage": "WordNet"}, "CANDIDATE"),
        vc.attestation("genus", "canine",
                       {"source": "Webster 1913",
                        "lineage": "Webster"}, "CANDIDATE"),
    ]
    check("B1_pos_classes_exclude_genus_record",
          vc.classes_for_field(recs, "POS")
          == {"Webster", "WordNet"},
          str(vc.classes_for_field(recs, "POS")))
    check("B2_genus_classes_exclude_pos_records",
          vc.classes_for_field(recs, "genus") == {"Webster"},
          str(vc.classes_for_field(recs, "genus")))
    # The same Webster spelling under both fields counts once
    # PER FIELD and never leaks across: POS has 2 classes,
    # genus has 1, and a POS-only pair spanning two spellings
    # of one lineage collapses through the governed table.
    recs2 = [
        vc.attestation("genus", "canine",
                       {"source": "Webster",
                        "lineage": "Webster"}, "CANDIDATE"),
        vc.attestation("genus", "canine",
                       {"source": "Webster 1913",
                        "lineage": "Webster"}, "CANDIDATE"),
    ]
    check("B3_spelling_split_collapses_within_field",
          vc.classes_for_field(recs2, "genus") == {"Webster"}
          and vc.classes_for_field(recs2, "POS") == set())

    # --- C. Read precedence: stored fact stands -------------
    # The stored fact: a variables list as an earlier
    # generation stored it (here: ["verb"] for "cup"). Fresh
    # evaluation under the shared verb_capable says cup is
    # BOTH noun- and verb-capable. The read path serves the
    # STORED fact — it stands until re-derived — and the
    # read mutates nothing. Re-derivation (a fresh
    # enrich_from_dictionary) is the only thing that
    # replaces it, exactly per docket:280 ("stand as facts
    # ... until re-derived").
    store = wi.new_store()
    population.populate_word(store, "cup", "wg2_battery")
    store["entries"]["cup"]["variables"] = ["verb"]
    fresh = vc.pos_answer("cup")["value"]
    check("C0_fresh_evaluation_differs_from_stored",
          fresh == {"verb_capable": True, "noun_capable": True},
          str(fresh))
    before = copy.deepcopy(store["entries"]["cup"])
    state = population.entry_state(store, "cup")
    check("C1_stored_fact_stands_on_read",
          state["variables"] == ["verb"],
          str(state["variables"]))
    check("C2_read_does_not_rederive_or_mutate",
          store["entries"]["cup"] == before)
    rep = population.enrich_from_dictionary(store, "cup")
    state2 = population.entry_state(store, "cup")
    check("C3_rederivation_replaces_stored_fact",
          rep["outcome"] == "enriched"
          and state2["variables"] == ["noun", "verb"],
          str(state2["variables"]))

    # --- D. Part 4 dossier -> home gate ---------------------
    dossier = {
        "word": "wolf",
        "fields": {
            "is_a": [
                {"value": "canine",
                 "provenance": {"source": "Webster 1913",
                                "lineage": "Webster"}},
                {"value": "canine",
                 "provenance": {"source": "Open English WordNet",
                                "lineage": "WordNet"}},
            ],
            "kind": [
                {"value": "animal",
                 "provenance": {"source": "Webster 1913",
                                "lineage": "Webster"}},
                {"value": "plant",
                 "provenance": {"source": "book:other",
                                "lineage": "book:other"}},
            ],
            "habitat": [],
        },
    }
    # BEFORE (pre-gate path): a direct attestation writes the
    # first gathered value straight into a home entry — the
    # conflict never gates anything and one side crosses.
    store_before = wi.new_store()
    seq = wi._next_seq(store_before)
    wi._attest(store_before, "wolf", "kind", "animal",
               {"source": "Webster 1913", "lineage": "Webster"},
               seq)
    before_sub = store_before["entries"]["wolf"][
        "subcoordinates"].get("kind")
    check("D1_before_conflicted_value_crosses_ungated",
          before_sub is not None
          and before_sub["value"] == "animal",
          str(before_sub and before_sub["value"]))
    # AFTER (the gate): same dossier through build_home_entry.
    store_after = wi.new_store()
    report = gate.build_home_entry(store_after, dossier)
    entry = store_after["entries"].get("wolf")
    subs = entry["subcoordinates"] if entry else {}
    check("D2_gate_outcome_built_with_gaps",
          report["outcome"] == "built_with_gaps",
          report["outcome"])
    check("D3_conflicted_field_held_at_gate",
          "kind" not in subs
          and report["fields"]["kind"]["disposition"]
          == "held_conflicted"
          and report["fields"]["kind"]["values"]
          == ["animal", "plant"],
          str(report["fields"]["kind"]))
    check("D4_unattested_field_held_at_gate",
          "habitat" not in subs
          and report["fields"]["habitat"]["disposition"]
          == "held_unattested")
    gaps = wi.gap_entry(store_after, "slot::wolf::kind")
    gaps_h = wi.gap_entry(store_after, "slot::wolf::habitat")
    check("D5_held_fields_recorded_as_gaps",
          gaps is not None and gaps_h is not None,
          str([gaps is not None, gaps_h is not None]))
    isa = subs.get("is_a") or {}
    hist = isa.get("provenance_history") or []
    check("D6_agreeing_field_crosses_with_dossier_provenance",
          isa.get("value") == "canine"
          and len(hist) == 2
          and all(h.get("starting_point") == "dossier"
                  and h.get("dossier_field") == "is_a"
                  for h in hist),
          str(hist))
    check("D7_crossed_status_follows_store_prom1_rule",
          isa.get("status") == "verified",
          str(isa.get("status")))
    check("D8_dossier_not_mutated",
          dossier["fields"]["kind"][0]["provenance"]
          == {"source": "Webster 1913", "lineage": "Webster"})

    # --- E. Retirement scan ---------------------------------
    import glob
    import re
    offenders = []
    test_re = re.compile(r"""\.startswith\(\s*["']To [\"']\)""")
    for path in sorted(glob.glob(os.path.join(_HERE, "*.py"))):
        base = os.path.basename(path)
        # Excluded, by name: the shared module itself (the one
        # surviving copy); the orphan archive copies v1-v4
        # (not verdict paths, listed in the WG2 paper); and
        # this build's own batteries (wg2_*.py — measuring
        # instruments that recompute the retired RAW test to
        # define the adjudication population; they decide no
        # verdict).
        if base in ("pmca_verb_capable.py",) or \
                base.startswith("wg2_") or \
                re.match(r"pmca_category_derive_v[1-4]\.py$",
                         base):
            continue
        src = open(path).read()
        if test_re.search(src):
            offenders.append(base)
    check("E1_no_to_test_outside_shared_module",
          offenders == [], str(offenders))
    src = open(os.path.join(_HERE,
                            "pmca_sentence_analyzer.py")).read()
    check("E2_no_seed_oracle_or_in_guard_wrapper",
          "dict_verb_capable(w) or" not in src
          and "VERB_FORMS.get(w, w) in VERBS" not in src)
    src_nf = open(os.path.join(_HERE, "pmca_noun_finder.py")).read()
    import ast as _ast
    tree = _ast.parse(src_nf)
    vf = next(n for n in tree.body
              if isinstance(n, _ast.FunctionDef)
              and n.name == "verb_capable")
    seg = "\n".join(src_nf.splitlines()[vf.lineno - 1:vf.end_lineno])
    check("E3_noun_finder_verb_capable_has_no_seed_or",
          "_VERBS" not in seg and "_VERB_FORMS" not in seg,
          seg.splitlines()[0])

    print("WG2 BUILD BATTERY: %d/%d passed"
          % (sum(RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
