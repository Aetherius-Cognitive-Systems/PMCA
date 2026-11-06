"""
PMCA simulation demo — every gate, end to end.

Run from ~/workspace/pmca:  python3 demo.py
"""

import sys
sys.path.insert(0, ".")

from pmca import Engine
from pmca.model import Entry, Subcoord

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, verdict, expected_outcome, reason_hint=None):
    ok = verdict.outcome == expected_outcome
    hint_ok = True
    if reason_hint:
        hint_ok = any(reason_hint.lower() in r.lower()
                      for r in verdict.reasons)
        ok = ok and hint_ok
    results.append(ok)
    print(f"[{PASS if ok else FAIL}] {name}")
    print(f"       outcome={verdict.outcome} "
          f"(expected {expected_outcome})")
    for r in verdict.reasons:
        print(f"       - {r[:150]}")
    if verdict.removed:
        print(f"       removed={[s.content for s in verdict.removed]}")
    if verdict.condition:
        print(f"       condition={verdict.condition}")
    print()


eng = Engine()

# 1. benign adult input -> APPROVED
v1 = eng.admit("the cat in the garden", "adult")
check("1. benign adult input", v1, "APPROVED")

# 2. child audience, mild word -> REFUSED outright (A3 revised 2026-10-08:
# any taint is refused, not held, not redacted, never released to a child)
v2 = eng.admit("the cat is angry", "child")
check("2. child audience, mild harm", v2, "REFUSED", "refused outright")

# 3. intrinsic harm -> REJECTED, no path to approval
v3 = eng.admit("slur_alpha", "adult")
check("3. intrinsic-harm word", v3, "REJECTED", "IntrinsicHarm")

# 4. violent descriptors -> REJECTED (dismantled into scraps)
v4 = eng.admit("stab wound", "adult")
check("4. violent descriptors", v4, "REJECTED", "exposure")

# 5. news report with violent detail -> REDACTED (remainder consistent)
v5 = eng.admit("news report says a man was killed downtown", "adult")
check("5. news report, redaction", v5, "REDACTED", "redaction")

# 6. subliminal: no single harmful word, harmful pattern -> HELD
v6 = eng.admit("whisper rumor plan midnight alone follow", "adult")
check("6. subliminal harm pattern", v6, "HELD", "subliminal")

# 7. twist ban: mild alone, harmful inside its closure -> REJECTED
# (runs before the reaction case so no matching scraps exist yet)
v7 = eng.admit("spark", "adult")
check("7. twist ban", v7, "REJECTED", "twist ban")

# 8. deterministic reaction: harm path forces the outcome -> REJECTED
v8 = eng.admit("spark panic stampede", "adult")
check("8. deterministic reaction", v8, "REJECTED", "reaction")

# 9. false physics claim -> collapse -> REJECTED then inspected
v9 = eng.admit("water boils at 50 c at 1 atm", "adult")
check("9. false claim collapses", v9, "REJECTED", "collapses")
print(f"       collapse inspections on record: {len(eng.collapses)}")
print()

# 10. missing dimension -> conditional approval (proposed rule)
v10 = eng.admit("water boils at 100 c", "adult")
check("10. missing dimension", v10, "CONDITIONAL", "dimension")

# 11. true claim with dimensions -> APPROVED
v11 = eng.admit("water boils at 100 c at 1 atm", "adult")
check("11. true claim", v11, "APPROVED")

# 12. claim outside seeded laws -> HELD (OPEN-2)
v12 = eng.admit("phlogiston boils at 100 c at 1 atm", "adult")
check("12. unseeded subject", v12, "HELD", "outside the seeded laws")

# 13. chaos-pool tripwire: new words, same *connections* as rejected scraps
# ("stab wound" -> 2 Exposure-meaning edges; same multiset here)
v13 = eng.admit("strangle torture", "adult")
check("13. chaos-pool tripwire", v13, "SANDBOX_TRIPWIRE", "tripwire")
print(f"       sandbox holds {len(eng.sandbox)} item(s)")
print()

# 14. determinism: same input, same verdict (T1)
a = eng.admit("the cat in the garden", "adult")
b = eng.admit("the cat in the garden", "adult")
det = (a.outcome == b.outcome and a.reasons == b.reasons
       and [s.content for s in a.admitted] == [s.content for s in b.admitted])
results.append(det)
print(f"[{'PASS' if det else 'FAIL'}] 14. determinism (T1)")
print()

# 15. output gate: deep verification + render + round-trip
out = eng.emit(v1, "adult")
ok15 = out["gate"] == "PASSED" and out["round_trip"] == "accept"
results.append(ok15)
print(f"[{'PASS' if ok15 else 'FAIL'}] 15. output gate + round-trip")
print(f"       {out['output'][:160]}")
print(f"       round_trip={out['round_trip']}")
print()

# 16. cycle: pending -> review -> idle half -> switch
rep = eng.run_cycle()
ok16 = rep["cycle"] == 1 and rep["dropped"] == 0
results.append(ok16)
print(f"[{'PASS' if ok16 else 'FAIL'}] 16. cycle commit")
print(f"       {rep}")
print(f"       metrics: bytes_copied={eng.store.bytes_copied_per_cycle}, "
      f"lookups={len(eng.store.lookups_per_admission)}")
print()

# 17. chain reaction on a fresh engine: remove the Exposure category node
eng2 = Engine()
exp_coord = next(c for c, n in eng2.cat_names.items() if n == "Exposure")
cr = eng2.chain_reaction(exp_coord)
rep2 = eng2.run_cycle()
gone = eng2.store.lookup(exp_coord) is None
# 13 affected: the 12 words pointing at Exposure + the node's own self-edge.
# The repaired shape is coherent, so the cycle approves.
ok17 = cr["removed"] == exp_coord and len(cr["affected_entries"]) == 13 \
    and rep2["dropped"] == 1 and gone and rep2.get("approved") is True
results.append(ok17)
print(f"[{'PASS' if ok17 else 'FAIL'}] 17. chain reaction + removal")
print(f"       removed coord {cr['removed']} (Exposure category)")
print(f"       affected entries rechecked: {len(cr['affected_entries'])}")
print(f"       dropped at switch: {rep2['dropped']}, "
      f"lookup after switch: {'gone' if gone else 'STILL THERE'}, "
      f"approved={rep2.get('approved')}")
print()

# 18. cycle-review approval criterion (decided 2026-10-07): a raw removal
# with no chain reaction leaves dangling subcoordinates -> the resulting
# shape is incoherent -> the delta is rejected, state moves nowhere.
eng3 = Engine()
exp3 = next(c for c, n in eng3.cat_names.items() if n == "Exposure")
eng3.pending.remove(exp3)  # no chain reaction: edges left dangling
rep3 = eng3.run_cycle()
still_there = eng3.store.lookup(exp3) is not None
ok18 = rep3.get("approved") is False \
    and any("dangling" in r for r in rep3.get("reasons", [])) \
    and still_there and eng3.store.cycle_count == 0
results.append(ok18)
print(f"[{'PASS' if ok18 else 'FAIL'}] 18. incoherent delta rejected")
print(f"       approved={rep3.get('approved')}, "
      f"reasons={rep3.get('reasons', [])[:2]}")
print(f"       state unchanged: {still_there}, cycles run: "
      f"{eng3.store.cycle_count}")
print()

# 19. rejected additions are routed to the Sandbox (decided 2026-10-07)
# for further evaluation, correction, or dismantling.
eng4 = Engine()
ghost = Entry(content="ghostword",
              sigma=[Subcoord(target=999999, direction="out",
                              role="meaning", harm=0)])
eng4.pending.add(ghost)
n_sandbox_before = len(eng4.sandbox)
rep4 = eng4.run_cycle()
held = [i for i in eng4.sandbox.items if i.entry.content == "ghostword"]
ok19 = rep4.get("approved") is False and len(held) == 1 \
    and len(eng4.sandbox) == n_sandbox_before + 1 \
    and held[0].entry.coordinate is None  # unapproved: no coordinate
# one fate, dismantling: the held entry becomes Chaos Pool scraps
n_scraps_before = len(eng4.chaos.scraps)
eng4.chaos.dismantle(held[0].entry, eng4.cat_names)
eng4.sandbox.release(held[0].entry)
ok19 = ok19 and len(eng4.chaos.scraps) == n_scraps_before + 1 \
    and len(eng4.sandbox) == n_sandbox_before
results.append(ok19)
print(f"[{'PASS' if ok19 else 'FAIL'}] 19. rejected addition -> sandbox "
      f"-> dismantled")
print(f"       approved={rep4.get('approved')}, "
      f"sandboxed={rep4.get('sandboxed')}, scraps now="
      f"{len(eng4.chaos.scraps)}")
print()

# 20. redaction consistency is never singular (decided 2026-10-07): the
# remainder passes harm evaluation at *every* gate — admission (case 5),
# cycle review (case 16 committed it), deep verification (here) — so
# harmful content cannot normalize into the approved geometry.
out5 = eng.emit(v5, "adult")
ok20 = out5["gate"] == "PASSED"
results.append(ok20)
print(f"[{'PASS' if ok20 else 'FAIL'}] 20. redacted remainder passes "
      f"deep verification")
print(f"       gate={out5['gate']}, round_trip={out5['round_trip']} "
      f"(known renderer tension, see README)")
print()

# 21. sandbox re-check order is FIFO (decided 2026-10-07, was OPEN-4)
eng5 = Engine()
e_first = Entry(content="firstheld")
e_second = Entry(content="secondheld")
eng5.sandbox.hold(e_first, "reason a", 0)
eng5.sandbox.hold(e_second, "reason b", 0)
nxt = eng5.sandbox.next_for_recheck()
ok21 = nxt is not None and nxt.entry is e_first
results.append(ok21)
print(f"[{'PASS' if ok21 else 'FAIL'}] 21. sandbox re-check is FIFO")
print(f"       next for re-check: {nxt.entry.content if nxt else None}")
print()

# 22. A3 revised 2026-10-08: intrinsic harm for a child is refused outright
# (no redaction attempt — nothing is released to a child in any form)
eng6 = Engine()
v22 = eng6.admit("slur_alpha", "child")
check("22. child intrinsic harm refused", v22, "REFUSED", "refused outright")
print(f"       sandbox holds {len(eng6.sandbox)} item(s) "
      f"(refused, not held)")
print()

# 23. unknown words for a child are still fail-closed quarantine (HELD) —
# the refusal rule covers harm taint; unseeded words go to the Sandbox
# for evaluation, matching the newer sims' treatment of unknowns.
v23 = eng6.admit("the cat is zzzqqq", "child")
check("23. child unknown word held", v23, "HELD", "fail-closed")
print()

n_ok = sum(results)
print(f"== {n_ok}/{len(results)} checks passed ==")
sys.exit(0 if n_ok == len(results) else 1)
