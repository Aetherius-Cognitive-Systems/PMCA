"""PMCA definition cross-referencing PROTOTYPE (testing only, not system code).

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's architecture.

Purpose: TEST the logic of harm determination via definition
cross-referencing BEFORE committing it to the system. Jonathan's logic
(reapproached 2026-10-15):

  A word's definition is LINKS to the coordinates of the words in that
  definition. Meaning is a graph, not text. To determine if a word
  causes harm, the system follows those links: word -> definition
  words -> their coordinates -> their relationships -> harm.

  The human teaches (explains); the system encodes the explanation
  as relationships; then it cross-references definitions to determine
  harm for other words. Relationships are built only after passing
  the whole system. Scoring is the output of cross-referencing,
  not assigned by a human.

  Definition links MUST carry the RELATIONSHIP (how the word relates
  to its definition words: is_a, causes, treats, prevents, used_for,
  made_of, part_of, describes). Without it, cross-referencing
  mis-scores: surgery TREATS injury, it does not CAUSE it.

Failure modes this prototype tests:
  1. Cycles (loop_a <-> loop_b): must terminate via visited set.
  2. Explosion: must stop at MAX_DEPTH.
  3. Surgery problem: treats/prevents links must NOT transmit harm.
  4. Cold start: words with no transmitting path -> BENIGN/HOLD, not a guess.

Rules: Python 3, stdlib only. No max(), no floats in the decision
path, no random, no guessing. (The no-randomness claim is scoped to
the reasoning path: decisions are functions of stored structure and
inputs only; seal keys and nonces in pmca_persistence use OS
randomness by design, and wall-clock timestamps appear as record
metadata in some stores — neither enters a decision.) All harm
values come from SEED_HARM
(the "taught" ground truth). Everything else is DERIVED.
"""

import ast
import collections
import sys

# ---------------------------------------------------------------------------
# Test dictionary: word -> list of (definition_word, relationship_type).
# Relationship types: is_a, causes, treats, prevents, used_for,
# made_of, part_of, describes.
# ---------------------------------------------------------------------------

DEFINITIONS = {
    # Harm carriers (via is_a / causes paths to seed harm).
    "bomb": [("explosive", "is_a"), ("weapon", "is_a"),
             ("explosion", "causes")],
    "poison": [("substance", "is_a"), ("harm", "causes"),
               ("death", "causes")],
    "weapon": [("harm", "causes")],
    "explosive": [("explosion", "causes")],
    "injury": [("harm", "is_a")],
    # Benign: no transmitting path to any seed harm.
    "bread": [("food", "is_a"), ("flour", "made_of"),
              ("water", "made_of")],
    "food": [("nourishment", "is_a")],
    # Surgery problem: treats injury, does NOT cause it.
    "surgery": [("procedure", "is_a"), ("medical", "describes"),
                ("injury", "treats")],
    "knife": [("tool", "is_a"), ("sharp", "describes"),
              ("cutting", "used_for")],
    # Circular pair: must terminate, not crash.
    "loop_a": [("loop_b", "is_a")],
    "loop_b": [("loop_a", "is_a")],
    # Fillers: words with no harm links.
    "flour": [],
    "water": [],
    "tool": [],
    "sharp": [],
    "cutting": [],
    "procedure": [],
    "medical": [],
    "nourishment": [],
    "substance": [],
    "explosion": [],
    "harm": [],
    "death": [],
}

# ---------------------------------------------------------------------------
# Seed harm: the ONLY harm values the prototype knows. These stand in
# for relationships built by passing the whole system (teaching).
# Everything else must be DERIVED via cross-referencing, not hard-coded.
# Key: (source, target) -> harm int (0-10).
# ---------------------------------------------------------------------------

SEED_HARM = {
    ("weapon", "harm"): 8,
    ("injury", "harm"): 8,
    ("explosion", "harm"): 9,
    ("harm", "death"): 10,
    ("poison", "harm"): 9,
}

# Relationship types that TRANSMIT harm along a path. A word inherits
# harm from a definition word only when the link says the word IS that
# thing (is_a) or CAUSES it (causes). treats / prevents / used_for /
# made_of / part_of / describes do NOT transmit harm: they block the path.
HARM_TRANSMITTING = frozenset({"is_a", "causes"})


def cross_reference(word, definitions=None, seed_harm=None, max_depth=4):
    """Determine a word's harm score by cross-referencing definitions.

    BFS from `word` through definition links. Visited set handles
    cycles; max_depth controls explosion. A path transmits harm only
    if EVERY link on it has a relationship type in HARM_TRANSMITTING.
    A seed harm edge (source, target) counts only when both ends are
    on a transmitting path from `word`.

    Returns (score, path, status):
      status "SCORED": a transmitting path reached a seed harm edge.
        score = that edge's harm value; path = the word chain.
      status "BENIGN": word is known but no transmitting path reaches
        any seed harm within max_depth (only blocked links found, or
        no harm anywhere reachable).
      status "HOLD": word not in definitions, or it has no definition
        links at all. score None.
    """
    defs = DEFINITIONS if definitions is None else definitions
    harm = SEED_HARM if seed_harm is None else seed_harm
    if word not in defs:
        return (None, [word], "HOLD")
    if not defs[word]:
        return (None, [word], "HOLD")

    # BFS queue: (current_word, path_so_far, transmitting_so_far).
    # transmitting_so_far is False if any link on the path so far was
    # non-transmitting (the path is blocked and cannot score).
    visited = set()
    queue = collections.deque()
    queue.append((word, [word], True))
    visited.add(word)

    # Track the first (shortest) transmitting seed edge found.
    while queue:
        current, path, transmitting = queue.popleft()
        depth = len(path) - 1
        if depth >= max_depth:
            continue
        for def_word, rel in defs.get(current, []):
            link_transmits = rel in HARM_TRANSMITTING
            new_transmitting = transmitting and link_transmits
            new_path = path + [def_word]
            # Seed harm edge check: (current, def_word) on a
            # transmitting path. The edge must be reached via a
            # transmitting chain from `word` (including this link).
            if new_transmitting and (current, def_word) in harm:
                return (harm[(current, def_word)], new_path, "SCORED")
            # Also check the reverse direction: an edge (def_word,
            # current) on a transmitting path counts the same way.
            if new_transmitting and (def_word, current) in harm:
                return (harm[(def_word, current)], new_path, "SCORED")
            if def_word not in visited:
                visited.add(def_word)
                queue.append((def_word, new_path, new_transmitting))
    return (None, [word], "BENIGN")


# ---------------------------------------------------------------------------
# Static checks (same shape as the other PMCA modules).
# ---------------------------------------------------------------------------

def check_no_max():
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "max":
                return False
    return True


def check_no_floats():
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            return False
    return True


def check_no_random():
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "random":
                    return False
        if isinstance(node, ast.ImportFrom):
            if node.module == "random":
                return False
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "random":
                return False
    return True


def check_no_forbidden_word():
    text = open(__file__).read().lower()
    # Forbidden guessing verb, built by concatenation so the literal
    # does not appear in this file and trip its own check.
    return ("ass" + "ume") not in text


def _check(name, cond, results):
    results.append(cond)
    print(("PASS: " if cond else "FAIL: ") + name)
    return cond


def run_tests():
    results = []

    # 1. bomb -> SCORED, score >= 8 (via weapon/explosion, is_a/causes).
    score, path, status = cross_reference("bomb")
    _check("bomb -> SCORED", status == "SCORED", results)
    _check("bomb score >= 8", score is not None and score >= 8, results)
    print("  bomb path: " + " -> ".join(path) + f" (score {score})")

    # 2. bread -> BENIGN (food/flour/water, no transmitting path).
    score, path, status = cross_reference("bread")
    _check("bread -> BENIGN", status == "BENIGN", results)
    _check("bread score None", score is None, results)

    # 3. Surgery problem: treats injury must NOT transmit harm.
    score, path, status = cross_reference("surgery")
    _check("surgery -> BENIGN (treats does not transmit)", status == "BENIGN", results)
    _check("surgery score None (no mis-score)", score is None, results)

    # 4. knife -> BENIGN or HOLD, must NOT score harm.
    score, path, status = cross_reference("knife")
    _check("knife -> BENIGN or HOLD", status in ("BENIGN", "HOLD"), results)
    _check("knife score None (no harm)", score is None, results)

    # 5. poison -> SCORED, score >= 9 (causes harm/death).
    score, path, status = cross_reference("poison")
    _check("poison -> SCORED", status == "SCORED", results)
    _check("poison score >= 9", score is not None and score >= 9, results)
    print("  poison path: " + " -> ".join(path) + f" (score {score})")

    # 6. Circular pair terminates, no crash, not SCORED.
    for w in ("loop_a", "loop_b"):
        score, path, status = cross_reference(w)
        _check(f"{w} terminates, not SCORED",
                status in ("BENIGN", "HOLD") and score is None, results)

    # 7. Unknown word -> HOLD, score None.
    score, path, status = cross_reference("florp")
    _check("unknown word -> HOLD", status == "HOLD", results)
    _check("unknown word score None", score is None, results)

    # 8. Depth limit: chain longer than max_depth must NOT be followed.
    deep_defs = dict(DEFINITIONS)
    deep_defs["deep0"] = [("d1", "is_a")]
    deep_defs["d1"] = [("d2", "is_a")]
    deep_defs["d2"] = [("d3", "is_a")]
    deep_defs["d3"] = [("d4", "is_a")]
    deep_defs["d4"] = [("d5", "is_a")]
    deep_defs["d5"] = [("harm", "is_a")]
    deep_harm = dict(SEED_HARM)
    deep_harm[("d5", "harm")] = 7
    # d5 -> harm edge sits at depth 5 from deep0; max_depth=3 must not reach it.
    score, path, status = cross_reference("deep0", deep_defs, deep_harm,
                                           max_depth=3)
    _check("deep chain beyond max_depth -> not SCORED",
            status in ("BENIGN", "HOLD") and score is None, results)
    # Same chain WITH enough depth must reach it (control: depth works).
    score2, _p2, status2 = cross_reference("deep0", deep_defs, deep_harm,
                                           max_depth=6)
    _check("deep chain within max_depth -> SCORED (control)",
            status2 == "SCORED" and score2 == 7, results)

    # 9. Determinism: two runs identical.
    r1 = [cross_reference(w) for w in ("bomb", "bread", "surgery",
                                       "knife", "poison", "loop_a", "florp")]
    r2 = [cross_reference(w) for w in ("bomb", "bread", "surgery",
                                       "knife", "poison", "loop_a", "florp")]
    _check("determinism: two runs identical", r1 == r2, results)

    # 10. Static checks.
    _check("static: no maximum call anywhere (AST)", check_no_max(), results)
    _check("static: no float literal anywhere (AST)", check_no_floats(), results)
    _check("static: random never used (AST)", check_no_random(), results)
    _check("static: no forbidden guessing verb (grep)",
            check_no_forbidden_word(), results)

    passed = sum(1 for r in results if r)
    print(f"{passed}/{len(results)} tests passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(run_tests())
