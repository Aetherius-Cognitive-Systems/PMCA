"""PMCA Stored-Once Storage - one permanent storage, many doors.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's
architecture.

Rulings (Jonathan, 2026-10-22 / 2026-10-23), which this
module implements as a proof slice behind Revision 2 of the
storage layout design (files/pmca-storage-layout-design-
2026-10-23.md):

- All things write to storage; the graphs are structured in
  the storage.
- The layout has coordinate data (entries and their
  coordinates - the addressing view), entry data (each entry
  as a separate entry including its relationship identifiers
  - the entry view), and relationship identifiers also stored
  separately (the identifier view).
- Everything can be searched, every way possible: storage is
  enterable from any fragment - coordinate, entry/word,
  identifier, target value, status, provenance, event, gap.
- STORED ONCE: "Yes, once stored is a fact. Nothing has
  ever stored a second time. It's all a permanent storage
  that the system can recall from." A fact is stored exactly
  once. The coordinate / entry / identifier organizations
  are recall paths (doors) onto the single stored facts,
  NOT copies: a door holds pointing information only (the
  target entry's identity, the identifier, the value, the
  storage location of the fact), never the fact itself, so
  copies that could disagree are impossible by construction.
  Internal identifiers carry values independent of layer,
  of entry identity, and of the subcoordinate itself, so a
  specific piece of information can be traversed using a
  single individual identifier.
- Canonical JSON artifacts remain DERIVED sealed exports
  (checkpoints, falsifiability anchors), not the residence.

Layout (PROPOSAL formats under Jonathan's ruled principles):

    <root>/
      graphs/
        language/graph.json              descriptor
        __self/graph.json                descriptor
        __self/self_entry.json           the self record (fact)
      entry_data/
        language/<word>.json             ENTRY DATA: the fact,
                                         once, one file per entry
      coordinate_data/
        language/L<layer>/<dim>/<index>.json
                                         DOOR: identities at the
                                         coordinate + the entry-
                                         data location of each
      identifier_data/
        <identifier>.json                DOOR (P2): dual-keyed.
                                         One use set per file,
                                         encoded twice:
                                         "by_source" (source
                                         word -> rows {target,
                                         graph, coord, state})
                                         and "by_target" (target
                                         value -> rows {source,
                                         graph, coord, state}).
                                         The two maps must parse
                                         to the SAME use set;
                                         the loader proves it
                                         and refuses by name on
                                         disagreement. Rows are
                                         pointing information
                                         only; coord is the
                                         SOURCE entry's
                                         coordinate, state the
                                         standing fact's status.

The FACT, for a subcoordinate, is the whole attestation
record (value, status, attestations, attested_events,
provenance, first/last seq) and it exists ONLY inside the
entry's entry-data file. A door row repeats the value as a
traversal key - pointing information that selects the fact;
it is not a second storage of the fact, which is the
attestation-bearing record. Coordinate records hold no part
of any fact at all: identities and locations only.

Write discipline (P2, replacing this slice's original blind
differing-record refusal): the stored state of an entry may
advance along TWO axes, and only along them.

- Axis 1, ENTRY state: BARE (no subcoordinate content) ->
  POPULATED (at least one subcoordinate). Entry identity
  (word, home graph, coordinate) is write-once.
- Axis 2, BINDING state, per identifier: CANDIDATE ->
  VERIFIED (attestation accretion or the explicit promote
  path), and VERIFIED -> SUPERSEDED only via an explicit
  teaching supersession. PROM-1 gate (2026-10-29): a fact
  may become VERIFIED only when its recorded provenances
  span >= 2 distinct provenance CLASSES - class is the
  "source" field of a provenance record, counted across
  the fact's standing "provenance" plus its
  "provenance_history", resolved through the governed
  source->lineage table (C-3; pmca_lineage_table) — a
  missing source is NO class, and spelling variants of
  one lineage are one class (D-2/D-3, fixed 2026-11-01;
  pre-fix text here counted a missing source as one
  unnamed class, None). The gate fires at BOTH doors that create
  a verified fact in this layout: initial storage (a fact
  born "verified" on a single class is refused) and the
  candidate -> verified accretion flip (refused as
  "verified_without_two_provenance_classes:<identifier>").
  WG2c D2 closure (2026-11-01): the initial-storage door
  additionally refuses a fact born "verified" whose own
  provenance history asserts a value conflicting with the
  fact's stored value (see _history_value_conflicts) — the
  class count alone does not verify a self-contradicting
  history. The refusal is this door's standing discipline
  (loud StoredOnceError, tree byte-unchanged), exactly as
  for the single-class born-verified refusal at the same
  door; the claimed status is never silently rewritten.
  It does NOT fire at load time and does NOT gate the
  supersession value-change path: an entry already stored
  verified is never re-judged (grandfathered). A
  supersession is recorded in the
  entry-level "superseded" list (append-only, items
  immutable): each item is {"identifier", "fact" (the old
  fact deep-copied with status rewritten to "superseded"),
  "superseded_seq", "provenance" (a dict carrying at least
  "source")}; the corrected binding takes the standing slot
  as "verified". Superseded bindings get NO door rows.

store_entry enforces the contract: an identical record
reports already_stored and writes nothing; a differing
record is validated BEFORE any write (governed fields:
subcoordinates, conflicts, superseded - any other top-level
field must be deep-equal). Permitted: adding an identifier
landing as CANDIDATE; CANDIDATE -> VERIFIED; attestation
accretion (attestations non-decreasing, attested_events
append-only, last_seq consistent); conflicts appends
(contested candidates, standing fact unmoved);
promoted_reason/promoted_seq added on promotion; a complete
teaching supersession. Refused, naming the violation and
the entry file, tree byte-unchanged: identifier or binding
deletion; a candidate's value change; a verified value
change without the complete supersession audit; any status
regression; shrinking or mutating conflicts / superseded
history; altering an immutable fact field (provenance,
first_seq, value, ...); any change to an ungoverned field.

All record writes are atomic (dot-prefixed temp file in the
same directory, flush + fsync, os.replace). The strict
loader validates every file and every cross-reference
between doors and facts, and raises StoredOnceError naming
the offending file. Searches resolve doors into the facts
they point at; a broken door raises, naming the door's file.

Deterministic. Stdlib only at module level; sibling modules
are imported inside functions and the check battery, never
at import time.
"""

import copy
import json
import os
import shutil
import tempfile

BASE_GRAPH = "language"
SELF_GRAPH = "__self"
SELF_RECORD_NAME = "self_entry.json"

ENTRY_FORMAT = "pmca-stored-once-entry/2"
COORD_FORMAT = "pmca-stored-once-coordinate/2"
IDENT_FORMAT = "pmca-stored-once-identifier/3"
GRAPH_FORMAT = "pmca-stored-once-graph/2"
SELF_FORMAT = "pmca-stored-once-self/2"

# WG4 AMENDMENT (2026-11-01, recorded in wg4-forge-build.md):
# the self record may carry ONE pointer field naming the
# capability registry store that stands with the self
# (placement RULED by Jonathan, 2026-10-31: "It stands as its
# own store with the self"). The pointer holds exactly three
# values — format, location relative to the storage root,
# identity — and no registry content. SELF_FORMAT stays /2:
# the field is optional-forever, so pre-registry records load
# unchanged and absence is a recorded gap in the registry's
# own checker (self_pointer_missing), not a load error.
CAPABILITY_REGISTRY_POINTER_KEYS = frozenset([
    "store_format", "store_location", "registry_id"])


def _validate_registry_pointer(pointer, path):
    """Validate the capability-registry pointer's shape when
    present: a dict with exactly the three pointer keys, all
    string values. Anything else is loud, naming the file."""
    if not isinstance(pointer, dict) \
            or set(pointer) != CAPABILITY_REGISTRY_POINTER_KEYS \
            or not all(isinstance(pointer[k], str)
                       for k in CAPABILITY_REGISTRY_POINTER_KEYS):
        raise StoredOnceError(
            "self record capability_registry_pointer has "
            "the wrong shape: " + path)
    return pointer

_LAYOUT_DIRS = ("graphs", "entry_data", "coordinate_data",
                "identifier_data")

# Keys that belong to a fact. No door file may contain any
# of them, anywhere: a door that carried one would be a copy.
_FACT_ONLY_KEYS = frozenset([
    "subcoordinates", "conflicts", "superseded", "status",
    "attestations",
    "attested_events", "provenance", "first_seq", "last_seq",
    "definition", "variables", "population"])


class StoredOnceError(Exception):
    """A stored-once layout is malformed, a record or door
    file fails validation, a door disagrees with the fact it
    points at, or a write cannot be completed. The message
    names the offending file wherever one exists."""


# ---------------------------------------------------------------------
# Canonical bytes + atomic per-record writes
# ---------------------------------------------------------------------

def _canon(obj):
    return json.dumps(obj, sort_keys=True,
                      separators=(",", ":"),
                      ensure_ascii=True)


def _canon_bytes(obj):
    return _canon(obj).encode("utf-8")


def _atomic_write(path, data):
    """Write data to path atomically: dot-prefixed temp in
    the same directory, flush + fsync, os.replace, directory
    fsync when available. No temp file is left behind."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory,
                                    prefix=".pmca-once-",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise StoredOnceError("cannot write record file: "
                              + str(path) + ": " + str(exc))
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


# ---------------------------------------------------------------------
# Paths. Locations stored in doors are root-relative,
# forward-slash paths; they are the door's pointing data.
# ---------------------------------------------------------------------

def _entry_relpath(graph, word):
    return "entry_data/%s/%s.json" % (graph, word)


def _entry_path(root, graph, word):
    return os.path.join(str(root), "entry_data", graph,
                        word + ".json")


def _coord_path(root, graph, coord):
    layer, dimension, index = coord
    return os.path.join(str(root), "coordinate_data", graph,
                        "L%d" % layer, dimension,
                        "%d.json" % index)


def _ident_relpath(identifier):
    return "identifier_data/%s.json" % identifier


def _ident_path(root, identifier):
    return os.path.join(str(root), "identifier_data",
                        identifier + ".json")


def _follow(root, location):
    return os.path.join(str(root), *location.split("/"))


def _descriptor(graph):
    return {"format": GRAPH_FORMAT, "graph": graph,
            "role": "self" if graph == SELF_GRAPH else "base"}


def _check_path_component(kind, value):
    """A graph name, word, or identifier becomes ONE path
    component under the layout root. Refuse, naming the
    value, anything that is not a single safe component:
    a non-string, the empty string, "." or "..", or any
    value carrying a path separator (Pack C, 2026-10-26;
    claims-02 Claim 8: the write path once accepted
    "../escaped" and "../idor", landing files outside
    their directories and poisoning the layout for every
    later reader). The write path must never create a
    file the read path will refuse or that lands outside
    its directory."""
    if not isinstance(value, str) or value == "" \
            or value in (".", "..") \
            or "/" in value or "\\" in value \
            or "\x00" in value:
        raise StoredOnceError(
            "refused: %s %r is not a safe storage path "
            "component" % (kind, value))


# ---------------------------------------------------------------------
# Reading + validation. Every failure names the file.
# ---------------------------------------------------------------------

def _read_record(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise StoredOnceError("cannot read record file: "
                              + str(path) + ": " + str(exc))
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoredOnceError("record file is not valid "
                              "JSON: " + str(path) + ": "
                              + str(exc))
    if not isinstance(record, dict):
        raise StoredOnceError("record file is not a JSON "
                              "object: " + str(path))
    return record


def _validate_entry_shape(word, entry, path):
    """The write-in's entry shape: a dict carrying a
    subcoordinates dict and a conflicts list."""
    if not isinstance(entry, dict):
        raise StoredOnceError("entry for word %r is not a "
                              "record: %s" % (word, path))
    if not isinstance(entry.get("subcoordinates"), dict):
        raise StoredOnceError("entry for word %r lacks a "
                              "subcoordinates record: %s"
                              % (word, path))
    if not isinstance(entry.get("conflicts"), list):
        raise StoredOnceError("entry for word %r lacks a "
                              "conflicts list: %s"
                              % (word, path))
    if "superseded" in entry \
            and not isinstance(entry.get("superseded"), list):
        raise StoredOnceError("entry for word %r carries a "
                              "superseded record that is not "
                              "a list: %s" % (word, path))
    for identifier, sub in entry["subcoordinates"].items():
        if not isinstance(sub, dict) or "value" not in sub:
            raise StoredOnceError(
                "subcoordinate %r on word %r is not an "
                "attestation record: %s"
                % (identifier, word, path))


def _load_entry_file(root, graph, word):
    """Read and validate one entry-data file. Returns
    (envelope, path). The envelope's record IS the fact."""
    path = _entry_path(root, graph, word)
    envelope = _read_record(path)
    if envelope.get("format") != ENTRY_FORMAT:
        raise StoredOnceError("entry file has wrong format: "
                              + path)
    if envelope.get("graph") != graph:
        raise StoredOnceError("entry file names the wrong "
                              "graph: " + path)
    if envelope.get("entry") != word:
        raise StoredOnceError("entry file names the wrong "
                              "entry: " + path)
    _validate_entry_shape(word, envelope.get("record"), path)
    return envelope, path


def _load_coordinate_file(root, graph, coord):
    """Read and validate one coordinate door file. Returns
    (record, path)."""
    path = _coord_path(root, graph, coord)
    record = _read_record(path)
    if record.get("format") != COORD_FORMAT:
        raise StoredOnceError("coordinate file has wrong "
                              "format: " + path)
    if record.get("graph") != graph:
        raise StoredOnceError("coordinate file names the "
                              "wrong graph: " + path)
    if record.get("coordinate") != [coord[0], coord[1],
                                    coord[2]]:
        raise StoredOnceError("coordinate file's coordinate "
                              "disagrees with its path: "
                              + path)
    entries = record.get("entries")
    locations = record.get("locations")
    if not isinstance(entries, list) \
            or not isinstance(locations, dict):
        raise StoredOnceError("coordinate file holds no "
                              "pointer record: " + path)
    if sorted(locations.keys()) != sorted(entries):
        raise StoredOnceError("coordinate file's locations "
                              "do not match its entries: "
                              + path)
    # Purity is STRUCTURAL (Pack C, 2026-10-26; Argon F1):
    # the envelope's top-level key set is exactly the five
    # pointer-envelope keys, and every entries member /
    # locations key / locations value is a string. An
    # earlier generation substring-matched the serialized
    # record for fact-key names, which matched word VALUES
    # and bricked the layout when an ordinary word (e.g.
    # "status") was stored; key sets cannot collide with
    # values, because words live only in values here.
    if sorted(record.keys()) != ["coordinate", "entries",
                                  "format", "graph",
                                  "locations"]:
        raise StoredOnceError(
            "coordinate file carries keys beyond the "
            "pointer envelope (format, graph, coordinate, "
            "entries, locations): " + path)
    for word in entries:
        if not isinstance(word, str):
            raise StoredOnceError(
                "coordinate file holds a non-string entry "
                "identity: " + path)
    for word, loc in locations.items():
        if not isinstance(word, str) \
                or not isinstance(loc, str):
            raise StoredOnceError(
                "coordinate file holds a non-string "
                "pointer: " + path)
    import pmca_graphspace as gs
    for word in entries:
        if gs.dist_address(word) != coord:
            raise StoredOnceError(
                "word %r is pointed at from a coordinate "
                "that is not its address: %s" % (word, path))
        if locations[word] != _entry_relpath(graph, word):
            raise StoredOnceError(
                "coordinate file points word %r at the "
                "wrong location: %s" % (word, path))
    return record, path


# ---------------------------------------------------------------------
# Identifier doors (P2): ONE use set, encoded under two keys.
# A use is the tuple (source, target, graph, coord, state):
# source entry word, standing fact's value, the source
# entry's home graph, the source entry's coordinate
# (layer, dimension, index), and the standing fact's status.
# by_source maps source -> rows {target, graph, coord,
# state}; by_target maps target -> rows {source, graph,
# coord, state}. Both maps encode the same set; a file whose
# two views disagree is a broken door.
# ---------------------------------------------------------------------

def _target_key(value):
    """The by_target map key for a target value: the value
    itself when it is a string (JSON object keys are
    strings), its canonical JSON otherwise."""
    return value if isinstance(value, str) else _canon(value)


def _coord_is_valid(coord):
    return isinstance(coord, list) and len(coord) == 3 \
        and isinstance(coord[0], int) \
        and not isinstance(coord[0], bool) \
        and isinstance(coord[1], str) \
        and isinstance(coord[2], int) \
        and not isinstance(coord[2], bool)


def _uses_from_by_source(envelope, path):
    """Parse the by_source map of an identifier door into
    the canonical use set, validating every row's shape.
    Returns (uses, n_rows)."""
    by_source = envelope.get("by_source")
    if not isinstance(by_source, dict):
        raise StoredOnceError("identifier file holds no "
                              "by_source map: " + path)
    uses = set()
    n_rows = 0
    for source in sorted(by_source):
        rows = by_source[source]
        if not isinstance(rows, list):
            raise StoredOnceError(
                "identifier file's by_source entry for %r "
                "is not a row list: %s" % (source, path))
        for row in rows:
            n_rows += 1
            if not isinstance(row, dict) \
                    or sorted(row.keys()) != ["coord",
                                              "graph",
                                              "state",
                                              "target"]:
                raise StoredOnceError(
                    "identifier file holds a by_source row "
                    "that is not pure pointing information "
                    "(target, graph, coord, state only): "
                    + path)
            if not _coord_is_valid(row["coord"]) \
                    or not isinstance(row["graph"], str) \
                    or not isinstance(row["state"], str):
                raise StoredOnceError(
                    "identifier file holds a by_source row "
                    "with a malformed pointer: " + path)
            uses.add((source, row["target"], row["graph"],
                      tuple(row["coord"]), row["state"]))
    if len(uses) != n_rows:
        raise StoredOnceError("identifier file's by_source "
                              "map holds a duplicate row: "
                              + path)
    return uses, n_rows


def _uses_from_by_target(envelope, path):
    """Parse the by_target map of an identifier door,
    validating every row's shape. Returns (uses, n_rows)
    where each use carries the target's DOOR KEY in place
    of the target value (JSON keys are strings: a string
    target is its own key; any other target is keyed by its
    canonical JSON - see _target_key)."""
    by_target = envelope.get("by_target")
    if not isinstance(by_target, dict):
        raise StoredOnceError("identifier file holds no "
                              "by_target map: " + path)
    uses = set()
    n_rows = 0
    for key in sorted(by_target):
        rows = by_target[key]
        if not isinstance(rows, list):
            raise StoredOnceError(
                "identifier file's by_target entry for %r "
                "is not a row list: %s" % (key, path))
        for row in rows:
            n_rows += 1
            if not isinstance(row, dict) \
                    or sorted(row.keys()) != ["coord",
                                              "graph",
                                              "source",
                                              "state"]:
                raise StoredOnceError(
                    "identifier file holds a by_target row "
                    "that is not pure pointing information "
                    "(source, graph, coord, state only): "
                    + path)
            if not _coord_is_valid(row["coord"]) \
                    or not isinstance(row["graph"], str) \
                    or not isinstance(row["state"], str):
                raise StoredOnceError(
                    "identifier file holds a by_target row "
                    "with a malformed pointer: " + path)
            uses.add((row["source"], key, row["graph"],
                      tuple(row["coord"]), row["state"]))
    if len(uses) != n_rows:
        raise StoredOnceError("identifier file's by_target "
                              "map holds a duplicate row: "
                              + path)
    return uses, n_rows


def _door_use_set(envelope, path):
    """The canonical use set of an identifier door: parse
    EACH map into a use set and prove them equal (the
    by_source set projected through _target_key must equal
    the by_target set exactly, row count included). A door
    whose two views disagree is refused, naming the file."""
    uses_source, n_source = _uses_from_by_source(envelope,
                                                 path)
    uses_target, n_target = _uses_from_by_target(envelope,
                                                 path)
    projected = {(source, _target_key(target), graph,
                  coord, state)
                 for (source, target, graph, coord, state)
                 in uses_source}
    if projected != uses_target or n_source != n_target:
        raise StoredOnceError(
            "identifier file's by_target view disagrees "
            "with its by_source view: " + path)
    return uses_source


def _envelope_from_uses(identifier, uses):
    """Encode a use set as the dual-keyed door envelope.
    Rows are sorted deterministically: by_source rows by
    (target, state, graph, coord); by_target rows by
    (source, state, graph, coord)."""
    by_source = {}
    by_target = {}
    for source, target, graph, coord, state in uses:
        by_source.setdefault(source, []).append(
            {"target": target, "graph": graph,
             "coord": list(coord), "state": state})
        by_target.setdefault(_target_key(target), []).append(
            {"source": source, "graph": graph,
             "coord": list(coord), "state": state})
    for source in by_source:
        by_source[source].sort(
            key=lambda r: (_canon(r["target"]), r["state"],
                           r["graph"], r["coord"]))
    for key in by_target:
        by_target[key].sort(
            key=lambda r: (r["source"], r["state"],
                           r["graph"], r["coord"]))
    return {"format": IDENT_FORMAT, "identifier": identifier,
            "by_source": by_source, "by_target": by_target}


def _sorted_uses(uses):
    return sorted(uses,
                  key=lambda u: (u[0], _canon(u[1]), u[2],
                                 u[3], u[4]))


def _load_identifier_file(root, identifier):
    """Read and validate one identifier door file. Returns
    (envelope, path), or (None, path) when the identifier
    has no stored uses. Both maps' row shapes are validated
    and the two views are proven to encode the same use
    set; any disagreement raises naming the file."""
    path = _ident_path(root, identifier)
    if not os.path.isfile(path):
        return None, path
    envelope = _read_record(path)
    if envelope.get("format") != IDENT_FORMAT:
        raise StoredOnceError("identifier file has wrong "
                              "format: " + path)
    if envelope.get("identifier") != identifier:
        raise StoredOnceError("identifier file names the "
                              "wrong identifier: " + path)
    # Structural purity (Pack C, 2026-10-26): the envelope
    # carries exactly its four keys; rows keep their exact
    # four-field validation in the _uses_from_* parsers.
    if sorted(envelope.keys()) != ["by_source", "by_target",
                                    "format", "identifier"]:
        raise StoredOnceError(
            "identifier file carries keys beyond the door "
            "envelope (format, identifier, by_source, "
            "by_target): " + path)
    _door_use_set(envelope, path)
    return envelope, path


def _resolve_use(root, identifier, use, door_path):
    """Follow one identifier door use into entry data and
    return the stored fact it points at. The use's value,
    status, coordinate, and graph are ALL checked against
    the stored fact and the source entry's address; any
    disagreement is a broken door, refused naming the
    door's file."""
    import pmca_graphspace as gs
    source, target, graph, coord, state = use
    envelope, _ = _load_entry_file(root, graph, source)
    sub = envelope["record"]["subcoordinates"].get(
        identifier)
    if sub is None:
        raise StoredOnceError(
            "identifier door points at an entry holding no "
            "%r fact: %s" % (identifier, door_path))
    if sub["value"] != target:
        raise StoredOnceError(
            "identifier door's target disagrees with the "
            "stored fact: " + door_path)
    if sub.get("status") != state:
        raise StoredOnceError(
            "identifier door's state disagrees with the "
            "stored fact: " + door_path)
    if gs.dist_address(source) != tuple(coord):
        raise StoredOnceError(
            "identifier door's coord disagrees with the "
            "source entry's address: " + door_path)
    return {"entry": source, "identifier": identifier,
            "value": target,
            "record": copy.deepcopy(sub)}


# ---------------------------------------------------------------------
# Build: a full materialization of a write-in store
# ---------------------------------------------------------------------

def build_layout(store, root):
    """Materialize a pmca_write_in store in the stored-once
    layout under root. Entry content is written exactly
    once, into entry data; coordinate data and identifier
    data are written as doors holding pointing information
    only. Any existing layout under root is removed first,
    so the tree always equals the store exactly. Returns a
    summary."""
    import pmca_graphspace as gs
    import pmca_write_in as wi
    if (not isinstance(store, dict) or "entries" not in store
            or "seq" not in store
            or wi.SELF_KEY not in store["entries"]):
        raise StoredOnceError("not a write-in store")
    self_entry = store["entries"][wi.SELF_KEY]
    # P1: gaps may be the typed index (dict) or a pre-P1
    # list; both are the self entry's stored gap facts.
    if (not isinstance(self_entry.get("encounters"), list)
            or not isinstance(self_entry.get("gaps"),
                              (list, dict))):
        raise StoredOnceError("store self entry lacks "
                              "encounters/gaps lists")
    # Path safety (Pack C, 2026-10-26): every word and
    # identifier in the store becomes a path component in
    # the build; validate all of them BEFORE anything is
    # removed or written, so a refused build leaves any
    # existing layout intact.
    for _w in sorted(store["entries"]):
        if _w == wi.SELF_KEY:
            continue
        _check_path_component("word", _w)
        _entry = store["entries"][_w]
        if isinstance(_entry, dict) and isinstance(
                _entry.get("subcoordinates"), dict):
            for _ident in sorted(
                    _entry["subcoordinates"]):
                _check_path_component("identifier", _ident)
    root = str(root)
    # WG4 AMENDMENT (2026-11-01): capture any capability-
    # registry pointer already on disk BEFORE the layout
    # directories are removed below, so a rebuild that does
    # not restate it preserves it instead of dropping it.
    _prior_pointer = None
    _prior_self_path = os.path.join(
        root, "graphs", SELF_GRAPH, SELF_RECORD_NAME)
    if os.path.isfile(_prior_self_path):
        try:
            with open(_prior_self_path, "r",
                      encoding="utf-8") as _fh:
                _prior_record = json.load(_fh)
            if isinstance(_prior_record, dict):
                _prior_pointer = _prior_record.get(
                    "capability_registry_pointer")
        except (ValueError, OSError):
            _prior_pointer = None
    for name in _LAYOUT_DIRS:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
    graphs_dir = os.path.join(root, "graphs")
    for graph in (BASE_GRAPH, SELF_GRAPH):
        _atomic_write(os.path.join(graphs_dir, graph,
                                   "graph.json"),
                      _canon_bytes(_descriptor(graph)))
    words = sorted(w for w in store["entries"]
                   if w != wi.SELF_KEY)
    # ENTRY DATA: the facts, once, one file per entry.
    for word in words:
        entry = store["entries"][word]
        _validate_entry_shape(word, entry,
                              _entry_path(root, BASE_GRAPH,
                                          word))
        envelope = {"format": ENTRY_FORMAT,
                    "graph": BASE_GRAPH, "entry": word,
                    "record": entry}
        _atomic_write(_entry_path(root, BASE_GRAPH, word),
                      _canon_bytes(envelope))
    # COORDINATE DATA (door): identities + locations only.
    groups = {}
    for word in words:
        groups.setdefault(gs.dist_address(word),
                          []).append(word)
    for coord in sorted(groups):
        members = sorted(groups[coord])
        record = {"format": COORD_FORMAT, "graph": BASE_GRAPH,
                  "coordinate": [coord[0], coord[1],
                                 coord[2]],
                  "entries": members,
                  "locations": {w: _entry_relpath(BASE_GRAPH,
                                                 w)
                                for w in members}}
        _atomic_write(_coord_path(root, BASE_GRAPH, coord),
                      _canon_bytes(record))
    # IDENTIFIER DATA (door, P2 dual-keyed): one use set
    # per identifier, encoded under by_source AND by_target.
    uses_by_ident = {}
    n_facts = 0
    for word in words:
        subs = store["entries"][word]["subcoordinates"]
        coord = gs.dist_address(word)
        for identifier in sorted(subs):
            uses_by_ident.setdefault(identifier,
                                     set()).add(
                (word, subs[identifier]["value"], BASE_GRAPH,
                 coord, subs[identifier].get("status")))
            n_facts += 1
    for identifier in sorted(uses_by_ident):
        envelope = _envelope_from_uses(
            identifier, uses_by_ident[identifier])
        _atomic_write(_ident_path(root, identifier),
                      _canon_bytes(envelope))
    # The self record: the system's own fact, stored once.
    self_record = {"format": SELF_FORMAT, "graph": SELF_GRAPH,
                   "seq": store["seq"],
                   "encounters": self_entry["encounters"],
                   "gaps": self_entry["gaps"]}
    # WG4 AMENDMENT (2026-11-01): the capability-registry
    # pointer survives rebuilds. Its source, in order: the
    # store's self entry (the caller-stated pointer), else
    # the pointer already on disk in the record this build
    # replaces (preserved, never silently dropped — F-5).
    _self_path = os.path.join(graphs_dir, SELF_GRAPH,
                              SELF_RECORD_NAME)
    _pointer = self_entry.get("capability_registry_pointer")
    if _pointer is None:
        _pointer = _prior_pointer
    if _pointer is not None:
        _validate_registry_pointer(_pointer, _self_path)
        self_record["capability_registry_pointer"] = _pointer
    _atomic_write(_self_path, _canon_bytes(self_record))
    return {"root": root,
            "graphs": [BASE_GRAPH, SELF_GRAPH],
            "entry_files": len(words),
            "coordinate_files": len(groups),
            "identifier_files": len(uses_by_ident),
            "facts": n_facts}


# ---------------------------------------------------------------------
# Strict load: validate everything, reassemble the store
# ---------------------------------------------------------------------

def _load_self_record(root):
    graph_dir = os.path.join(str(root), "graphs", SELF_GRAPH)
    path = os.path.join(graph_dir, SELF_RECORD_NAME)
    if not os.path.isfile(path):
        raise StoredOnceError("self graph has no self "
                              "record: " + path)
    record = _read_record(path)
    if record.get("format") != SELF_FORMAT:
        raise StoredOnceError("self record has wrong "
                              "format: " + path)
    if record.get("graph") != SELF_GRAPH:
        raise StoredOnceError("self record names the "
                              "wrong graph: " + path)
    if not isinstance(record.get("encounters"), list):
        raise StoredOnceError("self record lacks its "
                              "encounters list: " + path)
    if not isinstance(record.get("gaps"), (list, dict)):
        raise StoredOnceError("self record lacks its gaps "
                              "list/index: " + path)
    seq = record.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise StoredOnceError("self record lacks its event "
                              "counter: " + path)
    # WG4 AMENDMENT (2026-11-01): the capability-registry
    # pointer is optional; when present its shape is
    # validated loudly here.
    if "capability_registry_pointer" in record:
        _validate_registry_pointer(
            record["capability_registry_pointer"], path)
    return record


def _load_descriptor(root, graph):
    path = os.path.join(str(root), "graphs", graph,
                        "graph.json")
    if not os.path.isfile(path):
        raise StoredOnceError("graph has no descriptor: "
                              + path)
    record = _read_record(path)
    if record.get("format") != GRAPH_FORMAT:
        raise StoredOnceError("descriptor has wrong "
                              "format: " + path)
    if record.get("graph") != graph:
        raise StoredOnceError("descriptor names the "
                              "wrong graph: " + path)
    return record


def load_layout(root):
    """Reassemble a write-in store from the stored-once
    layout under root, validating EVERYTHING: descriptors,
    entry files, coordinate doors (each entry pointed at
    exactly once, from its own address), identifier doors
    (every row resolves to its fact; every stored fact is
    reachable through exactly one row), and the self
    record. The first failure raises StoredOnceError naming
    the offending file. This function performs no writes."""
    import pmca_write_in as wi
    root = str(root)
    graphs_dir = os.path.join(root, "graphs")
    if not os.path.isdir(graphs_dir):
        raise StoredOnceError("no graphs/ tree in storage "
                              "root: " + root)
    graphs = []
    for name in sorted(os.listdir(graphs_dir)):
        if name.startswith("."):
            continue
        if not os.path.isdir(os.path.join(graphs_dir,
                                          name)):
            raise StoredOnceError("unexpected file in "
                                  "graphs/: "
                                  + os.path.join(graphs_dir,
                                                 name))
        _load_descriptor(root, name)
        graphs.append(name)
    if SELF_GRAPH not in graphs:
        raise StoredOnceError("storage root has no self "
                              "graph: " + root)
    self_record = _load_self_record(root)
    entries = {}
    graph_by_word = {}
    for graph in graphs:
        if graph == SELF_GRAPH:
            continue
        # ENTRY DATA for this graph.
        entry_dir = os.path.join(root, "entry_data", graph)
        graph_words = {}
        if os.path.isdir(entry_dir):
            for fname in sorted(os.listdir(entry_dir)):
                if fname.startswith("."):
                    continue
                if not fname.endswith(".json"):
                    raise StoredOnceError(
                        "unexpected file in entry data: "
                        + os.path.join(entry_dir, fname))
                word = fname[:-len(".json")]
                envelope, _ = _load_entry_file(root, graph,
                                               word)
                if word in entries:
                    raise StoredOnceError(
                        "word %r is stored in two graphs; "
                        "second: %s"
                        % (word,
                           os.path.join(entry_dir, fname)))
                graph_words[word] = envelope["record"]
                entries[word] = envelope["record"]
                graph_by_word[word] = graph
        # COORDINATE DATA for this graph: coverage must be
        # exact - every entry pointed at once, nothing else.
        coord_root = os.path.join(root, "coordinate_data",
                                  graph)
        pointed = {}
        if os.path.isdir(coord_root):
            import pmca_graphspace as gs
            for layer_name in sorted(os.listdir(coord_root)):
                if layer_name.startswith("."):
                    continue
                import re
                m = re.match(r"^L(-?\d+)$", layer_name)
                layer_path = os.path.join(coord_root,
                                          layer_name)
                if m is None or not os.path.isdir(
                        layer_path):
                    raise StoredOnceError(
                        "unexpected entry in coordinate "
                        "data: " + layer_path)
                layer = int(m.group(1))
                for dim_name in sorted(
                        os.listdir(layer_path)):
                    if dim_name.startswith("."):
                        continue
                    dim_path = os.path.join(layer_path,
                                            dim_name)
                    if (len(dim_name) != 1
                            or dim_name not in gs.ALPHABET
                            or not os.path.isdir(dim_path)):
                        raise StoredOnceError(
                            "unexpected entry in "
                            "coordinate data: " + dim_path)
                    for fname in sorted(
                            os.listdir(dim_path)):
                        if fname.startswith("."):
                            continue
                        fm = re.match(r"^(-?\d+)\.json$",
                                      fname)
                        fpath = os.path.join(dim_path,
                                             fname)
                        if fm is None or not os.path.isfile(
                                fpath):
                            raise StoredOnceError(
                                "unexpected file in "
                                "coordinate data: "
                                + fpath)
                        coord = (layer, dim_name,
                                 int(fm.group(1)))
                        record, _ = \
                            _load_coordinate_file(
                                root, graph, coord)
                        for word in record["entries"]:
                            if word in pointed:
                                raise StoredOnceError(
                                    "word %r is pointed at "
                                    "from two coordinate "
                                    "files; second: %s"
                                    % (word, fpath))
                            pointed[word] = fpath
        if set(pointed) != set(graph_words):
            missing = sorted(set(graph_words)
                             - set(pointed))
            extra = sorted(set(pointed) - set(graph_words))
            raise StoredOnceError(
                "coordinate data for graph %r disagrees "
                "with entry data (unpointed: %s; pointed "
                "without entry: %s): %s"
                % (graph, missing, extra, coord_root))
    # IDENTIFIER DATA (P2 dual-keyed doors): the use set of
    # each door must equal the uses computed from entry data
    # EXACTLY - every use resolves to its fact (value,
    # status, coordinate, graph all checked), every stored
    # fact is reachable through exactly one use, and a
    # stored fact with no door is refused by name.
    import pmca_graphspace as gs
    expected = {}
    for word in sorted(entries):
        subs = entries[word]["subcoordinates"]
        graph = graph_by_word[word]
        coord = gs.dist_address(word)
        for identifier in sorted(subs):
            expected.setdefault(identifier, set()).add(
                (word, subs[identifier]["value"], graph,
                 coord, subs[identifier].get("status")))
    ident_dir = os.path.join(root, "identifier_data")
    seen_idents = set()
    if os.path.isdir(ident_dir):
        for fname in sorted(os.listdir(ident_dir)):
            if fname.startswith("."):
                continue
            if not fname.endswith(".json"):
                raise StoredOnceError("unexpected file in "
                                      "identifier data: "
                                      + os.path.join(
                                          ident_dir, fname))
            identifier = fname[:-len(".json")]
            envelope, path = _load_identifier_file(
                root, identifier)
            seen_idents.add(identifier)
            got = _door_use_set(envelope, path)
            want = expected.get(identifier, set())
            if got != want:
                raise StoredOnceError(
                    "identifier door disagrees with the "
                    "stored facts: " + path)
            for use in _sorted_uses(got):
                _resolve_use(root, identifier, use, path)
    for identifier in sorted(expected):
        if identifier not in seen_idents:
            raise StoredOnceError(
                "stored facts have no identifier door: "
                + _ident_path(root, identifier))
    out = dict(entries)
    _self_out = {"encounters": self_record["encounters"],
                 "gaps": self_record["gaps"]}
    # WG4 AMENDMENT (2026-11-01): a present pointer is carried
    # back into the reassembled self entry, so a load -> build
    # round trip preserves it (F-5).
    if "capability_registry_pointer" in self_record:
        _self_out["capability_registry_pointer"] = \
            self_record["capability_registry_pointer"]
    out[wi.SELF_KEY] = _self_out
    return {"seq": self_record["seq"], "entries": out}


# ---------------------------------------------------------------------
# Two-axis monotonic contract (P2): transition validation
# ---------------------------------------------------------------------

_GOVERNED_FIELDS = ("subcoordinates", "conflicts",
                    "superseded")
_MUTABLE_FACT_FIELDS = ("attestations", "attested_events",
                        "last_seq", "status")
# provenance_history is addable (a fact written before the
# write-in kept it gains the record on later accretion) and
# thereafter append-only, validated in _validate_accretion
# (Pack F+B, 2026-10-26).
_ADDABLE_FACT_KEYS = ("promoted_reason", "promoted_seq",
                      "provenance_history")


def _validate_transition(word, old, new, epath):
    """Validate a differing record for an already-stored
    entry against the two-axis monotonic contract BEFORE
    anything is written. Returns the deterministic list of
    transition strings for a permitted advance. Any
    violation raises StoredOnceError naming the violation
    and the entry file; the caller writes nothing."""

    def refuse(violation):
        raise StoredOnceError(
            "transition refused for entry %r: %s: %s"
            % (word, violation, epath))

    transitions = []

    # conflicts: append-only, existing items immutable.
    old_conf = old.get("conflicts") or []
    new_conf = new.get("conflicts") or []
    if not isinstance(new_conf, list) \
            or new_conf[:len(old_conf)] != old_conf:
        refuse("conflicts_not_append_only")
    if len(new_conf) > len(old_conf):
        transitions.append("conflicts_appended:%d"
                           % (len(new_conf) - len(old_conf)))

    # superseded: append-only, existing items immutable.
    old_sup = old.get("superseded") or []
    new_sup = new.get("superseded") or []
    if not isinstance(new_sup, list) \
            or new_sup[:len(old_sup)] != old_sup:
        refuse("superseded_history_mutated")
    sup_delta = new_sup[len(old_sup):]
    sup_by_ident = {}
    for item in sup_delta:
        if isinstance(item, dict):
            sup_by_ident.setdefault(item.get("identifier"),
                                    []).append(item)

    old_subs = old["subcoordinates"]
    new_subs = new["subcoordinates"]
    if not old_subs and new_subs:
        transitions.append("entry_axis:bare_to_populated")

    supersession_idents = set()
    for identifier in sorted(set(old_subs) | set(new_subs)):
        if identifier not in new_subs:
            refuse("identifier_deleted:%s" % identifier)
        if identifier not in old_subs:
            fact = new_subs[identifier]
            if not isinstance(fact, dict) \
                    or fact.get("status") != "candidate":
                refuse("new_identifier_not_candidate:%s"
                       % identifier)
            transitions.append(
                "identifier_added:%s:candidate" % identifier)
            continue
        old_fact = old_subs[identifier]
        new_fact = new_subs[identifier]
        if old_fact == new_fact:
            continue
        if not isinstance(new_fact, dict):
            refuse("fact_replaced:%s" % identifier)
        if new_fact.get("value") == old_fact.get("value"):
            _validate_accretion(identifier, old_fact,
                                new_fact, refuse, transitions)
            continue
        # The value moved: the ONLY permitted path is a
        # teaching supersession of a VERIFIED binding.
        if old_fact.get("status") == "candidate":
            refuse("candidate_value_changed:%s" % identifier)
        if old_fact.get("status") != "verified":
            refuse("value_changed_from_status_%s:%s"
                   % (old_fact.get("status"), identifier))
        if new_fact.get("status") != "verified":
            refuse("supersession_new_fact_not_verified:%s"
                   % identifier)
        items = sup_by_ident.get(identifier, [])
        if len(items) == 0:
            refuse("verified_value_changed_without_"
                   "supersession_audit:%s" % identifier)
        if len(items) > 1:
            refuse("supersession_audit_duplicated:%s"
                   % identifier)
        _validate_supersession_item(identifier, old_fact,
                                    items[0], refuse)
        supersession_idents.add(identifier)
        transitions.append(
            "binding_superseded:%s:%s->%s"
            % (identifier, _canon(old_fact.get("value")),
               _canon(new_fact.get("value"))))

    # Ungoverned top-level fields fail closed: deep-equal
    # or refused, naming the field. (Checked after the
    # governed subcoordinate pass, so a structural
    # violation - e.g. an identifier deletion - is the one
    # named when both are present.)
    for field in sorted(set(old) | set(new)):
        if field in _GOVERNED_FIELDS:
            continue
        if field not in old or field not in new \
                or old[field] != new[field]:
            refuse("ungoverned_field_changed:%s" % field)

    # A superseded append with no value change behind it
    # is spurious: refused.
    for identifier in sorted(sup_by_ident, key=repr):
        if identifier not in supersession_idents:
            refuse("superseded_append_without_value_change:"
                   "%s" % (identifier,))
    return transitions


def _provenance_classes(fact):
    """The distinct provenance CLASSES recorded on a fact
    (PROM-1, 2026-10-29), read through the GOVERNED
    source->lineage table (Candidacy Rule Set C-3, ruled
    2026-10-31; pmca_lineage_table, shared data): the
    classes of the fact's standing "provenance" (when it
    is a dict) and of each dict in its
    "provenance_history", with spelling variants of one
    lineage ("Webster" / "Webster 1913") counted as ONE
    class (Ledger D-2, fixed 2026-11-01) and a record
    with a missing or empty "source" counting as NO class
    (Ledger D-3, fixed 2026-11-01; this supersedes the
    pre-fix text here, under which a missing source
    counted as one unnamed class, None). Evidence strings
    are NOT classes."""
    import pmca_lineage_table
    if not isinstance(fact, dict):
        return set()
    records = []
    provenance = fact.get("provenance")
    if isinstance(provenance, dict):
        records.append(provenance)
    history = fact.get("provenance_history")
    if isinstance(history, list):
        records.extend(r for r in history
                       if isinstance(r, dict))
    return pmca_lineage_table.provenance_classes(
        records, context="pmca_stored_once._provenance_classes")


# Keys under which a provenance record may explicitly state
# the value it attests (WG2c D2, 2026-11-01). Ordinary
# write-in provenance records carry none of them ({source,
# evidence, sentence} only); a record that DOES state a
# value is making a checkable assertion about it.
_ASSERTED_VALUE_KEYS = ("value_asserted", "asserted_value",
                        "value", "target_value")


def _history_value_conflicts(fact):
    """The values a fact's own provenance history asserts
    AGAINST its stored value (WG2c D2 closure, 2026-11-01):
    the distinct explicitly-asserted values, in first-
    assertion order, found in the standing "provenance" or
    any "provenance_history" record under an asserted-value
    key (see _ASSERTED_VALUE_KEYS) that differ from the
    fact's "value" (compared by canonical JSON, so non-
    string values compare exactly). Two history records
    asserting different values from each other necessarily
    produce a hit here: at most one of them can equal the
    stored value, the other differs from it. Records
    asserting no value contribute nothing — the ordinary
    write-in history, which attests the stored value by
    construction, is never a conflict under this test."""
    if not isinstance(fact, dict):
        return []
    records = []
    provenance = fact.get("provenance")
    if isinstance(provenance, dict):
        records.append(provenance)
    history = fact.get("provenance_history")
    if isinstance(history, list):
        records.extend(r for r in history
                       if isinstance(r, dict))
    stored_key = _canon(fact.get("value"))
    out = []
    seen = set()
    for record in records:
        for key in _ASSERTED_VALUE_KEYS:
            if key not in record:
                continue
            asserted = record[key]
            akey = _canon(asserted)
            if akey != stored_key and akey not in seen:
                seen.add(akey)
                out.append(asserted)
            break
    return out


def _validate_accretion(identifier, old_fact, new_fact,
                        refuse, transitions):
    """Same identifier, same value: fact accretion
    integrity. Only attestations (non-decreasing),
    attested_events (append-only prefix), last_seq
    (non-decreasing, equal to the last event when events
    exist), status (candidate -> verified only),
    provenance_history (append-only prefix; Pack F+B,
    2026-10-26 — the write-in's per-fact record of its
    distinct attestation provenances, which grows exactly
    when a new distinct provenance attests), and the
    addable promoted_reason / promoted_seq keys may move.
    Everything else is immutable; a removed key is
    refused."""
    # PROM-1 gate (2026-10-29; D-1): a candidate ->
    # verified flip is checked FIRST, before any other
    # accretion validation: the new fact's recorded
    # provenances must span >= 2 distinct provenance
    # classes (see _provenance_classes). A bare status
    # flip at attestations=1, and the duplicate-event
    # variant whose records all sit under one class, die
    # here by name.
    if isinstance(old_fact, dict) \
            and isinstance(new_fact, dict) \
            and old_fact.get("status") == "candidate" \
            and new_fact.get("status") == "verified" \
            and len(_provenance_classes(new_fact)) < 2:
        refuse("verified_without_two_provenance_classes:%s"
               % identifier)
    for key in sorted(set(old_fact) - set(new_fact)):
        refuse("fact_key_removed:%s:%s" % (identifier, key))
    for key in sorted(set(new_fact) - set(old_fact)):
        if key not in _ADDABLE_FACT_KEYS:
            refuse("fact_key_added:%s:%s" % (identifier, key))
        if key == "provenance_history":
            transitions.append(
                "provenance_history_recorded:%s" % identifier)
        else:
            transitions.append("promotion_recorded:%s:%s"
                               % (identifier, key))
    for key in sorted(set(old_fact) & set(new_fact)):
        if key in _MUTABLE_FACT_FIELDS \
                or key == "provenance_history":
            continue
        if old_fact[key] != new_fact[key]:
            refuse("immutable_fact_field_changed:%s:%s"
                   % (identifier, key))
    # Type integrity (Pack C, 2026-10-26; Argon F8): the
    # counters must be strict ints on BOTH facts - bool is
    # an int subclass in Python, and a non-int (None, a
    # string) must refuse by name rather than slip past an
    # isinstance-gated comparison, as attestations=None
    # once did ("advanced", None written to disk).
    def _is_strict_int(v):
        return isinstance(v, int) \
            and not isinstance(v, bool)

    old_att = old_fact.get("attestations")
    new_att = new_fact.get("attestations")
    if not _is_strict_int(old_att) \
            or not _is_strict_int(new_att):
        refuse("attestations_not_int:%s" % identifier)
    if new_att < old_att:
        refuse("attestations_decreased:%s" % identifier)
    old_events = old_fact.get("attested_events")
    new_events = new_fact.get("attested_events")
    if not isinstance(new_events, list) \
            or not isinstance(old_events, list) \
            or not all(_is_strict_int(x) for x in new_events) \
            or not all(_is_strict_int(x) for x in old_events) \
            or new_events[:len(old_events)] != old_events:
        refuse("attested_events_not_append_only:%s"
               % identifier)
    # Provenance history (Pack F+B, 2026-10-26): when both
    # facts carry it, the new must be an append-only
    # extension of the old — a distinct provenance, once
    # recorded as attested, is history. (A new fact GAINING
    # the key over an old fact that lacks it is the addable
    # case above; an old fact LOSING it is a key removal,
    # refused before this point.)
    old_hist = old_fact.get("provenance_history")
    new_hist = new_fact.get("provenance_history")
    if old_hist is not None and new_hist is not None:
        if not isinstance(old_hist, list) \
                or not isinstance(new_hist, list) \
                or new_hist[:len(old_hist)] != old_hist:
            refuse("provenance_history_not_append_only:%s"
                   % identifier)
        if len(new_hist) > len(old_hist):
            transitions.append(
                "provenance_history_appended:%s" % identifier)
    old_seq = old_fact.get("last_seq")
    new_seq = new_fact.get("last_seq")
    if not _is_strict_int(old_seq) \
            or not _is_strict_int(new_seq):
        refuse("last_seq_not_int:%s" % identifier)
    if new_seq < old_seq:
        refuse("last_seq_decreased:%s" % identifier)
    if new_events and new_seq != new_events[-1]:
        refuse("last_seq_disagrees_with_last_event:%s"
               % identifier)
    old_status = old_fact.get("status")
    new_status = new_fact.get("status")
    if new_status != old_status:
        if old_status == "candidate" \
                and new_status == "verified":
            transitions.append("binding_verified:%s"
                               % identifier)
        else:
            refuse("status_transition_refused:%s:%s->%s"
                   % (identifier, old_status, new_status))
    if len(new_events) > len(old_events) \
            or new_att != old_att:
        transitions.append("attestations_appended:%s"
                           % identifier)


def _validate_supersession_item(identifier, old_fact, item,
                                refuse):
    """One appended superseded item for a value change:
    it must carry the identifier, the old fact deep-copied
    with ONLY its status rewritten to "superseded", an
    integer superseded_seq, and a provenance dict carrying
    a "source" key. A missing or malformed piece is
    refused, naming the piece."""
    if not isinstance(item, dict):
        refuse("supersession_audit_malformed:%s" % identifier)
    if item.get("identifier") != identifier:
        refuse("supersession_audit_identifier_mismatch:%s"
               % identifier)
    if "fact" not in item:
        refuse("supersession_audit_missing:fact:%s"
               % identifier)
    expected = copy.deepcopy(old_fact)
    expected["status"] = "superseded"
    if item["fact"] != expected:
        refuse("superseded_fact_altered:%s" % identifier)
    if "superseded_seq" not in item:
        refuse("supersession_audit_missing:superseded_seq:"
               "%s" % identifier)
    if not isinstance(item["superseded_seq"], int) \
            or isinstance(item["superseded_seq"], bool):
        refuse("supersession_audit_malformed:superseded_seq:"
               "%s" % identifier)
    if "provenance" not in item:
        refuse("supersession_audit_missing:provenance:%s"
               % identifier)
    prov = item["provenance"]
    if not isinstance(prov, dict) or "source" not in prov:
        refuse("supersession_audit_missing:provenance.source:"
               "%s" % identifier)


def _door_uses(root, identifier):
    """Load one identifier door and return (path, use set),
    with the door fully validated (format, envelope keys,
    both views parsed and proven to encode the same use
    set). A door with no file yields an empty use set. A
    malformed door raises StoredOnceError naming it -
    store_entry calls this in its pre-validation phase,
    before any write (Pack C, 2026-10-26)."""
    ipath = _ident_path(root, identifier)
    ienv, _ = _load_identifier_file(root, identifier)
    uses = set()
    if ienv is not None:
        uses = _door_use_set(ienv, ipath)
    return ipath, uses


def _write_identifier_door(root, identifier, uses):
    """Publish one identifier door's use set: the envelope
    re-encoded in the dual-key format and written
    atomically; an emptied door file is removed."""
    ipath = _ident_path(root, identifier)
    if not uses:
        if os.path.isfile(ipath):
            os.unlink(ipath)
        return
    _atomic_write(ipath, _canon_bytes(
        _envelope_from_uses(identifier, uses)))


def _update_identifier_door(root, graph, word, identifier,
                              fact, preloaded=None):
    """Rewrite one identifier door after a store: drop this
    source entry's uses (in this graph) for the identifier,
    then add the use for the given standing fact (None
    removes the entry's uses; a door with no uses left is
    removed). Doors are re-encoded from the use set in the
    dual-key format. `preloaded`, when given, is the door's
    use set as loaded by _door_uses during store_entry's
    pre-validation phase; the door is then not re-read, so
    the write phase performs no validation reads that
    could fail after earlier writes have landed."""
    import pmca_graphspace as gs
    _check_path_component("identifier", identifier)
    if preloaded is None:
        _, uses = _door_uses(root, identifier)
    else:
        uses = set(preloaded)
    uses = {u for u in uses
            if not (u[0] == word and u[2] == graph)}
    if fact is not None:
        uses.add((word, fact["value"], graph,
                  gs.dist_address(word), fact.get("status")))
    _write_identifier_door(root, identifier, uses)


# ---------------------------------------------------------------------
# Write-once: storing one entry's fact into an existing layout
# ---------------------------------------------------------------------

def _require_graph(root, graph):
    graph_dir = os.path.join(str(root), "graphs", graph)
    if not os.path.isdir(graph_dir):
        raise StoredOnceError("no such graph in storage: "
                              + graph_dir)
    _load_descriptor(root, graph)


def store_entry(root, graph, word, record):
    """Store one entry's fact (its write-in record) into an
    existing layout, with its doors, under the two-axis
    monotonic contract (see the module docstring):

    - no entry file yet: initial storage - write the fact
      into entry data (once), then add the coordinate door
      pointer and the identifier door uses (dual-key
      format); outcome "stored".
    - an identical record already stored: NOTHING is
      written; outcome "already_stored". A fact is never
      stored a second time.
    - a different record already stored: the transition is
      validated BEFORE any write; a violation is refused
      with StoredOnceError naming the violation and the
      entry's file, tree byte-unchanged. A permitted
      advance writes the entry file, updates the
      identifier doors for every identifier in old U new
      subcoordinates, and returns outcome "advanced" with
      the deterministic "transitions" list.

    Pack C (2026-10-26): the graph, word, and identifiers
    are validated as safe single path components before
    anything is read or written, and every door the store
    will rewrite (plus the coordinate door, on initial
    storage) is pre-loaded and fully validated BEFORE the
    first write - a malformed door refuses the store,
    naming the door, with the tree byte-unchanged.

    Returns a placement report."""
    import pmca_graphspace as gs
    root = str(root)
    # Path safety first (Pack C, 2026-10-26): the graph,
    # the word, and every identifier the write could touch
    # are single path components, validated before anything
    # is read or written.
    _check_path_component("graph", graph)
    _check_path_component("word", word)
    _require_graph(root, graph)
    epath = _entry_path(root, graph, word)
    _validate_entry_shape(word, record, epath)
    for identifier in sorted(record["subcoordinates"]):
        _check_path_component("identifier", identifier)
    if os.path.isfile(epath):
        envelope, _ = _load_entry_file(root, graph, word)
        old_record = envelope["record"]
        if old_record == record:
            return {"outcome": "already_stored",
                    "word": word, "path": epath}
        transitions = _validate_transition(word, old_record,
                                           record, epath)
        idents = sorted(
            set(old_record["subcoordinates"])
            | set(record["subcoordinates"]))
        for identifier in idents:
            _check_path_component("identifier", identifier)
        # Transactional door phase (Pack C; Argon item 4):
        # pre-load and fully validate EVERY identifier door
        # this store will rewrite, BEFORE the entry file is
        # written. A malformed door refuses here, naming
        # the door, with the tree byte-unchanged - the
        # write phase below performs no validation reads.
        preloaded = {}
        for identifier in idents:
            _, preloaded[identifier] = _door_uses(
                root, identifier)
        new_envelope = {"format": ENTRY_FORMAT,
                        "graph": graph, "entry": word,
                        "record": copy.deepcopy(record)}
        _atomic_write(epath, _canon_bytes(new_envelope))
        for identifier in idents:
            _update_identifier_door(
                root, graph, word, identifier,
                record["subcoordinates"].get(identifier),
                preloaded=preloaded[identifier])
        return {"outcome": "advanced", "word": word,
                "path": epath, "transitions": transitions}
    # Initial storage: the same transactional terms - the
    # coordinate door and every identifier door are
    # pre-loaded and validated BEFORE the entry file (the
    # first write) lands.
    # PROM-1 gate (2026-10-29; D-2): no fact is BORN
    # verified on a single provenance class. This check
    # runs on the initial-storage path only, after
    # _validate_entry_shape (above) and before any write;
    # entries already stored verified are never re-judged
    # here or at load time (grandfathered).
    for identifier in sorted(record["subcoordinates"]):
        fact = record["subcoordinates"][identifier]
        if isinstance(fact, dict) \
                and fact.get("status") == "verified" \
                and len(_provenance_classes(fact)) < 2:
            raise StoredOnceError(
                "subcoordinate %r on word %r is born "
                "verified on a single provenance class: %s"
                % (identifier, word, epath))
        # WG2c D2 closure (2026-11-01): the same standard at
        # the same door — a fact born "verified" whose own
        # provenance history asserts a conflicting value is
        # refused by name, before any write, however many
        # classes its history spans (the checker's beetle:
        # stored value "insect", history asserting "bird"
        # under one of its two classes, conflicts []). The
        # discipline chosen is this module's standing one
        # for its doors — loud refusal (StoredOnceError,
        # tree byte-unchanged), as in the single-class
        # refusal above and every _validate_transition
        # refusal — NOT a silent downgrade to candidate,
        # which would rewrite the caller's claimed status
        # without a record the caller ever made.
        if isinstance(fact, dict) \
                and fact.get("status") == "verified":
            conflicts = _history_value_conflicts(fact)
            if conflicts:
                raise StoredOnceError(
                    "subcoordinate %r on word %r is born "
                    "verified with a value conflict in its "
                    "own provenance history "
                    "(born_verified_history_value_conflict:%s; "
                    "history asserts %r against stored "
                    "value %r): %s"
                    % (identifier, word, identifier, conflicts,
                       fact.get("value"), epath))
    coord = gs.dist_address(word)
    cpath = _coord_path(root, graph, coord)
    if os.path.isfile(cpath):
        crec, _ = _load_coordinate_file(root, graph, coord)
    else:
        crec = {"format": COORD_FORMAT, "graph": graph,
                "coordinate": [coord[0], coord[1], coord[2]],
                "entries": [], "locations": {}}
    preloaded = {}
    for identifier in sorted(record["subcoordinates"]):
        _, preloaded[identifier] = _door_uses(root,
                                              identifier)
    envelope = {"format": ENTRY_FORMAT, "graph": graph,
                "entry": word,
                "record": copy.deepcopy(record)}
    _atomic_write(epath, _canon_bytes(envelope))
    # Coordinate door: add this word's pointer.
    if word not in crec["entries"]:
        crec["entries"] = sorted(crec["entries"] + [word])
        crec["locations"][word] = _entry_relpath(graph, word)
        _atomic_write(cpath, _canon_bytes(crec))
    # Identifier doors (dual-key format): one use per
    # stored subcoordinate.
    for identifier in sorted(record["subcoordinates"]):
        _update_identifier_door(
            root, graph, word, identifier,
            record["subcoordinates"][identifier],
            preloaded=preloaded[identifier])
    return {"outcome": "stored", "word": word, "path": epath,
            "coordinate": coord}


# ---------------------------------------------------------------------
# Searches: enter from any fragment, resolve to the facts
# ---------------------------------------------------------------------

def search_by_coordinate(root, graph, coord):
    """Coordinate data entry point: follow the coordinate's
    door into entry data. Returns {word: entry record} for
    every entry stored at the coordinate ({} when the
    coordinate is unoccupied). The returned records ARE
    the stored facts, read from their one home."""
    root = str(root)
    path = _coord_path(root, graph, coord)
    if not os.path.isfile(path):
        return {}
    record, _ = _load_coordinate_file(root, graph, coord)
    out = {}
    for word in record["entries"]:
        envelope, _ = _load_entry_file(root, graph, word)
        out[word] = copy.deepcopy(envelope["record"])
    return out


def search_by_word(root, word, graph=BASE_GRAPH):
    """Entry data entry point: the entry's own record, or
    None when the word is not stored. (Entry data is keyed
    by the entry itself; the search also succeeds from the
    coordinate door because the address is a function of
    the word.)"""
    root = str(root)
    if not os.path.isfile(_entry_path(root, graph, word)):
        return None
    envelope, _ = _load_entry_file(root, graph, word)
    return copy.deepcopy(envelope["record"])


def search_by_identifier(root, identifier):
    """Identifier data entry point: every stored fact under
    this identifier, resolved through the door's use set
    into entry data. Returns a list of {entry, identifier,
    value, record} ([] when the identifier has no stored
    uses). The record is the fact itself, from entry data;
    the door use only pointed at it."""
    root = str(root)
    envelope, path = _load_identifier_file(root, identifier)
    if envelope is None:
        return []
    uses = _door_use_set(envelope, path)
    return [_resolve_use(root, identifier, use, path)
            for use in _sorted_uses(uses)]


def traverse_identifier(root, identifier, value):
    """Single-identifier traversal: from the identifier and
    its value ALONE - no entry, no coordinate supplied -
    reach the specific stored information. The identifier's
    value is independent of layer, of entry identity, and
    of the subcoordinate, so the door uses keyed by it lead
    straight to the facts. Returns the list of resolved
    facts whose stored value equals the given value."""
    root = str(root)
    envelope, path = _load_identifier_file(root, identifier)
    if envelope is None:
        return []
    uses = _door_use_set(envelope, path)
    return [_resolve_use(root, identifier, use, path)
            for use in _sorted_uses(uses)
            if use[1] == value]


def incoming_links(root, target, graph=BASE_GRAPH):
    """Incoming-link enumeration (P2): every pointer INTO
    the target value, from the identifier doors alone.
    Opens each identifier file under identifier_data -
    O(|I|) file opens - and looks up by_target[target] in
    it. Cost, as measured (claims-14, 2026-10-26): each
    door file is fully parsed and its two views proven
    equal before the lookup, so per-file cost is
    Theta(uses stored under that identifier) and a full
    call's cost tracks total door bytes; only the final
    dict lookup is O(1). The call is NOT independent of
    vocabulary size. (This docstring originally claimed
    "O(1) per file, independent of vocabulary size"; the
    measurement contradicted it and the wording is
    corrected, Pack C, 2026-10-26.)

    Every candidate row is cross-checked through
    _resolve_use - the strict loader's own resolution -
    before it may be returned (Pack C; claims-02 Claim 5):
    a use not backed by the stored fact it points at (a
    phantom row in mutually consistent doors) is NOT
    returned, while load_layout and search_by_identifier
    refuse such a door outright.

    Returns pointer rows ONLY ({entry, identifier, graph,
    coord, state, value}; coord as a (layer, dimension,
    index) tuple; never fact records), sorted by
    (identifier, entry). No incoming links are stored on
    the target's entry; this is the worklist a chain
    reaction would consume."""
    root = str(root)
    ident_dir = os.path.join(root, "identifier_data")
    if not os.path.isdir(ident_dir):
        return []
    key = _target_key(target)
    out = []
    for fname in sorted(os.listdir(ident_dir)):
        if fname.startswith(".") \
                or not fname.endswith(".json"):
            continue
        identifier = fname[:-len(".json")]
        envelope, door_path = _load_identifier_file(
            root, identifier)
        if envelope is None:
            continue
        for row in envelope["by_target"].get(key, []):
            if row["graph"] != graph:
                continue
            use = (row["source"], target, row["graph"],
                   tuple(row["coord"]), row["state"])
            try:
                _resolve_use(root, identifier, use,
                             door_path)
            except StoredOnceError:
                continue
            out.append({"entry": row["source"],
                        "identifier": identifier,
                        "graph": row["graph"],
                        "coord": tuple(row["coord"]),
                        "state": row["state"],
                        "value": target})
    out.sort(key=lambda r: (r["identifier"], r["entry"],
                            r["graph"], r["coord"],
                            r["state"]))
    return out


def search_facts(root, graph=BASE_GRAPH, identifier=None,
                 value=None, status=None, source=None,
                 event=None):
    """Fragment entry points that live INSIDE the facts:
    status, provenance source, attestation event. These
    need no door and no copy - the facts are stored once
    and are findable by reading them. Scans the graph's
    entry data and returns every stored fact matching ALL
    given criteria, as {entry, identifier, value, record},
    in (entry, identifier) order."""
    root = str(root)
    out = []
    entry_dir = os.path.join(root, "entry_data", graph)
    if not os.path.isdir(entry_dir):
        return []
    for fname in sorted(os.listdir(entry_dir)):
        if fname.startswith(".") \
                or not fname.endswith(".json"):
            continue
        word = fname[:-len(".json")]
        envelope, _ = _load_entry_file(root, graph, word)
        subs = envelope["record"]["subcoordinates"]
        for ident in sorted(subs):
            sub = subs[ident]
            if identifier is not None and ident != identifier:
                continue
            if value is not None and sub["value"] != value:
                continue
            if status is not None \
                    and sub.get("status") != status:
                continue
            prov = sub.get("provenance") or {}
            if source is not None \
                    and prov.get("source") != source:
                continue
            if event is not None and event not in sub.get(
                    "attested_events", []):
                continue
            out.append({"entry": word, "identifier": ident,
                        "value": sub["value"],
                        "record": copy.deepcopy(sub)})
    return out


def search_gaps(root, word=None, identifier=None):
    """Gap entry point: the gaps stored once on the self
    entry, filtered by word and/or identifier.

    P1 form: the self entry's gaps are the typed index (a
    dict keyed by gap key, pmca_write_in P1). A word filter
    matches keys that name the word ("slot::<word>::..." or
    "word::<word>::..."); an identifier filter matches slot
    keys ending "::<identifier>". Returned entries are deep
    copies of the index entries with their "key" included.
    A pre-P1 layout whose self record still holds the list
    form is read in its own terms (records returned as
    stored)."""
    root = str(root)
    self_record = _load_self_record(root)
    gaps = self_record["gaps"]
    out = []
    if isinstance(gaps, dict):
        for key in sorted(gaps):
            if word is not None and not (
                    key.startswith("slot::" + word + "::")
                    or key.startswith("word::" + word + "::")):
                continue
            if identifier is not None and not (
                    key.startswith("slot::")
                    and key.endswith("::" + identifier)):
                continue
            entry = copy.deepcopy(gaps[key])
            entry["key"] = key
            out.append(entry)
        return out
    for gap in gaps:
        if word is not None and gap.get("word") != word:
            continue
        if identifier is not None \
                and gap.get("identifier") != identifier:
            continue
        out.append(copy.deepcopy(gap))
    return out


# ---------------------------------------------------------------------
# The canonical file, demoted to a derived export
# ---------------------------------------------------------------------

def derive_export(root):
    """The canonical write-in store bytes, DERIVED from the
    stored-once layout: strict-load the layout (validating
    every door against its facts) and serialize the
    reassembled store with pmca_write_in's own canonical
    form. An output of the storage, never its residence."""
    import pmca_write_in as wi
    return wi.canonical(load_layout(root)).encode("utf-8")


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_stored_once.py
# Builds the rich store with the REAL write-in, population,
# finders, and dictionary, then exercises the stored-once
# layout against it in temporary storage roots.
# ---------------------------------------------------------------------

def run_checks():
    import tempfile

    import pmca_sentence_analyzer as _analyzer
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    _analyzer.configure_dictionary(db)

    import pmca_graphspace as gs
    import pmca_noun_finder as noun_finder
    import pmca_population as pop
    import pmca_verb_finder as verb_finder
    import pmca_write_in as wi

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    def tree_bytes(root):
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fname in sorted(filenames):
                p = os.path.join(dirpath, fname)
                with open(p, "rb") as f:
                    out[os.path.relpath(p, root)] = f.read()
        return out

    def subtree(tree, prefix):
        return {rel: b for rel, b in tree.items()
                if rel.split(os.sep)[0] == prefix}

    def keys_of(obj):
        out = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.add(k)
                out |= keys_of(v)
        elif isinstance(obj, list):
            for v in obj:
                out |= keys_of(v)
        return out

    # ---- Build the rich store with the real modules. ----
    store = wi.new_store()
    pop.populate_word(store, "banana", "suite")
    pop.enrich_from_dictionary(store, "banana")
    s_wolf = "The wolf eats the sheep."
    pop.encounter(store, s_wolf, noun_finder.find(s_wolf),
                  verb_finder.find(s_wolf))
    pop.encounter(store, s_wolf, noun_finder.find(s_wolf),
                  verb_finder.find(s_wolf))
    wi.write_fill(store, "wolf", "is_a", "canine",
                  "dictionary_note")
    # PROM-1 re-base (2026-10-29): the second attestation
    # was a second write_fill - the SAME "verifier" class
    # as the first fill - which under the ruled class
    # counting no longer verifies (it stays candidate and
    # the downstream verified checks cascade). It is now
    # a dictionary-class attestation stamped through the
    # shared engine: no public door writes is_a under a
    # second class, so the fixture calls wi._attest
    # directly and records the event on the self entry.
    # The standing provenance remains the verifier's
    # (the first fill); attestations stays 2.
    seq = wi._next_seq(store)
    wi._attest(store, "wolf", "is_a", "canine",
               {"source": "dictionary",
                "evidence": "seed_exemplar"}, seq)
    wi._append_self_record(store, {"seq": seq,
                                   "kind":
                                   "cross_attestation",
                                   "outcome": "reattested",
                                   "word": "wolf",
                                   "identifier": "is_a"})
    pop.populate_word(store, "flibberty", "suite")
    pop.enrich_from_dictionary(store, "flibberty")

    binding = wi.binding_for(store, "wolf", "eat")
    # D13 (2026-10-26; claims-05 F3b): two identical
    # encounters no longer verify the binding — candidate.
    check("built_store_binding_candidate",
          binding is not None
          and binding["status"] == "candidate"
          and binding["attestations"] == 2, repr(binding))
    wolf_isa = store["entries"]["wolf"]["subcoordinates"] \
        ["is_a"]
    check("built_store_wolf_is_a_verified",
          wolf_isa["value"] == "canine"
          and wolf_isa["status"] == "verified"
          and wolf_isa["attestations"] == 2)
    # P1 re-base: gaps are the typed index now; the same fact
    # is asserted by its key (slot::flibberty::definition).
    check("built_store_banana_enriched_flibberty_gap",
          store["entries"]["banana"]["definition"]["text"]
          .startswith("(Bot.)")
          and store["entries"][wi.SELF_KEY]["gaps"]
          ["slot::flibberty::definition"]["reason"]
          == "not_in_dictionary")
    orig_canonical = wi.canonical(store)
    orig_hash = wi.store_hash(store)

    with tempfile.TemporaryDirectory() as root1, \
            tempfile.TemporaryDirectory() as root2, \
            tempfile.TemporaryDirectory() as root3, \
            tempfile.TemporaryDirectory() as root4, \
            tempfile.TemporaryDirectory() as root5:
        summary = build_layout(store, root1)
        tree1 = tree_bytes(root1)
        n_words = len([w for w in store["entries"]
                        if w != wi.SELF_KEY])
        check("build_summary_counts",
              summary["entry_files"] == n_words
              and summary["identifier_files"] == 5
              and summary["facts"] == 5,
              repr(summary))

        # ---- STORED-ONCE AUDIT over the whole tree. ----
        wolf_fact = store["entries"]["wolf"] \
            ["subcoordinates"]["originates::eat"]
        # Stored once is a property of the ADDRESSED fact -
        # (entry, identifier, record) - not of bare record
        # bytes. Two different facts can carry byte-identical
        # record bodies: wolf's originates and sheep's
        # target_of were attested by the same two events
        # with the same provenance, so their bodies coincide
        # exactly (same value, events, status). Neither is a
        # copy of the other - the fact is the record AT its
        # (entry, identifier) address, and each address lives
        # in exactly one file. The audit collects every fact
        # in the tree by address and asserts exactly that.
        fact_locations = {}
        for rel, b in tree1.items():
            if rel.split(os.sep)[0] != "entry_data":
                continue
            env = json.loads(b.decode("utf-8"))
            for ident in env["record"]["subcoordinates"]:
                fact_locations.setdefault(
                    (env["entry"], ident), []).append(rel)
        wolf_originates_files = fact_locations.get(
            ("wolf", "originates::eat"), [])
        check("audit_fact_appears_in_exactly_one_file",
              wolf_originates_files
              == [_entry_relpath(BASE_GRAPH, "wolf")]
              and all(files == [_entry_relpath(
                  BASE_GRAPH, entry)]
                      for (entry, _ident), files
                      in fact_locations.items())
              and len(fact_locations) == summary["facts"],
              "wolf.originates::eat in %r; %d addressed "
              "facts, each in exactly its own entry file"
              % (wolf_originates_files,
                 len(fact_locations)))
        every_once = True
        for w in store["entries"]:
            if w == wi.SELF_KEY:
                continue
            entry_bytes = _canon_bytes(store["entries"][w])
            if sum(1 for b in tree1.values()
                   if entry_bytes in b) != 1:
                every_once = False
        check("audit_every_entry_content_in_exactly_one_file",
              every_once)
        doors_pure = True
        for rel, b in tree1.items():
            top = rel.split(os.sep)[0]
            if top in ("coordinate_data",
                       "identifier_data"):
                parsed = json.loads(b.decode("utf-8"))
                if keys_of(parsed) & _FACT_ONLY_KEYS:
                    doors_pure = False
        check("audit_doors_hold_no_fact_content_keys",
              doors_pure)
        # P2 re-base: doors are dual-keyed now (no "rows");
        # total uses are counted via by_source, one use per
        # stored fact (the old audit read envelope["rows"]).
        ident_rows_total = 0
        for rel, b in tree1.items():
            if rel.split(os.sep)[0] == "identifier_data":
                env_d = json.loads(b.decode("utf-8"))
                ident_rows_total += sum(
                    len(rows) for rows in
                    env_d["by_source"].values())
        check("audit_identifier_rows_equal_stored_facts",
              ident_rows_total == summary["facts"])

        # ---- EVERY-WAY SEARCH. ----
        wolf_coord = gs.dist_address("wolf")
        by_coord = search_by_coordinate(root1, BASE_GRAPH,
                                        wolf_coord)
        check("search_by_coordinate_resolves_wolf",
              by_coord.get("wolf")
              == store["entries"]["wolf"]
              and all(gs.dist_address(w) == wolf_coord
                      for w in by_coord))
        banana = search_by_word(root1, "banana")
        check("search_by_word_resolves_banana_definition",
              banana == store["entries"]["banana"]
              and banana["definition"]["text"].startswith(
                  "(Bot.)")
              and search_by_word(root1, "nosuchword")
              is None)
        by_orig = search_by_identifier(root1,
                                       "originates::eat")
        check("search_by_identifier_scoped_originates",
              len(by_orig) == 1
              and by_orig[0]["entry"] == "wolf"
              and by_orig[0]["record"] == wolf_fact)
        by_acts = search_by_identifier(root1, "acts_on::wolf")
        by_isa = search_by_identifier(root1, "is_a")
        check("search_by_identifier_scoped_acts_on_and_is_a",
              len(by_acts) == 1
              and by_acts[0]["entry"] == "eat"
              and by_acts[0]["record"]
              == store["entries"]["eat"]["subcoordinates"]
              ["acts_on::wolf"]
              and len(by_isa) == 1
              and by_isa[0]["record"] == wolf_isa
              and search_by_identifier(root1, "teleports")
              == [])
        fact_acts = store["entries"]["eat"] \
            ["subcoordinates"]["acts_on::wolf"]
        trav_sheep = traverse_identifier(root1,
                                         "acts_on::wolf",
                                         "sheep")
        check("traverse_by_target_value_sheep",
              len(trav_sheep) == 1
              and trav_sheep[0]["entry"] == "eat"
              and trav_sheep[0]["record"] == fact_acts)
        trav_eat = traverse_identifier(root1,
                                       "originates::eat",
                                       "eat")
        check("traverse_single_identifier_originates_eat",
              len(trav_eat) == 1
              and trav_eat[0]["entry"] == "wolf"
              and trav_eat[0]["record"] == wolf_fact
              and trav_eat[0]["record"]
              == by_orig[0]["record"])
        # D13 re-base: the rich store's verified fact is the
        # cross-class is_a, not the replayed binding.
        # (PROM-1 re-base, 2026-10-29: "cross-evidence" read
        # as cross-class - verifier + dictionary classes.)
        by_status = search_facts(root1,
                                 identifier="is_a",
                                 status="verified")
        ev = wolf_fact["attested_events"][-1]
        by_event = search_facts(root1, event=ev)
        by_source = search_facts(root1, identifier="is_a",
                                 source="verifier")
        check("search_facts_by_status_event_source",
              len(by_status) == 1
              and by_status[0]["entry"] == "wolf"
              and by_status[0]["record"] == wolf_isa
              and any(f["entry"] == "wolf"
                      and f["identifier"] == "originates::eat"
                      for f in by_event)
              and len(by_source) == 1
              and by_source[0]["record"] == wolf_isa)
        gaps = search_gaps(root1, word="flibberty")
        # P1 re-base: the found entry names its key (the
        # identifier lives in the key, slot::...::definition).
        check("search_gaps_finds_flibberty_gap",
              len(gaps) == 1
              and gaps[0]["key"] == "slot::flibberty::definition"
              and gaps[0]["reason"]
              == "not_in_dictionary")

        # ---- ROUND TRIP: the canonical file is derived. ----
        check("derive_export_byte_identical",
              derive_export(root1)
              == orig_canonical.encode("utf-8"))
        check("load_layout_canonical_identical",
              wi.canonical(load_layout(root1))
              == orig_canonical
              and wi.store_hash(load_layout(root1))
              == orig_hash)

        # ---- DETERMINISM + build-twice write-once. ----
        build_layout(store, root2)
        tree2_first = tree_bytes(root2)
        build_layout(store, root2)
        check("determinism_second_root_byte_identical",
              tree2_first == tree1
              and tree_bytes(root2) == tree2_first)

        # ---- WRITE-ONCE at fact granularity. ----
        shutil.copytree(root1, root5, dirs_exist_ok=True)
        before5 = tree_bytes(root5)
        rep_same = store_entry(root5, BASE_GRAPH, "wolf",
                               copy.deepcopy(
                                   store["entries"]
                                   ["wolf"]))
        check("store_entry_already_stored_changes_nothing",
              rep_same["outcome"] == "already_stored"
              and tree_bytes(root5) == before5)
        otter_fact = {
            "value": "mammal", "status": "candidate",
            "provenance": {"source": "suite",
                           "evidence":
                           "stored_once_probe"},
            "attestations": 1,
            "attested_events": [store["seq"]],
            "first_seq": store["seq"],
            "last_seq": store["seq"]}
        otter_record = {"subcoordinates":
                        {"is_a": otter_fact},
                        "conflicts": []}
        rep_new = store_entry(root5, BASE_GRAPH, "otter",
                              otter_record)
        loaded5 = load_layout(root5)
        check("store_entry_new_word_stored_with_doors",
              rep_new["outcome"] == "stored"
              and loaded5["entries"]["otter"] == otter_record
              and any(f["entry"] == "otter"
                      for f in search_by_identifier(
                          root5, "is_a")))
        before_conflict = tree_bytes(root5)
        err = None
        try:
            store_entry(root5, BASE_GRAPH, "wolf",
                        {"subcoordinates": {},
                         "conflicts": []})
        except StoredOnceError as exc:
            err = exc
        check("store_entry_differing_record_refused",
              err is not None
              and _entry_path(root5, BASE_GRAPH, "wolf")
              in str(err)
              and tree_bytes(root5) == before_conflict)

        # ---- STRICTNESS: a corrupt coordinate door. ----
        shutil.copytree(root1, root3, dirs_exist_ok=True)
        entry_subtree_before = subtree(tree_bytes(root3),
                                       "entry_data")
        bad_coord_path = _coord_path(root3, BASE_GRAPH,
                                     wolf_coord)
        with open(bad_coord_path, "wb") as f:
            f.write(b'{"door": broken')
        err3 = None
        try:
            load_layout(root3)
        except StoredOnceError as exc:
            err3 = exc
        err3b = None
        try:
            search_by_coordinate(root3, BASE_GRAPH,
                                 wolf_coord)
        except StoredOnceError as exc:
            err3b = exc
        check("corrupt_coordinate_door_raises_naming_it",
              err3 is not None
              and bad_coord_path in str(err3)
              and err3b is not None
              and bad_coord_path in str(err3b),
              str(err3) if err3 else "no error raised")
        check("corrupt_door_leaves_entry_data_untouched",
              subtree(tree_bytes(root3), "entry_data")
              == entry_subtree_before)

        # ---- STRICTNESS: a corrupt entry-data fact. ----
        shutil.copytree(root1, root4, dirs_exist_ok=True)
        doors_before = {rel: b
                        for rel, b in
                        tree_bytes(root4).items()
                        if rel.split(os.sep)[0] in
                        ("coordinate_data",
                         "identifier_data")}
        bad_entry_path = _entry_path(root4, BASE_GRAPH,
                                     "wolf")
        with open(bad_entry_path, "wb") as f:
            f.write(b'{"fact": broken')
        err4 = None
        try:
            load_layout(root4)
        except StoredOnceError as exc:
            err4 = exc
        err4b = None
        try:
            search_by_word(root4, "wolf")
        except StoredOnceError as exc:
            err4b = exc
        check("corrupt_entry_fact_raises_naming_it",
              err4 is not None
              and bad_entry_path in str(err4)
              and err4b is not None
              and bad_entry_path in str(err4b),
              str(err4) if err4 else "no error raised")
        doors_after = {rel: b
                       for rel, b in
                       tree_bytes(root4).items()
                       if rel.split(os.sep)[0] in
                       ("coordinate_data",
                        "identifier_data")}
        check("corrupt_fact_leaves_doors_untouched",
              doors_after == doors_before)

    # ---- P2: two-axis contract (T) + dual-key doors (D). ----
    # Contract fixture: a hand-shaped write-in store (self
    # entry + wolf with a CANDIDATE is_a fact + bare cup),
    # facts shaped field-for-field as pmca_write_in facts.
    def p2_fact(value, status, first, events=None,
                source="suite"):
        events = list(events) if events is not None \
            else [first]
        return {"value": value, "status": status,
                "provenance": {"source": source,
                               "evidence":
                               "p2_contract_fixture"},
                "attestations": len(events),
                "attested_events": events,
                "first_seq": first,
                "last_seq": events[-1]}

    wolf_cand = p2_fact("canine", "candidate", 1,
                        source="dictionary_note")
    contract_store = {"seq": 3, "entries": {
        wi.SELF_KEY: {"encounters": [], "gaps": {}},
        "wolf": {"subcoordinates":
                 {"is_a": copy.deepcopy(wolf_cand)},
                 "conflicts": []},
        "cup": {"subcoordinates": {}, "conflicts": []}}}

    def refused(root, word, record, needle):
        before = tree_bytes(root)
        err = None
        try:
            store_entry(root, BASE_GRAPH, word, record)
        except StoredOnceError as exc:
            err = exc
        return err is not None and needle in str(err) \
            and _entry_path(root, BASE_GRAPH, word) \
            in str(err) \
            and tree_bytes(root) == before

    with tempfile.TemporaryDirectory() as cdir:
        pristine = os.path.join(cdir, "pristine")
        build_layout(contract_store, pristine)

        def fresh(name):
            dest = os.path.join(cdir, name)
            shutil.copytree(pristine, dest)
            return dest

        def door_state(root, identifier, source):
            env, path = _load_identifier_file(root,
                                              identifier)
            for use in _door_use_set(env, path):
                if use[0] == source:
                    return use[4]
            return None

        def verified_root(name):
            # PROM-1 re-base (2026-10-29): the candidate
            # is_a fact's standing provenance is the single
            # class "dictionary_note" with NO history, so a
            # bare flip to verified is now refused by the
            # PROM-1 gate. The fixture gains a
            # provenance_history spanning two classes
            # (gaining the key is the validator's addable
            # case); the flip's intent is unchanged.
            r = fresh(name)
            rec = search_by_word(r, "wolf")
            f = rec["subcoordinates"]["is_a"]
            f["attested_events"] = [1, 2]
            f["attestations"] = 2
            f["last_seq"] = 2
            f["status"] = "verified"
            f["provenance_history"] = [
                {"source": "verifier",
                 "evidence": "dictionary_note"},
                {"source": "dictionary",
                 "evidence": "webster1913"}]
            rep = store_entry(r, BASE_GRAPH, "wolf", rec)
            assert rep["outcome"] == "advanced"
            return r

        # T1: new identifier as CANDIDATE on a BARE entry.
        r = fresh("t1")
        cup_new = {"subcoordinates":
                   {"is_a": p2_fact("vessel", "candidate", 5,
                                    source="teaching")},
                   "conflicts": []}
        rep = store_entry(r, BASE_GRAPH, "cup", cup_new)
        check("p2_t1_new_identifier_candidate_bare_to_"
              "populated",
              rep["outcome"] == "advanced"
              and "entry_axis:bare_to_populated"
              in rep["transitions"]
              and "identifier_added:is_a:candidate"
              in rep["transitions"]
              and load_layout(r)["entries"]["cup"] == cup_new
              and door_state(r, "is_a", "cup") == "candidate")

        # T2: new identifier arriving VERIFIED -> refused.
        r = fresh("t2")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"]["originates"] = p2_fact(
            "eat", "verified", 5, events=[5, 6])
        check("p2_t2_new_identifier_verified_refused",
              refused(r, "wolf", rec,
                      "new_identifier_not_candidate:"
                      "originates"))

        # T3: CANDIDATE -> VERIFIED by accretion.
        # PROM-1 re-base (2026-10-29): as in verified_root,
        # the flip gains a provenance_history spanning two
        # classes, so the PROM-1 gate passes it.
        r = fresh("t3")
        rec = search_by_word(r, "wolf")
        f = rec["subcoordinates"]["is_a"]
        f["attested_events"] = [1, 2]
        f["attestations"] = 2
        f["last_seq"] = 2
        f["status"] = "verified"
        f["provenance_history"] = [
            {"source": "verifier",
             "evidence": "dictionary_note"},
            {"source": "dictionary",
             "evidence": "webster1913"}]
        rep = store_entry(r, BASE_GRAPH, "wolf", rec)
        check("p2_t3_candidate_to_verified_by_accretion",
              rep["outcome"] == "advanced"
              and "binding_verified:is_a" in rep["transitions"]
              and "attestations_appended:is_a"
              in rep["transitions"]
              and door_state(r, "is_a", "wolf") == "verified"
              and load_layout(r)["entries"]["wolf"] == rec)

        # T4: accretion on a VERIFIED binding (third event).
        r = verified_root("t4")
        rec = search_by_word(r, "wolf")
        f = rec["subcoordinates"]["is_a"]
        f["attested_events"] = [1, 2, 3]
        f["attestations"] = 3
        f["last_seq"] = 3
        rep = store_entry(r, BASE_GRAPH, "wolf", rec)
        loaded_f = load_layout(r)["entries"]["wolf"] \
            ["subcoordinates"]["is_a"]
        check("p2_t4_verified_accretion_third_event",
              rep["outcome"] == "advanced"
              and "attestations_appended:is_a"
              in rep["transitions"]
              and "binding_verified:is_a"
              not in rep["transitions"]
              and loaded_f["attested_events"] == [1, 2, 3]
              and loaded_f["attestations"] == 3
              and loaded_f["status"] == "verified"
              and door_state(r, "is_a", "wolf") == "verified")

        # T5: explicit-promotion keys added on promotion.
        # PROM-1 re-base (2026-10-29): as in verified_root,
        # the flip gains a provenance_history spanning two
        # classes, so the PROM-1 gate passes it.
        r = fresh("t5")
        rec = search_by_word(r, "wolf")
        f = rec["subcoordinates"]["is_a"]
        f["attested_events"] = [1, 2]
        f["attestations"] = 2
        f["last_seq"] = 2
        f["status"] = "verified"
        f["provenance_history"] = [
            {"source": "verifier",
             "evidence": "dictionary_note"},
            {"source": "dictionary",
             "evidence": "webster1913"}]
        f["promoted_reason"] = "explicit_review"
        f["promoted_seq"] = 2
        rep = store_entry(r, BASE_GRAPH, "wolf", rec)
        check("p2_t5_promotion_keys_added",
              rep["outcome"] == "advanced"
              and "promotion_recorded:is_a:promoted_reason"
              in rep["transitions"]
              and "promotion_recorded:is_a:promoted_seq"
              in rep["transitions"]
              and load_layout(r)["entries"]["wolf"] == rec)

        # T6: identifier deletion (POPULATED -> BARE).
        r = fresh("t6")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"] = {}
        check("p2_t6_identifier_deletion_refused",
              refused(r, "wolf", rec,
                      "identifier_deleted:is_a"))

        # T7: conflicts append permitted (standing fact
        # unmoved); shrink or mutation refused.
        r = fresh("t7")
        rec = search_by_word(r, "wolf")
        rec["conflicts"] = [
            {"seq": 4, "identifier": "is_a",
             "stored_value": "canine",
             "attempted_value": "lupine",
             "provenance": {"source": "suite"}}]
        rep = store_entry(r, BASE_GRAPH, "wolf", rec)
        standing = load_layout(r)["entries"]["wolf"] \
            ["subcoordinates"]["is_a"]
        rec_shrink = search_by_word(r, "wolf")
        rec_shrink["conflicts"] = []
        ok_shrink = refused(r, "wolf", rec_shrink,
                            "conflicts_not_append_only")
        rec_mut = search_by_word(r, "wolf")
        rec_mut["conflicts"][0]["attempted_value"] = "wolfdog"
        ok_mut = refused(r, "wolf", rec_mut,
                         "conflicts_not_append_only")
        check("p2_t7_conflicts_append_only",
              rep["outcome"] == "advanced"
              and "conflicts_appended:1" in rep["transitions"]
              and standing == wolf_cand
              and ok_shrink and ok_mut)

        # T8: CANDIDATE target changed in place -> refused.
        r = fresh("t8")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"]["is_a"]["value"] = "lupine"
        check("p2_t8_candidate_value_change_refused",
              refused(r, "wolf", rec,
                      "candidate_value_changed:is_a"))

        # T9: VERIFIED target changed, NO audit -> refused.
        r = verified_root("t9")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"]["is_a"]["value"] = "lupine"
        check("p2_t9_verified_value_change_no_audit_refused",
              refused(r, "wolf", rec,
                      "verified_value_changed_without_"
                      "supersession_audit:is_a"))

        # T10: teaching supersession, complete.
        r = verified_root("t10")
        rec = search_by_word(r, "wolf")
        old_fact = copy.deepcopy(
            rec["subcoordinates"]["is_a"])
        retained = copy.deepcopy(old_fact)
        retained["status"] = "superseded"
        rec["subcoordinates"]["is_a"] = p2_fact(
            "canid", "verified", 7, source="teaching")
        rec["superseded"] = [
            {"identifier": "is_a", "fact": retained,
             "superseded_seq": 7,
             "provenance": {"source": "teaching",
                            "evidence": "correction"}}]
        rep = store_entry(r, BASE_GRAPH, "wolf", rec)
        loaded_rec = load_layout(r)["entries"]["wolf"]
        env, ipath = _load_identifier_file(r, "is_a")
        uses = _door_use_set(env, ipath)
        check("p2_t10_teaching_supersession_complete",
              rep["outcome"] == "advanced"
              and any(t.startswith("binding_superseded:is_a:")
                      for t in rep["transitions"])
              and loaded_rec == rec
              and loaded_rec["superseded"][0]["fact"]
              == retained
              and loaded_rec["superseded"][0]
              ["superseded_seq"] == 7
              and loaded_rec["superseded"][0]["provenance"]
              ["source"] == "teaching"
              and loaded_rec["subcoordinates"]["is_a"]
              ["value"] == "canid"
              and loaded_rec["subcoordinates"]["is_a"]
              ["status"] == "verified"
              and any(u[0] == "wolf" and u[1] == "canid"
                      and u[4] == "verified" for u in uses)
              and not any(u[1] == "canine" for u in uses))

        # T11: malformed / spurious supersession audits.
        def sup_attempt(name, mutate=None,
                        new_status="verified"):
            rr = verified_root(name)
            rr_rec = search_by_word(rr, "wolf")
            of = copy.deepcopy(
                rr_rec["subcoordinates"]["is_a"])
            ret = copy.deepcopy(of)
            ret["status"] = "superseded"
            item = {"identifier": "is_a", "fact": ret,
                    "superseded_seq": 7,
                    "provenance": {"source": "teaching"}}
            if mutate is not None:
                mutate(item)
            rr_rec["subcoordinates"]["is_a"] = p2_fact(
                "canid", new_status, 7, source="teaching")
            rr_rec["superseded"] = [item]
            return rr, rr_rec

        r_a, rec_a = sup_attempt(
            "t11a", lambda it: it.pop("provenance"))
        r_b, rec_b = sup_attempt(
            "t11b", lambda it: it.pop("superseded_seq"))
        r_c, rec_c = sup_attempt(
            "t11c",
            lambda it: it["fact"].__setitem__(
                "attestations", 99))
        r_d, rec_d = sup_attempt("t11d",
                                 new_status="candidate")
        r_e = verified_root("t11e")
        rec_e = search_by_word(r_e, "wolf")
        of_e = copy.deepcopy(rec_e["subcoordinates"]["is_a"])
        ret_e = copy.deepcopy(of_e)
        ret_e["status"] = "superseded"
        rec_e["superseded"] = [
            {"identifier": "is_a", "fact": ret_e,
             "superseded_seq": 7,
             "provenance": {"source": "teaching"}}]
        check("p2_t11_supersession_audit_refusals",
              refused(r_a, "wolf", rec_a,
                      "supersession_audit_missing:provenance:"
                      "is_a")
              and refused(r_b, "wolf", rec_b,
                          "supersession_audit_missing:"
                          "superseded_seq:is_a")
              and refused(r_c, "wolf", rec_c,
                          "superseded_fact_altered:is_a")
              and refused(r_d, "wolf", rec_d,
                          "supersession_new_fact_not_"
                          "verified:is_a")
              and refused(r_e, "wolf", rec_e,
                          "superseded_append_without_value_"
                          "change:is_a"))

        # T12: VERIFIED -> CANDIDATE regression.
        r = verified_root("t12")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"]["is_a"]["status"] = "candidate"
        check("p2_t12_status_regression_refused",
              refused(r, "wolf", rec,
                      "status_transition_refused:is_a:"
                      "verified->candidate"))

        # T13: attestation shrink / first_seq change.
        r = verified_root("t13a")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"]["is_a"]["attestations"] = 1
        ok_a = refused(r, "wolf", rec,
                       "attestations_decreased:is_a")
        r = verified_root("t13b")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"]["is_a"]["attested_events"] = [1]
        ok_b = refused(r, "wolf", rec,
                       "attested_events_not_append_only:is_a")
        r = verified_root("t13c")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"]["is_a"]["first_seq"] = 99
        ok_c = refused(r, "wolf", rec,
                       "immutable_fact_field_changed:is_a:"
                       "first_seq")
        check("p2_t13_accretion_shrink_refused",
              ok_a and ok_b and ok_c)

        # T14: standing fact's provenance altered.
        r = verified_root("t14")
        rec = search_by_word(r, "wolf")
        rec["subcoordinates"]["is_a"]["provenance"] = {
            "source": "forged", "evidence": "p2"}
        check("p2_t14_provenance_altered_refused",
              refused(r, "wolf", rec,
                      "immutable_fact_field_changed:is_a:"
                      "provenance"))

        # T15: ungoverned top-level field altered.
        store15 = copy.deepcopy(contract_store)
        store15["entries"]["wolf"]["definition"] = {
            "text": "A canine animal."}
        root15 = os.path.join(cdir, "t15")
        build_layout(store15, root15)
        rec = search_by_word(root15, "wolf")
        rec["definition"] = {"text": "Altered text."}
        check("p2_t15_ungoverned_field_refused",
              refused(root15, "wolf", rec,
                      "ungoverned_field_changed:definition"))

        # T16: mutating retained superseded history.
        r = verified_root("t16setup")
        rec = search_by_word(r, "wolf")
        of = copy.deepcopy(rec["subcoordinates"]["is_a"])
        ret = copy.deepcopy(of)
        ret["status"] = "superseded"
        rec["subcoordinates"]["is_a"] = p2_fact(
            "canid", "verified", 7, source="teaching")
        rec["superseded"] = [
            {"identifier": "is_a", "fact": ret,
             "superseded_seq": 7,
             "provenance": {"source": "teaching"}}]
        store_entry(r, BASE_GRAPH, "wolf", rec)
        rec2 = search_by_word(r, "wolf")
        rec2["superseded"][0]["superseded_seq"] = 99
        check("p2_t16_superseded_history_immutable",
              refused(r, "wolf", rec2,
                      "superseded_history_mutated"))

        # T17: baseline outcomes carried in the contract
        # fixture - new word "stored" with dual-key doors,
        # identical record "already_stored", nothing written.
        r = fresh("t17")
        zebra_rec = {"subcoordinates":
                     {"is_a": p2_fact("equine", "candidate",
                                      8)},
                     "conflicts": []}
        rep_new = store_entry(r, BASE_GRAPH, "zebra",
                              zebra_rec)
        env, _ = _load_identifier_file(r, "is_a")
        before = tree_bytes(r)
        rep_same = store_entry(r, BASE_GRAPH, "zebra",
                               copy.deepcopy(zebra_rec))
        check("p2_t17_baseline_outcomes_carried",
              rep_new["outcome"] == "stored"
              and env["format"] == IDENT_FORMAT
              == "pmca-stored-once-identifier/3"
              and rep_same["outcome"] == "already_stored"
              and tree_bytes(r) == before
              and load_layout(r)["entries"]["zebra"]
              == zebra_rec)

        # D1: both maps parse to the SAME use set (rich
        # layout rebuilt + contract fixture); by_source use
        # total == stored facts.
        d1_rich = os.path.join(cdir, "d1_rich")
        build_layout(store, d1_rich)

        def door_sets_equal(root):
            ident_dir = os.path.join(root, "identifier_data")
            total = 0
            for fname in sorted(os.listdir(ident_dir)):
                if not fname.endswith(".json"):
                    continue
                ident = fname[:-len(".json")]
                env, path = _load_identifier_file(root, ident)
                s_uses, s_n = _uses_from_by_source(env, path)
                t_uses, t_n = _uses_from_by_target(env, path)
                projected = {
                    (u[0], _target_key(u[1]), u[2], u[3],
                     u[4]) for u in s_uses}
                if projected != t_uses or s_n != t_n \
                        or _door_use_set(env, path) != s_uses:
                    return False, 0
                total += s_n
            return True, total

        ok_rich, total_rich = door_sets_equal(d1_rich)
        ok_contract, total_contract = door_sets_equal(
            pristine)
        n_store_facts = sum(
            len(e["subcoordinates"])
            for w, e in store["entries"].items()
            if w != wi.SELF_KEY)
        check("p2_d1_door_views_equal_use_sets",
              ok_rich and ok_contract
              and total_rich == n_store_facts == 5
              and total_contract == 1)

        # D2: tampered doors refuse strict load, by name.
        def tamper(name, identifier, fn):
            rr = fresh(name)
            ipath = _ident_path(rr, identifier)
            with open(ipath, "rb") as fh:
                env = json.loads(fh.read().decode("utf-8"))
            fn(env)
            with open(ipath, "wb") as fh:
                fh.write(json.dumps(
                    env, sort_keys=True).encode("utf-8"))
            err = None
            try:
                load_layout(rr)
            except StoredOnceError as exc:
                err = exc
            return err is not None and ipath in str(err)

        ok_a = tamper(
            "d2a", "is_a",
            lambda env: env["by_target"]["canine"].pop())
        ok_b = tamper(
            "d2b", "is_a",
            lambda env: env["by_source"]["wolf"][0]
            .__setitem__("target", "lupine"))

        def state_tamper(env):
            env["by_source"]["wolf"][0]["state"] = "verified"
            env["by_target"]["canine"][0]["state"] = \
                "verified"

        ok_c = tamper("d2c", "is_a", state_tamper)
        check("p2_d2_tampered_door_refused_by_name",
              ok_a and ok_b and ok_c)

        # D3/D4 fixture: real write-in facts, several
        # identifiers, shared targets, plus a bare target
        # entry ("canine") for the D4 rebuild.
        store2 = wi.new_store()
        wi.write_fill(store2, "wolf", "is_a", "canine",
                      "dictionary_note")
        # PROM-1 re-base (2026-10-29): same repair as the
        # rich-store build above - the second write_fill
        # is the same "verifier" class and no longer
        # verifies wolf's is_a, which D3 asserts verified.
        # A dictionary-class attestation through the
        # shared engine replaces it; standing provenance
        # remains the verifier's, attestations stays 2.
        seq2 = wi._next_seq(store2)
        wi._attest(store2, "wolf", "is_a", "canine",
                   {"source": "dictionary",
                    "evidence": "seed_exemplar"}, seq2)
        wi._append_self_record(store2, {"seq": seq2,
                                        "kind":
                                        "cross_attestation",
                                        "outcome":
                                        "reattested",
                                        "word": "wolf",
                                        "identifier": "is_a"})
        wi.write_fill(store2, "dog", "is_a", "canine",
                      "dictionary_note")
        wi.write_fill(store2, "wolf", "originates", "eat",
                      "finder_parse")
        wi.write_fill(store2, "sheep", "target_of", "eat",
                      "finder_parse")
        store2["entries"]["canine"] = {
            "subcoordinates": {}, "conflicts": []}

        def scan_oracle(root, target):
            out = []
            entry_dir = os.path.join(root, "entry_data",
                                     BASE_GRAPH)
            for fname in sorted(os.listdir(entry_dir)):
                if not fname.endswith(".json"):
                    continue
                w = fname[:-len(".json")]
                env, _ = _load_entry_file(root, BASE_GRAPH, w)
                for ident in sorted(
                        env["record"]["subcoordinates"]):
                    sub = env["record"]["subcoordinates"] \
                        [ident]
                    if sub["value"] == target:
                        out.append(
                            {"entry": w, "identifier": ident,
                             "graph": BASE_GRAPH,
                             "coord": gs.dist_address(w),
                             "state": sub["status"],
                             "value": target})
            out.sort(key=lambda x: (x["identifier"],
                                    x["entry"]))
            return out

        d3_root = os.path.join(cdir, "d3")
        build_layout(store2, d3_root)
        links_canine = incoming_links(d3_root, "canine")
        links_eat = incoming_links(d3_root, "eat")
        check("p2_d3_incoming_links_equal_scan_oracle",
              links_canine == scan_oracle(d3_root, "canine")
              and links_eat == scan_oracle(d3_root, "eat")
              and [(x["entry"], x["identifier"], x["state"])
                   for x in links_canine]
              == [("dog", "is_a", "candidate"),
                  ("wolf", "is_a", "verified")]
              and [(x["entry"], x["identifier"])
                   for x in links_eat]
              == [("wolf", "originates"),
                  ("sheep", "target_of")])

        # D4: target entry absent on rebuild - the
        # incoming-pointer worklist is unchanged.
        store4 = copy.deepcopy(store2)
        del store4["entries"]["canine"]
        d4_root = os.path.join(cdir, "d4")
        build_layout(store4, d4_root)
        check("p2_d4_incoming_links_target_entry_absent",
              incoming_links(d4_root, "canine") == links_canine
              and incoming_links(d4_root, "canine")
              == scan_oracle(d4_root, "canine"))

        # D5: door purity in the new format - exactly the
        # four pointer fields per row, no fact-only key
        # anywhere in a door file.
        def doors_pure_v3(root):
            ident_dir = os.path.join(root, "identifier_data")
            for fname in sorted(os.listdir(ident_dir)):
                if not fname.endswith(".json"):
                    continue
                with open(os.path.join(ident_dir, fname),
                          "rb") as fh:
                    env = json.loads(fh.read().decode(
                        "utf-8"))
                if keys_of(env) & _FACT_ONLY_KEYS:
                    return False
                for rows in env["by_source"].values():
                    for row in rows:
                        if sorted(row.keys()) != [
                                "coord", "graph", "state",
                                "target"]:
                            return False
                for rows in env["by_target"].values():
                    for row in rows:
                        if sorted(row.keys()) != [
                                "coord", "graph", "source",
                                "state"]:
                            return False
            return True

        check("p2_d5_door_purity_exact_pointer_fields",
              doors_pure_v3(d1_rich)
              and doors_pure_v3(pristine)
              and doors_pure_v3(d3_root))

        # ---- PACK C (2026-10-26): stored-once hardening.
        # Sealed expectations: hidden_files/fix-campaign-
        # 2026-10-26/pack-C-expectations.md. Argon finds
        # F1/F8/items 2-4 (Gemini 4 Argon); claims-02
        # Claims 5/6/8; claims-14 cost measurement.
        def pc_refused(fn, needle):
            err = None
            try:
                fn()
            except StoredOnceError as exc:
                err = exc
            return err is not None and needle in str(err)

        # C1: words named like fact keys ("status",
        # "provenance", "attestations") store, load, and
        # round-trip; a fact VALUE may carry such a name.
        r = fresh("packc1")
        pc_ok = True
        for w_pc, v_pc in (("status", "condition"),
                           ("provenance", "origin"),
                           ("attestations", "count")):
            rep_pc = store_entry(
                r, BASE_GRAPH, w_pc,
                {"subcoordinates":
                 {"is_a": p2_fact(v_pc, "candidate", 21)},
                 "conflicts": []})
            pc_ok = pc_ok and rep_pc["outcome"] == "stored"
        rep_pc = store_entry(
            r, BASE_GRAPH, "health",
            {"subcoordinates":
             {"is_a": p2_fact("status", "candidate", 22)},
             "conflicts": []})
        loaded_pc = load_layout(r)
        export_pc = json.loads(
            derive_export(r).decode("utf-8"))
        check("packc_fact_key_words_store_load_roundtrip",
              pc_ok and rep_pc["outcome"] == "stored"
              and loaded_pc["entries"]["status"]
              ["subcoordinates"]["is_a"]["value"]
              == "condition"
              and loaded_pc["entries"]["provenance"]
              ["subcoordinates"]["is_a"]["value"] == "origin"
              and loaded_pc["entries"]["attestations"]
              ["subcoordinates"]["is_a"]["value"] == "count"
              and loaded_pc["entries"]["health"]
              ["subcoordinates"]["is_a"]["value"]
              == "status"
              and search_by_word(r, "status")
              == loaded_pc["entries"]["status"]
              and export_pc["entries"]["status"]
              ["subcoordinates"]["is_a"]["value"]
              == "condition")

        # C1b: an extra top-level envelope key (fact content
        # smuggled as a key) refuses by name - structurally,
        # on the coordinate door and the identifier door.
        r = fresh("packc1b")
        cpath_pc = _coord_path(r, BASE_GRAPH,
                               gs.dist_address("wolf"))
        with open(cpath_pc, "rb") as fh:
            cenv_pc = json.loads(fh.read().decode("utf-8"))
        cenv_pc["definition"] = {"text": "smuggled"}
        with open(cpath_pc, "wb") as fh:
            fh.write(json.dumps(
                cenv_pc, sort_keys=True).encode("utf-8"))
        ok_coord_key = pc_refused(lambda: load_layout(r),
                                  cpath_pc)
        r = fresh("packc1c")
        ipath_pc = _ident_path(r, "is_a")
        with open(ipath_pc, "rb") as fh:
            ienv_pc = json.loads(fh.read().decode("utf-8"))
        ienv_pc["definition"] = {"text": "smuggled"}
        with open(ipath_pc, "wb") as fh:
            fh.write(json.dumps(
                ienv_pc, sort_keys=True).encode("utf-8"))
        ok_ident_key = pc_refused(
            lambda: search_by_identifier(r, "is_a"),
            ipath_pc)
        check("packc_envelope_extra_key_refused",
              ok_coord_key and ok_ident_key)

        # C2: accretion type integrity - None / string /
        # bool counters and non-int events refuse by name,
        # tree byte-unchanged.
        def pc_accretion_refused(root, word, mutate,
                                 needle):
            before = tree_bytes(root)
            rec = search_by_word(root, word)
            mutate(rec["subcoordinates"]["is_a"])
            err = None
            try:
                store_entry(root, BASE_GRAPH, word, rec)
            except StoredOnceError as exc:
                err = exc
            return err is not None and needle in str(err) \
                and tree_bytes(root) == before

        r = verified_root("packc2a")
        ok_none = pc_accretion_refused(
            r, "wolf",
            lambda f: f.__setitem__("attestations", None),
            "attestations_not_int:is_a")
        ok_str = pc_accretion_refused(
            r, "wolf",
            lambda f: f.__setitem__("attestations",
                                    "corrupted"),
            "attestations_not_int:is_a")
        ok_seq = pc_accretion_refused(
            r, "wolf",
            lambda f: f.__setitem__("last_seq", None),
            "last_seq_not_int:is_a")

        def _ev_mut(f):
            f["attested_events"] = [1, 2, "3"]
            f["attestations"] = 3
            f["last_seq"] = 3

        ok_ev = pc_accretion_refused(
            r, "wolf", _ev_mut,
            "attested_events_not_append_only:is_a")
        # The distinguishing bool case: the record really
        # differs (events grow), and attestations=True -
        # which == 1 under Python equality - must refuse,
        # never be written as the counter.
        r = fresh("packc2b")
        rec = search_by_word(r, "wolf")
        f = rec["subcoordinates"]["is_a"]
        f["attestations"] = True
        f["attested_events"] = [1, 2]
        f["last_seq"] = 2
        before = tree_bytes(r)
        err = None
        try:
            store_entry(r, BASE_GRAPH, "wolf", rec)
        except StoredOnceError as exc:
            err = exc
        ok_bool = err is not None \
            and "attestations_not_int:is_a" in str(err) \
            and tree_bytes(r) == before
        check("packc_accretion_type_integrity",
              ok_none and ok_str and ok_seq and ok_ev
              and ok_bool)

        # C3: transactional doors - a malformed door refuses
        # the store, tree byte-unchanged, on the advance
        # path, the initial path, and (coordinate door) the
        # initial path again.
        r = fresh("packc3a")
        ipath3 = _ident_path(r, "is_a")
        with open(ipath3, "wb") as fh:
            fh.write(b'{"door": broken')
        before = tree_bytes(r)
        rec = search_by_word(r, "wolf")
        f = rec["subcoordinates"]["is_a"]
        f["attested_events"] = [1, 2]
        f["attestations"] = 2
        f["last_seq"] = 2
        f["status"] = "verified"
        # PROM-1 re-base (2026-10-29): as in verified_root,
        # the flip gains a provenance_history spanning two
        # classes, so it passes the PROM-1 gate and the
        # refusal asserted below is the malformed door's.
        f["provenance_history"] = [
            {"source": "verifier",
             "evidence": "dictionary_note"},
            {"source": "dictionary",
             "evidence": "webster1913"}]
        err = None
        try:
            store_entry(r, BASE_GRAPH, "wolf", rec)
        except StoredOnceError as exc:
            err = exc
        ok_adv = err is not None and ipath3 in str(err) \
            and tree_bytes(r) == before
        r = fresh("packc3b")
        ipath3 = _ident_path(r, "is_a")
        with open(ipath3, "wb") as fh:
            fh.write(b'{"door": broken')
        before = tree_bytes(r)
        zebra = {"subcoordinates":
                 {"is_a": p2_fact("equine", "candidate", 8)},
                 "conflicts": []}
        err = None
        try:
            store_entry(r, BASE_GRAPH, "zebra", zebra)
        except StoredOnceError as exc:
            err = exc
        ok_init = err is not None and ipath3 in str(err) \
            and tree_bytes(r) == before
        # A colliding pair (two words, one address) found
        # by a deterministic search over the address
        # function itself: the first word's layout carries
        # the coordinate door the second word's initial
        # store must pre-load.
        by_addr = {}
        base_w = mate = base_addr = None
        for i in range(2000):
            cand = "packcw%04d" % i
            addr_c = gs.dist_address(cand)
            if addr_c in by_addr:
                base_w, mate, base_addr = \
                    by_addr[addr_c], cand, addr_c
                break
            by_addr[addr_c] = cand
        store_c3 = {"seq": 1, "entries": {
            wi.SELF_KEY: {"encounters": [], "gaps": {}},
            base_w: {"subcoordinates": {},
                     "conflicts": []}}}
        r = os.path.join(cdir, "packc3c")
        build_layout(store_c3, r)
        cpath3 = _coord_path(r, BASE_GRAPH, base_addr)
        with open(cpath3, "wb") as fh:
            fh.write(b'{"door": broken')
        before = tree_bytes(r)
        err = None
        try:
            store_entry(r, BASE_GRAPH, mate,
                        {"subcoordinates": {},
                         "conflicts": []})
        except StoredOnceError as exc:
            err = exc
        ok_coord_tx = mate is not None \
            and err is not None and cpath3 in str(err) \
            and tree_bytes(r) == before
        check("packc_store_transactional_door_refusal",
              ok_adv and ok_init and ok_coord_tx)

        # C4: path validation at the storage boundary -
        # hostile words, identifiers, and graphs refuse by
        # name before any write; build_layout refuses
        # before removing or writing anything.
        r = fresh("packc4")
        before = tree_bytes(r)
        bare = {"subcoordinates": {}, "conflicts": []}
        ok_w = True
        for bad_word in ("../escaped", "../../escaped2",
                         "a/b", "..", ""):
            ok_w = ok_w and pc_refused(
                lambda bw=bad_word: store_entry(
                    r, BASE_GRAPH, bw,
                    copy.deepcopy(bare)),
                "is not a safe storage path component")
        ok_i = True
        for bad_ident in ("../idor", "a/b"):
            ok_i = ok_i and pc_refused(
                lambda bi=bad_ident: store_entry(
                    r, BASE_GRAPH, "newword",
                    {"subcoordinates":
                     {bi: p2_fact("x", "candidate", 31)},
                     "conflicts": []}),
                "is not a safe storage path component")
        ok_g = pc_refused(
            lambda: store_entry(r, "../x", "newword",
                                copy.deepcopy(bare)),
            "is not a safe storage path component")
        evil_store = {"seq": 1, "entries": {
            wi.SELF_KEY: {"encounters": [], "gaps": {}},
            "../evil": {"subcoordinates": {},
                        "conflicts": []}}}
        evil_root = os.path.join(cdir, "packc4_evil")
        ok_build = pc_refused(
            lambda: build_layout(evil_store, evil_root),
            "'../evil'") and not os.path.exists(evil_root)
        check("packc_path_validation",
              ok_w and ok_i and ok_g and ok_build
              and tree_bytes(r) == before
              and not os.path.exists(
                  os.path.join(r, "escaped2.json"))
              and not os.path.exists(
                  os.path.join(r, "idor.json"))
              and not os.path.exists(os.path.join(
                  r, "entry_data", "escaped.json")))

        # C5: incoming_links never returns a phantom row -
        # a use injected consistently into BOTH door views
        # (so the two-view proof passes) is excluded by the
        # _resolve_use cross-check, the genuine rows still
        # equal the full-scan oracle, and the strict loader
        # still refuses the same door outright.
        proot = os.path.join(cdir, "packc5")
        shutil.copytree(d3_root, proot)
        ipath5 = _ident_path(proot, "is_a")
        with open(ipath5, "rb") as fh:
            env5 = json.loads(fh.read().decode("utf-8"))
        wolf_coord5 = list(gs.dist_address("wolf"))
        env5["by_source"]["wolf"].append(
            {"target": "zebra", "graph": BASE_GRAPH,
             "coord": wolf_coord5, "state": "candidate"})
        env5["by_target"].setdefault("zebra", []).append(
            {"source": "wolf", "graph": BASE_GRAPH,
             "coord": wolf_coord5, "state": "candidate"})
        with open(ipath5, "wb") as fh:
            fh.write(json.dumps(
                env5, sort_keys=True).encode("utf-8"))
        check("packc_incoming_links_phantom_excluded",
              incoming_links(proot, "zebra") == []
              and incoming_links(proot, "canine")
              == scan_oracle(proot, "canine")
              and pc_refused(lambda: load_layout(proot),
                             ipath5))

    print("FINAL_STORE_SHA256 " + orig_hash)
    print("FINAL_STORE_JSON " + orig_canonical)

    passed = sum(1 for r in results if r)
    print("Passed %d of %d checks" % (passed, len(results)))
    print("ALL CHECKS PASS" if passed == len(results)
          else "SOME CHECKS FAILED")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(run_checks())
