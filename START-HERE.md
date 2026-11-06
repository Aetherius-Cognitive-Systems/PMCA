# START HERE

The `code/` folder holds 67 scripts, 51,208 lines. Not every file is a place to start. Four files at the top of the alphabetical list are dead legacy and will crash if run: do not start there.

## Do not run these

- `demo.py`
- `demo_dict.py`
- `benchmark.py`
- `build_test_graphs.py`

All four open with `from pmca import Engine`, a package that does not exist in this flat tree. They are retained for the record and are flagged as dead legacy in the blueprint (LV-18). Running `demo.py` first gives an immediate ModuleNotFoundError. That crash says nothing about the live system.

Also in the tree, retained for the record: the nine retired finder microscripts (for example `pmca_noun_finder.py`) and `pmca_category_derive_v1.py` through `v4.py`. The live finder is the single unified `pmca_finder.py`; the category derivation that counts is the latest version.

## Run these instead

- `pmca_sentence_analyzer.py`: the sentence analyzer, the main verdict path.
- `pmca_finder.py`: the unified finder (lookup, dictionary attestation, sentence-context arbitration).
- `pmca_self_fix_battery.py`: the self-fix battery.
- `wg2_conformance.py`: the conformance suite.

## The verdict contract

The gate returns one of three verdicts: APPROVE, REFUSE, or HOLD. APPROVE is the only verdict that lets anything proceed. In any gate deployment, HOLD means not verified and blocks, the same as REFUSE. The difference is what happens next: REFUSE is closed, while HOLD is a work order that feeds the teaching queue.

## What this system is

PMCA is a deterministic symbolic graph reasoning engine. It is not a language model and has no neural component. It looks up stored relationships, applies exact rules, and holds instead of guessing when it does not know. Every verdict can be traced to the edges and rules that produced it.
