"""PMCA Write-In Storage - the durable write-in store.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's
architecture.

Direction (Jonathan, 2026-10-22): "We need to write in for
storage." The write-in store built by pmca_write_in lives in
memory for a run and dies with the process; the entries it
accumulates are the system's experience and must survive it.
This module is the storage layer for that store, and nothing
else: it introduces NO second store format. A stored store's
payload is exactly the pmca_write_in store object; P1 wraps
that payload in a single-file seal envelope (below).

Discipline carried over from the persistence rulings:

- WRITE-THROUGH: every mutating operation on a StoredStore
  delegates to the real write-in / population function and
  then saves, so the file on disk always holds the store as
  of the last completed operation.
- STRICT LOAD: a file is accepted only under the envelope
  rules or the legacy rules below. Anything else raises
  StorageError. A failed load never modifies the file.
- ENVELOPE SEAL (P1, 2026-10-25; mechanism found and drafted
  by Gemini 4 Argon, D-10/D-08 lineage recorded by Alder,
  ruled by Jonathan): strict load of a bare canonical file
  proves a file's FORM, not its CONTENT - a single-bit change
  inside a stored value or the seq digits preserves canonical
  form and would otherwise load silently. Every save
  therefore writes ONE envelope file:
  {"seal_v": 1, "sha256": <hex>, "payload": <store object>}.
  The payload is serialized canonically (sort_keys=True,
  separators=(',', ':'), ensure_ascii=False, UTF-8 bytes),
  sha256 is the SHA-256 hex digest of those exact payload
  bytes, and the file's text is the canonical serialization
  of the envelope object itself under the same settings, with
  no trailing newline. Load of an envelope requires the file
  text to equal that canonical envelope serialization, the
  digest to match the canonical payload bytes, and the
  payload to pass the store shape checks (entries / seq /
  SELF_KEY); any failure raises StorageError and the file is
  untouched. The digest is a plain digest, not a keyed seal,
  so it guards content against corruption and accidental
  alteration; it does not authenticate against a writer who
  recomputes it. Crash note: the envelope is written by a
  single atomic replace (temp + flush/fsync + os.replace +
  directory fsync), so the on-disk state is always a complete
  old envelope or a complete new envelope - never a payload
  paired with a foreign digest. A stray temp file left by an
  interrupted write is inert and is never loaded.
- LEGACY ADOPTION (never a brick): a store in either legacy
  form - unsealed canonical JSON, or canonical JSON + a
  sidecar ".seal" file verified against the file's digest
  when present (a sidecar mismatch is refused, exactly as
  before P1) - loads under its existing seal_state semantics
  ("adopted_unsealed" / "verified") and is rewritten as an
  envelope on its first save under the new code. That save
  retires the sidecar: a pre-existing sidecar file for the
  path is removed after the envelope write succeeds.
- BOOT ADOPTION: opening a StoredStore never writes. A path
  that does not exist starts as a new empty store IN MEMORY
  only; no file appears until the first mutation. Opening an
  existing store and making only read calls leaves its bytes
  untouched.
- STALE SNAPSHOT REFUSAL (Pack D, 2026-10-26): a StoredStore
  remembers the generation it loaded (the SHA-256 of the file
  bytes, or None when no file existed). save() compares the
  file's current generation against the remembered one and
  refuses with StorageError naming the stale snapshot when
  they differ: another writer committed since this object
  loaded, and overwriting would silently discard their
  commits. A successful save advances the remembered
  generation. BOUNDARY (amended 2026-10-27, atomic-entry
  pack spec 6.3; ST-03/ST-04): the comparison and the write
  are now executed inside a lock. save() acquires the one
  RLock shared by every StoredStore for the path (a
  module-level per-path registry) and, where fcntl is
  available, an advisory flock on the "<path>.lock" sidecar;
  the generation is re-read inside both locks and only then
  compared and written. The check and the replace are one
  critical section in-process and cross-process: of two
  racing writers exactly one commits, and the other's
  mutation is refused loudly at save time, NOT acknowledged.
  Where fcntl is unavailable (non-POSIX), save() degrades
  to the registry RLock alone and the cross-process half of
  the guarantee does not hold. A refused writer's remedy is
  unchanged: reopen and reapply.
- ATOMIC SAVE: the envelope text goes to a temp file in the
  same directory, is flushed and fsynced, and os.replace
  moves it onto the store path; the directory is fsynced
  when the platform allows. No temp files are left behind.

Reads that find nothing missing never save: entry_view,
binding_for, and entry_state are exactly as read-only here
as on an in-memory store. COMPOSING is read-only at the
composer layer (the composer never writes), but at THIS
surface a composed record's named gaps are recorded
through pmca_write_in.record_composer_hold and written
through (Pack F+B, 2026-10-26; claims-13 H1/D9): a HOLD —
or any record naming a mappable gap — never leaves this
store unrecorded. The surfacing consumes one store seq,
exactly as the bridge documents.

Deterministic. Stdlib only at module level; sibling modules
are imported inside the functions, methods, and the check
battery, never at import time.
"""

import hashlib
import json
import os
import tempfile
import threading

try:
    import fcntl
except ImportError:
    # Non-POSIX platform: no flock. StoredStore.save()
    # degrades to the per-path registry RLock alone (see
    # the module header, STALE SNAPSHOT REFUSAL boundary).
    fcntl = None


class StorageError(Exception):
    """A store file could not be loaded strictly, or a save
    could not be completed atomically."""


def seal_path_for(path):
    """The sidecar seal path for a store path: the store
    path with ".seal" appended. Retained for legacy stores:
    a legacy sidecar is verified on load and removed by the
    first envelope save."""
    return str(path) + ".seal"


def _digest_text(text):
    """The legacy seal digest of a store file's exact text:
    SHA-256 hex of its UTF-8 bytes. Canonical legacy store
    text is ASCII (json ensure_ascii), so this is the digest
    of the file bytes themselves."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_text(obj):
    """The P1 canonical serialization: sort_keys=True,
    separators=(',', ':'), ensure_ascii=False."""
    return json.dumps(obj, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


def _canonical_bytes(obj):
    """The P1 canonical serialization as UTF-8 bytes."""
    return _canonical_text(obj).encode("utf-8")


_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _is_envelope(obj):
    """True only for a well-formed P1 envelope object: keys
    exactly {"seal_v", "sha256", "payload"}, seal_v == 1,
    sha256 a 64-char hex string, payload a dict."""
    return (isinstance(obj, dict)
            and set(obj.keys()) == {"seal_v", "sha256", "payload"}
            and obj["seal_v"] == 1
            and not isinstance(obj["seal_v"], bool)
            and isinstance(obj["sha256"], str)
            and len(obj["sha256"]) == 64
            and all(c in _HEX_DIGITS for c in obj["sha256"])
            and isinstance(obj["payload"], dict))


def _check_store_shape(store, path):
    """The existing store shape checks (entries / seq /
    SELF_KEY), shared by the envelope and legacy load paths."""
    import pmca_write_in as wi
    if (not isinstance(store, dict) or "entries" not in store
            or "seq" not in store
            or not isinstance(store["entries"], dict)
            or wi.SELF_KEY not in store["entries"]):
        raise StorageError("not a write-in store: " + str(path))


# ---------------------------------------------------------------------
# Strict load / atomic save
# ---------------------------------------------------------------------

def _load_with_state(path):
    """Load a write-in store from path, strictly, and report
    its file form deterministically. Returns (store, state)
    where state is "enveloped" for a P1 envelope, "verified"
    for a legacy store whose sidecar seal matched, or
    "adopted_unsealed" for a legacy store with no sidecar.
    Any failure raises StorageError; the file is never
    modified."""
    import pmca_write_in as wi
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise StorageError("cannot read store file: "
                           + str(path) + ": " + str(exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StorageError("store file is not valid UTF-8: "
                           + str(path) + ": " + str(exc))
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise StorageError("store file is not valid JSON: "
                           + str(path) + ": " + str(exc))
    if _is_envelope(parsed):
        if text != _canonical_text(parsed):
            raise StorageError(
                "store envelope is not in canonical form: "
                + str(path))
        payload = parsed["payload"]
        if parsed["sha256"] != hashlib.sha256(
                _canonical_bytes(payload)).hexdigest():
            raise StorageError("store envelope digest mismatch: "
                               + str(path))
        _check_store_shape(payload, path)
        return payload, "enveloped"
    # Legacy form: the pre-P1 strict path, unchanged.
    store = parsed
    _check_store_shape(store, path)
    if text != wi.canonical(store):
        raise StorageError("store file is not in canonical form: "
                           + str(path))
    seal_path = seal_path_for(path)
    if os.path.exists(seal_path):
        try:
            with open(seal_path, "r", encoding="utf-8") as f:
                seal = f.read().strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise StorageError("cannot read store seal file: "
                               + seal_path + ": " + str(exc))
        if seal != _digest_text(text):
            raise StorageError("store seal mismatch: "
                               + str(path))
        return store, "verified"
    return store, "adopted_unsealed"


def load_store(path):
    """Load a write-in store from path, strictly. An envelope
    file must be in canonical envelope form, its digest must
    match the canonical payload bytes, and its payload must
    pass the store shape checks. A legacy file must parse as
    JSON, pass pmca_write_in.load's shape checks, and be
    byte-identical to pmca_write_in's canonical serialization
    of the parsed store; when a sidecar seal file exists, the
    file's SHA-256 digest must also match it. Any failure
    raises StorageError; the file is never modified."""
    store, _state = _load_with_state(path)
    return store


def _write_atomic(text, path, prefix):
    """Write text to path atomically: a temp file in the
    same directory, flush + fsync, os.replace onto path,
    then a directory fsync when available. No temp file is
    left behind, on success or on failure. Raises OSError."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory,
                                    prefix=prefix,
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def save_store(store, path):
    """Save store to path atomically as ONE P1 envelope file:
    {"seal_v": 1, "sha256": <hex>, "payload": <store>}, where
    sha256 is the SHA-256 hex digest of the payload's
    canonical bytes and the file text is the canonical
    serialization of the envelope object (no trailing
    newline). After a successful envelope write, a
    pre-existing legacy sidecar seal file for this path is
    removed; if that removal fails for a reason other than
    the sidecar being absent, StorageError is raised naming
    the sidecar."""
    payload_bytes = _canonical_bytes(store)
    envelope = {"seal_v": 1,
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "payload": store}
    text = _canonical_text(envelope)
    try:
        _write_atomic(text, path, ".pmca-store-")
    except OSError as exc:
        raise StorageError("cannot save store file: "
                           + str(path) + ": " + str(exc))
    seal_path = seal_path_for(path)
    try:
        os.unlink(seal_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StorageError("cannot remove legacy store seal "
                           "file: " + seal_path + ": " + str(exc))


def _file_generation(path):
    """The generation token of the store file at path: the
    SHA-256 hex digest of its exact bytes, or None when no
    file exists. StoredStore compares generations at save
    time to refuse a stale snapshot (see the module header,
    STALE SNAPSHOT REFUSAL). An unreadable file raises
    StorageError: a generation that cannot be read cannot be
    compared, and an uncompared save is exactly what the
    refusal exists to prevent."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StorageError("cannot read store file for "
                           "generation check: " + str(path)
                           + ": " + str(exc))


# ---------------------------------------------------------------------
# Save serialization (atomic-entry pack, spec 6.3): one RLock
# per store path, shared by every StoredStore object for that
# path, so the generation check and the envelope write in
# save() are one critical section. Access to the registry
# itself is guarded by a plain lock.
# ---------------------------------------------------------------------

_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path):
    """The one RLock shared by every StoredStore whose store
    file is at path (the registry is keyed by the path's
    absolute form, so different spellings of one file share
    the lock)."""
    key = os.path.abspath(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _lock_file_for(path):
    """The advisory-lock sidecar path for a store path: the
    store path with ".lock" appended. The file carries no
    content and is never loaded; it exists so flock has one
    stable inode to serialize on while the envelope file
    itself is atomically replaced on every save."""
    return str(path) + ".lock"


def _entry_environment():
    """The entry_v 1 environment for entries written from this
    module (atomic-entry pack, spec 4.5): the ONE manifest
    helper is the analyzer's, imported here at call time per
    this module's import discipline (sibling modules are never
    imported at import time), so every surface's entries name
    their code and dictionary environment the same way."""
    import pmca_sentence_analyzer as analyzer
    return analyzer._entry_environment()


# ---------------------------------------------------------------------
# A write-in store bound to a file
# ---------------------------------------------------------------------

class StoredStore:
    """A pmca_write_in store whose every mutation is written
    through to one envelope JSON file. Opening adopts: an
    existing file is loaded strictly (envelope, or either
    legacy form); a missing path starts as a new empty store
    in memory and creates nothing. The object remembers the
    generation it loaded and its saves refuse if the file has
    since changed (STALE SNAPSHOT REFUSAL, module header).

    Atomic-entry wiring (atomic-entry pack, build step 4,
    2026-10-27; spec sections 4.5, 5, 8): the optional
    entry_store parameter is the host's adoption act for this
    surface -- an already-open pmca_atomic_entries.EntryStore,
    or a state root to open one at (its files live under
    <root>/atomic/). With none given (the default), every
    wrapper behaves exactly as before the pack. With one,
    each mutating wrapper call (and each compose call) runs
    as one process entry: see _entry_wrapped."""

    def __init__(self, path, entry_store=None):
        import pmca_write_in as wi
        self.path = str(path)
        if os.path.exists(self.path):
            self.store, self.seal_state = _load_with_state(
                self.path)
        else:
            self.store = wi.new_store()
            self.seal_state = "new"
        self._generation = _file_generation(self.path)
        self._entry_store = None
        if entry_store is not None:
            import pmca_atomic_entries as ae
            if isinstance(entry_store, ae.EntryStore):
                self._entry_store = entry_store
            else:
                self._entry_store = ae.EntryStore.open(
                    str(entry_store))

    def save(self):
        """Write the current store through to the file, as a
        P1 envelope (retiring any legacy sidecar). The
        generation comparison and the write are ONE critical
        section (atomic-entry pack, spec 6.3): this path's
        registry RLock is acquired, then an advisory flock on
        the "<path>.lock" sidecar, and the file's generation
        is re-read inside both locks before it is compared
        against the generation this object loaded. Refuses
        with StorageError when they differ: another writer
        committed since the load, and overwriting would
        silently discard their commits (module header, STALE
        SNAPSHOT REFUSAL). A successful save advances this
        object's generation to the file it just wrote. Where
        fcntl is unavailable the flock is skipped and the
        section is guarded by the RLock alone."""
        with _path_lock(self.path):
            lock_fd = None
            try:
                if fcntl is not None:
                    lock_path = _lock_file_for(self.path)
                    try:
                        lock_fd = os.open(
                            lock_path,
                            os.O_RDWR | os.O_CREAT, 0o644)
                    except OSError as exc:
                        raise StorageError(
                            "cannot open store lock file: "
                            + lock_path + ": " + str(exc))
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                if _file_generation(self.path) \
                        != self._generation:
                    raise StorageError(
                        "stale store snapshot: the store file "
                        "changed on disk after this StoredStore "
                        "loaded it (generation mismatch); "
                        "refusing to overwrite newer commits: "
                        + self.path)
                save_store(self.store, self.path)
                self._generation = _file_generation(self.path)
                self.seal_state = "enveloped"
            finally:
                if lock_fd is not None:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                    os.close(lock_fd)

    # -- Entry wiring (atomic-entry pack, build step 4): one
    # process entry per wrapper call, when configured. --

    def _entry_wrapped(self, surface, call, arguments, op):
        """Run one wrapper call (op: the delegate + save, which
        returns the wrapper's result) as an atomic-entry
        process when an entry store is configured for this
        store; otherwise run it exactly as before the pack.

        The entry brackets the op: input is the wrapper name
        and its arguments verbatim; store_generation_before is
        the store file's generation token read before the op
        and store_generation_after the token after it (the
        pair makes a refused or lost state save distinguishable
        from a committed one after the fact); steps are the
        pmca_write_in self-records in the seq range the op
        consumed (the store's seq before and after bracket the
        range; the records in it, in order); outcome is the
        wrapper's return value verbatim. If the op raises --
        the stale-snapshot StorageError at save time is the
        standing case -- the entry context files the process
        incomplete and the exception propagates unchanged: the
        mutation was never acknowledged, and both the entry
        store and the refusal say so."""
        if self._entry_store is None:
            return op()
        import pmca_write_in as wi
        generation_before = _file_generation(self.path)
        seq_before = self.store["seq"]
        with self._entry_store.process(
                surface, {"wrapper": call, "arguments": arguments},
                _entry_environment(),
                {"store_generation_before": generation_before}) \
                as handle:
            result = op()
            seq_after = self.store["seq"]
            self_records = \
                self.store["entries"][wi.SELF_KEY]["encounters"]
            for record in self_records:
                if seq_before < record["seq"] <= seq_after:
                    handle.step("self_record", record)
            handle.outcome = result
            handle.ending_state = {
                "store_generation_after":
                    _file_generation(self.path)}
        return result

    # -- Mutating wrappers: delegate, then write through. --

    def populate_word(self, word, source="encounter"):
        import pmca_population as pop

        def _op():
            result = pop.populate_word(self.store, word, source)
            self.save()
            return result

        return self._entry_wrapped(
            "population", "populate_word",
            {"word": word, "source": source}, _op)

    def encounter(self, sentence, noun_result, verb_result):
        import pmca_population as pop

        def _op():
            result = pop.encounter(self.store, sentence,
                                   noun_result, verb_result)
            self.save()
            return result

        return self._entry_wrapped(
            "population", "encounter",
            {"sentence": sentence, "noun_result": noun_result,
             "verb_result": verb_result}, _op)

    def enrich_from_dictionary(self, word):
        import pmca_population as pop

        def _op():
            result = pop.enrich_from_dictionary(self.store, word)
            self.save()
            return result

        return self._entry_wrapped(
            "population", "enrich_from_dictionary",
            {"word": word}, _op)

    def write_fill(self, word, identifier, value, evidence):
        import pmca_write_in as wi

        def _op():
            result = wi.write_fill(self.store, word, identifier,
                                   value, evidence)
            self.save()
            return result

        return self._entry_wrapped(
            "write_in", "write_fill",
            {"word": word, "identifier": identifier,
             "value": value, "evidence": evidence}, _op)

    def record_gap(self, word, identifier, reason):
        import pmca_write_in as wi

        def _op():
            result = wi.record_gap(self.store, word, identifier,
                                   reason)
            self.save()
            return result

        return self._entry_wrapped(
            "write_in", "record_gap",
            {"word": word, "identifier": identifier,
             "reason": reason}, _op)

    # -- Read wrappers: never save. --

    def entry_view(self, word):
        import pmca_write_in as wi
        return wi.entry_view(self.store, word)

    def binding_for(self, subject, verb):
        import pmca_write_in as wi
        return wi.binding_for(self.store, subject, verb)

    def entry_state(self, word):
        import pmca_population as pop
        return pop.entry_state(self.store, word)

    # -- Compose wrappers: the pure composer reads; this
    # surface records the record's named gaps (bridge +
    # write-through) whenever it names any (Pack F+B,
    # 2026-10-26; claims-13 H1). --

    def _compose_and_record(self, record):
        import pmca_write_in as wi
        if record.get("gaps"):
            wi.record_composer_hold(self.store, record)
            self.save()
        return record

    def compose_binding(self, subject, verb):
        import pmca_composer as comp
        return self._entry_wrapped(
            "composition", "compose_binding",
            {"subject": subject, "verb": verb},
            lambda: self._compose_and_record(
                comp.compose_binding(self.store, subject, verb)))

    def compose_who(self, verb, target):
        import pmca_composer as comp
        return self._entry_wrapped(
            "composition", "compose_who",
            {"verb": verb, "target": target},
            lambda: self._compose_and_record(
                comp.compose_who(self.store, verb, target)))

    def compose_what_is(self, word):
        import pmca_composer as comp
        return self._entry_wrapped(
            "composition", "compose_what_is",
            {"word": word},
            lambda: self._compose_and_record(
                comp.compose_what_is(self.store, word)))


# ---------------------------------------------------------------------
# Test support: the restart proof's second process.
#   python3 pmca_write_in_storage.py --verify-restart \
#       <path> <expected_hash> <expected_seq>
# Opens the store fresh in THIS process, asserts what phase 1
# stored, answers one composed question from the reopened
# store, then performs a second wolf encounter (real finders,
# write-through) and prints the new hash and seq as
# "RESTART_RESULT {json}".
# ---------------------------------------------------------------------

def _verify_restart(path, expected_hash, expected_seq):
    import pmca_sentence_analyzer as _analyzer
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    _analyzer.configure_dictionary(db)

    import pmca_noun_finder as noun_finder
    import pmca_verb_finder as verb_finder
    import pmca_write_in as wi

    def require(cond, label):
        if not cond:
            raise StorageError("restart verification failed: "
                               + label)

    ss = StoredStore(path)
    require(wi.store_hash(ss.store) == expected_hash,
            "store hash differs after restart")
    require(ss.store["seq"] == expected_seq,
            "seq differs after restart")
    for word in ("banana", "wolf", "sheep", "eat"):
        require(ss.entry_view(word) is not None,
                "entry missing after restart: " + word)
    banana = ss.entry_view("banana")
    require("definition" in banana,
            "banana definition not stored")
    require(banana.get("variables") == ["noun"],
            "banana variables differ")
    binding = ss.binding_for("wolf", "eat")
    require(binding is not None
            and binding["status"] == "candidate"
            and binding["attestations"] == 1,
            "wolf binding differs after restart")
    rec = ss.compose_what_is("banana")
    require(rec["verdict"] == "HOLD"
            and "kind: not stored" in rec["gaps"]
            and rec["definition_on_record"] is not None,
            "compose_what_is(banana) differs after restart")
    sentence = "The wolf eats the sheep."
    ss.encounter(sentence, noun_finder.find(sentence),
                 verb_finder.find(sentence))
    print("RESTART_RESULT " + json.dumps(
        {"hash": wi.store_hash(ss.store), "seq": ss.store["seq"]},
        sort_keys=True))
    return 0


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_write_in_storage.py
# Uses a temporary directory as the storage root and crosses
# a real process boundary for the restart proof.
# ---------------------------------------------------------------------

def run_checks():
    # SUITE_NOTES (P1 re-base of the pre-P1 battery, 2026-10-25)
    # CARRIED unchanged in intent and assertions:
    #   open_creates_no_file
    #   phase1_binding_candidate
    #   restart_subprocess_verified_and_extended (E8)
    #   phase3_binding_verified_attestations_2
    #   phase3_no_event_reuse_across_restart
    #   adoption_reads_leave_bytes_untouched (E9)
    # PACK F+B (2026-10-26) re-scopes, with citations:
    #   phase3_binding_verified_attestations_2 ->
    #     phase3_binding_candidate_attestations_2 (D13;
    #     claims-05 F3b: same-sentence replay does not
    #     verify, even across a restart).
    #   adoption_reads_leave_bytes_untouched ->
    #     adoption_pure_reads_leave_bytes_untouched
    #     (claims-13 H1: a compose naming gaps records them
    #     at this surface now; the untouched-bytes property
    #     is asserted for pure reads + a gapless compose).
    #   corrupt_file_raises_and_is_untouched (E6)
    # RE-BASED for the envelope (same defect caught, new form):
    #   enrich_writes_through_canonical ->
    #     enrich_writes_through_envelope: on-disk text is now
    #     the envelope; payload equality replaces canonical-text
    #     equality; directory listing is ["store.json"].
    #   encounter_writes_through: same re-base as above.
    #   noncanonical_file_raises_and_is_untouched: the
    #     pretty-printed file is now a pretty-printed ENVELOPE.
    #   seal_written_and_verified_on_reopen ->
    #     envelope_written_and_verified_on_reopen: the seal is
    #     the digest field inside the single envelope file and
    #     seal_state is "enveloped" (E1/E2).
    #   sealed_value_flip_refused_files_untouched ->
    #     enveloped_value_flip_refused_file_untouched: there is
    #     no sidecar anymore; the one envelope file is untouched.
    #   sealed_seq_flip_refused -> enveloped_seq_flip_refused.
    #   tampered_seal_refused -> enveloped_digest_flip_refused:
    #     the tampered seal is now a flipped digest hex char.
    #   legacy_adopts_then_reseals -> SPLIT into
    #     legacy_unsealed_adopts_then_envelopes (E4) and
    #     legacy_sidecar_sealed_adopts_then_envelopes (E5);
    #     "reseals" is now "becomes an envelope on save" and the
    #     sidecar-sealed case also asserts sidecar removal.
    # DROPPED in original form, with reason:
    #   - Any assertion naming a separate ".seal" file as part
    #     of a CURRENT save (listings containing
    #     "store.json.seal", seal-file content equality): the
    #     sidecar is retired by P1, so those forms no longer
    #     exist to assert. Their content (digest correctness,
    #     mismatch refusal) is carried by the envelope checks
    #     above. No pre-P1 assertion is dropped without a
    #     replacement above.
    # NEW in P1:
    #   invalid_utf8_refused_and_untouched (E2)
    #   enveloped_byte_flip_battery_all_refused (E3: every byte
    #     position of a small enveloped store, masks 0x01/0xFF)
    #   enveloped_sampled_flip_battery_all_refused (E3: 67
    #     sampled positions x 2 masks on the phase-size store)
    #   legacy_sidecar_mismatch_refused (E6, explicit form)
    #   crash_replace_failure_keeps_old_envelope (E7)
    #   crash_completed_replace_loads_new_envelope (E7)
    #   stray_temp_file_beside_envelope_ignored (E7)
    # ATOMIC-ENTRY PACK (2026-10-27, spec 6.3) re-scopes,
    # with citation:
    #   every exact directory-listing assertion gains
    #     "store.json.lock": save() now holds an advisory
    #     flock on the "<path>.lock" sidecar (the ST-03 /
    #     ST-04 fix), and the sidecar persists beside the
    #     envelope. It carries no content and is never
    #     loaded. No other assertion changes.
    import hashlib
    import subprocess
    import sys

    import pmca_sentence_analyzer as _analyzer
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    _analyzer.configure_dictionary(db)

    import pmca_noun_finder as noun_finder
    import pmca_verb_finder as verb_finder
    import pmca_write_in as wi

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    def file_bytes(p):
        with open(p, "rb") as f:
            return f.read()

    def file_hash(p):
        return hashlib.sha256(file_bytes(p)).hexdigest()

    def envelope_payload(p):
        return json.loads(file_bytes(p).decode("utf-8"))["payload"]

    def envelope_ok(p, store):
        """The file at p is exactly the canonical envelope of
        store: canonical envelope text, digest over the
        canonical payload bytes, payload equal to store."""
        text = file_bytes(p).decode("utf-8")
        try:
            env = json.loads(text)
        except ValueError:
            return False
        return (_is_envelope(env)
                and text == _canonical_text(env)
                and env["payload"] == store
                and env["sha256"] == hashlib.sha256(
                    _canonical_bytes(env["payload"])).hexdigest())

    def raises_storage_error(p):
        try:
            StoredStore(p)
        except StorageError:
            return True
        return False

    sentence = "The wolf eats the sheep."

    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "store.json")

        # -- Phase 1: open (adoption writes nothing), mutate,
        # write-through after every mutation.
        ss = StoredStore(path)
        check("open_creates_no_file", not os.path.exists(path))
        ss.populate_word("banana")
        rep = ss.enrich_from_dictionary("banana")
        check("enrich_writes_through_envelope",
              rep["outcome"] == "enriched"
              and os.path.exists(path)
              and envelope_ok(path, ss.store)
              and ss.seal_state == "enveloped"
              and sorted(os.listdir(root))
              == ["store.json", "store.json.lock"])
        hash_before_encounter = file_hash(path)
        ss.encounter(sentence, noun_finder.find(sentence),
                     verb_finder.find(sentence))
        binding1 = ss.binding_for("wolf", "eat")
        check("encounter_writes_through",
              file_hash(path) != hash_before_encounter
              and envelope_ok(path, ss.store)
              and sorted(os.listdir(root))
              == ["store.json", "store.json.lock"])
        check("phase1_binding_candidate",
              binding1 is not None
              and binding1["status"] == "candidate"
              and binding1["attestations"] == 1)
        h1 = wi.store_hash(ss.store)
        s1 = ss.store["seq"]
        print("PHASE1 hash=%s seq=%d" % (h1, s1))

        # -- Phase 2: a new process reopens the store, checks
        # it, and extends it.
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__),
             "--verify-restart", path, h1, str(s1)],
            capture_output=True, text=True)
        result_lines = [ln for ln in proc.stdout.splitlines()
                        if ln.startswith("RESTART_RESULT ")]
        h2 = s2 = None
        if result_lines:
            payload = json.loads(
                result_lines[-1][len("RESTART_RESULT "):])
            h2, s2 = payload["hash"], payload["seq"]
        check("restart_subprocess_verified_and_extended",
              proc.returncode == 0 and h2 is not None
              and h2 != h1 and s2 is not None and s2 > s1,
              (proc.stdout.strip().splitlines() or [""])[-1]
              if proc.returncode == 0 else proc.stderr.strip())
        print("PHASE2 hash=%s seq=%s" % (h2, s2))

        # -- Phase 3: reopen here; the second encounter landed
        # across the process boundary with fresh event numbers.
        ss3 = StoredStore(path)
        binding3 = ss3.binding_for("wolf", "eat")
        wolf3 = ss3.entry_view("wolf")
        events3 = wolf3["subcoordinates"]["originates::eat"][
            "attested_events"]
        # D13 (2026-10-26; claims-05 F3b): the two encounters
        # are the same sentence under the same provenance, on
        # both sides of the restart — attestations 2, status
        # candidate. Replay across a process boundary is
        # still replay; it does not verify.
        check("phase3_binding_candidate_attestations_2",
              binding3 is not None
              and binding3["status"] == "candidate"
              and binding3["attestations"] == 2)
        check("phase3_no_event_reuse_across_restart",
              len(events3) == 2 and events3[0] < events3[1]
              and ss3.store["seq"] == s2
              and wi.store_hash(ss3.store) == h2)

        # -- Adoption: pure reads and a gapless compose
        # leave the bytes untouched. (Re-scoped 2026-10-26,
        # Pack F+B: a compose that names gaps now RECORDS
        # them at this surface, claims-13 H1 — so the compose
        # exercised here is the wolf/eat binding, whose
        # record names no gaps.)
        good_hash = file_hash(path)
        ss4 = StoredStore(path)
        gapless = ss4.compose_binding("wolf", "eat")
        ss4.binding_for("wolf", "eat")
        ss4.entry_view("banana")
        ss4.entry_state("banana")
        check("adoption_pure_reads_leave_bytes_untouched",
              gapless["gaps"] == []
              and file_hash(path) == good_hash)

        # -- Corruption: garbage bytes are refused and the
        # file is left exactly as it was.
        corrupt_path = os.path.join(root, "corrupt.json")
        with open(corrupt_path, "wb") as f:
            f.write(b"this is not json {{{")
        corrupt_before = file_bytes(corrupt_path)
        check("corrupt_file_raises_and_is_untouched",
              raises_storage_error(corrupt_path)
              and file_bytes(corrupt_path) == corrupt_before)

        # -- Invalid UTF-8: refused (C-09 discipline) and the
        # file is left exactly as it was.
        utf8_path = os.path.join(root, "badutf8.json")
        with open(utf8_path, "wb") as f:
            f.write(b"\xff\xfe not utf-8 {{{")
        utf8_before = file_bytes(utf8_path)
        check("invalid_utf8_refused_and_untouched",
              raises_storage_error(utf8_path)
              and file_bytes(utf8_path) == utf8_before)

        # -- Non-canonical: the same envelope, pretty-printed,
        # is refused by the canonical-form check, file
        # untouched.
        pretty_path = os.path.join(root, "pretty.json")
        pretty_text = json.dumps(
            json.loads(file_bytes(path).decode("utf-8")), indent=2)
        with open(pretty_path, "w", encoding="utf-8") as f:
            f.write(pretty_text)
        check("noncanonical_file_raises_and_is_untouched",
              raises_storage_error(pretty_path)
              and file_bytes(pretty_path).decode("utf-8")
              == pretty_text)

        # -- Envelope: written on save, verified on reopen.
        env = json.loads(file_bytes(path).decode("utf-8"))
        ss5 = StoredStore(path)
        check("envelope_written_and_verified_on_reopen",
              _is_envelope(env)
              and env["sha256"] == hashlib.sha256(
                  _canonical_bytes(env["payload"])).hexdigest()
              and ss5.seal_state == "enveloped"
              and ss5.store == env["payload"]
              and not os.path.exists(seal_path_for(path)))

        # -- Form-preserving single-bit flips on the phase
        # store's envelope now refuse, file untouched: inside
        # a payload value, in the payload seq digits, and in
        # the digest hex.
        good_envelope_bytes = file_bytes(path)

        def envelope_copy(name):
            cpath = os.path.join(root, name)
            with open(cpath, "wb") as f:
                f.write(good_envelope_bytes)
            return cpath

        flip_path = envelope_copy("flip_value.json")
        raw = bytearray(file_bytes(flip_path))
        at = bytes(raw).find(b"banana")
        raw[at] ^= 0x01
        with open(flip_path, "wb") as f:
            f.write(bytes(raw))
        flip_after = file_bytes(flip_path)
        check("enveloped_value_flip_refused_file_untouched",
              at >= 0
              and raises_storage_error(flip_path)
              and file_bytes(flip_path) == flip_after)

        seq_path = envelope_copy("flip_seq.json")
        raw = bytearray(file_bytes(seq_path))
        digit_at = bytes(raw).rfind(b'"seq":') + len(b'"seq":')
        raw[digit_at] ^= 0x01
        with open(seq_path, "wb") as f:
            f.write(bytes(raw))
        check("enveloped_seq_flip_refused",
              raises_storage_error(seq_path))

        digest_path = envelope_copy("flip_digest.json")
        raw = bytearray(file_bytes(digest_path))
        hex_at = (bytes(raw).find(b'"sha256":"')
                  + len(b'"sha256":"'))
        raw[hex_at] = (ord("0") if raw[hex_at] != ord("0")
                       else ord("1"))
        with open(digest_path, "wb") as f:
            f.write(bytes(raw))
        check("enveloped_digest_flip_refused",
              raises_storage_error(digest_path))

        # -- Byte-flip battery on a SMALL enveloped store:
        # flip EVERY byte position, with masks 0x01 and 0xFF;
        # every flip must refuse with StorageError and leave
        # the flipped file untouched.
        small_path = os.path.join(root, "small.json")
        save_store(wi.new_store(), small_path)
        small_bytes = file_bytes(small_path)
        flips_tried = 0
        flips_refused = 0
        for pos in range(len(small_bytes)):
            for mask in (0x01, 0xFF):
                flipped = bytearray(small_bytes)
                flipped[pos] ^= mask
                with open(small_path, "wb") as f:
                    f.write(bytes(flipped))
                flips_tried += 1
                if (raises_storage_error(small_path)
                        and file_bytes(small_path) == bytes(flipped)):
                    flips_refused += 1
        with open(small_path, "wb") as f:
            f.write(small_bytes)
        check("enveloped_byte_flip_battery_all_refused",
              flips_tried == 2 * len(small_bytes)
              and flips_refused == flips_tried,
              "%d/%d flips refused" % (flips_refused, flips_tried))

        # -- Sampled byte-flip battery on the volume-size
        # (phase) enveloped store (E3): 67 evenly spaced byte
        # positions, masks 0x01 and 0xFF; every flip refuses.
        sampled_path = os.path.join(root, "sampled.json")
        positions = sorted(set(
            i * (len(good_envelope_bytes) - 1) // 66
            for i in range(67)))
        sampled_tried = 0
        sampled_refused = 0
        for pos in positions:
            for mask in (0x01, 0xFF):
                flipped = bytearray(good_envelope_bytes)
                flipped[pos] ^= mask
                with open(sampled_path, "wb") as f:
                    f.write(bytes(flipped))
                sampled_tried += 1
                if raises_storage_error(sampled_path):
                    sampled_refused += 1
        check("enveloped_sampled_flip_battery_all_refused",
              sampled_tried == 2 * len(positions)
              and sampled_refused == sampled_tried,
              "%d/%d flips refused"
              % (sampled_refused, sampled_tried))

        # -- Legacy fixtures: the phase store's payload in its
        # pre-P1 canonical form, built in their own directories
        # so listings can be asserted exactly.
        phase_payload = envelope_payload(path)
        legacy_text = wi.canonical(phase_payload)

        # E4: legacy unsealed adoption -> envelope on save.
        legacy_dir = os.path.join(root, "legacy_unsealed")
        os.mkdir(legacy_dir)
        legacy_path = os.path.join(legacy_dir, "store.json")
        with open(legacy_path, "w", encoding="utf-8") as f:
            f.write(legacy_text)
        ss6 = StoredStore(legacy_path)
        adopted_state = ss6.seal_state
        adopted_store = ss6.store
        ss6.save()
        ss7 = StoredStore(legacy_path)
        check("legacy_unsealed_adopts_then_envelopes",
              adopted_state == "adopted_unsealed"
              and adopted_store == phase_payload
              and sorted(os.listdir(legacy_dir))
              == ["store.json", "store.json.lock"]
              and envelope_ok(legacy_path, phase_payload)
              and ss7.seal_state == "enveloped"
              and ss7.store == phase_payload)

        # E5: legacy sidecar-sealed adoption -> envelope on
        # save, with the sidecar removed.
        sealed_dir = os.path.join(root, "legacy_sealed")
        os.mkdir(sealed_dir)
        sealed_path = os.path.join(sealed_dir, "store.json")
        with open(sealed_path, "w", encoding="utf-8") as f:
            f.write(legacy_text)
        with open(seal_path_for(sealed_path), "w",
                  encoding="utf-8") as f:
            f.write(_digest_text(legacy_text) + "\n")
        ss8 = StoredStore(sealed_path)
        verified_state = ss8.seal_state
        verified_store = ss8.store
        ss8.save()
        ss9 = StoredStore(sealed_path)
        check("legacy_sidecar_sealed_adopts_then_envelopes",
              verified_state == "verified"
              and verified_store == phase_payload
              and sorted(os.listdir(sealed_dir))
              == ["store.json", "store.json.lock"]
              and not os.path.exists(seal_path_for(sealed_path))
              and envelope_ok(sealed_path, phase_payload)
              and ss9.seal_state == "enveloped"
              and ss9.store == phase_payload)

        # E6: legacy sidecar mismatch is refused, exactly as
        # pre-P1, and both files are left untouched.
        mismatch_dir = os.path.join(root, "legacy_mismatch")
        os.mkdir(mismatch_dir)
        mismatch_path = os.path.join(mismatch_dir, "store.json")
        with open(mismatch_path, "w", encoding="utf-8") as f:
            f.write(legacy_text)
        with open(seal_path_for(mismatch_path), "w",
                  encoding="utf-8") as f:
            f.write("0" * 64 + "\n")
        mismatch_store_before = file_bytes(mismatch_path)
        mismatch_seal_before = file_bytes(
            seal_path_for(mismatch_path))
        try:
            StoredStore(mismatch_path)
            mismatch_msg_ok = False
        except StorageError as exc:
            mismatch_msg_ok = "seal mismatch" in str(exc)
        check("legacy_sidecar_mismatch_refused",
              mismatch_msg_ok
              and file_bytes(mismatch_path)
              == mismatch_store_before
              and file_bytes(seal_path_for(mismatch_path))
              == mismatch_seal_before)

        # E7: crash-window simulation by fault injection.
        crash_dir = os.path.join(root, "crash")
        os.mkdir(crash_dir)
        crash_path = os.path.join(crash_dir, "store.json")
        cs = StoredStore(crash_path)
        cs.populate_word("fig")
        old_envelope_bytes = file_bytes(crash_path)
        old_payload = load_store(crash_path)

        real_replace = os.replace

        def boom(src, dst):
            raise OSError("injected replace failure")

        os.replace = boom
        try:
            try:
                cs.populate_word("plum")
                replace_raised = False
            except StorageError:
                replace_raised = True
        finally:
            os.replace = real_replace
        check("crash_replace_failure_keeps_old_envelope",
              replace_raised
              and file_bytes(crash_path) == old_envelope_bytes
              and load_store(crash_path) == old_payload
              and sorted(os.listdir(crash_dir))
              == ["store.json", "store.json.lock"])

        cs2 = StoredStore(crash_path)
        cs2.populate_word("plum")
        check("crash_completed_replace_loads_new_envelope",
              file_bytes(crash_path) != old_envelope_bytes
              and envelope_ok(crash_path, cs2.store)
              and StoredStore(crash_path).entry_view("plum")
              is not None
              and sorted(os.listdir(crash_dir))
              == ["store.json", "store.json.lock"])

        stray_path = os.path.join(crash_dir,
                                  ".pmca-store-stray.tmp")
        with open(stray_path, "w", encoding="utf-8") as f:
            f.write("inert leftover {{{")
        cs3 = StoredStore(crash_path)
        check("stray_temp_file_beside_envelope_ignored",
              cs3.seal_state == "enveloped"
              and cs3.store == cs2.store)
        os.unlink(stray_path)

    passed = sum(1 for r in results if r)
    print("Passed %d of %d checks" % (passed, len(results)))
    print("ALL CHECKS PASS" if passed == len(results)
          else "SOME CHECKS FAILED")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "--verify-restart":
        try:
            sys.exit(_verify_restart(sys.argv[2], sys.argv[3],
                                     int(sys.argv[4])))
        except Exception as exc:
            print("RESTART VERIFICATION FAILED: " + str(exc))
            sys.exit(1)
    sys.exit(run_checks())
