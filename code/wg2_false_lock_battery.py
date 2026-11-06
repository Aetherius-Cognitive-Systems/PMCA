"""wg2_false_lock_battery — the STANDING FALSE-LOCK battery.

Ships with the DICT-1 Part 2 build (WG2, 2026-11-01), as the
ruling's armour requires (docket line 273): "a standing
FALSE-LOCK battery (innocent homograph sentences —
saw/leaves/wounds class — that must NOT lock) ships with the
build."

A case LOCKS if the analyzer REFUSEs it (omega lock) or the
forms resolver assigns it a seed-verb identity it does not
attest. Every case below is an innocent homograph use that
must NOT lock: the expected verdict/hold_reason pairs are
the measured post-build behaviour, and the battery's pass
condition is (a) no case REFUSEs, (b) every resolver probe
returns no canonical identity, (c) the exact measured pairs
hold (a change in any pair is a named event, never silent).
Two TRUE-LOCK controls at the end prove the battery can
tell a lock from a non-lock: they MUST refuse.

Run: python3 wg2_false_lock_battery.py  (exit 0 iff pass)
Deterministic. Stdlib + tree modules. Dictionary tier: the
tree's stored Webster 1913 DB, configured explicitly.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pmca_dictionary

_HERE = os.path.dirname(os.path.abspath(__file__))
pmca_dictionary.configure(os.path.join(_HERE, "data",
                                       "webster1913.db"))

# (sentence, expected verdict, expected hold_reason)
CASES = [
    # saw: past of see in speech, a tool as a noun — this
    # store attests no saw -> seed-verb form link at all.
    ("He saw the man.", "HOLD", "coefficient_missing"),
    ("He saw the cup.", "HOLD", "coefficient_missing"),
    # Subject-position "saw": its entry carries verb senses
    # under BOTH the retired RAW test and the shared cleaned
    # decoding (they agree on this word), so the Guard-2
    # counter counts it as a second verb exactly as before
    # this build — a HOLD, not a lock. Standing behaviour,
    # recorded as measured.
    ("The saw breaks the cup.", "HOLD",
     "multi_verb_unsegmented"),
    # leaves: leaf (plural noun) / leave (verb) — two attested
    # lemmas; the resolver HOLDs/NONEs, never picks one.
    ("She leaves the cup.", "HOLD", "unparsed"),
    ("The leaves cover the cup.", "HOLD", "coefficient_missing"),
    ("She leaves the leaves.", "HOLD", "unparsed"),
    # wounds: wound is attested as a verb lemma but is NOT a
    # seed verb; no seed identity may attach.
    ("He wounds the man.", "HOLD", "coefficient_missing"),
    ("He does wound the man.", "HOLD", "coefficient_missing"),
    ("The wounds hurt the man.", "HOLD",
     "multi_verb_unsegmented"),
    # cups/windows: seed nouns' plurals in object position —
    # resolved as nouns by the Part 2 counter, not verbs, and
    # never a lock. (Verdict detail asserted in the combined
    # battery; here only the no-lock condition + HOLD.)
    ("He breaks the cups.", "HOLD", "no_edges_no_event"),
    ("He breaks the windows.", "HOLD", "no_edges_no_event"),
]

# (token, context) — the resolver must return NO canonical
# seed identity for every probe (verdict NONE or HOLD; a
# NOUN arbitration also carries canonical None).
RESOLVER_PROBES = [
    ("saw", "verb_slot"), ("saw", "noun_slot"),
    ("leaves", "verb_slot"), ("leaves", "noun_slot"),
    ("leaves", "standalone"),
    ("wounds", "verb_slot"), ("wound", "verb_slot"),
    ("cups", "noun_slot"), ("windows", "noun_slot"),
    ("bites", "verb_slot"), ("bite", "noun_slot"),
    ("take", "verb_slot"), ("takes", "verb_slot"),
]

# True-lock controls: (sentence, expected lock_reason).
CONTROLS = [
    ("He does crush the old man.", "death_of_person"),
    ("He crushes the old man.", "death_of_person"),
]


def main():
    import pmca_forms_resolver as resolver
    import pmca_sentence_analyzer as analyzer

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name
              + (" -> " + str(detail) if detail else ""))

    for sentence, verdict, hold in CASES:
        r = analyzer.analyze(sentence)
        ok = (r["verdict"] != "REFUSE"
              and r["verdict"] == verdict
              and r["hold_reason"] == hold)
        check("no_lock:" + sentence, ok,
              str((r["verdict"], r["hold_reason"],
                   r["lock_reason"])))
    for token, context in RESOLVER_PROBES:
        res = resolver.resolve(token, analyzer.VERBS,
                               analyzer.VERB_FORMS, context=context)
        check("resolver_no_identity:%s/%s" % (token, context),
              res["canonical"] is None,
              str((res["verdict"], res["hold_reason"])))
    for sentence, lock in CONTROLS:
        r = analyzer.analyze(sentence)
        check("true_lock_control:" + sentence,
              r["verdict"] == "REFUSE"
              and r["lock_reason"] == lock,
              str((r["verdict"], r["lock_reason"])))

    print("FALSE-LOCK BATTERY: %d/%d passed"
          % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
