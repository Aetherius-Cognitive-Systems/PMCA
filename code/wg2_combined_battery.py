"""wg2_combined_battery — the STANDING COMBINED-SENTENCE battery.

Built 2026-11-01, WG2 (04 Forge), under Candidacy Rule Set
C-9 and Jonathan's test-design direction (2026-10-31):
sentences must force the whole system into one sentence —
ambiguous verb-noun forms settled only by sentence
structure/company, auxiliaries, modals, negation, and
consequence-carrying nouns firing together — because
class-in-sentence is decided by the composed sentence, not
in isolation.

Every case carries a STATUS label in the battery itself:
  OWNED-PART2        — the expectation differs from the
                       pre-build behaviour and the flip is
                       owned by DICT-1 Part 2's ruling text
                       (docket:273), verified against the
                       WG2 before/after probe corpus.
  STANDING           — behaviour predates this build and is
                       unchanged by it (verified on the
                       probe corpus where the sentence
                       appears there; otherwise the measured
                       post-build record of a mechanism this
                       build did not touch).
  CURRENT-BEHAVIOUR  — the expectation records CURRENT
                       behaviour ONLY. Modal cases carry
                       this label in full: the GRAM-1 modal
                       text is UNRULED, this battery encodes
                       NO modal doctrine, and a modal ruling
                       is expected to change these cases —
                       when it does, the change is a named
                       event against this record.

Run: python3 wg2_combined_battery.py  (exit 0 iff pass)
Dictionary tier: the tree's stored Webster 1913 DB.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pmca_dictionary

_HERE = os.path.dirname(os.path.abspath(__file__))
pmca_dictionary.configure(os.path.join(_HERE, "data",
                                       "webster1913.db"))

# (sentence, status, verdict, hold_reason, lock_reason, action)
CASES = [
    # The canonical case: noun "can" (container), modal
    # "can", verb "contain", noun "soda" — class only the
    # composed sentence can settle. The modal screen fires
    # first in the current build.
    ("A can can contain soda.", "CURRENT-BEHAVIOUR",
     "HOLD", "modal_state_unresolved", None, None),
    # Part 2's named flip: do-support + base form of a seed
    # verb + consequence-carrying noun (man, person).
    ("He does crush the old man.", "OWNED-PART2",
     "REFUSE", None, "death_of_person", "crushes"),
    # Same shape, bare base form, no do-support.
    ("He crush the old man.", "OWNED-PART2",
     "REFUSE", None, "death_of_person", "crushes"),
    # Gate marker + do-support + base form + child target:
    # the identity is Part 2's (action crushes); the lock is
    # the child gate armed by the clause's own marker.
    ("Knowingly he does crush the child.", "OWNED-PART2",
     "REFUSE", None, "child_gate", "crushes"),
    # Negation in the verb group over the same composed
    # shape: negation geometry HOLDs before any lock.
    ("He does not crush the old man.", "STANDING",
     "HOLD", "negation_out_of_scope:not", None, None),
    # Modal + seed verb + replaceable target.
    ("She can break the cup.", "CURRENT-BEHAVIOUR",
     "HOLD", "modal_state_unresolved", None, None),
    ("He will break the cup.", "CURRENT-BEHAVIOUR",
     "HOLD", "modal_state_unresolved", None, None),
    # Modal over a seed verb whose table is empty (holds):
    # the modal screen precedes all coefficient work.
    ("He will holds the child.", "CURRENT-BEHAVIOUR",
     "HOLD", "modal_state_unresolved", None, None),
    # Do-support + non-seed dictionary verb + person target:
    # the verb carries no seed structure; coefficient HOLD.
    ("The dog does bite the man.", "STANDING",
     "HOLD", "coefficient_missing", None, "bite"),
    # Do-form as MAIN verb (no company): grammar-tier "do".
    ("He does his work.", "STANDING",
     "HOLD", "target_structure_missing:work", None, "do"),
    # Generic frame: demonstrative + main-verb do-form.
    ("That does that.", "STANDING",
     "HOLD", "generic_frame_unresolved", None, None),
    # Ambiguous form in BOTH slots (leaves/leaves): no
    # surface may settle it by force; the clause HOLDs
    # unparsed.
    ("She leaves the leaves.", "STANDING",
     "HOLD", "unparsed", None, None),
    # Negation + ambiguous subject noun + dictionary verb:
    # the current counter reads "leaves" through its
    # dictionary resolution and counts two verbs.
    ("The leaves do not cover the cup.", "CURRENT-BEHAVIOUR",
     "HOLD", "multi_verb_unsegmented", None, None),
    # Two clauses; plural seed-noun targets in both: the
    # Part 2 counter reads cups/windows as resolved plurals,
    # so the sentence reaches the (targetless) clause
    # evaluation instead of the multi-verb HOLD.
    ("He breaks the cups and cracks the windows.",
     "OWNED-PART2", "HOLD", "no_edges_no_event", None,
     "breaks"),
    # Two clauses: an attested third-person form (rolls ->
    # rolled, Part 2) carries clause 1 to an edge, so the
    # loop reaches clause 2's stored death consequence.
    ("The boulder rolls down the hill and crushes a man.",
     "OWNED-PART2", "REFUSE", None, "death_of_person",
     "rolled"),
    # Negation in the second clause + non-replaceable
    # consequence noun (spine) as its target: the negation
    # HOLD names the whole sentence's disposition.
    ("He breaks the window and does not crack the spine.",
     "STANDING", "HOLD", "negation_out_of_scope:not", None,
     None),
    # Do-support + base form + repairable target: the
    # canonical verb's own verdict, by Part 2 identity.
    ("He does crack the window.", "OWNED-PART2",
     "APPROVE", None, None, "cracks"),
    # Do-support + base form of the empty-table verb: the
    # identity flips (action holds), the HOLD stands —
    # an attested form is that verb, empty table included.
    ("He does hold the child.", "OWNED-PART2",
     "HOLD", "coefficient_missing", None, "holds"),
]


def main():
    import pmca_sentence_analyzer as analyzer

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name
              + (" -> " + str(detail) if detail else ""))

    for sentence, status, verdict, hold, lock, action in CASES:
        r = analyzer.analyze(sentence)
        got = (r["verdict"], r["hold_reason"], r["lock_reason"],
               r["parameters"]["action"])
        want = (verdict, hold, lock, action)
        check("[%s] %s" % (status, sentence), got == want,
              str(got))

    print("COMBINED-SENTENCE BATTERY: %d/%d passed"
          % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
