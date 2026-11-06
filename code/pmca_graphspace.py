"""pmca_graphspace.py - GraphSpace foundation, PMCA graph architecture generation.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code implemented by Alder (Muse) per Jonathan's architecture and the
full-rewrite spec of 2026-10-20 (stage 1: foundation module only).

A graph address is a tuple (layer, dimension, index):
  layer     - any int (negative layers exist)
  dimension - one letter 'A'..'Z' (anything else raises)
  index     - any int; SPARSE: an index may exist without its neighbours

Graph address and entry coordinate are different levels: the address says
which graph; an entry's own coordinate fields (existing system) say its
position inside that graph. They are never conflated here.

Deterministic: integer/string addressing only. No floats introduced,
no random, no wall-clock. (Scoped to the reasoning path, here and
system-wide: addresses and verdicts are functions of stored structure
and inputs only. Seal keys and nonces in pmca_persistence use OS
randomness by design, and wall-clock timestamps appear as record
metadata in some stores; neither enters an address or a decision.)

On the architecture's "neural network" framing (ruled 2026-10-16):
the geometry functions as the network in the definitional sense that
reasoning IS traversal over this stored structure — lookups and walks
over coordinates, edges, and subcoordinates. It is not a claim about
trained weights: nothing in this layer learns, fits, or adjusts
parameters.
"""

import json

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class GlueConflict(Exception):
    """Raised when a glue placement disagrees with the canonical entry."""


def _validate_address(address):
    """Return the address as a (layer, dimension, index) tuple, or raise."""
    if not isinstance(address, tuple) or len(address) != 3:
        raise ValueError("graph address must be a tuple (layer, dimension, index)")
    layer, dimension, index = address
    if isinstance(layer, bool) or not isinstance(layer, int):
        raise ValueError("layer must be an int")
    if not isinstance(dimension, str) or len(dimension) != 1 or dimension not in ALPHABET:
        raise ValueError("dimension must be one letter 'A'..'Z'")
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("index must be an int")
    return (layer, dimension, index)


def _canon(obj):
    """Recursively convert obj to a JSON-safe, deterministically ordered value."""
    if isinstance(obj, dict):
        return {str(key): _canon(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canon(value) for value in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((_canon(value) for value in obj), key=repr)
    return obj


def dist_address(word, layer=1):
    """Distribution address for a word on the given layer.

    Pure integer arithmetic on the word itself; no semantics, addressing only.
    """
    code = sum((i + 1) * ord(c) for i, c in enumerate(word))
    dimension = ALPHABET[code % 26]
    index = ((code // 26) % 41) - 20
    return (layer, dimension, index)


class GraphSpace:
    """A sparse space of graphs, with an address book for placed entries."""

    def __init__(self, system):
        self.system = system
        # address -> {'entries': {key: entry}, 'edges': [edge],
        #             'meta': {'system': ..., 'created_by': ...}}
        self.graphs = {}
        # key -> home address (the graph holding the canonical entry)
        self.address_book = {}

    def produce_graph(self, address, system, created_by):
        """Create an EMPTY graph at address. Raises if it already exists."""
        address = _validate_address(address)
        if address in self.graphs:
            raise ValueError("graph already exists at %r" % (address,))
        self.graphs[address] = {
            "entries": {},
            "edges": [],
            "meta": {"system": system, "created_by": created_by},
        }
        return address

    def place_entry(self, address, key, entry):
        """Place entry under key at address; record key in the address book.

        If key already has a home, this is a GLUE operation: an entry equal
        to the canonical entry stores a reference marker {'glued': home} in
        this graph; a differing entry raises GlueConflict.
        """
        address = _validate_address(address)
        if address not in self.graphs:
            raise ValueError("no graph at %r" % (address,))
        home = self.address_book.get(key)
        if home is None:
            self.graphs[address]["entries"][key] = entry
            self.address_book[key] = address
            return address
        canonical = self.graphs[home]["entries"][key]
        if entry != canonical:
            raise GlueConflict(
                "glue conflict for key %r at %r: differs from canonical entry at %r"
                % (key, address, home)
            )
        if address != home:
            self.graphs[address]["entries"][key] = {"glued": home}
        return address

    def place_edge(self, address, edge):
        """Append edge to the graph at address; return the address.

        The guarded counterpart of place_entry for edge lists (restore
        rebuilds edges through this, never by raw list writes). Raises
        ValueError if no graph exists at address, or if an identical
        edge is already present in that graph's edge list.
        """
        address = _validate_address(address)
        if address not in self.graphs:
            raise ValueError("no graph at %r" % (address,))
        if edge in self.graphs[address]["edges"]:
            raise ValueError(
                "edge already present at %r: %r" % (address, edge)
            )
        self.graphs[address]["edges"].append(edge)
        return address

    def lookup(self, key):
        """Return the canonical entry for key via the address book, or None."""
        home = self.address_book.get(key)
        if home is None:
            return None
        return self.graphs[home]["entries"].get(key)

    def home_of(self, key):
        """Return the home address of key, or None if it has no home."""
        return self.address_book.get(key)

    def addresses(self):
        """Return the sorted list of graph addresses in this space."""
        return sorted(self.graphs)

    def canonical_bytes(self):
        """Deterministic serialization of all graphs/entries/edges (for sealing)."""
        payload = []
        for address in sorted(self.graphs):
            graph = self.graphs[address]
            payload.append([
                [address[0], address[1], address[2]],
                {
                    "entries": _canon(graph["entries"]),
                    "edges": _canon(graph["edges"]),
                    "meta": _canon(graph["meta"]),
                },
            ])
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def bootstrap(self, system=None):
        """Create exactly two empty exemplar graphs, (1,'A',1) and (2,'A',1)."""
        system = self.system if system is None else system
        created = []
        for address in ((1, "A", 1), (2, "A", 1)):
            self.produce_graph(address, system, "bootstrap")
            created.append(address)
        return created
