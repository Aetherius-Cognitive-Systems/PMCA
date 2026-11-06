"""PMCA capability registry — its own first-class store standing
with the self (placement RULED by Jonathan, 2026-10-31, daily
record 2026-10-31: "It stands as its own store with the self";
SELF-FIX SPEC sections 4.7/8 pointer language). The self POINTS
to this store, never copies it (stored-once, pointer-only).

SPEC OF RECORD: WG4 registry spec (2026-11-01) as amended by its
check (fixes F-1..F-12 are binding on this build).

The registry is an append-only, hash-chained JSONL store
(registry.jsonl + registry_gaps.jsonl + registry.lock sidecar),
a sibling of graphs/ under the same storage root. It registers
one entry per scoped function (spec section 4.2 AST enumeration
over the Scope Manifest below). A checker runs store-vs-code in
both directions plus the self-pointer detector (F-5); mismatches
are recorded gap records, never silent, never repaired by the
checker. The checker never registers itself.

FENCES (spec section 7, standing): no scoped verdict/teaching
module imports this module; registration copies no function
bodies, stored facts, dictionary or seed content; an entry is
not evidence a function works.

Registered = exists and is described. Behaviour is proven only by
batteries.

Deterministic. Standard library only. No floats in records.
"""

import ast
import fcntl
import hashlib
import inspect
import json
import os
import subprocess
import sys

STORE_FORMAT = "pmca-capability-registry/1"
REGISTRY_ID = "capability_registry"
GENESIS = "GENESIS"
STANDING_LINE = ("Registered = exists and is described. "
                 "Behaviour is proven only by batteries.")
CHECKER_VERSION = "pmca-capability-registry/checker-1"

# Scope Manifest (spec section 4.1) AS AMENDED: F-1 adds
# pmca_morphology_finder (import closure: pmca_parse_guards
# imports it on the verdict path); F-2 adds the self-fix area as
# built by WG3 (pmca_self_fix + pmca_defect_store); the build
# wave's verdict/teaching-path modules join as recorded
# amendments (pmca_lineage_table as amended by WG1,
# pmca_verb_capable, pmca_forms_resolver, pmca_dossier_gate);
# the registry and its checker register themselves.
# F-12: values pin the default surfaces_served per module
# (closed vocabulary, spec section 1.1). Per-function deviation,
# registrar-recorded: a harness function (leaf name check,
# check_*, run_tests, run_suite, check_suite, run_checks, main,
# main_*) carries ["self_check"] instead of the module default.
MANIFEST = {
    "pmca_sentence_analyzer": ["verdict", "teaching"],
    "pmca_proof": ["verdict", "teaching"],
    "pmca_harm_calculus": ["verdict", "teaching"],
    "pmca_parse_guards": ["verdict"],
    "pmca_graphspace": ["verdict", "teaching"],
    "pmca_seed": ["verdict", "teaching"],
    "pmca_persistence": ["verdict", "persistence"],
    "pmca_dictionary": ["verdict", "dictionary"],
    "pmca_isa": ["verdict", "recognition"],
    "pmca_lineage_table": ["verdict", "self_fix"],
    "pmca_layer1": ["verdict", "teaching", "parsing"],
    "pmca_pools": ["verdict", "persistence"],
    "pmca_teaching": ["teaching"],
    "pmca_atomic_entries": ["verdict", "teaching", "storage"],
    "pmca_morphology_finder": ["verdict", "recognition"],
    "pmca_verb_capable": ["verdict", "dictionary"],
    "pmca_forms_resolver": ["verdict", "dictionary"],
    "pmca_dossier_gate": ["dictionary", "storage"],
    # FINDER BUILD (2026-11-01): the ONE FINDER joins as a
    # recorded amendment, in the same pattern as the WG2
    # modules above — it is consumed by the sentence analyzer
    # on the verdict path and is itself the parsing surface
    # that classifies every token in context.
    "pmca_finder": ["verdict", "parsing"],
    "pmca_defect_store": ["self_fix", "storage"],
    "pmca_self_fix": ["self_fix"],
    "pmca_capability_registry": ["registry"],
}

SURFACES = frozenset([
    "verdict", "teaching", "self_fix", "registry", "persistence",
    "recognition", "parsing", "seed", "dictionary", "storage",
    "self_check", "demonstration"])

GAP_KINDS = frozenset([
    "uncovered_function", "phantom_entry", "unresolvable_module",
    "not_callable", "out_of_manifest_entry", "stale_code_hash",
    "stale_locus", "missing_purpose", "missing_field",
    "generation_unstamped", "scoped_area_absent",
    "self_pointer_missing", "checker_entry_missing",
    "generation_invariant_broken"])

# Gap kinds whose target is the store itself: no module file
# exists to hash, so code_hash is null (F-9).
STORE_LEVEL_KINDS = frozenset([
    "generation_unstamped", "scoped_area_absent",
    "self_pointer_missing", "checker_entry_missing"])

SELF_POINTER = {
    "store_format": STORE_FORMAT,
    "store_location": REGISTRY_ID,
    "registry_id": REGISTRY_ID,
}

_LOOP_DESCRIPTION = {}


class RegistryError(Exception):
    """The registry store is unreadable, corrupt mid-file, or a
    write cannot be completed. Always loud, naming the file."""


class RegistryLocked(RegistryError):
    """Another live writer holds the registry lock. Loud
    refusal; contention is never silent (spec section 7.4)."""


def _canonical(obj):
    """The tree's one serialization: sorted keys, tight
    separators (pmca_atomic_entries._canonical precedent)."""
    try:
        return json.dumps(obj, sort_keys=True,
                          separators=(",", ":"),
                          ensure_ascii=True)
    except (TypeError, ValueError):
        return json.dumps(obj, sort_keys=True,
                          separators=(",", ":"),
                          ensure_ascii=True,
                          default=str)


def _canonical_bytes(obj):
    """Canonical bytes of an object, for hashing and seals."""
    return _canonical(obj).encode("utf-8")


def _file_hash(path):
    """SHA-256 of a file's bytes (the code_hash source)."""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _gap_id(kind, target_id, code_hash):
    """Deterministic gap id (spec section 4.4, F-9): first 16
    hex of SHA-256 of the canonical {kind, target_id,
    code_hash}; code_hash is null for store-level kinds."""
    key = hashlib.sha256(_canonical_bytes(
        {"kind": kind, "target_id": target_id,
         "code_hash": code_hash})).hexdigest()[:16]
    return "gap:" + key


def _is_harness(qualname):
    """True for a module's own check/main harness functions,
    which carry the self_check surface (spec section 4.2 rule
    4 + the F-12 deviation rule in this module's MANIFEST
    comment)."""
    leaf = qualname.rsplit(".", 1)[-1]
    return (leaf in ("check", "run_tests", "run_suite",
                     "check_suite", "run_checks")
            or leaf.startswith("check_")
            or leaf == "main" or leaf.startswith("main_"))


def surfaces_for(module, qualname):
    """The pinned surfaces_served for one scoped function:
    the module default from the MANIFEST, or ["self_check"]
    for harness functions."""
    if _is_harness(qualname):
        return ["self_check"]
    return list(MANIFEST[module])


def _parse_chain_file(path, payload_key):
    """Parse one hash-chained JSONL store file. Returns
    (records, truncated). A torn final line is truncation;
    corruption anywhere else, a broken chain, or a bad seal
    raises RegistryError naming the file."""
    if not os.path.exists(path):
        return [], False
    with open(path, "rb") as handle:
        raw = handle.read()
    lines = raw.split(b"\n")
    records = []
    truncated = False
    prev = GENESIS
    nonempty = [ln for ln in lines if ln.strip()]
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # torn only if it is the last non-empty line
            if line is nonempty[-1]:
                truncated = True
                break
            raise RegistryError(
                "registry store corrupt mid-file: %s line %d"
                % (path, i + 1))
        if not isinstance(rec, dict) or rec.get("seq") != len(records):
            raise RegistryError(
                "registry store corrupt: seq at %s line %d"
                % (path, i + 1))
        if rec.get("prev_hash") != prev:
            raise RegistryError(
                "registry store chain broken: %s line %d"
                % (path, i + 1))
        material = {k: v for k, v in rec.items() if k != "seal"}
        expect = hashlib.sha256(
            (prev + _canonical(material)).encode("utf-8")
        ).hexdigest()
        if rec.get("seal") != expect:
            raise RegistryError(
                "registry store seal mismatch: %s line %d"
                % (path, i + 1))
        prev = rec["seal"]
        records.append(rec)
    return records, truncated


class RegistryStore:
    """One append-only capability registry store: two
    hash-chained JSONL files plus a lock sidecar, under one
    directory. Single writer, loud refusal."""

    def __init__(self, directory):
        """Initialise a closed store handle for a directory;
        files are verified and the lock taken by open."""
        self.directory = os.path.abspath(directory)
        self.entries_path = os.path.join(
            self.directory, "registry.jsonl")
        self.gaps_path = os.path.join(
            self.directory, "registry_gaps.jsonl")
        self.lock_path = os.path.join(
            self.directory, "registry.lock")
        self._lock_fd = None
        self._closed = True
        self.entry_records = []
        self.gap_records = []
        self.truncated = {}

    @classmethod
    def open(cls, directory, read_only=False):
        """Open (creating if needed) the store at directory,
        verify both chains, and take the single-writer lock
        unless read_only. Returns the open store."""
        store = cls(directory)
        os.makedirs(store.directory, exist_ok=True)
        if not read_only:
            fd = os.open(store.lock_path,
                         os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                raise RegistryLocked(
                    "registry store is locked by another "
                    "writer: " + store.lock_path)
            store._lock_fd = fd
        store.entry_records, t1 = _parse_chain_file(
            store.entries_path, "entry")
        store.gap_records, t2 = _parse_chain_file(
            store.gaps_path, "record")
        store.truncated = {"registry.jsonl": t1,
                           "registry_gaps.jsonl": t2}
        store._closed = False
        return store

    def close(self):
        """Release the writer lock and close the store."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None
        self._closed = True

    def _require_writable(self):
        """Raise RegistryLocked when the store is not open
        for writing under this handle."""
        if self._closed or self._lock_fd is None:
            raise RegistryLocked(
                "registry store is not open for writing: "
                + self.directory)

    def _append(self, path, records, payload_key, payload):
        """Append one sealed, hash-chained record: allocate
        seq and seal in the same critical section as the
        write, fsync before acknowledgement."""
        self._require_writable()
        prev = records[-1]["seal"] if records else GENESIS
        rec = {"seq": len(records), "prev_hash": prev,
               payload_key: payload}
        rec["seal"] = hashlib.sha256(
            (prev + _canonical(rec)).encode("utf-8")).hexdigest()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(_canonical(rec) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        records.append(rec)
        return rec

    def append_entry(self, entry):
        """Append one entry version, allocating its seq and
        chain seal in the same critical section as the write.
        Returns the sealed record."""
        return self._append(self.entries_path,
                            self.entry_records, "entry", entry)

    def append_gap(self, gap):
        """Append one gap record to the gaps file. Returns
        the sealed record."""
        record = {"record_type": "gap"}
        record.update(gap)
        return self._append(self.gaps_path, self.gap_records,
                            "record", record)

    def append_gap_closure(self, gap_id, closed_by_check_seq,
                           basis):
        """Append a gap-closure record (the only way a gap
        closes; gap records are never edited or deleted)."""
        record = {"record_type": "gap_closure",
                  "gap_id": gap_id,
                  "closed_by_check_seq": closed_by_check_seq,
                  "basis": basis}
        return self._append(self.gaps_path, self.gap_records,
                            "record", record)


def _standing_map(entry_records):
    """id -> standing version record. The standing version is
    the highest-seq version; a standing version marked
    retracted means the id has NO standing entry (F-4:
    retraction is a state, not prose)."""
    latest = {}
    for rec in entry_records:
        entry = rec["entry"]
        cur = latest.get(entry["id"])
        if cur is None or rec["seq"] > cur["seq"]:
            latest[entry["id"]] = rec
    standing = {}
    for entry_id, rec in latest.items():
        if rec["entry"].get("retracted"):
            continue
        standing[entry_id] = rec
    return standing


def _gap_state(gap_records):
    """(open_gaps, closed_ids): gap records keyed by gap id,
    minus those closed by a later closure record."""
    gaps = {}
    closed = set()
    for rec in gap_records:
        record = rec["record"]
        if record.get("record_type") == "gap":
            gaps[record["id"]] = record
        elif record.get("record_type") == "gap_closure":
            closed.add(record["gap_id"])
    open_gaps = {gid: g for gid, g in gaps.items()
                 if gid not in closed}
    return open_gaps, closed


def load_registry(registry_root):
    """Load a registry store read-only (no lock, no writes):
    verify both chains and rebuild every index in memory.
    Returns a dict with standing entries, all versions, and
    gap state. The files are the record; the indexes are
    doors, rebuilt — never a second stored copy."""
    directory = os.path.abspath(registry_root)
    entry_records, t1 = _parse_chain_file(
        os.path.join(directory, "registry.jsonl"), "entry")
    gap_records, t2 = _parse_chain_file(
        os.path.join(directory, "registry_gaps.jsonl"),
        "record")
    standing = _standing_map(entry_records)
    open_gaps, closed = _gap_state(gap_records)
    return {
        "format": STORE_FORMAT,
        "registry_id": REGISTRY_ID,
        "directory": directory,
        "entry_records": entry_records,
        "gap_records": gap_records,
        "standing": standing,
        "open_gaps": open_gaps,
        "closed_gap_ids": sorted(closed),
        "truncated": {"registry.jsonl": t1,
                      "registry_gaps.jsonl": t2},
        "standing_line": STANDING_LINE,
    }


def standing_entry(registry, entry_id):
    """The standing entry dict for entry_id in a loaded
    registry, or None."""
    rec = registry["standing"].get(entry_id)
    return rec["entry"] if rec else None


def _tokens(text):
    """The alphanumeric/underscore tokens of a text,
    lowercased — the exact-token unit of purpose and limit
    search."""
    out = set()
    cur = []
    for ch in str(text).lower():
        if ch.isalnum() or ch == "_":
            cur.append(ch)
        elif cur:
            out.add("".join(cur))
            cur = []
    if cur:
        out.add("".join(cur))
    return out


def _search(registry, predicate):
    """Standing entries matching a predicate, in id order."""
    return [rec["entry"] for _id, rec in
            sorted(registry["standing"].items())
            if predicate(rec["entry"])]


def search_by_id(registry, entry_id):
    """Exact search by entry id; empty list when absent —
    never a near match."""
    entry = standing_entry(registry, entry_id)
    return [entry] if entry else []


def search_by_function(registry, name):
    """Exact search by qualname, or by bare function name
    across modules."""
    return _search(registry, lambda e: e["function"] == name
                   or e["function"].rsplit(".", 1)[-1] == name)


def search_by_purpose(registry, token):
    """Exact token match over purpose text."""
    tok = str(token).lower()
    return _search(registry,
                   lambda e: tok in _tokens(e["purpose"]))


def search_by_input(registry, name):
    """Exact search by parameter name or annotation string."""
    return _search(registry, lambda e: any(
        p["name"] == name or p["annotation"] == name
        for p in e["inputs"]["parameters"]))


def search_by_output(registry, annotation):
    """Exact search by returns-annotation string."""
    return _search(registry, lambda e:
                   e["outputs"]["returns_annotation"]
                   == annotation)


def search_by_limit(registry, token):
    """Exact token match over limits and hold_conditions."""
    tok = str(token).lower()
    return _search(registry, lambda e: any(
        tok in _tokens(text)
        for text in list(e["limits"])
        + list(e["hold_conditions"])))


def search_by_generation(registry, generation):
    """Exact search by generation value; None returns the
    null / generation-gap population."""
    if generation is None:
        return _search(registry,
                       lambda e: e["generation"] is None)
    return _search(registry,
                   lambda e: e["generation"] == generation)


def search_by_surface(registry, surface):
    """Exact search by surface (closed vocabulary)."""
    return _search(registry,
                   lambda e: surface in e["surfaces_served"])


def search_by_status(registry, status):
    """Search by EFFECTIVE status (derived, spec section
    1.2): gap-flagged iff an open gap references the id."""
    flagged = {g["target_id"]
               for g in registry["open_gaps"].values()}

    def effective(entry):
        return ("gap-flagged" if entry["id"] in flagged
                else "registered")
    return _search(registry,
                   lambda e: effective(e) == status)


def search_gaps(registry, kind=None, target_id=None,
                state="open"):
    """Search gap records by kind, by referenced entry id,
    and by open/closed state."""
    if state == "open":
        pool = list(registry["open_gaps"].values())
    else:
        closed = set(registry["closed_gap_ids"])
        pool = [rec["record"] for rec in registry["gap_records"]
                if rec["record"].get("record_type") == "gap"
                and rec["record"]["id"] in closed]
    out = []
    for gap in pool:
        if kind is not None and gap["kind"] != kind:
            continue
        if target_id is not None \
                and gap["target_id"] != target_id:
            continue
        out.append(gap)
    return sorted(out, key=lambda g: g["id"])


def enumerate_scoped_functions(tree_root):
    """The scoped function set, by the spec section 4.2 AST
    rule over the MANIFEST modules: module-level functions
    plus methods directly in a class body, in file order;
    dunders excluded except __init__; nested functions and
    nested-class functions excluded. Recomputed from the
    current files at every call — never cached."""
    out = []
    for module in sorted(MANIFEST):
        path = os.path.join(tree_root, module + ".py")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            src = handle.read()
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                out.append({
                    "module": module, "qualname": node.name,
                    "definition_line": node.lineno,
                    "docstring": ast.get_docstring(node),
                    "source_file": module + ".py"})
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if not isinstance(sub, (ast.FunctionDef,
                                            ast.AsyncFunctionDef)):
                        continue
                    if (sub.name.startswith("__")
                            and sub.name.endswith("__")
                            and sub.name != "__init__"):
                        continue
                    out.append({
                        "module": module,
                        "qualname": node.name + "." + sub.name,
                        "definition_line": sub.lineno,
                        "docstring": ast.get_docstring(sub),
                        "source_file": module + ".py"})
    return out


_REQUIRED_FIELDS = {
    "id": str, "kind": str, "module": str, "function": str,
    "locus": dict, "purpose": str, "inputs": dict,
    "outputs": dict, "limits": list, "hold_conditions": list,
    "surfaces_served": list, "provenance": dict,
    "status": str}


def _entry_problems(entry):
    """Field-shape problems of a standing entry (the
    missing_field check): a required field absent or of the
    wrong type. Returns a list of problem strings."""
    problems = []
    for field, typ in _REQUIRED_FIELDS.items():
        if field not in entry:
            problems.append("missing:" + field)
        elif not isinstance(entry[field], typ):
            problems.append("wrong_type:" + field)
    if "generation" not in entry or "generation_gap" not in entry:
        problems.append("missing:generation_pair")
    return problems


def register_entry(store, entry):
    """Registrar act: validate and append one entry version.
    A re-registration of the same id is a supersession (the
    new version's supersedes names the standing version's
    seq), never a second entry. Returns the sealed record."""
    problems = _entry_problems(entry)
    if problems:
        raise RegistryError(
            "entry refused, field problems: "
            + ",".join(problems))
    expect_id = "cap:%s:%s" % (entry["module"],
                               entry["function"])
    if entry["id"] != expect_id:
        raise RegistryError(
            "entry id %r does not match its locus id %r"
            % (entry["id"], expect_id))
    standing = _standing_map(store.entry_records)
    prior = standing.get(entry["id"])
    entry = dict(entry)
    entry["supersedes"] = prior["seq"] if prior else None
    return store.append_entry(entry)


def retract_entry(store, entry_id, reason):
    """Retraction as a STATE (F-4): append a superseding
    version of the standing entry with status "retracted"
    and retracted True, its purpose recording the retraction.
    There is no delete. After retraction the id has no
    standing entry, so Direction 1 reports it uncovered."""
    standing = _standing_map(store.entry_records)
    prior = standing.get(entry_id)
    if prior is None:
        raise RegistryError(
            "cannot retract: no standing entry " + entry_id)
    entry = json.loads(_canonical(prior["entry"]))
    entry["status"] = "retracted"
    entry["retracted"] = True
    entry["purpose"] = ("RETRACTED (WG4 demonstration): "
                        + reason + " Prior purpose: "
                        + entry["purpose"])
    entry["supersedes"] = prior["seq"]
    return store.append_entry(entry)


def register_loop_description(description):
    """Adapter for WG3's registration hook (F-3): WG4 calls
    SelfFixLoop.registration_hook with a callable; this
    callable receives capability_description() as data and
    files it as registrar provenance material for the loop's
    per-function entries. The description gets NO entry under
    its own id (pmca.self_fix_loop names no code locus and
    would be a phantom by construction). Returns the
    description filed."""
    if not isinstance(description, dict) \
            or description.get("id") != "pmca.self_fix_loop":
        raise RegistryError(
            "loop description refused: unexpected id %r"
            % (description.get("id")
               if isinstance(description, dict) else None,))
    _LOOP_DESCRIPTION.clear()
    _LOOP_DESCRIPTION.update(description)
    return description


def _purpose_from_docstring(docstring):
    """Purpose text from a function's own docstring: its
    first paragraph, whitespace-flattened (quoted source,
    provenance purpose_source "docstring")."""
    para = docstring.strip().split("\n\n")[0]
    return " ".join(para.split())


def build_entry(tree_root, item, registrar_purposes,
                generation_gap_id):
    """Author one registry entry for one enumerated function
    (the registrar path): purpose from the docstring when one
    exists, else from registrar_purposes (purpose_source
    "registrar" — a purpose is never invented silently; a
    function with neither is refused loudly here so the
    bootstrap cannot pass it over). Signature and annotations
    are read from the live code object by inspect — a
    description of the locus, never a call."""
    module = item["module"]
    qualname = item["qualname"]
    entry_id = "cap:%s:%s" % (module, qualname)
    if tree_root not in sys.path:
        sys.path.insert(0, tree_root)
    mod = __import__(module)
    obj = mod
    for part in qualname.split("."):
        obj = getattr(obj, part)
    try:
        signature = str(inspect.signature(obj))
    except (TypeError, ValueError):
        signature = "(...)"
    params = []
    try:
        sig = inspect.signature(obj)
        for name, par in sig.parameters.items():
            ann = par.annotation
            params.append({
                "name": name,
                "annotation": ("unannotated"
                               if ann is inspect.Parameter.empty
                               else str(ann))})
        ret = sig.return_annotation
        returns = ("unannotated"
                   if ret is inspect.Signature.empty
                   else str(ret))
    except (TypeError, ValueError):
        returns = "unannotated"
    docstring = item["docstring"]
    if docstring:
        purpose = _purpose_from_docstring(docstring)
        purpose_source = "docstring"
    elif entry_id in registrar_purposes:
        purpose = registrar_purposes[entry_id]
        purpose_source = "registrar"
    else:
        raise RegistryError(
            "registrar has no purpose for " + entry_id
            + " and the function has no docstring; refusing "
              "to invent one silently")
    limits = []
    hold_conditions = []
    description_source = None
    if module in ("pmca_self_fix", "pmca_defect_store"):
        desc = _LOOP_DESCRIPTION
        if desc:
            description_source = (
                "pmca_self_fix.capability_description() "
                "id=pmca.self_fix_loop (code's own data, "
                "filed via registration_hook adapter)")
            raw_limits = desc.get("limits", {})
            never = raw_limits.get("never_class", [])
            if module == "pmca_self_fix" and never:
                limits.append(
                    "never_class (from capability_description): "
                    + ", ".join(never))
                hold_conditions.append(
                    "A signature in the never-class is refused "
                    "before remedy matching (loop step 3); no "
                    "remedy outranks the never-class.")
            for key in sorted(raw_limits):
                if key in ("never_class",
                           "never_class_scope_surfaces"):
                    continue
                value = raw_limits[key]
                if isinstance(value, (str, bool, int)):
                    limits.append("%s: %s" % (key, value))
    if module == "pmca_defect_store":
        limits.append(
            "Never-class signatures and scope surfaces are "
            "unlistable by construction (NeverClassRefused).")
    path = os.path.join(tree_root, module + ".py")
    entry = {
        "id": entry_id,
        "kind": "capability",
        "module": module,
        "function": qualname,
        "locus": {"source_file": item["source_file"],
                  "definition_line": item["definition_line"]},
        "purpose": purpose,
        "inputs": {"signature": signature,
                   "parameters": params},
        "outputs": {"returns_annotation": returns,
                    "description": ""},
        "limits": limits,
        "hold_conditions": hold_conditions,
        "generation": None,
        "generation_gap": generation_gap_id,
        "surfaces_served": surfaces_for(module, qualname),
        "provenance": {
            "registered_by": "builder-build:wg4-step-2",
            "registered_in": "wg4-registry-spec.md as amended "
                             "by wg4-spec-check.md (F-1..F-12)",
            "purpose_source": purpose_source,
            "code_hash": _file_hash(path),
        },
        "status": "registered",
        "retracted": False,
        "supersedes": None,
    }
    if description_source:
        entry["provenance"]["description_source"] = \
            description_source
    return entry


def generation_gap_id():
    """The pinned id of the standing generation gap (F-9):
    derived from {kind, target, code_hash null} exactly as
    _gap_id derives every gap id."""
    return _gap_id("generation_unstamped", "registry", None)


def bootstrap_registry(registry_root, tree_root,
                       registrar_purposes):
    """Bootstrap seeding, in the spec section 4.5 order:
    (1) create the store; (2) register every manifest entry
    (the amended manifest, including this module's own);
    (3) append the generation gap; (4) return a summary. The
    self pointer (step 5) is written by the caller's
    stored-once amendment path; the first checker run (step
    6) is the caller's next act. Refuses to bootstrap over a
    non-empty store."""
    store = RegistryStore.open(registry_root)
    try:
        if store.entry_records or store.gap_records:
            raise RegistryError(
                "bootstrap refused: store is not empty: "
                + store.directory)
        gap_id = generation_gap_id()
        items = enumerate_scoped_functions(tree_root)
        count = 0
        for item in items:
            entry = build_entry(tree_root, item,
                                registrar_purposes, gap_id)
            register_entry(store, entry)
            count += 1
        store.append_gap({
            "id": gap_id,
            "kind": "generation_unstamped",
            "target_id": "registry",
            "module": None,
            "function": None,
            "code_hash": None,
            "detected_by": "bootstrap",
            "check_seq": 0,
            "details": (
                "No tree-wide generation stamp exists. "
                "Partial identities that exist: the deployed "
                "surfaces' head 2735d20 (not a stamp of this "
                "tree; never copied onto it); "
                "pmca_sentence_analyzer._code_manifest_hash "
                "(four modules, verdict-scoped); "
                "pmca_persistence save generations (state, "
                "caller-supplied); "
                "pmca_write_in_storage._file_generation "
                "(per-file); pmca_seed.SEED_VERSION "
                "(seed only). Per-entry precision is carried "
                "by provenance.code_hash. Closes only when a "
                "stamp mechanism is ruled and built AND a "
                "registrar pass stamps every standing entry."),
            "status": "open",
        })
        return {"registered": count,
                "generation_gap": gap_id,
                "standing_line": STANDING_LINE}
    finally:
        store.close()


_RESOLVER_SCRIPT = (
    "import importlib, json, sys\n"
    "items = json.load(sys.stdin)\n"
    "out = {}\n"
    "for module, qualname in items:\n"
    "    key = module + ':' + qualname\n"
    "    try:\n"
    "        obj = importlib.import_module(module)\n"
    "    except Exception as exc:\n"
    "        out[key] = {'status': 'unresolvable_module',\n"
    "                    'error': '%s: %s' % (type(exc).__name__,"
    " exc)}\n"
    "        continue\n"
    "    try:\n"
    "        for part in qualname.split('.'):\n"
    "            obj = getattr(obj, part)\n"
    "    except AttributeError as exc:\n"
    "        out[key] = {'status': 'phantom_entry',\n"
    "                    'error': str(exc)}\n"
    "        continue\n"
    "    ok = callable(obj)\n"
    "    out[key] = {'status': 'ok' if ok else 'not_callable',\n"
    "                'error': '' if ok else\n"
    "                'resolved object type: '\n"
    "                + type(obj).__name__}\n"
    "json.dump(out, sys.stdout)\n")


def _resolve_all(tree_root, pairs):
    """Direction 2 resolution in a subprocess (the teaching
    verification-subprocess precedent): import each module
    and walk the qualname by attribute. Attributes are
    resolved only — no scoped function is ever called."""
    proc = subprocess.run(
        [sys.executable, "-c", _RESOLVER_SCRIPT],
        input=json.dumps(pairs), capture_output=True,
        text=True, cwd=tree_root, timeout=600)
    if proc.returncode != 0:
        raise RegistryError(
            "resolver subprocess failed: "
            + (proc.stderr or "")[-500:])
    return json.loads(proc.stdout)


def check_registry(registry_root, tree_root):
    """The completeness check, store-vs-code in both
    directions plus the self-pointer detector (F-5).
    Appends gap records and gap closures to the gaps file
    only; never registers, supersedes, or edits an entry.
    Returns the check report dict (with exit_code 0/1; a
    store that cannot be read raises RegistryError, which
    run_checks maps to exit 2)."""
    registry_root = os.path.abspath(registry_root)
    tree_root = os.path.abspath(tree_root)
    store = RegistryStore.open(registry_root)
    try:
        loaded = load_registry(registry_root)
        standing = loaded["standing"]
        open_gaps = dict(loaded["open_gaps"])
        check_seq = 1 + max(
            [rec["record"].get("check_seq", 0)
             for rec in store.gap_records
             if rec["record"].get("record_type") == "gap"]
            + [0])
        module_hashes = {}
        for module in sorted(MANIFEST):
            path = os.path.join(tree_root, module + ".py")
            module_hashes[module] = (
                _file_hash(path) if os.path.exists(path)
                else None)
        enumerated = enumerate_scoped_functions(tree_root)
        enum_by_id = {}
        for item in enumerated:
            enum_by_id["cap:%s:%s" % (item["module"],
                                       item["qualname"])] = item
        opened = []
        closed = []

        def append_gap(kind, target_id, module, function,
                       code_hash, details):
            # F-10: standing-open dedupe on (kind, target_id).
            for gap in open_gaps.values():
                if gap["kind"] == kind \
                        and gap["target_id"] == target_id:
                    return gap["id"]
            gap_id = _gap_id(kind, target_id, code_hash)
            for gap in open_gaps.values():
                if gap["id"] == gap_id:
                    return gap_id
            gap = {"id": gap_id, "kind": kind,
                   "target_id": target_id, "module": module,
                   "function": function,
                   "code_hash": code_hash,
                   "detected_by": "checker " + CHECKER_VERSION,
                   "check_seq": check_seq, "details": details,
                   "status": "open"}
            store.append_gap(gap)
            open_gaps[gap_id] = gap
            opened.append(gap_id)
            return gap_id

        def close_gap(gap_id, basis):
            store.append_gap_closure(gap_id, check_seq, basis)
            open_gaps.pop(gap_id, None)
            closed.append(gap_id)

        # -- Direction 1: code -> store (coverage) --------
        uncovered = {}
        for entry_id, item in enum_by_id.items():
            if entry_id not in standing:
                uncovered[entry_id] = item
        for entry_id, item in sorted(uncovered.items()):
            if item["module"] == "pmca_capability_registry":
                # F-8: instead of uncovered_function.
                append_gap(
                    "checker_entry_missing", "registry",
                    item["module"], item["qualname"], None,
                    "A registry-module function has no "
                    "standing entry: " + entry_id
                    + ". The checker never registers itself.")
            else:
                append_gap(
                    "uncovered_function", entry_id,
                    item["module"], item["qualname"],
                    module_hashes[item["module"]],
                    "Enumerated by the section 4.2 AST rule "
                    "with no standing registry entry.")

        # -- Direction 2: store -> code (existence) -------
        pairs = []
        for entry_id, rec in sorted(standing.items()):
            entry = rec["entry"]
            if entry["module"] in MANIFEST:
                pairs.append([entry["module"],
                              entry["function"]])
        resolved = _resolve_all(tree_root, pairs) if pairs \
            else {}
        resolution_gap = {}
        for entry_id, rec in sorted(standing.items()):
            entry = rec["entry"]
            module = entry["module"]
            if module not in MANIFEST:
                append_gap(
                    "out_of_manifest_entry", entry_id, module,
                    entry["function"], None,
                    "Standing entry whose module is not in "
                    "the Scope Manifest.")
                resolution_gap[entry_id] = \
                    "out_of_manifest_entry"
                continue
            result = resolved.get(module + ":"
                                  + entry["function"],
                                  {"status": "phantom_entry",
                                   "error": "no resolver result"})
            if result["status"] != "ok":
                append_gap(
                    result["status"], entry_id, module,
                    entry["function"], module_hashes[module],
                    "Subprocess resolution failed: "
                    + result["status"] + " "
                    + result.get("error", ""))
                resolution_gap[entry_id] = result["status"]

        # -- Per-entry field/hash/locus/generation checks --
        for entry_id, rec in sorted(standing.items()):
            entry = rec["entry"]
            module = entry["module"]
            problems = _entry_problems(entry)
            if problems:
                append_gap(
                    "missing_field", entry_id, module,
                    entry["function"],
                    module_hashes.get(module),
                    "Field problems: " + ",".join(problems))
            gen = entry.get("generation")
            gen_gap = entry.get("generation_gap")
            if (gen is None) == (gen_gap is None):
                append_gap(
                    "generation_invariant_broken", entry_id,
                    module, entry["function"],
                    module_hashes.get(module),
                    "Exactly one of generation / "
                    "generation_gap must be non-null.")
            if entry.get("purpose", "") == "":
                append_gap(
                    "missing_purpose", entry_id, module,
                    entry["function"],
                    module_hashes.get(module),
                    "Standing entry carries an empty purpose.")
            if module in MANIFEST:
                if entry["provenance"].get("code_hash") \
                        != module_hashes[module]:
                    append_gap(
                        "stale_code_hash", entry_id, module,
                        entry["function"],
                        module_hashes[module],
                        "Module file hash differs from the "
                        "entry's provenance.code_hash; the "
                        "description may no longer describe "
                        "the code. Flagged, never "
                        "auto-refreshed.")
                item = enum_by_id.get(entry_id)
                if item and entry["locus"].get(
                        "definition_line") \
                        != item["definition_line"]:
                    append_gap(
                        "stale_locus", entry_id, module,
                        entry["function"],
                        module_hashes[module],
                        "Definition line moved from %s to %s "
                        "(advisory only)."
                        % (entry["locus"].get(
                            "definition_line"),
                           item["definition_line"]))

        # -- Direction 3: the self pointer (F-5) ----------
        storage_root = os.path.dirname(registry_root)
        pointer_path = os.path.join(
            storage_root, "graphs", "__self",
            "self_entry.json")
        pointer_ok = False
        pointer_detail = ""
        if not os.path.isfile(pointer_path):
            pointer_detail = ("No stored-once self record at "
                              + pointer_path)
        else:
            try:
                with open(pointer_path, "r",
                          encoding="utf-8") as handle:
                    record = json.load(handle)
                pointer = record.get(
                    "capability_registry_pointer")
                if pointer is None:
                    pointer_detail = ("Self record carries no "
                                      "capability_registry_"
                                      "pointer field.")
                elif pointer != SELF_POINTER:
                    pointer_detail = (
                        "Self record pointer does not resolve "
                        "to this registry: %r" % (pointer,))
                else:
                    pointer_ok = True
            except (ValueError, OSError) as exc:
                pointer_detail = ("Self record unreadable: "
                                  + str(exc))
        if not pointer_ok:
            append_gap("self_pointer_missing", "self", None,
                       None, None, pointer_detail)

        # -- Closures, by observation at this run ---------
        def still_obtains(gap):
            kind = gap["kind"]
            target = gap["target_id"]
            if kind in ("uncovered_function",):
                return target in uncovered
            if kind == "checker_entry_missing":
                return any(
                    item["module"]
                    == "pmca_capability_registry"
                    for item in uncovered.values())
            if kind in ("phantom_entry",
                        "unresolvable_module", "not_callable",
                        "out_of_manifest_entry"):
                return resolution_gap.get(target) == kind
            if kind == "stale_code_hash":
                rec = standing.get(target)
                return bool(rec) and rec["entry"][
                    "provenance"].get("code_hash") \
                    != module_hashes.get(
                        rec["entry"]["module"])
            if kind == "stale_locus":
                rec = standing.get(target)
                item = enum_by_id.get(target)
                return bool(rec and item) and rec["entry"][
                    "locus"].get("definition_line") \
                    != item["definition_line"]
            if kind == "missing_purpose":
                rec = standing.get(target)
                return bool(rec) and rec["entry"].get(
                    "purpose", "") == ""
            if kind == "missing_field":
                rec = standing.get(target)
                return bool(rec) and bool(
                    _entry_problems(rec["entry"]))
            if kind == "generation_invariant_broken":
                rec = standing.get(target)
                if not rec:
                    return False
                return (rec["entry"].get("generation")
                        is None) == (rec["entry"].get(
                            "generation_gap") is None)
            if kind == "self_pointer_missing":
                return not pointer_ok
            if kind == "generation_unstamped":
                return any(rec["entry"].get("generation")
                           is None
                           for rec in standing.values())
            if kind == "scoped_area_absent":
                return not all(
                    m in MANIFEST for m in
                    ("pmca_self_fix", "pmca_defect_store"))
            return True

        for gap_id, gap in sorted(list(open_gaps.items())):
            if gap_id in opened:
                continue
            if not still_obtains(gap):
                close_gap(gap_id,
                          "Mismatch no longer obtains at "
                          "check_seq %d." % check_seq)

        counts = {}
        for gap in open_gaps.values():
            counts[gap["kind"]] = counts.get(gap["kind"], 0) + 1
        report = {
            "format": STORE_FORMAT,
            "checker": CHECKER_VERSION,
            "check_seq": check_seq,
            "registry_root": registry_root,
            "tree_root": tree_root,
            "enumerated": len(enumerated),
            "standing_entries": len(standing),
            "open_gaps": len(open_gaps),
            "counts_by_kind": counts,
            "opened": opened,
            "closed": closed,
            "module_hashes": module_hashes,
            "exit_code": 1 if open_gaps else 0,
            "standing_line": STANDING_LINE,
        }
        return report
    finally:
        store.close()


def run_checks(registry_root, tree_root):
    """Run the checker and return its report; a checker that
    cannot complete (store unreadable / corrupt) returns a
    report with exit_code 2 instead of raising."""
    try:
        return check_registry(registry_root, tree_root)
    except RegistryError as exc:
        return {"format": STORE_FORMAT,
                "checker": CHECKER_VERSION,
                "error": str(exc), "exit_code": 2,
                "standing_line": STANDING_LINE}


def main(argv=None):
    """Runner: python3 pmca_capability_registry.py
    [registry_root] [tree_root]. Prints the check report as
    canonical JSON and exits with its exit code (0/1/2)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    here = os.path.dirname(os.path.abspath(__file__))
    registry_root = argv[0] if len(argv) > 0 else os.path.join(
        here, "storage", "capability_registry")
    tree_root = argv[1] if len(argv) > 1 else here
    report = run_checks(registry_root, tree_root)
    print(_canonical(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
