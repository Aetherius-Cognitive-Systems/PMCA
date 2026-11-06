"""pmca_self_fix — the self-fix loop (Spec of record:
pmca-self-fix-spec-2026-10-31.md, sections 4-6; build WG3,
04 Forge, 2026-11-01).

Architecture and design: Jonathan Wayne Fleuren (the builder) —
the self-fix ruling 2026-10-31; the certification principle
2026-11-01 ("It certifies by cross checking real data";
certification ONCE, with the NC-1 carve-out). Code implemented
by Alder's hive (04 Forge) per the spec and its three Protogen
derivations.

THE LOOP (spec section 4, exactly seven steps; the step-to-code
map is in the WG3 build paper):

  1. RECORD — a process fails or records a gap -> a failure
     record is written to the defect store first (no silent
     gaps; the record precedes any repair attempt).
     record_failure()
  2. EXACT-MATCH LOOKUP — the failure's signature is looked up
     exactly. Exact remedy -> continue. Near-match -> the
     matched record may be named as a CANDIDATE cause on the
     failure record; no repair is applied. No match -> the
     failure record stands open; the process HOLDs on that
     surface exactly as today.                    run_once()
  3. NEVER-CLASS CHECK — before remedy matching. A never-class
     failure is flagged and the loop stops (spec section 2);
     a remedy record can never outrank the never-class,
     regardless of the remedy's provenance.
     pmca_defect_store.is_never_class, called in run_once()
     before remedies_for_signature is consulted for matching.
  4. SANDBOX APPLY — the verified remedy's operations are
     applied inside a sandbox copy of the affected entries;
     the live store is not touched yet.           run_once()
  5. REPLAY PROOF — (a) the original failing case replayed
     end-to-end on the sandbox; it must complete with its
     verdict computed fresh; (b) the remedy's battery set;
     (c) the standing regression batteries for every surface
     the remedy touches (system-wide fix rule). Any
     disagreement -> the repair FAILS: sandbox discarded,
     attempt appended to the failure record, HOLD stands.
     This replay is the certification cross-check: nothing
     is certified by its own assertion or by agreement with
     its own products (2026-11-01 principle); a remedy whose
     provenance claims certification by agreement with its
     own class is REFUSED here, before any application.
  6. SEAL — the repair is written as ONE atomic entry via
     pmca_atomic_entries (the atomic-entry ruling): the entry
     carries the failure reference, the remedy id +
     provenance, the operations, the full replay trace, and
     (Derivation 3) the content hash of each affected entry
     BEFORE the repair and AS SEALED after it, scoped to the
     affected entries only — hashes, never copies (stored-
     once). The failure record closes by reference to that
     entry. A self-fix never counts as its own witness: the
     closing evidence is the replay, not the remedy's say-so.
     Certification is ONCE: the sealed repair is registered
     in the defect store's certified registry and is never
     re-adjudicated; across a calculus generation change
     (NC-1) the code identity is checked and the check stops
     there — the old certification is neither re-run nor
     revoked.
  7. REGISTRATION HOOK — registration_hook(registry) is the
     function WG4's capability registry will call; it hands
     the registry capability_description() as DATA (id,
     purpose, inputs, outputs, limits including the
     never-class list), so the system can state, by lookup,
     exactly what it may and may not repair in itself.

REMEDY LIFECYCLE (spec sections 5-6):
  - Teaching (TEACH-V semantics): a taught remedy enters
    CANDIDATE; the teacher's assertion is provenance only;
    the verdict of record is the section 4 replay. A taught
    remedy whose first proof fails stays CANDIDATE and the
    failure record carries the disagreement.
  - Derivation from a verified builder fix: CANDIDATE until
    its first successful section 4 proof on a live failure;
    scope inherits the parent's or narrower, never wider
    (enforced at registration in the defect store).
  - System proposals: CANDIDATE only, never self-applied —
    the loop refuses to apply a remedy whose provenance is a
    system proposal, however exact the match.
  - Remedy loop fence: 2 failed attempts by the same remedy
    on one failure suspend that remedy to CANDIDATE; no third
    silent attempt (a third call is refused loudly and
    records no attempt).
  - Conflicting remedies (two applicable remedies match one
    signature): HOLD; conflict recorded; never first-match.
  - Rot: a verified remedy whose battery later fails on
    replay is suspended system-wide; every closure it ever
    sealed is listed for review — closures stand (sealed
    entries are sealed) but are flagged with the suspension.
    check_rot()

WORLD MODEL: the loop repairs CONTENT over stored structure.
The caller supplies the affected-entries world as a plain
dict {"entries": {entry_key: entry_dict, ...}} plus a replay
function replay_fn(world, case) -> {"completed": bool,
"verdict": ...} and per-surface regression functions. The
loop itself holds no verdict machinery and imports none: it
imports pmca_defect_store (records + never-class) and, only
at seal time, pmca_atomic_entries (the public EntryStore
API). Integration with the tree's verdict/teaching modules
is through this harness only — no shared module is edited.

ATOMIC SURFACE NOTE (recorded, not smoothed): entry format v1
admits five surfaces (analysis / teaching / write_in /
composition / population). A self-fix writes repaired content
into stored structure, so its entries are sealed on the
"write_in" surface; a dedicated self-fix surface is an entry-
format question for a later ruling (OPEN).

Deterministic. Standard library only. No floats.

WG3b FIX-PASS (2026-11-01, paired check wg3-forge-check.md):
- Remedy operations are validated BEFORE any application,
  twice: by target identity (pmca_defect_store.
  classify_operation_target — machinery/evidence state is
  never a target) and by SCOPE (a target is in-scope only
  when it is the failing case's entry or the entry the
  failure's missing item names; anything else, and anything
  unclassifiable, is REFUSED — HOLD-on-unclassifiable).
- The replay proof's cross-check set is SNAPSHOTTED at
  attempt start: the proof runs on a world whose
  evidence-class entries are frozen at their pre-attempt
  values, so a record the repair itself writes during the
  attempt is excluded from its own certification evidence
  by construction.
- The self-agreement fence is structural: claim/source
  identifiers are canonicalized and the agreeing lineage is
  compared through the governed table (pmca_lineage_table),
  never by claim spelling.
- Certification identity is (signature, operations content
  hash, governed lineage set): a resubmission under a new
  remedy ID or a respelled source is the SAME certification
  and is refused as already-certified, citing the original
  seal. Certified records store the seal's code-manifest
  hash; NC-1 compares that stored hash against the current
  code manifest hash (the pmca_sentence_analyzer
  _code_manifest precedent: {module: sha256 of file bytes},
  hashed canonically), never a generation label alone.
- Verification requires a non-empty replay battery, and
  every application re-checks the remedy record's operations
  hash (stored at registration): a mismatch is the rot path
  (system-wide suspension + closure listing), never an
  application.
- Offered (unregistered) remedies are REGISTERED through
  the store — with every registration fence — BEFORE any
  apply, and every post-seal store transition goes through
  the store's resolution-by-reference doors. After any seal,
  the store reconciles seal -> closure on open (loop
  construction and every run_once): a seal referencing a
  still-open failure closes it by reference, idempotently.
"""

import copy
import hashlib
import os

import pmca_defect_store as ds
import pmca_lineage_table as lt

CAPABILITY_ID = "pmca.self_fix_loop"
SELF_FIX_SURFACE = "write_in"

# Provenance source kinds.
SOURCE_TEACHING = "teaching"
SOURCE_BUILDER_FIX = "builder_fix"
SOURCE_SYSTEM_PROPOSAL = "system_proposal"

# A remedy whose provenance claims certification by agreement
# with its own class certifies nothing (2026-11-01 principle:
# agreement with one's own class is one witness repeated).
SELF_AGREEMENT_CLAIMS = frozenset({
    "own_class_agreement", "self_agreement",
    "agreement_with_own_class", "agreement_with_its_own_class",
})


def _code_manifest():
    """{module file name: sha256 of file bytes} for the two
    modules whose code seals a repair — the analyzer's
    _code_manifest precedent, reused."""
    manifest = {}
    for name in ("pmca_self_fix.py", "pmca_defect_store.py"):
        path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), name)
        if os.path.exists(path):
            with open(path, "rb") as handle:
                manifest[name] = hashlib.sha256(
                    handle.read()).hexdigest()
    return manifest


def _code_manifest_hash():
    """The current code identity: canonical hash of the code
    manifest. Compared against the hash stored in a certified
    record at seal time — an actual code comparison, NC-1."""
    return ds.content_hash(_code_manifest())


def is_self_agreement_provenance(prov):
    """The self-agreement fence, structural (WG3b): a
    provenance claims certification by agreement with its own
    class when (a) any listed claim spelling appears, OR
    (b) its canonical claim tokens name agreement with its
    OWN / SELF / INTERNAL records under any respelling, OR
    (c) the lineage of the source it claims agreement /
    certification from — resolved through the GOVERNED table
    (pmca_lineage_table) — is the lineage of its own
    teacher/source: agreement with one's own lineage is one
    witness repeated, whatever either side is spelled."""
    prov = prov or {}
    claim = prov.get("certification_claim")
    if claim in SELF_AGREEMENT_CLAIMS:
        return True
    tokens = set()
    for key in ("source_kind", "certification_claim", "claim"):
        value = prov.get(key)
        if isinstance(value, str):
            tokens |= ds.canonical_tokens(value)
    if ds.canonical_key(prov.get("source_kind") or "") == \
            "self_agreement":
        return True
    if "agreement" in tokens and tokens & {
            "own", "self", "itself", "internal"}:
        return True
    if "self" in tokens and tokens & {
            "agreement", "certification", "certified"}:
        return True
    own_lineages = set()
    for key in ("teacher", "source", "lineage"):
        cls = lt.lineage_class(prov.get(key))
        if cls is not None:
            own_lineages.add(cls)
    witness_lineages = set()
    for key in ("certification_source", "certifying_source",
                "agreement_source", "witness_source",
                "certified_by"):
        cls = lt.lineage_class(prov.get(key))
        if cls is not None:
            witness_lineages.add(cls)
    return bool(own_lineages & witness_lineages)


def capability_description():
    """The loop's capability description AS DATA (spec section
    4 step 7): id, purpose, inputs, outputs, limits — the
    never-class list included in full."""
    return {
        "id": CAPABILITY_ID,
        "version": 1,
        "purpose": "Repair recorded, closable content failures "
                   "in the teachable class by exact-signature "
                   "lookup of stored remedies, sandbox "
                   "application, replay proof, and one sealed "
                   "atomic entry per repair. Everything else "
                   "HOLDs or is refused.",
        "inputs": [
            "failure records in the defect store "
            "(pmca_defect_store)",
            "the affected-entries world (stored structure)",
            "a caller-supplied replay function and per-surface "
            "regression functions",
            "remedy records registered in the defect store",
        ],
        "outputs": [
            "closed failure records referencing their sealing "
            "atomic entry",
            "one sealed atomic entry per successful repair "
            "(failure ref, remedy id + provenance, operations, "
            "full replay trace, pre/post content hashes of the "
            "affected entries)",
            "attempt, conflict, suspension, and rot records in "
            "the defect store — no silent gaps",
        ],
        "limits": {
            "never_class": sorted(ds.NEVER_CLASS_PROCESSES),
            "never_class_scope_surfaces":
                sorted(ds.NEVER_CLASS_SCOPE_SURFACES),
            "teachable_class_only": True,
            "candidate_only_sources": [SOURCE_SYSTEM_PROPOSAL],
            "max_attempts_per_remedy_per_failure": 2,
            "conflicting_remedies": "HOLD, never first-match",
            "certification": "cross-check replay against the "
                             "recorded original case and the "
                             "remedy battery; ONCE — a sealed "
                             "certification is never "
                             "re-adjudicated (NC-1: across a "
                             "calculus generation change only "
                             "the code identity is checked)",
            "self_certification": "refused — agreement with "
                                  "one's own class certifies "
                                  "nothing",
            "operations_validated": "by target identity and "
                                    "by failure scope before "
                                    "any application; an "
                                    "unclassifiable target is "
                                    "refused (HOLD-on-"
                                    "unclassifiable)",
            "verification_requires": "a non-empty replay "
                                     "battery; the remedy "
                                     "record's operations "
                                     "hash is re-checked on "
                                     "every application",
            "store_doors": "VERIFIED / CLOSED transitions are "
                           "resolution-by-reference only "
                           "(a sealed atomic-entry reference "
                           "whose content hash verifies)",
            "outcome_records": "provenance-grade only; never "
                               "unseal, amend, outweigh, or "
                               "witness a sealed entry",
        },
    }


class SelfFixRefused(Exception):
    """A loud refusal by the loop (never-class, certification,
    amendment fences). Raised only by the doors whose contract
    is refusal; run_once reports its refusals as data."""


class SelfFixLoop:
    """One self-fix loop over one defect store.

    defect_store: a pmca_defect_store.DefectStore.
    atomic_root: directory root for pmca_atomic_entries
        EntryStore.open (the store lives at <root>/atomic/).
        None disables sealing — a proof that passes with no
        atomic root reports SEAL_SKIPPED and closes nothing
        (the battery always sets a root).
    replay_fn: callable(world, case) -> {"completed": bool,
        "verdict": <any JSON value>}. The verdict is computed
        by the caller from world content only; the loop never
        passes marks, thresholds, or store state to it — the
        section 4A non-influence property holds by this
        signature.
    regression_fns: {surface: callable(world) -> bool or
        {"ok": bool}} for every surface a remedy may touch.
    calculus_generation: the sealing code's generation label,
        recorded on certified repairs for the NC-1 check.
    """

    def __init__(self, defect_store, atomic_root=None,
                 replay_fn=None, regression_fns=None,
                 calculus_generation="current"):
        self.store = defect_store
        self.atomic_root = atomic_root
        self.replay_fn = replay_fn
        self.regression_fns = dict(regression_fns or {})
        self.calculus_generation = calculus_generation
        # WG3b: constructing the loop over a store reconciles
        # any seal -> closure gap left by a crash (the T2c
        # recoverability guarantee). Reconciliation is
        # idempotent; over a consistent store it is a no-op.
        self.last_reconciliation = self._reconcile()

    def _reconcile(self):
        try:
            return self.store.reconcile_sealed_closures(
                self.atomic_root,
                calculus_generation=self.calculus_generation)
        except Exception as exc:  # recorded, never silent
            return {"error": "%s: %s" % (type(exc).__name__, exc)}

    # -- registration hook (step 7) -------------------------------

    def registration_hook(self, registry):
        """The function WG4's capability registry will call.
        Hands the registry capability_description() as data:
        registry may be a callable, an object with .register,
        a dict (keyed by capability id), or a list (appended).
        Returns the description handed over."""
        desc = capability_description()
        if callable(registry):
            registry(desc)
        elif hasattr(registry, "register"):
            registry.register(desc)
        elif isinstance(registry, dict):
            registry[desc["id"]] = desc
        elif isinstance(registry, list):
            registry.append(desc)
        else:
            raise SelfFixRefused(
                "registry accepts no registration: %r"
                % (type(registry),))
        return desc

    # -- step 1: record ---------------------------------------------

    def record_failure(self, entry_ref, signature, mechanism,
                       cause=None, case_name=None):
        """Step 1: write the failure record before anything
        else is attempted (no silent gaps)."""
        return self.store.add_failure(
            entry_ref=entry_ref, signature=signature,
            mechanism=mechanism, cause=cause,
            case_name=case_name)

    # -- remedy entry paths (spec section 5) -------------------------

    def teach_remedy(self, signature, operations, teacher,
                     claim_id=None, replay_set=None,
                     scope_surfaces=None, world=None, cases=None,
                     failure_id=None):
        """Teaching path (TEACH-V semantics): the remedy enters
        CANDIDATE; the teacher's assertion is provenance ONLY.
        When a world and a failure are supplied, the section 4
        replay runs as the verdict of record: proof passed ->
        the remedy's standing comes from the replay, not the
        assertion; proof failed -> the remedy stays CANDIDATE
        and the failure record carries the disagreement."""
        try:
            remedy = self.store.add_remedy(
                signature=signature, operations=operations,
                provenance={"source_kind": SOURCE_TEACHING,
                            "teacher": teacher,
                            "claim_id": claim_id,
                            "assertion_grade": "provenance_only"},
                replay_set=replay_set,
                scope_surfaces=scope_surfaces)
        except (ds.NeverClassRefused, ds.DefectStoreError) as exc:
            # WG3b: a teaching whose signature, scope, or
            # OPERATION TARGETS are refused at registration
            # (never-class machinery/evidence state by
            # identity, or an unclassifiable target) is a
            # refusal of record, not a crash and never an
            # application: nothing is registered, nothing is
            # applied, the failure stands open.
            outcome = "NEVER_CLASS_REFUSED" if isinstance(
                exc, ds.NeverClassRefused) \
                else "REGISTRATION_REFUSED"
            return {"remedy_id": None, "status": "refused",
                    "verdict_of_record": outcome,
                    "assertion_is_provenance_only": True,
                    "refusal_reason": str(exc),
                    "proof": {"outcome": outcome,
                              "reason": str(exc)}}
        result = {"remedy_id": remedy["remedy_id"],
                  "status": remedy["status"],
                  "verdict_of_record": None,
                  "assertion_is_provenance_only": True,
                  "proof": None}
        if world is not None and failure_id is not None:
            proof = self.run_once(failure_id, world, cases=cases)
            result["proof"] = proof
            result["verdict_of_record"] = proof.get("outcome")
            result["status"] = self.store.get_remedy(
                remedy["remedy_id"])["status"]
        return result

    def derive_remedy(self, parent_remedy_id, signature,
                      operations, fix_claim_id, replay_set=None,
                      scope_surfaces=None):
        """Derivation path: from a verified builder fix (or a
        verified remedy standing for one). CANDIDATE until its
        first successful section 4 proof on a live failure;
        scope inherits the parent's or narrower (the store
        refuses wider)."""
        parent = self.store.get_remedy(parent_remedy_id)
        if parent is None:
            raise SelfFixRefused(
                "unknown parent remedy %r" % (parent_remedy_id,))
        return self.store.add_remedy(
            signature=signature, operations=operations,
            provenance={"source_kind": SOURCE_BUILDER_FIX,
                        "claim_id": fix_claim_id,
                        "derived_from": parent_remedy_id},
            replay_set=replay_set, scope_surfaces=scope_surfaces,
            derivation={"parent_remedy_id": parent_remedy_id,
                        "parent_status": parent["status"]})

    def propose_remedy(self, signature, operations, detail=None,
                       replay_set=None, scope_surfaces=None):
        """System-proposal path: CANDIDATE only, never
        self-applied (the loop refuses it in run_once)."""
        return self.store.add_remedy(
            signature=signature, operations=operations,
            provenance={"source_kind": SOURCE_SYSTEM_PROPOSAL,
                        "detail": detail,
                        "assertion_grade": "provenance_only"},
            replay_set=replay_set, scope_surfaces=scope_surfaces)

    # -- outcome records (Derivation 2) -------------------------------

    def record_outcome(self, sealed_entry_ref, responder,
                       response, classification=None,
                       asserts_failure=False):
        """Store an external response to a sealed output. An
        asserting outcome opens a provenance-grade failure
        record; resolution is section 4 replay only."""
        return self.store.add_outcome(
            sealed_entry_ref=sealed_entry_ref, responder=responder,
            response=response, classification=classification,
            asserts_failure=asserts_failure)

    def apply_outcome_amendment(self, outcome_id, amendments):
        """FENCE (Derivation 2): an outcome record never
        unseals, amends, or outweighs the sealed entry it
        references. Any amendment routed here is refused,
        loudly, as data — the sealed entry is not touched."""
        outcome = self.store.get_outcome(outcome_id)
        if outcome is None:
            raise SelfFixRefused("no outcome %r" % (outcome_id,))
        return {
            "refused": True,
            "reason": "outcome records never amend sealed "
                      "entries (Derivation 2)",
            "outcome_id": outcome_id,
            "sealed_entry_ref": outcome["sealed_entry_ref"],
            "amendments_refused": amendments,
        }

    # -- the loop: steps 2-6 -------------------------------------------

    def run_once(self, failure_id, world, cases=None,
                 offered_remedy=None):
        """One pass of the loop over one failure record.

        world: {"entries": {...}} — mutated in place ONLY on a
        sealed success (the sandbox is a deep copy until then).
        cases: {case_name: case} — the original case is
        cases[failure.case_name]; remedy battery cases are
        named in each remedy's replay_set.
        offered_remedy: an unregistered remedy dict handed to
        the loop directly (an external offer). It is subject
        to exactly the same fences — in particular the
        never-class check precedes any consideration of it,
        whatever its provenance.
        Returns a result dict whose "outcome" is one of:
        CLOSED, HOLD_NO_MATCH, HOLD_NEAR_MATCH,
        NEVER_CLASS_REFUSED, HOLD_CONFLICT,
        HOLD_CANDIDATE_NOT_APPLICABLE, CERTIFICATION_REFUSED,
        REFUSED_SUSPENDED, REPLAY_FAILED,
        ALREADY_CERTIFIED, CERTIFIED_STANDS_NC1,
        FAILURE_NOT_OPEN.
        """
        cases = dict(cases or {})
        trace = []
        store = self.store
        # WG3b: before anything else, reconcile any seal ->
        # closure gap (a seal that references this failure
        # while it still stands open closes it by reference).
        self.last_reconciliation = self._reconcile()
        failure = store.get_failure(failure_id)
        if failure is None:
            raise SelfFixRefused("no failure %r" % (failure_id,))

        # Certification ONCE: a closed failure is never
        # re-adjudicated. Across a generation change (NC-1)
        # the code identity is checked and the check stops
        # there — not re-run, not revoked. WG3b: the code
        # identity check compares the code-manifest HASH
        # stored in the certified record at seal time against
        # the CURRENT code manifest hash — an actual stored
        # code hash, never a generation label alone.
        if failure["status"] != ds.FAILURE_OPEN:
            certified = store.find_certified(failure_id=failure_id)
            if certified:
                rec = certified[0]
                stored_hash = rec.get("code_manifest_hash")
                current_hash = _code_manifest_hash()
                identity_checked = stored_hash is not None
                identity_match = identity_checked and \
                    stored_hash == current_hash
                if rec["calculus_generation"] == \
                        self.calculus_generation:
                    return {"outcome": "ALREADY_CERTIFIED",
                            "failure_id": failure_id,
                            "certified": rec,
                            "code_identity_checked":
                                identity_checked,
                            "code_identity_match": identity_match,
                            "code_manifest_hash_stored":
                                stored_hash,
                            "code_manifest_hash_current":
                                current_hash,
                            "re_adjudicated": False,
                            "trace": trace}
                return {"outcome": "CERTIFIED_STANDS_NC1",
                        "failure_id": failure_id,
                        "certified": rec,
                        "code_identity_checked": identity_checked,
                        "code_identity_match": identity_match,
                        "code_manifest_hash_stored": stored_hash,
                        "code_manifest_hash_current": current_hash,
                        "certified_generation":
                            rec["calculus_generation"],
                        "current_generation":
                            self.calculus_generation,
                        "re_adjudicated": False,
                        "revoked": False,
                        "trace": trace}
            return {"outcome": "FAILURE_NOT_OPEN",
                    "failure_id": failure_id,
                    "status": failure["status"], "trace": trace}

        sig = failure["signature"]
        sig_key = failure["signature_key"]
        trace.append({"step": 2, "name": "exact_match_lookup",
                      "signature_key": sig_key})

        # STEP 3 — NEVER-CLASS CHECK, BEFORE REMEDY MATCHING.
        # No remedy record — registered or offered, whatever
        # its provenance — is consulted before this check.
        never, reason = ds.is_never_class(sig)
        trace.append({"step": 3, "name": "never_class_check",
                      "never_class": never, "reason": reason})
        if never:
            store.set_failure_status(failure_id,
                                     ds.FAILURE_NEVER_CLASS)
            store.append_attempt(failure_id, {
                "remedy_id": None, "outcome": "never_class_refused",
                "detail": reason})
            return {"outcome": "NEVER_CLASS_REFUSED",
                    "failure_id": failure_id, "reason": reason,
                    "step_reached": 3, "trace": trace}

        # STEP 2 (matching half) — exact remedy matching.
        remedies = store.remedies_for_signature(sig_key)
        if offered_remedy is not None:
            remedies = remedies + [dict(offered_remedy)]
        exact = [r for r in remedies
                 if ds.signature_key(r["signature"]) == sig_key]
        trace.append({"step": 2, "name": "remedy_matching",
                      "exact_remedy_ids":
                          [r.get("remedy_id") for r in exact]})

        # Certification fence: a remedy claiming certification
        # by agreement with its own class is refused before
        # any application, however impeccable the match. WG3b:
        # the fence is structural — canonical claim tokens and
        # lineage comparison through the governed table, never
        # a spelling list (is_self_agreement_provenance).
        for remedy in exact:
            prov = remedy.get("provenance") or {}
            if is_self_agreement_provenance(prov):
                store.append_attempt(failure_id, {
                    "remedy_id": remedy.get("remedy_id"),
                    "outcome": "certification_refused",
                    "detail": "certification by agreement with "
                              "its own class certifies nothing "
                              "(2026-11-01 principle)"})
                return {"outcome": "CERTIFICATION_REFUSED",
                        "failure_id": failure_id,
                        "remedy_id": remedy.get("remedy_id"),
                        "trace": trace}

        # WG3b (T2c): an OFFERED remedy is registered through
        # the store — with every registration fence (never-
        # class signature/scope, operation-target identity) —
        # BEFORE any application is considered, and the loop
        # proceeds with the REGISTERED record. Registration /
        # verification status is therefore always checked
        # before apply; an offered remedy can no longer pass
        # its proof and then crash the post-seal bookkeeping
        # as an unknown id. Its asserted status counts for
        # nothing: the registered record is a CANDIDATE whose
        # standing comes from the replay, like any teaching.
        if offered_remedy is not None:
            offered_exact = [r for r in exact
                             if r is offered_remedy or
                             r.get("remedy_id") ==
                             offered_remedy.get("remedy_id")]
            if offered_exact:
                existing = store.get_remedy(
                    offered_remedy.get("remedy_id")) \
                    if offered_remedy.get("remedy_id") else None
                if existing is not None:
                    registered = existing
                else:
                    try:
                        registered = store.add_remedy(
                            signature=offered_remedy.get(
                                "signature") or sig,
                            operations=offered_remedy.get(
                                "operations") or [],
                            provenance=offered_remedy.get(
                                "provenance") or {},
                            replay_set=offered_remedy.get(
                                "replay_set"),
                            scope_surfaces=offered_remedy.get(
                                "scope_surfaces"))
                    except (ds.NeverClassRefused,
                            ds.DefectStoreError) as exc:
                        outcome = "NEVER_CLASS_REFUSED" if \
                            isinstance(exc, ds.NeverClassRefused) \
                            else "REGISTRATION_REFUSED"
                        store.append_attempt(failure_id, {
                            "remedy_id": offered_remedy.get(
                                "remedy_id"),
                            "outcome": outcome.lower(),
                            "detail": str(exc)})
                        return {"outcome": outcome,
                                "failure_id": failure_id,
                                "remedy_id": offered_remedy.get(
                                    "remedy_id"),
                                "reason": str(exc),
                                "applied": False, "trace": trace}
                exact = [registered if r in offered_exact else r
                         for r in exact]
                seen_ids = set()
                deduped = []
                for r in exact:
                    rid = r.get("remedy_id")
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    deduped.append(r)
                exact = deduped
                trace.append({"step": 2,
                              "name": "offered_remedy_registered",
                              "remedy_id": registered["remedy_id"]})

        if not exact:
            # Near-match: name a CANDIDATE cause, apply nothing.
            near = []
            for other in store.failures():
                if other["failure_id"] != failure_id and \
                        ds.signatures_near(sig, other["signature"]):
                    near.append({"kind": "near_failure",
                                 "failure_id": other["failure_id"],
                                 "grade": "CANDIDATE"})
            for remedy in store.remedies():
                if ds.signatures_near(sig, remedy["signature"]):
                    near.append({"kind": "near_remedy",
                                 "remedy_id": remedy["remedy_id"],
                                 "grade": "CANDIDATE"})
            for prereq in store.prereq_gaps():
                if failure_id in prereq["member_failure_ids"]:
                    near.append({"kind": "prerequisite_gap",
                                 "prereq_id": prereq["prereq_id"],
                                 "grade": "CANDIDATE"})
            if near:
                for cause in near:
                    store.add_candidate_cause(failure_id, cause)
                return {"outcome": "HOLD_NEAR_MATCH",
                        "failure_id": failure_id,
                        "candidate_causes": near,
                        "applied": False, "trace": trace}
            return {"outcome": "HOLD_NO_MATCH",
                    "failure_id": failure_id, "applied": False,
                    "trace": trace}

        # Applicability: VERIFIED remedies apply. A CANDIDATE
        # applies only on the teaching / builder-fix paths
        # (its first section 4 proof is what verifies it).
        # System proposals never self-apply. Suspended
        # remedies never apply.
        applicable = []
        blocked = []
        for remedy in exact:
            prov = remedy.get("provenance") or {}
            if remedy.get("suspended") or \
                    remedy.get("status") == ds.REMEDY_SUSPENDED:
                blocked.append((remedy, "suspended"))
            elif prov.get("source_kind") == \
                    SOURCE_SYSTEM_PROPOSAL:
                blocked.append((remedy, "system_proposal_"
                                        "candidate_only"))
            elif remedy.get("status") == ds.REMEDY_VERIFIED:
                applicable.append(remedy)
            elif remedy.get("status") == ds.REMEDY_CANDIDATE \
                    and prov.get("source_kind") in (
                        SOURCE_TEACHING, SOURCE_BUILDER_FIX):
                applicable.append(remedy)
            else:
                blocked.append((remedy, "candidate_not_"
                                        "applicable"))
        if not applicable:
            remedy, why = blocked[0]
            if why == "suspended":
                # No third silent attempt: refused loudly,
                # no attempt recorded.
                return {"outcome": "REFUSED_SUSPENDED",
                        "failure_id": failure_id,
                        "remedy_id": remedy.get("remedy_id"),
                        "trace": trace}
            return {"outcome": "HOLD_CANDIDATE_NOT_APPLICABLE",
                    "failure_id": failure_id,
                    "remedy_id": remedy.get("remedy_id"),
                    "reason": why, "applied": False,
                    "trace": trace}
        if len(applicable) > 1:
            conflict = {
                "remedy_ids": [r["remedy_id"] for r in applicable],
                "signature_key": sig_key,
                "resolution": "ruling or teaching event; never "
                              "first-match-wins"}
            store.add_conflict(failure_id, conflict)
            return {"outcome": "HOLD_CONFLICT",
                    "failure_id": failure_id,
                    "conflict": conflict, "applied": False,
                    "trace": trace}
        remedy = applicable[0]

        # Remedy-loop fence: this remedy already failed twice
        # on this failure — it stands suspended (set when the
        # second failure landed); refuse without an attempt.
        if store.failed_attempts_on(remedy["remedy_id"],
                                    failure_id) >= 2:
            return {"outcome": "REFUSED_SUSPENDED",
                    "failure_id": failure_id,
                    "remedy_id": remedy["remedy_id"],
                    "trace": trace}

        # WG3b (T3c): certification ONCE is by certification
        # IDENTITY — (signature, canonical repair-content
        # hash, governed lineage set) — not by remedy id or
        # failure id. A repair with this identity is already
        # certified, under whatever ID or source spelling it
        # is resubmitted: refused as already-certified, with
        # the original seal cited, and nothing applied.
        identity_key = ds.certification_identity_key(
            sig_key, remedy.get("operations"),
            remedy.get("provenance"))
        prior = [r for r in
                 store.find_certified_by_identity(identity_key)
                 if r["failure_id"] != failure_id]
        if prior:
            original = prior[0]
            store.append_attempt(failure_id, {
                "remedy_id": remedy["remedy_id"],
                "outcome": "already_certified",
                "detail": "certification identity already "
                          "certified under seal %s; a "
                          "resubmission under a new ID or a "
                          "respelled source is the same "
                          "certification"
                          % (original["seal_entry_id"],)})
            return {"outcome": "ALREADY_CERTIFIED",
                    "failure_id": failure_id,
                    "remedy_id": remedy["remedy_id"],
                    "certified": original,
                    "seal_entry_id": original["seal_entry_id"],
                    "certification_identity_key": identity_key,
                    "re_adjudicated": False,
                    "applied": False, "trace": trace}

        return self._apply_prove_seal(failure, remedy, world,
                                      cases, trace)

    # -- steps 4-6 -----------------------------------------------------

    def _in_scope_entries(self, failure, cases, world):
        """The positively classifiable in-scope target set
        for one failure (WG3b): the failing case's entry (the
        case named on the failure record, else the
        caller-supplied '__original__' case — the same
        resolution the replay proof uses) and the entry the
        failure's missing item names ('definition:wolf' ->
        'wolf'), when that name is a plain content name.
        Anything else is out of scope by construction."""
        entries = world.get("entries") or {}
        scope = set()
        case_name = failure.get("case_name")
        original = cases.get(case_name) if case_name else None
        if original is None:
            original = cases.get("__original__")
        if isinstance(original, dict) and \
                isinstance(original.get("entry"), str):
            scope.add(original["entry"])
        missing = (failure.get("signature") or {}).get(
            "missing_item") or ""
        if ":" in missing:
            target = missing.rsplit(":", 1)[1].strip()
            if target and ds.classify_operation_target(
                    target)[0] == "content":
                scope.add(target)
        entry_ref = failure.get("entry_ref") or ""
        if isinstance(entry_ref, str) and "-" in entry_ref:
            suffix = entry_ref.rsplit("-", 1)[1]
            if suffix in entries:
                scope.add(suffix)
        return scope

    def _apply_prove_seal(self, failure, remedy, world, cases,
                          trace):
        store = self.store
        failure_id = failure["failure_id"]
        remedy_id = remedy["remedy_id"]

        # WG3b pre-apply fences — ALL of them run BEFORE any
        # application, sandbox included:
        # (0) The remedy must be a REGISTERED record in the
        #     store (T2c: registration/verification status is
        #     checked before apply, never discovered after a
        #     seal). The stored record governs, not the
        #     caller's copy.
        fresh = store.get_remedy(remedy_id)
        if fresh is None:
            store.append_attempt(failure_id, {
                "remedy_id": remedy_id,
                "outcome": "registration_refused",
                "detail": "remedy is not registered in the "
                          "defect store; nothing applied"})
            return {"outcome": "REGISTRATION_REFUSED",
                    "failure_id": failure_id,
                    "remedy_id": remedy_id, "applied": False,
                    "trace": trace}
        remedy = fresh
        if remedy.get("suspended") or \
                remedy.get("status") == ds.REMEDY_SUSPENDED:
            return {"outcome": "REFUSED_SUSPENDED",
                    "failure_id": failure_id,
                    "remedy_id": remedy_id, "applied": False,
                    "trace": trace}
        # (1) Operations-hash re-check (T6b): the record's
        #     operations must hash to the operations hash
        #     stored at registration. A mismatch is ROT —
        #     suspension + closure listing — never an
        #     application.
        stored_ops_hash = remedy.get("operations_hash")
        if stored_ops_hash is None:
            store.append_attempt(failure_id, {
                "remedy_id": remedy_id,
                "outcome": "operation_refused",
                "detail": "remedy record carries no operations "
                          "hash; its standing is "
                          "unclassifiable, nothing applied"})
            return {"outcome": "OPERATION_REFUSED",
                    "failure_id": failure_id,
                    "remedy_id": remedy_id, "applied": False,
                    "reason": "no operations hash on the remedy "
                              "record (HOLD-on-unclassifiable)",
                    "trace": trace}
        if ds.content_hash(remedy["operations"]) != \
                stored_ops_hash:
            rot = self._suspend_for_rot(
                remedy_id,
                "operations hash mismatch: the record's "
                "operations no longer hash to the hash stored "
                "at registration (tamper or rot, WG3b T6b)")
            store.append_attempt(failure_id, {
                "remedy_id": remedy_id,
                "outcome": "rot_detected",
                "detail": "operations hash mismatch"})
            return {"outcome": "REFUSED_ROT",
                    "failure_id": failure_id,
                    "remedy_id": remedy_id, "applied": False,
                    "rotted": True,
                    "suspended_system_wide":
                        rot["suspended_system_wide"],
                    "closures_listed": rot["closures_listed"],
                    "trace": trace}
        # (2) Operation targets, by identity (T1c/T3b): any
        #     target that is machinery/evidence state, or
        #     cannot be positively classified, is refused.
        for op in remedy["operations"]:
            cls, reason = ds.classify_operation_target(
                op.get("entry"), op.get("field"))
            if cls != "content":
                outcome = "NEVER_CLASS_REFUSED" if cls in (
                    "protected", "surface") \
                    else "OPERATION_REFUSED"
                store.append_attempt(failure_id, {
                    "remedy_id": remedy_id,
                    "outcome": outcome.lower(),
                    "detail": reason})
                store.note_remedy_attempt(
                    remedy_id, failure_id, outcome.lower())
                return {"outcome": outcome,
                        "failure_id": failure_id,
                        "remedy_id": remedy_id, "applied": False,
                        "reason": reason,
                        "stage": "operation_validation",
                        "trace": trace}
        # (3) Operation targets, by SCOPE: a target is
        #     in-scope only when it is positively tied to
        #     THIS failure — the failing case's entry or the
        #     entry the missing item names. An operation
        #     aimed anywhere else is refused, not permitted.
        in_scope = self._in_scope_entries(failure, cases, world)
        if not in_scope:
            store.append_attempt(failure_id, {
                "remedy_id": remedy_id,
                "outcome": "operation_refused",
                "detail": "no positively classifiable in-scope "
                          "target for this failure "
                          "(HOLD-on-unclassifiable)"})
            return {"outcome": "OPERATION_REFUSED",
                    "failure_id": failure_id,
                    "remedy_id": remedy_id, "applied": False,
                    "reason": "no in-scope target can be "
                              "positively classified for this "
                              "failure",
                    "stage": "operation_validation",
                    "trace": trace}
        for op in remedy["operations"]:
            if op.get("entry") not in in_scope:
                reason = "operation target %r is not in this " \
                    "failure's scope %s — refused, not " \
                    "permitted" % (op.get("entry"),
                                   sorted(in_scope))
                store.append_attempt(failure_id, {
                    "remedy_id": remedy_id,
                    "outcome": "operation_refused",
                    "detail": reason})
                store.note_remedy_attempt(
                    remedy_id, failure_id, "operation_refused")
                return {"outcome": "OPERATION_REFUSED",
                        "failure_id": failure_id,
                        "remedy_id": remedy_id, "applied": False,
                        "reason": reason,
                        "stage": "operation_validation",
                        "trace": trace}
        trace.append({"step": 4, "name": "operation_validation",
                      "in_scope_entries": sorted(in_scope),
                      "operations_hash_checked": True})

        # Derivation 3 (pre): content hash of each affected
        # entry BEFORE the repair, affected entries only.
        affected = sorted({op.get("entry")
                           for op in remedy["operations"]
                           if op.get("entry") is not None})
        entries = world.get("entries") or {}
        pre_hashes = {}
        pre_images = {}
        for key in affected:
            if key in entries:
                pre_hashes[key] = ds.content_hash(entries[key])
                pre_images[key] = copy.deepcopy(entries[key])
            else:
                pre_hashes[key] = None

        # STEP 4 — sandbox apply. The live world is untouched.
        sandbox = copy.deepcopy(world)
        try:
            _apply_operations(sandbox, remedy["operations"])
        except (KeyError, ValueError, TypeError) as exc:
            store.append_attempt(failure_id, {
                "remedy_id": remedy_id,
                "outcome": "apply_failed",
                "detail": "%s: %s" % (type(exc).__name__, exc)})
            store.note_remedy_attempt(remedy_id, failure_id,
                                      "apply_failed")
            return {"outcome": "REPLAY_FAILED",
                    "failure_id": failure_id,
                    "remedy_id": remedy_id, "stage": "apply",
                    "trace": trace}
        trace.append({"step": 4, "name": "sandbox_apply",
                      "affected_entries": affected,
                      "pre_hashes": pre_hashes})

        # STEP 5 — replay proof: (a) original case, (b) remedy
        # battery, (c) regression for every touched surface.
        # WG3b (T3b): the proof's cross-check set is
        # SNAPSHOTTED at attempt start — the proof runs on a
        # copy of the sandbox whose EVIDENCE-class entries
        # (every entry that does not classify as plain
        # content: attestation residences, counter state, any
        # machinery surface) are restored to their
        # pre-attempt values, and evidence entries the
        # attempt itself created are dropped. A record the
        # repair writes during the attempt is therefore
        # excluded from its own certification evidence BY
        # CONSTRUCTION: the witness the replay reads is the
        # witness as it stood before the attempt began.
        proof_world = copy.deepcopy(sandbox)
        proof_entries = proof_world.get("entries") or {}
        evidence_frozen = []
        for key in sorted(set(list(entries) +
                              list(proof_entries))):
            if ds.classify_operation_target(key)[0] == "content":
                continue
            evidence_frozen.append(key)
            if key in entries:
                proof_entries[key] = copy.deepcopy(entries[key])
            else:
                proof_entries.pop(key, None)
        proof = self._replay_proof(failure, remedy, proof_world,
                                   cases)
        proof["evidence_snapshot"] = {
            "snapshotted_at_attempt_start": True,
            "evidence_entries_frozen": evidence_frozen,
            "attempt_created_records_excluded": True}
        trace.append({"step": 5, "name": "replay_proof",
                      "proof": proof})
        if not proof["passed"]:
            store.append_attempt(failure_id, {
                "remedy_id": remedy_id,
                "outcome": "replay_failed",
                "detail": proof["failed_stage"]})
            store.note_remedy_attempt(remedy_id, failure_id,
                                      "replay_failed")
            suspended_now = False
            if store.failed_attempts_on(remedy_id,
                                        failure_id) >= 2:
                store.suspend_remedy(
                    remedy_id, ds.REMEDY_CANDIDATE,
                    suspension={
                        "reason": "2 failed attempts on one "
                                  "failure (spec section 6)",
                        "failure_id": failure_id,
                        "suspended_to": ds.REMEDY_CANDIDATE,
                        "flagged_for": "builder"})
                suspended_now = True
            return {"outcome": "REPLAY_FAILED",
                    "failure_id": failure_id,
                    "remedy_id": remedy_id,
                    "failed_stage": proof["failed_stage"],
                    "proof": proof, "sandbox_discarded": True,
                    "remedy_suspended": suspended_now,
                    "trace": trace}

        # Proof passed — but a proof with no atomic root
        # seals nothing and closes nothing (resolution is by
        # sealed reference; there is no reference to give).
        # The live world is NOT touched in that case: the
        # end state is fully absent, never applied-and-open.
        if self.atomic_root is None:
            store.append_attempt(failure_id, {
                "remedy_id": remedy_id,
                "outcome": "seal_skipped",
                "detail": "proof passed but no atomic root is "
                          "configured; nothing applied, "
                          "nothing closed"})
            return {"outcome": "SEAL_SKIPPED",
                    "failure_id": failure_id,
                    "remedy_id": remedy_id, "applied": False,
                    "proof": proof, "trace": trace}

        # Proof passed: apply to the live world, then seal.
        _apply_operations(world, remedy["operations"])
        live_entries = world.get("entries") or {}
        post_hashes = {}
        for key in affected:
            post_hashes[key] = ds.content_hash(live_entries[key]) \
                if key in live_entries else None

        # STEP 6 — seal as ONE atomic entry.
        seal_entry = None
        if self.atomic_root is not None:
            try:
                seal_entry = self._seal(failure, remedy, trace,
                                        proof, pre_hashes,
                                        post_hashes)
            except Exception:
                # Roll the live world back to its pre-images:
                # an unsealed repair does not stand.
                for key in affected:
                    if key in pre_images:
                        live_entries[key] = pre_images[key]
                    else:
                        live_entries.pop(key, None)
                raise
        trace.append({"step": 6, "name": "seal",
                      "sealed": seal_entry is not None,
                      "entry_id": seal_entry["entry_id"]
                      if seal_entry else None,
                      "pre_hashes": pre_hashes,
                      "post_hashes": post_hashes})

        # WG3b: every post-seal store transition is
        # resolution-by-reference — each door is presented
        # with the sealed reference (entry id + content hash
        # + atomic root) and verifies it against the atomic
        # store before it writes. If any step of the sequence
        # fails after the seal is durable, reconciliation
        # (the same scan that runs on loop construction)
        # links the seal to the still-open failure before the
        # error is allowed to surface: the end state is
        # applied+closed, never mutated-and-open.
        seal_entry_id = seal_entry["entry_id"] if seal_entry \
            else None
        seal_proof = None
        if seal_entry is not None:
            seal_proof = {
                "entry_id": seal_entry_id,
                "content_hash": ds.content_hash(seal_entry),
                "atomic_root": self.atomic_root}
        try:
            # Standing after proof: a candidate on the
            # teaching / builder-fix path is now VERIFIED
            # (its first live proof). A verified remedy stays
            # verified.
            if remedy["status"] == ds.REMEDY_CANDIDATE:
                store.set_remedy_status(
                    remedy_id, ds.REMEDY_VERIFIED,
                    seal_proof=seal_proof)
            store.note_remedy_attempt(remedy_id, failure_id,
                                      "sealed")
            remedy_rec = store.get_remedy(remedy_id)
            if failure_id not in remedy_rec["closures"]:
                remedy_rec["closures"].append(failure_id)
                store._save()
            store.set_failure_status(
                failure_id, ds.FAILURE_CLOSED_BY_REMEDY,
                closure={"remedy_id": remedy_id,
                         "seal_entry_id": seal_entry_id,
                         "basis": "replay_proof",
                         "pre_hashes": pre_hashes,
                         "post_hashes": post_hashes},
                seal_proof=seal_proof)
            store.add_certified(
                remedy_id, failure_id, failure["signature_key"],
                seal_entry_id, self.calculus_generation,
                seal_proof=seal_proof)
            # Derivation 2: the verdict is recorded on BOTH
            # the failure record (above) and any outcome
            # record that opened it.
            opened_by = failure.get("opened_by") or {}
            if opened_by.get("kind") == "outcome_record":
                store.set_outcome_resolution(
                    opened_by["outcome_id"],
                    {"verdict": proof["original"]["verdict"],
                     "outcome": "CLOSED",
                     "seal_entry_id": seal_entry_id,
                     "failure_id": failure_id,
                     "remedy_id": remedy_id},
                    seal_proof=seal_proof)
        except Exception:
            self._reconcile()
            if store.get_failure(failure_id)["status"] != \
                    ds.FAILURE_CLOSED_BY_REMEDY:
                raise
        return {"outcome": "CLOSED", "failure_id": failure_id,
                "remedy_id": remedy_id,
                "seal_entry_id": seal_entry_id,
                "pre_hashes": pre_hashes,
                "post_hashes": post_hashes,
                "proof": proof, "trace": trace}

    def _replay_proof(self, failure, remedy, sandbox, cases):
        """Step 5's three parts, in order. Returns the proof
        record; 'passed' only when every part passes."""
        if self.replay_fn is None:
            return {"passed": False,
                    "failed_stage": "no_replay_function",
                    "original": None, "battery": [],
                    "regression": []}
        # (a) the original failing case, verdict computed fresh.
        # The case is named on the failure record; a failure
        # carrying no case name (an outcome-opened failure,
        # Derivation 2) takes the caller-supplied "__original__"
        # case instead.
        case_name = failure.get("case_name")
        original_case = cases.get(case_name) if case_name \
            else None
        if original_case is None:
            original_case = cases.get("__original__")
            case_name = case_name or "__original__"
        if original_case is None:
            return {"passed": False,
                    "failed_stage": "original_case_missing",
                    "original": None, "battery": [],
                    "regression": []}
        original = self.replay_fn(sandbox, original_case)
        original_rec = {"case": case_name,
                        "completed": bool(original.get("completed")),
                        "verdict": original.get("verdict")}
        if not original_rec["completed"]:
            return {"passed": False,
                    "failed_stage": "original_case",
                    "original": original_rec, "battery": [],
                    "regression": []}
        # (b) the remedy's battery set. WG3b (T6b):
        # verification REQUIRES a non-empty replay battery —
        # a proof with no battery cases is vacuous (parts (b)
        # and (c) both empty certify nothing), so it FAILS
        # here, at the battery stage, by construction.
        battery_names = (remedy.get("replay_set") or {}).get(
            "battery_cases") or []
        if not battery_names:
            return {"passed": False,
                    "failed_stage": "remedy_battery_empty",
                    "original": original_rec, "battery": [],
                    "regression": []}
        battery = []
        for name in battery_names:
            case = cases.get(name)
            if case is None:
                battery.append({"case": name, "completed": False,
                                "verdict": None,
                                "missing_case": True})
                return {"passed": False,
                        "failed_stage": "remedy_battery",
                        "original": original_rec,
                        "battery": battery, "regression": []}
            res = self.replay_fn(sandbox, case)
            battery.append({"case": name,
                            "completed": bool(res.get("completed")),
                            "verdict": res.get("verdict")})
            if not res.get("completed"):
                return {"passed": False,
                        "failed_stage": "remedy_battery",
                        "original": original_rec,
                        "battery": battery, "regression": []}
        # (c) regression batteries for every touched surface
        regression = []
        for surface in remedy.get("scope_surfaces") or []:
            fn = self.regression_fns.get(surface)
            if fn is None:
                regression.append({"surface": surface,
                                   "ok": False,
                                   "missing_battery": True})
                return {"passed": False,
                        "failed_stage": "regression",
                        "original": original_rec,
                        "battery": battery,
                        "regression": regression}
            res = fn(sandbox)
            ok = res.get("ok") if isinstance(res, dict) \
                else bool(res)
            regression.append({"surface": surface, "ok": bool(ok)})
            if not ok:
                return {"passed": False,
                        "failed_stage": "regression",
                        "original": original_rec,
                        "battery": battery,
                        "regression": regression}
        return {"passed": True, "failed_stage": None,
                "original": original_rec, "battery": battery,
                "regression": regression}

    def _seal(self, failure, remedy, trace, proof, pre_hashes,
              post_hashes):
        """Step 6's atomic entry via pmca_atomic_entries'
        public API: input and every step in the SAME entry
        (atomic-entry ruling)."""
        import pmca_atomic_entries as ae
        manifest = _code_manifest()
        environment = {
            "code_manifest_hash": _code_manifest_hash(),
            "code_manifest": manifest,
            "dictionary": {"configured": False, "db_hash": None,
                           "source": "off"},
        }
        input_rec = {
            "kind": "self_fix",
            "failure_id": failure["failure_id"],
            "failure_entry_ref": failure["entry_ref"],
            "signature": failure["signature"],
            "remedy_id": remedy["remedy_id"],
            "remedy_provenance": remedy["provenance"],
            "operations": remedy["operations"],
        }
        store = ae.EntryStore.open(self.atomic_root)
        try:
            with store.process(SELF_FIX_SURFACE, input_rec,
                               environment) as handle:
                for step in trace:
                    handle.step("self_fix_step", step)
                handle.step("seal_hashes", {
                    "affected_entries": sorted(pre_hashes),
                    "pre_hashes": pre_hashes,
                    "post_hashes": post_hashes,
                    "scoped_to_affected_only": True,
                    "hashes_never_copies": True})
                handle.outcome = {
                    "outcome": "SELF_FIX_SEALED",
                    "failure_id": failure["failure_id"],
                    "remedy_id": remedy["remedy_id"],
                    "verdict": proof["original"]["verdict"],
                    "pre_hashes": pre_hashes,
                    "post_hashes": post_hashes,
                }
                handle.ending_state = {}
            entry = handle.sealed_entry \
                if hasattr(handle, "sealed_entry") else None
            if entry is None:
                # ProcessHandle exposes the sealed entry via
                # seal(); the context manager sealed on exit —
                # read it back from the store by word-free seq.
                entries = store.replay()
                entry = entries[-1] if entries else None
            return entry
        finally:
            store.close()

    # -- rot (spec section 6) -------------------------------------------

    def _suspend_for_rot(self, remedy_id, reason):
        """The rot path (spec section 6 + WG3b): suspend the
        remedy SYSTEM-WIDE, list every closure it ever sealed
        for review and flag them — closures stand (sealed
        entries are sealed). Shared by check_rot and the
        loop's pre-apply operations-hash re-check."""
        remedy = self.store.get_remedy(remedy_id)
        closures = list(remedy.get("closures") or [])
        self.store.suspend_remedy(
            remedy_id, ds.REMEDY_SUSPENDED,
            suspension={"reason": reason,
                        "system_wide": True,
                        "closures_listed": closures})
        remedy_rec = self.store.get_remedy(remedy_id)
        remedy_rec["rot"] = {"closures_listed": closures,
                             "closures_stand": True,
                             "reason": reason}
        self.store._save()
        for failure_id in closures:
            failure = self.store.get_failure(failure_id)
            if failure is not None:
                failure["closure_review_flag"] = \
                    "remedy_suspended_rot:%s" % remedy_id
        self.store._save()
        return {"suspended_system_wide": True,
                "closures_listed": closures,
                "closures_stand": True}

    def check_rot(self, remedy_id, world, cases=None):
        """Replay a verified remedy's battery against the
        current world. On failure: the remedy is suspended
        SYSTEM-WIDE, every closure it ever sealed is listed
        for review and flagged — closures stand (sealed
        entries are sealed). WG3b (T6b): rot is also detected
        with NO battery replay at all — an operations-hash
        mismatch, or a VERIFIED remedy whose battery has gone
        empty, is rot on its face; neither is blind any more."""
        cases = dict(cases or {})
        remedy = self.store.get_remedy(remedy_id)
        if remedy is None:
            raise SelfFixRefused("no remedy %r" % (remedy_id,))
        battery = []
        rotted = False
        reason = "remedy rot: battery fails on replay " \
            "(spec section 6)"
        stored_ops_hash = remedy.get("operations_hash")
        if stored_ops_hash is not None and \
                ds.content_hash(remedy["operations"]) != \
                stored_ops_hash:
            battery.append({"case": "operations_hash",
                            "completed": False,
                            "ops_hash_mismatch": True})
            rotted = True
            reason = "remedy rot: operations hash mismatch " \
                "(WG3b T6b)"
        battery_names = (remedy.get("replay_set") or {}).get(
            "battery_cases") or []
        if remedy.get("status") == ds.REMEDY_VERIFIED and \
                not battery_names:
            battery.append({"case": "replay_battery",
                            "completed": False,
                            "battery_missing": True})
            rotted = True
            reason = "remedy rot: verified remedy carries an " \
                "empty replay battery (WG3b T6b)"
        for name in battery_names:
            case = cases.get(name)
            if case is None or self.replay_fn is None:
                battery.append({"case": name, "completed": False,
                                "missing": case is None})
                rotted = True
                continue
            res = self.replay_fn(copy.deepcopy(world), case)
            battery.append({"case": name,
                            "completed": bool(res.get("completed")),
                            "verdict": res.get("verdict")})
            if not res.get("completed"):
                rotted = True
        if not rotted:
            return {"remedy_id": remedy_id, "rotted": False,
                    "battery": battery}
        suspended = self._suspend_for_rot(remedy_id, reason)
        return {"remedy_id": remedy_id, "rotted": True,
                "battery": battery,
                "suspended_system_wide":
                    suspended["suspended_system_wide"],
                "closures_listed": suspended["closures_listed"],
                "closures_stand": True}


def _apply_operations(world, operations):
    """Apply a remedy's deterministic operations to a world
    (sandbox or live). Operations are content-level only:
    fill (set a field), link (append to a list field), derive
    (copy a stored field's value by rule). Anything else is
    refused — an unknown operation is a loud failure, never a
    silent skip."""
    entries = world.setdefault("entries", {})
    for op in operations:
        kind = op.get("op")
        entry_key = op.get("entry")
        field = op.get("field")
        if entry_key is None or field is None:
            raise ValueError("operation lacks entry/field: %r"
                             % (op,))
        entry = entries.setdefault(entry_key, {})
        if kind == "fill":
            entry[field] = copy.deepcopy(op.get("value"))
        elif kind == "link":
            current = entry.get(field)
            if current is None:
                current = []
                entry[field] = current
            if not isinstance(current, list):
                raise ValueError(
                    "link target is not a list: %r" % (op,))
            value = copy.deepcopy(op.get("value"))
            if value not in current:
                current.append(value)
        elif kind == "derive":
            source = op.get("from_field")
            if source not in entry:
                raise ValueError(
                    "derive source field missing: %r" % (op,))
            entry[field] = copy.deepcopy(entry[source])
        else:
            raise ValueError("unknown operation: %r" % (op,))
    return world
