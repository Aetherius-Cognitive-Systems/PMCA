"""check_graphspace.py - checks for pmca_graphspace.py (rewrite stage 1).

Plain asserts with a printed PASS/FAIL count; exits nonzero on any failure.
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pmca_graphspace import GraphSpace, GlueConflict, dist_address

RESULTS = []


def check(name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - harness records any failure
        RESULTS.append(False)
        print("FAIL %s: %r" % (name, exc))
    else:
        RESULTS.append(True)
        print("PASS %s" % name)


def expect_raises(fn, exc_type=Exception):
    try:
        fn()
    except exc_type:
        return
    raise AssertionError("expected %s, nothing raised" % exc_type.__name__)


def t_bad_dimension_raises():
    s = GraphSpace("seed")
    expect_raises(lambda: s.produce_graph((1, "a", 1), "seed", "build"), ValueError)
    expect_raises(lambda: s.produce_graph((1, "AA", 1), "seed", "build"), ValueError)
    expect_raises(lambda: s.produce_graph((1, "1", 1), "seed", "build"), ValueError)
    expect_raises(lambda: s.produce_graph((1, "", 1), "seed", "build"), ValueError)
    expect_raises(lambda: s.produce_graph(("x", "A", 1), "seed", "build"), ValueError)
    expect_raises(lambda: s.produce_graph([1, "A", 1], "seed", "build"), ValueError)
    assert s.addresses() == []


def t_sparse_production():
    s = GraphSpace("seed")
    s.produce_graph((16, "X", 58), "seed", "build")
    assert (16, "X", 56) not in s.graphs
    assert (16, "X", 57) not in s.graphs
    assert s.addresses() == [(16, "X", 58)]
    assert s.graphs[(16, "X", 58)]["entries"] == {}
    assert s.graphs[(16, "X", 58)]["edges"] == []


def t_negative_layer_and_index():
    s = GraphSpace("own")
    s.produce_graph((-3, "B", -7), "own", "system")
    s.produce_graph((-1, "Z", 0), "own", "system")
    assert s.addresses() == [(-3, "B", -7), (-1, "Z", 0)]


def t_collision_raises():
    s = GraphSpace("seed")
    s.produce_graph((1, "A", 1), "seed", "build")
    expect_raises(lambda: s.produce_graph((1, "A", 1), "seed", "build"), ValueError)
    assert s.addresses() == [(1, "A", 1)]


def t_place_lookup_round_trip():
    s = GraphSpace("seed")
    s.produce_graph((1, "T", 5), "seed", "build")
    entry = {"word": "cup", "harm": 1}
    s.place_entry((1, "T", 5), "cup", entry)
    assert s.lookup("cup") == entry
    assert s.home_of("cup") == (1, "T", 5)
    assert s.graphs[(1, "T", 5)]["entries"]["cup"] == entry


def t_missing_lookup_none():
    s = GraphSpace("seed")
    assert s.lookup("nonexistent") is None
    assert s.home_of("nonexistent") is None


def t_glue_agreement():
    s = GraphSpace("seed")
    s.produce_graph((1, "A", 1), "seed", "build")
    s.produce_graph((16, "X", 58), "seed", "build")
    entry = {"word": "alpha", "value": 3}
    s.place_entry((1, "A", 1), "alpha", entry)
    s.place_entry((16, "X", 58), "alpha", dict(entry))  # equal value, new object
    assert s.graphs[(16, "X", 58)]["entries"]["alpha"] == {"glued": (1, "A", 1)}
    assert s.lookup("alpha") == entry
    assert s.home_of("alpha") == (1, "A", 1)


def t_glue_conflict():
    s = GraphSpace("seed")
    s.produce_graph((1, "A", 1), "seed", "build")
    s.produce_graph((2, "A", 1), "seed", "build")
    s.place_entry((1, "A", 1), "beta", {"word": "beta", "value": 1})
    expect_raises(
        lambda: s.place_entry((2, "A", 1), "beta", {"word": "beta", "value": 2}),
        GlueConflict,
    )
    assert "beta" not in s.graphs[(2, "A", 1)]["entries"]
    assert s.lookup("beta") == {"word": "beta", "value": 1}


def t_bootstrap():
    s = GraphSpace("seed")
    created = s.bootstrap("seed")
    assert created == [(1, "A", 1), (2, "A", 1)]
    assert s.addresses() == [(1, "A", 1), (2, "A", 1)]
    for addr in ((1, "A", 1), (2, "A", 1)):
        graph = s.graphs[addr]
        assert graph["entries"] == {}
        assert graph["edges"] == []
        assert graph["meta"] == {"system": "seed", "created_by": "bootstrap"}


def t_dist_address():
    # Hand-computed from the spec formula: "a" -> code 97 -> 'T', (3 % 41) - 20 = -17
    assert dist_address("a") == (1, "T", -17)
    assert dist_address("a", layer=2) == (2, "T", -17)
    assert dist_address("") == (1, "A", -20)
    words = ["cup", "spine", "boulder", "chef", "breaks", "window", "child",
             "alpha", "beta", "zebra", "a", "I", "antidisestablishmentarianism"]
    for word in words:
        addr = dist_address(word)
        assert addr == dist_address(word)  # determinism
        assert addr[0] == 1
        assert len(addr[1]) == 1 and "A" <= addr[1] <= "Z"
        assert -20 <= addr[2] <= 20


def build_sample():
    s = GraphSpace("seed")
    s.bootstrap("seed")
    s.produce_graph((16, "X", 58), "seed", "build")
    s.produce_graph((-1, "Z", -3), "seed", "build")
    entry = {"word": "alpha", "value": 3}
    s.place_entry((1, "A", 1), "alpha", entry)
    s.place_entry((16, "X", 58), "alpha", dict(entry))  # glue across graphs
    for word in ["cup", "spine", "boulder", "chef"]:
        addr = dist_address(word)
        if addr not in s.graphs:
            s.produce_graph(addr, "seed", "build")
        s.place_entry(addr, word, {"word": word})
    return s


def t_canonical_identical_independent_builds():
    a = build_sample()
    b = build_sample()
    assert isinstance(a.canonical_bytes(), bytes)
    assert a.canonical_bytes() == b.canonical_bytes()


def t_full_determinism_same_process():
    first = build_sample().canonical_bytes()
    for _ in range(3):
        assert build_sample().canonical_bytes() == first


def main():
    check("bad_dimension_raises", t_bad_dimension_raises)
    check("sparse_production", t_sparse_production)
    check("negative_layer_and_index", t_negative_layer_and_index)
    check("collision_raises", t_collision_raises)
    check("place_lookup_round_trip", t_place_lookup_round_trip)
    check("missing_lookup_none", t_missing_lookup_none)
    check("glue_agreement", t_glue_agreement)
    check("glue_conflict", t_glue_conflict)
    check("bootstrap", t_bootstrap)
    check("dist_address", t_dist_address)
    check("canonical_identical_independent_builds", t_canonical_identical_independent_builds)
    check("full_determinism_same_process", t_full_determinism_same_process)
    digest = hashlib.md5(build_sample().canonical_bytes()).hexdigest()
    print("canonical_md5: %s" % digest)
    passed = sum(1 for ok in RESULTS if ok)
    print("PASSED %d/%d" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
