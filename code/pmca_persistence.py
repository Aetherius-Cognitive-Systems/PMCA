"""pmca_persistence.py - PMCA persistence module (Stage B core).

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code implemented by Alder (Muse) per Jonathan's architecture.

Persistence design, 2026-10-20 (ruled in full; Decision 5 delegated
to Alder and ruled: accept the standard-library archive construction
for version 1, local only, with conditions). This module is the
persistence module that design specifies: it persists the system's
primary state to the local storage root named by one config file,
and restores it at boot under seal-before-trust.

WHAT PERSISTS (primary artifacts), and what does not:

  - The current seed dict, complete: entries, edges, metadata
    counters, pending_event, pending_proposals. The seed dict is the
    source of truth; the seed GraphSpace, teaching's POTENTIAL held
    space, and teaching's LIVE held space are materialized VIEWS of
    it and are never persisted -- they re-materialize from the
    restored dict (entry and edge objects are shared between the
    dict's flat views and the spaces, so serializing them separately
    would manufacture divergence).
  - Teaching's event counter and in-memory audit log, riding in the
    seed save envelope (without the counter, a restarted process
    re-mints event ids that already exist in the proposals archive).
  - The analyzer's own GraphSpace: the ONLY true primary space. Its
    registered logic entries change verdicts; losing them silently
    flips future verdicts back to HOLD. Analysis records inside it
    are scratch (Decision 4) and are excluded at save, never
    persisted.

Already persisting correctly and untouched by this module: the four
pool files and the proposals archive.

SEALS (Decision 3: if it can affect a verdict, it sits inside
exactly one seal):

  - pmca_seed's own seal is unchanged (SHA-256 over the materialized
    space's canonical bytes).
  - The persisted seed dict carries this module's seal over
    canonical_seed_json(seed) -- which INCLUDES the pending keys and
    metadata the space seal never covered -- plus the canonical JSON
    of the teaching block. approve() treats stored post_verdicts as
    its tamper reference; this seal is what authenticates them in
    storage.
  - The analyzer space save carries a checksum: SHA-256 over the
    canonical bytes of the space re-materialized from the saved
    structure. After a load materializes the space through the
    guarded graphspace calls, its canonical bytes must hash to that
    checksum or the space is discarded and the load fails.

SEAL VERSIONS (Pack H, 2026-10-26: keyed seals). The module seals
above exist in three versions, recorded per file as "seal_v":

  - seal_v absent (pre-Pack-H files): the original unkeyed SHA-256
    digests, with the envelope's generation OUTSIDE the sealed
    material. These files still load (adoption, below) and are
    marked adopted_unsealed.
  - seal_v 1: unkeyed SHA-256 over the same material WITH the
    envelope's generation inside it. Written when a save runs
    without a keyring.
  - seal_v 2: HMAC-SHA256 under a domain-separated subkey derived
    from the caller's keyring (the archive's keyring machinery;
    domains "PMCA/state/seed" and "PMCA/state/space"), generation
    inside the sealed material, the key id recorded in the
    envelope. Written when a save runs with a keyring. Loading a
    seal_v 2 file REQUIRES the keyring: without it (or without the
    named key) the load raises ArchiveLocked, never a silent
    unverified load.

CORRECTION OF THE RECORD: before Pack H these state seals were
unkeyed digests, and any wording that called them tamper-proofing
was wrong. Anyone who could write a state file could recompute its
digest over forged contents and the load accepted the forgery
(claims audit 09, executed end-to-end forgeries against both state
files). An unkeyed digest is corruption detection, not tamper
evidence. A keyed (seal_v 2) seal is tamper evidence against anyone
who does not hold the key. ADOPTION (the P1 envelope precedent): a
pre-Pack-H file loads under its original digest, is marked
adopted_unsealed in the load's envelope and in boot()'s report, and
the first save with a keyring rewrites it keyed. The adoption mark
is surfaced, never silent: a legacy file appearing in a root that
has since been saved keyed is operator-visible in the boot report.

VERIFY GATE AND PENDING SEEDS (a resolved conflict, recorded for
review): teach() clears a pending seed's space seal by design (the
pending seed is unsealed until approve() seals it), so a legitimate
pending seed never passes pmca_seed.verify(). The module seal above
exists precisely to authenticate what the space seal does not speak
about. load_seed_state therefore applies the pmca_seed.verify() gate
to NON-pending seeds only; for a pending seed the module seal is the
authority, and the decoded seed's pending-ness must match the
envelope's sealed-adjacent "pending" flag exactly.

FORMAT: one JSON envelope per artifact, keys sorted, compact
separators, so identical state saved at the same generation produces
identical file bytes. State is carried in a typed encoding (tuples
tagged distinctly from lists -- canonical bytes are a checksum, not
a load format; a plain JSON round trip returns every tuple as a list
and the seal cannot detect the type loss). Writes are atomic: a
per-writer unique temp file in the same directory, flush, fsync,
rename, then a directory fsync so the rename itself is durable; the
previous file is first rotated to <name>.prev, kept for MANUAL
restore only -- there is no automatic fallback to an earlier
generation.

SINGLE WRITER: boot acquires <root>/.pmca-writer.lock with
O_CREAT|O_EXCL. A second writer fails loud. A stale lock left by a
crash is an operator decision (delete it by hand); it is never
auto-broken, because a lock that breaks itself cannot distinguish a
crashed writer from a live one.

ARCHIVE (Stage C): replaced meanings persist in an append-only
encrypted archive, <root>/archive.jsonl (Design Decision 5, ruled
by Alder: accept the standard-library construction, local only).
HMAC-SHA256 counter-mode keystream under a domain-separated
encryption subkey; encrypt-then-MAC with an independent subkey,
verified before any decryption. Nonces combine the append sequence
with 16 bytes of OS randomness. Keys live in a keyring OUTSIDE the
storage root -- the key never travels with the data. An absent key
locks the archive; a failed tag holds it, totally, inside its own
domain; neither ever aborts boot. Since Pack H the archive also
carries a head commitment (<root>/archive.head.json): a keyed
chain digest plus the entry count, rewritten on every append and
verified on every read, so deleting tail entries at a clean line
boundary is detected (ArchiveHeld) instead of silently verifying
(claims audit 09, section 5d). Archives written before Pack H have
no head file: they verify in legacy form and adopt the commitment
on their next append. The standard library has no
vetted symmetric cipher: this is hand-assembled cryptography from
sound primitives in a standard pattern, pinned in the suite by
known-answer vectors and a nonce audit, and versioned so a vetted
AEAD can replace it if ciphertext ever leaves this machine.

Standard library only. No floats anywhere in code or format: a float
found in state is corruption and raises, loudly. Deterministic:
same state + same generation = same file bytes.
"""

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time

import pmca_graphspace

FORMAT = 1
SEED_FILENAME = "seed_state.json"
SPACE_FILENAME = "analyzer_space.json"
ARCHIVE_FILENAME = "archive.jsonl"
ARCHIVE_HEAD_FILENAME = "archive.head.json"
ARCHIVE_FORMAT = 1
LOCK_FILENAME = ".pmca-writer.lock"
TUPLE_TAG = "__pmca_tuple__"
DICT_TAG = "__pmca_dict__"


# ---------------------------------------------------------------------
# Exceptions. All loud; no partial returns anywhere.
# ---------------------------------------------------------------------

class PersistenceError(Exception):
    """Base class for every persistence failure."""


class ConfigError(PersistenceError):
    """The config file is missing, malformed, or names an illegal root."""


class StateNotFound(PersistenceError):
    """No saved state file exists for the requested artifact."""


class SealMismatch(PersistenceError):
    """Stored bytes do not match their seal or checksum."""


class ClassMismatch(PersistenceError):
    """A save file's kind is not the kind the loader was asked for."""


class FormatTooNew(PersistenceError):
    """A save file's format is newer than this code supports."""


class WriterLockHeld(PersistenceError):
    """Another writer holds the storage root's writer lock."""


class ArchiveLocked(PersistenceError):
    """The archive's keyring, or a needed key in it, is absent."""


class ArchiveHeld(PersistenceError):
    """An archive entry failed verification; the archive refuses."""


# ---------------------------------------------------------------------
# Config. Exactly two allowed keys; relative roots resolve against
# the config file's directory, never the process cwd. A missing
# config file is a loud failure: a guessed root can split state
# across locations (Alder's reconciliation; workstream 4 governs).
# ---------------------------------------------------------------------

def load_config(path):
    """Read and validate the config file at path.

    Returns {"format": 1, "storage_root": <absolute path>}.
    Raises ConfigError on: missing file, malformed JSON, a top level
    that is not an object, unknown keys, a "format" that is not the
    integer 1, a missing or non-string "storage_root", or a root
    containing "://" (the config can never name a remote location).
    """
    if not os.path.isfile(path):
        raise ConfigError("config file not found: " + str(path))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ConfigError("config file unreadable: " + str(path)) from exc
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise ConfigError("config file is not valid JSON: " + str(path)) from exc
    if not isinstance(obj, dict):
        raise ConfigError("config top level is not an object: " + str(path))
    unknown = sorted(set(obj) - {"format", "storage_root"})
    if unknown:
        raise ConfigError("unknown config keys: " + ", ".join(unknown))
    fmt = obj.get("format", 1)
    if type(fmt) is not int or fmt != FORMAT:
        raise ConfigError("config format must be the integer 1, got: " + repr(fmt))
    if "storage_root" not in obj:
        raise ConfigError("config storage_root is required")
    raw_root = obj["storage_root"]
    if not isinstance(raw_root, str) or not raw_root:
        raise ConfigError("config storage_root must be a non-empty string")
    if "://" in raw_root:
        raise ConfigError("config storage_root may not name a remote "
                          " location: " + raw_root)
    expanded = os.path.expanduser(raw_root)
    if os.path.isabs(expanded):
        resolved = expanded
    else:
        base = os.path.dirname(os.path.abspath(path))
        resolved = os.path.join(base, expanded)
    return {"format": FORMAT, "storage_root": os.path.abspath(resolved)}


# ---------------------------------------------------------------------
# Typed codec. Recursive over dict/list/tuple/str/int/bool/None.
# Tuples are tagged so they never come back as lists; a literal dict
# containing a reserved key is encoded in pair form so it round-trips
# as a dict. Dict keys must be strings: the encoder raises on any
# non-string key (this closes the proven _canon str(key) latent
# hazard at the save boundary). Floats raise: the system bans them,
# so a float in state is corruption.
# ---------------------------------------------------------------------

def encode_typed(obj):
    """Encode a state value into JSON-safe typed form."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        raise PersistenceError("float in state (corruption): " + repr(obj))
    if isinstance(obj, tuple):
        return {TUPLE_TAG: [encode_typed(item) for item in obj]}
    if isinstance(obj, list):
        return [encode_typed(item) for item in obj]
    if isinstance(obj, dict):
        for key in obj:
            if not isinstance(key, str):
                raise PersistenceError(
                    "non-string dict key in state: " + repr(key))
        if TUPLE_TAG in obj or DICT_TAG in obj:
            return {DICT_TAG: [[key, encode_typed(value)]
                               for key, value in obj.items()]}
        return {key: encode_typed(value) for key, value in obj.items()}
    raise PersistenceError("unencodable type in state: " + type(obj).__name__)


def decode_typed(obj):
    """Reverse encode_typed. Raises PersistenceError on bad tags."""
    if isinstance(obj, dict):
        if len(obj) == 1 and TUPLE_TAG in obj:
            items = obj[TUPLE_TAG]
            if not isinstance(items, list):
                raise PersistenceError("tuple tag payload is not a list")
            return tuple(decode_typed(item) for item in items)
        if len(obj) == 1 and DICT_TAG in obj:
            pairs = obj[DICT_TAG]
            if not isinstance(pairs, list):
                raise PersistenceError("dict tag payload is not a list")
            out = {}
            for pair in pairs:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise PersistenceError("dict tag pair is malformed")
                out[pair[0]] = decode_typed(pair[1])
            return out
        return {key: decode_typed(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [decode_typed(item) for item in obj]
    if isinstance(obj, float):
        raise PersistenceError("float in stored state (corruption): "
                               + repr(obj))
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    raise PersistenceError("undecodable type in stored state: "
                           + type(obj).__name__)


# ---------------------------------------------------------------------
# Atomic write with previous-generation rotation, and the writer lock.
# ---------------------------------------------------------------------

def _fsync_dir(directory):
    """Fsync a directory so a rename inside it is durable. (A file
    fsync alone does not persist the directory entry; claims audit
    09, Claim 4.)"""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path, data):
    """Write data (bytes) to path atomically.

    A per-writer unique temp file in the same directory (mkstemp;
    two concurrent writers never share a temp file — claims audit
    09, Claim 4), flush, fsync, then the existing target (if any) is
    rotated to <path>.prev, then the temp renames over the target,
    and the directory is fsynced so the rename itself survives a
    power loss. The .prev file is kept for manual restore only;
    nothing in this module ever falls back to it automatically.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".pmca-tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(path):
            os.replace(path, path + ".prev")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    _fsync_dir(directory)
    return path


def acquire_lock(root):
    """Acquire the single-writer lock for a storage root.

    Creates <root>/.pmca-writer.lock with O_CREAT|O_EXCL and returns
    the lock path. Raises WriterLockHeld if the lock already exists.
    A stale lock after a crash is an operator decision (delete it by
    hand); it is never auto-broken.
    """
    lock_path = os.path.join(root, LOCK_FILENAME)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise WriterLockHeld("writer lock already held: " + lock_path) from exc
    except FileNotFoundError as exc:
        raise PersistenceError("storage root does not exist: " + root) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("pmca persistence writer lock\n")
    return lock_path


def release_lock(lock_path):
    """Release a writer lock acquired by acquire_lock."""
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------
# Envelope helpers.
# ---------------------------------------------------------------------

def _canonical_json(obj):
    """Canonical JSON text: sorted keys, fixed separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _envelope_bytes(envelope):
    return _canonical_json(envelope).encode("utf-8")


def _read_envelope(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise PersistenceError("save file unreadable: " + path) from exc
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PersistenceError("save file is not valid JSON: " + path) from exc
    if not isinstance(obj, dict):
        raise PersistenceError("save file envelope is not an object: " + path)
    return obj


def _check_kind_format(envelope, want_kind, path):
    kind = envelope.get("kind")
    if kind != want_kind:
        raise ClassMismatch("save file kind is %r, loader wanted %r: %s"
                            % (kind, want_kind, path))
    fmt = envelope.get("format")
    if type(fmt) is not int:
        raise PersistenceError("save file format is not an integer: " + path)
    if fmt > FORMAT:
        raise FormatTooNew("save file format %d is newer than this code "
                           "supports (%d): %s" % (fmt, FORMAT, path))
    if fmt < 1:
        raise PersistenceError("save file format is invalid: " + path)
    generation = envelope.get("generation")
    if type(generation) is not int:
        raise PersistenceError("save file generation is not an integer: "
                               + path)


def _verify_readback(load_once):
    """Run a save's read-back load (Pack H save/load symmetry).

    This module is single-writer by contract (the boot writer
    lock), and under that contract the first attempt always
    succeeds. The retries exist only to absorb the rotation window
    when the convention is violated by a concurrent writer: between
    our rename and our read, another writer can rotate our file to
    .prev before its own rename lands, so the path is momentarily
    absent (StateNotFound, or an OSError inside the envelope read).
    Only those momentary-absence failures are retried, a few times
    over ~a fifth of a second; a seal, format, or class failure is
    real and raises at once."""
    last_exc = None
    for attempt in range(5):
        try:
            load_once()
            return
        except StateNotFound as exc:
            last_exc = exc
        except PersistenceError as exc:
            if not isinstance(exc.__cause__, OSError):
                raise
            last_exc = exc
        time.sleep(0.02 * (attempt + 1))
    raise last_exc


# ---------------------------------------------------------------------
# Seed dict save/load.
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# State seals (Pack H): versions, subkeys, key resolution.
#
# seal_v absent -> the legacy pre-Pack-H digest (unkeyed, generation
# outside the material); adopted on load, marked adopted_unsealed.
# seal_v 1 -> unkeyed digest, generation inside the material.
# seal_v 2 -> HMAC-SHA256 under a domain-separated subkey from the
# caller's keyring, generation inside the material, key id recorded
# in the envelope. The subkey derivation mirrors the archive's:
# HMAC-SHA256 over the domain label, the key id as u32be, and the
# state FORMAT as u16be; the master key is never used directly.
# ---------------------------------------------------------------------

_STATE_SEAL_DOMAINS = {"seed_dict": b"PMCA/state/seed",
                       "analyzer_space": b"PMCA/state/space",
                       "atomic_entry": b"PMCA/entry/process",
                       "incomplete_entry": b"PMCA/entry/incomplete"}


def _state_subkey(master_key, key_id, kind):
    """The domain-separated state-seal subkey for one key id and
    artifact kind ("seed_dict" or "analyzer_space")."""
    suffix = (_key_id_number(key_id).to_bytes(4, "big")
              + FORMAT.to_bytes(2, "big"))
    return hmac.new(master_key, _STATE_SEAL_DOMAINS[kind] + suffix,
                    hashlib.sha256).digest()


def _resolve_seal_key(keyring_path, key_id=None):
    """Load a keyring and return (key_id, master key bytes) for
    state sealing: the named key, or the ring's current key.
    ArchiveLocked if the keyring or the named key is absent (the
    module's existing vocabulary for an absent key)."""
    ring = load_keyring(keyring_path)
    chosen = key_id if key_id is not None else ring["current"]
    if chosen not in ring["keys"]:
        raise ArchiveLocked("state seal key absent from keyring: %r"
                            % (chosen,))
    return chosen, bytes.fromhex(ring["keys"][chosen])


def _seed_seal_material(seed, teaching_block):
    """The sealed material of a seed save: canonical_seed_json(seed)
    (pending keys and metadata included) plus the canonical JSON of
    the teaching block."""
    import pmca_seed
    return (pmca_seed.canonical_seed_json(seed) + "\n"
            + _canonical_json(teaching_block))


def _seed_seal(seed, teaching_block):
    """The LEGACY (pre-Pack-H, seal_v absent) seed seal: unkeyed
    SHA-256 over the sealed material, the envelope generation
    outside it. Kept so pre-Pack-H files verify for adoption; new
    saves never write this form."""
    material = _seed_seal_material(seed, teaching_block)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _seed_seal_v1(seed, teaching_block, generation):
    """seal_v 1: unkeyed SHA-256 over the sealed material with the
    envelope generation inside it."""
    material = (_seed_seal_material(seed, teaching_block) + "\n"
                + str(generation))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _seed_seal_v2(master_key, key_id, seed, teaching_block, generation):
    """seal_v 2: HMAC-SHA256 under the seed state subkey over the
    sealed material with the envelope generation inside it."""
    material = (_seed_seal_material(seed, teaching_block) + "\n"
                + str(generation))
    subkey = _state_subkey(master_key, key_id, "seed_dict")
    return hmac.new(subkey, material.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def save_seed_state(root, seed, generation, keyring_path=None):
    """Persist the current seed dict plus teaching's counter and
    audit log to <root>/seed_state.json. Returns the file path.

    The teaching block is read from pmca_teaching's accessors at save
    time, so what is stored is the module state that belongs with
    this seed. The previous file rotates to seed_state.json.prev.

    With keyring_path, the module seal is the keyed seal_v 2 form
    (HMAC under the ring's current key); without one it is the
    unkeyed seal_v 1 form. Both place the generation inside the
    sealed material (Pack H).

    SAVE/LOAD SYMMETRY (Pack H; claims audit 09, D25): the save
    boundary verifies that what it wrote can be read back. A
    non-pending seed whose pmca_seed space seal is empty -- a fresh
    build_seed output, whose seal otherwise arrives only through
    teaching's approve path -- is REPAIRED BY SEALING (pmca_seed.seal
    applied to the passed seed) before it is written, so this save
    never persists a file load_seed_state would refuse on every
    load. After the write, the file is read back through the full
    load path; any failure raises instead of reporting success.
    """
    if type(generation) is not int:
        raise PersistenceError("generation must be an integer")
    import pmca_seed
    import pmca_teaching
    if (("pending_event" not in seed)
            and ("pending_proposals" not in seed)
            and not seed.get("metadata", {}).get("seal")):
        # Repair by sealing: an unsealed non-pending seed is exactly
        # what load_seed_state's space-seal gate refuses.
        pmca_seed.seal(seed)
    teaching_block = {
        "event_counter": pmca_teaching.current_event_counter(),
        "audit_log": pmca_teaching.current_audit_log(),
    }
    envelope = {
        "format": FORMAT,
        "kind": "seed_dict",
        "generation": generation,
        "pending": ("pending_event" in seed) or ("pending_proposals" in seed),
        "payload": encode_typed(seed),
        "teaching": encode_typed(teaching_block),
    }
    if keyring_path is not None:
        key_id, master_key = _resolve_seal_key(keyring_path)
        envelope["seal_v"] = 2
        envelope["key_id"] = key_id
        envelope["seal"] = _seed_seal_v2(master_key, key_id, seed,
                                         teaching_block, generation)
    else:
        envelope["seal_v"] = 1
        envelope["seal"] = _seed_seal_v1(seed, teaching_block, generation)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, SEED_FILENAME)
    _atomic_write(path, _envelope_bytes(envelope))
    # Read-back: the save is not done until the written file passes
    # the full load path (module seal, re-materialization, and the
    # non-pending space-seal gate).
    _verify_readback(
        lambda: _load_seed_state_full(root, keyring_path=keyring_path))
    return path


def _load_seed_state_full(root, keyring_path=None):
    """Load and fully verify a seed save. Returns
    (envelope, seed, teaching_block). Nothing is returned unless the
    module seal verifies, the decoded state re-materializes, and (for
    a non-pending seed) pmca_seed.verify() passes. The returned
    envelope carries "seal_state": "adopted_unsealed" (a pre-Pack-H
    file, verified under its legacy digest), "unkeyed" (seal_v 1),
    or "keyed" (seal_v 2). A seal_v 2 file REQUIRES keyring_path:
    without it, or without the envelope's named key, ArchiveLocked
    is raised — never a silent unverified load."""
    path = os.path.join(root, SEED_FILENAME)
    if not os.path.isfile(path):
        raise StateNotFound("no seed state file: " + path)
    envelope = _read_envelope(path)
    _check_kind_format(envelope, "seed_dict", path)
    if "payload" not in envelope or "teaching" not in envelope:
        raise PersistenceError("seed save is missing payload or teaching: "
                               + path)
    seed = decode_typed(envelope["payload"])
    teaching_block = decode_typed(envelope["teaching"])
    if not isinstance(seed, dict) or not all(
            key in seed for key in ("entries", "edges", "metadata")):
        raise PersistenceError("decoded seed is structurally wrong: " + path)
    if (not isinstance(teaching_block, dict)
            or type(teaching_block.get("event_counter")) is not int
            or not isinstance(teaching_block.get("audit_log"), list)):
        raise PersistenceError("decoded teaching block is structurally "
                               "wrong: " + path)
    # Seal before trust: recompute over the decoded state, compare
    # before anything is materialized or returned. The comparison
    # follows the file's seal version (Pack H): absent = the legacy
    # unkeyed digest (adopted, marked on the envelope below); 1 =
    # unkeyed with the generation inside the material; 2 = keyed
    # HMAC, which requires the keyring.
    seal_v = envelope.get("seal_v", 0)
    stored_seal = envelope.get("seal")
    if seal_v == 0:
        verified = (isinstance(stored_seal, str)
                    and hmac.compare_digest(
                        _seed_seal(seed, teaching_block), stored_seal))
        seal_state = "adopted_unsealed"
    elif seal_v == 1:
        verified = (isinstance(stored_seal, str)
                    and hmac.compare_digest(
                        _seed_seal_v1(seed, teaching_block,
                                      envelope["generation"]),
                        stored_seal))
        seal_state = "unkeyed"
    elif seal_v == 2:
        if keyring_path is None:
            raise ArchiveLocked(
                "seed save is key-sealed (seal_v 2) and no keyring "
                "was given: " + path)
        key_id, master_key = _resolve_seal_key(
            keyring_path, envelope.get("key_id"))
        verified = (isinstance(stored_seal, str)
                    and hmac.compare_digest(
                        _seed_seal_v2(master_key, key_id, seed,
                                      teaching_block,
                                      envelope["generation"]),
                        stored_seal))
        seal_state = "keyed"
    else:
        raise PersistenceError("seed save seal version is not "
                               "supported: %r" % (seal_v,))
    if not verified:
        raise SealMismatch("seed save seal does not verify: " + path)
    envelope["seal_state"] = seal_state
    pending_flag = envelope.get("pending")
    if type(pending_flag) is not bool:
        raise PersistenceError("seed save pending flag is not a boolean: "
                               + path)
    actual_pending = (("pending_event" in seed)
                      or ("pending_proposals" in seed))
    if pending_flag != actual_pending:
        raise SealMismatch("seed save pending flag disagrees with the "
                           "sealed state: " + path)
    import pmca_seed
    try:
        pmca_seed.seed_space(seed)
    except Exception as exc:
        raise PersistenceError("decoded seed does not re-materialize: "
                               + path) from exc
    if not actual_pending and not pmca_seed.verify(seed):
        # A non-pending seed must carry its own intact space seal.
        # (A pending seed is unsealed by design -- teach() clears the
        # seal -- and is authenticated by the module seal alone; see
        # the module docstring.)
        raise SealMismatch("seed space seal does not verify: " + path)
    return envelope, seed, teaching_block


def load_seed_state(root, keyring_path=None):
    """Load the persisted seed dict. Returns (seed, teaching_block).

    Missing file -> StateNotFound. Kind confusion -> ClassMismatch.
    Newer format -> FormatTooNew. Any seal failure -> SealMismatch,
    raised before anything is returned. A key-sealed (seal_v 2) file
    loaded without its keyring -> ArchiveLocked. Restoring the
    teaching block into pmca_teaching (restore_state) is the
    caller's next step; boot() does it.
    """
    _envelope, seed, teaching_block = _load_seed_state_full(
        root, keyring_path=keyring_path)
    return seed, teaching_block


# ---------------------------------------------------------------------
# Analyzer space save/load.
# ---------------------------------------------------------------------

def _is_glue_marker(entry):
    """True iff an entry is a graphspace glue marker: a dict whose
    only key is "glued", holding a 3-part address. (That shape is
    reserved by the format; a canonical entry of exactly that shape
    cannot be distinguished from a marker and is refused at save.)"""
    return (isinstance(entry, dict) and len(entry) == 1 and "glued" in entry
            and isinstance(entry["glued"], (list, tuple))
            and len(entry["glued"]) == 3)


def _space_structure(space):
    """The persisted structure of a GraphSpace: every graph in sorted
    address order, with its meta, its edges, and its entries EXCLUDING
    analysis records (Decision 4: records are scratch, never
    persisted)."""
    graphs = []
    for address in space.addresses():
        graph = space.graphs[address]
        entries = {}
        for key, entry in graph["entries"].items():
            if isinstance(entry, dict) and entry.get("kind") == "analysis_record":
                continue
            entries[key] = entry
        graphs.append({
            "address": [address[0], address[1], address[2]],
            "meta": {"system": graph["meta"]["system"],
                     "created_by": graph["meta"]["created_by"]},
            "entries": entries,
            "edges": list(graph["edges"]),
        })
    return {"system": space.system, "graphs": graphs}


def _materialize_space(structure):
    """Rebuild a GraphSpace from a persisted structure, through the
    guarded graphspace calls only: produce_graph per graph, canonical
    entries via place_entry first across all graphs, glue markers
    second (each checked against the rebuilt address book), then
    edges via place_edge in order. Any guard failure raises
    PersistenceError; the half-built space is a local and is never
    returned."""
    if not isinstance(structure, dict) or not isinstance(
            structure.get("graphs"), list):
        raise PersistenceError("space structure is malformed")
    system = structure.get("system")
    if not isinstance(system, str):
        system = "own"
    space = pmca_graphspace.GraphSpace(system)
    specs = structure["graphs"]
    addresses = []
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(
                spec.get("address"), list) or len(spec["address"]) != 3:
            raise PersistenceError("graph spec is malformed")
        address = (spec["address"][0], spec["address"][1], spec["address"][2])
        meta = spec.get("meta") or {}
        try:
            space.produce_graph(address, meta.get("system", system),
                                meta.get("created_by", "restore"))
        except Exception as exc:
            raise PersistenceError("graph production failed during "
                                   "materialization: %r" % (address,)) from exc
        addresses.append(address)
    for spec, address in zip(specs, addresses):
        entries = spec.get("entries") or {}
        if not isinstance(entries, dict):
            raise PersistenceError("graph entries are malformed")
        for key, entry in entries.items():
            if _is_glue_marker(entry):
                continue
            try:
                space.place_entry(address, key, entry)
            except Exception as exc:
                raise PersistenceError("entry placement failed during "
                                       "materialization: %r" % (key,)) from exc
    for spec, address in zip(specs, addresses):
        entries = spec.get("entries") or {}
        for key, entry in entries.items():
            if not _is_glue_marker(entry):
                continue
            canonical = space.lookup(key)
            if canonical is None:
                raise PersistenceError("glue marker has no canonical entry: "
                                       "%r" % (key,))
            if tuple(entry["glued"]) != space.home_of(key):
                raise PersistenceError("glue marker home mismatch for "
                                       "%r" % (key,))
            try:
                space.place_entry(address, key, canonical)
            except Exception as exc:
                raise PersistenceError("glue placement failed during "
                                       "materialization: %r" % (key,)) from exc
    for spec, address in zip(specs, addresses):
        edges = spec.get("edges") or []
        if not isinstance(edges, list):
            raise PersistenceError("graph edges are malformed")
        for edge in edges:
            try:
                space.place_edge(address, edge)
            except Exception as exc:
                raise PersistenceError("edge placement failed during "
                                       "materialization") from exc
    return space


def analyzer_space_checksum(space):
    """The record-filtered checksum of an analyzer own space: the
    SHA-256 of the canonical bytes of a throwaway space materialized
    from the space's persisted structure (typed encode/decode round
    trip, analysis records excluded per Decision 4, exactly the
    round trip a load performs). This is the checksum
    save_analyzer_space seals a save with, factored out so callers
    (the falsifiability engagement records) can fingerprint a space
    without saving it."""
    structure = _space_structure(space)
    decoded = decode_typed(encode_typed(structure))
    throwaway = _materialize_space(decoded)
    return hashlib.sha256(throwaway.canonical_bytes()).hexdigest()


def save_analyzer_space(root, space, generation, keyring_path=None):
    """Persist an analyzer own space to <root>/analyzer_space.json.
    Returns the file path.

    The checksum is computed over the canonical bytes of a throwaway
    space materialized from the DECODED structure (the exact round
    trip a load will perform), so what is sealed is exactly what a
    load can reproduce. Analysis records are excluded (Decision 4).

    Pack H: the sealed material is those canonical bytes PLUS the
    envelope generation. With keyring_path, the checksum is the
    keyed seal_v 2 form (HMAC under the ring's current key, domain
    "PMCA/state/space"); without one it is the unkeyed seal_v 1
    form. After the write, the file is read back through the full
    load path; any failure raises instead of reporting success
    (save/load symmetry, claims audit 09 D25).
    """
    if type(generation) is not int:
        raise PersistenceError("generation must be an integer")
    structure = _space_structure(space)
    encoded = encode_typed(structure)
    canonical = _materialize_space(
        decode_typed(encoded)).canonical_bytes()
    material = canonical + b"\n" + str(generation).encode("utf-8")
    envelope = {
        "format": FORMAT,
        "kind": "analyzer_space",
        "generation": generation,
        "payload": encoded,
    }
    if keyring_path is not None:
        key_id, master_key = _resolve_seal_key(keyring_path)
        envelope["seal_v"] = 2
        envelope["key_id"] = key_id
        envelope["checksum"] = hmac.new(
            _state_subkey(master_key, key_id, "analyzer_space"),
            material, hashlib.sha256).hexdigest()
    else:
        envelope["seal_v"] = 1
        envelope["checksum"] = hashlib.sha256(material).hexdigest()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, SPACE_FILENAME)
    _atomic_write(path, _envelope_bytes(envelope))
    # Read-back, as in save_seed_state: the save is not done until
    # the written file passes the full load path.
    _verify_readback(
        lambda: _load_analyzer_space_full(root,
                                          keyring_path=keyring_path))
    return path


def _load_analyzer_space_full(root, keyring_path=None):
    """Load and fully verify an analyzer space save. Returns
    (envelope, space). The space is materialized through the guarded
    calls and its canonical bytes must verify against the saved
    checksum, else SealMismatch and the space is discarded (never
    returned). The returned envelope carries "seal_state":
    "adopted_unsealed" (a pre-Pack-H file: checksum is the bare
    SHA-256 of the canonical bytes, generation outside), "unkeyed"
    (seal_v 1: generation inside), or "keyed" (seal_v 2: HMAC,
    generation inside). A seal_v 2 file REQUIRES keyring_path:
    without it, or without the envelope's named key, ArchiveLocked
    is raised."""
    path = os.path.join(root, SPACE_FILENAME)
    if not os.path.isfile(path):
        raise StateNotFound("no analyzer space file: " + path)
    envelope = _read_envelope(path)
    _check_kind_format(envelope, "analyzer_space", path)
    if "payload" not in envelope:
        raise PersistenceError("space save is missing payload: " + path)
    structure = decode_typed(envelope["payload"])
    space = _materialize_space(structure)
    canonical = space.canonical_bytes()
    seal_v = envelope.get("seal_v", 0)
    stored_checksum = envelope.get("checksum")
    if seal_v == 0:
        want = hashlib.sha256(canonical).hexdigest()
        seal_state = "adopted_unsealed"
    elif seal_v == 1:
        material = (canonical + b"\n"
                    + str(envelope["generation"]).encode("utf-8"))
        want = hashlib.sha256(material).hexdigest()
        seal_state = "unkeyed"
    elif seal_v == 2:
        if keyring_path is None:
            raise ArchiveLocked(
                "analyzer space save is key-sealed (seal_v 2) and "
                "no keyring was given: " + path)
        key_id, master_key = _resolve_seal_key(
            keyring_path, envelope.get("key_id"))
        material = (canonical + b"\n"
                    + str(envelope["generation"]).encode("utf-8"))
        want = hmac.new(
            _state_subkey(master_key, key_id, "analyzer_space"),
            material, hashlib.sha256).hexdigest()
        seal_state = "keyed"
    else:
        raise PersistenceError("space save seal version is not "
                               "supported: %r" % (seal_v,))
    if (not isinstance(stored_checksum, str)
            or not hmac.compare_digest(want, stored_checksum)):
        raise SealMismatch("analyzer space checksum does not verify: "
                           + path)
    envelope["seal_state"] = seal_state
    return envelope, space


def load_analyzer_space(root, keyring_path=None):
    """Load the persisted analyzer own space. Returns a GraphSpace.

    Missing file -> StateNotFound. Kind confusion -> ClassMismatch.
    Newer format -> FormatTooNew. Checksum failure -> SealMismatch.
    A key-sealed (seal_v 2) file loaded without its keyring ->
    ArchiveLocked. The caller passes the returned space to
    pmca_sentence_analyzer.restore_own_space.
    """
    _envelope, space = _load_analyzer_space_full(
        root, keyring_path=keyring_path)
    return space


# ---------------------------------------------------------------------
# Boot / shutdown. boot() is the only caller-facing orchestration:
# config -> root -> writer lock -> restore what exists. Any
# SealMismatch / ClassMismatch / FormatTooNew propagates and the
# boot aborts: the engine never opens on unverified state. If boot
# aborts after acquiring the lock, the lock stays held -- like a
# crash, that is an operator decision, never silently released.
# ---------------------------------------------------------------------

def boot(config_path, keyring_path=None):
    """Boot-restore from the config at config_path.

    Returns a report dict: {"root", "lock_path",
    "seed": "restored"|"fresh", "analyzer_space": "restored"|"fresh",
    "generation_seed": int|None, "generation_space": int|None,
    "seed_seal": "keyed"|"unkeyed"|"adopted_unsealed"|None,
    "space_seal": same, "archive": "absent"|"verified"|"held"|"locked"}.
    Release the writer lock with shutdown(report) at process end.

    keyring_path authenticates the state files as well as the
    archive (Pack H): a key-sealed (seal_v 2) state file restored
    without its keyring aborts the boot with ArchiveLocked — the
    engine never opens on state it could not verify. The report's
    seal fields surface each restored file's seal state, so an
    adopted legacy file is operator-visible, never silent.

    The archive is reported, never enforced: if an archive file
    exists, its status is computed when keyring_path is given, and
    reported "locked" when it is not. A held or locked archive never
    aborts boot (the ruled judgment call): the engine boots on
    verified state while the archive refuses every lookup.
    """
    config = load_config(config_path)
    root = config["storage_root"]
    os.makedirs(root, exist_ok=True)
    lock_path = acquire_lock(root)
    report = {
        "root": root,
        "lock_path": lock_path,
        "seed": "fresh",
        "analyzer_space": "fresh",
        "generation_seed": None,
        "generation_space": None,
        "seed_seal": None,
        "space_seal": None,
        "archive": "absent",
    }
    if os.path.isfile(os.path.join(root, SEED_FILENAME)):
        envelope, seed, teaching_block = _load_seed_state_full(
            root, keyring_path=keyring_path)
        import pmca_teaching
        pmca_teaching.restore_state(seed, teaching_block["event_counter"],
                                    teaching_block["audit_log"])
        report["seed"] = "restored"
        report["generation_seed"] = envelope["generation"]
        report["seed_seal"] = envelope.get("seal_state")
    if os.path.isfile(os.path.join(root, SPACE_FILENAME)):
        envelope, space = _load_analyzer_space_full(
            root, keyring_path=keyring_path)
        import pmca_sentence_analyzer
        pmca_sentence_analyzer.restore_own_space(space)
        report["analyzer_space"] = "restored"
        report["generation_space"] = envelope["generation"]
        report["space_seal"] = envelope.get("seal_state")
    if os.path.isfile(os.path.join(root, ARCHIVE_FILENAME)):
        if keyring_path is None:
            report["archive"] = "locked"
        else:
            report["archive"] = archive_status(root, keyring_path)
    return report


def shutdown(report):
    """Release the writer lock held by a boot() report."""
    release_lock(report["lock_path"])


# ---------------------------------------------------------------------
# Encrypted archive of replaced meanings (Stage C).
#
# Design Decision 5 (ruled by Alder under Jonathan's delegation):
# HMAC-SHA256 counter-mode keystream under a domain-separated
# encryption subkey; encrypt-then-MAC with an independent subkey over
# header and ciphertext, verified before any decryption. Nonces
# combine the archive's append sequence with 16 bytes of OS
# randomness, so nonce reuse requires two independent failures at
# once. (The earlier external proposal derived its IV from the
# entry's coordinates: the same address is archived on every
# replacement, so its keystream repeated across different plaintexts
# -- a two-time pad. Here the address is metadata only, and metadata
# is never an input to the keystream.) Keys live in a keyring
# OUTSIDE the storage root; entries carry their key_id; rotation
# only adds keys, so old entries stay readable; an absent key locks
# the archive, and a replacement that would need it cannot proceed.
# Conditions from the ruling: known-answer vectors and a nonce audit
# are part of the suite below; the key never travels with the data;
# if archive ciphertext is ever stored off this machine, this
# construction is replaced with a vetted AEAD first, as a format
# version bump, before the move.
#
# THE CAVEAT, kept with the code: the standard library has no vetted
# symmetric cipher. This construction is hand-assembled from sound
# primitives in a standard pattern (HMAC as a PRF in counter mode is
# the HKDF-Expand pattern family) with no independent review. What
# it protects: replaced meanings -- the system's history, off the
# reasoning path -- against disclosure of the archive file separated
# from the keyring, with any tampering detected by the MAC. What it
# does not protect: compromise of the whole machine, and key loss.
# Key loss is archive loss; there is no recovery path and no
# backdoor, by construction. Entry lengths are visible (no padding);
# sizes leak, contents do not.
# ---------------------------------------------------------------------

def _key_id_number(key_id):
    """Numeric part of a keyring key id ("k12" -> 12)."""
    if (not isinstance(key_id, str) or len(key_id) < 2
            or key_id[0] != "k" or not key_id[1:].isdigit()):
        raise ValueError("malformed key id: %r" % (key_id,))
    return int(key_id[1:])


def _archive_subkeys(master_key, key_id):
    """Domain-separated subkeys for one key id: HMAC-SHA256 over the
    domain label, the key id as u32be, and the archive format major
    as u16be. The master key is never used directly, so the
    encryption key and the authentication key are independent, and
    a future format version derives fresh subkeys automatically."""
    suffix = (_key_id_number(key_id).to_bytes(4, "big")
              + ARCHIVE_FORMAT.to_bytes(2, "big"))
    enc_key = hmac.new(master_key, b"PMCA/archive/enc" + suffix,
                       hashlib.sha256).digest()
    mac_key = hmac.new(master_key, b"PMCA/archive/mac" + suffix,
                       hashlib.sha256).digest()
    return enc_key, mac_key


def _keystream(enc_key, nonce_material, nbytes):
    """HMAC-SHA256 counter-mode keystream: block i is
    HMAC-SHA256(enc_key, nonce_material || i as u64be), i from 0,
    truncated to nbytes."""
    out = bytearray()
    counter = 0
    while len(out) < nbytes:
        out += hmac.new(enc_key,
                        nonce_material + counter.to_bytes(8, "big"),
                        hashlib.sha256).digest()
        counter += 1
    return bytes(out[:nbytes])


def _xor_bytes(data, pad):
    return bytes(a ^ b for a, b in zip(data, pad))


def _contains_float(obj):
    """True iff a float occurs anywhere inside obj."""
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(_contains_float(value) for value in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_float(item) for item in obj)
    return False


# ---------------------------------------------------------------------
# Keyring. A JSON file the CALLER places outside the storage root:
# {"keys": {"k1": <hex>, ...}, "current": "k1"}. Key material is
# generated from the OS, never derived from anything in the data,
# and no function in this module writes it into the root.
# ---------------------------------------------------------------------

def _write_keyring(path, ring):
    """Write a keyring file atomically: temp file in the same
    directory (mkstemp, so the file is created mode 0600 and key
    material is never world-readable), flush, fsync, rename."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".keyring-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical_json(ring).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    _fsync_dir(directory)


def create_keyring(path):
    """Create a new keyring at path with one key, "k1", and return
    the keyring dict. Refuses, loudly, to overwrite an existing
    keyring: overwriting would orphan every entry the old keys
    protect."""
    if os.path.exists(path):
        raise PersistenceError("keyring already exists, refusing to "
                               "overwrite: " + str(path))
    ring = {"keys": {"k1": secrets.token_bytes(32).hex()}, "current": "k1"}
    _write_keyring(path, ring)
    return ring


def load_keyring(path):
    """Load and validate a keyring file. Missing file ->
    ArchiveLocked. Malformed content -> PersistenceError."""
    if not os.path.isfile(path):
        raise ArchiveLocked("keyring not found: " + str(path))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            ring = json.loads(handle.read())
    except (OSError, ValueError) as exc:
        raise PersistenceError("keyring is unreadable or malformed: "
                               + str(path)) from exc
    if (not isinstance(ring, dict)
            or not isinstance(ring.get("keys"), dict)
            or not ring["keys"]
            or ring.get("current") not in ring["keys"]):
        raise PersistenceError("keyring structure is wrong: " + str(path))
    for key_id, material in ring["keys"].items():
        try:
            _key_id_number(key_id)
            if not isinstance(material, str):
                raise ValueError("key material is not a hex string")
            bytes.fromhex(material)
        except (ValueError, TypeError) as exc:
            raise PersistenceError("keyring key material is malformed: "
                                   + str(path)) from exc
    return ring


def add_key(path):
    """Append a fresh key to an existing keyring, make it current,
    and return the new key id ("k2", "k3", ...). Old keys are never
    removed: a key that protects any existing entry must stay
    readable forever -- rotation adds keys, it never retires the
    ability to read."""
    ring = load_keyring(path)
    new_id = "k" + str(max(_key_id_number(key_id)
                            for key_id in ring["keys"]) + 1)
    ring["keys"][new_id] = secrets.token_bytes(32).hex()
    ring["current"] = new_id
    _write_keyring(path, ring)
    return new_id


# ---------------------------------------------------------------------
# Entry encryption. encrypt_entry is a pure function of its inputs,
# pinned byte-for-byte by the suite's known-answer test.
# ---------------------------------------------------------------------

def _entry_header(fields):
    """The authenticated header of an archive entry: every field
    except the ciphertext and the tag, as canonical JSON bytes."""
    return _canonical_json({
        "v": fields["v"],
        "key_id": fields["key_id"],
        "seq": fields["seq"],
        "nonce": fields["nonce"],
        "label": fields["label"],
    }).encode("utf-8")


def encrypt_entry(master_key, key_id, seq, nonce_bytes, label, payload_obj):
    """Encrypt one archive entry and return its entry dict:
    {"v", "seq", "key_id", "label", "nonce", "ct", "tag"}.

    master_key is the raw key bytes named by key_id; nonce_bytes is
    the full nonce material (the archive append sequence as u64be
    followed by 16 bytes of OS randomness, built by archive_append).
    The payload's canonical JSON bytes are XORed with the
    counter-mode keystream under the encryption subkey; the tag is
    HMAC-SHA256 under the MAC subkey over the encoded header
    followed by the ciphertext (encrypt-then-MAC). A float anywhere
    in the payload raises: the system bans floats, so one in an
    archived meaning is corruption."""
    if not isinstance(master_key, (bytes, bytearray)):
        raise PersistenceError("archive master key must be bytes")
    if type(seq) is not int or seq < 0:
        raise PersistenceError("archive seq must be a non-negative integer")
    if not isinstance(nonce_bytes, (bytes, bytearray)) or not nonce_bytes:
        raise PersistenceError("archive nonce must be non-empty bytes")
    if not isinstance(label, str):
        raise PersistenceError("archive label must be a string")
    if _contains_float(payload_obj):
        raise PersistenceError("float in archive payload (corruption)")
    try:
        enc_key, mac_key = _archive_subkeys(bytes(master_key), key_id)
    except ValueError as exc:
        raise PersistenceError(str(exc)) from exc
    nonce_bytes = bytes(nonce_bytes)
    plaintext = _canonical_json(payload_obj).encode("utf-8")
    ciphertext = _xor_bytes(plaintext,
                            _keystream(enc_key, nonce_bytes, len(plaintext)))
    entry = {"v": ARCHIVE_FORMAT, "seq": seq, "key_id": key_id,
             "label": label, "nonce": nonce_bytes.hex(),
             "ct": ciphertext.hex()}
    entry["tag"] = hmac.new(mac_key, _entry_header(entry) + ciphertext,
                            hashlib.sha256).hexdigest()
    return entry


def decrypt_entry(keyring, entry):
    """Decrypt one archive entry against a loaded keyring dict and
    return its payload.

    Order is fixed: the entry's key is looked up first (absent ->
    ArchiveLocked); the tag is verified with hmac.compare_digest
    BEFORE any decryption (mismatch -> ArchiveHeld); only then is
    the payload recovered. Payloads return in JSON-normalized form:
    a tuple archived through canonical JSON returns as a list."""
    if not isinstance(entry, dict):
        raise ArchiveHeld("archive entry is not an object")
    try:
        version = entry["v"]
        key_id = entry["key_id"]
        seq = entry["seq"]
        nonce_hex = entry["nonce"]
        label = entry["label"]
        ct_hex = entry["ct"]
        tag_hex = entry["tag"]
    except (KeyError, TypeError) as exc:
        raise ArchiveHeld("archive entry is missing fields") from exc
    if type(version) is not int:
        raise ArchiveHeld("archive entry version is not an integer")
    if version > ARCHIVE_FORMAT:
        raise FormatTooNew("archive entry format %d is newer than this "
                           "code supports (%d)"
                           % (version, ARCHIVE_FORMAT))
    if version < 1:
        raise ArchiveHeld("archive entry version is invalid")
    keys = keyring.get("keys") if isinstance(keyring, dict) else None
    if not isinstance(keys, dict) or key_id not in keys:
        raise ArchiveLocked("archive key absent for entry: %r" % (key_id,))
    try:
        master_key = bytes.fromhex(keys[key_id])
        nonce = bytes.fromhex(nonce_hex)
        ciphertext = bytes.fromhex(ct_hex)
        enc_key, mac_key = _archive_subkeys(master_key, key_id)
    except (ValueError, TypeError) as exc:
        raise ArchiveHeld("archive entry fields are malformed") from exc
    header = {"v": version, "key_id": key_id, "seq": seq,
              "nonce": nonce_hex, "label": label}
    want_tag = hmac.new(mac_key, _entry_header(header) + ciphertext,
                        hashlib.sha256).hexdigest()
    if (not isinstance(tag_hex, str)
            or not hmac.compare_digest(want_tag, tag_hex)):
        raise ArchiveHeld("archive entry tag does not verify (seq %r)"
                          % (seq,))
    plaintext = _xor_bytes(ciphertext,
                           _keystream(enc_key, nonce, len(ciphertext)))
    try:
        return json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ArchiveHeld("archive entry payload does not decode "
                          "(seq %r)" % (seq,)) from exc


# ---------------------------------------------------------------------
# The archive file: <root>/archive.jsonl, append-only, one entry
# JSON per line, flush + fsync per append (the pools discipline).
# Reads verify everything before returning anything: failure is
# total inside the archive domain, never a silent partial answer.
# ---------------------------------------------------------------------

def _archive_path(root):
    return os.path.join(root, ARCHIVE_FILENAME)


def _archive_line_count(root):
    """The next append sequence: the current line count of the
    archive file (0 if absent). The archive is append-only, so this
    is strictly increasing for the life of the archive."""
    path = _archive_path(root)
    if not os.path.isfile(path):
        return 0
    count = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


# ---------------------------------------------------------------------
# Archive head commitment (Pack H). The entries' tags authenticate
# each entry, but nothing in the pre-Pack-H format commits to HOW
# MANY entries exist: deleting the last line at a clean boundary
# left a shorter archive that verified perfectly (claims audit 09,
# section 5d). The head file <root>/archive.head.json closes that:
# {"format", "kind": "archive_head", "count", "key_id", "digest"},
# where digest is a keyed chain over every entry's (seq, tag) in
# order, under a domain-separated head subkey. archive_append
# rewrites it (atomically) after every append; archive_read_all
# verifies it whenever it exists. A missing head file means a
# pre-Pack-H archive: reads verify in legacy form and the next
# append adopts the commitment. A crash between the entry append
# and the head rewrite leaves the head one entry behind: the next
# read raises ArchiveHeld (loud, fail-closed), and the next append
# recomputes the head from the full file and heals it.
# ---------------------------------------------------------------------

def _archive_head_path(root):
    return os.path.join(root, ARCHIVE_HEAD_FILENAME)


def _archive_head_subkey(master_key, key_id):
    """Domain-separated subkey for the archive head chain:
    HMAC-SHA256 over the head domain label, the key id as u32be,
    and the archive format major as u16be (the _archive_subkeys
    pattern, a third domain)."""
    suffix = (_key_id_number(key_id).to_bytes(4, "big")
              + ARCHIVE_FORMAT.to_bytes(2, "big"))
    return hmac.new(master_key, b"PMCA/archive/head" + suffix,
                    hashlib.sha256).digest()


def _archive_chain_digest(head_key, entries):
    """The head chain over parsed entries: HMAC-chained (seq, tag)
    pairs from a genesis value, so the digest commits to the count,
    the order, and every entry's tag at once."""
    digest = hmac.new(head_key, b"PMCA/archive/head/genesis",
                      hashlib.sha256).digest()
    for entry in entries:
        digest = hmac.new(
            head_key,
            digest + str(entry.get("seq")).encode("utf-8")
            + b":" + str(entry.get("tag")).encode("utf-8"),
            hashlib.sha256).digest()
    return digest.hex()


def _write_archive_head(root, ring):
    """Rewrite the head commitment for the archive's current
    contents under the keyring's current key. Called by
    archive_append after the entry line is durable; recomputed from
    the full file, so it also heals a head left behind by a crash
    and adopts a pre-Pack-H archive on its first Pack H append."""
    if os.path.isfile(_archive_path(root)):
        entries = _load_archive_entries(root)
    else:
        entries = []
    key_id = ring["current"]
    master_key = bytes.fromhex(ring["keys"][key_id])
    head = {
        "format": ARCHIVE_FORMAT,
        "kind": "archive_head",
        "count": len(entries),
        "key_id": key_id,
        "digest": _archive_chain_digest(
            _archive_head_subkey(master_key, key_id), entries),
    }
    _atomic_write(_archive_head_path(root), _envelope_bytes(head))
    return head


def _verify_archive_head(root, ring, entries):
    """Verify the head commitment against parsed entries, when a
    head file exists. Absent head file -> a pre-Pack-H archive,
    nothing to verify (adoption happens on the next append). A
    present but malformed head, a count mismatch (the
    tail-truncation signature), or a chain mismatch ->
    ArchiveHeld. A head whose key is absent from the ring ->
    ArchiveLocked."""
    head_path = _archive_head_path(root)
    if not os.path.isfile(head_path):
        return
    try:
        with open(head_path, "rb") as handle:
            head = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ArchiveHeld("archive head commitment is unreadable") from exc
    if (not isinstance(head, dict) or head.get("kind") != "archive_head"
            or type(head.get("count")) is not int
            or not isinstance(head.get("digest"), str)):
        raise ArchiveHeld("archive head commitment is malformed")
    if head["count"] != len(entries):
        raise ArchiveHeld(
            "archive length commitment does not verify: head "
            "commits to %d entries, the file holds %d"
            % (head["count"], len(entries)))
    key_id = head.get("key_id")
    keys = ring.get("keys") if isinstance(ring, dict) else None
    if not isinstance(keys, dict) or key_id not in keys:
        raise ArchiveLocked("archive head key absent: %r" % (key_id,))
    try:
        master_key = bytes.fromhex(keys[key_id])
        want = _archive_chain_digest(
            _archive_head_subkey(master_key, key_id), entries)
    except (ValueError, TypeError) as exc:
        raise ArchiveHeld("archive head commitment is malformed") from exc
    if not hmac.compare_digest(want, head["digest"]):
        raise ArchiveHeld("archive head chain does not verify")


def archive_append(root, keyring_path, label, payload_obj):
    """Archive one replaced meaning under label and return the
    stored entry dict.

    The keyring loads first: a missing keyring raises ArchiveLocked,
    because archiving the old meaning is a precondition of replacing
    it -- a replacement can never proceed, or silently skip the
    archive, without the key. The nonce is the append sequence as
    u64be followed by 16 bytes of OS randomness. The append is one
    full-line write, flushed and fsynced before returning; the head
    commitment (Pack H) is then rewritten over the archive as it now
    stands."""
    ring = load_keyring(keyring_path)
    key_id = ring["current"]
    master_key = bytes.fromhex(ring["keys"][key_id])
    seq = _archive_line_count(root)
    nonce = seq.to_bytes(8, "big") + secrets.token_bytes(16)
    entry = encrypt_entry(master_key, key_id, seq, nonce, label, payload_obj)
    os.makedirs(root, exist_ok=True)
    with open(_archive_path(root), "a", encoding="utf-8") as handle:
        handle.write(_canonical_json(entry) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _write_archive_head(root, ring)
    return entry


def _load_archive_entries(root):
    """Parse every line of the archive file into entry dicts. Any
    malformed line raises ArchiveHeld: the archive fails as a whole,
    never partially."""
    entries = []
    with open(_archive_path(root), "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                raise ArchiveHeld("archive line is not valid JSON") from exc
            if not isinstance(obj, dict):
                raise ArchiveHeld("archive line is not an entry object")
            entries.append(obj)
    return entries


def archive_read_all(root, keyring_path):
    """Read the whole archive: a list of (label, payload) pairs in
    seq order. Every entry's sequence position and tag is verified
    before anything is returned, and the head commitment (Pack H)
    is verified whenever a head file exists — a count or chain
    mismatch raises ArchiveHeld, so a tail truncated at a clean
    line boundary no longer reads as verified. Any failure raises
    (ArchiveLocked for an absent key, ArchiveHeld for any
    verification failure) and NOTHING is returned. A missing
    archive file reads as an empty list."""
    if not os.path.isfile(_archive_path(root)):
        return []
    ring = load_keyring(keyring_path)
    entries = _load_archive_entries(root)
    _verify_archive_head(root, ring, entries)
    out = []
    for position, entry in enumerate(entries):
        if entry.get("seq") != position:
            raise ArchiveHeld("archive sequence is broken at position %d"
                              % position)
        payload = decrypt_entry(ring, entry)
        out.append((entry["label"], payload))
    return out


def archive_status(root, keyring_path):
    """Report the archive's state, without raising for content
    problems: "absent" (no archive file), "verified" (every entry
    verifies), "held" (any entry fails verification or is
    malformed), "locked" (the keyring, or a key an entry needs, is
    absent)."""
    if not os.path.isfile(_archive_path(root)):
        return "absent"
    try:
        archive_read_all(root, keyring_path)
    except ArchiveLocked:
        return "locked"
    except PersistenceError:
        return "held"
    return "verified"


def archive_get(root, keyring_path, label):
    """The most recently archived payload under label, or None if
    the label was never archived. Reads through archive_read_all: a
    held archive raises ArchiveHeld here too -- a lookup never
    answers from an unverified archive."""
    found = None
    for entry_label, payload in archive_read_all(root, keyring_path):
        if entry_label == label:
            found = payload
    return found


# ---------------------------------------------------------------------
# Test suite. Temp directories only; nothing is written outside them.
# ---------------------------------------------------------------------

def _raises(exc_type, fn):
    """True iff fn() raises exc_type (a different exception, or no
    exception, is a failure)."""
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def run_suite():
    """Run all persistence checks. Return list of (name, value)."""
    import shutil

    import pmca_seed
    import pmca_sentence_analyzer as psa
    import pmca_teaching

    results = []

    # ---- Config ----
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "nope.json")
        results.append(("config_missing_file",
                        _raises(ConfigError, lambda: load_config(missing))))
        bad = os.path.join(d, "bad.json")
        _write_text(bad, "{not json")
        results.append(("config_malformed_json",
                        _raises(ConfigError, lambda: load_config(bad))))
        unk = os.path.join(d, "unk.json")
        _write_text(unk, json.dumps({"format": 1, "storage_root": "s",
                                     "bogus": 1}))
        results.append(("config_unknown_key",
                        _raises(ConfigError, lambda: load_config(unk))))
        url = os.path.join(d, "url.json")
        _write_text(url, json.dumps({"format": 1,
                                     "storage_root": "hf://buckets/x"}))
        results.append(("config_url_root_rejected",
                        _raises(ConfigError, lambda: load_config(url))))
        nrt = os.path.join(d, "noroot.json")
        _write_text(nrt, json.dumps({"format": 1}))
        results.append(("config_root_required",
                        _raises(ConfigError, lambda: load_config(nrt))))
        bfmt = os.path.join(d, "bfmt.json")
        _write_text(bfmt, json.dumps({"format": 2, "storage_root": "s"}))
        bfmt2 = os.path.join(d, "bfmt2.json")
        _write_text(bfmt2, json.dumps({"format": "1", "storage_root": "s"}))
        results.append(("config_bad_format",
                        _raises(ConfigError, lambda: load_config(bfmt))
                        and _raises(ConfigError, lambda: load_config(bfmt2))))
        rel = os.path.join(d, "rel.json")
        _write_text(rel, json.dumps({"format": 1, "storage_root": "store"}))
        cfg = load_config(rel)
        results.append(("config_relative_resolution",
                        cfg["storage_root"] == os.path.abspath(
                            os.path.join(d, "store"))
                        and cfg["format"] == 1))

    # ---- Typed codec ----
    sample = {"a": (1, "x", (2, 3)), "b": [{"c": (True, None)}],
              "d": [1, [2, (3,)]], "e": "plain", "f": 7}
    back = decode_typed(encode_typed(sample))
    results.append(("codec_roundtrip_nested",
                    back == sample and isinstance(back["a"], tuple)
                    and isinstance(back["a"][2], tuple)
                    and isinstance(back["b"][0]["c"], tuple)
                    and isinstance(back["d"][1][1], tuple)))
    sole = {TUPLE_TAG: 5}
    sole_back = decode_typed(encode_typed(sole))
    results.append(("codec_reserved_key_sole",
                    isinstance(sole_back, dict) and sole_back == sole))
    mixed = {TUPLE_TAG: 5, "a": 1}
    mixed_back = decode_typed(encode_typed(mixed))
    results.append(("codec_reserved_key_mixed",
                    isinstance(mixed_back, dict) and mixed_back == mixed))
    results.append(("codec_int_key_raises",
                    _raises(PersistenceError,
                            lambda: encode_typed({1: "x"}))))
    results.append(("codec_float_raises",
                    _raises(PersistenceError,
                            lambda: encode_typed({"a": float("1.5")}))))

    # ---- Writer lock ----
    with tempfile.TemporaryDirectory() as d:
        lock_path = acquire_lock(d)
        held = _raises(WriterLockHeld, lambda: acquire_lock(d))
        release_lock(lock_path)
        gone = not os.path.exists(lock_path)
        lock_path2 = acquire_lock(d)
        release_lock(lock_path2)
        results.append(("lock_cycle", held and gone
                        and lock_path == lock_path2))

    # ---- Seed round trip: pending state ----
    pmca_teaching.clear_audit_log()
    counter_before = pmca_teaching.current_event_counter()
    base = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    event = {"input": "stab the fish", "human_verdict": "APPROVE",
             "explanation_text": "stabbing a fish is cooking, not violence",
             "edge_updates": [{"source": "stab", "target": "fish", "harm": 0,
                               "reason": "stabbing a fish is cooking"}]}
    teach_result = pmca_teaching.teach(base, event)
    pend = teach_result["seed"]
    with tempfile.TemporaryDirectory() as d:
        save_seed_state(d, pend, 1)
        loaded_pend, block = load_seed_state(d)
    results.append(("seed_pending_roundtrip",
                    teach_result["status"] == "applied"
                    and pmca_seed.canonical_seed_json(loaded_pend)
                    == pmca_seed.canonical_seed_json(pend)
                    and block["event_counter"] == counter_before + 1
                    and block["audit_log"]
                    == pmca_teaching.current_audit_log()
                    and "pending_event" in loaded_pend
                    and loaded_pend["metadata"]["seal"] == ""))

    # ---- Seed round trip: approved (sealed, non-pending) ----
    with tempfile.TemporaryDirectory() as d:
        approved_result = pmca_teaching.approve(
            pend, archive_path=os.path.join(d, "archive.jsonl"))
        approved = approved_result["seed"]
        save_seed_state(d, approved, 2)
        loaded_appr, _block2 = load_seed_state(d)
    results.append(("seed_approve_roundtrip",
                    approved_result["status"] == "approved"
                    and pmca_seed.verify(loaded_appr)
                    and pmca_seed.canonical_seed_json(loaded_appr)
                    == pmca_seed.canonical_seed_json(approved)
                    and "pending_event" not in loaded_appr))

    # ---- Tamper: payload (post_verdicts) and teaching block ----
    with tempfile.TemporaryDirectory() as d:
        save_seed_state(d, pend, 1)
        path = os.path.join(d, SEED_FILENAME)
        with open(path, "rb") as handle:
            obj = json.loads(handle.read().decode("utf-8"))
        post = obj["payload"]["pending_event"]["post_verdicts"]
        key0 = sorted(post)[0]
        post[key0] = "HOLD" if post[key0] != "HOLD" else "APPROVE"
        _write_text(path, json.dumps(obj, sort_keys=True,
                                     separators=(",", ":")))
        results.append(("tamper_payload_seal_mismatch",
                        _raises(SealMismatch, lambda: load_seed_state(d))))
    with tempfile.TemporaryDirectory() as d:
        save_seed_state(d, pend, 1)
        path = os.path.join(d, SEED_FILENAME)
        with open(path, "rb") as handle:
            obj = json.loads(handle.read().decode("utf-8"))
        obj["teaching"]["event_counter"] = obj["teaching"]["event_counter"] + 100
        _write_text(path, json.dumps(obj, sort_keys=True,
                                     separators=(",", ":")))
        results.append(("tamper_teaching_seal_mismatch",
                        _raises(SealMismatch, lambda: load_seed_state(d))))

    # ---- Class confusion, both directions ----
    with tempfile.TemporaryDirectory() as da, \
            tempfile.TemporaryDirectory() as db, \
            tempfile.TemporaryDirectory() as dc:
        sealed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(sealed)
        save_seed_state(da, sealed, 1)
        empty_space = pmca_graphspace.GraphSpace("own")
        save_analyzer_space(db, empty_space, 1)
        shutil.copyfile(os.path.join(db, SPACE_FILENAME),
                        os.path.join(da, SEED_FILENAME))
        conf1 = _raises(ClassMismatch, lambda: load_seed_state(da))
        save_seed_state(dc, sealed, 1)
        shutil.copyfile(os.path.join(dc, SEED_FILENAME),
                        os.path.join(db, SPACE_FILENAME))
        conf2 = _raises(ClassMismatch, lambda: load_analyzer_space(db))
        results.append(("class_confusion_both_directions", conf1 and conf2))

    # ---- Format too new ----
    with tempfile.TemporaryDirectory() as d:
        sealed2 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(sealed2)
        save_seed_state(d, sealed2, 1)
        path = os.path.join(d, SEED_FILENAME)
        with open(path, "rb") as handle:
            obj = json.loads(handle.read().decode("utf-8"))
        obj["format"] = 2
        _write_text(path, json.dumps(obj, sort_keys=True,
                                     separators=(",", ":")))
        results.append(("format_too_new",
                        _raises(FormatTooNew, lambda: load_seed_state(d))))

    # ---- Analyzer space round trip ----
    own = pmca_graphspace.GraphSpace("own")
    r_hold = psa.analyze("He breaks the window.", space=own)
    psa.register_logic("breaks:repairable", 1, space=own)
    r_ok = psa.analyze("He breaks the window.", space=own)
    with tempfile.TemporaryDirectory() as d:
        save_analyzer_space(d, own, 1)
        loaded_space = load_analyzer_space(d)
    logic = loaded_space.lookup("breaks:repairable")
    logic_ok = (isinstance(logic, dict) and logic.get("kind") == "own_logic"
                and logic.get("identifier") == 1)
    record_count = 0
    for graph in loaded_space.graphs.values():
        for entry in graph["entries"].values():
            if isinstance(entry, dict) and entry.get("kind") == "analysis_record":
                record_count += 1
    psa.restore_own_space(loaded_space)
    r_restored = psa.analyze("He breaks the window.")
    own_noted = any("provenance: own" in line for line in r_restored["trace"])
    results.append(("analyzer_roundtrip",
                    (r_hold["verdict"], r_ok["verdict"],
                     r_restored["verdict"], logic_ok, record_count == 0,
                     own_noted)))

    # ---- Analyzer fidelity: glued marker + tuple values + edge ----
    addr1 = (1, "A", 1)
    addr2 = (2, "C", -7)
    hand = pmca_graphspace.GraphSpace("own")
    hand.produce_graph(addr1, "own", "suite")
    hand.produce_graph(addr2, "own", "suite")
    note = {"kind": "note", "pair": (3, 4), "nested": {"t": (1, 2)},
            "name": "k1"}
    hand.place_entry(addr1, "k1", note)
    hand.place_entry(addr2, "k1", note)
    hand.place_edge(addr1, {"source": "k1", "target": "k1", "role": "self"})
    original_bytes = hand.canonical_bytes()
    with tempfile.TemporaryDirectory() as d:
        save_analyzer_space(d, hand, 1)
        loaded_hand = load_analyzer_space(d)
    marker = loaded_hand.graphs[addr2]["entries"]["k1"]
    restored_note = loaded_hand.lookup("k1")
    results.append(("analyzer_fidelity_glue_tuples_edges",
                    loaded_hand.canonical_bytes() == original_bytes
                    and marker == {"glued": addr1}
                    and isinstance(marker["glued"], tuple)
                    and restored_note["pair"] == (3, 4)
                    and isinstance(restored_note["pair"], tuple)))

    # ---- Previous generation: manual restore path ----
    with tempfile.TemporaryDirectory() as d:
        gen_seed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(gen_seed)
        save_seed_state(d, gen_seed, 1)
        save_seed_state(d, gen_seed, 2)
        path = os.path.join(d, SEED_FILENAME)
        prev_exists = os.path.isfile(path + ".prev")
        with open(path, "rb") as handle:
            current_env = json.loads(handle.read().decode("utf-8"))
        os.remove(path)
        os.replace(path + ".prev", path)
        with open(path, "rb") as handle:
            restored_env = json.loads(handle.read().decode("utf-8"))
        loaded_gen, _block3 = load_seed_state(d)
        results.append(("previous_generation_manual_restore",
                        prev_exists and current_env["generation"] == 2
                        and restored_env["generation"] == 1
                        and pmca_seed.canonical_seed_json(loaded_gen)
                        == pmca_seed.canonical_seed_json(gen_seed)
                        and pmca_seed.verify(loaded_gen)))

    # ---- Determinism: same state, same generation, two roots ----
    with tempfile.TemporaryDirectory() as dx, \
            tempfile.TemporaryDirectory() as dy:
        det_seed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(det_seed)
        path_x = save_seed_state(dx, det_seed, 1)
        path_y = save_seed_state(dy, det_seed, 1)
        with open(path_x, "rb") as handle:
            bytes_x = handle.read()
        with open(path_y, "rb") as handle:
            bytes_y = handle.read()
        results.append(("byte_determinism_two_roots", bytes_x == bytes_y))

    # ---- Boot: fresh, then restored ----
    with tempfile.TemporaryDirectory() as d:
        cfg_path = os.path.join(d, "pmca_storage.json")
        _write_text(cfg_path, json.dumps({"format": 1,
                                          "storage_root": "store"}))
        rep1 = boot(cfg_path)
        lock_seen = os.path.isfile(rep1["lock_path"])
        shutdown(rep1)
        lock_gone = not os.path.exists(rep1["lock_path"])
        pmca_teaching.clear_audit_log()
        boot_base = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        boot_event = {"input": "stab the fish", "human_verdict": "APPROVE",
                      "explanation_text": "stabbing a fish is cooking",
                      "edge_updates": [{"source": "stab", "target": "fish",
                                        "harm": 0,
                                        "reason": "stabbing a fish is "
                                                  "cooking"}]}
        boot_pend = pmca_teaching.teach(boot_base, boot_event)["seed"]
        boot_appr = pmca_teaching.approve(
            boot_pend, archive_path=os.path.join(d, "arch.jsonl"))["seed"]
        counter_at_save = pmca_teaching.current_event_counter()
        save_seed_state(rep1["root"], boot_appr, 1)
        boot_space = pmca_graphspace.GraphSpace("own")
        psa.register_logic("breaks:repairable", 1, space=boot_space)
        save_analyzer_space(rep1["root"], boot_space, 1)
        rep2 = boot(cfg_path)
        counter_match = (pmca_teaching.current_event_counter()
                         == counter_at_save)
        shutdown(rep2)
        results.append(("boot_fresh_then_restored",
                        (rep1["seed"], rep1["analyzer_space"], lock_seen,
                         lock_gone, rep2["seed"], rep2["analyzer_space"],
                         rep2["generation_seed"], rep2["generation_space"],
                         counter_match)))

    # ---- Archive: known-answer test + decrypt round trips ----
    # The expected entry is recomputed here from raw hashlib/hmac
    # primitives, written out in full -- the test does NOT call the
    # module's subkey, keystream, or header helpers, so a drift in
    # any of them fails this check loudly.
    kat_master = bytes(range(32))
    kat_nonce = (7).to_bytes(8, "big") + bytes(range(16, 32))
    kat_payload = {"address": [1, "B", -14], "meaning": "kat meaning",
                   "n": 3}
    kat_entry = encrypt_entry(kat_master, "k1", 7, kat_nonce, "kat-label",
                              kat_payload)
    kat_suffix = (1).to_bytes(4, "big") + (1).to_bytes(2, "big")
    kat_enc = hmac.new(kat_master, b"PMCA/archive/enc" + kat_suffix,
                       hashlib.sha256).digest()
    kat_mac = hmac.new(kat_master, b"PMCA/archive/mac" + kat_suffix,
                       hashlib.sha256).digest()
    kat_pt = json.dumps(kat_payload, sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True).encode("utf-8")
    kat_stream = b""
    kat_i = 0
    while len(kat_stream) < len(kat_pt):
        kat_stream += hmac.new(kat_enc,
                               kat_nonce + kat_i.to_bytes(8, "big"),
                               hashlib.sha256).digest()
        kat_i += 1
    kat_ct = bytes(a ^ b for a, b in zip(kat_pt, kat_stream))
    kat_header = json.dumps({"v": 1, "key_id": "k1", "seq": 7,
                             "nonce": kat_nonce.hex(),
                             "label": "kat-label"},
                            sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True).encode("utf-8")
    kat_tag = hmac.new(kat_mac, kat_header + kat_ct,
                       hashlib.sha256).hexdigest()
    kat_ring = {"keys": {"k1": kat_master.hex()}, "current": "k1"}
    kat_rt1 = decrypt_entry(kat_ring, kat_entry) == kat_payload
    kat_rt2 = decrypt_entry(
        kat_ring, encrypt_entry(kat_master, "k1", 0, kat_nonce, "s",
                                "plain meaning")) == "plain meaning"
    kat_rt3 = decrypt_entry(
        kat_ring, encrypt_entry(kat_master, "k1", 0, kat_nonce, "t",
                                {"pair": (3, 4), "lst": [1, (2, 3)]})) \
        == {"pair": [3, 4], "lst": [1, [2, 3]]}
    results.append(("archive_kat",
                    kat_entry == {"v": 1, "seq": 7, "key_id": "k1",
                                  "label": "kat-label",
                                  "nonce": kat_nonce.hex(),
                                  "ct": kat_ct.hex(), "tag": kat_tag}
                    and kat_rt1 and kat_rt2 and kat_rt3))

    # ---- Archive: nonce audit (300 appends, same label + payload) ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        akr = os.path.join(d, "keyring.json")
        create_keyring(akr)
        appended = [archive_append(aroot, akr, "audit", {"x": 1})
                    for _ in range(300)]
        pairs = archive_read_all(aroot, akr)
        results.append(("archive_nonce_audit",
                        len({e["nonce"] for e in appended}) == 300
                        and len({e["ct"] for e in appended}) == 300
                        and [e["seq"] for e in appended] == list(range(300))
                        and len(pairs) == 300
                        and all(p == ("audit", {"x": 1}) for p in pairs)))

    # ---- Archive: key rotation ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        akr = os.path.join(d, "keyring.json")
        create_keyring(akr)
        e1 = archive_append(aroot, akr, "rot", {"gen": 1})
        new_id = add_key(akr)
        mid_pairs = archive_read_all(aroot, akr)
        e2 = archive_append(aroot, akr, "rot", {"gen": 2})
        end_pairs = archive_read_all(aroot, akr)
        results.append(("archive_rotation",
                        e1["key_id"] == "k1" and new_id == "k2"
                        and e2["key_id"] == "k2"
                        and mid_pairs == [("rot", {"gen": 1})]
                        and end_pairs == [("rot", {"gen": 1}),
                                          ("rot", {"gen": 2})]))

    # ---- Archive: absent key locks ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        akr = os.path.join(d, "keyring.json")
        append_locked = _raises(
            ArchiveLocked,
            lambda: archive_append(aroot, os.path.join(d, "nope.json"),
                                   "x", {"a": 1}))
        create_keyring(akr)
        archive_append(aroot, akr, "x", {"a": 1})
        add_key(akr)
        archive_append(aroot, akr, "x", {"a": 2})
        k1_only = {"keys": {"k1": load_keyring(akr)["keys"]["k1"]},
                   "current": "k1"}
        akr2 = os.path.join(d, "keyring_k1_only.json")
        _write_text(akr2, json.dumps(k1_only))
        read_locked = _raises(ArchiveLocked,
                              lambda: archive_read_all(aroot, akr2))
        results.append(("archive_absent_key",
                        (append_locked, read_locked,
                         archive_status(aroot, akr2))))

    # ---- Archive: ciphertext tamper -> held; boot reports held ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        os.makedirs(aroot)
        akr = os.path.join(d, "keyring.json")
        create_keyring(akr)
        tamper_seed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(tamper_seed)
        save_seed_state(aroot, tamper_seed, 1)
        archive_append(aroot, akr, "lbl", {"m": "one"})
        archive_append(aroot, akr, "lbl", {"m": "two"})
        apath = os.path.join(aroot, ARCHIVE_FILENAME)
        with open(apath, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        first = json.loads(lines[0])
        first["ct"] = ("0" if first["ct"][0] != "0" else "1") \
            + first["ct"][1:]
        lines[0] = json.dumps(first, sort_keys=True)
        _write_text(apath, "\n".join(lines) + "\n")
        held_read = _raises(ArchiveHeld,
                            lambda: archive_read_all(aroot, akr))
        held_get = _raises(ArchiveHeld,
                           lambda: archive_get(aroot, akr, "lbl"))
        held_status = archive_status(aroot, akr)
        cfg_path = os.path.join(d, "cfg.json")
        _write_text(cfg_path, json.dumps({"format": 1,
                                          "storage_root": aroot}))
        rep = boot(cfg_path, keyring_path=akr)
        boot_state = (rep["archive"], rep["seed"])
        shutdown(rep)
        results.append(("archive_tamper_ct_boot_held",
                        (held_read, held_get, held_status) + boot_state))

    # ---- Archive: tag tamper -> held ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        akr = os.path.join(d, "keyring.json")
        create_keyring(akr)
        archive_append(aroot, akr, "lbl", {"m": "one"})
        apath = os.path.join(aroot, ARCHIVE_FILENAME)
        with open(apath, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        first = json.loads(lines[0])
        first["tag"] = ("0" if first["tag"][0] != "0" else "1") \
            + first["tag"][1:]
        lines[0] = json.dumps(first, sort_keys=True)
        _write_text(apath, "\n".join(lines) + "\n")
        results.append(("archive_tamper_tag_held",
                        _raises(ArchiveHeld,
                                lambda: archive_read_all(aroot, akr))
                        and archive_status(aroot, akr) == "held"))

    # ---- Archive: torn tail -> held status ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        akr = os.path.join(d, "keyring.json")
        create_keyring(akr)
        archive_append(aroot, akr, "lbl", {"m": "one"})
        archive_append(aroot, akr, "lbl", {"m": "two"})
        apath = os.path.join(aroot, ARCHIVE_FILENAME)
        with open(apath, "rb") as handle:
            data = handle.read()
        with open(apath, "wb") as handle:
            handle.write(data[:-10])
        results.append(("archive_truncated_held",
                        archive_status(aroot, akr) == "held"))

    # ---- Archive: the key never travels with the data ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        akr = os.path.join(d, "keyring.json")
        create_keyring(akr)
        for i in range(3):
            archive_append(aroot, akr, "sep", {"i": i})
        add_key(akr)
        archive_append(aroot, akr, "sep", {"i": 3})
        materials = [material.encode("utf-8")
                     for material in load_keyring(akr)["keys"].values()]
        leaked = False
        for dirpath, _dirnames, filenames in os.walk(aroot):
            for name in filenames:
                with open(os.path.join(dirpath, name), "rb") as handle:
                    blob = handle.read()
                if any(material in blob for material in materials):
                    leaked = True
        results.append(("archive_key_separation", not leaked))

    # ---- Archive: boot report states ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        cfg_path = os.path.join(d, "cfg.json")
        _write_text(cfg_path, json.dumps({"format": 1,
                                          "storage_root": aroot}))
        rep = boot(cfg_path)
        state_absent = rep["archive"]
        shutdown(rep)
        akr = os.path.join(d, "keyring.json")
        create_keyring(akr)
        archive_append(aroot, akr, "x", {"a": 1})
        rep = boot(cfg_path)
        state_nokey = rep["archive"]
        shutdown(rep)
        rep = boot(cfg_path, keyring_path=akr)
        state_verified = rep["archive"]
        shutdown(rep)
        results.append(("archive_boot_states",
                        (state_absent, state_nokey, state_verified)))

    # ---- Pack H: keyed seed seal — round trip + key required ----
    with tempfile.TemporaryDirectory() as d:
        hroot = os.path.join(d, "store")
        hkr = os.path.join(d, "keyring.json")
        create_keyring(hkr)
        hseed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(hseed)
        save_seed_state(hroot, hseed, 3, keyring_path=hkr)
        spath = os.path.join(hroot, SEED_FILENAME)
        with open(spath, "rb") as handle:
            keyed_env = json.loads(handle.read().decode("utf-8"))
        loaded_hseed, _hblock = load_seed_state(hroot, keyring_path=hkr)
        keyed_rt = (keyed_env.get("seal_v") == 2
                    and keyed_env.get("key_id") == "k1"
                    and pmca_seed.canonical_seed_json(loaded_hseed)
                    == pmca_seed.canonical_seed_json(hseed))
        needs_key = _raises(ArchiveLocked, lambda: load_seed_state(hroot))
        results.append(("packh_seed_keyed_roundtrip",
                        keyed_rt and needs_key))

    # ---- Pack H: seed forgery with the pre-Pack-H attack refused ----
    with tempfile.TemporaryDirectory() as d:
        froot = os.path.join(d, "store")
        fkr = os.path.join(d, "keyring.json")
        create_keyring(fkr)
        fseed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(fseed)
        save_seed_state(froot, fseed, 1, keyring_path=fkr)
        spath = os.path.join(froot, SEED_FILENAME)
        with open(spath, "rb") as handle:
            fenv = json.loads(handle.read().decode("utf-8"))
        forged_seed = decode_typed(fenv["payload"])
        forged_tb = decode_typed(fenv["teaching"])
        forged_seed["edges"][0]["harm"] = 10
        fenv["payload"] = encode_typed(forged_seed)
        # The audit's attack: reseal with the LEGACY unkeyed digest.
        fenv["seal"] = _seed_seal(forged_seed, forged_tb)
        _write_text(spath, json.dumps(fenv, sort_keys=True,
                                      separators=(",", ":")))
        refused = _raises(SealMismatch,
                          lambda: load_seed_state(froot,
                                                  keyring_path=fkr))
        results.append(("packh_seed_forgery_refused", refused))

    # ---- Pack H: generation inside the seed seal (v1 and v2) ----
    with tempfile.TemporaryDirectory() as d:
        groot = os.path.join(d, "store")
        gkr = os.path.join(d, "keyring.json")
        create_keyring(gkr)
        gseed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(gseed)
        outcomes = []
        for kr in (None, gkr):
            save_seed_state(groot, gseed, 7, keyring_path=kr)
            spath = os.path.join(groot, SEED_FILENAME)
            with open(spath, "rb") as handle:
                genv = json.loads(handle.read().decode("utf-8"))
            genv["generation"] = 9999
            _write_text(spath, json.dumps(genv, sort_keys=True,
                                          separators=(",", ":")))
            outcomes.append(_raises(
                SealMismatch,
                lambda: load_seed_state(groot, keyring_path=kr)))
        results.append(("packh_seed_generation_sealed",
                        outcomes == [True, True]))

    # ---- Pack H: keyed space seal — round trip, forgery, generation ----
    with tempfile.TemporaryDirectory() as d:
        sroot = os.path.join(d, "store")
        skr = os.path.join(d, "keyring.json")
        create_keyring(skr)
        sspace = pmca_graphspace.GraphSpace("own")
        saddr = (1, "A", 1)
        sspace.produce_graph(saddr, "own", "suite")
        sspace.place_entry(saddr, "note1", {"kind": "note", "harm": (1, 1)})
        save_analyzer_space(sroot, sspace, 4, keyring_path=skr)
        spath = os.path.join(sroot, SPACE_FILENAME)
        with open(spath, "rb") as handle:
            senv = json.loads(handle.read().decode("utf-8"))
        loaded_sspace = load_analyzer_space(sroot, keyring_path=skr)
        space_rt = (senv.get("seal_v") == 2
                    and loaded_sspace.canonical_bytes()
                    == sspace.canonical_bytes())
        space_needs_key = _raises(ArchiveLocked,
                                  lambda: load_analyzer_space(sroot))
        # Forgery: add an entry to the decoded structure, reseal
        # with the LEGACY bare SHA-256 (the audit's attack).
        forged_structure = decode_typed(senv["payload"])
        forged_structure["graphs"][0]["entries"]["injected"] = \
            {"kind": "note", "harm": (10, 10)}
        forged_space = _materialize_space(forged_structure)
        fenv = dict(senv)
        fenv["payload"] = encode_typed(forged_structure)
        fenv["checksum"] = hashlib.sha256(
            forged_space.canonical_bytes()).hexdigest()
        _write_text(spath, json.dumps(fenv, sort_keys=True,
                                      separators=(",", ":")))
        space_refused = _raises(
            SealMismatch,
            lambda: load_analyzer_space(sroot, keyring_path=skr))
        # With-key reseal of the same modified structure loads:
        # the protection is the key, not algorithm secrecy.
        material = (forged_space.canonical_bytes() + b"\n"
                    + str(fenv["generation"]).encode("utf-8"))
        _kid, _mk = _resolve_seal_key(skr, fenv["key_id"])
        fenv["checksum"] = hmac.new(
            _state_subkey(_mk, _kid, "analyzer_space"), material,
            hashlib.sha256).hexdigest()
        _write_text(spath, json.dumps(fenv, sort_keys=True,
                                      separators=(",", ":")))
        keyed_reseal_loads = (
            load_analyzer_space(sroot, keyring_path=skr).canonical_bytes()
            == forged_space.canonical_bytes())
        results.append(("packh_space_keyed_forgery",
                        (space_rt, space_needs_key, space_refused,
                         keyed_reseal_loads)))

    # ---- Pack H: generation inside the space seal (v1) ----
    with tempfile.TemporaryDirectory() as d:
        groot = os.path.join(d, "store")
        gspace = pmca_graphspace.GraphSpace("own")
        save_analyzer_space(groot, gspace, 7)
        spath = os.path.join(groot, SPACE_FILENAME)
        with open(spath, "rb") as handle:
            genv = json.loads(handle.read().decode("utf-8"))
        genv["generation"] = 9999
        _write_text(spath, json.dumps(genv, sort_keys=True,
                                      separators=(",", ":")))
        results.append(("packh_space_generation_sealed",
                        _raises(SealMismatch,
                                lambda: load_analyzer_space(groot))))

    # ---- Pack H: legacy (pre-Pack-H form) adoption, both files ----
    with tempfile.TemporaryDirectory() as d:
        aroot = os.path.join(d, "store")
        os.makedirs(aroot)
        aseed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(aseed)
        atb = {"event_counter": pmca_teaching.current_event_counter(),
               "audit_log": pmca_teaching.current_audit_log()}
        legacy_seed_env = {"format": FORMAT, "kind": "seed_dict",
                           "generation": 5, "pending": False,
                           "seal": _seed_seal(aseed, atb),
                           "payload": encode_typed(aseed),
                           "teaching": encode_typed(atb)}
        _atomic_write(os.path.join(aroot, SEED_FILENAME),
                      _envelope_bytes(legacy_seed_env))
        env_a, loaded_a, _atb = _load_seed_state_full(aroot)
        adopt_seed = (env_a.get("seal_state") == "adopted_unsealed"
                      and pmca_seed.canonical_seed_json(loaded_a)
                      == pmca_seed.canonical_seed_json(aseed))
        aspace = pmca_graphspace.GraphSpace("own")
        legacy_space_env = {"format": FORMAT, "kind": "analyzer_space",
                            "generation": 2,
                            "checksum": analyzer_space_checksum(aspace),
                            "payload": encode_typed(
                                _space_structure(aspace))}
        _atomic_write(os.path.join(aroot, SPACE_FILENAME),
                      _envelope_bytes(legacy_space_env))
        env_b, loaded_b = _load_analyzer_space_full(aroot)
        adopt_space = (env_b.get("seal_state") == "adopted_unsealed"
                       and loaded_b.canonical_bytes()
                       == aspace.canonical_bytes())
        results.append(("packh_legacy_adoption",
                        adopt_seed and adopt_space))

    # ---- Pack H: save repairs an unsealed seed (D25) ----
    with tempfile.TemporaryDirectory() as d:
        fresh = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        unsealed = fresh["metadata"]["seal"] == ""
        save_seed_state(d, fresh, 1)
        loaded_fresh, _fb = load_seed_state(d)
        results.append(("packh_save_repairs_unsealed_seed",
                        unsealed and pmca_seed.verify(loaded_fresh)))

    # ---- Pack H: archive tail truncation is detected ----
    with tempfile.TemporaryDirectory() as d:
        troot = os.path.join(d, "store")
        tkr = os.path.join(d, "keyring.json")
        create_keyring(tkr)
        for i in range(3):
            archive_append(troot, tkr, "lbl", {"m": i})
        apath = os.path.join(troot, ARCHIVE_FILENAME)
        with open(apath, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        _write_text(apath, "\n".join(lines[:-1]) + "\n")
        tail_held = _raises(ArchiveHeld,
                            lambda: archive_read_all(troot, tkr))
        results.append(("packh_archive_tail_truncation_held",
                        tail_held
                        and archive_status(troot, tkr) == "held"))

    # ---- Pack H: legacy archive adopts the head on next append ----
    with tempfile.TemporaryDirectory() as d:
        lroot = os.path.join(d, "store")
        lkr = os.path.join(d, "keyring.json")
        create_keyring(lkr)
        archive_append(lroot, lkr, "lbl", {"m": 1})
        archive_append(lroot, lkr, "lbl", {"m": 2})
        head_path = os.path.join(lroot, ARCHIVE_HEAD_FILENAME)
        head_exists = os.path.isfile(head_path)
        os.remove(head_path)  # simulate a pre-Pack-H archive
        legacy_reads = (archive_read_all(lroot, lkr)
                        == [("lbl", {"m": 1}), ("lbl", {"m": 2})])
        archive_append(lroot, lkr, "lbl", {"m": 3})
        adopted = (os.path.isfile(head_path)
                   and archive_read_all(lroot, lkr)
                   == [("lbl", {"m": 1}), ("lbl", {"m": 2}),
                       ("lbl", {"m": 3})]
                   and archive_status(lroot, lkr) == "verified")
        results.append(("packh_archive_legacy_adopts_head",
                        head_exists and legacy_reads and adopted))

    # ---- Pack H: keyed byte determinism across two roots ----
    with tempfile.TemporaryDirectory() as dx, \
            tempfile.TemporaryDirectory() as dy:
        dkr = os.path.join(dx, "keyring.json")
        create_keyring(dkr)
        det_hseed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(det_hseed)
        px = save_seed_state(os.path.join(dx, "s"), det_hseed, 9,
                             keyring_path=dkr)
        py = save_seed_state(os.path.join(dy, "s"), det_hseed, 9,
                             keyring_path=dkr)
        with open(px, "rb") as handle:
            bx = handle.read()
        with open(py, "rb") as handle:
            by = handle.read()
        results.append(("packh_keyed_byte_determinism", bx == by))

    # ---- Pack H: no fixed temp name remains after writes ----
    with tempfile.TemporaryDirectory() as d:
        troot = os.path.join(d, "store")
        tseed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(tseed)
        save_seed_state(troot, tseed, 1)
        save_seed_state(troot, tseed, 2)
        names = os.listdir(troot)
        results.append(("packh_no_fixed_temp",
                        (SEED_FILENAME + ".tmp") not in names
                        and not any(n.startswith(".pmca-tmp-")
                                    for n in names)))

    # ---- Pack H: boot reports seal states; keyed boot needs key ----
    with tempfile.TemporaryDirectory() as d:
        broot = os.path.join(d, "store")
        bcfg = os.path.join(d, "cfg.json")
        bkr = os.path.join(d, "keyring.json")
        _write_text(bcfg, json.dumps({"format": 1,
                                      "storage_root": broot}))
        create_keyring(bkr)
        bseed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
        pmca_seed.seal(bseed)
        save_seed_state(broot, bseed, 1, keyring_path=bkr)
        rep = boot(bcfg, keyring_path=bkr)
        boot_seal = rep["seed_seal"]
        shutdown(rep)
        boot_locked = _raises(ArchiveLocked, lambda: boot(bcfg))
        results.append(("packh_boot_seal_states",
                        (boot_seal, boot_locked)))

    return results


def check_suite(results):
    """Check results against expected values. Return list of
    (name, passed, actual, want) tuples."""
    expected = {
        "config_missing_file": True,
        "config_malformed_json": True,
        "config_unknown_key": True,
        "config_url_root_rejected": True,
        "config_root_required": True,
        "config_bad_format": True,
        "config_relative_resolution": True,
        "codec_roundtrip_nested": True,
        "codec_reserved_key_sole": True,
        "codec_reserved_key_mixed": True,
        "codec_int_key_raises": True,
        "codec_float_raises": True,
        "lock_cycle": True,
        "seed_pending_roundtrip": True,
        "seed_approve_roundtrip": True,
        "tamper_payload_seal_mismatch": True,
        "tamper_teaching_seal_mismatch": True,
        "class_confusion_both_directions": True,
        "format_too_new": True,
        "analyzer_roundtrip": ("HOLD", "APPROVE", "APPROVE", True, True, True),
        "analyzer_fidelity_glue_tuples_edges": True,
        "previous_generation_manual_restore": True,
        "byte_determinism_two_roots": True,
        "boot_fresh_then_restored": ("fresh", "fresh", True, True,
                                     "restored", "restored", 1, 1, True),
        "archive_kat": True,
        "archive_nonce_audit": True,
        "archive_rotation": True,
        "archive_absent_key": (True, True, "locked"),
        "archive_tamper_ct_boot_held": (True, True, "held", "held",
                                        "restored"),
        "archive_tamper_tag_held": True,
        "archive_truncated_held": True,
        "archive_key_separation": True,
        "archive_boot_states": ("absent", "locked", "verified"),
        "packh_seed_keyed_roundtrip": True,
        "packh_seed_forgery_refused": True,
        "packh_seed_generation_sealed": True,
        "packh_space_keyed_forgery": (True, True, True, True),
        "packh_space_generation_sealed": True,
        "packh_legacy_adoption": True,
        "packh_save_repairs_unsealed_seed": True,
        "packh_archive_tail_truncation_held": True,
        "packh_archive_legacy_adopts_head": True,
        "packh_keyed_byte_determinism": True,
        "packh_no_fixed_temp": True,
        "packh_boot_seal_states": ("keyed", True),
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
            passed_count += 1
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
