"""pmca_weight_learner.py — Deterministic Structural Weight & Class Learner.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Weight-derivation mechanism (Cycle-Review Severing Test + candidate
law selection by Coherence and Edge Economy): proposed by
Gemini 4 Argon, 2026-10-20; evaluated by Alder (it reproduces the
seed weights 1 / 2 / 10 and fails closed), and adopted for testing
under Jonathan's rulings. The replaceability class ladder is a
DESIGNED rule (Alder's delegated call, 2026-10-20), not learned;
one collision on a confirmed structure reopens it to candidate
competition. This copy lives in the DUPLICATE tree only
(pmca_argon_test), per Jonathan's build method: the running system
is never modified in place; the duplicate is promoted only on a
full pass, else discarded.

Implements:
- Cycle-Review Severing Test ([C00#13]): remove node X in Sandbox;
  trace all connected nodes to their terminal coordinates (Problem 8).
- Axiom 2 (PRESUPPOSES), Axiom 3 (SUBSTITUTES), Axiom 5 (OMISSIBLE)
  topological trace evaluation (§3B.5, Problem 7).
- 1 + 1 Seed induction ([C17#2-3]) + Homological Mapping (Problem 20:
  requires >= 2 confirmed seed structures) + Properly selector
  (Problem 22: Coherence == 0 collisions, minimal Edge Economy).
- Verification Ladder Rungs 1-4 (Problem 23): Format -> Axiom ->
  Sandbox Simulation -> Live Integration into GraphSpace ("own").

Rules: Python 3 stdlib only. Integers only. No max(), no floats,
no random, no hardcoded semantic word lists, no guessing.

Flags (Jonathan 2026-10-20: "the learner has to learn from the
flags"): this module carries an honest scaffold for flag learning,
not a mechanism. Seed examples may carry "expected_flags"; a flag
mismatch counts as a collision in learn_derivation_law exactly like
a weight or class mismatch. But no flag model exists yet:
learn_flag_model HOLDs (flag_structure_unresolved) because the
flagged exemplars (man, child) have no clusters in any vocabulary,
and the one flagged word with stored structure (person) has
seed-graph edges whose roles have no ruled mapping into the learner
relations. derive_target_structure therefore reports flags () with
provenance "unresolved_no_flag_model" until a flag model is
lawfully induced. No predicate is hardcoded here.
"""

import ast
import collections
import pmca_graphspace


def terminal_nodes(cluster_edges):
    all_nodes = set()
    has_outgoing_causative = set()
    for src, rel, dst in cluster_edges:
        all_nodes.add(src)
        all_nodes.add(dst)
        if rel in ("causes", "presupposes"):
            has_outgoing_causative.add(src)
    return frozenset(all_nodes - has_outgoing_causative)


def node_signature(node, cluster_edges):
    outgoing = []
    incoming = []
    for src, rel, dst in cluster_edges:
        if src == node and rel in ("causes", "presupposes"):
            outgoing.append((rel, dst))
        if dst == node and rel in ("causes", "presupposes"):
            incoming.append((rel, src))
    return (tuple(sorted(incoming)), tuple(sorted(outgoing)))


def has_substitutes_path(node, cluster_edges):
    sig_x = node_signature(node, cluster_edges)
    if not sig_x[0] and not sig_x[1]:
        return False
    nodes = set()
    for src, _, dst in cluster_edges:
        nodes.add(src)
        nodes.add(dst)
    for other in sorted(nodes):
        if other == node:
            continue
        if node_signature(other, cluster_edges) == sig_x:
            return True
    return False


def can_reach_terminal(start_node, severed_node, cluster_edges, terminals):
    if start_node == severed_node:
        return (False, 0)

    substitute_available = has_substitutes_path(severed_node, cluster_edges)

    omissible_targets = set()
    for src, rel, dst in cluster_edges:
        if src == start_node and rel == "omissible":
            omissible_targets.add(dst)

    visited = {start_node}
    queue = collections.deque([(start_node, 0)])
    steps = 0

    while queue:
        curr, dist = queue.popleft()
        steps = steps + 1
        if curr in terminals:
            return (True, steps)
        for src, rel, dst in cluster_edges:
            if src != curr:
                continue
            if rel not in ("causes", "presupposes"):
                continue
            if dst == severed_node:
                if severed_node in omissible_targets or substitute_available:
                    return (True, steps + 1)
                continue
            if dst not in visited:
                visited.add(dst)
                queue.append((dst, dist + 1))

    return (False, steps)


def sever_collapse_analysis(target, cluster_edges):
    if not cluster_edges:
        return None

    terminals = terminal_nodes(cluster_edges)
    dependents = set()
    has_repair = False
    has_renewal = False

    for src, rel, dst in cluster_edges:
        if dst == target and rel == "presupposes" and src != target:
            dependents.add(src)
        if src == target and rel == "repairs":
            has_repair = True
        if src == target and rel == "renews":
            has_renewal = True

    has_sub = has_substitutes_path(target, cluster_edges)

    collapsed_count = 0
    total_steps = 1
    for dep in sorted(dependents):
        reached, steps = can_reach_terminal(
            dep, target, cluster_edges, terminals)
        total_steps = total_steps + steps
        if not reached:
            collapsed_count = collapsed_count + 1

    return {
        "collapsed_count": collapsed_count,
        "dependent_count": len(dependents),
        "raw_edge_count": len(cluster_edges),
        "has_substitute": has_sub,
        "has_repair": has_repair,
        "has_renewal": has_renewal,
        "trace_steps": total_steps,
    }


def _candidate_raw_edges(metrics):
    return (1 + metrics["raw_edge_count"], metrics["raw_edge_count"] + 1)


def _candidate_raw_dependents(metrics):
    return (1 + metrics["dependent_count"], metrics["dependent_count"] + 1)


def _candidate_axiom_collapse(metrics):
    return (1 + metrics["collapsed_count"], metrics["trace_steps"])


CANDIDATE_WEIGHT_LAWS = (
    ("raw_edge_sum", _candidate_raw_edges),
    ("unfiltered_dependent_sum", _candidate_raw_dependents),
    ("axiom_collapse_sum", _candidate_axiom_collapse),
)


def derive_replaceability_class(metrics):
    if metrics is None:
        return None
    if metrics["has_substitute"]:
        return "replaceable"
    if metrics["has_renewal"]:
        return "regrowable"
    if metrics["has_repair"]:
        return "repairable"
    if metrics["collapsed_count"] > 0:
        return "non_replaceable"
    return None


# ---------------------------------------------------------------------
# Flags (scaffold — Jonathan 2026-10-20: "the learner has to learn
# from the flags"). No flag model exists yet. A future candidate
# competition over flag predicates plugs into learn_flag_model and
# yields a callable flag_model(word, cluster_edges) -> tuple of flags,
# which learn_derivation_law already accepts without a signature
# change.
# ---------------------------------------------------------------------

# Candidate flag predicates: (name, callable(word, cluster_edges)).
# EMPTY in this slice — no predicate has been lawfully induced, and
# none is hardcoded. learn_flag_model competes these over flagged
# training examples once both exist.
CANDIDATE_FLAG_PREDICATES = ()


def predict_flags(word, cluster_edges, flag_model):
    """Predict the flags for a word from its cluster, under a flag
    model. With no model (flag_model None — the only state that
    exists in this slice), the honest prediction is the empty tuple:
    no flags are claimed for any word."""
    if flag_model is None:
        return ()
    return tuple(flag_model(word, cluster_edges))


def learn_flag_model(seed_examples):
    """Attempt to induce a flag model from seed examples.

    Induction is attempted ONLY from examples carrying BOTH
    cluster_edges (in the learner relation vocabulary) AND
    expected_flags. With the currently available exemplars there is
    no valid training set — the flagged words (man, child) have no
    clusters anywhere, and person's stored structure is seed-graph
    edges in a relation vocabulary with no ruled mapping into the
    learner relations — so this HOLDs rather than fabricating a
    predicate. When flagged clusters and candidate predicates both
    exist, the competition runs here (Coherence: 0 collisions on the
    training set, then Edge Economy), mirroring learn_derivation_law,
    and the winner is returned as {"status": "LEARNED", ...}.
    """
    training = [ex for ex in seed_examples
                if ex.get("cluster_edges")
                and tuple(ex.get("expected_flags", ()))]
    if not training:
        return {
            "status": "HOLD",
            "reason": "flag_structure_unresolved",
            "detail": ("no seed example carries both cluster_edges and "
                       "expected_flags; the flagged exemplars (man, "
                       "child) have no clusters in any vocabulary, and "
                       "person's seed-graph edges use roles with no "
                       "ruled mapping into the learner relations "
                       "(causes, presupposes, omissible, renews, "
                       "repairs); flag induction has no lawful training "
                       "set"),
        }
    if not CANDIDATE_FLAG_PREDICATES:
        return {
            "status": "HOLD",
            "reason": "flag_structure_unresolved",
            "detail": ("flagged cluster examples exist, but no "
                       "candidate flag predicates have been implemented "
                       "to compete over them; no flag model can be "
                       "induced in this slice"),
        }
    # Future slice: candidate competition over CANDIDATE_FLAG_PREDICATES
    # on `training`, selected by Coherence then Edge Economy.
    return {
        "status": "HOLD",
        "reason": "flag_structure_unresolved",
        "detail": "candidate flag predicate competition is not built",
    }


def extract_person_exemplar():
    """Package the one flagged word with stored structure as evidence.

    Reads the seed graph (pmca_seed.build_seed over DEMO_DICTIONARY)
    and returns the "person" entry with every edge touching it,
    verbatim — seed roles UNMAPPED (no ruled mapping into the learner
    relations exists, and none is invented here). This is evidence
    packaging for a future flag induction, not a learner cluster.
    """
    import pmca_seed
    seed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    entry = seed["entries"]["person"]
    edges = [e for e in seed["edges"]
             if e["source"] == "person" or e["target"] == "person"]
    return {
        "word": "person",
        "entry": entry,
        "edges": edges,
        "expected_flags": ("person",),
    }


def learn_derivation_law(seed_examples, space=None, flag_model=None):
    import json
    # PROM-1 (2026-10-29) — duplicated examples are one provenance; homological mapping requires two genuinely distinct confirming structures.
    distinct_examples = []
    _seen_keys = set()
    for _ex in seed_examples:
        _key = json.dumps(_ex, sort_keys=True, default=str)
        if _key not in _seen_keys:
            _seen_keys.add(_key)
            distinct_examples.append(_ex)
    if len(distinct_examples) < 2:
        return {"status": "HOLD", "reason": "homological_mapping_requires_two_examples"}

    passing_laws = []
    flag_collisions = 0
    for law_name, law_fn in CANDIDATE_WEIGHT_LAWS:
        collisions = 0
        total_steps = 0
        for ex in distinct_examples:
            metrics = sever_collapse_analysis(ex["word"], ex["cluster_edges"])
            if metrics is None:
                collisions = collisions + 1
                continue
            derived_w, steps = law_fn(metrics)
            derived_cls = derive_replaceability_class(metrics)
            total_steps = total_steps + steps
            if derived_w != ex["expected_weight"]:
                collisions = collisions + 1
            if derived_cls != ex["expected_class"]:
                collisions = collisions + 1
            # Flags are part of coherence (Jonathan 2026-10-20: "the
            # learner has to learn from the flags"): a flag mismatch
            # is a collision exactly like a weight/class mismatch.
            expected_flags = tuple(ex.get("expected_flags", ()))
            if predict_flags(ex["word"], ex["cluster_edges"],
                             flag_model) != expected_flags:
                collisions = collisions + 1
                flag_collisions = flag_collisions + 1
        if collisions == 0:
            passing_laws.append((total_steps, law_name))

    if not passing_laws:
        if flag_collisions:
            return {"status": "HOLD",
                    "reason": "flag_collision_no_coherent_law"}
        return {"status": "HOLD", "reason": "no_coherent_derivation_law"}

    passing_laws.sort()
    best_steps, best_law_name = passing_laws[0]

    sandbox_check = sever_collapse_analysis("unmapped_node", ())
    if sandbox_check is not None:
        return {"status": "HOLD", "reason": "sandbox_fail_closed_violation"}

    if space is not None:
        key = "derivation_law:target_weight"
        entry = {
            "kind": "own_derivation_law",
            "key": key,
            "law_name": best_law_name,
            "confirmed_examples": len(distinct_examples),
            "coherence_collisions": 0,
            "edge_economy_steps": best_steps,
            "provenance": "own",
        }
        home = pmca_graphspace.dist_address(key)
        if home not in space.graphs:
            space.produce_graph(home, "own", "system")
        space.place_entry(home, key, entry)

    return {
        "status": "LEARNED",
        "law_name": best_law_name,
        "confirmed_examples": len(distinct_examples),
        "edge_economy_steps": best_steps,
    }


def derive_target_structure(word, cluster_edges, space):
    law_entry = space.lookup("derivation_law:target_weight")
    if not isinstance(law_entry, dict) or law_entry.get("kind") != "own_derivation_law":
        return None

    law_name = law_entry["law_name"]
    law_fn = None
    for name, fn in CANDIDATE_WEIGHT_LAWS:
        if name == law_name:
            law_fn = fn
            break
    if law_fn is None:
        return None

    metrics = sever_collapse_analysis(word, cluster_edges)
    if metrics is None:
        return None

    cls = derive_replaceability_class(metrics)
    if cls is None:
        return None

    weight, steps = law_fn(metrics)
    return {
        "word": word,
        "weight": weight,
        "replaceability": cls,
        "collapsed_dependents": metrics["collapsed_count"],
        "trace_steps": steps,
        "provenance": "own_derived:" + law_name,
        # No flag model has been lawfully induced yet (see
        # learn_flag_model); report the honest state, claim no flags.
        "flags": (),
        "flags_provenance": "unresolved_no_flag_model",
    }
