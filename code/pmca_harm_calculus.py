"""PMCA harm calculus — the ruled calculus, implemented exactly once.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's architecture.

WHAT THIS MODULE IS
    The single shared implementation of the harm calculus that
    Jonathan ruled on 2026-10-20 and completed on 2026-10-28
    (rulings docket, batch sitting 2026-10-28). Both verdict
    engines call it: pmca_sentence_analyzer (the sentence path)
    and pmca_proof (the core proof / demo path, via its
    gate_verdict wrapper). Before this module existed, the two
    engines totalled differently — the analyzer ran this calculus
    while pmca_proof still ran the pre-ruling noisy-OR combiner
    and could return REDACT, a verdict the ruled set does not
    contain (drift register LV-07 / LV-08). The cutover of
    2026-10-28 closed that drift by extraction: the calculus
    lives here, and neither engine carries a private copy.

THE CALCULUS (rulings of record)
    - Harm values are rational pairs (numerator, denominator),
      integers only, normalized by gcd, denominator positive.
      Comparisons by cross-multiplication. No floats.
    - Edge harm = target dependency weight x verb irreversibility
      coefficient, the coefficient resolved per the target's
      replaceability class. The classes are BINARY (P3-1, ruled
      2026-10-28): replaceability is a class lookup, never a
      graded value computed here.
    - Per-edge gate first (Candidate A): any single RAW edge harm
      at or above GATE_LIMIT refuses on its own. The scan reads
      the raw edge harms, before any decay (SEM-1, ruled
      2026-10-28).
    - Totaling is cascade addition with decay: edges sorted
      descending (bookkeeping only — sorting never decides the
      verdict), the first edge counts in full, each later edge is
      divided by its position (second by 2, third by 3, ...).
      Totals are EXACT rational pairs at any chain length — no
      denominator cap, no window (P3-2, ruled 2026-10-28).
    - Verdict set: APPROVE / REFUSE / HOLD. There is no REDACT:
      it was superseded by the 2026-10-20 calculus and left the
      verdict set (SEM-2, ruled 2026-10-28).
    - Omega (the infinity lock) has two triggers, checked by the
      caller per clause after the target is known and before the
      coefficient lookup:
        Trigger 1 (child_gate): the target's stored flags carry
          "child" and the clause's conscious-choice gate is true.
        Trigger 2 (death_of_person): the chain's stored terminal
          consequence is the death of a person (P3-3, ruled
          2026-10-28) — read from stored data only: the target's
          stored "person" flag and the verb entry's stored
          death_consequence field. No verb name is ever compared;
          a verb locks under this trigger iff its own stored
          entry declares the consequence. Trigger 2 has no gate
          condition.
      A third lock, closure_unknown_high_harm, is not a trigger:
      it is the closing state of the totaling verdict when the
      consequence is missing, harm is at the gate, and the caller
      demands closure.
    - The conscious-choice gate itself (the marker scan) is NOT
      in this module: it is a per-engine input, and its current
      marker-based form is a known approximation of the ruled
      recursive intent loop, which is a separate future design
      artefact.

WHAT IS NOT HERE (SEM-3, ruled 2026-10-28)
    No audience input of any kind. The child-audience refusal
    axis is a DEPLOYMENT parameter, not a calculus parameter:
    audience overlays live in the calling deployment wrappers
    (see pmca_proof.gate_verdict), never in this module.

Deterministic. Stdlib only. Integers only. No statistics.
Same input gives same output, always.
"""

import functools
import math

# The Candidate A per-edge gate limit, as a rational pair.
GATE_LIMIT = (5, 1)


# ---------------------------------------------------------------------
# Rational pairs. A harm value is a tuple (numerator, denominator),
# denominator positive, divided down by the greatest common divisor.
# Ordering is by cross-multiplication, integers only.
# ---------------------------------------------------------------------

def pair(num, den):
    """Build a normalized rational pair."""
    if den < 0:
        num = -num
        den = -den
    g = math.gcd(num, den)
    if g == 0:
        return (0, 1)
    return (num // g, den // g)


def pair_cmp(a, b):
    """Compare two pairs: -1, 0, or 1, by cross-multiplication."""
    left = a[0] * b[1]
    right = b[0] * a[1]
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def pair_ge(a, b):
    """True iff pair a is at or above pair b."""
    return a[0] * b[1] >= b[0] * a[1]


def pair_add(a, b):
    """Add two rational pairs, normalized."""
    return pair(a[0] * b[1] + b[0] * a[1], a[1] * b[1])


def cascade_total(harms):
    """Cascade addition with decay.

    Sorting here is bookkeeping only. The first edge counts in
    full; each later edge is divided by its position (second
    edge by 2, third edge by 3, and so on). Exact at any length:
    pairs are reduced by gcd at every step and never capped
    (P3-2).
    """
    def cmp_desc(a, b):
        return pair_cmp(b, a)

    ordered = sorted(harms, key=functools.cmp_to_key(cmp_desc))
    total = (0, 1)
    for j, h in enumerate(ordered):
        if j == 0:
            term = h
        else:
            term = pair(h[0], h[1] * (j + 1))
        total = pair_add(total, term)
    return total


# ---------------------------------------------------------------------
# The per-edge gate (Candidate A). A boolean scan over the RAW
# edge harms — before decay, never over the total (SEM-1).
# ---------------------------------------------------------------------

def any_edge_at_gate(harms):
    """True iff any single raw edge harm is at or above the gate
    limit. Such an edge refuses on its own, whatever the cascade
    total says."""
    for h in harms:
        if pair_ge(h, GATE_LIMIT):
            return True
    return False


# ---------------------------------------------------------------------
# Omega triggers. Checked by the caller per clause, after the
# target is known and before any coefficient lookup. Both read
# stored data only.
# ---------------------------------------------------------------------

def child_gate_lock(target_flags, clause_gate):
    """Trigger 1: the target's stored flags carry "child" and
    this clause's conscious-choice gate is true."""
    return "child" in target_flags and bool(clause_gate)


def terminal_death_lock(target_flags, verb_entry):
    """Trigger 2 (P3-3): the stored terminal consequence is the
    death of a person. True iff the target's stored flags carry
    "person" and the verb's stored entry declares its terminal
    consequence on a person to be death ("death_consequence").
    verb_entry is the verb's stored entry mapping, or None when
    the verb has no stored entry. No verb name is consulted."""
    if verb_entry is None:
        return False
    if "person" not in target_flags:
        return False
    return bool(verb_entry.get("death_consequence", False))


# ---------------------------------------------------------------------
# The totaling verdict. The decision table the analyzer has
# executed since the 2026-10-20 rulings, extracted unchanged:
#
#   intrinsic edge present        -> REFUSE (edge datum from the
#                                     proof surface; the sentence
#                                     path never passes it)
#   consequence present           -> REFUSE iff any raw edge is at
#                                     the gate or the cascade total
#                                     is at the gate, else APPROVE
#   consequence missing, at gate  -> closure: REFUSE, omega, the
#                                     closing state
#                                     (closure_unknown_high_harm);
#                                     open: HOLD, the waiting state
#                                     (consequence_missing_high_harm)
#   consequence missing, below    -> APPROVE
#
# Returns (verdict, omega, reason, total): reason is the lock
# reason when omega is true, the hold reason when the verdict is
# HOLD, else None. total is the exact cascade total.
# ---------------------------------------------------------------------

def totaling_verdict(harms, consequence_present, closure=False,
                     intrinsic=False):
    total = cascade_total(harms)
    if not harms:
        # B-2 (ruled 2026-10-30): no harm edges -> HOLD, system-wide.
        # A sentence presenting no actioner-caused harm structure is
        # not an approval; it HOLDs as no-event, recorded. This guard
        # lives here, in the one shared implementation, so every
        # caller inherits the ruled verdict.
        return ("HOLD", False, "no_edges_no_event", total)
    if intrinsic:
        return ("REFUSE", False, None, total)
    any_at_gate = any_edge_at_gate(harms)
    total_at_gate = pair_ge(total, GATE_LIMIT)
    if consequence_present:
        if any_at_gate or total_at_gate:
            return ("REFUSE", False, None, total)
        return ("APPROVE", False, None, total)
    if any_at_gate or total_at_gate:
        if closure:
            return ("REFUSE", True, "closure_unknown_high_harm", total)
        return ("HOLD", False, "consequence_missing_high_harm", total)
    return ("APPROVE", False, None, total)
