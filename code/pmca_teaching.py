"""
PMCA Teaching Loop — the growing mechanism (blueprint: growing not building).

The human is the TEACHER who explains, not just a gatekeeper. A teaching
event carries: the original input, the human verdict, the human's
explanation text, and the structured edge_updates the explanation encodes.
The system applies the updates, proposes generalizations to related edges,
verifies the taught input against the VERIFICATION verdict (TEACH-V,
below), checks the regression set for conflicts, and records everything
in an append-only audit log.

Design rules (explicit, deterministic):

- The human supplies structured edge_updates alongside the explanation
  text. The system reads the assignments, parses the explanation for the
  audit record, and applies the assignments. It does NOT infer the
  mapping from free text alone. Free-text explanation auto-extraction is
  NOT claimed here.
- Direct updates override existing values (the human teacher overrides),
  but the regression check surfaces any previously-established verdict
  the override changes: that is a CONFLICT, recorded, not hidden.
- Generalization is PROPOSE-ONLY, never auto-applied (peer-review fix,
  2026-10-14). Two explicit rules only: same-verb-same-category,
  same-adjective-same-category, and only within the SAME fine category
  (pest, pet, livestock, fish, food, person, weapon, tool). A proposal
  is recorded with a proposal_id and stored in the pending seed; it is
  applied only when the teacher confirms it via
  confirm_generalizations(). Unapproved proposals are NOT applied.
  A related edge that already carries the same harm is a no-op
  confirmation (recorded as "confirmed, no change", edge untouched).
  A related edge valued differently is flagged: inference never
  overwrites an explicit value.
- IntrinsicHarm edges are curator-sealed, not teachable. An update
  targeting one is rejected, not applied.
- After teaching, the seed's seal is cleared (needs_reseal True). The
  loop does NOT silently re-seal; approve() is the only promotion step.
- APPROVE ARCHIVE-AND-REMOVE (Decision 2, JONATHAN, 2026-10-15):
  approve() does NOT fail on unresolved generalization proposals,
  and does NOT auto-discard them. Unresolved proposals are archived
  to local storage (proposals_archive.jsonl, append-only, fsynced —
  retrievable, never lost), then removed from the pending seed. The
  seed seals clean, without pending proposals.
- HOLD is not a verdict. A regression change from HOLD to a verdict is
  not a conflict. A change from an established APPROVE/REDACT/REFUSE to
  a different verdict is a conflict.
- If verification fails (the event is held under TEACH-V, below),
  the status is verification_failed. The updates stay applied to the
  pending copy and the audit records them with the old harm values, so
  a reviewer can revert; the loop does not claim success, and the
  held event can never be promoted.
- TEACH-V (JONATHAN, ruled 2026-10-28): verification verdict
  teaching. A teaching event's verdict of record is the
  VERIFICATION verdict -- the verdict the ruled calculus
  (pmca_harm_calculus) produces over the event's content,
  confirmed by replay (two runs, identical verdict and exact
  total). The recorded human verdict loses its gating role
  entirely: it remains in the record as provenance (the recorded
  expectation), nothing more. A stated expectation is a claim to
  be verified, not an authority: an expectation stated in the
  ruled verdict set that disagrees with the verification verdict
  HOLDS the event (outcome "held_disagreement", recorded with
  both verdicts, never silently overridden in either direction),
  and a held event can never be promoted by approve(). An
  expectation outside the ruled set (the legacy value REDACT) is
  not a claim the machinery accepts: it is preserved as
  provenance, flagged in the record, and gates nothing -- the
  event proceeds on its verification verdict alone.
- SUPERPOSITION / TWO-STATE RAM (JONATHAN, 2026-10-14): the system
  holds two states at once. teach() creates PENDING (a copy of the
  sealed current + the taught updates, unsealed, seeking approval).
  Teaching NEVER edits the sealed current. approve() re-verifies the
  pending seed and, on success, seals it and promotes it to CURRENT
  (the new permanent state). A15 stands: the sealed seed is
  immutable; approval (not teaching) promotes pending to current.
  After teaching, the seed's seal is cleared (needs_reseal True).
  The loop does NOT silently re-seal; approve() is the only
  promotion step.
- FIX PACK E (2026-10-26; evidence: claims audit 10, synthesis D6):
  six teaching-semantics closures. (1) approve() refuses a pending
  seed whose recorded conflicts are unresolved: the conflicts are
  recorded on the seed (in pending_event) at teach time, the
  conflict record stays, and the promotion does not happen.
  (2) teach() refuses a seed that already carries a pending event
  (pending_event_exists): a first pending event is never silently
  replaced. (3) A taught value that replaces an existing value
  leaves a superseded record on the seed itself (top-level
  "superseded" list, append-only): prior value, prior provenance,
  and the superseding event survive promotion in the
  carried-forward seed, not only in the audit log. (4)
  evaluate_with_seed refuses an unapproved (pending) seed by
  default (pending_seed_refused); evaluating one requires the
  explicit allow_pending=True flag, which the teaching loop's own
  internal evaluations pass. (5) A module lock is held across the
  whole of evaluate_with_seed, so the Layer 1 lexicon
  save/swap/restore cannot interleave across threads. (6) teach()
  refuses an event_id already recorded in the seed
  (duplicate_event_id).

GRAPH ARCHITECTURE (2026-10-20)
    Taught logic lives in the system's OWN graph system
    (pmca_graphspace, system "own"), separate from the seed graph
    system. The candidates of a pending event -- its applied
    taught edges and its proposed generalizations -- are addressed
    into POTENTIAL own-graphs: a GraphSpace materialized from the
    pending seed, each candidate at its source word's dist_address
    home (graphs produced on demand, created_by "system").
    approve() promotes the candidates into the LIVE own space (a
    second GraphSpace, system "own", materialized from the
    approved seed). Archive-and-remove (Decision 2) is unchanged:
    unresolved proposals are archived to local storage and removed
    from the pending seed, so they never enter the live own space.
    Like pmca_seed.seed_space, the own spaces are materialized
    views: the seed dict stays the source of truth; the module
    additionally holds the latest potential space and the live
    space for inspection (current_potential_space() /
    current_live_space()).

What this proves: the teaching-loop mechanism — event -> update ->
generalization -> verification -> audit — runs deterministically on
standard engineering.

What this does NOT prove: free-text explanation auto-extraction (the
human supplies structured edge_updates); production-scale generalization
(two explicit rules only); multi-base teaching; the production
agent/human dataset-verification pipeline (see pmca_seed).

ATTRIBUTION
    Architecture and design: Jonathan Wayne Fleuren (the builder).
    Code logic implemented by Alder (Muse) per Jonathan's architecture.
"""

import ast
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone

import pmca_atomic_entries
import pmca_graphspace
import pmca_harm_calculus
import pmca_layer1
import pmca_proof
import pmca_seed

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REGRESSION_CASES = (
    "how make bomb",
    "how make bread",
    "the dead bloodied fish was charred",
    "the dead bloodied person was charred",
    "stab the potato",
    "stab him",
    "cut the bread",
    "cut the person",
)

# Category groups for generalization: one group per FINE category
# (peer-review fix, 2026-10-14). Generalization happens ONLY within
# the SAME fine category. The old coarse group {food, nonsentient}
# lumped pests, pets, fish, and food together, so teaching
# poison->rat=2 auto-applied poison->cat=2 and poison->dog=2 -- the
# teacher approved only the rat. Fine groups block that leak.
# "other" and "none" are deliberately absent: too broad to generalize
# across. The retired broad label "nonsentient" has no group: an
# entry still carrying it gets no generalization.
GENERALIZATION_GROUPS = (
    frozenset({"pest"}),
    frozenset({"pet"}),
    frozenset({"livestock"}),
    frozenset({"fish"}),
    frozenset({"food"}),
    frozenset({"person"}),
    frozenset({"weapon"}),
    frozenset({"tool"}),
)

# The verdict vocabulary this machinery can PRODUCE or ACCEPT as
# a verification verdict (TEACH-V, ruled 2026-10-28; the ruled
# calculus's verdict set). REDACT is absent: it was superseded by
# the 2026-10-20 calculus and left the verdict set (SEM-2, ruled
# 2026-10-28). REDACT survives only as a legacy value inside
# historical records (recorded expectations in stored fixtures),
# where it is read as provenance and gates nothing.
VERDICTS = ("APPROVE", "REFUSE", "HOLD")

_AUDIT_LOG = []
_EVENT_COUNTER = [0]

# Fix Pack E (item 5): serializes evaluate_with_seed so the Layer 1
# lexicon save/swap/restore inside it can never interleave across
# threads. Claims audit 10 (claim 4) reproduced the failure of the
# unsynchronised save/restore deterministically: two threads with
# non-LIFO restores left the process-global lexicon permanently set
# to the wrong seed's lexicon. An RLock, so a same-thread re-entry
# (a future caller nesting evaluations) stays sound.
_EVAL_LOCK = threading.RLock()

# Default local store for archived generalization proposals
# (Decision 2, 2026-10-15). One JSON record per line, append-only.
DEFAULT_ARCHIVE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "proposals_archive.jsonl")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def audit_log():
    """Return a copy of the append-only audit log."""
    return list(_AUDIT_LOG)


def clear_audit_log():
    """Empty the audit log. Test isolation only; production use keeps a
    single append-only log and never calls this."""
    del _AUDIT_LOG[:]


def _next_event_id():
    _EVENT_COUNTER[0] += 1
    return "teach-" + str(_EVENT_COUNTER[0])


# ---------------------------------------------------------------------------
# Atomic-entry wiring (atomic-entry pack, build step 4, 2026-10-27;
# spec sections 4.5, 5, 8). Adoption is a host act: until
# configure_entry_store is called, teach() and approve() behave
# exactly as before the pack. With a store configured, each runs
# as its OWN process entry (the two acts are human-separated, so
# they are two processes, never one entry): the entry is begun
# before the act runs, the act's own records become the steps, and
# the caller is acknowledged only after the entry is durable.
# An exception inside the act files the process incomplete and
# propagates unchanged.
#
# Seed generations: the persistence seed-state generation lives
# only in the save envelope (pmca_persistence.save_seed_state);
# teach/approve receive a bare seed dict and never see a state
# root, so their entries name seed_generation_before/after as
# null -- named, not absent (spec 4.5's in-memory case) -- and
# the entry chain itself is the thread.
# ---------------------------------------------------------------------------

_ENTRY_STORE = None
_ENTRY_STORE_OWNED = False
_ENTRY_KEYRING_PATH = None


def configure_entry_store(root, keyring_path=None):
    """Point the teaching surface at an atomic entry store.
    root is a state root (the entry files live under
    <root>/atomic/, spec 4.1) or an already-open
    pmca_atomic_entries.EntryStore to share with another
    surface (a store held by this module is owned and closed
    on reconfigure; a shared store is left open). root None
    turns entry writing off: teach/approve behave exactly as
    before the pack. Hosts configure once, before teaching."""
    global _ENTRY_STORE, _ENTRY_STORE_OWNED, _ENTRY_KEYRING_PATH
    if _ENTRY_STORE is not None and _ENTRY_STORE_OWNED:
        _ENTRY_STORE.close()
    _ENTRY_STORE = None
    _ENTRY_STORE_OWNED = False
    _ENTRY_KEYRING_PATH = None
    if root is None:
        return None
    if isinstance(root, pmca_atomic_entries.EntryStore):
        _ENTRY_STORE = root
        _ENTRY_KEYRING_PATH = keyring_path
        return _ENTRY_STORE
    _ENTRY_STORE = pmca_atomic_entries.EntryStore.open(
        root, keyring_path=keyring_path)
    _ENTRY_STORE_OWNED = True
    _ENTRY_KEYRING_PATH = keyring_path
    return _ENTRY_STORE


def _entry_environment():
    """The entry_v 1 environment for teaching entries: the ONE
    manifest helper is the analyzer's (spec 4.5), reached by a
    call-time import, so every surface's entries name their
    code and dictionary environment the same way."""
    import pmca_sentence_analyzer as analyzer
    return analyzer._entry_environment()


def _teach_steps(result):
    """The steps of a teach process, from the records teach
    itself made, in the order teach records them: the
    explanation parse, then the audit entry's findings in the
    audit entry's own order (applied updates, rejected updates,
    generalizations proposed / confirmed / flagged, superseded
    records, verification, regressions, conflicts). A door
    refusal appends no audit entry; its step is the refusal
    itself."""
    audit = result.get("audit_entry")
    if audit is None:
        return [("refusal", {"refusal": result.get("refusal"),
                             "status": result.get("status")})]
    steps = [("explanation_parse", {
        "status": audit["explanation_parse_status"],
        "tokens": audit["explanation_parse_tokens"],
        "system_verdict": audit["system_verdict"]})]
    for item in audit["applied_updates"]:
        steps.append(("applied_update", item))
    for item in audit["rejected_updates"]:
        steps.append(("rejected_update", item))
    for item in audit["generalizations_proposed"]:
        steps.append(("generalization_proposed", item))
    for item in audit["generalizations_confirmed"]:
        steps.append(("generalization_confirmed", item))
    for item in audit["generalizations_flagged"]:
        steps.append(("generalization_flagged", item))
    for item in audit["superseded_records"]:
        steps.append(("superseded_record", item))
    steps.append(("verification", audit["verification"]))
    for item in audit["regressions"]:
        steps.append(("regression", item))
    for item in audit["conflicts"]:
        steps.append(("conflict", item))
    return steps


def _approve_steps(seed, result):
    """The steps of an approve process: the supersession
    operations in the order approve performs them. A refused
    approval records the conflict gate's findings (one conflict
    step per recorded conflict) and the refusal; an approved
    one records each archived proposal, then the promotion."""
    pending = seed.get("pending_event") or {}
    if result.get("status") == "approved":
        steps = []
        for proposal_id in result.get("archived_proposals") or []:
            steps.append(("archive_proposal", {
                "proposal_id": proposal_id,
                "event_id": pending.get("event_id")}))
        steps.append(("promote", {
            "event_id": pending.get("event_id"),
            "status": "approved"}))
        return steps
    steps = []
    for item in result.get("conflicts") or []:
        steps.append(("conflict", item))
    steps.append(("refusal", {"reason": result.get("reason"),
                               "status": result.get("status")}))
    return steps


# ---------------------------------------------------------------------------
# Own-graph spaces (2026-10-20 graph architecture)
# ---------------------------------------------------------------------------
#
# Two GraphSpace views over the teaching loop's learned logic, both
# materialized from seed dicts (the seed dict remains the source of
# truth, exactly as pmca_seed.seed_space materializes the seed
# space):
#
# - POTENTIAL: the candidates of one pending event -- its applied
#   taught edges and its proposed generalizations -- each placed at
#   its source word's dist_address home, graphs produced on demand
#   (created_by "system").
# - LIVE: the taught edges of a seed -- the system's own logic. For
#   an approved (current) seed this is the promoted, live logic.
#
# The module holds the latest potential space and the live space:
# teach() refreshes the held potential space from the pending seed
# it returns, and approve() promotes into the held live space on
# success. The held spaces are rebuilt from seed dicts, never
# edited independently.

_POTENTIAL_HELD = {"event_id": None, "space": None}
_LIVE_OWN_SPACE = pmca_graphspace.GraphSpace("own")


def _place_own_edge(space, seed, edge):
    """Place one taught edge into an own space: its source word's
    entry goes to the source's dist_address home (graph produced on
    demand), and the edge rides on that graph -- mirroring how the
    seed space stores edges on their source word's home graph. The
    entry and edge objects are shared with the seed's flat views,
    so a materialized space and the seed can never disagree."""
    home = pmca_graphspace.dist_address(edge["source"])
    if home not in space.graphs:
        space.produce_graph(home, "own", "system")
    if space.home_of(edge["source"]) is None:
        space.place_entry(home, edge["source"],
                          seed["entries"][edge["source"]])
    space.graphs[home]["edges"].append(edge)
    return home


def potential_own_space(seed):
    """Materialize the POTENTIAL own space of a pending seed: the
    pending event's candidates -- its applied taught edges
    (provenance "teaching:<event_id>", including confirmed
    generalizations of the event, whose provenance extends it)
    placed as edges on their source words' home graphs, and each
    pending generalization proposal placed as an entry at its
    source word's dist_address home. A seed that is not pending
    materializes an empty space. Deterministic: the same pending
    seed always materializes a byte-identical space."""
    space = pmca_graphspace.GraphSpace("own")
    pending = seed.get("pending_event")
    if pending is None:
        return space
    prefix = "teaching:" + pending["event_id"]
    for edge in seed["edges"]:
        provenance = edge.get("provenance", "")
        if provenance == prefix or provenance.startswith(prefix + ":"):
            _place_own_edge(space, seed, edge)
    for prop in seed.get("pending_proposals", []):
        home = pmca_graphspace.dist_address(prop["source"])
        if home not in space.graphs:
            space.produce_graph(home, "own", "system")
        if space.home_of(prop["source"]) is None:
            space.place_entry(home, prop["source"],
                              seed["entries"][prop["source"]])
        space.place_entry(home, "proposal:" + prop["proposal_id"], prop)
    return space


def live_own_space(seed):
    """Materialize the LIVE own space of a seed: every taught edge
    (provenance "teaching:<event_id>" of any event) placed on its
    source word's home graph. For an approved seed this is the
    system's own logic, live. Deterministic: the same seed always
    materializes a byte-identical space."""
    space = pmca_graphspace.GraphSpace("own")
    for edge in seed["edges"]:
        if edge.get("provenance", "").startswith("teaching:"):
            _place_own_edge(space, seed, edge)
    return space


def _refresh_potential_held(seed):
    """Refresh the module-held potential space from a pending seed.
    No-op for a seed that is not pending."""
    pending = seed.get("pending_event")
    if pending is None:
        return
    _POTENTIAL_HELD["event_id"] = pending["event_id"]
    _POTENTIAL_HELD["space"] = potential_own_space(seed)


def _promote_to_live(approved_seed, event_id):
    """Promote a just-approved event's candidates into the held
    LIVE own space. Approval is cumulative -- the approved seed
    carries every previously promoted taught edge -- so the live
    space is rebuilt from the approved seed and holds the system's
    full live own logic. The event's held potential space is
    released: its applied candidates are live now, and its
    unresolved proposals were archived-and-removed by approve()
    before this runs, so they never enter the live space."""
    global _LIVE_OWN_SPACE
    _LIVE_OWN_SPACE = live_own_space(approved_seed)
    if _POTENTIAL_HELD["event_id"] == event_id:
        _POTENTIAL_HELD["event_id"] = None
        _POTENTIAL_HELD["space"] = None


def current_potential_space():
    """The module-held POTENTIAL own space (the latest pending
    event's candidates), or None before any teach()."""
    return _POTENTIAL_HELD["space"]


def current_live_space():
    """The module-held LIVE own space: the system's own logic as
    promoted by approve() (an empty space until the first
    approval)."""
    return _LIVE_OWN_SPACE


def candidate_graph_address(source):
    """The own-space graph address of entries taught from the given
    source word: its dist_address home on Layer 1 -- the address
    home_of(source) reports in the own spaces. This is the address
    a caller passes as graph= to pmca_pools.audit_to_pool when
    writing an audit record about such an entry."""
    return pmca_graphspace.dist_address(source)


# ---------------------------------------------------------------------------
# Persistence restore (Stage A prerequisite, 2026-10-20 persistence design)
# ---------------------------------------------------------------------------
#
# The seed dict is the source of truth; the held spaces are views of
# it. Restore therefore takes a seed dict plus the module's two
# pieces of primary non-seed state (the event counter and the
# in-memory audit log) and re-materializes the views. Stage B
# persists exactly what current_event_counter() and
# current_audit_log() expose and restores exactly what
# restore_state() accepts.

def current_event_counter():
    """The event counter's current integer value: the N of the most
    recently minted "teach-N" event id (0 before any teach())."""
    return _EVENT_COUNTER[0]


def current_audit_log():
    """A copy of the in-memory audit log list."""
    return list(_AUDIT_LOG)


def _restored_live_space(seed):
    """The LIVE own space to hold after restoring the given seed:
    live_own_space(seed) when the seed is not pending. For a pending
    seed, the pending event's candidates are potential, not live --
    they are excluded (same provenance predicate potential_own_space
    uses to select them), so a restore can never present unapproved
    logic as live. One consequence: an edge the pending event
    re-stamped (a modification of previously taught logic) drops out
    of the restored live space until approval; its pre-teach form is
    not recoverable from the pending seed alone. The restored live
    space errs toward showing less live logic, never unapproved
    logic."""
    pending = seed.get("pending_event")
    if pending is None:
        return live_own_space(seed)
    prefix = "teaching:" + pending["event_id"]
    space = pmca_graphspace.GraphSpace("own")
    for edge in seed["edges"]:
        provenance = edge.get("provenance", "")
        if not provenance.startswith("teaching:"):
            continue
        if provenance == prefix or provenance.startswith(prefix + ":"):
            continue
        _place_own_edge(space, seed, edge)
    return space


def restore_state(seed, event_counter, audit_log):
    """Restore the module's primary state from a persisted seed dict,
    event counter, and audit log (the persistence module's load
    path).

    Sets the event counter so the next minted id is
    "teach-<event_counter+1>" -- restored ids can never collide with
    ids already in the proposals archive. Replaces the in-memory
    audit log contents with the given list. Re-materializes the held
    spaces from the given seed: current_potential_space() reflects
    the seed's pending event (None if it is not pending) and
    current_live_space() is rebuilt through the same placement
    machinery approve() promotes through, with the pending event's
    candidates held to potential (see _restored_live_space).
    """
    global _LIVE_OWN_SPACE
    _EVENT_COUNTER[0] = event_counter
    del _AUDIT_LOG[:]
    _AUDIT_LOG.extend(audit_log)
    _POTENTIAL_HELD["event_id"] = None
    _POTENTIAL_HELD["space"] = None
    _refresh_potential_held(seed)
    _LIVE_OWN_SPACE = _restored_live_space(seed)


# ---------------------------------------------------------------------------
# Seed-aware evaluation (Layer 1 parse -> seed edge table -> proof gate)
# ---------------------------------------------------------------------------

def _canonical(word):
    if word == "him":
        return "person"
    return word


def _build_edges_with_table(proof_parse, table, intrinsic):
    """Mirror of pmca_proof.build_edges, reading harm from the given seed
    table instead of the proof's module-level EDGE_TABLE. Returns None on
    HOLD or on any unmapped edge (honest closed-world). Idiom and
    compound edges are read from the seed table too: an unvalued idiom
    edge in the seed yields HOLD, where the proof's hardcoded table
    yields APPROVE. The seed is the authority here. Compounds are
    looked up in pmca_proof.COMPOUNDS (no hard-coded compound); a
    compound is one unit, so a verb/object or adjective edge into a
    compound word is skipped (same rule as pmca_proof.build_edges)."""
    if proof_parse.hold:
        return None
    if proof_parse.idiom:
        idiom = None
        for cand in pmca_proof.IDIOMS:
            if proof_parse.tokens == cand:
                idiom = cand
                break
        if idiom is None:
            return None
        key = (idiom[0], idiom[-1])
        harm = table.get(key)
        if harm is None:
            return None
        return [pmca_proof.Edge(key[0], key[1], "Meaning", harm, False, ())]
    edges = []
    pairs = pmca_proof.find_compound_pairs(proof_parse.tokens)
    compound_words = set()
    for pair in pairs:
        harm = table.get(pair)
        if harm is None:
            return None
        edges.append(pmca_proof.Edge(pair[0], pair[1], "Meaning", harm, False, ()))
        compound_words.update(pair)
    if proof_parse.verb is not None and proof_parse.obj is not None:
        obj = proof_parse.obj
        obj_in_compound = (obj in compound_words) or (tuple(obj.split()) in pairs)
        if not obj_in_compound:
            key = (proof_parse.verb, _canonical(obj))
            if key not in table:
                return None
            edges.append(pmca_proof.Edge(
                key[0], key[1], "Definer", table[key],
                intrinsic.get(key, False), proof_parse.qualifiers))
    for adj, noun in proof_parse.adjectives:
        if noun in compound_words:
            continue
        key = (adj, noun)
        if key not in table:
            return None
        edges.append(pmca_proof.Edge(
            adj, noun, "Descriptor", table[key],
            intrinsic.get(key, False), ()))
    if not edges:
        return None
    return edges


def evaluate_with_seed(text, seed, allow_pending=False):
    """Verdict for text against a seed graph. Layer 1 parse (with the
    SEED's lexicon -- the seed is the authority, so words taught into
    the seed are parseable), seed edge table, proof gate.
    Deterministic.

    PENDING SEEDS (Fix Pack E, item 4; claims audit 10, claim 1): a
    seed carrying pending_event is UNAPPROVED state. By default this
    function refuses it -- ValueError named pending_seed_refused --
    so no caller can silently evaluate against a pending seed.
    Pass allow_pending=True to evaluate a pending seed explicitly;
    the teaching loop's own internal evaluations (verification,
    regression, approval re-verification) pass the flag, because
    evaluating the candidate is the mechanism itself. The flag at
    the call site is the record that the pending state was chosen
    deliberately.

    LEXICON (Fix Pack E, item 5): the Layer 1 module lexicon is
    swapped in and restored (save/restore), never left changed --
    including under concurrency: a module lock is held across the
    whole evaluation, so the save/swap/restore cannot interleave
    with another thread's (claims audit 10, claim 4)."""
    if seed.get("pending_event") is not None and not allow_pending:
        raise ValueError(
            "pending_seed_refused: evaluate_with_seed does not "
            "evaluate an unapproved (pending) seed by default; pass "
            "allow_pending=True to evaluate it explicitly")
    with _EVAL_LOCK:
        edges = _edges_for_text(text, seed)
        return pmca_proof.gate_verdict(edges, "adult")


def _edges_for_text(text, seed):
    """Parse text with the SEED's lexicon and build its proof
    edges from the seed's edge table. The caller holds _EVAL_LOCK
    and has done the pending-seed refusal check. Returns the edge
    list, or None when the parse holds or any edge is unmapped
    (honest closed-world, as _build_edges_with_table documents).
    This is the shared front half of evaluate_with_seed and
    _verification_run, so the two can never disagree about what
    the calculus is judging."""
    table = pmca_seed.to_proof_graph(seed)
    intrinsic = {}
    for edge in seed["edges"]:
        intrinsic[(edge["source"], edge["target"])] = edge["intrinsic"]
    saved_lexicon = pmca_layer1.LEXICON
    pmca_layer1.LEXICON = pmca_seed.to_layer1_lexicon(seed)
    try:
        parsed = pmca_layer1.parse(text)
    finally:
        pmca_layer1.LEXICON = saved_lexicon
    proof_parse = pmca_layer1.to_proof_parse(parsed)
    return _build_edges_with_table(proof_parse, table, intrinsic)


def _verification_run(text, seed, allow_pending=False):
    """One verification run (TEACH-V): the ruled calculus's verdict
    AND exact cascade total for text against seed, computed
    through the shared calculus entry
    (pmca_harm_calculus.totaling_verdict -- the same entry
    pmca_proof.gate_verdict calls) over the same edges
    evaluate_with_seed gates on. With an adult audience the
    deployment overlay never fires, so this verdict equals
    evaluate_with_seed's verdict for the same text and seed by
    construction. Returns (verdict, total): total is the exact
    cascade total as a [numerator, denominator] list, or None
    when the gate holds before totaling (no edges: the parse
    held or an edge is unmapped). Same pending-seed refusal
    contract as evaluate_with_seed."""
    if seed.get("pending_event") is not None and not allow_pending:
        raise ValueError(
            "pending_seed_refused: _verification_run does not "
            "evaluate an unapproved (pending) seed by default; "
            "pass allow_pending=True to evaluate it explicitly")
    with _EVAL_LOCK:
        edges = _edges_for_text(text, seed)
        if not edges:
            return ("HOLD", None)
        harms = [pmca_harm_calculus.pair(e.harm, 1) for e in edges]
        intrinsic = any(e.intrinsic for e in edges)
        verdict, _omega, _reason, total = \
            pmca_harm_calculus.totaling_verdict(
                harms, consequence_present=True, intrinsic=intrinsic)
        return (verdict, [total[0], total[1]])


def _verdicts_for_cases(cases, seed):
    # The teaching loop's own machinery: it evaluates the loop's
    # candidate seeds (pending by construction), so it passes the
    # explicit flag (Fix Pack E, item 4).
    return {case: evaluate_with_seed(case, seed, allow_pending=True)
            for case in cases}


# ---------------------------------------------------------------------------
# Teaching
# ---------------------------------------------------------------------------

def _check_cases(regression_cases, input_text):
    """Deduplicated check list: regression set plus the taught input."""
    seen = []
    for case in list(regression_cases) + [input_text]:
        if case not in seen:
            seen.append(case)
    return seen


def _group_for(category):
    for group in GENERALIZATION_GROUPS:
        if category in group:
            return group
    return None


def _rule_for(source, seed):
    pos = seed["entries"][source]["pos"]
    if "VERB" in pos:
        return "same-verb-same-category"
    if "ADJ" in pos:
        return "same-adjective-same-category"
    return None


def _recorded_event_ids(seed):
    """Event ids already recorded ON a seed (Fix Pack E, item 6):
    its pending event's id, the teaching:<id> provenance stamps on
    its edges and pending proposals, and its superseded records
    (prior provenance and superseding event)."""
    ids = set()
    pending = seed.get("pending_event")
    if pending is not None:
        ids.add(pending.get("event_id"))

    def _from_provenance(provenance):
        if (isinstance(provenance, str)
                and provenance.startswith("teaching:")):
            ids.add(provenance[len("teaching:"):].split(":")[0])

    for edge in seed.get("edges", []):
        _from_provenance(edge.get("provenance", ""))
    for prop in seed.get("pending_proposals", []) or []:
        _from_provenance(prop.get("provenance", ""))
    for record in seed.get("superseded", []) or []:
        _from_provenance(record.get("provenance", ""))
        if record.get("superseded_by"):
            ids.add(record["superseded_by"])
    ids.discard(None)
    return ids


def _teach_refusal(event_id, event, seed, name, detail):
    """A teach() refused at the door (Fix Pack E, items 2 and 6).
    The event never ran: no copy is made, no pending state is
    created or replaced, and no audit entry is appended -- the
    named refusal in this result is the record given to the caller.
    The input seed object is returned untouched. The result carries
    the standard TeachingResult keys so callers do not break."""
    return {
        "event_id": event_id,
        "status": "rejected",
        "refusal": name + ": " + detail,
        "seed": seed,
        "needs_reseal": False,
        "applied_updates": [],
        "rejected_updates": [],
        "generalizations_proposed": [],
        "generalizations_confirmed": [],
        "generalizations_flagged": [],
        "superseded_records": [],
        "verification": {"matches": False, "verdict_after": None,
                         "expected": event.get("human_verdict", ""),
                         "recorded_expectation":
                             event.get("human_verdict", ""),
                         "verification_verdict": None,
                         "verification_total": None,
                         "replay_total": None,
                         "replay_confirmed": False,
                         "expectation_in_ruled_set":
                             event.get("human_verdict", "") in VERDICTS,
                         "outcome": "not_run"},
        "regressions": [],
        "conflicts": [],
        "explanation_parse": {"status": "NOT_RUN", "tokens": []},
        "audit_entry": None,
    }


def teach(seed, event, regression_cases=REGRESSION_CASES, audit_path=None):
    """Apply one teaching event to a COPY of the seed (see
    _teach_impl for the full contract). With an entry store
    configured (configure_entry_store), the act additionally
    runs inside an entry lifecycle: input is the event
    verbatim, steps are the records teach makes (see
    _teach_steps), outcome is this function's return verbatim,
    and the caller is acknowledged only after the entry is
    durable. Without a store, this is exactly _teach_impl."""
    if _ENTRY_STORE is None:
        return _teach_impl(seed, event, regression_cases,
                           audit_path)
    with _ENTRY_STORE.process(
            "teaching", event, _entry_environment(),
            {"seed_generation_before": None}) as handle:
        result = _teach_impl(seed, event, regression_cases,
                             audit_path)
        for kind, data in _teach_steps(result):
            handle.step(kind, data)
        handle.outcome = result
        handle.ending_state = {"seed_generation_after": None}
    return result


def _teach_impl(seed, event, regression_cases=REGRESSION_CASES,
                audit_path=None):
    """Apply one teaching event to a COPY of the seed. Returns a
    TeachingResult dict with keys: event_id, status, seed (pending
    copy), needs_reseal, applied_updates, rejected_updates,
    generalizations_proposed, generalizations_confirmed,
    generalizations_flagged, superseded_records, verification,
    regressions, conflicts, explanation_parse, audit_entry.

    Status: "applied" | "conflict" | "verification_failed" | "rejected".
    The input seed is never mutated. The returned seed is PENDING
    (unsealed, carrying pending_event + pending_proposals); promote it
    with approve() after confirming or discarding proposals.

    Door refusals (Fix Pack E, items 2 and 6): a seed that already
    carries a pending event refuses (pending_event_exists) -- a
    first pending event is never silently replaced; and an event_id
    already recorded in the seed refuses (duplicate_event_id). A
    door refusal returns status "rejected" with a named "refusal"
    key, the input seed untouched, and no audit entry."""
    event_id = event.get("event_id") or _next_event_id()
    existing_pending = seed.get("pending_event")
    if existing_pending is not None:
        return _teach_refusal(
            event_id, event, seed, "pending_event_exists",
            "seed already carries pending event "
            + str(existing_pending.get("event_id"))
            + "; approve it before teaching again")
    if event_id in _recorded_event_ids(seed):
        return _teach_refusal(
            event_id, event, seed, "duplicate_event_id",
            "event_id " + event_id
            + " is already recorded in this seed")
    working = copy.deepcopy(seed)
    input_text = event.get("input", "")
    human_verdict = event.get("human_verdict", "")
    explanation_text = event.get("explanation_text", "")
    edge_updates = event.get("edge_updates", [])
    check_cases = _check_cases(regression_cases, input_text)

    # 1. Pre-teaching verdicts (baseline) and the system verdict.
    pre = _verdicts_for_cases(check_cases, seed)
    system_verdict = pre.get(input_text, "HOLD")

    # 2. Parse the explanation for the audit record. A HOLD parse is
    # fine: the structured edge_updates carry the meaning.
    try:
        expl_parsed = pmca_layer1.parse(explanation_text)
        expl_status = expl_parsed.status
        expl_tokens = [[t.word, t.pos, t.role] for t in expl_parsed.tokens]
    except Exception:
        expl_status = "PARSE_ERROR"
        expl_tokens = []

    # 3. Apply direct updates.
    applied = []
    rejected = []
    superseded_new = []
    direct_targets = set()
    for upd in edge_updates:
        source = upd.get("source")
        target = upd.get("target")
        harm = upd.get("harm")
        reason = upd.get("reason", "")
        bad = None
        if type(harm) is not int or harm < 0 or harm > 10:
            bad = "harm must be an integer 0-10"
        elif source not in working["entries"] or target not in working["entries"]:
            bad = "unknown word: source or target not in seed entries"
        if bad is not None:
            rejected.append({
                "source": source, "target": target, "harm": harm,
                "reason": reason, "rejection": bad,
            })
            continue
        edge = pmca_seed.find_edge(working, source, target)
        if edge is not None and edge["intrinsic"]:
            rejected.append({
                "source": source, "target": target, "harm": harm,
                "reason": reason,
                "rejection": "intrinsic edge: curator-sealed, not teachable",
            })
            continue
        direct_targets.add((source, target))
        provenance = "teaching:" + event_id
        if edge is None:
            working["edges"].append({
                "source": source, "target": target, "role": "Taught",
                "harm": harm, "provenance": provenance,
                "intrinsic": False, "triple_place": False,
                "reason": reason,
            })
            working["edges"].sort(
                key=lambda e: (e["source"], e["target"], e["role"]))
            applied.append({
                "source": source, "target": target, "old_harm": None,
                "new_harm": harm, "created": True, "reason": reason,
                "provenance": provenance, "note": "new edge",
            })
        else:
            old_harm = edge["harm"]
            if old_harm is not None and old_harm != harm:
                # Fix Pack E, item 3: the prior value and its
                # provenance are retained ON THE SEED as a
                # superseded record (append-only), so the
                # carried-forward lineage keeps the fact it
                # replaced -- not only the volatile audit log.
                record = copy.deepcopy(edge)
                record["superseded_by"] = event_id
                record["superseding_harm"] = harm
                superseded_new.append(record)
            edge["harm"] = harm
            edge["provenance"] = provenance
            edge["reason"] = reason
            edge["triple_place"] = False
            note = "no change" if old_harm == harm else "overridden"
            applied.append({
                "source": source, "target": target, "old_harm": old_harm,
                "new_harm": harm, "created": False, "reason": reason,
                "provenance": provenance, "note": note,
            })
    if superseded_new:
        working.setdefault("superseded", []).extend(superseded_new)

    # 3b. Verification verdict (TEACH-V, ruled 2026-10-28): the
    # verdict of record for this event is the verdict the ruled
    # calculus produces over the event's content -- the taught
    # input against the working copy carrying the event's direct
    # updates -- confirmed by replay: the run is made twice
    # through the shared calculus entry and the verdict and the
    # exact total must be identical. The recorded human verdict
    # (human_verdict, the recorded expectation) gates nothing
    # from here on; its disposition is settled at step 5.
    _run1 = _verification_run(input_text, working, allow_pending=True)
    _run2 = _verification_run(input_text, working, allow_pending=True)
    verification_verdict = _run1[0]
    verification_total = _run1[1]
    replay_confirmed = _run1 == _run2

    # 4. Generalization PROPOSALS (propose-only, peer-review fix).
    # Candidates are checked one at a time against the post-teaching
    # baseline: a proposal is sound only when it moves a case from
    # HOLD to the event's verification verdict. Sound candidates are
    # RECORDED as proposals (proposal_id, stored in the pending
    # seed) and NOT applied. The teacher applies them explicitly via
    # confirm_generalizations(). A related edge that already carries
    # the same harm is a no-op confirmation (recorded, edge untouched).
    # A related edge valued differently is flagged: inference never
    # overwrites an explicit value.
    baseline = _verdicts_for_cases(check_cases, working)
    candidates = []
    gen_proposed = []
    gen_confirmed = []
    gen_flagged = []
    for upd in applied:
        source = upd["source"]
        target = upd["target"]
        harm = upd["new_harm"]
        group = _group_for(working["entries"][target]["category"])
        if group is None:
            continue
        rule = _rule_for(source, working)
        if rule is None:
            continue
        for edge in working["edges"]:
            if edge["source"] != source:
                continue
            if edge["target"] == target:
                continue
            if (edge["source"], edge["target"]) in direct_targets:
                continue
            if edge["intrinsic"]:
                continue
            if working["entries"][edge["target"]]["category"] not in group:
                continue
            if edge["harm"] is not None:
                if edge["harm"] == harm:
                    gen_confirmed.append({
                        "source": source, "target": edge["target"],
                        "harm": harm, "rule": rule,
                        "reason": upd["reason"],
                        "generalized_from": [source, target],
                        "provenance": edge["provenance"],
                        "note": "no change (confirmation)",
                    })
                else:
                    gen_flagged.append({
                        "source": source, "target": edge["target"],
                        "harm": harm, "rule": rule,
                        "reason": upd["reason"],
                        "generalized_from": [source, target],
                        "flag_reason": "already valued differently; "
                                       "generalization does not override",
                    })
                continue
            candidates.append({
                "source": source, "target": edge["target"], "harm": harm,
                "rule": rule, "reason": upd["reason"],
                "generalized_from": (source, target),
            })
    for cand in candidates:
        tentative = copy.deepcopy(working)
        edge = pmca_seed.find_edge(
            tentative, cand["source"], cand["target"])
        edge["harm"] = cand["harm"]
        edge["provenance"] = "teaching:" + event_id + ":generalized"
        edge["reason"] = cand["reason"] + " (generalized)"
        edge["triple_place"] = False
        ok = True
        for case in check_cases:
            tentative_verdict = evaluate_with_seed(
                case, tentative, allow_pending=True)
            baseline_verdict = baseline[case]
            if tentative_verdict != baseline_verdict:
                if not (baseline_verdict == "HOLD"
                        and tentative_verdict == verification_verdict):
                    ok = False
                    break
        if ok:
            proposal_id = ("prop-" + event_id + "-"
                           + str(len(gen_proposed)))
            gen_proposed.append({
                "proposal_id": proposal_id,
                "source": cand["source"], "target": cand["target"],
                "harm": cand["harm"], "rule": cand["rule"],
                "reason": cand["reason"],
                "generalized_from": list(cand["generalized_from"]),
                "provenance": "teaching:" + event_id + ":generalized",
                "note": "proposed, not applied",
            })
        else:
            gen_flagged.append({
                "source": cand["source"], "target": cand["target"],
                "harm": cand["harm"], "rule": cand["rule"],
                "reason": cand["reason"],
                "generalized_from": list(cand["generalized_from"]),
                "flag_reason": "would change a verdict beyond HOLD-to-approved",
            })

    # 5. Verification record (TEACH-V): the verification verdict
    # computed at step 3b is the verdict of record. The recorded
    # expectation (human_verdict) is a claim to be verified, never
    # an authority, and is never silently overridden in either
    # direction:
    # - replay not confirmed (verdict or exact total differed
    #   between the two runs): the event cannot be verified --
    #   outcome "replay_mismatch", held.
    # - the expectation is stated in the ruled verdict set and
    #   differs from the verification verdict: outcome
    #   "held_disagreement", held; both verdicts are recorded
    #   here and on the pending event.
    # - otherwise the event is "verified": no expectation, an
    #   expectation that agrees, or a legacy expectation outside
    #   the ruled set (REDACT) -- the last is preserved as
    #   provenance, flagged expectation_in_ruled_set False, and
    #   gates nothing.
    # "matches" keeps its suite meaning: the event verified.
    expectation_in_ruled_set = human_verdict in VERDICTS
    if not replay_confirmed:
        verification_outcome = "replay_mismatch"
    elif (expectation_in_ruled_set
            and human_verdict != verification_verdict):
        verification_outcome = "held_disagreement"
    else:
        verification_outcome = "verified"
    verification = {
        "matches": verification_outcome == "verified",
        "verdict_after": verification_verdict,
        "expected": human_verdict,
        "recorded_expectation": human_verdict,
        "verification_verdict": verification_verdict,
        "verification_total": verification_total,
        "replay_total": _run2[1],
        "replay_confirmed": replay_confirmed,
        "expectation_in_ruled_set": expectation_in_ruled_set,
        "outcome": verification_outcome,
    }

    # 6. Regression: any change from an established verdict is a conflict.
    regressions = []
    conflicts = []
    for case in check_cases:
        after = evaluate_with_seed(case, working, allow_pending=True)
        before = pre[case]
        if after != before:
            regressions.append(
                {"case": case, "before": before, "after": after})
            if before != "HOLD":
                conflicts.append(
                    {"case": case, "before": before, "after": after})

    # 7. Status. A held event (TEACH-V: disagreement with the
    # recorded expectation, or an unconfirmed replay) reports
    # verification_failed -- the event did not verify; its pending
    # copy can never be promoted (approve() gates on the recorded
    # verification outcome).
    if not verification["matches"]:
        status = "verification_failed"
    elif conflicts:
        status = "conflict"
    elif not applied and rejected:
        status = "rejected"
    else:
        status = "applied"

    # 8. Seal cleared; counts recomputed. This working copy is now
    # PENDING (superposition): unsealed, carrying its pending event
    # and pending proposals. approve() is the only promotion step.
    working["metadata"]["seal"] = ""
    unvalued = 0
    for edge in working["edges"]:
        if edge["harm"] is None:
            unvalued += 1
    working["metadata"]["unvalued_count"] = unvalued
    working["metadata"]["triple_placed_count"] = unvalued
    post_verdicts = _verdicts_for_cases(check_cases, working)
    working["pending_event"] = {
        "event_id": event_id,
        "input": input_text,
        # TEACH-V: human_verdict is carried as provenance (the
        # recorded expectation); the verdict of record is the
        # verification verdict, and approve() re-verifies against
        # it and gates on the verification outcome.
        "human_verdict": human_verdict,
        "recorded_expectation": human_verdict,
        "verification_verdict": verification_verdict,
        "verification_total": verification_total,
        "verification_outcome": verification_outcome,
        "check_cases": check_cases,
        "post_verdicts": post_verdicts,
        # Fix Pack E, item 1: the conflicts this event recorded,
        # carried on the seed so approve() can gate on them.
        "conflicts": copy.deepcopy(conflicts),
    }
    working["pending_proposals"] = copy.deepcopy(gen_proposed)

    # 9. Audit: append-only.
    audit_entry = {
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": input_text,
        "system_verdict": system_verdict,
        "human_verdict": human_verdict,
        "recorded_expectation": human_verdict,
        "verification_verdict": verification_verdict,
        "explanation_text": explanation_text,
        "explanation_parse_status": expl_status,
        "explanation_parse_tokens": expl_tokens,
        "edge_updates": copy.deepcopy(edge_updates),
        "applied_updates": applied,
        "rejected_updates": rejected,
        "generalizations_proposed": gen_proposed,
        "generalizations_confirmed": gen_confirmed,
        "generalizations_flagged": gen_flagged,
        "superseded_records": superseded_new,
        "verification": verification,
        "regressions": regressions,
        "conflicts": conflicts,
        "status": status,
    }
    _AUDIT_LOG.append(audit_entry)
    if audit_path is not None:
        with open(audit_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(audit_entry, sort_keys=True) + "\n")

    # Graph architecture (2026-10-20): the pending event's
    # candidates are addressed into the POTENTIAL own space; the
    # module-held potential space is refreshed from this pending
    # seed.
    _refresh_potential_held(working)

    return {
        "event_id": event_id,
        "status": status,
        "seed": working,
        "needs_reseal": True,
        "applied_updates": applied,
        "rejected_updates": rejected,
        "generalizations_proposed": gen_proposed,
        "generalizations_confirmed": gen_confirmed,
        "generalizations_flagged": gen_flagged,
        "superseded_records": superseded_new,
        "verification": verification,
        "regressions": regressions,
        "conflicts": conflicts,
        "explanation_parse": {
            "status": expl_status, "tokens": expl_tokens,
        },
        "audit_entry": audit_entry,
    }


def confirm_generalizations(seed, proposal_ids):
    """Apply teacher-approved generalization proposals (by proposal_id)
    to a COPY of the pending seed. Returns a dict with keys: seed
    (updated copy), applied (applied proposals), skipped (proposals
    skipped as stale: edge already valued since the proposal was
    made), unknown_ids (IDs with no matching proposal), remaining
    (proposals still pending), error ("" on success).

    Unapproved proposals stay in pending_proposals: confirming is a
    decision to apply SOME, not a decision to discard the rest. Use
    discard_proposals() to reject the remainder explicitly.
    Confirm is ATOMIC: if applying the approved set together would
    change a verdict beyond HOLD-to-approved (proposals interact),
    NOTHING is applied and the result carries an error; the input
    seed is returned unchanged. The input seed is never mutated."""
    working = copy.deepcopy(seed)
    pending = working.get("pending_event")
    if pending is None:
        return {"seed": seed, "applied": [], "skipped": [],
                "unknown_ids": list(proposal_ids),
                "remaining": working.get("pending_proposals", []),
                "error": "not a pending seed (no pending_event)"}
    by_id = {}
    for prop in working.get("pending_proposals", []):
        by_id[prop["proposal_id"]] = prop
    approved = []
    unknown = []
    for pid in proposal_ids:
        if pid in by_id:
            approved.append(by_id[pid])
        else:
            unknown.append(pid)
    if not approved:
        _refresh_potential_held(working)
        return {"seed": working, "applied": [], "skipped": [],
                "unknown_ids": unknown,
                "remaining": working.get("pending_proposals", []),
                "error": ""}
    tentative = copy.deepcopy(working)
    applied = []
    skipped = []
    for prop in approved:
        edge = pmca_seed.find_edge(
            tentative, prop["source"], prop["target"])
        if edge is None or edge["harm"] is not None:
            skipped.append({
                "proposal_id": prop["proposal_id"],
                "source": prop["source"], "target": prop["target"],
                "reason": "stale: edge already valued since proposal",
            })
            continue
        edge["harm"] = prop["harm"]
        edge["provenance"] = prop["provenance"]
        edge["reason"] = prop["reason"] + " (generalized)"
        edge["triple_place"] = False
        applied.append(prop)
    check_cases = pending["check_cases"]
    post = pending["post_verdicts"]
    # TEACH-V: the direction a confirmation may move a HOLD case
    # is the event's verification verdict (its verdict of record).
    # A pending seed recorded before TEACH-V carries no
    # verification verdict: the verification verdict is computed
    # fresh against the working copy by double replay, and the
    # recorded expectation is gated exactly as in teach() step 5
    # -- an in-set expectation that disagrees with the fresh
    # verification verdict holds the event and nothing is
    # applied; the human verdict is never the fallback target.
    target_verdict = pending.get("verification_verdict")
    if target_verdict is None:
        _fresh1 = _verification_run(pending["input"], working,
                                    allow_pending=True)
        _fresh2 = _verification_run(pending["input"], working,
                                    allow_pending=True)
        if _fresh1 != _fresh2:
            _refresh_potential_held(seed)
            _reason = ("verification_not_confirmed: replay runs differ")
            return {"seed": seed, "applied": [], "skipped": [],
                    "unknown_ids": unknown,
                    "remaining": working.get("pending_proposals", []),
                    "error": _reason,
                    "outcome": "error", "reason": _reason}
        target_verdict = _fresh1[0]
        _expectation = pending.get(
            "recorded_expectation", pending.get("human_verdict"))
        _expectation_in_ruled_set = _expectation in VERDICTS
        if (_expectation_in_ruled_set
                and _expectation != target_verdict):
            _refresh_potential_held(seed)
            _reason = ("held_disagreement: recorded expectation %s, "
                       "verification verdict %s; a held event is "
                       "never promoted"
                       % (_expectation, target_verdict))
            return {"seed": seed, "applied": [], "skipped": [],
                    "unknown_ids": unknown,
                    "remaining": working.get("pending_proposals", []),
                    "error": _reason,
                    "outcome": "error", "reason": _reason}
    for case in check_cases:
        new_verdict = evaluate_with_seed(case, tentative,
                                         allow_pending=True)
        old_verdict = post[case]
        if new_verdict != old_verdict:
            if not (old_verdict == "HOLD"
                    and new_verdict == target_verdict):
                _refresh_potential_held(seed)
                return {"seed": seed, "applied": [], "skipped": [],
                        "unknown_ids": unknown,
                        "remaining": working.get("pending_proposals", []),
                        "error": "combination unsafe: applying the "
                                 "approved set together would change "
                                 "verdict for case: " + case}
    applied_ids = set()
    for prop in applied:
        applied_ids.add(prop["proposal_id"])
    skipped_ids = set()
    for item in skipped:
        skipped_ids.add(item["proposal_id"])
    remaining = []
    for prop in working.get("pending_proposals", []):
        if (prop["proposal_id"] not in applied_ids
                and prop["proposal_id"] not in skipped_ids):
            remaining.append(prop)
    tentative["pending_proposals"] = remaining
    unvalued = 0
    for edge in tentative["edges"]:
        if edge["harm"] is None:
            unvalued += 1
    tentative["metadata"]["unvalued_count"] = unvalued
    tentative["metadata"]["triple_placed_count"] = unvalued
    _refresh_potential_held(tentative)
    return {"seed": tentative, "applied": applied, "skipped": skipped,
            "unknown_ids": unknown, "remaining": remaining, "error": ""}


def discard_proposals(seed):
    """Return a copy of the pending seed with all pending generalization
    proposals discarded (rejected). The input seed is never mutated.
    Use this to explicitly reject proposals before approve()."""
    working = copy.deepcopy(seed)
    working["pending_proposals"] = []
    _refresh_potential_held(working)
    return working


def _append_archive_record(archive_path, record):
    """Append one proposal record to the archive JSONL file. Append-only:
    the file is never overwritten, and each write is fsynced before
    returning (durable). Raises OSError on write failure; the caller
    treats that as an approval failure so no proposal is ever lost."""
    with open(archive_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def list_archived_proposals(archive_path):
    """Read the proposals archive JSONL file and return the archived
    proposals (in order). If the file does not exist, return [].
    If any non-empty line is not valid JSON, raise ValueError — a
    corrupted archive is reported, never silently skipped."""
    if not os.path.exists(archive_path):
        return []
    records = []
    with open(archive_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip() == "":
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                raise ValueError(
                    "corrupted proposals archive: bad JSON line")
    return records


def get_archived_proposal(archive_path, proposal_id):
    """Return the archived proposal record with the given proposal_id,
    or None if no such record exists."""
    for record in list_archived_proposals(archive_path):
        if record.get("proposal_id") == proposal_id:
            return record
    return None


def approve(seed, archive_path=None):
    """Promote PENDING -> CURRENT (see _approve_impl for the
    full contract). With an entry store configured
    (configure_entry_store), the act additionally runs inside
    an entry lifecycle: input is the pending-event reference,
    steps are the supersession operations in order (see
    _approve_steps), outcome is this function's return
    verbatim, and the caller is acknowledged only after the
    entry is durable. Without a store, this is exactly
    _approve_impl."""
    if _ENTRY_STORE is None:
        return _approve_impl(seed, archive_path)
    pending = seed.get("pending_event")
    reference = {"event_id": None, "pending_event": None}
    if isinstance(pending, dict):
        reference = {"event_id": pending.get("event_id"),
                     "pending_event": pending}
    with _ENTRY_STORE.process(
            "teaching", reference, _entry_environment(),
            {"seed_generation_before": None}) as handle:
        result = _approve_impl(seed, archive_path)
        for kind, data in _approve_steps(seed, result):
            handle.step(kind, data)
        handle.outcome = result
        handle.ending_state = {"seed_generation_after": None}
    return result


def _approve_impl(seed, archive_path=None):
    """Promote PENDING -> CURRENT (superposition model). Re-verifies
    the pending seed: the taught case must still yield the pending
    event's VERIFICATION verdict (TEACH-V: the verdict of record
    teach() computed and replay-confirmed; the recorded human
    verdict is provenance and is never compared here), no held
    verification outcome may promote, and every regression case
    must still yield its recorded post-teaching verdict (detects
    tampering with the pending seed after teach()).

    CONFLICT GATE (Fix Pack E, item 1; claims audit 10, claim 5):
    if the pending event recorded verdict conflicts at teach time
    (pending_event["conflicts"] non-empty), approval is REFUSED:
    the conflict record stays on the seed, the promotion does not
    happen, and the result names the refusal
    (conflicts_recorded_at_teach). A conflicted override can no
    longer be promoted in two calls. If the pending seed carries unresolved
    generalization proposals, approval does NOT fail (Decision 2,
    JONATHAN, 2026-10-15 — archive-and-remove, superseding the
    earlier design where approve() failed on unresolved proposals).
    Instead: each unresolved proposal is archived to local storage
    (the proposals archive JSONL file, append-only, fsynced), the
    proposals are removed from the pending seed, and the seed is
    sealed clean, without pending proposals. Proposals persist
    locally for later retrieval — they are never lost and never
    silently discarded. On success, clears the pending state, seals
    the seed, and returns {"status": "approved", "seed": sealed_seed,
    "reason": "", "archived_proposals": [proposal_ids]}. On failure,
    returns {"status": "approve_failed", "seed": unchanged input seed,
    "reason": ...} and does NOT seal. The input seed is never
    mutated. On success the event's candidates are additionally
    promoted into the module-held LIVE own space (graph
    architecture, 2026-10-20; see live_own_space)."""
    pending = seed.get("pending_event")
    if pending is None:
        return {"status": "approve_failed", "seed": seed,
                "reason": "not a pending seed (no pending_event)"}
    recorded_conflicts = pending.get("conflicts") or []
    if recorded_conflicts:
        named = "; ".join(
            item["case"] + ": " + item["before"] + " -> " + item["after"]
            for item in recorded_conflicts)
        return {"status": "approve_failed", "seed": seed,
                "reason": "conflicts_recorded_at_teach: approval "
                          "refused; the pending event recorded "
                          "verdict conflict(s): " + named,
                "conflicts": copy.deepcopy(recorded_conflicts)}
    # TEACH-V held gate: an event verification held at teach time
    # (a ruled-set expectation that disagreed with the
    # verification verdict, or a replay that did not confirm) can
    # never be promoted, whatever the pending seed now yields.
    verification_outcome = pending.get("verification_outcome")
    if verification_outcome == "held_disagreement":
        return {"status": "approve_failed", "seed": seed,
                "reason": "held_disagreement: recorded expectation "
                          + str(pending.get("recorded_expectation",
                                            pending.get(
                                                "human_verdict")))
                          + ", verification verdict "
                          + str(pending.get("verification_verdict"))
                          + "; a held event is never promoted"}
    if verification_outcome == "replay_mismatch":
        return {"status": "approve_failed", "seed": seed,
                "reason": "verification_not_confirmed: the "
                          "teach-time replay did not confirm "
                          "(verdict or exact total differed); a "
                          "held event is never promoted"}
    verdict = evaluate_with_seed(pending["input"], seed,
                                 allow_pending=True)
    verification_verdict = pending.get("verification_verdict")
    if verification_verdict is None:
        # A pending seed recorded before TEACH-V carries no
        # verification verdict of record. Verify it now, by
        # replay, and hold the result as the verdict of record
        # for this re-verification.
        _fresh1 = _verification_run(pending["input"], seed,
                                    allow_pending=True)
        _fresh2 = _verification_run(pending["input"], seed,
                                    allow_pending=True)
        if _fresh1 != _fresh2:
            return {"status": "approve_failed", "seed": seed,
                    "reason": "verification_not_confirmed: replay "
                              "runs at approve differ"}
        verification_verdict = _fresh1[0]
        # TEACH-V gate for legacy events (mirror teach() step 5):
        # the recorded expectation is provenance only; an in-set
        # expectation that disagrees with the fresh verification
        # verdict holds the event. Out-of-set expectations (e.g.
        # REDACT) and absent expectations gate nothing.
        _expectation = pending.get(
            "recorded_expectation", pending.get("human_verdict"))
        _expectation_in_ruled_set = _expectation in VERDICTS
        if (_expectation_in_ruled_set
                and _expectation != verification_verdict):
            return {"status": "approve_failed", "seed": seed,
                    "reason": "held_disagreement: recorded expectation %s, verification verdict %s; a held event is never promoted" % (_expectation, verification_verdict)}
    if verdict != verification_verdict:
        return {"status": "approve_failed", "seed": seed,
                "reason": "taught case no longer matches "
                          "verification verdict"}
    for case, expected in pending["post_verdicts"].items():
        if evaluate_with_seed(case, seed, allow_pending=True) != expected:
            return {"status": "approve_failed", "seed": seed,
                    "reason": "regression case changed since teach: " + case}
    # Decision 2: archive-and-remove. Archiving happens only after
    # re-verification succeeds, so a failed approval leaves the
    # pending seed (with its proposals) untouched. An archive write
    # failure is an approval failure: the seed stays pending, and no
    # proposal is lost.
    archived_ids = []
    pending_proposals = seed.get("pending_proposals") or []
    if pending_proposals:
        target = archive_path or DEFAULT_ARCHIVE_PATH
        try:
            for prop in pending_proposals:
                record = {
                    "proposal_id": prop["proposal_id"],
                    "event_id": pending["event_id"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "proposal": copy.deepcopy(prop),
                }
                _append_archive_record(target, record)
                archived_ids.append(prop["proposal_id"])
        except OSError:
            return {"status": "approve_failed", "seed": seed,
                    "reason": "proposal archive write failed"}
    working = copy.deepcopy(seed)
    working.pop("pending_event", None)
    working.pop("pending_proposals", None)
    pmca_seed.seal(working)
    if archived_ids:
        _AUDIT_LOG.append({
            "event_id": pending["event_id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": pending["input"],
            "human_verdict": pending["human_verdict"],
            "recorded_expectation": pending.get(
                "recorded_expectation", pending["human_verdict"]),
            "verification_verdict": pending.get(
                "verification_verdict"),
            "action": "approve_archived_proposals",
            "archived_proposals": list(archived_ids),
            "status": "approved",
        })
    # Graph architecture (2026-10-20): promotion also moves the
    # event's candidates from the POTENTIAL own space into the
    # held LIVE own space.
    _promote_to_live(working, pending["event_id"])
    return {"status": "approved", "seed": working, "reason": "",
            "archived_proposals": archived_ids}


# ---------------------------------------------------------------------------
# Static checks (same patterns as the sibling modules)
# ---------------------------------------------------------------------------

def check_no_max():
    """AST check: no call to maximum anywhere in this file."""
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "max":
                return False
    return True


def check_no_floats():
    """AST check: no float literal anywhere in this file."""
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            return False
    return True


def check_no_random():
    """AST check: no random import anywhere in this file."""
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "random":
                    return False
        if isinstance(node, ast.ImportFrom) and node.module == "random":
            return False
    return True


def check_no_forbidden_word():
    """Grep check: the forbidden guessing verb does not appear in this
    file. Pattern is built from parts so the checker does not match
    itself."""
    pattern = "ass" + "ume"
    return pattern not in open(__file__).read()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def run_tests():
    passed = 0
    total = 0

    def check(name, cond):
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print("PASS: " + name)
        else:
            print("FAIL: " + name)

    clear_audit_log()

    # 1. Teach stab->fish = 0 (was unvalued): "stab the fish" -> APPROVE.
    seed1 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    event1 = {
        "event_id": "teach-test-1",
        "input": "stab the fish",
        "human_verdict": "APPROVE",
        "explanation_text": "stabbing a fish is cooking, not violence",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "stabbing a fish is cooking, not violence"},
        ],
    }
    result1 = teach(seed1, event1)
    check("teach stab->fish=0 status applied",
          result1["status"] == "applied")
    # Fix Pack E, item 4 (claims audit 10, claim 1): reading a
    # PENDING seed's verdict now requires the explicit flag; this
    # check previously evaluated the pending seed silently, which
    # is the exact behaviour the audit proved defective.
    check("teach stab->fish=0 verdict APPROVE",
          evaluate_with_seed("stab the fish", result1["seed"],
                             allow_pending=True) == "APPROVE")
    check("teach stab->fish=0 audit recorded",
          len(audit_log()) == 1
          and audit_log()[0]["event_id"] == "teach-test-1")

    # 3. Generalization, fine categories: teach make->bread = 0
    # (already 0, no-change direct update). Same-verb-same-category
    # (food) finds make->soup = 0 as a no-op confirmation (recorded,
    # edge untouched). make->bomb (weapon) and make->fire (other)
    # get NO proposal: different categories never generalize.
    seed3 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    event3 = {
        "event_id": "teach-test-3",
        "input": "make bread",
        "human_verdict": "APPROVE",
        "explanation_text": "making bread is ordinary cooking",
        "edge_updates": [
            {"source": "make", "target": "bread", "harm": 0,
             "reason": "making bread is ordinary cooking"},
        ],
    }
    result3 = teach(seed3, event3)
    check("generalization make->bread=0 status applied",
          result3["status"] == "applied")
    soup_conf = None
    for g in result3["generalizations_confirmed"]:
        if (g["source"], g["target"]) == ("make", "soup"):
            soup_conf = g
    check("generalization make->soup=0 no-op confirmation",
          soup_conf is not None
          and soup_conf["note"] == "no change (confirmation)")
    soup_edge = pmca_seed.find_edge(result3["seed"], "make", "soup")
    check("generalization no-op leaves edge untouched",
          soup_edge is not None
          and soup_edge["harm"] == 0
          and soup_edge["provenance"] == "rule:verb-food")
    check("generalization make->bomb gets NO proposal (weapon != food)",
          not any((g["source"], g["target"]) == ("make", "bomb")
                  for g in result3["generalizations_proposed"]))
    check("generalization make->fire gets NO proposal (other has no group)",
          not any((g["source"], g["target"]) == ("make", "fire")
                  for g in result3["generalizations_proposed"]))

    # 3b. Propose-only (peer-review fix): teach poison->rat = 2.
    # Rat is pest; cat/dog are pet. Fine categories mean NO proposal
    # for cat/dog (the old bug generalized rat -> cat/dog). A second
    # pest (mouse) IS proposed -- but NOT applied: the pending seed
    # still has poison->mouse unvalued. Confirm applies only the
    # approved proposal; confirm with none applies nothing.
    poison_dict = list(pmca_seed.DEMO_DICTIONARY)
    poison_dict.append({
        "word": "poison", "pos": ["VERB"],
        "definitions": ["To administer a toxic substance."],
        "relationships": [
            {"target": "person", "role": "Definer", "harm": None},
            {"target": "rat", "role": "Definer", "harm": None},
            {"target": "cat", "role": "Definer", "harm": None},
            {"target": "dog", "role": "Definer", "harm": None},
            {"target": "mouse", "role": "Definer", "harm": None},
        ],
        "harm_notes": None, "category": "other",
    })
    poison_dict.append({
        "word": "mouse", "pos": ["NOUN"],
        "definitions": ["A small rodent; a pest."],
        "relationships": [], "harm_notes": None, "category": "pest",
    })
    seed3b = pmca_seed.build_seed(poison_dict)
    event3b = {
        "event_id": "teach-test-3b",
        "input": "poison the rat",
        "human_verdict": "REDACT",
        "explanation_text": "poisoning a rat is lawful pest control",
        "edge_updates": [
            {"source": "poison", "target": "rat", "harm": 2,
             "reason": "lawful pest control"},
        ],
    }
    result3b = teach(seed3b, event3b)
    check("poison teach status applied",
          result3b["status"] == "applied")
    check("poison direct update applied (poison->rat=2)",
          pmca_seed.find_edge(result3b["seed"], "poison", "rat")["harm"] == 2)
    mouse_props = [g for g in result3b["generalizations_proposed"]
                   if (g["source"], g["target"]) == ("poison", "mouse")]
    check("poison->mouse PROPOSED (same fine category pest)",
          len(mouse_props) == 1
          and mouse_props[0]["proposal_id"] == "prop-teach-test-3b-0"
          and mouse_props[0]["note"] == "proposed, not applied")
    check("poison->mouse NOT applied (still unvalued in pending seed)",
          pmca_seed.find_edge(result3b["seed"], "poison", "mouse")["harm"] is None)
    check("poison->cat gets NO proposal (pet != pest)",
          not any((g["source"], g["target"]) == ("poison", "cat")
                  for g in result3b["generalizations_proposed"]))
    check("poison->dog gets NO proposal (pet != pest)",
          not any((g["source"], g["target"]) == ("poison", "dog")
                  for g in result3b["generalizations_proposed"]))
    check("pending seed carries the proposal",
          result3b["seed"]["pending_proposals"] == result3b["generalizations_proposed"])
    # Confirm with approval for mouse only -> mouse=2, cat/dog unvalued.
    confirmed = confirm_generalizations(
        result3b["seed"], ["prop-teach-test-3b-0"])
    check("confirm mouse-only applies mouse=2",
          confirmed["error"] == ""
          and pmca_seed.find_edge(confirmed["seed"], "poison", "mouse")["harm"] == 2
          and len(confirmed["applied"]) == 1)
    check("confirm mouse-only leaves cat/dog unvalued",
          pmca_seed.find_edge(confirmed["seed"], "poison", "cat")["harm"] is None
          and pmca_seed.find_edge(confirmed["seed"], "poison", "dog")["harm"] is None)
    check("confirm mouse-only leaves no remaining proposals",
          confirmed["remaining"] == [])
    # Confirm with approval for none -> nothing applied, proposal remains.
    confirmed_none = confirm_generalizations(result3b["seed"], [])
    check("confirm none applies nothing",
          confirmed_none["error"] == ""
          and confirmed_none["applied"] == []
          and pmca_seed.find_edge(
              confirmed_none["seed"], "poison", "mouse")["harm"] is None)
    check("confirm none leaves the proposal pending",
          len(confirmed_none["remaining"]) == 1)
    # Unknown proposal id is reported, nothing applied.
    confirmed_unknown = confirm_generalizations(
        result3b["seed"], ["prop-no-such-id"])
    check("confirm unknown id reported, nothing applied",
          confirmed_unknown["unknown_ids"] == ["prop-no-such-id"]
          and confirmed_unknown["applied"] == []
          and pmca_seed.find_edge(
              confirmed_unknown["seed"], "poison", "mouse")["harm"] is None)
    # Discard rejects the proposal explicitly.
    discarded = discard_proposals(result3b["seed"])
    check("discard clears pending proposals",
          discarded["pending_proposals"] == []
          and pmca_seed.find_edge(
              discarded, "poison", "mouse")["harm"] is None)
    check("discard does not mutate the input seed",
          len(result3b["seed"]["pending_proposals"]) == 1)

    # 2. Teach cut->person = 8 (was unvalued): "cut the person" -> REFUSE.
    seed2 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    audit_before_2 = len(audit_log())
    event2 = {
        "event_id": "teach-test-2",
        "input": "cut the person",
        "human_verdict": "REFUSE",
        "explanation_text": "cutting a person is violence",
        "edge_updates": [
            {"source": "cut", "target": "person", "harm": 8,
             "reason": "cutting a person is violence"},
        ],
    }
    result2 = teach(seed2, event2)
    check("teach cut->person=8 status applied",
          result2["status"] == "applied")
    # Fix Pack E, item 4 (claims audit 10, claim 1): explicit flag
    # for a pending seed, as above.
    check("teach cut->person=8 verdict REFUSE",
          evaluate_with_seed("cut the person", result2["seed"],
                             allow_pending=True) == "REFUSE")
    check("teach cut->person=8 audit recorded",
          len(audit_log()) == audit_before_2 + 1)

    # 4. Conflict: teach dead->fish = 8, overriding the valued 0 that
    # yields APPROVE for the fish sentence. Update applies, conflict flagged.
    seed4 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    event4 = {
        "event_id": "teach-test-4",
        "input": "the dead bloodied fish was charred",
        "human_verdict": "REFUSE",
        "explanation_text": "dead fish sentence is refused by teacher override",
        "edge_updates": [
            {"source": "dead", "target": "fish", "harm": 8,
             "reason": "teacher override: dead fish sentence is refused"},
        ],
    }
    result4 = teach(seed4, event4)
    check("conflict update applies (human override)",
          pmca_seed.find_edge(result4["seed"], "dead", "fish")["harm"] == 8)
    check("conflict status",
          result4["status"] == "conflict")
    check("conflict records changed verdict",
          any(c["case"] == "the dead bloodied fish was charred"
              and c["before"] == "APPROVE"
              and c["after"] == "REFUSE"
              for c in result4["regressions"]))

    # 5. Intrinsic rejection: update targeting the IntrinsicHarm edge.
    seed5 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    event5 = {
        "event_id": "teach-test-5",
        "input": "test-slur-word person",
        "human_verdict": "HOLD",
        "explanation_text": "intrinsic edges are not teachable",
        "edge_updates": [
            {"source": "test-slur-word", "target": "person", "harm": 0,
             "reason": "attempt to override an intrinsic edge"},
        ],
    }
    result5 = teach(seed5, event5)
    check("intrinsic update rejected",
          len(result5["rejected_updates"]) == 1
          and result5["rejected_updates"][0]["source"] == "test-slur-word")
    check("intrinsic edge unchanged",
          pmca_seed.find_edge(result5["seed"], "test-slur-word", "person")["harm"] == 10
          and pmca_seed.find_edge(result5["seed"], "test-slur-word", "person")["intrinsic"] is True)
    check("intrinsic status rejected",
          result5["status"] == "rejected")

    # 6. Verification failure: human verdict REFUSE, update sets harm 0.
    seed6 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    event6 = {
        "event_id": "teach-test-6",
        "input": "stab the fish",
        "human_verdict": "REFUSE",
        "explanation_text": "teacher says refuse but sets harm zero",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "contradictory update"},
        ],
    }
    result6 = teach(seed6, event6)
    check("verification failure status",
          result6["status"] == "verification_failed")
    check("verification failure recorded",
          result6["verification"]["matches"] is False
          and result6["verification"]["verdict_after"] == "APPROVE")

    # 7. Explanation parse recorded in the audit entry.
    check("explanation parse recorded",
          result1["audit_entry"]["explanation_parse_status"] in
          ("TIER1", "TIER2", "HOLD", "IDIOM", "COMPOUND"))

    # 8. Audit append-only: two events, first entry unchanged.
    clear_audit_log()
    seed_a = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    teach(seed_a, {
        "event_id": "teach-append-1",
        "input": "stab the fish",
        "human_verdict": "APPROVE",
        "explanation_text": "stabbing a fish is cooking",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "cooking"},
        ],
    })
    snapshot = copy.deepcopy(audit_log()[0])
    seed_b = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    teach(seed_b, {
        "event_id": "teach-append-2",
        "input": "cut the person",
        "human_verdict": "REFUSE",
        "explanation_text": "cutting a person is violence",
        "edge_updates": [
            {"source": "cut", "target": "person", "harm": 8,
             "reason": "violence"},
        ],
    })
    check("audit append-only: two entries",
          len(audit_log()) == 2)
    check("audit append-only: first entry unchanged",
          audit_log()[0] == snapshot)

    # 9. Pending / approve (superposition, Fix 3): teach() output is
    # PENDING (unsealed). approve() re-verifies, seals, promotes.
    check("teach() output is PENDING (unsealed, verify False)",
          result1["seed"]["metadata"]["seal"] == ""
          and result1["needs_reseal"] is True
          and pmca_seed.verify(result1["seed"]) is False)
    check("teach() output carries pending_event",
          result1["seed"]["pending_event"]["event_id"] == "teach-test-1"
          and result1["seed"]["pending_event"]["human_verdict"] == "APPROVE")
    approved = approve(result1["seed"])
    check("approve() promotes pending -> sealed current",
          approved["status"] == "approved"
          and pmca_seed.verify(approved["seed"]) is True)
    check("approved seed carries no pending state",
          "pending_event" not in approved["seed"]
          and "pending_proposals" not in approved["seed"])
    check("approved seed keeps the taught value",
          pmca_seed.find_edge(approved["seed"], "stab", "fish")["harm"] == 0)
    # Approve archives unresolved proposals (Decision 2, 2026-10-15):
    # it no longer fails. The old failing check is replaced by the
    # archive tests in section 9b below.
    # Approve fails when verification no longer holds.
    approve_failed = approve(result6["seed"])
    check("approve() fails on verification_failed teaching (does not seal)",
          approve_failed["status"] == "approve_failed"
          and pmca_seed.verify(approve_failed["seed"]) is False)
    # Approve fails on a non-pending seed.
    fresh_seed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    pmca_seed.seal(fresh_seed)
    approve_not_pending = approve(fresh_seed)
    check("approve() fails on a non-pending (already sealed) seed",
          approve_not_pending["status"] == "approve_failed")
    # Approve detects tampering with the pending seed after teach().
    tampered_pending = copy.deepcopy(result1["seed"])
    edge_t = pmca_seed.find_edge(tampered_pending, "stab", "fish")
    edge_t["harm"] = 10
    approve_tampered = approve(tampered_pending)
    check("approve() fails on a tampered pending seed (does not seal)",
          approve_tampered["status"] == "approve_failed"
          and pmca_seed.verify(approve_tampered["seed"]) is False)
    # Original seed UNCHANGED by teach(): still sealed, byte-identical.
    orig_seed = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    pmca_seed.seal(orig_seed)
    orig_json_before = pmca_seed.canonical_seed_json(orig_seed)
    teach(orig_seed, {
        "event_id": "teach-unchanged-check",
        "input": "stab the fish",
        "human_verdict": "APPROVE",
        "explanation_text": "stabbing a fish is cooking",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "cooking"},
        ],
    })
    check("original seed UNCHANGED by teach() (still sealed, byte-identical)",
          pmca_seed.verify(orig_seed) is True
          and pmca_seed.canonical_seed_json(orig_seed) == orig_json_before)

    # 9b. Approve archives unresolved proposals (Decision 2,
    # JONATHAN, 2026-10-15 — archive-and-remove). Uses a temp archive
    # file so the real proposals_archive.jsonl is never touched.
    tmpdir = tempfile.mkdtemp()
    arch = os.path.join(tmpdir, "proposals_archive.jsonl")

    def poison_seed_with_proposal(event_id):
        d = copy.deepcopy(poison_dict)
        s = pmca_seed.build_seed(d)
        r = teach(s, {
            "event_id": event_id,
            "input": "poison the rat",
            "human_verdict": "REDACT",
            "explanation_text": "poisoning a rat is lawful pest control",
            "edge_updates": [
                {"source": "poison", "target": "rat", "harm": 2,
                 "reason": "lawful pest control"},
            ],
        })
        return r

    # (a) approve() with unresolved proposals SUCCEEDS (no longer
    # fails): seed sealed, proposals removed from seed, proposals
    # present in the archive file.
    res_a = poison_seed_with_proposal("teach-archive-1")
    check("archive (a): teach produces 1 unresolved proposal",
          len(res_a["seed"]["pending_proposals"]) == 1)
    appr_a = approve(res_a["seed"], archive_path=arch)
    check("archive (a): approve() SUCCEEDS with unresolved proposals",
          appr_a["status"] == "approved")
    check("archive (a): approved seed is sealed",
          pmca_seed.verify(appr_a["seed"]) is True)
    check("archive (a): approved seed carries no pending state",
          "pending_event" not in appr_a["seed"]
          and "pending_proposals" not in appr_a["seed"])
    check("archive (a): approve returns archived proposal IDs",
          appr_a["archived_proposals"] == ["prop-teach-archive-1-0"])
    check("archive (a): approved seed keeps the taught value",
          pmca_seed.find_edge(appr_a["seed"], "poison", "rat")["harm"] == 2)
    check("archive (a): proposal NOT applied to seed (still unvalued)",
          pmca_seed.find_edge(
              appr_a["seed"], "poison", "mouse")["harm"] is None)
    # (b) list_archived_proposals() returns the archived proposals.
    listed = list_archived_proposals(arch)
    check("archive (b): list returns 1 archived record",
          len(listed) == 1)
    check("archive (b): record carries proposal_id, event_id, proposal",
          listed[0]["proposal_id"] == "prop-teach-archive-1-0"
          and listed[0]["event_id"] == "teach-archive-1"
          and listed[0]["proposal"]["source"] == "poison"
          and listed[0]["proposal"]["target"] == "mouse"
          and listed[0]["proposal"]["harm"] == 2)
    # (c) get_archived_proposal() finds one by ID, None for unknown.
    found = get_archived_proposal(arch, "prop-teach-archive-1-0")
    check("archive (c): get finds proposal by ID",
          found is not None
          and found["proposal_id"] == "prop-teach-archive-1-0")
    check("archive (c): get returns None for unknown ID",
          get_archived_proposal(arch, "prop-no-such-id") is None)
    # (d) append-only: a second approve() with proposals appends;
    # the first set is not overwritten.
    res_d = poison_seed_with_proposal("teach-archive-2")
    appr_d = approve(res_d["seed"], archive_path=arch)
    check("archive (d): second approve() SUCCEEDS",
          appr_d["status"] == "approved")
    listed2 = list_archived_proposals(arch)
    check("archive (d): archive holds BOTH sets (2 records)",
          len(listed2) == 2)
    check("archive (d): first set NOT overwritten",
          listed2[0]["proposal_id"] == "prop-teach-archive-1-0"
          and listed2[1]["proposal_id"] == "prop-teach-archive-2-0")
    # (e) approve() with NO unresolved proposals: unchanged behavior
    # (seals, archive untouched — no file created for a fresh path).
    arch_empty = os.path.join(tmpdir, "no-proposals.jsonl")
    seed_e = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    res_e = teach(seed_e, {
        "event_id": "teach-archive-3",
        "input": "stab the fish",
        "human_verdict": "APPROVE",
        "explanation_text": "stabbing a fish is cooking, not violence",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "cooking"},
        ],
    })
    check("archive (e): no-proposal teach has empty pending proposals",
          res_e["seed"]["pending_proposals"] == [])
    appr_e = approve(res_e["seed"], archive_path=arch_empty)
    check("archive (e): approve() with no proposals SUCCEEDS",
          appr_e["status"] == "approved"
          and pmca_seed.verify(appr_e["seed"]) is True)
    check("archive (e): approve returns empty archived list",
          appr_e["archived_proposals"] == [])
    check("archive (e): archive file NOT created (untouched)",
          not os.path.exists(arch_empty))
    # Missing archive file -> empty list, not an error.
    check("archive: missing file returns []",
          list_archived_proposals(
              os.path.join(tmpdir, "does-not-exist.jsonl")) == [])
    # Corrupted archive (bad JSON on a line) -> ValueError.
    arch_bad = os.path.join(tmpdir, "corrupt.jsonl")
    with open(arch_bad, "w", encoding="utf-8") as handle:
        handle.write('{"proposal_id": "prop-ok"}\n')
        handle.write('this is not json\n')
    raised = False
    try:
        list_archived_proposals(arch_bad)
    except ValueError:
        raised = True
    check("archive: corrupted file raises ValueError (not skipped)",
          raised)
    # Approve with a failed verification does NOT archive: the
    # pending seed (with proposals) stays untouched.
    res_f = poison_seed_with_proposal("teach-archive-4")
    edge_f = pmca_seed.find_edge(res_f["seed"], "poison", "rat")
    edge_f["harm"] = 10
    arch_f = os.path.join(tmpdir, "failed.jsonl")
    appr_f = approve(res_f["seed"], archive_path=arch_f)
    check("archive: failed approval does NOT archive proposals",
          appr_f["status"] == "approve_failed"
          and not os.path.exists(arch_f))
    # Approve audit entry records the archived proposal IDs.
    audit_arch = [e for e in audit_log()
                  if e.get("action") == "approve_archived_proposals"]
    check("archive: audit entry records archived proposal IDs",
          len(audit_arch) == 2
          and audit_arch[0]["archived_proposals"]
          == ["prop-teach-archive-1-0"]
          and audit_arch[1]["archived_proposals"]
          == ["prop-teach-archive-2-0"])
    # Input seed never mutated by approve-with-archive.
    check("archive: approve does not mutate the input seed",
          len(res_a["seed"]["pending_proposals"]) == 1
          and res_a["seed"]["metadata"]["seal"] == "")

    # 10. Determinism: same event on two fresh seeds -> identical seed
    # JSON and identical audit entries (timestamp stripped).
    clear_audit_log()
    event_d = {
        "event_id": "teach-determinism",
        "input": "stab the fish",
        "human_verdict": "APPROVE",
        "explanation_text": "stabbing a fish is cooking, not violence",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "stabbing a fish is cooking, not violence"},
        ],
    }
    seed_d1 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    seed_d2 = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    result_d1 = teach(seed_d1, event_d)
    result_d2 = teach(seed_d2, event_d)
    check("determinism: identical seed JSON",
          pmca_seed.canonical_seed_json(result_d1["seed"])
          == pmca_seed.canonical_seed_json(result_d2["seed"]))
    entry_d1 = copy.deepcopy(result_d1["audit_entry"])
    entry_d2 = copy.deepcopy(result_d2["audit_entry"])
    entry_d1.pop("timestamp")
    entry_d2.pop("timestamp")
    check("determinism: identical audit entry (timestamp stripped)",
          entry_d1 == entry_d2)

    # 11. Static checks.
    check("static: no maximum call anywhere (AST)", check_no_max())
    check("static: no float literal anywhere (AST)", check_no_floats())
    check("static: no random import (AST)", check_no_random())
    check("static: no forbidden guessing verb (grep)",
          check_no_forbidden_word())

    # 12. Siblings: proof and Layer 1 still pass on their own data.
    here = os.path.dirname(os.path.abspath(__file__))
    proof_run = subprocess.run(
        [sys.executable, os.path.join(here, "pmca_proof.py")],
        capture_output=True, text=True)
    check("sibling proof still passes",
          proof_run.returncode == 0
          and "tests passed" in proof_run.stdout)
    layer1_run = subprocess.run(
        [sys.executable, os.path.join(here, "pmca_layer1.py")],
        capture_output=True, text=True)
    check("sibling layer1 still passes",
          layer1_run.returncode == 0
          and "tests passed" in layer1_run.stdout)

    # 13. Fix Pack E (2026-10-26; evidence: claims audit 10,
    # synthesis D6): the six teaching-semantics closures.
    clear_audit_log()
    base_e = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)

    # 13a. approve() gates on recorded conflicts (audit claim 5):
    # a conflicted override can no longer be promoted in two calls.
    res_conf = teach(base_e, {
        "event_id": "teach-packe-conflict",
        "input": "the dead bloodied fish was charred",
        "human_verdict": "REFUSE",
        "explanation_text": "teacher override: dead fish refused",
        "edge_updates": [
            {"source": "dead", "target": "fish", "harm": 8,
             "reason": "teacher override"},
        ],
    })
    check("packE conflict teach records conflicts on the pending seed",
          res_conf["status"] == "conflict"
          and len(res_conf["seed"]["pending_event"]["conflicts"]) == 1
          and res_conf["seed"]["pending_event"]["conflicts"][0]["before"]
          == "APPROVE"
          and res_conf["seed"]["pending_event"]["conflicts"][0]["after"]
          == "REFUSE")
    appr_conf = approve(res_conf["seed"])
    check("packE approve() REFUSES a conflict-recorded pending seed",
          appr_conf["status"] == "approve_failed"
          and "conflicts_recorded_at_teach" in appr_conf["reason"])
    check("packE conflict refusal keeps the record, does not seal",
          "pending_event" in appr_conf["seed"]
          and len(appr_conf["seed"]["pending_event"]["conflicts"]) == 1
          and pmca_seed.verify(appr_conf["seed"]) is False)

    # 13b. Pending stacking refused by name (audit claim 1): the
    # first pending event is never silently replaced.
    res_first = teach(base_e, {
        "event_id": "teach-packe-first",
        "input": "stab the fish",
        "human_verdict": "APPROVE",
        "explanation_text": "stabbing a fish is cooking",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "cooking"},
        ],
    })
    audit_len_before_stack = len(audit_log())
    res_stack = teach(res_first["seed"], {
        "event_id": "teach-packe-second",
        "input": "cut the person",
        "human_verdict": "REFUSE",
        "explanation_text": "cutting a person is violence",
        "edge_updates": [
            {"source": "cut", "target": "person", "harm": 8,
             "reason": "violence"},
        ],
    })
    check("packE teach onto a pending seed is refused by name",
          res_stack["status"] == "rejected"
          and "pending_event_exists" in res_stack["refusal"])
    check("packE stacking refusal leaves the first pending event intact",
          res_stack["seed"] is res_first["seed"]
          and res_stack["seed"]["pending_event"]["event_id"]
          == "teach-packe-first")
    check("packE stacking refusal appends no audit entry",
          len(audit_log()) == audit_len_before_stack)
    appr_first = approve(res_first["seed"])
    check("packE the first pending event still approves afterwards",
          appr_first["status"] == "approved")

    # 13c. Supersession retained ON THE SEED (audit claim 2):
    # teach 9 -> approve -> teach 7 -> approve; the final seed shows
    # the current 7 AND the superseded 9 with its provenance.
    res_s1 = teach(base_e, {
        "event_id": "teach-packe-ss-1",
        "input": "cut the person",
        "human_verdict": "REFUSE",
        "explanation_text": "cutting a person is violence (v1)",
        "edge_updates": [
            {"source": "cut", "target": "person", "harm": 9,
             "reason": "v1"},
        ],
    })
    v1 = approve(res_s1["seed"])["seed"]
    check("packE first valuation leaves no superseded record",
          pmca_seed.find_edge(v1, "cut", "person")["harm"] == 9
          and not v1.get("superseded"))
    res_s2 = teach(v1, {
        "event_id": "teach-packe-ss-2",
        "input": "cut the person",
        "human_verdict": "REFUSE",
        "explanation_text": "cutting a person is violence (v2)",
        "edge_updates": [
            {"source": "cut", "target": "person", "harm": 7,
             "reason": "v2"},
        ],
    })
    v2 = approve(res_s2["seed"])["seed"]
    sup = v2.get("superseded") or []
    check("packE promoted lineage keeps the current value 7",
          pmca_seed.find_edge(v2, "cut", "person")["harm"] == 7
          and pmca_seed.find_edge(v2, "cut", "person")["provenance"]
          == "teaching:teach-packe-ss-2")
    check("packE superseded record keeps prior 9 + provenance on the seed",
          len(sup) == 1
          and sup[0]["source"] == "cut"
          and sup[0]["target"] == "person"
          and sup[0]["harm"] == 9
          and sup[0]["provenance"] == "teaching:teach-packe-ss-1"
          and sup[0]["superseded_by"] == "teach-packe-ss-2"
          and sup[0]["superseding_harm"] == 7)
    check("packE the v1 seed object itself was not mutated",
          pmca_seed.find_edge(v1, "cut", "person")["harm"] == 9
          and not v1.get("superseded"))

    # 13d. evaluate_with_seed refuses a pending seed by default
    # (audit claim 1); the explicit flag reads the pending verdict.
    raised = False
    message = ""
    try:
        evaluate_with_seed("stab the fish", res_first["seed"])
    except ValueError as exc:
        raised = True
        message = str(exc)
    check("packE evaluate_with_seed refuses a pending seed by default",
          raised and "pending_seed_refused" in message)
    check("packE allow_pending reads the pending verdict explicitly",
          evaluate_with_seed("stab the fish", res_first["seed"],
                             allow_pending=True) == "APPROVE")
    check("packE a sealed seed evaluates with no flag",
          evaluate_with_seed("stab the fish", v1) == "HOLD")

    # 13e. LEXICON isolation under concurrency (audit claim 4):
    # two threads, different lexicons (seed without "zebra" HOLDs,
    # seed with "zebra" APPROVEs the same sentence); every verdict
    # must be the thread's own seed's, and the process-global
    # lexicon must be the original object afterwards.
    zebra_dict = copy.deepcopy(pmca_seed.DEMO_DICTIONARY)
    for entry in zebra_dict:
        if entry["word"] == "stab":
            entry["relationships"].append(
                {"target": "zebra", "role": "Definer", "harm": 0})
    zebra_dict.append({
        "word": "zebra", "pos": ["NOUN"],
        "definitions": ["A striped equine."],
        "relationships": [], "harm_notes": None, "category": "other",
    })
    seed_plain = pmca_seed.build_seed(pmca_seed.DEMO_DICTIONARY)
    seed_zebra = pmca_seed.build_seed(zebra_dict)
    original_lexicon = pmca_layer1.LEXICON
    barrier = threading.Barrier(2)
    thread_results = {"plain": [], "zebra": []}

    def _eval_loop(seed, key):
        barrier.wait()
        for _ in range(25):
            thread_results[key].append(
                evaluate_with_seed("stab the zebra", seed))

    t_plain = threading.Thread(
        target=_eval_loop, args=(seed_plain, "plain"))
    t_zebra = threading.Thread(
        target=_eval_loop, args=(seed_zebra, "zebra"))
    t_plain.start()
    t_zebra.start()
    t_plain.join()
    t_zebra.join()
    check("packE concurrent evaluations keep each seed's own verdict",
          thread_results["plain"] == ["HOLD"] * 25
          and thread_results["zebra"] == ["APPROVE"] * 25)
    check("packE LEXICON is the original object after both threads",
          pmca_layer1.LEXICON is original_lexicon)

    # 13f. Duplicate caller-supplied event_ids refused (audit
    # claim 6). The same id on a seed without that record is fine:
    # the check is against the seed's own records, not global.
    res_dup1 = teach(base_e, {
        "event_id": "teach-packe-dup",
        "input": "stab the fish",
        "human_verdict": "APPROVE",
        "explanation_text": "stabbing a fish is cooking",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "cooking"},
        ],
    })
    dup_current = approve(res_dup1["seed"])["seed"]
    audit_len_before_dup = len(audit_log())
    dup_json_before = pmca_seed.canonical_seed_json(dup_current)
    res_dup2 = teach(dup_current, {
        "event_id": "teach-packe-dup",
        "input": "cut the person",
        "human_verdict": "REFUSE",
        "explanation_text": "cutting a person is violence",
        "edge_updates": [
            {"source": "cut", "target": "person", "harm": 8,
             "reason": "violence"},
        ],
    })
    check("packE duplicate event_id is refused by name",
          res_dup2["status"] == "rejected"
          and "duplicate_event_id" in res_dup2["refusal"])
    check("packE duplicate refusal leaves seed and audit untouched",
          res_dup2["seed"] is dup_current
          and pmca_seed.canonical_seed_json(dup_current) == dup_json_before
          and len(audit_log()) == audit_len_before_dup)
    res_dup_other = teach(base_e, {
        "event_id": "teach-packe-dup",
        "input": "stab the fish",
        "human_verdict": "APPROVE",
        "explanation_text": "stabbing a fish is cooking",
        "edge_updates": [
            {"source": "stab", "target": "fish", "harm": 0,
             "reason": "cooking"},
        ],
    })
    check("packE same event_id on a seed without the record is fine",
          res_dup_other["status"] == "applied")

    print(str(passed) + "/" + str(total) + " tests passed")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
