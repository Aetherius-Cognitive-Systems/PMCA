"""pmca_cluster_builder.py — Directory-tree cluster builder (slice 1).

Architecture and design: Jonathan Wayne Fleuren (the builder).
Tree cluster design: JONATHAN, 2026-10-20 — "treat the cluster builder
like a directory tree builder: a parent to a child, which is a parent
to a child. The more prominent the use of the word is, the higher up
in [the hierarchy] it is; the use of the word through relationship
values or the subcoordinates links the word deeper into the system."
Built by Alder's hive, in the DUPLICATE tree only (pmca_argon_test);
the running system is never modified in place.

WHAT THIS MODULE DOES
Organizes every word appearing in a set of definition clusters into
ONE directory tree, each word filed exactly once:

- Input clusters are edge tuples in pmca_weight_learner's format:
  (source, relation, target) with relation in RELATIONS.
- PROMINENCE of a word = the number of relationship links touching
  it (every stored edge it appears in, on either end). This is the
  "use counted through relationship values": the more a word is used
  by the structure around it, the higher it sits.
- A word's TREE PARENT is chosen from its outgoing "presupposes"
  targets (what the word hangs from): the target with the highest
  prominence; ties are broken alphabetically, so the choice is
  deterministic. A word with no presupposes target is a root, filed
  under the single synthetic handle CLUSTER_ROOT ("cluster_root"),
  which is a filing handle only: it is not a word, carries no
  prominence, and appears in no exported edge.
- The chosen parent edge (word, "presupposes", parent) is the word's
  tree-parent edge. EVERY other input edge is stored on its SOURCE
  word's entry as a LINK (a subcoordinate), as the full original
  tuple. Storage is therefore lossless: every input edge is stored
  exactly once, either as a tree-parent edge or as a link.

CLUSTER RECONSTRUCTION (tree.cluster_edges) — read this before
assuming a narrower neighborhood. The severing law
(pmca_weight_learner.sever_collapse_analysis) traces every dependent
of the target toward a terminal node and compares the target's
topological signature against every sibling's signature across the
whole connected structure. Any truncated neighborhood manufactures
false collapses: e.g. under a {word + children + parent} neighborhood,
"cup" loses the edges that give "mug" its matching signature, the
Axiom 3 SUBSTITUTES path vanishes, and cup misderives as
(2, non_replaceable) instead of (1, replaceable). Because storage
above is lossless, the connected cluster a word arrived in is
exactly recoverable, so cluster_edges(word) returns it: every stored
edge tuple in the connected component containing `word`
(connectivity over the stored edges, either end), deduplicated and
sorted. The tree is the filing system (parent, children, path,
prominence); the component is the cluster the severing law reads.

CYCLES: presupposes edges can in principle form a loop (A hangs from
B, B hangs from A). A directory tree cannot contain a loop, so after
parent selection any cycle is broken deterministically: the
alphabetically first word in the cycle is re-filed as a root. Its
presupposes edge is NOT deleted; it remains stored as a link on its
entry, so cluster_edges still reconstructs the original structure.

Deterministic: same edges in any input order produce the identical
tree. Integers only, no randomness, no floats, stdlib only.
"""

import collections

# The relation vocabulary of the learner's edge format.
RELATIONS = ("causes", "presupposes", "omissible", "renews", "repairs")

# Filing handle for roots. Not a word; never exported as one.
CLUSTER_ROOT = "cluster_root"


def _validate_edge(edge):
    """Coerce one input edge to a (source, relation, target) tuple of
    strings, or raise ValueError. Deterministic and total: bad input
    is a loud failure here, never a silent guess downstream."""
    if not isinstance(edge, (tuple, list)) or len(edge) != 3:
        raise ValueError("edge must be a (source, relation, target) triple: %r"
                         % (edge,))
    source, relation, target = edge
    if not (isinstance(source, str) and isinstance(relation, str)
            and isinstance(target, str)):
        raise ValueError("edge parts must be strings: %r" % (edge,))
    if relation not in RELATIONS:
        raise ValueError("unknown relation %r in edge %r" % (relation, edge))
    return (source, relation, target)


class ClusterTree:
    """One directory tree over a stored edge set. Construct via
    build_tree(); the constructor takes the already-computed maps so
    that build order lives in exactly one place."""

    def __init__(self, words, prominence, parents, links, all_edges):
        self._words = words                # sorted tuple of real words
        self._prominence = prominence      # word -> int
        self._parents = parents            # word -> word or CLUSTER_ROOT
        self._links = links                # word -> sorted tuple of edges
        self._all_edges = all_edges        # sorted tuple of every stored edge
        children = {}
        for word in words:
            children.setdefault(parents[word], []).append(word)
        self._children = {}
        for holder in children:
            self._children[holder] = tuple(sorted(children[holder]))
        # Undirected adjacency over the stored edges, for component
        # reconstruction in cluster_edges().
        adjacency = {}
        for source, _relation, target in all_edges:
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
        self._adjacency = adjacency

    # ---------------------------------------------------------- reads
    def words(self):
        """Every filed word, sorted. CLUSTER_ROOT is not a word and
        is never listed."""
        return self._words

    def prominence(self, word):
        """Relationship-link count touching `word` (either end of
        every stored edge). 0 if the word is absent (or is the
        CLUSTER_ROOT handle)."""
        return self._prominence.get(word, 0)

    def parent(self, word):
        """The word's tree parent: the highest-prominence presupposes
        target it hangs from. Roots return CLUSTER_ROOT. Absent words
        return None (they are not filed anywhere)."""
        if word == CLUSTER_ROOT or word not in self._parents:
            return None
        return self._parents[word]

    def children(self, word):
        """Sorted tuple of the words filed directly under `word`.
        children(CLUSTER_ROOT) is the sorted tuple of roots. Absent
        words and leaves return ()."""
        return self._children.get(word, ())

    def path_to_root(self, word):
        """Tuple from `word` up to and including its root word.
        CLUSTER_ROOT is a filing handle and is excluded: the path
        ends at the real root word. Absent words return ()."""
        if word not in self._parents:
            return ()
        path = [word]
        current = self._parents[word]
        while current != CLUSTER_ROOT:
            path.append(current)
            current = self._parents[current]
        return tuple(path)

    def parent_edge(self, word):
        """The word's tree-parent edge (word, "presupposes", parent),
        or None for roots and absent words."""
        holder = self.parent(word)
        if holder is None or holder == CLUSTER_ROOT:
            return None
        return (word, "presupposes", holder)

    def links(self, word):
        """Sorted tuple of the non-tree edges stored on `word`'s
        entry (its subcoordinate links), each the full original
        (source, relation, target) tuple with source == word."""
        return self._links.get(word, ())

    def cluster_edges(self, word):
        """Reconstruct the cluster the severing law reads for `word`:
        every stored edge tuple in the connected component containing
        `word` (connectivity over the stored edges, either end),
        deduplicated and sorted. See the module docstring for why the
        component, not a truncated neighborhood, is the contract.
        Absent words return ()."""
        if word not in self._adjacency:
            return ()
        component = {word}
        queue = collections.deque([word])
        while queue:
            current = queue.popleft()
            for neighbor in self._adjacency[current]:
                if neighbor not in component:
                    component.add(neighbor)
                    queue.append(neighbor)
        return tuple(edge for edge in self._all_edges
                     if edge[0] in component)


def _break_cycles(parents):
    """Re-file the alphabetically first word of any parent cycle as a
    root, until the parent map is a forest. Deterministic: words are
    scanned in sorted order and each break restarts the scan."""
    while True:
        broke = False
        for word in sorted(parents):
            seen = {}
            chain = []
            current = word
            while current != CLUSTER_ROOT and current not in seen:
                seen[current] = len(chain)
                chain.append(current)
                current = parents[current]
            if current != CLUSTER_ROOT:
                cycle = chain[seen[current]:]
                parents[sorted(cycle)[0]] = CLUSTER_ROOT
                broke = True
                break
        if not broke:
            return parents


def build_tree(edge_sets):
    """Build the one directory tree over the given clusters.

    edge_sets: an iterable of (word, cluster_edges) pairs, where
    cluster_edges is an iterable of (source, relation, target)
    tuples in pmca_weight_learner's format. The declared word of a
    pair is filed even if it appears in no edge. Identical inputs in
    any order produce an identical tree.
    """
    all_edges = set()
    declared = set()
    for pair in edge_sets:
        word, cluster_edges = pair
        if not isinstance(word, str):
            raise ValueError("cluster word must be a string: %r" % (word,))
        declared.add(word)
        for edge in cluster_edges:
            all_edges.add(_validate_edge(edge))

    words = set(declared)
    for source, _relation, target in all_edges:
        words.add(source)
        words.add(target)

    # Prominence: use counted through relationship values — the
    # number of stored edges a word appears in, on either end (an
    # edge counts once per word, even a self-loop).
    prominence = {}
    for word in words:
        prominence[word] = 0
    for edge in all_edges:
        for word in {edge[0], edge[2]}:
            prominence[word] = prominence[word] + 1

    # Tree parents: chosen from outgoing presupposes targets (what
    # the word hangs from) — highest prominence, ties alphabetical.
    # A word never files under itself; a self-presupposes edge stays
    # stored as a link instead.
    parents = {}
    for word in words:
        targets = set()
        for source, relation, target in all_edges:
            if source == word and relation == "presupposes" and target != word:
                targets.add(target)
        if not targets:
            parents[word] = CLUSTER_ROOT
        else:
            ranked = sorted(targets, key=lambda t: (-prominence[t], t))
            parents[word] = ranked[0]
    parents = _break_cycles(parents)

    # Storage split: the chosen parent edge is the tree-parent edge;
    # every other edge is a link on its source word's entry.
    links = {}
    for word in words:
        links[word] = []
    for edge in all_edges:
        source, relation, target = edge
        if not (relation == "presupposes" and parents[source] == target):
            links[source].append(edge)
    for word in words:
        links[word] = tuple(sorted(links[word]))

    return ClusterTree(tuple(sorted(words)), prominence, parents, links,
                       tuple(sorted(all_edges)))


# ---------------------------------------------------------------------
# Self-check suite (house pattern: per-check PASS/FAIL lines + total).
# The fixtures are the walkthrough clusters of the weight learner.
# ---------------------------------------------------------------------

def _fixtures():
    cup_edges = (
        ("drink_liquid", "presupposes", "cup"),
        ("cup", "causes", "hold_liquid"),
        ("mug", "causes", "hold_liquid"),
        ("drink_liquid", "presupposes", "mug"),
    )
    window_edges = (
        ("wall_enclosure", "presupposes", "window"),
        ("window", "causes", "admit_light"),
        ("window", "repairs", "glaze_frame"),
    )
    spine_words = ("skeleton", "trunk", "head_support", "rib_cage",
                   "pelvis_link", "spinal_cord", "nerve_signal",
                   "posture", "locomotion")
    spine_edges = tuple((w, "presupposes", "spine") for w in spine_words) \
        + (("spine", "causes", "body_axis"),)
    plate_edges = (
        ("serve_food", "presupposes", "plate"),
        ("plate", "causes", "hold_food"),
        ("dish", "causes", "hold_food"),
        ("serve_food", "presupposes", "dish"),
    )
    return (("cup", cup_edges), ("window", window_edges),
            ("spine", spine_edges), ("plate", plate_edges))


def run_suite():
    import pmca_graphspace
    import pmca_weight_learner

    fixtures = _fixtures()
    by_word = dict(fixtures)
    results = []

    space = pmca_graphspace.GraphSpace("own")
    seed_examples = (
        {"word": "cup", "expected_weight": 1,
         "expected_class": "replaceable", "cluster_edges": by_word["cup"]},
        {"word": "window", "expected_weight": 2,
         "expected_class": "repairable", "cluster_edges": by_word["window"]},
        {"word": "spine", "expected_weight": 10,
         "expected_class": "non_replaceable",
         "cluster_edges": by_word["spine"]},
    )
    learned = pmca_weight_learner.learn_derivation_law(seed_examples,
                                                       space=space)
    results.append(("learn_law", learned["status"]))

    tree = build_tree(fixtures)
    for word, want in (("cup", (1, "replaceable")),
                       ("window", (2, "repairable")),
                       ("spine", (10, "non_replaceable")),
                       ("plate", (1, "replaceable"))):
        derived = pmca_weight_learner.derive_target_structure(
            word, tree.cluster_edges(word), space)
        actual = None if derived is None else (
            derived["weight"], derived["replaceability"])
        results.append(("derive_" + word, actual))

    results.append(("prominence_spine", tree.prominence("spine")))
    results.append(("prominence_cup", tree.prominence("cup")))

    reversed_tree = build_tree(tuple(reversed(fixtures)))
    same = True
    for word in tree.words():
        if (tree.parent(word) != reversed_tree.parent(word)
                or tree.children(word) != reversed_tree.children(word)
                or tree.prominence(word) != reversed_tree.prominence(word)):
            same = False
    for word in ("cup", "window", "spine", "plate"):
        if tree.cluster_edges(word) != reversed_tree.cluster_edges(word):
            same = False
    results.append(("order_independent", same))
    return results


def check_suite(results):
    expected = {
        "learn_law": "LEARNED",
        "derive_cup": (1, "replaceable"),
        "derive_window": (2, "repairable"),
        "derive_spine": (10, "non_replaceable"),
        "derive_plate": (1, "replaceable"),
        "prominence_spine": 10,
        "prominence_cup": 2,
        "order_independent": True,
    }
    checks = []
    for name, actual in results:
        want = expected[name]
        checks.append((name, actual == want, actual, want))
    return checks


def main():
    """Run suite twice, verify determinism, print results."""
    run1 = run_suite()
    run2 = run_suite()
    deterministic = (run1 == run2)
    checks = check_suite(run1)
    passed_count = 0
    for name, passed, actual, want in checks:
        if passed:
            passed_count = passed_count + 1
            print("PASS " + name + " -> " + str(actual))
        else:
            print("FAIL " + name + " -> got " + str(actual)
                  + " want " + str(want))
    print("Passed: " + str(passed_count) + " of " + str(len(checks)))
    print("Determinism (two runs identical): " + str(deterministic))
    if passed_count == len(checks) and deterministic:
        print("ALL CHECKS PASS")
    else:
        print("CHECKS FAILED")


if __name__ == "__main__":
    main()
