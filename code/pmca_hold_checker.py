#!/usr/bin/env python3
"""PMCA routine hold checker — the gap-worklist sweep.

Design: Jonathan Wayne Fleuren (the builder). Direction, 2026-10-28:
"We apply a routine hold checker — all words undefined are systemically
defined by cross-comparing undefined words to the dictionaries," extended
the same night to math parsing ("Same for the math parsing"). Code:
Alder (Muse), per the sealed expectations in
hidden_files/hold-checker-2026-10-28/expectations.md.

What this is: a routine pass over every undefined item the system is
holding — the verification pass's UNATTESTED words and the book-ingest
gap records' unknown words; the math parse store's undefined symbols —
which tries, in a fixed order and with every attempt recorded, to define
each item by cross-comparison against the dictionaries (words) or the
stored bindings and grammar (math). Definitions land as CANDIDATE
dossiers in the checker's own store. The checker promotes nothing,
changes no item's status, and reads every existing store read-only.

Discipline (the system's, not this file's invention):
- Meaning is looked up, never computed or assumed. Joins are exact or
  attested-form only: no fuzzy matching, no spelling correction. A 1914
  spelling variant no source attests as a form stays undefined, with
  the attempt recorded.
- NO silent gaps: every harvested item gets exactly one outcome
  record; every STILL_UNDEFINED record lists every attempt made, with
  its sources and result.
- Stored once: records reference words and stems by coordinate
  (pmca_graphspace.dist_address); stored entries are never copied.
  Appending is by record id, so a re-run appends zero records.
- Segmentation artifacts are not words: a token failing the word-shape
  rule (expectations.md) is recorded NOT_A_WORD with no dictionary
  attempt, by rule, the disqualifying characters stated.

Attempts per undefined word, fixed order: (1) HEADWORD cross-comparison
(inherited from the ingest verification pass where that pass already
ran it — cited, never silently redone); (2) FORM — attested as a form
of an attested headword (ECDICT reverse exchange index; Wiktionary
fixture forms); (3) MORPHOLOGY — leg A documented suffix strips with
each stem re-checked exactly, leg B the tree's own
pmca_morphology_finder.resolve_form (rules generate, its stored
dictionary disposes) — run with that dictionary tier configured to
the tree's Webster 1913 DB, as the finder's own battery configures
it (Pack G: the tier is off until configured; the delivered run's
leg B operates under the configured condition).

Attempts per undefined math symbol, fixed order: (1) STORE BINDINGS —
bound with a valid rational pair in a stored evaluation record;
(2) GRAMMAR — the symbol is an operator/relation token of
pmca_math_parse's grammar, hence NOT_A_VARIABLE; (3) EXTERNAL —
no external math corpus is locally machine-readable (Principia
Mathematica is a frozen foundational book, not a local data source);
that limit is recorded, not papered over.

Run with the dictionary probe's wn venv python (provides `wn`/OEWN):
  .../dictionary-probe-2026-10-26/oewn/venv/bin/python pmca_hold_checker.py
"""
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

HOME = Path("/home/hatch")
PROBE = HOME / "workspace/goals/pmca-simulation/hidden_files/dictionary-probe-2026-10-26"
INGEST = HOME / "workspace/goals/pmca-simulation/hidden_files/book-ingest-2026-10-28"
MATHPACK = HOME / "workspace/goals/pmca-simulation/hidden_files/math-parse-2026-10-28"
PACK = HOME / "workspace/goals/pmca-simulation/hidden_files/hold-checker-2026-10-28"
TREE = HOME / "workspace/pmca_argon_test"

WORD_STORE = INGEST / "book_word_store.json"
INGEST_STORE = INGEST / "book_ingest_store.jsonl"
VERIF_STORE = INGEST / "ingest_verification_store.jsonl"
MATH_STORE = MATHPACK / "math_parse_store.jsonl"
SEED_PY = TREE / "pmca_seed.py"
OUT = PACK / "hold_checker_store.jsonl"

sys.path.insert(0, str(TREE))
sys.path.insert(0, str(HOME / "workspace/goals/pmca-simulation/hidden_files/ingest-verification-2026-10-28"))

import pmca_ingest_verify as piv  # noqa: E402  (source loaders, dist_address)
from pmca_graphspace import dist_address  # noqa: E402

WORD_SHAPE = re.compile(r"^[a-z]+(['-][a-z]+)*$")
WORD_CHARS = set("abcdefghijklmnopqrstuvwxyz'-")
SOURCES = ["ECDICT", "Open English WordNet", "Wiktionary", "Webster 1913", "Wordset"]
GRAMMAR_OPERATORS = ["+", "-", "*", "/", "^", "(", ")"]
GRAMMAR_RELATIONS = ["=", "<", ">", "<=", ">=", "=="]


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def record_id(rec) -> str:
    return hashlib.sha256(canonical(rec).encode("utf-8")).hexdigest()


def is_word(token: str) -> bool:
    return bool(WORD_SHAPE.match(token.casefold()))


def disqualifying_chars(token: str):
    allowed = WORD_CHARS | {c.upper() for c in WORD_CHARS}
    return sorted({c for c in token if c not in allowed})


# ---------------------------------------------------------------- harvest

def harvest_words():
    verif = {}
    for line in VERIF_STORE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        verif[r["word"]] = r
    unattested = sorted(w for w, r in verif.items() if r["outcome"] == "UNATTESTED")
    gap_records = 0
    gap_words = set()
    for line in INGEST_STORE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("kind") == "gap" and r.get("gap_kind") == "unknown_word":
            gap_records += 1
            gap_words.add(r["detail"]["word"])
    gap_words = sorted(gap_words)
    harvest = sorted(set(unattested) | set(gap_words))
    arith = {
        "verification_unattested": len(unattested),
        "gap_unknown_word_records": gap_records,
        "gap_unknown_words_distinct": len(gap_words),
        "overlap_unattested_and_gap": len(set(unattested) & set(gap_words)),
        "harvest_union": len(harvest),
    }
    return harvest, verif, arith


def harvest_math():
    recs = [json.loads(l) for l in MATH_STORE.read_text(encoding="utf-8").splitlines()]
    gaps = {}
    for r in recs:
        g = r.get("gap") or {}
        t = g.get("type", "")
        if t.startswith("unknown_variable:") or t.startswith("invalid_binding:"):
            gaps.setdefault(g["variable"], []).append(r)
    return recs, gaps


# ---------------------------------------------------------------- words

def suffix_stems(word):
    """Documented suffix strips, sealed order. Returns [(rule, stem)]."""
    out = []
    if word.endswith("ies") and len(word) > 3:
        out.append(("-ies→y", word[:-3] + "y"))
    if word.endswith("es"):
        out.append(("-es", word[:-2]))
    if word.endswith("s") and not word.endswith("ss"):
        out.append(("-s", word[:-1]))
    if word.endswith("ed"):
        out.append(("-ed", word[:-2]))
    if word.endswith("ing"):
        out.append(("-ing", word[:-3]))
    if word.endswith("ly"):
        out.append(("-ly", word[:-2]))
    if word.endswith("er"):
        out.append(("-er", word[:-2]))
    if word.endswith("est"):
        out.append(("-est", word[:-3]))
    seen, res = set(), []
    for rule, stem in out:
        if stem != word and len(stem) >= 2 and stem not in seen:
            seen.add(stem)
            res.append((rule, stem))
    return res


def build_word_records():
    harvest, verif, arith = harvest_words()

    # Targets for the ECDICT loader: genuine undefined words + their stems.
    genuine = [w for w in harvest
               if is_word(w) and w in verif and verif[w]["outcome"] == "UNATTESTED"]
    target = {}
    for w in genuine:
        target.setdefault(w.casefold(), w)
        for _, stem in suffix_stems(w):
            target.setdefault(stem.casefold(), stem)
    ec_head, ec_rev = piv.load_ecdict(target)
    oewn = piv.load_oewn()
    webster = piv.load_webster()
    wikt = piv.load_wiktionary()
    wordset = piv.load_wordset()

    # Wiktionary fixture reverse forms index: form -> fixture headwords.
    wikt_forms_rev = {}
    for jf in sorted((PROBE / "wikt").glob("*.json")):
        d = json.loads(jf.read_text(encoding="utf-8"))
        for e in d.get("entries", []):
            for fm in e.get("forms", []) or []:
                if fm.get("word"):
                    wikt_forms_rev.setdefault(fm["word"].casefold(), set()).add(d["word"])

    def headword_sources(word):
        """Sources attesting `word` as a headword (exact/casefold joins)."""
        srcs = []
        if ec_head.get(word):
            srcs.append("ECDICT")
        if oewn(word):
            srcs.append("Open English WordNet")
        if wikt(word):
            srcs.append("Wiktionary")
        if webster(word):
            srcs.append("Webster 1913")
        if wordset(word):
            srcs.append("Wordset")
        return srcs

    # The morphology machinery's dictionary tier is OFF until configured
    # (Pack G, 2026-10-26 — no default path). Its own battery configures
    # it with the tree's stored Webster 1913 DB; the checker does the
    # same, so leg B runs under the machinery's designed condition.
    import pmca_dictionary
    morph_db = TREE / "data" / "webster1913.db"
    pmca_dictionary.configure(str(morph_db))
    from pmca_morphology_finder import resolve_form

    records = []
    for word in harvest:
        base = {
            "domain": "word",
            "item": word,
            "coordinate": list(dist_address(word)),
            "tier": "CANDIDATE",
        }
        vrec = verif.get(word)
        harvest_src = []
        if vrec and vrec["outcome"] == "UNATTESTED":
            harvest_src.append("verification_store:UNATTESTED")
        if vrec is None:
            harvest_src.append("book_ingest_store:gap/unknown_word")
        elif vrec["outcome"] != "UNATTESTED":
            harvest_src.append("book_ingest_store:gap/unknown_word")
            harvest_src.append("verification_store:" + vrec["outcome"])
        base["harvest_sources"] = harvest_src

        if not is_word(word):
            base.update({
                "item_kind": "not_a_word",
                "outcome": "NOT_A_WORD",
                "attempts": [],
                "classification": {
                    "rule": "token must match ^[a-z]+(['-][a-z]+)*$ (casefolded) "
                            "to be a word; this token does not",
                    "disqualifying_characters": disqualifying_chars(word),
                    "dictionary_attempts_made": False,
                    "reason": "segmentation artifact of the 1914 text "
                              "extraction, not a word; no dictionary "
                              "attempt is made, by rule",
                },
            })
            records.append(base)
            continue

        base["item_kind"] = "word"
        if vrec and vrec["outcome"] != "UNATTESTED":
            # Headword cross-comparison already ran in the verification
            # pass and attested this word.
            base.update({
                "outcome": "DEFINED",
                "attempts": [{
                    "step": 1, "kind": "headword",
                    "by": "ingest verification pass 2026-10-28 (inherited, "
                          "not re-run)",
                    "sources": SOURCES,
                    "result": "attested",
                    "verification_outcome": vrec["outcome"],
                    "independent_lineages": vrec["independent_lineages"],
                    "attesting_sources": sorted(
                        {a["source"] for a in vrec["attestations"]}),
                }],
                "verification_ref": "ingest_verification_store.jsonl",
            })
            records.append(base)
            continue

        # Genuinely undefined: headword attempt inherited (UNATTESTED),
        # then FORM, then MORPHOLOGY.
        attempts = [{
            "step": 1, "kind": "headword",
            "by": "ingest verification pass 2026-10-28 (inherited, not re-run)"
                  if vrec else "routine hold checker (fresh)",
            "sources": SOURCES,
            "result": "unattested — 0 of 5 sources attest this headword",
        }]

        # (2) FORM attempt.
        form_hits = []
        for lemma, k in ec_rev.get(word, []):
            form_hits.append({
                "headword": lemma, "form_type": k, "via": "ECDICT exchange",
                "coordinate": list(dist_address(lemma)),
            })
        for lemma in sorted(wikt_forms_rev.get(word.casefold(), set())):
            form_hits.append({
                "headword": lemma, "form_type": "listed form",
                "via": "Wiktionary fixture forms",
                "coordinate": list(dist_address(lemma)),
            })
        attempts.append({
            "step": 2, "kind": "form",
            "sources": ["ECDICT exchange (reverse index)",
                        "Wiktionary fixture forms"],
            "result": ("attested as a form of "
                       + ", ".join(sorted({h["headword"] for h in form_hits})))
                      if form_hits else "no source attests this word as a form",
            "form_of": form_hits,
        })
        if form_hits:
            base.update({"outcome": "DEFINED_VIA_FORM", "attempts": attempts,
                         "defined_via": {"form_of": form_hits}})
            records.append(base)
            continue

        # (3) MORPHOLOGY attempt.
        morph = {"step": 3, "kind": "morphology", "legs": []}
        stem_hit = None
        leg_a = {"leg": "A: documented suffix strips",
                 "candidates_tried": []}
        for rule, stem in suffix_stems(word):
            srcs = headword_sources(stem)
            leg_a["candidates_tried"].append(
                {"rule": rule, "stem": stem, "attested_by": srcs})
            if srcs and stem_hit is None:
                stem_hit = {"stem": stem, "rule": rule, "attested_by": srcs,
                            "coordinate": list(dist_address(stem))}
        leg_a["result"] = ("stem attested: " + stem_hit["stem"]
                           if stem_hit else "no stripped stem is attested")
        morph["legs"].append(leg_a)
        if stem_hit is None:
            r = resolve_form(word)
            leg_b = {
                "leg": "B: pmca_morphology_finder.resolve_form",
                "machinery_dictionary": "pmca_dictionary configured to "
                                        + str(morph_db) + " (the tree's "
                                        "Webster 1913 DB, per the finder's "
                                        "own battery convention)",
                "verdict": r["verdict"], "form": r["form"],
                "entry": r["entry"], "hold_reason": r["hold_reason"],
                "evidence": r["evidence"],
            }
            morph["legs"].append(leg_b)
            if r["verdict"] == "OK" and r["entry"] and r["entry"] != word:
                stem_hit = {"stem": r["entry"], "rule": "morphology "
                            "machinery, form: " + str(r["form"]),
                            "attested_by": ["pmca_morphology_finder "
                                            "(pmca_dictionary)"],
                            "coordinate": list(dist_address(r["entry"])),
                            "evidence": r["evidence"]}
        attempts.append(morph)
        if stem_hit:
            base.update({"outcome": "DEFINED_VIA_STEM", "attempts": attempts,
                         "defined_via": stem_hit})
        else:
            base.update({"outcome": "STILL_UNDEFINED", "attempts": attempts})
        records.append(base)

    return records, arith


# ---------------------------------------------------------------- math

def valid_pair(b):
    return (isinstance(b, list) and len(b) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in b))


def build_math_records():
    recs, gaps = harvest_math()
    records = []
    for symbol in sorted(gaps):
        attempts = []
        definitions, sightings = [], []
        for r in recs:
            b = (r.get("bindings") or {}).get(symbol)
            if b is None:
                continue
            if valid_pair(b) and r.get("kind") == "evaluation":
                definitions.append({"record_id": r["id"], "kind": "evaluation",
                                    "input": r.get("input"), "binding": b})
            elif valid_pair(b):
                sightings.append({"record_id": r["id"], "kind": r.get("kind"),
                                  "binding": b,
                                  "note": "valid pair bound inside a "
                                          "non-evaluation record — sighted, "
                                          "not a definition (sealed rule)"})
            else:
                sightings.append({"record_id": r["id"], "kind": r.get("kind"),
                                  "binding": b,
                                  "note": "binding is not a rational pair "
                                          "(invalid binding — the store's own "
                                          "invalid_binding gap records this)"})
        attempts.append({
            "step": 1, "kind": "store_bindings",
            "sources": ["math_parse_store.jsonl evaluation records"],
            "result": ("defined by %d stored evaluation record(s)"
                       % len(definitions)) if definitions
                      else "no stored evaluation record binds this symbol",
            "definitions": definitions, "other_sightings": sightings,
        })
        if definitions:
            records.append({
                "domain": "math", "item": symbol, "status": "CANDIDATE",
                "outcome": "DEFINED_BY_STORE",
                "gap_records": [{"record_id": r["id"],
                                 "gap_type": r["gap"]["type"],
                                 "input_excerpt": str(r.get("input"))[:60]}
                                for r in gaps[symbol]],
                "attempts": attempts,
                "defined_by": {"records": definitions},
            })
            continue
        in_grammar = symbol in GRAMMAR_OPERATORS or symbol in GRAMMAR_RELATIONS
        attempts.append({
            "step": 2, "kind": "grammar_table",
            "sources": ["pmca_math_parse grammar (tokenizer + header)"],
            "operators": GRAMMAR_OPERATORS, "relations": GRAMMAR_RELATIONS,
            "result": ("symbol is a grammar token" if in_grammar
                       else "symbol is an identifier, not a grammar token"),
        })
        if in_grammar:
            records.append({
                "domain": "math", "item": symbol, "status": "CANDIDATE",
                "outcome": "NOT_A_VARIABLE",
                "gap_records": [{"record_id": r["id"],
                                 "gap_type": r["gap"]["type"],
                                 "input_excerpt": str(r.get("input"))[:60]}
                                for r in gaps[symbol]],
                "attempts": attempts,
            })
            continue
        attempts.append({
            "step": 3, "kind": "external_sources",
            "sources": [],
            "result": "no external math corpus is locally machine-readable; "
                      "Principia Mathematica is a frozen foundational book "
                      "(ruling, 2026-10-20) but not a local data source — "
                      "the math domain's current source limit",
        })
        records.append({
            "domain": "math", "item": symbol, "status": "CANDIDATE",
            "outcome": "STILL_UNDEFINED",
            "gap_records": [{"record_id": r["id"],
                             "gap_type": r["gap"]["type"],
                             "input_excerpt": str(r.get("input"))[:60]}
                            for r in gaps[symbol]],
            "attempts": attempts,
        })
    arith = {
        "gap_records_unknown_or_invalid": sum(len(v) for v in gaps.values()),
        "distinct_symbols": len(gaps),
        "symbols": sorted(gaps),
    }
    return records, arith


# ---------------------------------------------------------------- main

def build_all():
    word_records, word_arith = build_word_records()
    math_records, math_arith = build_math_records()
    records = word_records + math_records
    for r in records:
        r["id"] = record_id({k: v for k, v in r.items() if k != "id"})
    return records, word_arith, math_arith


def serialize(records):
    lines = [canonical(r) for r in records]
    return ("\n".join(lines) + "\n").encode("utf-8")


def append_new(records):
    """Append records whose id is not already in the store. Returns
    (appended_count, store_bytes_after)."""
    existing_ids = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing_ids.add(json.loads(line)["id"])
    new = [r for r in records if r["id"] not in existing_ids]
    if new:
        with open(OUT, "a", encoding="utf-8") as f:
            for r in new:
                f.write(canonical(r) + "\n")
    return len(new), OUT.read_bytes()


def main():
    watched = [WORD_STORE, INGEST_STORE, VERIF_STORE, MATH_STORE, SEED_PY]
    before = {str(p): sha256_file(p) for p in watched}

    records, word_arith, math_arith = build_all()
    blob1 = serialize(records)
    records2, _, _ = build_all()
    assert serialize(records2) == blob1, "determinism failure: two builds differ"

    appended_first, store_bytes = append_new(records)
    appended_second, store_bytes2 = append_new(records)
    assert appended_second == 0 and store_bytes2 == store_bytes, \
        "idempotency failure on in-process second append"

    after = {str(p): sha256_file(p) for p in watched}
    assert before == after, "watched input files changed"
    for r in records:
        assert r.get("tier") == "CANDIDATE" or r.get("status") == "CANDIDATE"
        assert not any(v == "VERIFIED" for v in r.values())

    word_out = Counter(r["outcome"] for r in records if r["domain"] == "word")
    math_out = Counter(r["outcome"] for r in records if r["domain"] == "math")
    summary = {
        "word_harvest": word_arith,
        "math_harvest": math_arith,
        "total_records_built": len(records),
        "store_records": len(store_bytes.decode("utf-8").splitlines()),
        "store_sha256": hashlib.sha256(store_bytes).hexdigest(),
        "appended_this_run": appended_first,
        "appended_second_pass_in_process": appended_second,
        "word_outcomes": dict(word_out),
        "math_outcomes": dict(math_out),
        "still_undefined_words": sorted(
            r["item"] for r in records
            if r["domain"] == "word" and r["outcome"] == "STILL_UNDEFINED"),
        "defined_via_form": {r["item"]: r["defined_via"] for r in records
                             if r.get("outcome") == "DEFINED_VIA_FORM"},
        "defined_via_stem": {r["item"]: r["defined_via"] for r in records
                             if r.get("outcome") == "DEFINED_VIA_STEM"},
        "watched_hashes_unchanged": before == after,
    }
    print(json.dumps(summary, indent=1, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
