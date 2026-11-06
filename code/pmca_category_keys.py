"""pmca_category_keys.py — SHA-256 category keys for PMCA.

Category-key design: Jonathan Wayne Fleuren, 2026-10-20 — one SHA-256
key per semantic category; the key holds the category's whole member
set ("This key holds all animals. This holds all mammals. This one
holds all insects."). Membership is derived from the system's own
stored structure — here, the seed entry "category" field — the set is
computed ONCE (traversed at build time), then pulled by key with
verification. Stage output is frozen: a changed set is a NEW version
with a NEW key; an address-side pointer names the live key (an address
locates; a key proves).

Assembled from Jonathan's persistence / falsifiability hash patterns
(sealed canonical bytes, sha256 commitments, previous generations kept
beside the current one). Built by Alder's hive in the DUPLICATE tree
(pmca_argon_test) only, per Jonathan's build method: the running
system is never modified in place.

Rules: Python 3 stdlib only. Deterministic. No floats anywhere in a
keyed payload (canonical JSON of a float is producer-dependent, so a
float in the payload is a hard error, never silently converted).
"""

import copy
import hashlib
import json


class TamperError(Exception):
    """Stored bytes do not hash to the key they were pulled under."""


def _assert_no_floats(obj, path="payload"):
    if isinstance(obj, float):
        raise ValueError("float in keyed payload at %s: %r" % (path, obj))
    if isinstance(obj, dict):
        for k in sorted(obj):
            _assert_no_floats(obj[k], path + "." + str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_no_floats(v, "%s[%d]" % (path, i))


def canonical_bytes(payload):
    """The one byte representation of a payload: JSON, keys sorted,
    tight separators, ASCII-escaped. Same payload -> same bytes ->
    same key, on any machine, in any process."""
    _assert_no_floats(payload)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)
    return text.encode("utf-8")


def _edge_sort_key(edge):
    return json.dumps(edge, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def build_category(seed, category):
    """Derive category's member set from the seed and key it.

    Membership rule (the seed as it actually is): a word is a member
    of category C iff its entry's "category" field == C. The keyed
    payload carries the sorted member list plus the seed edges whose
    source AND target are both members, plus edges between a member
    and the category word itself when the category word is an entry.

    An unknown / unpopulated category is NOT an error: it builds a
    valid keyed empty set (members [], edges []), distinguishable by
    member_count == 0.
    """
    entries = seed["entries"]
    members = sorted(w for w, e in entries.items()
                     if isinstance(e, dict) and e.get("category") == category)
    member_set = frozenset(members)
    category_is_entry = category in entries

    edges = []
    for edge in seed["edges"]:
        src, dst = edge["source"], edge["target"]
        both_members = src in member_set and dst in member_set
        member_to_category_word = category_is_entry and (
            (src in member_set and dst == category)
            or (dst in member_set and src == category))
        if both_members or member_to_category_word:
            edges.append(dict(edge))
    edges.sort(key=_edge_sort_key)

    payload = {"category": category, "members": members, "edges": edges}
    key = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return {
        "category": category,
        "key": key,
        "member_count": len(members),
        "members": members,
        "edges": edges,
    }


class CategoryStore:
    """Key-addressed store of frozen category payloads.

    blobs: key -> canonical payload bytes (every version kept).
    live:  category -> key (the address-side pointer; an address
           locates, a key proves).
    """

    def __init__(self):
        self.blobs = {}
        self.live = {}

    def put(self, record):
        """Store a build_category record under its key. The record's
        payload is re-canonicalized and re-hashed here: a record whose
        stated key does not match its own content is refused."""
        if record["member_count"] != len(record["members"]):
            raise ValueError("member_count does not match members")
        payload = {"category": record["category"],
                   "members": list(record["members"]),
                   "edges": [dict(e) for e in record["edges"]]}
        blob = canonical_bytes(payload)
        if hashlib.sha256(blob).hexdigest() != record["key"]:
            raise TamperError("record key does not match record content")
        self.blobs[record["key"]] = blob
        return record["key"]

    def pull(self, key):
        """Return the payload stored under key ONLY if the stored
        bytes still hash to that key. Any alteration — one member
        renamed, one edge character flipped — raises TamperError."""
        blob = self.blobs[key]  # KeyError: no such key in the store
        if hashlib.sha256(blob).hexdigest() != key:
            raise TamperError("stored bytes do not hash to key %s" % key)
        return json.loads(blob.decode("utf-8"))

    def set_live(self, category, key):
        if key not in self.blobs:
            raise KeyError("cannot point live at unstored key %s" % key)
        self.live[category] = key

    def pull_live(self, category):
        return self.pull(self.live[category])


def survey(seed):
    """category value -> member count, for every entry category."""
    counts = {}
    for e in seed["entries"].values():
        if isinstance(e, dict) and "category" in e:
            counts[e["category"]] = counts.get(e["category"], 0) + 1
    return counts


def _most_populated(seed):
    counts = survey(seed)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


if __name__ == "__main__":
    import pmca_seed

    results = []

    def check(name, cond):
        results.append(cond)
        print(("PASS" if cond else "FAIL") + ": " + name)

    seed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    top = _most_populated(seed)

    rec = build_category(seed, top)
    check("build most populated category (%s, %d members)"
          % (top, rec["member_count"]), rec["member_count"] > 0)

    rec2 = build_category(seed, top)
    check("determinism: two builds, identical key", rec["key"] == rec2["key"])

    store = CategoryStore()
    store.put(rec)
    store.set_live(top, rec["key"])
    pulled = store.pull_live(top)
    check("pull round-trip equality",
          pulled == {"category": top, "members": rec["members"],
                     "edges": rec["edges"]})

    empty = build_category(seed, "zz_category_with_no_members")
    check("empty category builds a valid keyed empty set",
          empty["member_count"] == 0 and empty["members"] == []
          and empty["edges"] == [] and len(empty["key"]) == 64)
    store.put(empty)
    check("empty category pulls back as an empty set",
          store.pull(empty["key"])["members"] == [])

    passed = sum(1 for r in results if r)
    print("%d/%d checks passed" % (passed, len(results)))
    print("Determinism (two builds identical): %s" % (rec["key"] == rec2["key"]))
