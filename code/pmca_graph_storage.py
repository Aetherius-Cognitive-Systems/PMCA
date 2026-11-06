"""PMCA Graph Storage - the graph structured in the storage.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's
architecture.

Ruling (Jonathan, 2026-10-22): "All things have to be writing
to storage... the graphs have to be structured in the storage
as well." Persistence is not an in-memory graph occasionally
dumped to a file. The graph structure itself lives in storage
- graphs, entries at coordinates, subcoordinates, edges as
structured stored records - and the canonical JSON artifacts
are DERIVED exports and checkpoints, produced from the stored
structure, not its primary residence.

This module is the PROOF SLICE behind the design document
(files/pmca-storage-layout-design-2026-10-23.md). The layout
itself is UNRULED: nothing here is the final architecture
until Jonathan rules. The slice proves the layout's core
mechanics against a real write-in store:

- export_layout(store, root) materializes a store as a tree
  of small record files; import_layout(root) reassembles it,
  losslessly (canonical bytes and store hash identical).
- One file per coordinate, holding every entry at that
  coordinate, each entry stored verbatim in the write-in's
  own shape. Addressing is a pure function of the word, so
  there is no word index: get-by-word IS get-by-coordinate.
- put_subcoordinate writes exactly one coordinate's file:
  write-through at record granularity.
- derive_export(root) reassembles the store from the tree
  and emits pmca_write_in's canonical bytes - the old single
  file, demoted to an output of the storage.
- import validates EVERY record file's shape (format, graph,
  coordinate agreement with its path, and every word's
  dist_address agreement with the record's coordinate) and
  raises StorageLayoutError naming the offending file. A
  failed import rewrites nothing; import never writes at all.
- Relatedness is read only from stored subcoordinate values.
  The tree's directory adjacency is filing, not meaning:
  nearby entries are deliberately unrelated, and the check
  battery proves an address-adjacent pair's records do not
  reference each other.

Layout (PROPOSAL):

    <root>/graphs/<graph>/graph.json            descriptor
    <root>/graphs/<graph>/entries/L<layer>/<dimension>/<index>.json
    <root>/graphs/__self/graph.json              descriptor
    <root>/graphs/__self/self_entry.json         the self record

The base graph is named "language" (PROPOSAL). The write-in
store's self_entry lands in the reserved "__self" graph
(PROVISIONAL name, flagged OPEN in the design), and the
store's event counter rides in the self record: every event
is already a record on the self entry, so the counter that
numbers them is stored with the record it numbers.

Design decisions made inside this slice, stated in the open:
- export_layout is a FULL materialization: it removes any
  existing graphs/ tree under the root and writes the tree
  fresh, so the tree always equals the store exactly.
  put_subcoordinate is the incremental write path.
- The storage layer is dumb about meaning: it stores
  attestation-shaped records as given, allocates no event
  numbers, and enforces only structure. Candidate/verified
  discipline stays in the write-in layer above it.
- put_subcoordinate creates the word's entry (in write-in
  shape) when the coordinate record has none, mirroring the
  write-in's own get-or-create entry semantics; the word is
  used exactly as given (callers pass store keys).
- Atomic-write temp files are dot-prefixed; readers skip
  dotfiles, so a crash-stranded temp is never mistaken for
  a record.

Deterministic. Stdlib only at module level; sibling modules
are imported inside functions and the check battery, never
at import time.
"""

import copy
import json
import os
import re
import shutil
import tempfile

BASE_GRAPH = "language"
SELF_GRAPH = "__self"
SELF_RECORD_NAME = "self_entry.json"

RECORD_FORMAT = "pmca-graph-storage/1"
GRAPH_FORMAT = "pmca-graph-storage-graph/1"
SELF_FORMAT = "pmca-graph-storage-self/1"

_LAYER_RE = re.compile(r"^L(-?\d+)$")
_INDEX_RE = re.compile(r"^(-?\d+)\.json$")


class StorageLayoutError(Exception):
    """A storage layout is malformed, a record file fails
    validation, or a layout write cannot be completed. The
    message names the offending file wherever one exists."""


# ---------------------------------------------------------------------
# Canonical bytes + atomic per-record writes
# ---------------------------------------------------------------------

def _canon_bytes(obj):
    """The one serialization for every file in the tree:
    sorted keys, no spaces, ASCII - the write-in's canonical
    discipline applied per record."""
    return json.dumps(obj, sort_keys=True,
                      separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _atomic_write(path, data):
    """Write data to path atomically: a dot-prefixed temp
    file in the same directory, flush + fsync, os.replace
    onto path, directory fsync when available. No temp file
    is left behind, on success or on failure."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory,
                                    prefix=".pmca-graph-",
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
        raise StorageLayoutError("cannot write record file: "
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
# Paths
# ---------------------------------------------------------------------

def _graph_dir(root, graph):
    return os.path.join(str(root), "graphs", graph)


def _coord_path(root, graph, coord):
    layer, dimension, index = coord
    return os.path.join(_graph_dir(root, graph), "entries",
                        "L%d" % layer, dimension,
                        "%d.json" % index)


def _descriptor(graph):
    return {"format": GRAPH_FORMAT, "graph": graph,
            "role": "self" if graph == SELF_GRAPH else "base"}


# ---------------------------------------------------------------------
# Reading + validation. Every failure names the file.
# ---------------------------------------------------------------------

def _read_record(path):
    """Parse one record file as a JSON object. Any failure
    raises StorageLayoutError naming the file."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise StorageLayoutError("cannot read record file: "
                                 + str(path) + ": " + str(exc))
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StorageLayoutError("record file is not valid "
                                 "JSON: " + str(path) + ": "
                                 + str(exc))
    if not isinstance(record, dict):
        raise StorageLayoutError("record file is not a JSON "
                                 "object: " + str(path))
    return record


def _validate_entry(word, entry, path):
    """A stored word entry must carry the write-in's entry
    shape: a dict with a subcoordinates dict and a conflicts
    list. (Population, definition, and variables keys ride
    along unexamined: storage does not interpret them.)"""
    if not isinstance(entry, dict):
        raise StorageLayoutError("entry for word %r is not a "
                                 "record: %s" % (word, path))
    if not isinstance(entry.get("subcoordinates"), dict):
        raise StorageLayoutError("entry for word %r lacks a "
                                 "subcoordinates record: %s"
                                 % (word, path))
    if not isinstance(entry.get("conflicts"), list):
        raise StorageLayoutError("entry for word %r lacks a "
                                 "conflicts list: %s"
                                 % (word, path))


def _validate_coord_record(record, graph, coord, path):
    """A coordinate record must agree with where it is filed:
    its format, its graph, its stated coordinate == the
    path's coordinate, and every word it holds must actually
    address to that coordinate under pmca_graphspace's
    dist_address. A record out of place is a corrupt record,
    refused, never repaired in place."""
    import pmca_graphspace as gs
    if record.get("format") != RECORD_FORMAT:
        raise StorageLayoutError("record file has wrong "
                                 "format: " + str(path))
    if record.get("graph") != graph:
        raise StorageLayoutError("record file names the "
                                 "wrong graph: " + str(path))
    if record.get("coordinate") != [coord[0], coord[1],
                                    coord[2]]:
        raise StorageLayoutError("record file's coordinate "
                                 "disagrees with its path: "
                                 + str(path))
    entries = record.get("entries")
    if not isinstance(entries, dict):
        raise StorageLayoutError("record file holds no "
                                 "entries record: " + str(path))
    for word, entry in entries.items():
        if gs.dist_address(word) != coord:
            raise StorageLayoutError(
                "word %r is filed at a coordinate that is "
                "not its address: %s" % (word, path))
        _validate_entry(word, entry, path)


def _load_descriptor(graph_dir, graph):
    path = os.path.join(graph_dir, "graph.json")
    if not os.path.isfile(path):
        raise StorageLayoutError("graph has no descriptor: "
                                 + path)
    record = _read_record(path)
    if record.get("format") != GRAPH_FORMAT:
        raise StorageLayoutError("descriptor has wrong "
                                 "format: " + path)
    if record.get("graph") != graph:
        raise StorageLayoutError("descriptor names the "
                                 "wrong graph: " + path)
    return record


def _load_entries_tree(graph_dir, graph, words):
    """Read every coordinate record of one graph into words
    (word -> entry), validating each file. A word already
    loaded from another file is a structural contradiction
    and is refused, naming the second file."""
    entries_root = os.path.join(graph_dir, "entries")
    if not os.path.isdir(entries_root):
        return
    import pmca_graphspace as gs
    for layer_name in sorted(os.listdir(entries_root)):
        if layer_name.startswith("."):
            continue
        layer_path = os.path.join(entries_root, layer_name)
        m = _LAYER_RE.match(layer_name)
        if m is None or not os.path.isdir(layer_path):
            raise StorageLayoutError("unexpected entry in "
                                     "entries tree: "
                                     + layer_path)
        layer = int(m.group(1))
        for dim_name in sorted(os.listdir(layer_path)):
            if dim_name.startswith("."):
                continue
            dim_path = os.path.join(layer_path, dim_name)
            if (len(dim_name) != 1
                    or dim_name not in gs.ALPHABET
                    or not os.path.isdir(dim_path)):
                raise StorageLayoutError("unexpected entry "
                                         "in entries tree: "
                                         + dim_path)
            for fname in sorted(os.listdir(dim_path)):
                if fname.startswith("."):
                    continue
                fpath = os.path.join(dim_path, fname)
                fm = _INDEX_RE.match(fname)
                if fm is None or not os.path.isfile(fpath):
                    raise StorageLayoutError("unexpected "
                                             "file in entries "
                                             "tree: " + fpath)
                coord = (layer, dim_name, int(fm.group(1)))
                record = _read_record(fpath)
                _validate_coord_record(record, graph, coord,
                                       fpath)
                for word, entry in record["entries"].items():
                    if word in words:
                        raise StorageLayoutError(
                            "word %r is stored in two "
                            "files; second: %s" % (word, fpath))
                    words[word] = entry


def _load_self_record(graph_dir):
    """Read and validate the self graph's record: exactly
    graph.json + self_entry.json, the record holding seq,
    encounters, and gaps."""
    for name in sorted(os.listdir(graph_dir)):
        if name.startswith("."):
            continue
        path = os.path.join(graph_dir, name)
        if name in ("graph.json", SELF_RECORD_NAME) \
                and os.path.isfile(path):
            continue
        raise StorageLayoutError("unexpected entry in self "
                                 "graph: " + path)
    path = os.path.join(graph_dir, SELF_RECORD_NAME)
    if not os.path.isfile(path):
        raise StorageLayoutError("self graph has no self "
                                 "record: " + path)
    record = _read_record(path)
    if record.get("format") != SELF_FORMAT:
        raise StorageLayoutError("self record has wrong "
                                 "format: " + path)
    if record.get("graph") != SELF_GRAPH:
        raise StorageLayoutError("self record names the "
                                 "wrong graph: " + path)
    if not isinstance(record.get("encounters"), list):
        raise StorageLayoutError("self record lacks its "
                                 "encounters list: " + path)
    if not isinstance(record.get("gaps"), (list, dict)):
        raise StorageLayoutError("self record lacks its "
                                 "gaps list/index: " + path)
    seq = record.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise StorageLayoutError("self record lacks its "
                                 "event counter: " + path)
    return record


# ---------------------------------------------------------------------
# Export: a full materialization of a write-in store
# ---------------------------------------------------------------------

def export_layout(store, root):
    """Materialize a pmca_write_in store as the storage
    tree under root. Word entries land in the base graph at
    their dist_address coordinates, one file per coordinate,
    each entry verbatim; the self entry lands in the self
    graph with the store's event counter. Any existing
    graphs/ tree under root is removed first, so the tree
    always equals the store exactly. Returns a summary."""
    import pmca_graphspace as gs
    import pmca_write_in as wi
    if (not isinstance(store, dict) or "entries" not in store
            or "seq" not in store
            or wi.SELF_KEY not in store["entries"]):
        raise StorageLayoutError("not a write-in store")
    self_entry = store["entries"][wi.SELF_KEY]
    # P1: gaps may be the typed index (dict) or a pre-P1
    # list; both are the self entry's stored gap facts.
    if (not isinstance(self_entry.get("encounters"), list)
            or not isinstance(self_entry.get("gaps"),
                              (list, dict))):
        raise StorageLayoutError("store self entry lacks "
                                 "encounters/gaps lists")
    root = str(root)
    graphs_dir = os.path.join(root, "graphs")
    if os.path.isdir(graphs_dir):
        shutil.rmtree(graphs_dir)
    _atomic_write(os.path.join(graphs_dir, BASE_GRAPH,
                               "graph.json"),
                  _canon_bytes(_descriptor(BASE_GRAPH)))
    _atomic_write(os.path.join(graphs_dir, SELF_GRAPH,
                               "graph.json"),
                  _canon_bytes(_descriptor(SELF_GRAPH)))
    groups = {}
    for word, entry in store["entries"].items():
        if word == wi.SELF_KEY:
            continue
        if not isinstance(entry, dict):
            raise StorageLayoutError("store entry is not a "
                                     "record: " + str(word))
        groups.setdefault(gs.dist_address(word),
                          {})[word] = entry
    n_files = 0
    for coord in sorted(groups):
        record = {"format": RECORD_FORMAT, "graph": BASE_GRAPH,
                  "coordinate": [coord[0], coord[1], coord[2]],
                  "entries": groups[coord]}
        _atomic_write(_coord_path(root, BASE_GRAPH, coord),
                      _canon_bytes(record))
        n_files += 1
    self_record = {"format": SELF_FORMAT, "graph": SELF_GRAPH,
                   "seq": store["seq"],
                   "encounters": self_entry["encounters"],
                   "gaps": self_entry["gaps"]}
    _atomic_write(os.path.join(graphs_dir, SELF_GRAPH,
                               SELF_RECORD_NAME),
                  _canon_bytes(self_record))
    return {"root": root, "graphs": [BASE_GRAPH, SELF_GRAPH],
            "coordinate_files": n_files,
            "word_entries": sum(len(g)
                                for g in groups.values())}


# ---------------------------------------------------------------------
# Import: strict reassembly. Reads only; never writes.
# ---------------------------------------------------------------------

def import_layout(root):
    """Reassemble a write-in store from the storage tree
    under root. Every descriptor and record file is
    validated; the first failure raises StorageLayoutError
    naming the offending file. Nothing is rewritten, ever:
    this function performs no writes."""
    import pmca_write_in as wi
    root = str(root)
    graphs_dir = os.path.join(root, "graphs")
    if not os.path.isdir(graphs_dir):
        raise StorageLayoutError("no graphs/ tree in "
                                 "storage root: " + root)
    words = {}
    self_record = None
    for graph in sorted(os.listdir(graphs_dir)):
        if graph.startswith("."):
            continue
        graph_dir = os.path.join(graphs_dir, graph)
        if not os.path.isdir(graph_dir):
            raise StorageLayoutError("unexpected file in "
                                     "graphs/: " + graph_dir)
        _load_descriptor(graph_dir, graph)
        if graph == SELF_GRAPH:
            self_record = _load_self_record(graph_dir)
        else:
            # A non-self graph may hold only its descriptor
            # and its entries tree.
            for name in sorted(os.listdir(graph_dir)):
                if name.startswith("."):
                    continue
                path = os.path.join(graph_dir, name)
                if name == "graph.json" \
                        and os.path.isfile(path):
                    continue
                if name == "entries" \
                        and os.path.isdir(path):
                    continue
                raise StorageLayoutError("unexpected entry "
                                         "in graph: " + path)
            _load_entries_tree(graph_dir, graph, words)
    if self_record is None:
        raise StorageLayoutError("storage root has no self "
                                 "graph: " + root)
    entries = dict(words)
    entries[wi.SELF_KEY] = {
        "encounters": self_record["encounters"],
        "gaps": self_record["gaps"]}
    return {"seq": self_record["seq"], "entries": entries}


# ---------------------------------------------------------------------
# One-subcoordinate write-through: exactly one file changes
# ---------------------------------------------------------------------

def put_subcoordinate(root, graph, word, identifier,
                      record_dict):
    """Store one subcoordinate record on one word's entry,
    in the layout. Computes the word's coordinate, reads
    that coordinate's file (validating it), sets the one
    subcoordinate, and rewrites that one file atomically.
    Creates the coordinate file, and the entry in write-in
    shape, when they do not exist yet. The record is stored
    as given: attestation discipline belongs to the layer
    above storage. Returns a placement report."""
    import pmca_graphspace as gs
    if not isinstance(record_dict, dict):
        raise StorageLayoutError("subcoordinate record must "
                                 "be a dict: "
                                 + str(identifier))
    root = str(root)
    graph_dir = _graph_dir(root, graph)
    if not os.path.isdir(graph_dir):
        raise StorageLayoutError("no such graph in storage: "
                                 + graph_dir)
    _load_descriptor(graph_dir, graph)
    coord = gs.dist_address(word)
    path = _coord_path(root, graph, coord)
    if os.path.isfile(path):
        record = _read_record(path)
        _validate_coord_record(record, graph, coord, path)
    else:
        record = {"format": RECORD_FORMAT, "graph": graph,
                  "coordinate": [coord[0], coord[1], coord[2]],
                  "entries": {}}
    entry = record["entries"].get(word)
    if entry is None:
        entry = {"subcoordinates": {}, "conflicts": []}
        record["entries"][word] = entry
    else:
        _validate_entry(word, entry, path)
    entry["subcoordinates"][identifier] = copy.deepcopy(
        record_dict)
    _atomic_write(path, _canon_bytes(record))
    return {"graph": graph, "word": word,
            "identifier": identifier, "coordinate": coord,
            "path": path}


# ---------------------------------------------------------------------
# The canonical file, demoted to a derived export
# ---------------------------------------------------------------------

def derive_export(root):
    """The canonical write-in store bytes, derived from the
    resident structure under root: reassemble the store by
    strict import and serialize with pmca_write_in's own
    canonical form. This is what the old single store file
    becomes under this layout - an output, not a residence."""
    import pmca_write_in as wi
    return wi.canonical(import_layout(root)).encode("utf-8")


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_graph_storage.py
# Builds a rich store with the REAL write-in, population,
# finders, and dictionary, then exercises the layout against
# it in temporary storage roots.
# ---------------------------------------------------------------------

def run_checks():
    import hashlib
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

    # ---- Build the rich store with the real modules. ----
    store = wi.new_store()
    pop.populate_word(store, "banana", "suite")
    pop.enrich_from_dictionary(store, "banana")
    s_wolf = "The wolf eats the sheep."
    pop.encounter(store, s_wolf, noun_finder.find(s_wolf),
                  verb_finder.find(s_wolf))
    pop.encounter(store, s_wolf, noun_finder.find(s_wolf),
                  verb_finder.find(s_wolf))
    pop.populate_word(store, "flibberty", "suite")
    pop.enrich_from_dictionary(store, "flibberty")

    binding = wi.binding_for(store, "wolf", "eat")
    # D13 (2026-10-26; claims-05 F3b): two identical
    # encounters no longer verify the binding — candidate.
    check("built_store_binding_candidate",
          binding is not None
          and binding["status"] == "candidate"
          and binding["attestations"] == 2, repr(binding))
    self_built = wi.entry_view(store, wi.SELF_KEY)
    # P1 re-base: gaps are the typed index; the flibberty
    # definition gap is asserted by its key and reason.
    check("built_store_self_records_and_gap",
          len(self_built["encounters"]) >= 6
          and self_built["gaps"]
          ["slot::flibberty::definition"]["reason"]
          == "not_in_dictionary")

    # ---- Address-adjacent probe: a word whose address sits
    # one index step from a stored word's, same layer and
    # dimension. Found by search over a fixed candidate
    # list, so the pair is verified, not assumed. ----
    stored_words = sorted(w for w in store["entries"]
                          if w != wi.SELF_KEY)
    addr = {w: gs.dist_address(w) for w in stored_words}
    probe = None
    for cand in ("cup", "big", "and", "dog", "cat", "goat",
                 "sun", "fig", "oak", "sea"):
        if cand in store["entries"]:
            continue
        ca = gs.dist_address(cand)
        for w in stored_words:
            wa = addr[w]
            if (ca[0] == wa[0] and ca[1] == wa[1]
                    and abs(ca[2] - wa[2]) == 1):
                probe = (cand, w)
                break
        if probe is not None:
            break
    check("adjacency_probe_found", probe is not None,
          repr(probe))
    probe_word, host_word = probe
    pop.populate_word(store, probe_word, "suite")
    probe_addr = gs.dist_address(probe_word)
    host_addr = gs.dist_address(host_word)
    check("probe_addresses_adjacent",
          probe_addr[0] == host_addr[0]
          and probe_addr[1] == host_addr[1]
          and abs(probe_addr[2] - host_addr[2]) == 1,
          "%s %r beside %s %r"
          % (probe_word, probe_addr, host_word, host_addr))

    # Snapshot the finished store: everything below is
    # checked against these bytes.
    self_view = wi.entry_view(store, wi.SELF_KEY)
    word_entries = {w: store["entries"][w]
                    for w in store["entries"]
                    if w != wi.SELF_KEY}
    expected_files = {}
    for w in word_entries:
        coord = gs.dist_address(w)
        rel = os.path.join("graphs", BASE_GRAPH, "entries",
                           "L%d" % coord[0], coord[1],
                           "%d.json" % coord[2])
        expected_files.setdefault(rel, set()).add(w)
    rel_of = {w: rel for rel, words in expected_files.items()
              for w in words}
    orig_canonical = wi.canonical(store)
    orig_hash = wi.store_hash(store)

    with tempfile.TemporaryDirectory() as root1, \
            tempfile.TemporaryDirectory() as root2, \
            tempfile.TemporaryDirectory() as root3, \
            tempfile.TemporaryDirectory() as root4:
        summary = export_layout(store, root1)
        files1 = tree_bytes(root1)
        entry_files = {rel: b for rel, b in files1.items()
                       if "entries" in rel.split(os.sep)}
        check("export_wrote_expected_coordinate_files",
              set(entry_files) == set(expected_files)
              and summary["word_entries"] == len(word_entries)
              and summary["coordinate_files"]
              == len(expected_files),
              "%d coordinate files" % len(entry_files))
        own_ok = True
        for rel, words in expected_files.items():
            rec = json.loads(files1[rel].decode("utf-8"))
            coord = gs.dist_address(next(iter(words)))
            if set(rec["entries"].keys()) != words:
                own_ok = False
            if rec["coordinate"] != [coord[0], coord[1],
                                     coord[2]]:
                own_ok = False
            if rec["graph"] != BASE_GRAPH:
                own_ok = False
        check("coordinate_records_hold_only_their_own_entries",
              own_ok)
        self_rel = os.path.join("graphs", SELF_GRAPH,
                                SELF_RECORD_NAME)
        self_rec = json.loads(files1[self_rel].decode("utf-8"))
        check("self_record_holds_encounters_gaps_seq",
              self_rec["encounters"] == self_view["encounters"]
              and self_rec["gaps"] == self_view["gaps"]
              and self_rec["seq"] == store["seq"]
              and "slot::flibberty::definition"
              in self_rec["gaps"])

        # ---- Lossless round trip; the canonical file is a
        # derived export of the resident structure. ----
        store2 = import_layout(root1)
        check("round_trip_canonical_identical",
              wi.canonical(store2) == orig_canonical)
        check("round_trip_hash_identical",
              wi.store_hash(store2) == orig_hash)
        check("derive_export_equals_original_canonical",
              derive_export(root1)
              == orig_canonical.encode("utf-8"))
        before_import = tree_bytes(root1)
        import_layout(root1)
        check("import_rewrites_nothing",
              tree_bytes(root1) == before_import)

        # ---- Determinism: the same store, a second root,
        # byte-identical trees. ----
        export_layout(store, root2)
        check("second_export_byte_identical_tree",
              tree_bytes(root2) == tree_bytes(root1))

        # ---- Nearby-unrelated, from the stored records
        # themselves: adjacent coordinates, and neither
        # record references the other word anywhere. ----
        def strings_of(obj):
            out = set()
            if isinstance(obj, dict):
                for k, v in obj.items():
                    out.add(k)
                    out |= strings_of(v)
            elif isinstance(obj, list):
                for v in obj:
                    out |= strings_of(v)
            elif isinstance(obj, str):
                out.add(obj)
            return out

        probe_rec = json.loads(
            files1[rel_of[probe_word]].decode("utf-8")
        )["entries"][probe_word]
        host_rec = json.loads(
            files1[rel_of[host_word]].decode("utf-8")
        )["entries"][host_word]
        check("adjacent_records_do_not_reference_each_other",
              rel_of[probe_word] != rel_of[host_word]
              and probe_word not in strings_of(host_rec)
              and host_word not in strings_of(probe_rec),
              "%s beside %s; no stored reference either way"
              % (probe_word, host_word))

        # ---- Write-through granularity: one subcoordinate,
        # exactly one file changes, and the derived export
        # reflects it. ----
        before_put = tree_bytes(root1)
        probe_sub = {
            "value": "animal", "status": "candidate",
            "provenance": {"source": "suite",
                           "evidence": "storage_probe"},
            "attestations": 1,
            "attested_events": [store["seq"]],
            "first_seq": store["seq"],
            "last_seq": store["seq"]}
        put_subcoordinate(root1, BASE_GRAPH, "sheep", "is_a",
                          probe_sub)
        after_put = tree_bytes(root1)
        changed = sorted(
            rel for rel in set(before_put) | set(after_put)
            if before_put.get(rel) != after_put.get(rel))
        check("put_subcoordinate_changes_exactly_one_file",
              changed == [rel_of["sheep"]], repr(changed))
        store3 = import_layout(root1)
        check("put_subcoordinate_visible_to_import",
              store3["entries"]["sheep"]["subcoordinates"]
              ["is_a"] == probe_sub)
        check("derive_export_reflects_put",
              derive_export(root1)
              == wi.canonical(store3).encode("utf-8")
              and derive_export(root1)
              != orig_canonical.encode("utf-8"))

        # ---- Strictness: a corrupted entry file is refused,
        # named, and left byte-untouched; the failed import
        # rewrites nothing else either. ----
        shutil.copytree(root2, root3, dirs_exist_ok=True)
        wolf_path = os.path.join(root3, rel_of["wolf"])
        with open(wolf_path, "wb") as f:
            f.write(b'{"corrupted": true, garbage')
        with open(wolf_path, "rb") as f:
            corrupted_bytes = f.read()
        err = None
        try:
            import_layout(root3)
        except StorageLayoutError as exc:
            err = exc
        check("corrupt_record_raises_naming_file",
              err is not None and wolf_path in str(err),
              str(err) if err else "no error raised")
        files3 = tree_bytes(root3)
        files2 = tree_bytes(root2)
        check("corrupt_file_and_tree_untouched_by_failure",
              files3[rel_of["wolf"]] == corrupted_bytes
              and set(files3) == set(files2)
              and all(files3[rel] == files2[rel]
                      for rel in files2
                      if rel != rel_of["wolf"]))

        # ---- Strictness: a record filed at a coordinate
        # that is not its words' address is refused and
        # named - placement in the tree confers nothing. ----
        shutil.copytree(root2, root4, dirs_exist_ok=True)
        misplaced_path = os.path.join(
            root4, "graphs", BASE_GRAPH, "entries", "L1", "Z",
            "9.json")
        os.makedirs(os.path.dirname(misplaced_path),
                    exist_ok=True)
        with open(misplaced_path, "wb") as f:
            f.write(files2[rel_of["wolf"]])
        err4 = None
        try:
            import_layout(root4)
        except StorageLayoutError as exc:
            err4 = exc
        check("misplaced_record_raises_naming_file",
              err4 is not None
              and misplaced_path in str(err4),
              str(err4) if err4 else "no error raised")

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
