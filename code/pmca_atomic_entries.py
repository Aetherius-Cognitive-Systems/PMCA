"""PMCA atomic entry store — one sealed entry per process, its
input and every step inside it; processes that die midway filed
in a separate, searchable incomplete store; the main file holds
sealed, completed entries only.

Architecture and design: Jonathan Wayne Fleuren (the builder) —
the atomic-entry ruling, 2026-10-26: whatever gets written and
whatever gets processed is part of the SAME ENTRY in the stored
atomic file; the initial input is written atomically and the
steps the system makes are written as well, step by step; a
process that dies midway does NOT leave an open entry in the
main file — everything recorded for it goes into a dedicated,
searchable file for incomplete processes. Build spec:
atomic-entry-pack-spec.md (2026-10-27), sections 4 and 6.
Code logic implemented by Alder (Muse) per that spec.

WHAT IT PROVES (module check suite below): appends are one
critical section under one reentrant lock (seq allocation,
prev-hash selection, seal computation, single-line write,
fsync, in-memory update — the PoolStore.append discipline);
fsync precedes acknowledgement in both the journal and the
entry file; a torn final line is truncation (the pools
_load_file precedent) while corruption anywhere else raises
loudly on open; an unterminated journal run is filed incomplete
exactly once (idempotent by entry_id — a run whose entry
already sealed in the main store is retired WITHOUT filing and
WITHOUT duplicating); store exclusivity across processes is a
flock on the lock file, refused loudly (EntryStoreLocked),
never silently contended; a read-only open performs NO recovery
and files NOTHING.

SEALS (spec section 4.3): Pack H seal_v 2, extended by the two
domains registered in pmca_persistence ("atomic_entry":
b"PMCA/entry/process", "incomplete_entry":
b"PMCA/entry/incomplete"). An entry's seal is HMAC-SHA256 under
the domain subkey over the canonical bytes of the entry minus
{seal, key_id, seal_v}, with seq and prev_hash INSIDE the sealed
material (the Pack H "generation inside the material" rule,
translated: chain position inside the material). With a keyring
configured, entries are written seal_v 2 and loading REQUIRES
the keyring (ArchiveLocked without it — never a silent
unverified read). Without a keyring, the seal_v 1 unkeyed form
is written (the Pack H precedent; whether entries must refuse
to be written unkeyed is the spec's open question Q2 — this
module follows the precedent and invents no refusal).

ENTRY IDS: entry_id is allocated at BEGIN, from the store's
single id allocator, because the journal's entry_open record
must carry it before the process runs (spec 4.4 step 1). In
death-free sequential operation the id number coincides with
the entry's main-store seq (the spec 4.2 formula); a process
that dies consumes an id without consuming a main seq, which is
why the incomplete store cross-references by entry_id, never
by seq. The allocator is recovered on open as one past the
largest id number seen anywhere in the store's files.

JOURNAL HEAD HASH (spec 4.6, expectation A6): an incomplete
entry's interruption.journal_head_hash is the per-run step
fold — GENESIS folded over the run's steps in order, each step
hashed as canonical {"prev_hash", "step_seq", "kind", "data"} —
exposed as steps_head_hash() so it can be recomputed from the
filed steps alone, exactly as filed.

WHAT IT DOES NOT DO:
- No self-repair surface. Nothing in this module completes,
  edits, re-statuses, or deletes an incomplete entry; the
  IncompleteStore exposes read doors only (spec 4.6, E5).
- No wiring. analyze_logged, the StoredStore wrappers, and the
  segment lifecycle still write where they wrote before; the
  surface wiring is a later build step. Direct appends of
  segment lifecycle entries are provided for that step.
- Single writer. One live EntryStore per directory; multi-
  writer entry stores are the spec's open question Q1.
"""

import contextlib
import fcntl
import hashlib
import hmac
import inspect
import json
import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone

import pmca_persistence

GENESIS = "GENESIS"
ENTRY_V = 1
INCOMPLETE_V = 1

SURFACES = ("analysis", "teaching", "write_in", "composition",
            "population")
ENTRY_KINDS = ("process", "segment_open", "segment_close",
               "segment_discard")

_STARTING_KEYS = ("segment_id", "checkpoint_entry_seq",
                  "state_before_checksum",
                  "store_generation_before",
                  "seed_generation_before")
_ENDING_KEYS = ("state_after_checksum", "store_generation_after",
                "seed_generation_after")
_DICTIONARY_SOURCES = ("explicit", "env", "default", "off")
_SEAL_FIELDS = ("seal", "key_id", "seal_v")

_MAIN_FILE = "atomic_entries.jsonl"
_JOURNAL_FILE = "process_journal.jsonl"
_INCOMPLETE_FILE = "incomplete_processes.jsonl"
_LOCK_FILE = "atomic_entries.lock"


class EntryStoreLocked(Exception):
    """Another live writer holds the entry store's lock file,
    or a mutating operation was attempted through a read-only
    open. Always loud; contention is never silent here."""


def _canonical(obj):
    """Canonical JSON bytes: sorted keys, tight separators,
    ensure_ascii=False — the one serialization every hash and
    seal in this module is computed over (the pmca_pools and
    pmca_write_in_storage precedents).

    TOTAL over every JSON-serializable object: a string
    carrying a lone surrogate (the ST-21 input class) cannot be
    encoded as UTF-8, and before this fallback existed such an
    object raised UnicodeEncodeError at append time — a process
    whose input could not be canonicalized died before its
    existence was durable, leaving no record anywhere (sealed
    battery case A3). For exactly those objects the dump falls
    back to ensure_ascii=True: its \\uXXXX escapes are pure
    ASCII, always encodable, and json.loads restores the exact
    original strings, surrogates included, so the record
    round-trips byte-exact. For every object that encodes as
    UTF-8 — every record written before the fallback existed —
    the bytes are IDENTICAL to the plain ensure_ascii=False
    dump: no existing hash or seal changes."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(obj, sort_keys=True,
                          separators=(",", ":"),
                          ensure_ascii=True).encode("ascii")


def _canonical_text(obj):
    return _canonical(obj).decode("utf-8")


def _now():
    """Host-asserted timestamp: a hash input only, never
    evidence (the pools precedent)."""
    return datetime.now(timezone.utc).isoformat()


def _extract_words(payload):
    """Words for an index (the pmca_pools _extract_words
    pattern): a 'words' list if present, else lowercase
    alphanumeric tokens from every string in the structure."""
    if isinstance(payload, dict) and isinstance(
            payload.get("words"), list):
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


def steps_head_hash(steps):
    """The per-run step fold recorded as an incomplete entry's
    journal_head_hash: GENESIS folded over the steps in order.
    Recomputable from a filed entry's steps alone."""
    head = GENESIS
    for step in steps:
        head = hashlib.sha256(_canonical({
            "prev_hash": head,
            "step_seq": step["step_seq"],
            "kind": step["kind"],
            "data": step["data"]})).hexdigest()
    return head


def _validate_environment(environment):
    """Entry_v 1 environment discipline (spec 4.5): every key
    always present, and the dictionary block ALWAYS named —
    configured or explicitly off. A record that names no
    dictionary is malformed and is refused here, at write time,
    not discovered at check time."""
    if not isinstance(environment, dict):
        raise ValueError("environment must be a dict")
    for key in ("code_manifest_hash", "code_manifest",
                "dictionary"):
        if key not in environment:
            raise ValueError(
                "environment lacks %r: malformed under entry_v 1"
                % (key,))
    dictionary = environment["dictionary"]
    if not isinstance(dictionary, dict):
        raise ValueError("environment.dictionary must be a dict")
    for key in ("configured", "db_hash", "source"):
        if key not in dictionary:
            raise ValueError(
                "environment.dictionary lacks %r: the dictionary "
                "block is always named under entry_v 1" % (key,))
    if not isinstance(dictionary["configured"], bool):
        raise ValueError("environment.dictionary.configured "
                         "must be a bool")
    if dictionary["source"] not in _DICTIONARY_SOURCES:
        raise ValueError("environment.dictionary.source not in "
                         "%r" % (_DICTIONARY_SOURCES,))
    if dictionary["configured"] and not isinstance(
            dictionary["db_hash"], str):
        raise ValueError("a configured dictionary must name its "
                         "db_hash")
    if not dictionary["configured"] and \
            dictionary["db_hash"] is not None:
        raise ValueError("an unconfigured dictionary names "
                         "db_hash null")
    return environment


def _normalize_state(state, keys, label):
    """A state block with every key always present, null when
    unused, never absent (spec 4.2/4.5). Unknown keys are
    refused; missing keys are filled with null."""
    if state is None:
        return {key: None for key in keys}
    if not isinstance(state, dict):
        raise ValueError("%s must be a dict or None" % label)
    for key in state:
        if key not in keys:
            raise ValueError("%s names unknown key %r"
                             % (label, key))
    return {key: state.get(key) for key in keys}


def _parse_chain_lines(path):
    """Parse a JSONL chain file. Returns (records, truncated,
    good_end): a torn FINAL line is truncation (the pools
    _load_file precedent) and is dropped; a bad line anywhere
    else, or a non-object line, raises loudly. A missing file
    parses as an empty chain."""
    if not os.path.exists(path):
        return [], False, 0
    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    lines = raw.splitlines(keepends=True)
    records = []
    truncated = False
    good_end = 0
    for i, line in enumerate(lines):
        if line.strip() == "":
            good_end += len(line.encode("utf-8"))
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                truncated = True
                break
            raise ValueError(
                "corrupt atomic store: bad JSON in %s on line %d"
                % (path, i))
        if not isinstance(rec, dict):
            raise ValueError(
                "corrupt atomic store: non-object in %s on "
                "line %d" % (path, i))
        records.append(rec)
        good_end += len(line.encode("utf-8"))
    return records, truncated, good_end


def _gap_keys_in(obj):
    """Candidate gap keys inside a step's data: values of
    gap-naming fields, and any string carrying the '::' gap-key
    shape (the section 2.2 gaps-index key form)."""
    keys = set()
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for field, value in item.items():
                if field in ("gap_key", "gap", "key",
                             "identifier") \
                        and isinstance(value, str):
                    keys.add(value)
                stack.append(value)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, str):
            if "::" in item:
                keys.add(item)
    return keys


def _carries_graph(obj, target):
    """True when any nested dict carries a 'graph' field equal
    to the target address (stored as a JSON list, the pools
    graph-field precedent)."""
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            graph = item.get("graph")
            if isinstance(graph, list) and graph == target:
                return True
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return False


class IncompleteStore:
    """Read-only view over incomplete_processes.jsonl (spec
    4.6): searchable every way the standing storage principles
    name. Exposes EXACTLY six read doors and no other public
    method — there is no surface here, or anywhere in this
    module, that completes, edits, re-statuses, or deletes an
    incomplete entry (expectation E5). Filing is EntryStore's
    job; this class never files."""

    def __init__(self, records):
        self._records = list(records)
        self._index = {}
        for rec in self._records:
            self._index_record(rec)

    def _index_record(self, rec):
        for word in _extract_words(rec.get("input")):
            bucket = self._index.get(word)
            if bucket is None:
                self._index[word] = {rec["seq"]}
            else:
                bucket.add(rec["seq"])

    def _add(self, rec):
        """Module-internal: EntryStore extends the view when it
        files. Not a door; never called by readers."""
        self._records.append(rec)
        self._index_record(rec)

    def by_entry_id(self, entry_id):
        """The incomplete entry filed for one process id, or
        None."""
        for rec in self._records:
            if rec["entry_id"] == entry_id:
                return rec
        return None

    def by_word(self, word):
        """Incomplete entries whose recorded input mentions the
        word, in seq order."""
        word = str(word).lower()
        return [self._records[seq]
                for seq in sorted(self._index.get(word, ()))]

    def by_status(self, status):
        """Incomplete entries carrying the status. INCOMPLETE
        is the only status this pack writes; the door exists so
        any future status is searchable the day it exists."""
        return [rec for rec in self._records
                if rec["status"] == status]

    def by_surface(self, surface):
        """Incomplete entries by provenance: which process
        family died."""
        return [rec for rec in self._records
                if rec["surface"] == surface]

    def by_gap(self, word, identifier):
        """Incomplete entries whose steps contain a recorded
        gap for the word/identifier — findable from the gap the
        process died on. The identifier may be a full gap key
        (matched exactly) or a key component (matched against
        the key's '::' parts alongside the word)."""
        out = []
        for rec in self._records:
            keys = set()
            for step in rec.get("steps", []):
                keys |= _gap_keys_in(step.get("data"))
            for key in keys:
                parts = key.split("::")
                if identifier and "::" in identifier:
                    if key == identifier and (
                            not word or word in parts):
                        out.append(rec)
                        break
                elif word in parts and identifier in parts:
                    out.append(rec)
                    break
        return out

    def by_coordinate(self, graph):
        """Incomplete entries whose recorded input or steps
        carry the graph address (a (layer, dimension, index)
        triple, stored as a JSON list)."""
        target = list(graph)
        out = []
        for rec in self._records:
            if _carries_graph(rec.get("input"), target) or any(
                    _carries_graph(step.get("data"), target)
                    for step in rec.get("steps", [])):
                out.append(rec)
        return out


class ProcessHandle:
    """One in-flight process inside an EntryStore: the
    explicit begin/step/seal triple the EntryStore.process
    context manager wraps. Steps are journaled (fsynced) in
    the order the system makes them; seal assembles the entry
    from the journal run and acknowledges only after the entry
    line is durable."""

    def __init__(self, store, entry_id):
        self._store = store
        self.entry_id = entry_id
        self.outcome = None
        self.ending_state = None
        self._sealed_entry = None

    @property
    def sealed(self):
        return self._sealed_entry is not None

    def step(self, kind, data):
        """Journal one step of this process, fsynced before
        returning."""
        return self._store._journal_step(self, kind, data)

    def seal(self, outcome=None, ending_state=None,
             starting_state=None):
        """Seal the process into the main entry store and
        return the sealed entry. Falls back to the outcome /
        ending_state attributes the caller set on the handle.
        starting_state, when given, supersedes the journal's
        begun-state record FOR THE SEALED ENTRY ONLY (the
        journal's entry_open keeps the begun state): the
        analyzer's entry builder resolves segment membership
        at seal time and stamps the sealed entry with it,
        while state_before_checksum rides through from the
        begin (see pmca_sentence_analyzer._seal_analysis_entry).
        """
        if outcome is None:
            outcome = self.outcome
        if ending_state is None:
            ending_state = self.ending_state
        return self._store._seal_process(self, outcome,
                                         ending_state,
                                         starting_state)


class EntryStore:
    """The atomic entry store (spec 4.1/4.4/6.1): three
    hash-chained files under <root>/atomic/, one reentrant
    lock over every append, one live writer per directory
    enforced by an flock on the lock file.

    Open modes: exclusive (default) — takes the lock file,
    verifies all three chains, repairs torn tails, and runs
    recovery (dead journal runs filed incomplete, surfaced on
    the returned store's .recoveries, never silent). Read-only
    — no lock, no recovery, no repair, files NOTHING
    (expectation B4)."""

    def __init__(self):
        self.lock = threading.RLock()
        self._closed = True
        self._read_only = False
        self._root = None
        self._dir = None
        self._keyring_path = None
        self._write_key = None
        self._entries = []
        self._entries_by_id = {}
        self._sealed_ids = set()
        self._word_index = {}
        self._journal = []
        self._runs = {}
        self._incomplete = []
        self.incomplete_store = IncompleteStore([])
        self._entry_counter = 0
        self._handles = {}
        self._lock_fd = None
        self.recoveries = []
        self.truncated = {}

    # -- open / close -------------------------------------------------

    @classmethod
    def open(cls, root, keyring_path=None, read_only=False):
        """Open the entry store rooted at <root>/atomic/ and
        return the store. Exclusive open returns with
        .recoveries holding the incomplete entries this open
        filed (possibly empty) — surfaced, never silent.
        Raises EntryStoreLocked when a live writer holds the
        store; ArchiveLocked when a seal_v 2 entry names a key
        the configured keyring cannot supply."""
        store = cls()
        store._root = root
        store._dir = os.path.join(root, "atomic")
        store._keyring_path = keyring_path
        store._read_only = bool(read_only)
        if keyring_path is not None:
            store._write_key = pmca_persistence._resolve_seal_key(
                keyring_path)
        if read_only:
            store._load(create=False)
            store._closed = False
            return store
        os.makedirs(store._dir, exist_ok=True)
        lock_path = os.path.join(store._dir, _LOCK_FILE)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise EntryStoreLocked(
                "entry store is held by a live writer: %s"
                % store._dir)
        store._lock_fd = fd
        try:
            store._load(create=True)
            store._open_handles()
            store._closed = False
            store.recoveries = store._recover()
        except BaseException:
            store._release_lock()
            raise
        return store

    def _release_lock(self):
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def close(self):
        """Close append handles and release the lock file.
        In-flight journal runs are NOT sealed or filed by
        closing: in the journal they are indistinguishable
        from a hard death, and the next exclusive open files
        them (spec 4.6 trigger 2). Idempotent."""
        with self.lock:
            if self._closed:
                return
            for handle in self._handles.values():
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                finally:
                    handle.close()
            self._handles = {}
            self._release_lock()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -- loading and verification --------------------------------------

    def _path(self, name):
        return os.path.join(self._dir, name)

    def _load(self, create):
        for name in (_MAIN_FILE, _JOURNAL_FILE, _INCOMPLETE_FILE):
            path = self._path(name)
            records, truncated, good_end = _parse_chain_lines(
                path)
            self.truncated[name] = truncated
            if name == _MAIN_FILE:
                self._entries = records
            elif name == _JOURNAL_FILE:
                self._journal = records
            else:
                self._incomplete = records
            if truncated and not self._read_only:
                with open(path, "r+b") as handle:
                    handle.truncate(good_end)
        self._verify_main_chain()
        self._verify_journal_chain()
        self._verify_incomplete_chain()
        for entry in self._entries:
            self._entries_by_id[entry["entry_id"]] = entry
            self._sealed_ids.add(entry["entry_id"])
            self._index_entry(entry)
        self._rebuild_runs()
        self.incomplete_store = IncompleteStore(self._incomplete)
        counter = 0
        seen = list(self._runs) + [e["entry_id"]
                                   for e in self._entries] \
            + [r["entry_id"] for r in self._incomplete]
        for entry_id in seen:
            if isinstance(entry_id, str) \
                    and entry_id.startswith("ENT-"):
                try:
                    number = int(entry_id[4:])
                except ValueError:
                    continue
                if number + 1 > counter:
                    counter = number + 1
        self._entry_counter = counter

    def _verify_main_chain(self):
        prev = GENESIS
        for i, entry in enumerate(self._entries):
            if entry.get("entry_v") != ENTRY_V:
                raise ValueError(
                    "corrupt atomic store: entry_v at seq %d" % i)
            if entry.get("seq") != i:
                raise ValueError(
                    "corrupt atomic store: entry seq gap at %d"
                    % i)
            if entry.get("prev_hash") != prev:
                raise ValueError(
                    "corrupt atomic store: entry chain broken "
                    "at seq %d" % i)
            self._verify_seal(entry, "atomic_entry")
            prev = entry["seal"]

    def _verify_incomplete_chain(self):
        prev = GENESIS
        for i, rec in enumerate(self._incomplete):
            if rec.get("incomplete_v") != INCOMPLETE_V:
                raise ValueError(
                    "corrupt atomic store: incomplete_v at "
                    "seq %d" % i)
            if rec.get("seq") != i:
                raise ValueError(
                    "corrupt atomic store: incomplete seq gap "
                    "at %d" % i)
            if rec.get("incomplete_id") != "INC-%06d" % i:
                raise ValueError(
                    "corrupt atomic store: incomplete_id at "
                    "seq %d" % i)
            if rec.get("prev_hash") != prev:
                raise ValueError(
                    "corrupt atomic store: incomplete chain "
                    "broken at seq %d" % i)
            self._verify_seal(rec, "incomplete_entry")
            prev = rec["seal"]

    def _verify_journal_chain(self):
        prev = GENESIS
        for i, rec in enumerate(self._journal):
            if rec.get("seq") != i:
                raise ValueError(
                    "corrupt atomic store: journal seq gap at "
                    "%d" % i)
            if rec.get("prev_hash") != prev:
                raise ValueError(
                    "corrupt atomic store: journal chain broken "
                    "at seq %d" % i)
            expect = hashlib.sha256(_canonical(
                {k: v for k, v in rec.items()
                 if k != "hash"})).hexdigest()
            if rec.get("hash") != expect:
                raise ValueError(
                    "corrupt atomic store: journal hash broken "
                    "at seq %d" % i)
            prev = rec["hash"]

    def _rebuild_runs(self):
        self._runs = {}
        for rec in self._journal:
            kind = rec.get("record_kind")
            entry_id = rec.get("entry_id")
            if kind == "entry_open":
                self._runs[entry_id] = {
                    "open": rec, "steps": [], "terminal": None}
            elif kind == "step":
                run = self._runs.get(entry_id)
                if run is None:
                    raise ValueError(
                        "corrupt atomic store: journal step "
                        "for unknown entry_id %r" % (entry_id,))
                run["steps"].append({
                    "step_seq": rec["step_seq"],
                    "kind": rec["kind"],
                    "data": rec["data"]})
            elif kind == "entry_sealed":
                run = self._runs.get(entry_id)
                if run is not None:
                    run["terminal"] = "sealed"
            elif kind == "entry_filed_incomplete":
                run = self._runs.get(entry_id)
                if run is not None:
                    run["terminal"] = "filed"

    def verify_chain(self):
        """Re-verify all three chains against the in-memory
        state. True, or raise ValueError (the pools
        verify_chain idiom)."""
        with self.lock:
            self._verify_main_chain()
            self._verify_journal_chain()
            self._verify_incomplete_chain()
            return True

    # -- seals ----------------------------------------------------------

    def _seal_material(self, entry):
        return _canonical({k: v for k, v in entry.items()
                           if k not in _SEAL_FIELDS})

    def _seal_for_new(self, entry, domain):
        if self._write_key is not None:
            key_id, master = self._write_key
            entry["seal_v"] = 2
            entry["key_id"] = key_id
            subkey = pmca_persistence._state_subkey(
                master, key_id, domain)
            entry["seal"] = hmac.new(
                subkey, self._seal_material(entry),
                hashlib.sha256).hexdigest()
        else:
            entry["seal_v"] = 1
            entry["key_id"] = None
            entry["seal"] = hashlib.sha256(
                self._seal_material(entry)).hexdigest()

    def _verify_seal(self, entry, domain):
        version = entry.get("seal_v")
        material = self._seal_material(entry)
        if version == 2:
            if self._keyring_path is None:
                raise pmca_persistence.ArchiveLocked(
                    "entry names seal key %r but no keyring is "
                    "configured" % (entry.get("key_id"),))
            key_id, master = pmca_persistence._resolve_seal_key(
                self._keyring_path, entry.get("key_id"))
            subkey = pmca_persistence._state_subkey(
                master, key_id, domain)
            expect = hmac.new(subkey, material,
                              hashlib.sha256).hexdigest()
        elif version == 1:
            expect = hashlib.sha256(material).hexdigest()
        else:
            raise ValueError(
                "corrupt atomic store: unknown seal_v %r"
                % (version,))
        if not hmac.compare_digest(expect, entry.get("seal")
                                   or ""):
            raise ValueError(
                "corrupt atomic store: seal does not verify "
                "for %r" % (entry.get("entry_id")
                            or entry.get("incomplete_id"),))

    # -- low-level appends (one lock, fsync before return) --------------

    def _open_handles(self):
        for key, name in (("main", _MAIN_FILE),
                          ("journal", _JOURNAL_FILE),
                          ("incomplete", _INCOMPLETE_FILE)):
            path = self._path(name)
            if not os.path.exists(path):
                with open(path, "a", encoding="utf-8"):
                    pass
            self._handles[key] = open(path, "a",
                                      encoding="utf-8")

    def _write_line(self, handle, text):
        handle.write(text + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    def _require_writable(self):
        if self._closed:
            raise ValueError("entry store is closed")
        if self._read_only:
            raise EntryStoreLocked(
                "entry store was opened read-only")

    def _index_entry(self, entry):
        words = _extract_words({
            "input": entry.get("input"),
            "outcome": entry.get("outcome"),
            "steps": entry.get("steps")})
        for word in words:
            bucket = self._word_index.get(word)
            if bucket is None:
                self._word_index[word] = {entry["seq"]}
            else:
                bucket.add(entry["seq"])

    def _append_main_entry(self, entry):
        """The seal append: one canonical line, write+fsync,
        then the in-memory append — one critical section
        under the store lock (spec 6.1)."""
        with self.lock:
            self._write_line(self._handles["main"],
                             _canonical_text(entry))
            self._entries.append(entry)
            self._entries_by_id[entry["entry_id"]] = entry
            self._sealed_ids.add(entry["entry_id"])
            self._index_entry(entry)
            return entry

    def _journal_append(self, fields):
        with self.lock:
            seq = len(self._journal)
            prev = self._journal[-1]["hash"] if self._journal \
                else GENESIS
            rec = {"seq": seq, "prev_hash": prev}
            rec.update(fields)
            rec["hash"] = hashlib.sha256(_canonical(
                {k: v for k, v in rec.items()
                 if k != "hash"})).hexdigest()
            self._write_line(self._handles["journal"],
                             _canonical_text(rec))
            self._journal.append(rec)
            return rec

    # -- process lifecycle ----------------------------------------------

    def begin_process(self, surface, input, environment,
                      starting_state=None):
        """Begin a wrapped process (spec 4.4 step 1): allocate
        the entry_id and append the journal entry_open record,
        fsynced BEFORE the process runs. From this moment the
        process's existence is durable."""
        self._require_writable()
        if surface not in SURFACES:
            raise ValueError("surface not in %r" % (SURFACES,))
        environment = _validate_environment(environment)
        starting = _normalize_state(starting_state,
                                    _STARTING_KEYS,
                                    "starting_state")
        try:
            json.dumps(input)
        except (TypeError, ValueError):
            raise TypeError("input is not JSON-serializable")
        with self.lock:
            entry_id = "ENT-%06d" % self._entry_counter
            self._journal_append({
                "record_kind": "entry_open",
                "entry_id": entry_id,
                "surface": surface,
                "input": input,
                "environment": environment,
                "starting_state": starting})
            self._entry_counter += 1
            self._runs[entry_id] = {
                "open": self._journal[-1], "steps": [],
                "terminal": None}
            return ProcessHandle(self, entry_id)

    def _journal_step(self, handle, kind, data):
        self._require_writable()
        with self.lock:
            run = self._runs.get(handle.entry_id)
            if run is None or run["terminal"] is not None:
                raise ValueError(
                    "process %s is not in flight"
                    % handle.entry_id)
            if handle.sealed:
                raise ValueError(
                    "process %s is already sealed"
                    % handle.entry_id)
            try:
                json.dumps(data)
            except (TypeError, ValueError):
                raise TypeError("step data is not "
                                "JSON-serializable")
            step_seq = len(run["steps"])
            rec = self._journal_append({
                "record_kind": "step",
                "entry_id": handle.entry_id,
                "step_seq": step_seq,
                "kind": kind,
                "data": data})
            run["steps"].append({"step_seq": step_seq,
                                 "kind": kind, "data": data})
            return rec

    def _seal_process(self, handle, outcome, ending_state,
                      starting_state=None):
        self._require_writable()
        with self.lock:
            run = self._runs.get(handle.entry_id)
            if run is None or run["terminal"] is not None:
                raise ValueError(
                    "process %s is not in flight"
                    % handle.entry_id)
            if handle.sealed:
                raise ValueError(
                    "process %s is already sealed"
                    % handle.entry_id)
            if outcome is None:
                raise ValueError(
                    "a completed process must name its outcome")
            if starting_state is None:
                sealed_starting = run["open"]["starting_state"]
            else:
                # Seal-time supersession (the analyzer's
                # segment resolution): the SEALED entry carries
                # the caller-resolved starting state; the
                # journal's entry_open is untouched and remains
                # the begun-state record.
                sealed_starting = _normalize_state(
                    starting_state, _STARTING_KEYS,
                    "starting_state")
            entry = {
                "entry_v": ENTRY_V,
                "seq": len(self._entries),
                "entry_id": handle.entry_id,
                "prev_hash": self._entries[-1]["seal"]
                if self._entries else GENESIS,
                "kind": "process",
                "surface": run["open"]["surface"],
                "timestamp": _now(),
                "input": run["open"]["input"],
                "environment": run["open"]["environment"],
                "starting_state": sealed_starting,
                "steps": [dict(step) for step in run["steps"]],
                "outcome": outcome,
                "ending_state": _normalize_state(
                    ending_state, _ENDING_KEYS, "ending_state"),
            }
            self._seal_for_new(entry, "atomic_entry")
            self._append_main_entry(entry)
            self._journal_append({
                "record_kind": "entry_sealed",
                "entry_id": handle.entry_id,
                "main_seq": entry["seq"],
                "seal": entry["seal"]})
            run["terminal"] = "sealed"
            handle._sealed_entry = entry
            return entry

    @contextlib.contextmanager
    def process(self, surface, input, environment,
                starting_state=None):
        """Wrap a process in the entry lifecycle (spec 4.4).
        On clean exit the handle's outcome is sealed (an
        explicit handle.seal() inside the block takes
        precedence). On ANY exception — including a failing
        seal append — the process is filed incomplete
        immediately and the original exception re-raised
        unchanged (spec 4.6 trigger 1). Acknowledgement of an
        outcome happens only after its entry is durable."""
        handle = self.begin_process(surface, input, environment,
                                    starting_state)
        try:
            yield handle
        except BaseException as exc:
            self._file_run(handle.entry_id, "wrapper_exception",
                           "%s: %s" % (type(exc).__name__, exc))
            raise
        if not handle.sealed:
            try:
                handle.seal()
            except BaseException as exc:
                self._file_run(handle.entry_id,
                               "wrapper_exception",
                               "seal: %s: %s"
                               % (type(exc).__name__, exc))
                raise

    # -- incomplete filing ------------------------------------------------

    def _file_run(self, entry_id, detected_by, detail):
        """File one unterminated journal run incomplete, or
        retire it without filing when its entry already sealed
        in the main store (idempotent by entry_id — spec 4.4
        step 3). Returns the filed incomplete entry, or None
        when the run needed no filing."""
        with self.lock:
            run = self._runs.get(entry_id)
            if run is None or run["terminal"] is not None:
                return None
            if entry_id in self._sealed_ids:
                sealed = self._entries_by_id[entry_id]
                self._journal_append({
                    "record_kind": "entry_sealed",
                    "entry_id": entry_id,
                    "main_seq": sealed["seq"],
                    "seal": sealed["seal"]})
                run["terminal"] = "sealed"
                return None
            steps = [dict(step) for step in run["steps"]]
            inc = {
                "incomplete_v": INCOMPLETE_V,
                "seq": len(self._incomplete),
                "incomplete_id": "INC-%06d"
                % len(self._incomplete),
                "prev_hash": self._incomplete[-1]["seal"]
                if self._incomplete else GENESIS,
                "entry_id": entry_id,
                "surface": run["open"]["surface"],
                "input": run["open"]["input"],
                "environment": run["open"]["environment"],
                "starting_state": run["open"]["starting_state"],
                "steps": steps,
                "interruption": {
                    "last_step_seq": steps[-1]["step_seq"]
                    if steps else -1,
                    "journal_head_hash": steps_head_hash(steps),
                    "detected_by": detected_by,
                    "detail": detail},
                "status": "INCOMPLETE",
            }
            self._seal_for_new(inc, "incomplete_entry")
            self._write_line(self._handles["incomplete"],
                             _canonical_text(inc))
            self._incomplete.append(inc)
            self.incomplete_store._add(inc)
            self._journal_append({
                "record_kind": "entry_filed_incomplete",
                "entry_id": entry_id,
                "incomplete_id": inc["incomplete_id"]})
            run["terminal"] = "filed"
            return inc

    def _recover(self):
        """Open recovery (spec 4.6 trigger 2): holding the
        lock file is proof no live writer owns the store, so
        every unterminated journal run is dead. File each and
        return the filings."""
        filed = []
        order = [rec["entry_id"] for rec in self._journal
                 if rec.get("record_kind") == "entry_open"]
        for entry_id in order:
            inc = self._file_run(
                entry_id, "open_recovery",
                "journal run unterminated at exclusive open")
            if inc is not None:
                filed.append(inc)
        return filed

    def file_incomplete(self):
        """Explicit sweep (spec 4.6): re-run the recovery scan
        under this store's exclusivity. Idempotent by
        entry_id; returns the entries filed by THIS call."""
        self._require_writable()
        with self.lock:
            filed = []
            order = [rec["entry_id"] for rec in self._journal
                     if rec.get("record_kind") == "entry_open"]
            for entry_id in order:
                inc = self._file_run(
                    entry_id, "sweep",
                    "explicit file_incomplete sweep")
                if inc is not None:
                    filed.append(inc)
            return filed

    # -- segment lifecycle entries (spec 4.2) ---------------------------

    def _append_lifecycle(self, kind, extra, environment,
                          starting_state, input):
        self._require_writable()
        environment = _validate_environment(environment)
        starting = _normalize_state(starting_state,
                                    _STARTING_KEYS,
                                    "starting_state")
        with self.lock:
            entry_id = "ENT-%06d" % self._entry_counter
            entry = {
                "entry_v": ENTRY_V,
                "seq": len(self._entries),
                "entry_id": entry_id,
                "prev_hash": self._entries[-1]["seal"]
                if self._entries else GENESIS,
                "kind": kind,
                "timestamp": _now(),
                "input": input,
                "environment": environment,
                "starting_state": starting,
            }
            for key, value in extra.items():
                if key in entry or key in _SEAL_FIELDS:
                    raise ValueError(
                        "lifecycle field %r collides with the "
                        "entry format" % (key,))
                entry[key] = value
            self._entry_counter += 1
            self._seal_for_new(entry, "atomic_entry")
            return self._append_main_entry(entry)

    def append_segment_open(self, opening_fingerprint,
                            starting_state_blob, environment,
                            starting_state=None):
        """A segment_open entry: carries opening_fingerprint
        and the starting_state_blob — the serialized starting
        state INSIDE the entry (spec 4.2), written by the same
        append that opens the segment. There is no code path
        that opens a segment without it."""
        return self._append_lifecycle(
            "segment_open",
            {"opening_fingerprint": opening_fingerprint,
             "starting_state_blob": starting_state_blob},
            environment, starting_state, None)

    def append_segment_close(self, close_fields, environment,
                             starting_state=None):
        """A segment_close entry carrying the close fields
        exactly as the existing segment_close payload computes
        them (reason, engagements, first_seq, last_seq,
        segment_head, closing_fingerprint, key), so the key
        discipline is continuous with existing chains.
        first_seq/last_seq refer to entry seqs in this store."""
        return self._append_lifecycle(
            "segment_close", dict(close_fields), environment,
            starting_state, None)

    def append_segment_discard(self, segment_id, environment,
                               starting_state=None, reason=None):
        """A segment_discard entry: a discarded segment is
        marked by a record, never vaporized."""
        return self._append_lifecycle(
            "segment_discard",
            {"segment_id": segment_id, "reason": reason},
            environment, starting_state, None)

    # -- read views (expectation E4 names these) -------------------------

    def by_seq(self, seq):
        """Entry by main-store seq, or None."""
        with self.lock:
            if isinstance(seq, int) and 0 <= seq \
                    < len(self._entries):
                entry = self._entries[seq]
                if entry["seq"] == seq:
                    return entry
            return None

    def by_word(self, word):
        """Sealed entries whose input, outcome, or steps
        mention the word, in seq order."""
        word = str(word).lower()
        with self.lock:
            return [self._entries[seq]
                    for seq in sorted(
                        self._word_index.get(word, ()))]

    def replay(self):
        """All sealed entries in seq order. Deterministic."""
        with self.lock:
            return list(self._entries)


# ---------------------------------------------------------------------
# Module checks (run: python pmca_atomic_entries.py)
# ---------------------------------------------------------------------

_PASSED = []
_FAILED = []


def check(name, cond):
    (_PASSED if cond else _FAILED).append(name)
    print(("PASS " if cond else "FAIL ") + name)


def _suite_env(configured=True):
    return {
        "code_manifest_hash": "SUITE",
        "code_manifest": {"pmca_atomic_entries.py": "SUITE"},
        "dictionary": {
            "configured": configured,
            "db_hash": "SUITEDB" if configured else None,
            "source": "explicit" if configured else "off"}}


def _read_bytes(path):
    if not os.path.exists(path):
        return b""
    with open(path, "rb") as handle:
        return handle.read()


def run_tests():
    root = tempfile.mkdtemp(prefix="pmca_atomic_suite_")
    try:
        _run_suite(root)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    print("\n%d passed, %d failed"
          % (len(_PASSED), len(_FAILED)))
    return 1 if _FAILED else 0


def _run_suite(root):
    r1 = os.path.join(root, "plain")
    starting = {"segment_id": None, "checkpoint_entry_seq": None,
                "state_before_checksum": "BEFORE",
                "store_generation_before": None,
                "seed_generation_before": None}

    # -- append + chain verify -------------------------------------
    store = EntryStore.open(r1)
    with store.process("analysis",
                       {"sentence": "the wolf runs"},
                       _suite_env(), starting) as handle:
        handle.step("parse", {"tokens": ["the", "wolf", "runs"]})
        handle.step("verdict", {"verdict": "APPROVE"})
        handle.outcome = {"verdict": "APPROVE"}
    with store.process("teaching", {"sentence": "dogs bark"},
                       _suite_env()) as handle:
        handle.step("teach", {"word": "dogs"})
        handle.outcome = {"taught": True}
    handle = store.begin_process("composition",
                                 {"goal": "greeting"}, _suite_env())
    handle.step("compose", {"text": "hello wolf"})
    entry3 = handle.seal({"text": "hello wolf"})
    store.close()
    check("seal returns the entry", entry3["seq"] == 2
          and entry3["kind"] == "process")

    store = EntryStore.open(r1)
    entries = store.replay()
    check("append + reopen: 3 entries", len(entries) == 3)
    check("seqs contiguous from 0",
          [e["seq"] for e in entries] == [0, 1, 2])
    check("prev_hash chain: GENESIS then seals",
          entries[0]["prev_hash"] == GENESIS
          and entries[1]["prev_hash"] == entries[0]["seal"]
          and entries[2]["prev_hash"] == entries[1]["seal"])
    check("steps recorded inside the entry",
          [s["kind"] for s in entries[0]["steps"]]
          == ["parse", "verdict"])
    check("by_seq door", store.by_seq(1)["surface"] == "teaching"
          and store.by_seq(9) is None)
    check("by_word door",
          [e["seq"] for e in store.by_word("wolf")] == [0, 2])
    check("verify_chain", store.verify_chain() is True)

    # -- A4 seal completeness over this store's entries -------------
    check("A4: process outcomes non-null",
          all(e["outcome"] is not None for e in entries
              if e["kind"] == "process"))
    check("A4: seal_v present on every entry",
          all(e.get("seal_v") in (1, 2) for e in entries))
    check("A4: entry_ids unique",
          len({e["entry_id"] for e in entries}) == len(entries))

    # -- seal_v 1 fallback without a keyring -------------------------
    check("seal_v 1 unkeyed fallback",
          all(e["seal_v"] == 1 and e["key_id"] is None
              for e in entries))

    # -- environment refusal (spec 4.5) ------------------------------
    journal_path = os.path.join(r1, "atomic", _JOURNAL_FILE)
    journal_before = _read_bytes(journal_path)
    refused = False
    try:
        store.begin_process("analysis", {"x": 1},
                            {"code_manifest_hash": "X",
                             "code_manifest": {}})
    except ValueError:
        refused = True
    check("environment without a dictionary block refused",
          refused)
    check("refusal wrote no journal record",
          _read_bytes(journal_path) == journal_before)
    store.close()

    # -- segment lifecycle entries -----------------------------------
    store = EntryStore.open(r1)
    opened = store.append_segment_open(
        "FP-OPEN", "BLOB-STATE", _suite_env(), starting)
    closed = store.append_segment_close(
        {"reason": "count", "engagements": 2, "first_seq": 0,
         "last_seq": 1, "segment_head": "HEAD",
         "closing_fingerprint": "FP-CLOSE", "key": "KEY"},
        _suite_env(), starting)
    check("segment_open carries fingerprint + blob in-entry",
          opened["kind"] == "segment_open"
          and opened["opening_fingerprint"] == "FP-OPEN"
          and opened["starting_state_blob"] == "BLOB-STATE"
          and "steps" not in opened and "outcome" not in opened)
    check("segment_close carries the close fields",
          closed["kind"] == "segment_close"
          and closed["closing_fingerprint"] == "FP-CLOSE"
          and closed["first_seq"] == 0
          and closed["last_seq"] == 1)
    store.close()

    # -- keyring: seal_v 2, load REQUIRES the keyring ----------------
    r2 = os.path.join(root, "keyed")
    keyring_path = os.path.join(root, "keyring.json")
    pmca_persistence.create_keyring(keyring_path)
    store = EntryStore.open(r2, keyring_path=keyring_path)
    with store.process("analysis", {"sentence": "keyed wolf"},
                       _suite_env()) as handle:
        handle.step("parse", {"tokens": ["keyed", "wolf"]})
        handle.outcome = {"verdict": "HOLD"}
    keyed = store.replay()[0]
    check("seal_v 2 with keyring", keyed["seal_v"] == 2
          and keyed["key_id"] == "k1")
    store.close()
    locked = False
    try:
        EntryStore.open(r2)
    except pmca_persistence.ArchiveLocked:
        locked = True
    check("load without the keyring raises ArchiveLocked",
          locked)
    store = EntryStore.open(r2, keyring_path=keyring_path)
    check("load with the keyring verifies",
          store.verify_chain() is True)
    store.close()

    # -- crash recovery: dead journal run filed exactly once ---------
    r3 = os.path.join(root, "crash")
    store = EntryStore.open(r3)
    handle = store.begin_process(
        "analysis", {"sentence": "the dead process spoke"},
        _suite_env())
    handle.step("parse", {"tokens": ["the", "dead"]})
    handle.step("bind", {"slot::dead::target": "unfilled"})
    main_before = _read_bytes(
        os.path.join(r3, "atomic", _MAIN_FILE))
    store.close()   # closing files nothing: death is the journal's
    store = EntryStore.open(r3)
    check("recovery surfaced on open", len(store.recoveries) == 1)
    filed = store.recoveries[0]
    check("filed input byte-exact",
          filed["input"] == {"sentence": "the dead process spoke"})
    check("filed steps byte-exact, in order",
          [s["kind"] for s in filed["steps"]] == ["parse", "bind"])
    check("main store untouched by the death",
          _read_bytes(os.path.join(r3, "atomic", _MAIN_FILE))
          == main_before and store.replay() == [])
    check("A6: journal head hash recomputes from filed steps",
          filed["interruption"]["journal_head_hash"]
          == steps_head_hash(filed["steps"]))
    check("interruption names last step + detector",
          filed["interruption"]["last_step_seq"] == 1
          and filed["interruption"]["detected_by"]
          == "open_recovery")
    store.close()

    # -- E3 shape: re-recovery idempotent ------------------------------
    store = EntryStore.open(r3)
    check("second open files nothing new",
          store.recoveries == [])
    check("explicit sweep files nothing new",
          store.file_incomplete() == [])
    check("incomplete count still exactly one",
          len(store.incomplete_store.by_status("INCOMPLETE"))
          == 1)
    store.close()

    # -- B4 shape: read-only files nothing; exclusivity loud -----------
    r4 = os.path.join(root, "locking")
    store_a = EntryStore.open(r4)
    handle = store_a.begin_process(
        "teaching", {"sentence": "still in flight"}, _suite_env())
    handle.step("teach", {"word": "flight"})
    incomplete_path = os.path.join(r4, "atomic", _INCOMPLETE_FILE)
    incomplete_before = _read_bytes(incomplete_path)
    refused = False
    try:
        EntryStore.open(r4)
    except EntryStoreLocked:
        refused = True
    check("second exclusive open raises EntryStoreLocked",
          refused)
    readonly = EntryStore.open(r4, read_only=True)
    check("read-only open succeeds beside a live writer", True)
    check("read-only open filed nothing",
          _read_bytes(incomplete_path) == incomplete_before
          and readonly.recoveries == [])
    ro_refused = False
    try:
        readonly.file_incomplete()
    except EntryStoreLocked:
        ro_refused = True
    check("read-only store refuses mutation", ro_refused)
    readonly.close()
    store_a.close()
    store_b = EntryStore.open(r4)
    check("after release, exclusive open recovers the run",
          len(store_b.recoveries) == 1
          and store_b.recoveries[0]["surface"] == "teaching")
    store_b.close()

    # -- wrapper exception files immediately (A3 shape) ----------------
    r5 = os.path.join(root, "wrap")
    store = EntryStore.open(r5)
    raised = None
    try:
        with store.process("composition", {"goal": "boom"},
                           _suite_env()) as handle:
            handle.step("compose", {"draft": "x"})
            raise RuntimeError("simulated death")
    except RuntimeError as exc:
        raised = exc
    check("original exception re-raised unchanged",
          raised is not None and str(raised) == "simulated death")
    check("wrapper filed the process incomplete immediately",
          len(store.incomplete_store.by_status("INCOMPLETE"))
          == 1)
    check("recovery after wrapper filing is a no-op",
          store.file_incomplete() == [])
    store.close()

    # -- E1 shape: the six incomplete doors -----------------------------
    r6 = os.path.join(root, "doors")
    store = EntryStore.open(r6)

    def _die(surface, payload, steps):
        try:
            with store.process(surface, payload,
                               _suite_env()) as handle:
                for kind, data in steps:
                    handle.step(kind, data)
                raise RuntimeError("die")
        except RuntimeError:
            pass

    _die("analysis",
         {"sentence": "a wolf waits",
          "graph": [1, "A", 1]},
         [("parse", {"tokens": ["a", "wolf"]}),
          ("gap", {"gap_key": "slot::wolf::is_a",
                   "state": "HOLD"})])
    _die("teaching", {"sentence": "teach the fox"},
         [("teach", {"word": "fox"})])
    _die("composition", {"goal": "a wolf story"},
         [("compose", {"draft": "wolf"})])
    doors = store.incomplete_store
    check("doors: by_entry_id",
          doors.by_entry_id("ENT-000000")["surface"]
          == "analysis"
          and doors.by_entry_id("ENT-999999") is None)
    check("doors: by_word",
          [r["surface"] for r in doors.by_word("wolf")]
          == ["analysis", "composition"])
    check("doors: by_status",
          len(doors.by_status("INCOMPLETE")) == 3
          and doors.by_status("OTHER") == [])
    check("doors: by_surface",
          [r["entry_id"] for r in doors.by_surface("teaching")]
          == ["ENT-000001"])
    check("doors: by_gap",
          [r["surface"] for r in doors.by_gap("wolf", "is_a")]
          == ["analysis"])
    check("doors: by_coordinate",
          [r["surface"] for r in doors.by_coordinate((1, "A", 1))]
          == ["analysis"]
          and doors.by_coordinate((9, "Z", 9)) == [])
    store.close()

    # -- E5: public surface inspection ----------------------------------
    public = {name for name, _ in inspect.getmembers(
        IncompleteStore, predicate=inspect.isfunction)
        if not name.startswith("_")}
    check("E5: IncompleteStore exposes exactly the six doors",
          public == {"by_entry_id", "by_word", "by_status",
                     "by_surface", "by_gap", "by_coordinate"})
    bad_verbs = re.compile(
        r"(repair|fix_|edit_|mutate|set_status|re_status|"
        r"complete_entry|resolve_)")
    module_public = [name for name, fn in inspect.getmembers(
        sys.modules[__name__], predicate=inspect.isfunction)
        if not name.startswith("_")]
    check("E5: no public function mutates an incomplete entry",
          not any(bad_verbs.search(name)
                  for name in module_public))

    # -- A2 shape: torn seal append -------------------------------------
    r7 = os.path.join(root, "torn")
    store = EntryStore.open(r7)
    handle = store.begin_process(
        "analysis", {"sentence": "torn at the seal"},
        _suite_env())
    handle.step("parse", {"tokens": ["torn"]})
    store.close()
    main_path = os.path.join(r7, "atomic", _MAIN_FILE)
    with open(main_path, "a", encoding="utf-8") as fh:
        fh.write('{"entry_v":1,"seq":0,"entry_id":"ENT-0000')
    store = EntryStore.open(r7)
    check("A2: torn final line dropped, chain intact",
          store.replay() == [] and store.verify_chain() is True)
    check("A2: torn run filed incomplete exactly once",
          len(store.recoveries) == 1)
    with store.process("analysis",
                       {"sentence": "torn at the seal"},
                       _suite_env()) as handle:
        handle.step("parse", {"tokens": ["torn"]})
        handle.outcome = {"verdict": "APPROVE"}
    check("A2: rerun seals exactly one entry",
          len(store.replay()) == 1)
    store.close()
    store = EntryStore.open(r7)
    check("A2: reopening changes nothing",
          store.recoveries == [] and len(store.replay()) == 1
          and len(store.incomplete_store.by_status("INCOMPLETE"))
          == 1)
    store.close()

    # -- B1 shape (scaled): threads on one store -------------------------
    r8 = os.path.join(root, "threads")
    store = EntryStore.open(r8)
    errors = []

    def _worker(n):
        try:
            for i in range(10):
                with store.process(
                        "analysis",
                        {"sentence": "worker %d run %d" % (n, i)},
                        _suite_env()) as handle:
                    handle.step("work", {"n": n, "i": i})
                    handle.outcome = {"done": True}
        except Exception as exc:  # collected, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(n,))
               for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    entries = store.replay()
    check("threads: no errors", errors == [])
    check("threads: 80 entries, contiguous seqs",
          len(entries) == 80
          and [e["seq"] for e in entries] == list(range(80)))
    check("threads: chain verifies", store.verify_chain() is True)
    store.close()

    # -- canonical totality (A3 fix): fallback round-trip ------------
    plain = {"sentence": "café — the wolf runs",
             "nested": {"n": [1, 2.5, None, True]}}
    check("canonical: fallback-free objects byte-identical",
          _canonical(plain) == json.dumps(
              plain, sort_keys=True, separators=(",", ":"),
              ensure_ascii=False).encode("utf-8"))
    r9 = os.path.join(root, "surrogate")
    surrogate_sentence = "He breaks the \ud800 cup."
    store = EntryStore.open(r9)
    with store.process("analysis",
                       {"sentence": surrogate_sentence},
                       _suite_env()) as handle:
        handle.step("trace", "token \ud800 seen")
        handle.outcome = {"verdict": "HOLD"}
    sealed = store.replay()[0]
    check("canonical: surrogate record seals and restores "
          "byte-exact",
          sealed["input"]["sentence"] == surrogate_sentence
          and sealed["steps"][0]["data"] == "token \ud800 seen")
    store.close()
    store = EntryStore.open(r9)
    check("canonical: surrogate store re-verifies on reopen",
          store.verify_chain() is True
          and store.replay()[0]["input"]["sentence"]
          == surrogate_sentence)
    raised = None
    try:
        with store.process("analysis",
                           {"sentence": surrogate_sentence},
                           _suite_env()) as handle:
            handle.step("trace", "about to die \ud800")
            raise RuntimeError("death beside a surrogate")
    except RuntimeError as exc:
        raised = exc
    filed = store.incomplete_store.by_status("INCOMPLETE")
    check("canonical: surrogate death files immediately, "
          "byte-exact",
          raised is not None and len(filed) == 1
          and filed[0]["input"]["sentence"]
          == surrogate_sentence
          and filed[0]["steps"][0]["data"]
          == "about to die \ud800")
    store.close()

    # -- seal-time starting_state supersession (B5 accessor) ---------
    r10 = os.path.join(root, "sealstamp")
    store = EntryStore.open(r10)
    begun = {"segment_id": 1, "checkpoint_entry_seq": 10,
             "state_before_checksum": "BEGIN",
             "store_generation_before": None,
             "seed_generation_before": None}
    handle = store.begin_process(
        "analysis", {"sentence": "stamp at seal"}, _suite_env(),
        begun)
    handle.step("trace", "one step")
    stamped = handle.seal(
        {"verdict": "APPROVE"},
        starting_state={"segment_id": 2,
                        "checkpoint_entry_seq": 20,
                        "state_before_checksum": "BEGIN"})
    check("seal stamp: entry carries the seal-time segment",
          stamped["starting_state"]["segment_id"] == 2
          and stamped["starting_state"]["checkpoint_entry_seq"]
          == 20
          and stamped["starting_state"]["state_before_checksum"]
          == "BEGIN")
    journal_open = [r for r in store._journal
                    if r.get("record_kind") == "entry_open"][-1]
    check("seal stamp: journal keeps the begun-state record",
          journal_open["starting_state"]["segment_id"] == 1
          and journal_open["starting_state"]
          ["checkpoint_entry_seq"] == 10)
    store.close()


if __name__ == "__main__":
    sys.exit(run_tests())
