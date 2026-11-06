"""pmca_dossier_gate — the DICT-1 Part 4 dossier -> home gate.

Authority: DICT-1 Part 4, RULED 2026-10-31 (Jonathan:
"Ruled"; docket line 277): "The dossier is the starting
point, never a home. It gathers each word's attestations
source by source — never merged, conflicts recorded,
provenance and license carried. The home is the entry the
system builds from it in the one permanent storage, and the
built entry carries its starting-point provenance within
itself (atomic-entry ruling). What is conflicted or
unattested does NOT cross into the home: it remains at the
starting point as a recorded gap until resolved — conflict
gates the build rather than freezing a stored field."
Candidacy Rule Set C-8 states the same gate inside the set:
"Conflict gate (Part 4): conflicted material does not cross
from a starting point into a home; the conflict is the gap
record."

Built 2026-11-01, WG2 (04 Forge).

Shape: a DOSSIER is plain gathered data (the starting
point): {"word": w, "fields": {field: [attestation, ...]}}
where each attestation is {"value": ..., "provenance":
{...}} — attestations gathered source by source, never
merged. build_home_entry(store, dossier) is the gate at
the write-in/home-build path:

  - A field whose attestations AGREE on one value crosses:
    it is written into the home entry in the one permanent
    storage (a pmca_write_in store) through the store's own
    attestation path (_attest), once per attestation, so
    the home fact's provenance history IS its starting-
    point provenance carried inside itself — each carried
    provenance is stamped "starting_point": "dossier" and
    "dossier_field": <field> so the home record names where
    it started. Status follows the store's own PROM-1 rule
    (>= 2 distinct provenance classes verifies; the gate
    adds no promotion of its own, and POS/forms
    lineage-counted promotion stays blocked under C-6 —
    _attest's class counting is the store's standing rule,
    not a lineage-independence claim).
  - A field whose attestations CONFLICT (two or more
    distinct values) does not cross: nothing is written for
    it, and the conflict is recorded as a gap on the store's
    typed gap index (pmca_write_in.record_gap, reason
    "conflicted_at_dossier:<field>") — the conflict IS the
    gap record (C-8).
  - A field with NO attestation does not cross either: a
    gap is recorded ("unattested_at_dossier:<field>"). A
    field absent from the dossier is not gated and not
    gapped — the dossier simply never gathered it; the gate
    speaks only about what the dossier carries.

The gate's report names every field's disposition; no field
is silently dropped (no silent gaps). The dossier itself is
never mutated and never stored as a home.

WG2c WIRING (2026-11-01): the gate is on the production
path, not beside it. Its production caller is
pmca_write_in.write_dossier — the write-in's dossier
home-build door, which delegates to build_home_entry in
full (function-level import on that side, since this
module imports pmca_write_in). The WG2 second-pass check
found zero production callers at WG2; the caller set is
now write_dossier (production) plus the WG2 batteries
(tests). Paths audited in WG2c where dossiers genuinely
never flow — recorded, not wired: population enrichment
(single-source dictionary lookup; writes definition/
variables only, no multi-source field attestations),
dictionary_loop (its own loop-local store, single-source),
book_ingest (CANDIDATE records to its own ingest store,
never home entries), hold_checker (produces CANDIDATE
dossiers in its own store, builds no home entries),
teaching (GraphSpace seed, not a write-in store), and
stored_once (residence layer for write-in records; its
born-VERIFIED door is guarded there — WG2c D2).

Deterministic. Stdlib + pmca_write_in only.
"""

import copy
import json

import pmca_write_in as _wi


def _distinct_values(attestations):
    """The distinct attested values of one field, in
    first-attestation order, by canonical JSON identity."""
    seen = []
    for att in attestations or []:
        key = json.dumps(att.get("value"), sort_keys=True,
                         default=str)
        if key not in [k for k, _v in seen]:
            seen.append((key, att.get("value")))
    return [v for _k, v in seen]


def build_home_entry(store, dossier):
    """Gate one dossier into its home entry in `store` (a
    pmca_write_in store — the one permanent storage shape).
    Returns {"word", "outcome", "fields": {field:
    {"disposition": "crossed"|"held_conflicted"|
    "held_unattested", ...}}}. "outcome" is "built" when
    every dossier field crossed, "built_with_gaps" when at
    least one field was held at the gate (its gap recorded),
    "held" when no field crossed."""
    word = dossier["word"]
    fields = dossier.get("fields") or {}
    report = {"word": word, "fields": {}}
    crossed = 0
    for field in sorted(fields):
        atts = fields[field] or []
        values = _distinct_values(atts)
        if not atts or not values:
            gap = _wi.record_gap(store, word, field,
                                 "unattested_at_dossier:" + field)
            report["fields"][field] = {
                "disposition": "held_unattested",
                "gap_key": gap["gap_key"]}
            continue
        if len(values) > 1:
            gap = _wi.record_gap(store, word, field,
                                 "conflicted_at_dossier:" + field)
            report["fields"][field] = {
                "disposition": "held_conflicted",
                "values": values,
                "gap_key": gap["gap_key"]}
            continue
        # Agreeing field: cross, carrying starting-point
        # provenance inside the home fact.
        outcomes = []
        for att in atts:
            prov = copy.deepcopy(att.get("provenance") or {})
            prov["starting_point"] = "dossier"
            prov["dossier_field"] = field
            seq = _wi._next_seq(store)
            outcomes.append(_wi._attest(store, word, field,
                                        values[0], prov, seq))
        sub = store["entries"][word]["subcoordinates"][field]
        report["fields"][field] = {
            "disposition": "crossed", "value": values[0],
            "status": sub["status"],
            "attestations": len(atts),
            "outcomes": [o["outcome"] for o in outcomes]}
        crossed += 1
    if crossed == len(fields):
        report["outcome"] = "built"
    elif crossed == 0:
        report["outcome"] = "held"
    else:
        report["outcome"] = "built_with_gaps"
    return report
