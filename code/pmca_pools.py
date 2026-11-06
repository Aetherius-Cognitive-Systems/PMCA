"""PMCA pool store — append-only, hash-chained, crash-safe backing store
for the Sandbox, Chaos Pool, Pending Pool, and audit stores.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's architecture.

WHAT IT PROVES: the pool store mechanism works — records are write-once
and append-only (supersession is a new record pointing back, never an
edit or deletion, A5); every record is hash-chained to its predecessor
so tampering is detectable; appends are fsynced before returning (crash
safety); a word index enables the A8 sandbox-to-word skim; pool content
never touches the approved seed (separation, OPEN-10); replay is
deterministic.

OPEN-10 — RULED (Decision 3, 2026-10-15): pools are SEPARATE PHYSICAL
STORES. Many stores — one file per pool: sandbox.jsonl, chaos.jsonl,
pending.jsonl, audit.jsonl, each with its own hash chain (seq restarts
at 0 per file). Isolation: corruption in one pool does not affect the
others; each pool is manageable separately. This supersedes the earlier
one-file + namespaces implementation, which was a mechanism, not a
ruling.

WHAT IT DOES NOT PROVE:
- Not the production multi-base store (four files, single process).
- A8 wake-up itself is NOT implemented here. The word index enables the
  skim (find sandbox records mentioning a word); the wake-up trigger
  (any graph addition -> skim related sandbox items) is future work.
- Not distributed. One process only: separate processes opening
  the same directory are outside the envelope. Within the
  process, appends ARE transactional (Pack D, 2026-10-26): the
  store carries a reentrant lock (store.lock) and append holds
  it across seq allocation, predecessor selection, the
  write+fsync, and the in-memory append, so threads sharing one
  PoolStore cannot fork a chain or reissue a seq. Composite
  callers that must make several appends one atomic step (the
  analyzer's engagement open/append/close) hold store.lock
  across the whole operation.

RULES (from the blueprint):
- A5: nothing is ever deleted. Supersede marks, never edits.
- OPEN-10: pool content reaches the approved store ONLY through the
  teaching loop (teach() -> pending -> approve()). This module never
  writes to the seed.
- A8 (enabler): word index for the sandbox-to-word skim.
- Optional 'graph' field: when a caller supplies a graph address
  (layer, dimension, index), the record payload carries it as a
  JSON-safe list [layer, dimension, index]. When not supplied, the
  record is exactly as before: no 'graph' key, hash inputs unchanged.
"""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone

from pmca_graphspace import _validate_address as _validate_graph_address

NAMESPACES = ("sandbox", "chaos", "pending", "audit", "engagement")
GENESIS = "GENESIS"


def _canonical(obj):
    """Canonical JSON bytes for hashing: sorted keys, tight separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _record_hash(seq, namespace, timestamp, payload, prev_hash):
    """SHA-256 over the record fields excluding the hash itself."""
    return hashlib.sha256(_canonical({
        "seq": seq,
        "namespace": namespace,
        "timestamp": timestamp,
        "payload": payload,
        "prev_hash": prev_hash,
    })).hexdigest()


def _extract_words(payload):
    """Words for the index. If payload has a 'words' list, use it;
    else extract lowercase alphanumeric tokens from string fields."""
    if isinstance(payload, dict) and isinstance(payload.get("words"), list):
        return {str(w).lower() for w in payload["words"]}
    found = set()
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, str):
            found.update(re.findall(r"[a-z0-9]+", item.lower()))
    return found


def _chain_ok(namespace, records):
    """Check seqs, links, hashes, and that every record's namespace
    matches the file it was read from."""
    prev = GENESIS
    for i, rec in enumerate(records):
        try:
            if rec["seq"] != i:
                return False
            if rec["namespace"] != namespace:
                return False
            if rec["prev_hash"] != prev:
                return False
            expect = _record_hash(rec["seq"], rec["namespace"],
                                  rec["timestamp"], rec["payload"],
                                  rec["prev_hash"])
            if rec["hash"] != expect:
                return False
        except (KeyError, TypeError):
            return False
        prev = rec["hash"]
    return True


class PoolStore:
    """One directory, four JSONL files, one hash chain per file.

    Threading: `lock` is this store's reentrant lock. append()
    and close() hold it, and the read accessors take it for a
    consistent snapshot. Composite callers may hold it across
    several appends to make the group one atomic step; because
    it is reentrant, the appends inside still proceed."""

    def __init__(self, directory):
        if os.path.exists(directory) and not os.path.isdir(directory):
            raise ValueError("pool store path is not a directory: %r" % (directory,))
        os.makedirs(directory, exist_ok=True)
        self.directory = directory
        self.lock = threading.RLock()
        self._pools = {}
        self.truncated = {}
        for ns in NAMESPACES:
            records, truncated = self._load_file(ns)
            self._pools[ns] = {"records": records, "handle": None, "index": {}}
            self.truncated[ns] = truncated
            for rec in records:
                self._index_record(ns, rec)
        for ns in NAMESPACES:
            self._pools[ns]["handle"] = open(self._pool_path(ns), "a",
                                             encoding="utf-8")

    def _pool_path(self, namespace):
        return os.path.join(self.directory, namespace + ".jsonl")

    def _load_file(self, namespace):
        path = self._pool_path(namespace)
        if not os.path.exists(path):
            return [], False
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        lines = raw.splitlines(keepends=True)
        good_end = 0
        parsed = []
        truncated = False
        for i, line in enumerate(lines):
            if line.strip() == "":
                good_end += len(line)
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                if i == len(lines) - 1:
                    truncated = True
                    break
                raise ValueError("corrupt pool store: bad JSON in %s on line %d"
                                 % (path, i))
            if not isinstance(rec, dict):
                raise ValueError("corrupt pool store: non-object in %s on line %d"
                                 % (path, i))
            parsed.append(rec)
            good_end += len(line)
        if not _chain_ok(namespace, parsed):
            raise ValueError("corrupt pool store: hash chain broken in %s" % path)
        if truncated:
            with open(path, "r+b") as f:
                f.truncate(good_end)
        return parsed, truncated

    def _index_record(self, namespace, rec):
        index = self._pools[namespace]["index"]
        for word in _extract_words(rec.get("payload", {})):
            bucket = index.get(word)
            if bucket is None:
                index[word] = {rec["seq"]}
            else:
                bucket.add(rec["seq"])

    def _require_namespace(self, namespace):
        if namespace not in NAMESPACES:
            raise ValueError("unknown namespace: %r" % (namespace,))

    def verify_chain(self, namespace=None):
        """Verify one file's chain, or all four. True, or raise ValueError
        naming the broken file."""
        with self.lock:
            if namespace is not None:
                self._require_namespace(namespace)
                if not _chain_ok(namespace, self._pools[namespace]["records"]):
                    raise ValueError("corrupt pool store: hash chain broken in %s"
                                     % self._pool_path(namespace))
                return True
            for ns in NAMESPACES:
                if not _chain_ok(ns, self._pools[ns]["records"]):
                    raise ValueError("corrupt pool store: hash chain broken in %s"
                                     % self._pool_path(ns))
            return True

    def append(self, namespace, payload, timestamp=None, graph=None):
        """Append one record to the namespace's file. Durable (fsync)
        before returning. graph: optional graph address tuple
        (layer, dimension, index); when supplied, it is validated with
        the pmca_graphspace rules and stored in the payload as a
        JSON-safe 'graph' list [layer, dimension, index]. When
        omitted, the payload is stored exactly as given.

        The chain extension is one critical section under the
        store lock: seq allocation, predecessor selection, the
        write+fsync, and the in-memory append cannot interleave
        with another append (or with a composite caller holding
        store.lock), so a seq is issued exactly once and the
        in-memory list only ever holds fsynced records."""
        self._require_namespace(namespace)
        if graph is not None:
            layer, dimension, index = _validate_graph_address(graph)
            if not isinstance(payload, dict):
                raise ValueError("graph address requires a dict payload")
            payload = dict(payload)
            payload["graph"] = [layer, dimension, index]
        try:
            json.dumps(payload)
        except (TypeError, ValueError):
            raise TypeError("payload is not JSON-serializable")
        with self.lock:
            pool = self._pools[namespace]
            if pool["handle"] is None:
                raise ValueError("pool store is closed")
            records = pool["records"]
            seq = len(records)
            prev = GENESIS if seq == 0 else records[-1]["hash"]
            if timestamp is None:
                timestamp = datetime.now(timezone.utc).isoformat()
            rec_hash = _record_hash(seq, namespace, timestamp, payload, prev)
            rec = {"seq": seq, "namespace": namespace, "timestamp": timestamp,
                   "payload": payload, "prev_hash": prev, "hash": rec_hash}
            pool["handle"].write(json.dumps(rec, sort_keys=True) + "\n")
            pool["handle"].flush()
            os.fsync(pool["handle"].fileno())
            records.append(rec)
            self._index_record(namespace, rec)
            return rec

    def get(self, namespace, seq):
        """Record by namespace + seq, or None."""
        self._require_namespace(namespace)
        with self.lock:
            records = self._pools[namespace]["records"]
            if isinstance(seq, int) and 0 <= seq < len(records):
                rec = records[seq]
                if rec["seq"] == seq:
                    return rec
            return None

    def by_namespace(self, namespace):
        """Records of one pool file, in seq order."""
        self._require_namespace(namespace)
        with self.lock:
            return list(self._pools[namespace]["records"])

    def by_word(self, word):
        """Records (across all four pools) whose payload mentions the word.
        Deterministic order: NAMESPACES order, then seq order within
        each pool."""
        word = str(word).lower()
        out = []
        with self.lock:
            for ns in NAMESPACES:
                pool = self._pools[ns]
                for seq in sorted(pool["index"].get(word, ())):
                    out.append(pool["records"][seq])
        return out

    def replay(self, namespace=None):
        """One pool's records, or all four concatenated in NAMESPACES order.
        Deterministic: two replays identical."""
        if namespace is not None:
            return self.by_namespace(namespace)
        out = []
        with self.lock:
            for ns in NAMESPACES:
                out.extend(self._pools[ns]["records"])
        return out

    def supersede(self, namespace, seq, new_payload, graph=None):
        """Mark a record superseded WITHOUT editing or deleting it (A5).
        Returns a new record in the same pool pointing back.
        new_payload: dict (merged into the supersession payload) or
        string (used as the reason). graph: optional graph address,
        passed through to append()."""
        target = self.get(namespace, seq)
        if target is None:
            raise ValueError("no such record: %r in %r" % (seq, namespace))
        if isinstance(new_payload, dict):
            payload = {"supersedes": seq, "superseded_namespace": namespace}
            payload.update(new_payload)
        else:
            payload = {"supersedes": seq, "reason": new_payload,
                       "superseded_namespace": namespace}
        return self.append(namespace, payload, graph=graph)

    def close(self):
        """Flush, fsync, close all four files. Idempotent."""
        with self.lock:
            for ns in NAMESPACES:
                handle = self._pools[ns]["handle"]
                if handle is not None:
                    handle.flush()
                    os.fsync(handle.fileno())
                    handle.close()
                    self._pools[ns]["handle"] = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def audit_to_pool(store, teaching_result, graph=None):
    """Append a teaching event's audit entry to the 'audit' namespace.
    Helper only; the teaching loop itself is NOT modified here.
    Payload = the audit entry dict (preferred), with a 'words' list
    extracted from the input text for the word index. graph: optional
    graph address, passed through to append()."""
    entry = teaching_result.get("audit_entry")
    if isinstance(entry, dict):
        payload = {k: v for k, v in entry.items()
                   if k not in ("seed", "pending_seed", "updated_seed")}
        text = entry.get("input", "")
    else:
        payload = {k: v for k, v in teaching_result.items()
                   if k not in ("seed", "pending_seed", "updated_seed")}
        text = teaching_result.get("input", "")
    payload["words"] = sorted(_extract_words(text))
    return store.append("audit", payload, graph=graph)


def sandbox_record(store, edge, reason, graph=None):
    """Append an unvalued edge's sandbox evaluation record.
    graph: optional graph address, passed through to append()."""
    payload = {"edge": {"source": edge["source"], "target": edge["target"]},
               "reason": reason, "triple_place": True,
               "words": [str(edge["source"]).lower(),
                         str(edge["target"]).lower()]}
    return store.append("sandbox", payload, graph=graph)


def engagement_record(store, payload, graph=None):
    """Append one engagement record (a dict payload) to the
    'engagement' namespace, and nothing else. Same machinery as
    sandbox_record: the pool's own append (hash-chained, fsynced).
    graph: optional graph address, passed through to append()."""
    return store.append("engagement", payload, graph=graph)


# ---------------------------------------------------------------- tests

_PASSED = []
_FAILED = []


def check(name, cond):
    if cond:
        _PASSED.append(name)
        print("PASS: " + name)
    else:
        _FAILED.append(name)
        print("FAIL: " + name)


def run_tests():
    d = tempfile.mkdtemp(prefix="pmca_pools_test_")

    # 1. Four files exist; each file has its own chain (seqs restart at 0).
    d1 = os.path.join(d, "four")
    s = PoolStore(d1)
    r_s = s.append("sandbox", {"words": ["bomb"], "note": "unvalued edge"}, timestamp="2026-10-14T00:00:00+00:00")
    r_c = s.append("chaos", {"words": ["bread"], "note": "scrap"}, timestamp="2026-10-14T00:00:01+00:00")
    r_p = s.append("pending", {"words": ["bomb"], "event": "teach-1"}, timestamp="2026-10-14T00:00:02+00:00")
    r_a = s.append("audit", {"words": ["bomb"], "event": "teach-1"}, timestamp="2026-10-14T00:00:03+00:00")
    check("four files exist after open", all(os.path.isfile(os.path.join(d1, ns + ".jsonl")) for ns in NAMESPACES))
    check("seqs restart at 0 per file", (r_s["seq"], r_c["seq"], r_p["seq"], r_a["seq"]) == (0, 0, 0, 0))
    check("each record namespace matches its file", (r_s["namespace"], r_c["namespace"], r_p["namespace"], r_a["namespace"]) == ("sandbox", "chaos", "pending", "audit"))
    check("sandbox prev_hash is GENESIS", r_s["prev_hash"] == GENESIS)
    check("verify_chain() True", s.verify_chain())
    check("verify_chain('sandbox') True", s.verify_chain("sandbox"))
    check("get(namespace, 0) returns record", s.get("chaos", 0) == r_c)
    check("get(namespace, 99) returns None", s.get("chaos", 99) is None)
    check("replay() concatenates in NAMESPACES order", [r["namespace"] for r in s.replay()] == ["sandbox", "chaos", "pending", "audit"])
    check("replay('audit') returns only audit", s.replay("audit") == [r_a])
    check("by_namespace('pending') returns only pending", s.by_namespace("pending") == [r_p])
    check("replay determinism: two replays identical", s.replay() == s.replay())
    s.close()

    # 1b. Empty files: by_namespace on an untouched pool returns [].
    d1b = os.path.join(d, "empty")
    s = PoolStore(d1b)
    s.append("sandbox", {"a": 1}, timestamp="2026-10-14T00:00:00+00:00")
    check("by_namespace on empty pool returns []", s.by_namespace("chaos") == [])
    check("replay() on mostly-empty store", len(s.replay()) == 1)
    check("verify_chain() True on mostly-empty store", s.verify_chain())
    s.close()

    # 2. Tamper ONE file: reopen raises naming that file; other three
    #    still verify on their own (isolation).
    d2 = os.path.join(d, "tamper")
    s = PoolStore(d2)
    for ns in NAMESPACES:
        s.append(ns, {"words": [ns], "value": 5}, timestamp="2026-10-14T00:00:00+00:00")
        s.append(ns, {"words": [ns], "value": 0}, timestamp="2026-10-14T00:00:01+00:00")
    s.close()
    chaos_path = os.path.join(d2, "chaos.jsonl")
    with open(chaos_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    rec1 = json.loads(lines[1])
    rec1["payload"]["value"] = 9  # tamper payload, leave stale hash
    lines[1] = json.dumps(rec1, sort_keys=True)
    with open(chaos_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    raised = None
    try:
        PoolStore(d2)
    except ValueError as e:
        raised = str(e)
    check("tamper: reopen raises ValueError", raised is not None)
    check("tamper: error names the corrupted file", raised is not None and "chaos.jsonl" in raised)
    for ns in ("sandbox", "pending", "audit"):
        path = os.path.join(d2, ns + ".jsonl")
        with open(path, "r", encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
        check("isolation: %s file still verifies after chaos tamper" % ns, _chain_ok(ns, recs))
    with open(chaos_path, "r", encoding="utf-8") as f:
        recs = [json.loads(line) for line in f if line.strip()]
    check("isolation: tampered chaos file fails chain check", not _chain_ok("chaos", recs))

    # 3. Supersede in one pool: new record, old untouched, chain holds.
    d3 = os.path.join(d, "sup")
    s = PoolStore(d3)
    s.append("sandbox", {"words": ["bomb"], "value": 5}, timestamp="2026-10-14T00:00:00+00:00")
    before = json.dumps(s.get("sandbox", 0)["payload"], sort_keys=True)
    sup = s.supersede("sandbox", 0, "corrected value")
    check("supersede returns new record with supersedes=0", sup["payload"].get("supersedes") == 0)
    check("supersede keeps same namespace", sup["namespace"] == "sandbox")
    check("supersede new record seq 1 in its file", sup["seq"] == 1)
    check("supersede old record byte-identical payload", json.dumps(s.get("sandbox", 0)["payload"], sort_keys=True) == before)
    check("supersede verify_chain True", s.verify_chain())
    sup2 = s.supersede("sandbox", 1, {"reason": "second correction", "extra": 1})
    check("supersede dict payload merges", sup2["payload"].get("supersedes") == 1 and sup2["payload"].get("extra") == 1)
    missing = False
    try:
        s.supersede("sandbox", 99, "nope")
    except ValueError:
        missing = True
    check("supersede nonexistent raises ValueError", missing)
    s.close()

    # 4. by_word searches across all four pools.
    d4 = os.path.join(d, "words")
    s = PoolStore(d4)
    s.append("sandbox", {"words": ["bomb"], "note": "a"}, timestamp="2026-10-14T00:00:00+00:00")
    s.append("chaos", {"note": "cut the bread here"}, timestamp="2026-10-14T00:00:01+00:00")
    s.append("pending", {"words": ["bomb"], "note": "p"}, timestamp="2026-10-14T00:00:02+00:00")
    s.append("audit", {"words": ["bomb", "bread"], "note": "c"}, timestamp="2026-10-14T00:00:03+00:00")
    check("by_word bomb spans pools in NAMESPACES order",
          [(r["namespace"], r["seq"]) for r in s.by_word("bomb")] == [("sandbox", 0), ("pending", 0), ("audit", 0)])
    check("by_word bread spans pools",
          [(r["namespace"], r["seq"]) for r in s.by_word("bread")] == [("chaos", 0), ("audit", 0)])
    check("by_word unknown returns []", s.by_word("unknown") == [])
    check("by_word case-insensitive",
          [(r["namespace"], r["seq"]) for r in s.by_word("BOMB")] == [("sandbox", 0), ("pending", 0), ("audit", 0)])
    s.close()

    # 5. by_namespace per file.
    d5 = os.path.join(d, "ns")
    s = PoolStore(d5)
    s.append("sandbox", {"a": 1}, timestamp="2026-10-14T00:00:00+00:00")
    s.append("audit", {"a": 2}, timestamp="2026-10-14T00:00:01+00:00")
    s.append("sandbox", {"a": 3}, timestamp="2026-10-14T00:00:02+00:00")
    check("by_namespace sandbox only", [r["seq"] for r in s.by_namespace("sandbox")] == [0, 1])
    check("by_namespace audit only", [r["seq"] for r in s.by_namespace("audit")] == [0])
    check("by_namespace preserves seq order", [r["seq"] for r in s.by_namespace("sandbox")] == sorted(r["seq"] for r in s.by_namespace("sandbox")))
    bad_ns = False
    try:
        s.by_namespace("bogus")
    except ValueError:
        bad_ns = True
    check("by_namespace unknown namespace raises ValueError", bad_ns)
    s.close()

    # 6. Crash safety per file: partial last line in one file, others unaffected.
    d6 = os.path.join(d, "crash")
    s = PoolStore(d6)
    s.append("sandbox", {"a": 1}, timestamp="2026-10-14T00:00:00+00:00")
    s.append("sandbox", {"a": 2}, timestamp="2026-10-14T00:00:01+00:00")
    s.append("audit", {"a": 3}, timestamp="2026-10-14T00:00:02+00:00")
    s.close()
    s2 = PoolStore(d6)
    check("crash safety: reopen has all records", len(s2.replay()) == 3)
    check("crash safety: verify_chain True", s2.verify_chain())
    s2.close()
    with open(os.path.join(d6, "sandbox.jsonl"), "a", encoding="utf-8") as f:
        f.write('{"seq": 99, "namesp')
    s3 = PoolStore(d6)
    check("partial last line: truncated True for sandbox only",
          s3.truncated.get("sandbox") is True and s3.truncated.get("audit") is False
          and s3.truncated.get("chaos") is False and s3.truncated.get("pending") is False)
    check("partial last line: loads complete prefix", len(s3.replay("sandbox")) == 2)
    check("partial last line: verify_chain True on prefix", s3.verify_chain())
    check("partial last line: store usable after truncation",
          s3.append("sandbox", {"a": 4}, timestamp="2026-10-14T00:00:03+00:00")["seq"] == 2)
    check("partial last line: audit unaffected", len(s3.replay("audit")) == 1)
    s3.close()

    # 7. Separation: pool ops leave a seed file byte-identical.
    d7 = os.path.join(d, "sep")
    os.makedirs(d7, exist_ok=True)
    p7seed = os.path.join(d7, "seed.json")
    import pmca_seed as S
    seed = S.build_seed(S.DEMO_DICTIONARY)
    with open(p7seed, "w", encoding="utf-8") as f:
        json.dump(seed, f, sort_keys=True)
    with open(p7seed, "rb") as f:
        seed_bytes_before = f.read()
    s = PoolStore(d7)
    s.append("sandbox", {"words": ["bomb"]}, timestamp="2026-10-14T00:00:00+00:00")
    s.append("audit", {"event": "teach-1"}, timestamp="2026-10-14T00:00:01+00:00")
    s.supersede("sandbox", 0, "test")
    s.close()
    with open(p7seed, "rb") as f:
        seed_bytes_after = f.read()
    check("separation: seed file byte-identical after pool ops", seed_bytes_before == seed_bytes_after)

    # 8. Persistence: new instance sees all records in all four pools.
    d8 = os.path.join(d, "persist")
    s = PoolStore(d8)
    for ns in NAMESPACES:
        s.append(ns, {"a": 1}, timestamp="2026-10-14T00:00:00+00:00")
    s.close()
    s2 = PoolStore(d8)
    check("persistence: reopened store has all records", len(s2.replay()) == len(NAMESPACES) and s2.verify_chain())
    check("persistence: per-pool records intact", all(len(s2.by_namespace(ns)) == 1 for ns in NAMESPACES))
    s2.close()

    # 9. Determinism: same appends + same timestamps -> identical records.
    da = os.path.join(d, "det_a")
    db = os.path.join(d, "det_b")
    for directory in (da, db):
        s = PoolStore(directory)
        s.append("sandbox", {"words": ["bomb"], "v": 5}, timestamp="2026-10-14T00:00:00+00:00")
        s.append("audit", {"event": "teach-1"}, timestamp="2026-10-14T00:00:01+00:00")
        s.close()
    sa2 = PoolStore(da)
    sb2 = PoolStore(db)
    check("determinism: identical records incl. hashes", sa2.replay() == sb2.replay())
    sa2.close()
    sb2.close()

    # 10. Rejections.
    d10 = os.path.join(d, "rej")
    s = PoolStore(d10)
    bad_ns = False
    try:
        s.append("bogus", {})
    except ValueError:
        bad_ns = True
    check("append unknown namespace raises ValueError", bad_ns)
    bad_payload = False
    try:
        s.append("sandbox", {"fn": lambda x: x})
    except TypeError:
        bad_payload = True
    check("append non-serializable payload raises TypeError", bad_payload)
    bad_get = False
    try:
        s.get("bogus", 0)
    except ValueError:
        bad_get = True
    check("get unknown namespace raises ValueError", bad_get)
    bad_verify = False
    try:
        s.verify_chain("bogus")
    except ValueError:
        bad_verify = True
    check("verify_chain unknown namespace raises ValueError", bad_verify)
    bad_replay = False
    try:
        s.replay("bogus")
    except ValueError:
        bad_replay = True
    check("replay unknown namespace raises ValueError", bad_replay)
    s.close()
    closed = False
    try:
        s.append("sandbox", {})
    except ValueError:
        closed = True
    check("append after close raises ValueError", closed)

    # 11. Helpers: audit_to_pool + sandbox_record.
    d11 = os.path.join(d, "helpers")
    s = PoolStore(d11)
    import pmca_teaching as T
    seed = S.build_seed(S.DEMO_DICTIONARY)
    res = T.teach(seed, {"input": "poison the rat",
                         "human_verdict": "REDACT",
                         "explanation_text": "pest control",
                         "edge_updates": [{"source": "poison", "target": "rat", "harm": 2, "reason": "pest control"}]})
    audit_rec = audit_to_pool(s, res)
    check("audit_to_pool appends to audit namespace", audit_rec["namespace"] == "audit")
    check("audit_to_pool lands in audit.jsonl", s.get("audit", 0) == audit_rec)
    check("audit_to_pool words index finds input word", len(s.by_word("poison")) >= 1)
    sb_rec = sandbox_record(s, {"source": "cut", "target": "person"}, "unvalued edge evaluation")
    check("sandbox_record appends to sandbox namespace", sb_rec["namespace"] == "sandbox")
    check("sandbox_record payload triple_place True", sb_rec["payload"].get("triple_place") is True)
    check("sandbox_record findable by edge word", len(s.by_word("person")) >= 1)
    check("helpers verify_chain True", s.verify_chain())
    s.close()

    # 11b. Engagement namespace + engagement_record helper.
    d11b = os.path.join(d, "engagement")
    s = PoolStore(d11b)
    check("engagement: missing file loads as empty pool", s.by_namespace("engagement") == [])
    check("engagement: engagement.jsonl exists after open", os.path.isfile(os.path.join(d11b, "engagement.jsonl")))
    e1 = engagement_record(s, {"record_kind": "engagement", "note": "first"})
    e2 = engagement_record(s, {"record_kind": "engagement", "note": "second"})
    check("engagement_record lands in engagement namespace", e1["namespace"] == "engagement" and e2["namespace"] == "engagement")
    check("engagement_record seqs start at 0 and increment", (e1["seq"], e2["seq"]) == (0, 1))
    check("engagement_record chain links prev_hash", e1["prev_hash"] == GENESIS and e2["prev_hash"] == e1["hash"])
    check("engagement_record payload read back intact", s.get("engagement", 1)["payload"] == {"record_kind": "engagement", "note": "second"})
    check("engagement_record touches no other namespace", all(s.by_namespace(ns) == [] for ns in ("sandbox", "chaos", "pending", "audit")))
    check("engagement verify_chain('engagement') True", s.verify_chain("engagement"))
    check("engagement verify_chain() True", s.verify_chain())
    e3 = engagement_record(s, {"record_kind": "segment_open"}, graph=(1, "A", 1))
    check("engagement_record graph passthrough", e3["payload"].get("graph") == [1, "A", 1])
    s.close()
    s = PoolStore(d11b)
    check("engagement: records persist across reopen", [r["seq"] for r in s.by_namespace("engagement")] == [0, 1, 2] and s.verify_chain("engagement"))
    s.close()

    # 12. Static checks: no randomness (import-level; pool records
    # carry wall-clock timestamps as metadata, never read by a
    # verdict), no floats, no max(), no guessing verb.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pmca_pools.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    max_calls = sum(1 for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "max")
    check("static: no maximum call anywhere (AST)", max_calls == 0)
    floats = [n for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    check("static: no float literal anywhere (AST)", len(floats) == 0)
    random_imports = [n for n in ast.walk(tree)
                      if isinstance(n, ast.Import) and any(a.name == "random" for a in n.names)
                      or isinstance(n, ast.ImportFrom) and n.module == "random"]
    check("static: no random import (AST)", len(random_imports) == 0)
    check("static: no forbidden guessing verb (grep)", len(re.findall(r"\bassume\b", src, re.IGNORECASE)) == 0)

    # 13. Siblings still pass. NOTE: pmca_heldout.py is NOT re-run here:
    # its own suite re-runs pmca_pools.py, so spawning it from here
    # would recurse forever (pools -> heldout -> pools -> ...).
    # Heldout is verified separately by running it directly.
    here = os.path.dirname(os.path.abspath(__file__))
    for mod in ("pmca_proof.py", "pmca_layer1.py", "pmca_seed.py", "pmca_teaching.py"):
        proc = subprocess.run([sys.executable, os.path.join(here, mod)],
                              capture_output=True, text=True)
        check("sibling %s still passes" % mod, proc.returncode == 0)

    total = len(_PASSED) + len(_FAILED)
    print("%d/%d tests passed" % (len(_PASSED), total))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(run_tests())
