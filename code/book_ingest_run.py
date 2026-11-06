"""Book-ingest verification battery driver (2026-10-28 pack).

Runs the sealed expectations of
~/workspace/goals/pmca-simulation/hidden_files/book-ingest-2026-10-28/expectations.md
against pmca_book_ingest, each ingest in its own subprocess for
isolation:

  Run A  serial,   fresh temp stores   -> stream A
  Run B  parallel, fresh temp stores   -> stream B   (i)
  Run P  serial,   fresh PACK stores   -> commits the deliverable
                                            candidate store + word store
  Run C  serial,   PACK stores again   -> re-ingest  (iv), (vii)

plus the tree hash before/after (ii), provenance/status/gap/word
checks over stream A (ii)(iii)(v)(vii), and both timings (vi).
Writes results.json into the pack dir and prints a summary.
Exit code 0 iff every sealed expectation passes.

Stdlib only. Never configures the dictionary; clears
PMCA_DICTIONARY_DB for every subprocess.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

TREE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.expanduser(
    "~/workspace/goals/pmca-simulation/hidden_files/book-ingest-2026-10-28")
FIXTURE = os.path.join(PACK, "fixture_a_civic_biology_ch1-3.txt")
MANIFEST = os.path.join(PACK, "ingest_manifest.json")
PACK_STORE = os.path.join(PACK, "book_ingest_store.jsonl")
PACK_WORDS = os.path.join(PACK, "book_word_store.json")
RESULTS = os.path.join(PACK, "results.json")


def tree_hash():
    h = hashlib.sha256()
    for root, dirs, files in os.walk(TREE):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith(".pyc"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, TREE)
            fh = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    fh.update(chunk)
            h.update((rel + "\0" + fh.hexdigest() + "\n").encode())
    return h.hexdigest()


def run_ingest(mode, store, words, dump):
    env = dict(os.environ)
    env.pop("PMCA_DICTIONARY_DB", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, os.path.join(TREE, "pmca_book_ingest.py"),
           FIXTURE, "--meta", MANIFEST, "--mode", mode,
           "--store", store, "--word-store", words, "--dump", dump]
    out = subprocess.run(cmd, cwd=TREE, env=env, capture_output=True,
                         text=True)
    if out.returncode != 0:
        raise RuntimeError("ingest %s failed:\n%s\n%s"
                           % (mode, out.stdout, out.stderr))
    return json.loads(out.stdout.strip().splitlines()[-1])


def read_stream(path):
    with open(path, "rb") as f:
        raw = f.read()
    records = [json.loads(line) for line in
               raw.decode("utf-8").splitlines() if line.strip()]
    return raw, records


def word_store_facts(path):
    sys.path.insert(0, TREE)
    import pmca_write_in as wi
    with open(path, "r", encoding="utf-8") as f:
        store = json.loads(f.read())
    entries = store["entries"]
    words = [w for w in entries if w != wi.SELF_KEY]
    pop_words = set()
    for rec in entries[wi.SELF_KEY].get("encounters", []):
        if rec.get("kind") == "population":
            pop_words.add(rec.get("word"))
    return {"hash": wi.store_hash(store), "word_entries": len(words),
            "population_event_words": sorted(pop_words)}


def main():
    results = {"expectations": {}, "counts": {}, "timings": {}}
    checks = results["expectations"]

    manifest = json.load(open(MANIFEST))
    meta_keys = ("book_id", "title", "author", "license", "source_url")

    h0 = tree_hash()
    results["tree_hash_before"] = h0

    tmp = tempfile.mkdtemp(prefix="book-ingest-verify-")
    a_store, a_words, a_dump = (os.path.join(tmp, n) for n in
                                ("a.jsonl", "a_words.json", "a_stream.jsonl"))
    b_store, b_words, b_dump = (os.path.join(tmp, n) for n in
                                ("b.jsonl", "b_words.json", "b_stream.jsonl"))

    stats_a = run_ingest("serial", a_store, a_words, a_dump)
    stats_b = run_ingest("parallel", b_store, b_words, b_dump)
    raw_a, records = read_stream(a_dump)
    raw_b, _records_b = read_stream(b_dump)

    # (i) serial == parallel, byte-identical streams
    checks["i_serial_parallel_byte_identical"] = bool(raw_a == raw_b)
    facts_a = word_store_facts(a_words)
    facts_b = word_store_facts(b_words)
    results["word_store_hash_serial"] = facts_a["hash"]
    results["word_store_hash_parallel"] = facts_b["hash"]
    results["word_stores_identical"] = bool(facts_a == facts_b)

    # counts
    by_kind = {}
    verdicts = {}
    for r in records:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        if r["kind"] == "sentence_outcome":
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    results["counts"] = {"by_kind": by_kind, "verdicts": verdicts,
                         "sentences": stats_a["sentences"],
                         "records_total": len(records)}
    results["timings"] = {
        "serial_parse_seconds": stats_a["parse_seconds"],
        "parallel_parse_seconds": stats_b["parse_seconds"],
        "workers_note": "parallel over chapters, fork pool",
        "segmentation_rule": stats_a["segmentation_rule"]}

    # (ii-a) statuses: nothing VERIFIED; candidates are CANDIDATE
    statuses_ok = True
    for r in records:
        if r.get("status") == "VERIFIED":
            statuses_ok = False
        if r["kind"] == "definition_candidate" \
                and r.get("status") != "CANDIDATE":
            statuses_ok = False
    checks["ii_no_verified_candidates_only"] = statuses_ok

    # (iii) complete provenance on every record
    prov_keys = meta_keys + ("chapter", "sentence_index", "char_offset")
    checks["iii_complete_provenance"] = all(
        all(r["provenance"].get(k) not in (None, "")
            for k in prov_keys)
        for r in records)

    # (v) sentences all present; HOLDs have gaps; unknown gaps exist
    outcomes = [r for r in records if r["kind"] == "sentence_outcome"]
    gap_sentences = set()
    unknown_gaps = 0
    for r in records:
        if r["kind"] == "gap":
            gap_sentences.add(r["provenance"]["sentence_index"])
            if r["gap_kind"] == "unknown_word":
                unknown_gaps += 1
    holds = [r for r in outcomes if r["verdict"] == "HOLD"]
    checks["v_sentences_all_recorded"] = bool(
        len(outcomes) == stats_a["sentences"])
    checks["v_every_hold_has_gap"] = all(
        r["provenance"]["sentence_index"] in gap_sentences
        for r in holds)
    checks["v_unknown_word_gaps_positive"] = bool(unknown_gaps > 0)
    results["counts"]["unknown_word_gaps"] = unknown_gaps
    results["counts"]["holds"] = len(holds)

    # (vii) word-level stored-once
    sys.path.insert(0, TREE)
    import pmca_sentence_analyzer as analyzer
    import pmca_graphspace
    occ = [r for r in records if r["kind"] == "word_occurrence"]
    words_seen = {}
    for r in occ:
        words_seen.setdefault(r["word_ref"]["word"], []).append(r)
    plan = stats_a["word_plan"]
    distinct = len(words_seen)
    partition_ok = (plan["seed_referenced"] + plan["already_in_store"]
                    + plan["placed"] == distinct)
    # every occurrence of a word carries one identical ref
    refs_consistent = all(
        len({(tuple(o["word_ref"]["coordinate"]),
              o["word_ref"]["stored_in"]) for o in occs}) == 1
        for occs in words_seen.values())
    man = words_seen.get("man", [])
    man_addr = pmca_graphspace.dist_address("man")
    man_ok = (len(man) > 0 and all(
        o["word_ref"]["stored_in"] == "seed"
        and o["word_ref"]["coordinate"] == list(man_addr)
        and o["word_ref"]["placed_event"] is None for o in man))
    seed_words_placed = [w for w in facts_a["population_event_words"]
                         if w in analyzer.KNOWN]
    checks["vii_partition"] = bool(partition_ok)
    checks["vii_refs_consistent"] = bool(refs_consistent)
    checks["vii_seed_word_man_zero_created"] = bool(man_ok)
    checks["vii_no_seed_word_placed"] = bool(not seed_words_placed)
    checks["vii_word_store_entries_equal_placed"] = bool(
        facts_a["word_entries"] == plan["placed"])
    results["words"] = {
        "distinct": distinct, "plan": plan,
        "man_occurrences": len(man),
        "man_coordinate": list(man_addr),
        "word_store_entries": facts_a["word_entries"],
        "seed_words_wrongly_placed": seed_words_placed}
    top_unknown = {}
    for r in records:
        if r["kind"] == "gap" and r["gap_kind"] == "unknown_word":
            w = r["detail"]["word"]
            top_unknown[w] = top_unknown.get(w, 0) + 1
    results["top_unknown_words"] = sorted(
        top_unknown.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    results["definition_candidates"] = [
        {"term": r["term"], "construction": r["construction"],
         "genus_phrase": r["genus_phrase"],
         "chapter": r["provenance"]["chapter"],
         "sentence_index": r["provenance"]["sentence_index"]}
        for r in records if r["kind"] == "definition_candidate"]

    # Run P: commit the deliverable pack stores (fresh)
    for p in (PACK_STORE, PACK_WORDS):
        if os.path.exists(p):
            os.remove(p)
    p_dump = os.path.join(tmp, "p_stream.jsonl")
    stats_p = run_ingest("serial", PACK_STORE, PACK_WORDS, p_dump)
    raw_p, _ = read_stream(p_dump)
    results["pack_run_stream_matches"] = bool(raw_p == raw_a)
    results["pack_run_appended"] = stats_p["appended"]
    with open(PACK_STORE, "rb") as f:
        store_bytes_before = f.read()
    pack_words_hash_before = word_store_facts(PACK_WORDS)["hash"]

    # Run C: re-ingest against the pack stores (iv) + (vii) re-ingest
    c_dump = os.path.join(tmp, "c_stream.jsonl")
    stats_c = run_ingest("serial", PACK_STORE, PACK_WORDS, c_dump)
    with open(PACK_STORE, "rb") as f:
        store_bytes_after = f.read()
    pack_words_hash_after = word_store_facts(PACK_WORDS)["hash"]
    checks["iv_reingest_zero_new"] = bool(
        stats_c["appended"] == 0
        and store_bytes_before == store_bytes_after)
    checks["vii_reingest_zero_placed"] = bool(
        stats_c["word_plan"]["placed"] == 0
        and pack_words_hash_before == pack_words_hash_after)
    results["reingest"] = {"appended": stats_c["appended"],
                           "word_plan": stats_c["word_plan"]}

    # (ii-b) tree untouched across the whole battery
    h1 = tree_hash()
    results["tree_hash_after"] = h1
    checks["ii_tree_untouched"] = bool(h0 == h1)

    # (vi) both timings present
    checks["vi_timings_reported"] = bool(
        stats_a["parse_seconds"] > 0 and stats_b["parse_seconds"] > 0)

    results["all_pass"] = all(checks.values())
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, sort_keys=True)
    print(json.dumps({"all_pass": results["all_pass"],
                      "expectations": checks,
                      "counts": results["counts"],
                      "timings": results["timings"]}, indent=1))
    return 0 if results["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
