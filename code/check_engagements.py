#!/usr/bin/env python3
"""PMCA engagement-log replay checker (falsifiability Stage 2).

Architecture and design: Jonathan Wayne Fleuren (the builder).
Checker: Alder (Muse), per Jonathan's falsifiability design;
no independent third-party verification.

Usage: python3 check_engagements.py <pool_dir> <state_root>

Verifies one pools "engagement" chain against the sealed analyzer
checkpoints under <state_root>/checkpoints/, and — from the
atomic-entry pack on — the entry-backed segments in the entry
store under <state_root>/atomic/:

1. Code verification is per record, uniform across every key a
   record's code_manifest names (the four manifest modules, and
   "dictionary" when a dictionary was in use): a record is
   code-verified iff every key it names equals the checker's
   computed value, which comes from the analyzer's own
   _code_manifest() after the checker configures its dictionary
   under the chain-governs rule: an explicit PMCA_DICTIONARY_DB
   wins; else, if the chain itself names a dictionary (a
   "dictionary" key in an engagement payload's code_manifest,
   or a truthy environment.dictionary.configured on an entry
   record), the DB at <this dir>/data/webster1913.db is
   configured as a locator for that recorded file only; else
   no dictionary is configured at all. There
   is no whole-chain pin to any one record's manifest: a chain
   may legitimately span code generations (the public git
   history keeps every generation), so a checker verifies the
   records written under its own generation and reports the
   rest UNVERIFIABLE (code), each judged under a checker of
   its own generation. A record naming a value the checker
   cannot reproduce is UNVERIFIABLE (code), with the differing
   keys named; keys a record does not name are not required
   of it, so pre-wiring records stay verifiable. Such a record
   is still replayed, keeping the state thread intact for its
   neighbours; only its own line reports the code failure, and
   a closed segment containing one is UNVERIFIABLE (code),
   exit 1. Two refusals stay whole-chain, exit 2: a chain with
   no engagement records at all, and a chain in which NO
   engagement record is code-verified (this checker can speak
   for nothing in it), reported as UNVERIFIABLE-CODE naming
   the first record's differing keys.
2. Chain: the engagement JSONL is hash-verified exactly as
   pmca_pools defines it (the timestamp enters only as a hash
   input, replicated from pools; wall-clock time is never
   compared, ordered, or read as evidence). The checked pool is
   never mutated: PoolStore is opened, if at all, on a temp copy.
3. Replay: per segment, the opening state is loaded from its
   named checkpoint (persistence.load_analyzer_space, which
   verifies the seal) or, when no checkpoint is named and the
   segment opens on the fresh-space fingerprint, from a fresh
   space. Each engagement is re-executed with analyze() and the
   recorded fields are compared in order: state_before, verdict,
   omega, harm, parameters, edges, hold_reason, lock_reason,
   trace, state_after. Segment keys are recomputed from the
   close record's own fields exactly as the analyzer computes
   them.
4. Entry-backed segments (atomic-entry pack, build spec section
   7): when <state_root>/atomic/atomic_entries.jsonl exists, its
   entries are judged after the pool chain, each segment by the
   evidence its own records carry. A segment_open entry
   establishes its segment's space from the starting_state_blob
   embedded in the entry itself, loaded through
   persistence.load_analyzer_space so the blob's own persistence
   checksum/seal is verified by exactly the checkpoint path; no
   external checkpoint is consulted for it, and none is ever
   synthesized, inferred, or accepted retroactively — a segment
   whose open entry is absent is UNVERIFIABLE (state), never
   vouched and never silently skipped. Environment is compared
   BEFORE any replay comparison of an entry-backed claim: the
   entry's recorded environment (code manifest keys, and the
   dictionary block whose identity is the configured flag plus
   the db hash, compared FROM THE RECORD, never inferred from
   this checker's configuration) against the checker's
   configured environment. A dictionary mismatch reports the
   claim UNVERIFIABLE (environment) and its segment
   UNVERIFIABLE-ENVIRONMENT, counted toward exit 1 exactly as
   UNVERIFIABLE (code) is; it NEVER produces a verdict FAIL,
   and the replay still runs so the state thread stays intact
   for neighbouring claims. Replay of an entry-backed claim
   compares the same fields in the same order as a pool claim
   except the trace: recorded steps are aligned against the
   replayed process's steps by step_seq, and the first
   divergence is reported as FAIL step <step_seq> (<kind>)
   recorded=<...> replayed=<...>, naming the entry_id. Entry
   seals and the entry chain are EntryStore's load-time
   discipline; this checker reads the entry file raw, as it
   reads the pool raw, so a damaged copy still yields
   per-segment verdicts instead of a whole-store refusal.

Exit 0 only when there are no FAILs and no UNVERIFIABLE among
closed segments. An unverifiable POTENTIAL (still-open) tail
does not fail the run; an unverifiable closed segment does.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

GENESIS = "GENESIS"
AUTHORSHIP = ("Checker: Alder (Muse), per Jonathan's "
              "falsifiability design; no independent third-party "
              "verification.")


# ---------------------------------------------------------------------
# Raw pool reading and chain verification (no PMCA imports: this
# phase must be able to refuse code it has not yet cleared).
# ---------------------------------------------------------------------

def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _pool_canonical(obj):
    """Pools' canonical JSON for hashing (pmca_pools._canonical):
    sorted keys, tight separators, ensure_ascii=False."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _record_hash(seq, namespace, timestamp, payload, prev_hash):
    """pmca_pools._record_hash, replicated exactly."""
    return _sha256_bytes(_pool_canonical({
        "seq": seq,
        "namespace": namespace,
        "timestamp": timestamp,
        "payload": payload,
        "prev_hash": prev_hash}))


def _parse_engagement_file(pool_dir):
    """Read <pool_dir>/engagement.jsonl read-only. Returns
    (records, parse_break): parsed record dicts, and the index of
    the first line that could not be parsed as the next record in
    sequence (a torn final line, as pools tolerates, simply ends
    the list without a parse break)."""
    path = os.path.join(pool_dir, "engagement.jsonl")
    if not os.path.isfile(path):
        return [], None
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    records = []
    parse_break = None
    nonempty = [i for i, line in enumerate(lines) if line.strip()]
    for pos, i in enumerate(nonempty):
        try:
            rec = json.loads(lines[i])
            if not isinstance(rec, dict):
                raise ValueError("non-object record")
        except (ValueError, TypeError):
            if pos != len(nonempty) - 1:
                parse_break = len(records)
            break
        records.append(rec)
    return records, parse_break


def _parse_entry_file(state_root):
    """Read <state_root>/atomic/atomic_entries.jsonl read-only.
    Returns the parsed entry dicts in file order; a missing file
    reads as no entries, and a torn final line simply ends the
    list (a line that cannot be parsed anywhere ends the list
    there: entries after corruption are unreachable, exactly the
    conservative end the pools torn-tail rule gives a chain).
    Seals are NOT verified here: seal and chain verification are
    EntryStore's load-time discipline, and this checker must
    still produce per-segment verdicts on a damaged copy (a
    deleted segment_open must read as one segment UNVERIFIABLE
    (state) with its neighbours unaffected, never as a
    whole-store refusal)."""
    path = os.path.join(state_root, "atomic",
                        "atomic_entries.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    entries = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError("non-object entry")
        except (ValueError, TypeError):
            break
        entries.append(entry)
    return entries


def _first_chain_break(records):
    """First index where the pools chain fails (seq, namespace,
    prev link, or hash), replicating pmca_pools._chain_ok; None
    when the chain verifies."""
    prev = GENESIS
    for i, rec in enumerate(records):
        try:
            if rec["seq"] != i:
                return i
            if rec["namespace"] != "engagement":
                return i
            if rec["prev_hash"] != prev:
                return i
            expect = _record_hash(rec["seq"], rec["namespace"],
                                  rec["timestamp"], rec["payload"],
                                  rec["prev_hash"])
            if rec["hash"] != expect:
                return i
        except (KeyError, TypeError):
            return i
        prev = rec["hash"]
    return None


def _show(value):
    text = repr(value)
    if len(text) > 100:
        return text[:97] + "..."
    return text


# ---------------------------------------------------------------------
# Checker proper (PMCA modules imported lazily, once the raw
# chain has been read; code classification is per record,
# against the computed manifest of the modules imported here).
# ---------------------------------------------------------------------

class _Segment(object):
    def __init__(self, open_payload):
        self.segment_id = open_payload.get("segment_id")
        self.opening_fingerprint = open_payload.get(
            "opening_fingerprint")
        self.checkpoint_generation = open_payload.get(
            "checkpoint_generation")
        # "ok" | "pending_fresh" | "bad_state" | "bad_chain"
        self.status = "pending_fresh"
        self.space = None
        self.tainted = False
        self.code_unverifiable = False
        self.env_unverifiable = False
        self.engagements = 0
        self.seen_first_seq = None
        self.seen_last_seq = None
        self.seen_last_hash = None


def _load_checkpoint(persistence, state_root, generation):
    """Load the checkpoint space for a generation, or raise.
    The host keeps it at
    <state_root>/checkpoints/analyzer_space_gen<G>.json; the
    module loader is used without new APIs by copying it into a
    fresh temp dir under the module's own file name."""
    path = os.path.join(state_root, "checkpoints",
                        "analyzer_space_gen%d.json" % generation)
    if not os.path.isfile(path):
        raise ValueError("checkpoint file missing: " + path)
    with open(path, "r", encoding="utf-8") as f:
        envelope = json.load(f)
    if envelope.get("generation") != generation:
        raise ValueError("checkpoint generation mismatch")
    tmp = tempfile.mkdtemp(prefix="pmca_check_ckpt_")
    try:
        shutil.copyfile(path, os.path.join(tmp, "analyzer_space.json"))
        return persistence.load_analyzer_space(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    if len(argv) != 3:
        print("usage: check_engagements.py <pool_dir> <state_root>")
        print(AUTHORSHIP)
        return 2
    pool_dir, state_root = argv[1], argv[2]

    # Phase 1: read the chain raw, before importing PMCA. The
    # only refusal made at this phase is a chain holding no
    # engagement records at all; code classification is per
    # record and happens in Phase 2, once the checker's computed
    # manifest exists.
    records, parse_break = _parse_engagement_file(pool_dir)
    entry_records = _parse_entry_file(state_root)
    engagement_payloads = []
    for rec in records:
        payload = rec.get("payload")
        if isinstance(payload, dict) and \
                payload.get("record_kind") == "engagement":
            engagement_payloads.append(payload)
    if not engagement_payloads and not entry_records:
        print("UNVERIFIABLE-CODE no engagement records")
        print(AUTHORSHIP)
        return 2

    # Phase 2: chain verification. Manual scan first (it names
    # the first broken seq); PoolStore is then cross-checked on a
    # temp COPY so the checked artifact is never mutated (opening
    # a PoolStore creates files and append handles, and repairs a
    # torn tail in place).
    break_seq = _first_chain_break(records)
    if parse_break is not None:
        break_seq = parse_break if break_seq is None else min(
            break_seq, parse_break)

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import pmca_graphspace
    import pmca_persistence
    import pmca_pools
    import pmca_sentence_analyzer as analyzer

    # Dictionary configuration: the chain's recorded
    # configuration governs; an explicit operator flag wins.
    # This decision is made strictly before the analyzer's code
    # manifest is first computed in this process: configuration
    # is an input to the manifest (which the analyzer caches per
    # process), and the manifest names the dictionary exactly
    # when one is in use. The computed manifest is the analyzer's
    # own, so module hashes and the dictionary hash are computed
    # one way only.
    # 1. PMCA_DICTIONARY_DB set (the codebase's existing
    #    explicit-flag mechanism: pmca_dictionary resolves that
    #    environment variable when nothing is configured
    #    explicitly, and a writer's host configures through it):
    #    configure that path. Records that do not match will
    #    honestly read UNVERIFIABLE via manifest_differing.
    # 2. Else, if the chain names a dictionary -- any engagement
    #    payload's code_manifest contains a "dictionary" key, or
    #    any entry record's environment.dictionary.configured is
    #    truthy -- configure the tree's data/webster1913.db if
    #    the file exists. That path is only a LOCATOR for the
    #    recorded file, never a configuration decision; if its
    #    hash differs from the recorded one, manifest_differing
    #    flags the chain as it does any other differing key.
    # 3. Else configure NOTHING: the dictionary stays off, as
    #    the chain was made. The default path must never be
    #    resurrected as a configuration decision of its own.
    dictionary_env = os.environ.get("PMCA_DICTIONARY_DB")
    if dictionary_env:
        analyzer.configure_dictionary(dictionary_env)
    else:
        chain_names_dictionary = any(
            isinstance(payload.get("code_manifest"), dict)
            and "dictionary" in payload["code_manifest"]
            for payload in engagement_payloads)
        if not chain_names_dictionary:
            for entry in entry_records:
                env = entry.get("environment")
                if not isinstance(env, dict):
                    continue
                recorded_dict = env.get("dictionary")
                if isinstance(recorded_dict, dict) and \
                        recorded_dict.get("configured"):
                    chain_names_dictionary = True
                    break
        if chain_names_dictionary:
            default_db = os.path.join(here, "data",
                                      "webster1913.db")
            if os.path.isfile(default_db):
                analyzer.configure_dictionary(default_db)
    computed_manifest = analyzer._code_manifest()

    def manifest_differing(payload):
        """Every code_manifest key the record names whose value
        the checker's computed manifest cannot reproduce (a
        different value, or a key that cannot be computed in
        this environment at all, e.g. a named dictionary when no
        dictionary is configured or found here). An empty list
        means the record is code-verified. Keys the record does
        not name are not required of it."""
        recorded = payload.get("code_manifest")
        if not isinstance(recorded, dict):
            return []
        return [key for key in sorted(recorded)
                if computed_manifest.get(key) != recorded[key]]

    # Whole-chain refusal: if NO engagement record in the chain
    # is code-verified, this checker can speak for nothing in
    # it (the wrong-code case: every record names code this
    # checker does not have). Report and stop before any claim
    # is judged, naming the first record's differing keys.
    if engagement_payloads and all(
            manifest_differing(p) for p in engagement_payloads):
        print("UNVERIFIABLE-CODE differing modules: "
              + ", ".join(manifest_differing(engagement_payloads[0])))
        print(AUTHORSHIP)
        return 2

    if break_seq is None:
        if records:
            copy_dir = tempfile.mkdtemp(prefix="pmca_check_pool_")
            try:
                copied = os.path.join(copy_dir, "pool")
                shutil.copytree(pool_dir, copied)
                try:
                    store = pmca_pools.PoolStore(copied)
                    store.verify_chain("engagement")
                    store_records = store.by_namespace("engagement")
                    if len(store_records) == len(records):
                        records = store_records
                except ValueError:
                    pass  # manual scan governs; it found no break
            finally:
                shutil.rmtree(copy_dir, ignore_errors=True)
    else:
        print("CHAIN BREAK at seq %d" % break_seq)

    fingerprint = pmca_persistence.analyzer_space_checksum
    # The fresh-space fingerprint, derived from the analyzer
    # module's own initial own space before any restore rebinds
    # it. Never a hardcoded literal.
    fresh_fingerprint = fingerprint(analyzer.current_own_space())

    claim_counts = {"PASS": 0, "FAIL": 0, "UNVERIFIABLE": 0}
    segment_counts = {"KEY-OK": 0, "KEY-FAIL": 0, "POTENTIAL": 0,
                      "DISCARDED": 0, "UNVERIFIABLE": 0}
    any_fail = False
    unverifiable_closed = False

    def claim_line(claim_id, outcome, detail=None):
        line = "%s %s" % (claim_id, outcome)
        if detail:
            line += " " + detail
        print(line)

    def tally_claim(outcome):
        claim_counts[outcome] += 1

    def segment_line(seg, outcome, detail=None):
        line = "SEG %s %s" % (seg.segment_id, outcome)
        if detail:
            line += " " + detail
        print(line)
        key = outcome if outcome in segment_counts else "UNVERIFIABLE"
        segment_counts[key] += 1

    def establish_from_checkpoint(seg):
        try:
            seg.space = _load_checkpoint(
                pmca_persistence, state_root,
                seg.checkpoint_generation)
        except Exception:
            seg.status = "bad_state"
            return
        analyzer.restore_own_space(seg.space)
        seg.status = "ok"

    def establish_fresh(seg):
        seg.space = pmca_graphspace.GraphSpace("own")
        analyzer.restore_own_space(seg.space)
        seg.status = "ok"

    def resolve_pending_fresh(seg, first_before):
        if first_before == fresh_fingerprint:
            establish_fresh(seg)
        else:
            seg.status = "bad_state"

    def replay_claim(seg, payload):
        """Replay one engagement record inside an established
        segment, evolving the segment's space exactly as the
        writer's did. Returns (outcome, detail): outcome is
        "PASS" or "FAIL", detail the FAIL field report or None.
        Printing and tallying are the caller's: a record whose
        code commitment cannot be reproduced is reported
        UNVERIFIABLE (code) whatever the replay finds."""
        claim_id = payload.get("claim_id") or "ENG-??????"
        before_recorded = (payload.get("state_before") or {}).get(
            "space_checksum")
        after_recorded = (payload.get("state_after") or {}).get(
            "space_checksum")
        fail = None
        before = fingerprint(seg.space)
        if before != before_recorded:
            fail = ("state_before", before_recorded, before)
        result = analyzer.analyze(payload["input"]["sentence"],
                                  payload["input"]["closure"],
                                  space=seg.space)
        after = fingerprint(seg.space)
        if fail is None:
            recorded_out = payload.get("output") or {}
            checks = []
            checks.append(("verdict", recorded_out.get("verdict"),
                           result["verdict"]))
            checks.append(("omega", recorded_out.get("omega"),
                           result["omega"]))
            replayed_harm = (list(result["harm"])
                             if result["harm"] is not None else None)
            checks.append(("harm", recorded_out.get("harm"),
                           replayed_harm))
            checks.append((
                "parameters", recorded_out.get("parameters"),
                json.loads(json.dumps(result["parameters"],
                                      sort_keys=True))))
            replayed_edges = [{
                "cause": e["cause"], "action": e["action"],
                "target": e["target"],
                "harm": (list(e["harm"])
                         if e["harm"] is not None else None)}
                for e in result["edges"]]
            checks.append(("edges", recorded_out.get("edges"),
                           replayed_edges))
            checks.append(("hold_reason",
                           recorded_out.get("hold_reason"),
                           result["hold_reason"]))
            checks.append(("lock_reason",
                           recorded_out.get("lock_reason"),
                           result["lock_reason"]))
            checks.append(("trace", payload.get("trajectory"),
                           list(result["trace"])))
            for field, recorded_value, replayed_value in checks:
                if recorded_value != replayed_value:
                    fail = (field, recorded_value, replayed_value)
                    break
        if fail is None and after != after_recorded:
            fail = ("state_after", after_recorded, after)
        if fail is not None:
            return ("FAIL", "%s recorded=%s replayed=%s"
                    % (fail[0], _show(fail[1]), _show(fail[2])))
        return ("PASS", None)

    def handle_engagement(seg, rec, payload):
        seq = rec.get("seq")
        claim_id = payload.get("claim_id") or "ENG-??????"
        if break_seq is not None and isinstance(seq, int) \
                and seq >= break_seq:
            seg.tainted = True
            claim_line(claim_id, "UNVERIFIABLE", "(chain)")
            tally_claim("UNVERIFIABLE")
            return
        if seg.tainted or seg.status == "bad_chain":
            claim_line(claim_id, "UNVERIFIABLE", "(chain)")
            tally_claim("UNVERIFIABLE")
            return
        if seg.status == "pending_fresh":
            resolve_pending_fresh(
                seg, (payload.get("state_before") or {}).get(
                    "space_checksum"))
        if seg.status == "bad_state":
            claim_line(claim_id, "UNVERIFIABLE", "(state)")
            tally_claim("UNVERIFIABLE")
            return
        seg.engagements += 1
        if seg.seen_first_seq is None:
            seg.seen_first_seq = seq
        seg.seen_last_seq = seq
        seg.seen_last_hash = rec.get("hash")
        outcome, detail = replay_claim(seg, payload)
        differing = manifest_differing(payload)
        if differing:
            # The replay above still ran, so the space evolved
            # as the writer's did and neighbouring claims are
            # judged on their own merits; this claim's commitment
            # names code or data this checker cannot reproduce,
            # so the claim itself cannot be vouched for.
            seg.code_unverifiable = True
            claim_line(claim_id, "UNVERIFIABLE",
                       "(code) differing: " + ", ".join(differing))
            tally_claim("UNVERIFIABLE")
            return None
        if outcome == "FAIL":
            claim_line(claim_id, "FAIL", detail)
            tally_claim("FAIL")
            return "FAIL"
        claim_line(claim_id, "PASS")
        tally_claim("PASS")
        return None

    def finalize_close(seg, payload):
        nonlocal any_fail, unverifiable_closed
        key_input = {
            "closing_fingerprint": payload.get("closing_fingerprint"),
            "segment_head": payload.get("segment_head"),
            "first_seq": payload.get("first_seq"),
            "last_seq": payload.get("last_seq")}
        recomputed = hashlib.sha256(json.dumps(
            key_input, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        if seg.status == "pending_fresh":
            resolve_pending_fresh(seg, seg.opening_fingerprint)
        if seg.tainted or seg.status == "bad_chain":
            segment_line(seg, "UNVERIFIABLE", "(chain)")
            unverifiable_closed = True
            return
        if seg.status == "bad_state":
            segment_line(seg, "UNVERIFIABLE-STATE")
            unverifiable_closed = True
            return
        if seg.env_unverifiable:
            segment_line(seg, "UNVERIFIABLE-ENVIRONMENT")
            unverifiable_closed = True
            return
        if seg.code_unverifiable:
            segment_line(seg, "UNVERIFIABLE", "(code)")
            unverifiable_closed = True
            return
        if recomputed != payload.get("key"):
            segment_line(seg, "KEY-FAIL", "(key)")
            any_fail = True
            return
        # Alder tightening (2026-10-20): the key must commit to the
        # segment the checker actually read, not only to the fields
        # the close record declares about itself.
        if seg.engagements > 0:
            if payload.get("segment_head") != seg.seen_last_hash:
                segment_line(seg, "KEY-FAIL",
                             "(segment_head recorded=%s actual=%s)"
                             % (_show(payload.get("segment_head")),
                                _show(seg.seen_last_hash)))
                any_fail = True
                return
            if payload.get("first_seq") != seg.seen_first_seq \
                    or payload.get("last_seq") != seg.seen_last_seq:
                segment_line(seg, "KEY-FAIL",
                             "(seq_range recorded=%s..%s "
                             "actual=%s..%s)"
                             % (payload.get("first_seq"),
                                payload.get("last_seq"),
                                seg.seen_first_seq,
                                seg.seen_last_seq))
                any_fail = True
                return
        elif payload.get("segment_head") != "GENESIS":
            segment_line(seg, "KEY-FAIL",
                         "(segment_head recorded=%s actual=GENESIS)"
                         % _show(payload.get("segment_head")))
            any_fail = True
            return
        closing = fingerprint(seg.space)
        if closing != payload.get("closing_fingerprint"):
            segment_line(seg, "KEY-FAIL",
                         "(closing_fingerprint recorded=%s "
                         "replayed=%s)"
                         % (_show(payload.get("closing_fingerprint")),
                            _show(closing)))
            any_fail = True
            return
        segment_line(seg, "KEY-OK")

    def finalize_discard(seg):
        if seg.tainted or seg.status == "bad_chain":
            segment_line(seg, "UNVERIFIABLE", "(chain)")
            return
        segment_line(seg, "DISCARDED")

    def finalize_potential(seg):
        segment_line(seg, "POTENTIAL")

    current = None
    for rec in records:
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("record_kind")
        seq = rec.get("seq")
        after_break = break_seq is not None and isinstance(seq, int) \
            and seq >= break_seq
        if kind == "segment_open":
            if current is not None:
                finalize_potential(current)
            current = _Segment(payload)
            if after_break:
                current.status = "bad_chain"
                current.tainted = True
            elif current.checkpoint_generation is not None:
                establish_from_checkpoint(current)
        elif kind == "engagement":
            if current is None:
                claim_id = payload.get("claim_id") or "ENG-??????"
                claim_line(claim_id, "UNVERIFIABLE", "(no segment)")
                tally_claim("UNVERIFIABLE")
                continue
            if handle_engagement(current, rec, payload) == "FAIL":
                any_fail = True
        elif kind == "segment_close":
            if current is None:
                continue
            if after_break:
                current.tainted = True
            finalize_close(current, payload)
            current = None
        elif kind == "segment_discard":
            if current is None:
                continue
            if after_break:
                current.tainted = True
            finalize_discard(current)
            current = None
    if current is not None:
        finalize_potential(current)

    # -----------------------------------------------------------------
    # Entry-backed phase (atomic-entry pack, build spec section 7).
    # The entry store is judged after the pool chain; each segment
    # is established from its own segment_open entry, and a segment
    # whose open entry is absent is UNVERIFIABLE (state) — never
    # vouched, never silently skipped, its neighbours unaffected.
    # -----------------------------------------------------------------
    checker_dictionary = {
        "configured": "dictionary" in computed_manifest,
        "db_hash": computed_manifest.get("dictionary")}

    def entry_env_differing(entry):
        """(dictionary_differs, code_differing) for one entry: the
        entry's recorded environment against this checker's
        configured environment, compared BEFORE any replay
        comparison. The dictionary identity is the configured
        flag plus the db hash, read FROM THE RECORD; the
        dictionary is judged here, under (environment), never as
        a code-manifest key. Code keys the entry does not name
        are not required of it, exactly as for pool records."""
        env = entry.get("environment")
        if not isinstance(env, dict):
            return True, ["environment"]
        recorded_dict = env.get("dictionary")
        if not isinstance(recorded_dict, dict):
            dict_diff = True
        else:
            dict_diff = (
                recorded_dict.get("configured")
                != checker_dictionary["configured"]
                or recorded_dict.get("db_hash")
                != checker_dictionary["db_hash"])
        code_diff = []
        recorded_manifest = env.get("code_manifest")
        if isinstance(recorded_manifest, dict):
            code_diff = [key for key in sorted(recorded_manifest)
                         if key != "dictionary"
                         and computed_manifest.get(key)
                         != recorded_manifest[key]]
        return dict_diff, code_diff

    def entry_segment_id(entry):
        """A segment's id as its entries name it: a top-level
        segment_id when present, else the starting_state block's
        (where segment_open and analysis entries carry it)."""
        if isinstance(entry.get("segment_id"), int):
            return entry.get("segment_id")
        starting = entry.get("starting_state")
        if isinstance(starting, dict):
            return starting.get("segment_id")
        return None

    def establish_from_blob(seg, blob):
        """Establish a segment's space from its segment_open
        entry's starting_state_blob: the serialized space is
        written to a fresh temp dir under the module's own file
        name and loaded through persistence.load_analyzer_space,
        exactly as _load_checkpoint loads a checkpoint file, so
        the blob's own persistence checksum/seal is verified by
        the same path. Any failure leaves the segment bad_state:
        its claims are UNVERIFIABLE (state), never vouched."""
        tmp = tempfile.mkdtemp(prefix="pmca_check_blob_")
        try:
            path = os.path.join(tmp, "analyzer_space.json")
            with open(path, "w", encoding="utf-8") as f:
                if isinstance(blob, str):
                    f.write(blob)
                else:
                    json.dump(blob, f)
            seg.space = pmca_persistence.load_analyzer_space(tmp)
        except Exception:
            seg.status = "bad_state"
            return
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        analyzer.restore_own_space(seg.space)
        seg.status = "ok"

    def open_entry_segment(entry):
        seg = _Segment({
            "segment_id": entry_segment_id(entry),
            "opening_fingerprint": entry.get(
                "opening_fingerprint"),
            "checkpoint_generation": None})
        blob = entry.get("starting_state_blob")
        if blob is not None:
            establish_from_blob(seg, blob)
        # No blob: the segment stays pending_fresh and is
        # resolved against its first claim's recorded starting
        # checksum exactly as a checkpoint-less pool segment is
        # (a fresh-space opening only; anything else is
        # bad_state). No state is ever synthesized for it.
        return seg

    def headless_entry_segment(entry):
        """The shell for a segment whose segment_open entry is
        absent from the store: it can never be established, so
        every claim in it is UNVERIFIABLE (state) and its close
        reports UNVERIFIABLE-STATE (spec 7.4, expectation C5)."""
        seg = _Segment({
            "segment_id": entry_segment_id(entry),
            "opening_fingerprint": None,
            "checkpoint_generation": None})
        seg.status = "bad_state"
        return seg

    def replay_entry_claim(seg, entry):
        """replay_claim for an entry-backed claim: the same
        fields in the same order as a pool claim — state_before,
        verdict, omega, harm, parameters, edges, hold_reason,
        lock_reason, trace, state_after — except the trace,
        which is step alignment by step_seq (spec 7.3): the
        first divergence is reported as step <step_seq> (<kind>)
        with the recorded and replayed step data. Returns
        (outcome, detail) like replay_claim."""
        starting = entry.get("starting_state") or {}
        ending = entry.get("ending_state") or {}
        before_recorded = starting.get("state_before_checksum")
        after_recorded = ending.get("state_after_checksum")
        fail = None
        step_fail = None
        before = fingerprint(seg.space)
        if before != before_recorded:
            fail = ("state_before", before_recorded, before)
        result = analyzer.analyze(entry["input"]["sentence"],
                                  entry["input"]["closure"],
                                  space=seg.space)
        after = fingerprint(seg.space)
        if fail is None:
            recorded_out = entry.get("outcome") or {}
            checks = []
            checks.append(("verdict", recorded_out.get("verdict"),
                           result["verdict"]))
            checks.append(("omega", recorded_out.get("omega"),
                           result["omega"]))
            replayed_harm = (list(result["harm"])
                             if result["harm"] is not None else None)
            checks.append(("harm", recorded_out.get("harm"),
                           replayed_harm))
            checks.append((
                "parameters", recorded_out.get("parameters"),
                json.loads(json.dumps(result["parameters"],
                                      sort_keys=True))))
            replayed_edges = [{
                "cause": e["cause"], "action": e["action"],
                "target": e["target"],
                "harm": (list(e["harm"])
                         if e["harm"] is not None else None)}
                for e in result["edges"]]
            checks.append(("edges", recorded_out.get("edges"),
                           replayed_edges))
            checks.append(("hold_reason",
                           recorded_out.get("hold_reason"),
                           result["hold_reason"]))
            checks.append(("lock_reason",
                           recorded_out.get("lock_reason"),
                           result["lock_reason"]))
            for field, recorded_value, replayed_value in checks:
                if recorded_value != replayed_value:
                    fail = (field, recorded_value, replayed_value)
                    break
            if fail is None:
                recorded_steps = {}
                for step in entry.get("steps") or []:
                    if isinstance(step, dict) and isinstance(
                            step.get("step_seq"), int):
                        recorded_steps[step["step_seq"]] = step
                replayed_steps = {}
                for i, line in enumerate(result["trace"]):
                    replayed_steps[i] = {"step_seq": i,
                                         "kind": "trace",
                                         "data": line}
                for seq_no in sorted(set(recorded_steps)
                                     | set(replayed_steps)):
                    rec_step = recorded_steps.get(seq_no)
                    rep_step = replayed_steps.get(seq_no)
                    if rec_step is None or rep_step is None \
                            or rec_step.get("kind") \
                            != rep_step.get("kind") \
                            or rec_step.get("data") \
                            != rep_step.get("data"):
                        kind = (rec_step or rep_step).get("kind")
                        step_fail = (
                            seq_no, kind,
                            rec_step.get("data")
                            if rec_step is not None else None,
                            rep_step.get("data")
                            if rep_step is not None else None)
                        break
        if fail is not None:
            return ("FAIL", "%s recorded=%s replayed=%s"
                    % (fail[0], _show(fail[1]), _show(fail[2])))
        if step_fail is not None:
            return ("FAIL",
                    "step %s (%s) recorded=%s replayed=%s"
                    % (step_fail[0], step_fail[1],
                       _show(step_fail[2]), _show(step_fail[3])))
        if after != after_recorded:
            return ("FAIL", "state_after recorded=%s replayed=%s"
                    % (_show(after_recorded), _show(after)))
        return ("PASS", None)

    def handle_entry_claim(seg, entry):
        """One analysis entry inside an entry-backed segment.
        The replay always runs first — the state thread stays
        intact for neighbouring claims — and the environment
        comparison then governs what this claim may be reported
        as: a dictionary mismatch is UNVERIFIABLE (environment)
        whatever the replay found, never a verdict FAIL."""
        entry_id = entry.get("entry_id") or "ENT-??????"
        if seg.status == "pending_fresh":
            resolve_pending_fresh(
                seg, (entry.get("starting_state") or {}).get(
                    "state_before_checksum"))
        if seg.status == "bad_state":
            claim_line(entry_id, "UNVERIFIABLE", "(state)")
            tally_claim("UNVERIFIABLE")
            return
        seg.engagements += 1
        if seg.seen_first_seq is None:
            seg.seen_first_seq = entry.get("seq")
        seg.seen_last_seq = entry.get("seq")
        seg.seen_last_hash = entry.get("seal")
        outcome, detail = replay_entry_claim(seg, entry)
        dict_diff, code_diff = entry_env_differing(entry)
        if dict_diff:
            seg.env_unverifiable = True
            claim_line(entry_id, "UNVERIFIABLE",
                       "(environment) differing: dictionary")
            tally_claim("UNVERIFIABLE")
            return None
        if code_diff:
            seg.code_unverifiable = True
            claim_line(entry_id, "UNVERIFIABLE",
                       "(code) differing: " + ", ".join(code_diff))
            tally_claim("UNVERIFIABLE")
            return None
        if outcome == "FAIL":
            claim_line(entry_id, "FAIL", detail)
            tally_claim("FAIL")
            return "FAIL"
        claim_line(entry_id, "PASS")
        tally_claim("PASS")
        return None

    ecurrent = None
    for entry in entry_records:
        kind = entry.get("kind")
        if kind == "segment_open":
            if ecurrent is not None:
                finalize_potential(ecurrent)
            ecurrent = open_entry_segment(entry)
        elif kind == "process":
            if entry.get("surface") != "analysis":
                # Other surfaces' entries are sealed process
                # records, not engagement claims; the checker
                # judges analysis claims only.
                continue
            if ecurrent is None:
                ecurrent = headless_entry_segment(entry)
            if handle_entry_claim(ecurrent, entry) == "FAIL":
                any_fail = True
        elif kind == "segment_close":
            if ecurrent is None:
                ecurrent = headless_entry_segment(entry)
            finalize_close(ecurrent, entry)
            ecurrent = None
        elif kind == "segment_discard":
            if ecurrent is None:
                continue
            finalize_discard(ecurrent)
            ecurrent = None
    if ecurrent is not None:
        finalize_potential(ecurrent)

    print("CLAIMS PASS=%d FAIL=%d UNVERIFIABLE=%d"
          % (claim_counts["PASS"], claim_counts["FAIL"],
             claim_counts["UNVERIFIABLE"]))
    print("SEGMENTS KEY-OK=%d KEY-FAIL=%d POTENTIAL=%d "
          "DISCARDED=%d UNVERIFIABLE=%d"
          % (segment_counts["KEY-OK"], segment_counts["KEY-FAIL"],
             segment_counts["POTENTIAL"], segment_counts["DISCARDED"],
             segment_counts["UNVERIFIABLE"]))
    print(AUTHORSHIP)
    if break_seq is not None or any_fail or unverifiable_closed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
