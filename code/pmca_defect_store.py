"""pmca_defect_store — the defect store: the self-fix memory
(Spec of record: pmca-self-fix-spec-2026-10-31.md, section 3;
build WG3, 04 Forge, 2026-11-01).

Architecture and design: Jonathan Wayne Fleuren (the builder) —
the self-fix ruling 2026-10-31 and the certification principle
2026-11-01. Code implemented by Alder's hive (04 Forge) per the
spec and its three Protogen derivations (aggregation pass,
outcome records, pre/post hashes), stated in the WG3 build
orders.

WHAT THIS MODULE IS: the stored residence for

  - FAILURE records (spec section 3): id; the failing process's
    entry reference; pattern (structural signature: process,
    slot/gap kind, missing item, rule that fired); mechanism;
    cause as determined; every attempt in order with outcome;
    status (open / closed-by-remedy / closed-by-introduction /
    never-class-flagged).
  - REMEDY records (spec section 3): id; the exact signature it
    answers; deterministic repair operations over stored
    structure only; provenance (who taught it, or the verified
    builder fix it derives from, with that fix's claim id); the
    replay set it must pass; scope limits (never-class surfaces
    are UNLISTABLE here by construction — registration refuses
    them); status (candidate / verified / suspended).
  - OUTCOME records (Derivation 2): an external response to a
    sealed output, stored by reference to the output's atomic
    entry; responder is provenance; the response is verbatim;
    any pattern classification is marked as classification, not
    fact. An outcome record asserting failure may OPEN a failure
    record — provenance-grade only; it never unseals, amends, or
    outweighs the sealed entry, and never counts as a witness.
  - PREREQUISITE-GAP records (Derivation 1, section 4A): written
    by the aggregation pass when >= 2 open failures with
    different signatures name the same missing item. CANDIDATE
    cause, linked to every member; applies no repair, closes
    nothing.
  - CERTIFIED repair records (certification ONCE, 2026-11-01):
    a repair certified by cross-check replay stands on its
    sealed record permanently and is never re-adjudicated; the
    NC-1 carve-out (across a calculus generation change the code
    identity is checked and the check stops there — the old
    certification is neither re-run nor revoked) is enforced by
    pmca_self_fix reading this registry.
  - The section 4A CONFIGURATION: the struggle threshold lives
    here as explicit stored configuration marked PROPOSED
    (Jonathan has not ruled a number). The threshold and the
    "struggled" marks it produces affect worklist ORDER only —
    never a verdict input. Nothing in this module computes a
    verdict, and no verdict path reads this store's marks.

THE NEVER-CLASS (spec section 2, JONATHAN's standing boundary):
the verdict machinery itself is never self-fixable — provenance
counting and class computation (D-2/D-3 territory; WG1's
pmca_lineage_table counters are never-class here regardless of
spelling), promotion gates and thresholds, the locks (omega /
infinity class), the child protections, the harm calculus, the
gate, the dictionaries' attestation grades, and this loop's own
matching / replay / sealing machinery. is_never_class() is the
one deterministic check; the self-fix loop calls it BEFORE any
remedy matching (spec section 4 step 3), and remedy
registration refuses a never-class signature or scope outright.

PERSISTENCE: one JSON file (defect_store.json) in the store
directory, written atomically (the pmca_persistence _atomic_write
discipline: temp file, flush, fsync, rename, directory fsync).
SINGLE WRITER (ATOM-2 pattern, as WG1 reused it): every save
runs under pmca_persistence.acquire_lock / release_lock on the
store directory — O_CREAT|O_EXCL, loud WriterLockHeld, never
auto-broken. No second locking style is introduced.

Deterministic. Standard library only. No floats. This module
imports pmca_persistence for the lock + atomic write only; it
imports no verdict, teaching, or counting module.

WG3b FIX-PASS (2026-11-01, paired check wg3-forge-check.md):
the fences below close the check's nine confirmed breaches.
(1) Never-class determination is STRUCTURAL/CANONICAL:
surface identifiers are canonicalized (abbreviations,
synonyms and digit-leet folded to canonical tokens) before
any token rule fires, so respellings (counter_of_classes,
prov_counter, provenance tally, prom0tion_gate) classify
with their canonical names. (2) Remedy OPERATIONS are
validated by what their target IS: classify_operation_target
classifies a target entry/field by identity — any state a
provenance counter, the gate, the calculus, the locks, or an
evidence log/store/registry reads or writes is protected and
unlistable; a target that cannot be positively classified is
refused, never permitted. (3) Store doors to VERIFIED /
CLOSED are RESOLUTION-BY-REFERENCE only: the caller must
present a sealed atomic-entry reference (entry id + content
hash + atomic root) whose content hash verifies against the
atomic store; a direct status write is refused, the refusal
is RECORDED in the store's door_refusals log, and the record
is left unchanged. (4) Every remedy record carries the
content hash of its operations, computed at registration —
a later mismatch is rot. (5) Certified records carry the
certification identity (signature key + operations hash +
governed lineage set) and the seal's code-manifest hash, so
certification ONCE and the NC-1 code-identity check compare
hashes and lineages, not spellings or labels.
"""

import copy
import hashlib
import json
import os

import pmca_lineage_table
import pmca_persistence

STORE_FILENAME = "defect_store.json"
STORE_FORMAT = 1

FAILURE_OPEN = "open"
FAILURE_CLOSED_BY_REMEDY = "closed_by_remedy"
FAILURE_CLOSED_BY_INTRODUCTION = "closed_by_introduction"
FAILURE_NEVER_CLASS = "never_class_flagged"

REMEDY_CANDIDATE = "candidate"
REMEDY_VERIFIED = "verified"
REMEDY_SUSPENDED = "suspended"

# The never-class, as data (spec section 2). A signature whose
# process normalizes to one of these names is never-class.
NEVER_CLASS_PROCESSES = (
    "provenance_counting",
    "provenance_counter",
    "provenance_class_computation",
    "lineage_counting",
    "lineage_class_computation",
    "class_computation",
    "promotion_gate",
    "promotion_threshold",
    "promotion_gates",
    "candidacy_gate",
    "isa_promotion",
    "omega_lock",
    "infinity_lock",
    "child_protection",
    "child_protections",
    "harm_calculus",
    "verdict_gate",
    "gate",
    "dictionary_attestation_grade",
    "attestation_grade",
    "self_fix_matching",
    "self_fix_replay",
    "self_fix_sealing",
)

# Surfaces a remedy may never list in its scope (spec section 3:
# unlistable by construction).
NEVER_CLASS_SCOPE_SURFACES = frozenset({
    "provenance_counting", "provenance_counter",
    "lineage_counting", "class_computation",
    "promotion_gate", "promotion_threshold", "candidacy_gate",
    "isa_promotion", "omega_lock", "infinity_lock",
    "child_protection", "harm_calculus", "verdict_gate", "gate",
    "dictionary_attestation_grade", "attestation_grade",
    "self_fix_matching", "self_fix_replay", "self_fix_sealing",
    "self_fix",
})

# ------------------------------------------------------------------
# Canonical tokenization (WG3b). A surface identifier is
# canonicalized BEFORE any classification: abbreviations fold
# to their full form (prov -> provenance), synonyms fold to the
# concept they name (tally / counting / count -> counter;
# classes -> class), and digit-leet in a mixed alphanumeric
# token folds to letters (prom0tion -> promotion, 0mega ->
# omega). Two spellings with the same canonical tokens are the
# same surface, regardless of how either is spelled — the same
# stated rule WG1's governed table applies to source spellings
# (a spelling is resolved to its class before it is counted);
# the lineage table itself maps SOURCE spellings and does not
# canonicalize identifiers, so the identifier rule is stated
# and implemented here (duplication recorded for the attack
# phase in the WG3b build paper).
_LEET_FOLD = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})
_TOKEN_SYNONYMS = {
    "prov": "provenance", "provs": "provenance",
    "tally": "counter", "tallies": "counter",
    "counting": "counter", "counts": "counter",
    "count": "counter", "counters": "counter",
    "classes": "class", "grades": "grade",
    "protections": "protection", "gates": "gate",
    "locks": "lock", "calculi": "calculus",
}


def _canonical_token(token):
    tok = str(token).lower()
    if any(ch.isdigit() for ch in tok) and \
            any(ch.isalpha() for ch in tok):
        tok = tok.translate(_LEET_FOLD)
    return _TOKEN_SYNONYMS.get(tok, tok)


def _token_list(text):
    out = []
    cur = []
    for ch in str(text).lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def canonical_tokens(text):
    """The canonical token SET of a surface identifier."""
    return {_canonical_token(t) for t in _token_list(text)}


def canonical_key(text):
    """The canonical key of a surface identifier: its
    canonical tokens in order, underscore-joined."""
    return "_".join(_canonical_token(t) for t in _token_list(text))


# Token rules (deterministic, over the signature's CANONICAL
# tokens): each entry is (required token set, reason). Any one
# match makes the signature never-class.
_NEVER_TOKEN_RULES = (
    (frozenset({"provenance", "counter"}),
     "provenance counter (never-class, spec section 2)"),
    (frozenset({"provenance", "class"}),
     "provenance class computation (never-class, spec section 2)"),
    (frozenset({"lineage", "counter"}),
     "lineage counter (never-class: provenance class computation)"),
    (frozenset({"lineage", "class"}),
     "lineage class computation (never-class, spec section 2)"),
    (frozenset({"counter", "class"}),
     "counter of classes (never-class: provenance class "
     "computation, whatever the surface is named)"),
    (frozenset({"promotion"}),
     "promotion gate/threshold (never-class, spec section 2)"),
    (frozenset({"omega"}),
     "omega lock (never-class, spec section 2)"),
    (frozenset({"infinity"}),
     "infinity lock (never-class, spec section 2)"),
    (frozenset({"child", "protection"}),
     "child protections (never-class, spec section 2)"),
    (frozenset({"harm", "calculus"}),
     "harm calculus (never-class, spec section 2)"),
    (frozenset({"attestation", "grade"}),
     "dictionary attestation grade (never-class, spec section 2)"),
)

_NEVER_PROCESS_KEYS = frozenset(
    {canonical_key(p) for p in NEVER_CLASS_PROCESSES}
    | set(NEVER_CLASS_PROCESSES))
_NEVER_SCOPE_KEYS = frozenset(
    {canonical_key(s) for s in NEVER_CLASS_SCOPE_SURFACES}
    | set(NEVER_CLASS_SCOPE_SURFACES))

# Operation-target identity (WG3b): single canonical tokens
# that mark a target as machinery/evidence state — state a
# provenance counter, the gate, the calculus, or the locks
# read or write — plus tokens that mark a store/door/surface
# identity (a log, a store, a registry: evidence residences,
# never content a repair may write).
_PROTECTED_TARGET_TOKENS = frozenset({
    "provenance", "lineage", "counter", "attestation",
    "promotion", "omega", "infinity", "calculus", "gate",
    "lock", "verdict", "certification", "certified", "seal",
    "registry",
})
_SURFACE_TARGET_TOKENS = frozenset({"log", "store"})


def classify_operation_target(entry, field=None):
    """Classify one remedy operation's target BY WHAT IT IS.

    Returns ("content", None) only when the target is
    positively classifiable as plain content; ("protected",
    reason) when its canonical tokens name machinery or
    evidence state (a provenance/lineage counter, an
    attestation residence, the gate, the calculus, a lock,
    the promotion machinery, the self-fix machinery itself);
    ("surface", reason) when it names a store/log surface
    identity; ("unclassifiable", reason) when the target
    cannot be positively classified at all. Anything other
    than "content" is refused — HOLD-on-unclassifiable, never
    permitted by default."""
    if not isinstance(entry, str) or not entry.strip():
        return ("unclassifiable",
                "operation target entry is missing or not a "
                "name: %r" % (entry,))
    entry_tokens = canonical_tokens(entry)
    field_tokens = canonical_tokens(field) if field else set()
    tokens = entry_tokens | field_tokens
    hits = tokens & _PROTECTED_TARGET_TOKENS
    if hits:
        return ("protected",
                "target %r is machinery/evidence state "
                "(identity tokens: %s) — state a provenance "
                "counter, the gate, the calculus, or the locks "
                "read or write is never a repair target"
                % (entry, sorted(hits)))
    if {"child", "protection"} <= tokens:
        return ("protected",
                "target %r is child-protection state" % (entry,))
    if {"self", "fix"} <= entry_tokens:
        return ("protected",
                "target %r is the self-fix machinery itself"
                % (entry,))
    surface_hits = entry_tokens & _SURFACE_TARGET_TOKENS
    if surface_hits:
        return ("surface",
                "target %r is a store/log surface identity "
                "(%s) — an evidence residence, not content"
                % (entry, sorted(surface_hits)))
    return ("content", None)


def validate_operations(operations):
    """Validate a remedy's operations at registration: every
    operation must be a well-formed content operation
    (fill/link/derive with entry+field) whose target
    positively classifies as content. Raises NeverClassRefused
    for a protected/surface target, DefectStoreError for a
    malformed or unclassifiable one."""
    for op in operations or []:
        if not isinstance(op, dict):
            raise DefectStoreError(
                "remedy operation is not a record: %r" % (op,))
        if op.get("op") not in ("fill", "link", "derive"):
            raise DefectStoreError(
                "remedy operation kind is not a content "
                "operation: %r" % (op.get("op"),))
        if op.get("entry") is None or op.get("field") is None:
            raise DefectStoreError(
                "remedy operation lacks entry/field: %r" % (op,))
        cls, reason = classify_operation_target(
            op.get("entry"), op.get("field"))
        if cls in ("protected", "surface"):
            raise NeverClassRefused(
                "remedy operation target refused: %s" % reason)
        if cls != "content":
            raise DefectStoreError(
                "remedy operation target unclassifiable "
                "(HOLD-on-unclassifiable): %s" % reason)
    return True


def provenance_lineage_set(provenance):
    """The governed lineage set of a remedy provenance: the
    lineage classes (pmca_lineage_table) of its teacher /
    source spellings. 'Webster' and 'Webster 1913' are ONE
    entry in this set — lineage identity never follows
    spelling."""
    prov = provenance or {}
    lineages = set()
    for key in ("teacher", "source", "lineage",
                "certifying_source", "certification_source"):
        cls = pmca_lineage_table.lineage_class(prov.get(key))
        if cls is not None:
            lineages.add(cls)
    return sorted(lineages)


def certification_identity(sig_key, operations, provenance):
    """The certification identity of a repair (WG3b):
    (failure signature, canonical repair-content hash,
    lineage set via the governed table). A resubmission under
    any new remedy ID or respelled source with this identity
    IS the same certification."""
    return {
        "signature_key": sig_key,
        "operations_hash": content_hash(list(operations or [])),
        "lineage_set": provenance_lineage_set(provenance),
    }


def certification_identity_key(sig_key, operations, provenance):
    return content_hash(
        certification_identity(sig_key, operations, provenance))


def canonical(obj):
    """Canonical JSON text: sorted keys, tight separators —
    the one serialization every hash in this build is over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def content_hash(obj):
    """SHA-256 over the canonical bytes of obj."""
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def _tokens(text):
    out = set()
    cur = []
    for ch in str(text).lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.add("".join(cur))
            cur = []
    if cur:
        out.add("".join(cur))
    return out


def normalize_signature(signature):
    """The structural signature of a failure or remedy (spec
    section 3): process, gap kind (slot/gap kind; 'slot_kind' is
    accepted as the same field), missing item, rule that fired.
    Extra keys are dropped: matching is over these four fields
    and nothing else, so two signatures are equal exactly when
    these four are equal."""
    if not isinstance(signature, dict):
        raise ValueError("signature must be a dict")
    gap_kind = signature.get("gap_kind")
    if gap_kind is None:
        gap_kind = signature.get("slot_kind")
    return {
        "process": str(signature.get("process") or ""),
        "gap_kind": str(gap_kind or ""),
        "missing_item": str(signature.get("missing_item") or ""),
        "rule_fired": str(signature.get("rule_fired") or ""),
    }


def signature_key(signature):
    """The exact-match key of a signature: canonical JSON of
    its normalized form."""
    return canonical(normalize_signature(signature))


def is_never_class(signature):
    """(True, reason) when the signature belongs to the
    never-class (spec section 2), else (False, None). Checked
    by the loop BEFORE remedy matching (section 4 step 3), and
    by remedy registration. A signature naming a surface in
    NEVER_CLASS_SCOPE_SURFACES is likewise never-class."""
    sig = normalize_signature(signature)
    # The process name is compared CANONICALLY (WG3b): the
    # same surface under any respelling — abbreviation,
    # synonym, or digit-leet — has the same canonical key.
    if canonical_key(sig["process"]) in _NEVER_PROCESS_KEYS:
        return True, "process %r is never-class (spec section 2)" \
            % (sig["process"],)
    surface = signature.get("surface") if isinstance(
        signature, dict) else None
    if surface is not None and canonical_key(surface) in \
            _NEVER_SCOPE_KEYS:
        return True, "surface %r is never-class (spec section 2)" \
            % (surface,)
    tokens = set()
    for field in ("process", "gap_kind", "missing_item",
                  "rule_fired"):
        tokens |= canonical_tokens(sig[field])
    for required, reason in _NEVER_TOKEN_RULES:
        if required <= tokens:
            return True, reason
    return False, None


def signatures_near(sig_a, sig_b):
    """Near-match (spec section 4 step 2): NOT exact-equal, but
    sharing the missing item and at least one of process / gap
    kind. A near-match may be named as a CANDIDATE cause; it
    never licenses a repair."""
    a = normalize_signature(sig_a)
    b = normalize_signature(sig_b)
    if signature_key(a) == signature_key(b):
        return False
    if not a["missing_item"] or a["missing_item"] != \
            b["missing_item"]:
        return False
    return a["process"] == b["process"] or \
        a["gap_kind"] == b["gap_kind"]


class DefectStoreError(Exception):
    """Base class for defect-store refusals. All loud."""


class NeverClassRefused(DefectStoreError):
    """A remedy registration named a never-class signature or
    a never-class scope surface. Refused by construction."""


class DefectStore:
    """The defect store, rooted at one directory. Load on open;
    every mutating method saves atomically under the single-
    writer lock before returning."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.path = os.path.join(self.root, STORE_FILENAME)
        self._data = self._load()

    # -- persistence ------------------------------------------------

    def _load(self):
        if not os.path.exists(self.path):
            return self._empty()
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or \
                data.get("format") != STORE_FORMAT:
            raise DefectStoreError(
                "defect store format not supported: %s" % self.path)
        data.setdefault("door_refusals", [])
        return data

    @staticmethod
    def _empty():
        return {
            "format": STORE_FORMAT,
            "counters": {"failure": 0, "remedy": 0, "outcome": 0,
                         "prereq": 0},
            "config": {
                # Section 4A: the struggle threshold lives here
                # as explicit stored configuration, marked
                # PROPOSED — Jonathan has not ruled a number.
                "struggle_threshold": {
                    "value": 3,
                    "status": "PROPOSED",
                    "ruled_by": None,
                    "note": "affects worklist ORDER only; "
                            "never a verdict input",
                },
            },
            "failures": [],
            "remedies": [],
            "outcomes": [],
            "prereq_gaps": [],
            "certified": [],
            "door_refusals": [],
        }

    def _save(self):
        """One atomic write under the ATOM-2 single-writer lock
        (pmca_persistence.acquire_lock — the tree's lock, reused
        as WG1 reused it). Loud WriterLockHeld when held."""
        lock_path = pmca_persistence.acquire_lock(self.root)
        try:
            payload = canonical(self._data).encode("utf-8")
            pmca_persistence._atomic_write(self.path, payload)
        finally:
            pmca_persistence.release_lock(lock_path)

    def save(self):
        """Public save (the lock test's door)."""
        self._save()

    # -- id allocation ----------------------------------------------

    def _next_id(self, kind, prefix):
        counters = self._data["counters"]
        counters[kind] += 1
        return "%s-%06d" % (prefix, counters[kind])

    # -- read views ---------------------------------------------------

    @property
    def config(self):
        return self._data["config"]

    def failures(self):
        return list(self._data["failures"])

    def remedies(self):
        return list(self._data["remedies"])

    def outcomes(self):
        return list(self._data["outcomes"])

    def prereq_gaps(self):
        return list(self._data["prereq_gaps"])

    def certified(self):
        return list(self._data["certified"])

    def door_refusals(self):
        """Every refused direct door write, in order (WG3b:
        a refused status transition is recorded, never
        silent)."""
        return list(self._data.get("door_refusals") or [])

    def find_certified_by_identity(self, identity_key):
        """Certified records carrying one certification
        identity key (WG3b) — the resubmission fence's lookup,
        by identity, never by remedy id or spelling."""
        return [r for r in self._data["certified"]
                if r.get("certification_identity_key")
                == identity_key]

    def get_failure(self, failure_id):
        for rec in self._data["failures"]:
            if rec["failure_id"] == failure_id:
                return rec
        return None

    def get_remedy(self, remedy_id):
        for rec in self._data["remedies"]:
            if rec["remedy_id"] == remedy_id:
                return rec
        return None

    def get_outcome(self, outcome_id):
        for rec in self._data["outcomes"]:
            if rec["outcome_id"] == outcome_id:
                return rec
        return None

    def open_failures(self):
        return [r for r in self._data["failures"]
                if r["status"] == FAILURE_OPEN]

    def failures_by_signature(self, sig_key):
        return [r for r in self._data["failures"]
                if r["signature_key"] == sig_key]

    def remedies_for_signature(self, sig_key):
        """Remedy records whose signature is an EXACT match."""
        return [r for r in self._data["remedies"]
                if r["signature_key"] == sig_key]

    def find_certified(self, remedy_id=None, sig_key=None,
                       failure_id=None):
        """Certified repair records matching every given key."""
        out = []
        for rec in self._data["certified"]:
            if remedy_id is not None and \
                    rec["remedy_id"] != remedy_id:
                continue
            if sig_key is not None and \
                    rec["signature_key"] != sig_key:
                continue
            if failure_id is not None and \
                    rec["failure_id"] != failure_id:
                continue
            out.append(rec)
        return out

    # -- resolution-by-reference doors (WG3b) -------------------------

    def _record_door_refusal(self, door, target_id, attempted,
                             reason):
        """A refused direct door write is LOUD and RECORDED:
        the refusal is appended to the store's door_refusals
        log and saved; the target record is left unchanged.
        Returns the refusal record."""
        refusal = {"door": door, "target_id": target_id,
                   "attempted": attempted, "reason": reason,
                   "refused": True}
        self._data.setdefault("door_refusals", []).append(refusal)
        self._save()
        return refusal

    def _refused_copy(self, rec, refusal):
        out = copy.deepcopy(rec)
        out["door_refused"] = True
        out["door_refusal"] = refusal
        return out

    def _verify_seal_proof(self, seal_proof, failure_id=None,
                           operations=None, sig_key=None):
        """Verify a sealed atomic-entry reference against the
        atomic store: (True, entry) iff seal_proof names an
        entry (entry_id + content_hash + atomic_root) that
        exists in that atomic store, whose content hash
        recomputes to the presented hash (the store's own
        chain/seal verification runs on open), which is a
        self-fix seal, and which matches every given binding
        (failure id, operations content, signature key).
        Else (False, reason)."""
        if not isinstance(seal_proof, dict):
            return False, "no sealed atomic-entry reference " \
                "presented (resolution-by-reference required)"
        entry_id = seal_proof.get("entry_id")
        presented = seal_proof.get("content_hash")
        atomic_root = seal_proof.get("atomic_root")
        if not entry_id or not presented or not atomic_root:
            return False, "sealed reference is incomplete " \
                "(entry_id, content_hash, atomic_root all " \
                "required): %r" % (seal_proof,)
        import pmca_atomic_entries as ae
        try:
            astore = ae.EntryStore.open(atomic_root,
                                        read_only=True)
        except Exception as exc:
            return False, "atomic store at %r does not open: " \
                "%s: %s" % (atomic_root, type(exc).__name__, exc)
        try:
            entries = astore.replay()
        finally:
            astore.close()
        entry = None
        for candidate in entries:
            if candidate.get("entry_id") == entry_id:
                entry = candidate
                break
        if entry is None:
            return False, "no sealed entry %r in the atomic " \
                "store at %r" % (entry_id, atomic_root)
        if content_hash(entry) != presented:
            return False, "sealed entry %r content hash does " \
                "not verify against the atomic store" % (entry_id,)
        inp = entry.get("input") or {}
        if inp.get("kind") != "self_fix":
            return False, "sealed entry %r is not a self-fix " \
                "seal" % (entry_id,)
        if failure_id is not None and \
                inp.get("failure_id") != failure_id:
            return False, "sealed entry %r seals failure %r, " \
                "not %r" % (entry_id, inp.get("failure_id"),
                            failure_id)
        if operations is not None and content_hash(
                inp.get("operations") or []) != content_hash(
                list(operations or [])):
            return False, "sealed entry %r seals different " \
                "repair operations" % (entry_id,)
        if sig_key is not None and signature_key(
                inp.get("signature") or {}) != sig_key:
            return False, "sealed entry %r seals a different " \
                "signature" % (entry_id,)
        return True, entry

    # -- failure records (spec section 3 + section 4 step 1) ---------

    def add_failure(self, entry_ref, signature, mechanism,
                    cause=None, case_name=None, opened_by=None):
        """Write a failure record (no silent gaps: the record
        precedes any repair attempt, spec section 4 step 1).
        cause defaults to the recorded-gap cause; an outcome-
        opened failure carries its provenance-grade cause from
        the caller (Derivation 2)."""
        sig = normalize_signature(signature)
        rec = {
            "failure_id": self._next_id("failure", "FAIL"),
            "entry_ref": entry_ref,
            "signature": sig,
            "signature_key": signature_key(sig),
            "pattern": signature_key(sig),
            "mechanism": mechanism,
            "cause": cause or {
                "kind": "recorded_gap", "detail": None,
                "grade": "determined"},
            "candidate_causes": [],
            "attempts": [],
            "conflicts": [],
            "status": FAILURE_OPEN,
            "closure": None,
            "closure_review_flag": None,
            "case_name": case_name,
            "opened_by": opened_by,
            "worklist_marks": {"struggled": False},
        }
        self._data["failures"].append(rec)
        self._save()
        return rec

    def append_attempt(self, failure_id, attempt):
        rec = self.get_failure(failure_id)
        if rec is None:
            raise DefectStoreError("no failure %r" % (failure_id,))
        attempt = dict(attempt)
        attempt["attempt_no"] = len(rec["attempts"]) + 1
        rec["attempts"].append(attempt)
        self._save()
        return rec

    def set_failure_status(self, failure_id, status, closure=None,
                           seal_proof=None):
        """The failure-status door (WG3b). A transition to
        CLOSED_BY_REMEDY is resolution-by-reference ONLY: it
        is applied iff seal_proof verifies against the atomic
        store for THIS failure. A direct write without a
        verifying seal — or a direct write to
        CLOSED_BY_INTRODUCTION, whose named door is
        close_by_introduction — is refused: the refusal is
        recorded in door_refusals, the record is unchanged,
        and a copy of the record carrying the refusal is
        returned. NEVER_CLASS flagging (the loop's step-3
        record) remains a direct write: it grants nothing."""
        rec = self.get_failure(failure_id)
        if rec is None:
            raise DefectStoreError("no failure %r" % (failure_id,))
        if status == FAILURE_CLOSED_BY_REMEDY:
            ok, entry = self._verify_seal_proof(
                seal_proof, failure_id=failure_id)
            if not ok:
                refusal = self._record_door_refusal(
                    "set_failure_status", failure_id,
                    {"status": status, "closure": closure}, entry)
                return self._refused_copy(rec, refusal)
            rec["status"] = status
            merged = {"remedy_id": entry["input"].get("remedy_id"),
                      "seal_entry_id": entry["entry_id"],
                      "basis": "replay_proof"}
            if closure:
                merged.update(closure)
            merged["seal_entry_id"] = entry["entry_id"]
            rec["closure"] = merged
            self._save()
            return rec
        if status == FAILURE_NEVER_CLASS:
            rec["status"] = status
            if closure is not None:
                rec["closure"] = closure
            self._save()
            return rec
        refusal = self._record_door_refusal(
            "set_failure_status", failure_id, {"status": status},
            "direct status transition to %r is refused: "
            "closure is resolution-by-reference only (a "
            "verifying sealed atomic-entry reference, or the "
            "named close_by_introduction door)" % (status,))
        return self._refused_copy(rec, refusal)

    def close_by_introduction(self, failure_id, introduction_ref):
        """Closed-by-introduction (spec section 3 status): a
        later introduction closed the gap outside the loop.
        This is the NAMED door for that transition and it is
        itself by-reference: it refuses an empty reference."""
        if not introduction_ref or not isinstance(
                introduction_ref, str):
            raise DefectStoreError(
                "close_by_introduction requires an introduction "
                "reference, got %r" % (introduction_ref,))
        rec = self.get_failure(failure_id)
        if rec is None:
            raise DefectStoreError("no failure %r" % (failure_id,))
        rec["status"] = FAILURE_CLOSED_BY_INTRODUCTION
        rec["closure"] = {"kind": "introduction",
                          "ref": introduction_ref}
        self._save()
        return rec

    def add_candidate_cause(self, failure_id, cause):
        rec = self.get_failure(failure_id)
        if rec is None:
            raise DefectStoreError("no failure %r" % (failure_id,))
        if cause not in rec["candidate_causes"]:
            rec["candidate_causes"].append(cause)
            self._save()
        return rec

    def add_conflict(self, failure_id, conflict):
        rec = self.get_failure(failure_id)
        if rec is None:
            raise DefectStoreError("no failure %r" % (failure_id,))
        rec["conflicts"].append(conflict)
        self._save()
        return rec

    # -- remedy records (spec sections 3, 5, 6) -----------------------

    def add_remedy(self, signature, operations, provenance,
                   replay_set=None, scope_surfaces=None,
                   derivation=None):
        """Register a remedy as CANDIDATE. Registration refuses
        a never-class signature and any never-class scope
        surface (unlistable by construction, spec section 3),
        and refuses a derivation wider than its parent (spec
        section 6, scope creep)."""
        sig = normalize_signature(signature)
        never, reason = is_never_class(sig)
        if never:
            raise NeverClassRefused(
                "remedy signature is never-class: %s" % reason)
        scope = list(scope_surfaces or [])
        for surface in scope:
            if canonical_key(surface) in _NEVER_SCOPE_KEYS:
                raise NeverClassRefused(
                    "remedy scope names a never-class surface: "
                    "%r" % (surface,))
        # WG3b: the operations themselves are validated by
        # what their targets ARE — a remedy may not touch
        # machinery/evidence state indirectly through a
        # teachable signature, and an unclassifiable target
        # is refused, not permitted.
        validate_operations(operations)
        if derivation is not None:
            parent = self.get_remedy(
                derivation.get("parent_remedy_id"))
            if parent is None:
                raise DefectStoreError(
                    "derivation names unknown parent remedy %r"
                    % (derivation.get("parent_remedy_id"),))
            if not set(scope) <= set(parent["scope_surfaces"]):
                raise DefectStoreError(
                    "derived remedy scope is wider than its "
                    "parent's (scope creep, spec section 6): "
                    "%r not within %r"
                    % (scope, parent["scope_surfaces"]))
        rec = {
            "remedy_id": self._next_id("remedy", "REM"),
            "signature": sig,
            "signature_key": signature_key(sig),
            "operations": list(operations or []),
            # WG3b: the operations hash is computed HERE, at
            # registration, and carried by the record forever;
            # any later mismatch between the record's
            # operations and this hash is rot (see check_rot
            # and the loop's pre-apply re-check).
            "operations_hash": content_hash(
                list(operations or [])),
            "lineage_set": provenance_lineage_set(provenance),
            "provenance": dict(provenance or {}),
            "replay_set": dict(replay_set or {}),
            "scope_surfaces": scope,
            "derivation": derivation,
            "status": REMEDY_CANDIDATE,
            "suspended": False,
            "suspension": None,
            "attempt_history": [],
            "closures": [],
            "rot": None,
        }
        self._data["remedies"].append(rec)
        self._save()
        return rec

    def set_remedy_status(self, remedy_id, status, suspension=None,
                          seal_proof=None):
        """The remedy-status door (WG3b). A transition to
        VERIFIED is resolution-by-reference ONLY: it is
        applied iff seal_proof verifies against the atomic
        store for THIS repair (same operations content, same
        signature) AND the remedy carries a non-empty replay
        battery (verification requires a battery — a
        battery-less remedy has nothing to stand on).
        Downgrades — SUSPENDED, CANDIDATE — grant no standing
        and remain direct writes. A refused verification is
        recorded in door_refusals and changes nothing."""
        rec = self.get_remedy(remedy_id)
        if rec is None:
            raise DefectStoreError("no remedy %r" % (remedy_id,))
        if status == REMEDY_VERIFIED:
            battery = (rec.get("replay_set") or {}).get(
                "battery_cases") or []
            if not battery:
                refusal = self._record_door_refusal(
                    "set_remedy_status", remedy_id,
                    {"status": status},
                    "verification requires a non-empty replay "
                    "battery; this remedy's replay set is empty")
                return self._refused_copy(rec, refusal)
            ok, entry = self._verify_seal_proof(
                seal_proof, operations=rec["operations"],
                sig_key=rec["signature_key"])
            if not ok:
                refusal = self._record_door_refusal(
                    "set_remedy_status", remedy_id,
                    {"status": status}, entry)
                return self._refused_copy(rec, refusal)
            rec["status"] = status
            rec["suspended"] = False
            if suspension is not None:
                rec["suspension"] = suspension
            self._save()
            return rec
        if status not in (REMEDY_SUSPENDED, REMEDY_CANDIDATE):
            refusal = self._record_door_refusal(
                "set_remedy_status", remedy_id, {"status": status},
                "unknown remedy status transition %r refused"
                % (status,))
            return self._refused_copy(rec, refusal)
        rec["status"] = status
        if status == REMEDY_SUSPENDED:
            rec["suspended"] = True
            rec["suspension"] = suspension
        elif suspension is not None:
            rec["suspension"] = suspension
        self._save()
        return rec

    def suspend_remedy(self, remedy_id, to_status, suspension):
        """Suspend a remedy (spec section 6): the suspended
        flag is set WHATEVER the landing status — a remedy
        suspended to CANDIDATE after 2 failed attempts (its
        landing status) is as un-applicable as one suspended
        outright, and the loop's third-attempt fence reads
        this flag."""
        rec = self.get_remedy(remedy_id)
        if rec is None:
            raise DefectStoreError("no remedy %r" % (remedy_id,))
        rec["status"] = to_status
        rec["suspended"] = True
        rec["suspension"] = suspension
        self._save()
        return rec

    def note_remedy_attempt(self, remedy_id, failure_id, outcome):
        rec = self.get_remedy(remedy_id)
        if rec is None:
            raise DefectStoreError("no remedy %r" % (remedy_id,))
        rec["attempt_history"].append({
            "failure_id": failure_id, "outcome": outcome})
        self._save()
        return rec

    def failed_attempts_on(self, remedy_id, failure_id):
        """Failed replay attempts by one remedy on one failure
        (spec section 6: the count that suspends at 2)."""
        rec = self.get_failure(failure_id)
        if rec is None:
            return 0
        return sum(1 for a in rec["attempts"]
                   if a.get("remedy_id") == remedy_id
                   and a.get("outcome") == "replay_failed")

    # -- outcome records (Derivation 2) -------------------------------

    def add_outcome(self, sealed_entry_ref, responder, response,
                    classification=None, asserts_failure=False):
        """Store an outcome record: an external response to a
        sealed output. The response is verbatim; a pattern
        classification is marked as classification, not fact.
        asserts_failure opens a failure record — provenance-
        grade only, never a witness (the caller / loop resolves
        it by section 4 replay only)."""
        rec = {
            "outcome_id": self._next_id("outcome", "OUT"),
            "sealed_entry_ref": sealed_entry_ref,
            "responder": responder,
            "response": response,
            "classification": None if classification is None
            else {"label": classification,
                  "marked_as": "classification_not_fact"},
            "asserts_failure": bool(asserts_failure),
            "opened_failure_id": None,
            "counts_as_witness": False,
            "resolution": None,
        }
        self._data["outcomes"].append(rec)
        self._save()
        if asserts_failure:
            failure = self.add_failure(
                entry_ref=sealed_entry_ref,
                signature={
                    "process": "outcome_review",
                    "gap_kind": "asserted_failure",
                    "missing_item": str(sealed_entry_ref),
                    "rule_fired": "outcome_assertion"},
                mechanism="outcome record %s asserts failure of "
                          "the sealed output" % rec["outcome_id"],
                cause={"kind": "outcome_asserted_failure",
                       "detail": "responder assertion only",
                       "grade": "provenance_only",
                       "counts_as_witness": False},
                opened_by={"kind": "outcome_record",
                           "outcome_id": rec["outcome_id"],
                           "responder": responder})
            rec = self.get_outcome(rec["outcome_id"])
            rec["opened_failure_id"] = failure["failure_id"]
            self._save()
        return rec

    def set_outcome_resolution(self, outcome_id, resolution,
                               seal_proof=None):
        """The section 4 verdict, recorded on the outcome
        record as on the failure record (Derivation 2). WG3b:
        an outcome record resolves ONLY by replay — the door
        is resolution-by-reference, exactly like failure
        closure: without a seal verifying for the failure this
        outcome opened, the write is refused and recorded."""
        rec = self.get_outcome(outcome_id)
        if rec is None:
            raise DefectStoreError("no outcome %r" % (outcome_id,))
        failure_id = (resolution or {}).get("failure_id") \
            or rec.get("opened_failure_id")
        ok, entry = self._verify_seal_proof(
            seal_proof, failure_id=failure_id)
        if not ok:
            refusal = self._record_door_refusal(
                "set_outcome_resolution", outcome_id,
                {"resolution": resolution}, entry)
            return self._refused_copy(rec, refusal)
        rec["resolution"] = resolution
        self._save()
        return rec

    # -- certified repairs (certification ONCE) -----------------------

    def add_certified(self, remedy_id, failure_id, sig_key,
                      seal_entry_id, calculus_generation,
                      seal_proof=None, code_manifest_hash=None):
        """Register a certified repair. WG3b: registration is
        resolution-by-reference — without a seal verifying
        against the atomic store for this failure, the
        registration is refused (recorded) and NOTHING is
        certified. A registered record carries the
        certification identity (signature + operations hash +
        governed lineage set), the seal's content hash, and
        the seal's code-manifest hash (the NC-1 code identity
        is a stored hash, never a label)."""
        remedy = self.get_remedy(remedy_id)
        ok, entry = self._verify_seal_proof(
            seal_proof, failure_id=failure_id,
            operations=remedy["operations"] if remedy else None,
            sig_key=sig_key)
        if not ok:
            self._record_door_refusal(
                "add_certified", remedy_id,
                {"failure_id": failure_id,
                 "seal_entry_id": seal_entry_id}, entry)
            return None
        inp = entry["input"]
        operations = inp.get("operations") or []
        provenance = inp.get("remedy_provenance") or {}
        identity = certification_identity(
            sig_key, operations, provenance)
        environment = entry.get("environment") or {}
        rec = {
            "remedy_id": remedy_id,
            "failure_id": failure_id,
            "signature_key": sig_key,
            "seal_entry_id": seal_entry_id,
            "seal_content_hash": content_hash(entry),
            "calculus_generation": calculus_generation,
            "code_manifest_hash": code_manifest_hash
            or environment.get("code_manifest_hash"),
            "operations_hash": identity["operations_hash"],
            "lineage_set": identity["lineage_set"],
            "certification_identity": identity,
            "certification_identity_key": content_hash(identity),
            "certified_once": True,
            "basis": "cross_check_replay",
        }
        self._data["certified"].append(rec)
        self._save()
        return rec

    def reconcile_sealed_closures(self, atomic_root,
                                  calculus_generation=None):
        """Seal -> closure reconciliation (WG3b, T2c's
        recoverability guarantee): scan the atomic store at
        atomic_root; every self-fix seal that references a
        failure STILL OPEN in this store is linked — the
        failure is closed by seal reference, its remedy's
        closure list and standing are restored by the same
        reference, a missing certified record is registered,
        and an outcome resolution owed is written. The scan
        is IDEMPOTENT: over a consistent store it changes
        nothing and registers nothing twice."""
        result = {"closed": [], "certified": [],
                  "remedies_verified": [], "outcomes_resolved": []}
        if not atomic_root:
            return result
        import pmca_atomic_entries as ae
        try:
            astore = ae.EntryStore.open(atomic_root, read_only=True)
        except Exception:
            return result
        try:
            entries = astore.replay()
        finally:
            astore.close()
        for entry in entries:
            inp = entry.get("input") or {}
            if inp.get("kind") != "self_fix":
                continue
            failure_id = inp.get("failure_id")
            failure = self.get_failure(failure_id) \
                if failure_id else None
            if failure is None:
                continue
            seal_proof = {
                "entry_id": entry["entry_id"],
                "content_hash": content_hash(entry),
                "atomic_root": atomic_root}
            outcome = entry.get("outcome") or {}
            if failure["status"] == FAILURE_OPEN:
                closed = self.set_failure_status(
                    failure_id, FAILURE_CLOSED_BY_REMEDY,
                    closure={
                        "remedy_id": inp.get("remedy_id"),
                        "seal_entry_id": entry["entry_id"],
                        "basis": "replay_proof",
                        "pre_hashes": outcome.get("pre_hashes"),
                        "post_hashes": outcome.get("post_hashes"),
                        "reconciled": True},
                    seal_proof=seal_proof)
                if closed.get("door_refused"):
                    continue
                result["closed"].append(failure_id)
                failure = self.get_failure(failure_id)
            if failure["status"] != FAILURE_CLOSED_BY_REMEDY:
                continue
            remedy_id = inp.get("remedy_id")
            remedy = self.get_remedy(remedy_id) if remedy_id \
                else None
            if remedy is not None:
                if failure_id not in (remedy.get("closures")
                                      or []):
                    remedy["closures"].append(failure_id)
                    self._save()
                if remedy["status"] == REMEDY_CANDIDATE and \
                        not remedy.get("suspended"):
                    verified = self.set_remedy_status(
                        remedy_id, REMEDY_VERIFIED,
                        seal_proof=seal_proof)
                    if not verified.get("door_refused"):
                        result["remedies_verified"].append(
                            remedy_id)
            if not self.find_certified(failure_id=failure_id):
                added = self.add_certified(
                    remedy_id, failure_id,
                    failure["signature_key"], entry["entry_id"],
                    calculus_generation or "reconciled",
                    seal_proof=seal_proof)
                if added is not None:
                    result["certified"].append(failure_id)
            opened_by = failure.get("opened_by") or {}
            if opened_by.get("kind") == "outcome_record":
                outcome_rec = self.get_outcome(
                    opened_by["outcome_id"])
                if outcome_rec is not None and \
                        outcome_rec.get("resolution") is None:
                    resolved = self.set_outcome_resolution(
                        opened_by["outcome_id"],
                        {"verdict": outcome.get("verdict"),
                         "outcome": "CLOSED",
                         "seal_entry_id": entry["entry_id"],
                         "failure_id": failure_id,
                         "remedy_id": remedy_id,
                         "reconciled": True},
                        seal_proof=seal_proof)
                    if not resolved.get("door_refused"):
                        result["outcomes_resolved"].append(
                            opened_by["outcome_id"])
        return result

    # -- section 4A aggregation pass (Derivation 1) -------------------

    def aggregation_pass(self):
        """A deterministic pass over the store: exact counts of
        open failure records per signature and per missing
        item; a signature crossing the stored struggle
        threshold (PROPOSED configuration) is marked
        'struggled' on the worklist — a record affecting
        worklist ORDER only, never a verdict input; when >= 2
        open failures with different signatures name the same
        missing item, ONE prerequisite-gap record is written
        (CANDIDATE cause, linked to every member; applies no
        repair, closes nothing). Idempotent: a pass over an
        unchanged store creates no second prereq record for
        the same member set."""
        threshold = int(self.config["struggle_threshold"]["value"])
        open_recs = self.open_failures()
        by_sig = {}
        by_item = {}
        for rec in open_recs:
            by_sig[rec["signature_key"]] = \
                by_sig.get(rec["signature_key"], 0) + 1
            item = rec["signature"]["missing_item"]
            by_item.setdefault(item, []).append(rec)
        struggled = []
        for sig_key, count in sorted(by_sig.items()):
            if count >= threshold:
                struggled.append(sig_key)
                for rec in self.failures_by_signature(sig_key):
                    if rec["status"] == FAILURE_OPEN:
                        rec["worklist_marks"]["struggled"] = True
        created = []
        for item in sorted(by_item):
            members = by_item[item]
            sigs = {r["signature_key"] for r in members}
            if len(members) < 2 or len(sigs) < 2:
                continue
            member_ids = sorted(r["failure_id"] for r in members)
            existing = [p for p in self._data["prereq_gaps"]
                        if p["missing_item"] == item
                        and p["member_failure_ids"] == member_ids
                        and p["status"] == "open"]
            if existing:
                continue
            rec = {
                "prereq_id": self._next_id("prereq", "PRE"),
                "kind": "prerequisite_gap",
                "missing_item": item,
                "member_failure_ids": member_ids,
                "member_signature_keys": sorted(sigs),
                "cause_grade": "CANDIDATE",
                "status": "open",
                "applies_repair": False,
                "closes_nothing": True,
            }
            self._data["prereq_gaps"].append(rec)
            created.append(rec)
            for member in members:
                cause = {"kind": "prerequisite_gap",
                         "prereq_id": rec["prereq_id"],
                         "missing_item": item,
                         "grade": "CANDIDATE"}
                if cause not in member["candidate_causes"]:
                    member["candidate_causes"].append(cause)
        self._save()
        return {
            "counts_by_signature": by_sig,
            "counts_by_missing_item": {
                item: len(recs) for item, recs in by_item.items()},
            "threshold": threshold,
            "threshold_status":
                self.config["struggle_threshold"]["status"],
            "struggled_signatures": struggled,
            "prereq_records_created": [r["prereq_id"]
                                       for r in created],
        }

    def worklist(self):
        """Open failures in worklist order: struggled first,
        then by failure id. ORDER ONLY — this list is never a
        verdict input and nothing here is read by any verdict
        path."""
        open_recs = self.open_failures()
        return sorted(
            open_recs,
            key=lambda r: (0 if r["worklist_marks"]["struggled"]
                           else 1, r["failure_id"]))
