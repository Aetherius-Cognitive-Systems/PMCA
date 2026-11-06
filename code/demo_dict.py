"""
PMCA with the English dictionary taught.

Run from ~/workspace/pmca:  python3 demo_dict.py
"""

import sys
import time
sys.path.insert(0, ".")

from pmca import Engine
from pmca.lexicon import HARM_LEXICON

DICT = "data/words_alpha.txt"

print("== teaching the dictionary ==")
t0 = time.time()
eng = Engine(dictionary_path=DICT)
t1 = time.time()
n_entries = len(eng.store.active)
dict_added = getattr(eng.store, "dict_words_added", 0)
print(f"seed build: {t1 - t0:.1f}s, {n_entries} entries committed, "
      f"{dict_added} from the dictionary")

# how many dictionary words did the lexicon actually override?
with open(DICT, encoding="utf-8", errors="replace") as f:
    words = {line.strip().lower() for line in f if line.strip()}
hits = [w for w in words if w in HARM_LEXICON]
print(f"lexicon overrides hit on {len(hits)} dictionary words "
      f"(of {len(HARM_LEXICON)} lexicon entries)")
print()

results = []


def check(name, verdict, expected_outcome, reason_hint=None):
    ok = verdict.outcome == expected_outcome
    if reason_hint:
        ok = ok and any(reason_hint.lower() in r.lower()
                        for r in verdict.reasons)
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} -> {verdict.outcome}")
    for r in verdict.reasons:
        print(f"       - {r[:130]}")
    print()


# 1. the OPEN-7 case from the small-seed demo, revisited: "phlogiston" is a
# real (obsolete) English word, so it is valued now — the hold moves from
# "unvalued word" to the truth layer (OPEN-2).
v1 = eng.admit("phlogiston boils at 100 c at 1 atm", "adult")
check("1. phlogiston: valued, still held — now by truth layer",
      v1, "HELD", "outside the seeded laws")

# 2. ordinary vocabulary sails through (every word valued at 0)
v2 = eng.admit("the quick brown fox jumps over the lazy dog", "adult")
check("2. ordinary sentence", v2, "APPROVED")

# 3. lexicon override still bites at dictionary scale
v3 = eng.admit("murder", "adult")
check("3. lexicon word 'murder'", v3, "REJECTED", "exposure")

# 4. the mechanism survives: non-dictionary tokens still hold
v4 = eng.admit("the blorpt wobbles", "adult")
check("4. non-dictionary word 'blorpt'", v4, "HELD", "not in seed graph")

# 5. slur from the lexicon -> intrinsic harm, no path to approval
v5 = eng.admit("nigger", "adult")
check("5. lexicon slur", v5, "REJECTED", "IntrinsicHarm")

# 6. cycle cost at dictionary scale (Section 16: copy proportional to live data)
eng.admit("the cat sat", "adult")  # "sat" is valued now -> approved -> pending
t0 = time.time()
rep = eng.run_cycle()
t1 = time.time()
ok6 = rep["cycle"] == 1 and rep["dropped"] == 0
results.append(ok6)
print(f"[{'PASS' if ok6 else 'FAIL'}] 6. cycle at dictionary scale")
print(f"       {t1 - t0:.1f}s, bytes_copied={rep['bytes_copied']}, "
      f"added={len(rep['added'])}")
print()

n_ok = sum(results)
print(f"== {n_ok}/{len(results)} checks passed ==")
sys.exit(0 if n_ok == len(results) else 1)
