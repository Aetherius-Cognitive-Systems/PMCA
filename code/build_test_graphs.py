#!/usr/bin/env python3
"""PMCA paragraph distributor + edge-harm differential test.

Takes the 40-paragraph corpus (data/edge_harm_corpus.json) and, for each
paragraph, distributes it into a PMCA graph the way Jonathan specified:
sentences and words become entries; the harmful words are placed ON THE
EDGES — their harm values and categories ride Subcoord edges from the word
entry to a harm-category node, exactly per blueprint Section 5 ("the edges
ARE the subcoordinates"). The word entries themselves stay clean, except
the IntrinsicHarm slur tokens (the blueprint's word-level exception).

Two honest measurements, kept separate:

  1. EDGE MECHANISM — per-paragraph edge-harm totals from the distributor
     graphs, mapped to verdicts via documented placeholder thresholds.
     The edge values here are the HARNESS's annotations (the corpus's
     design intent), standing in for the edge values a deployment's
     seed/context would carry. This tests whether the GRAPH MECHANISM
     propagates edge harm to correct differential verdicts — it does NOT
     test value discovery. Who sets edge values in the world (the
     seed-curation problem) remains open.

  2. SIM CROSS-CHECK — Engine.admit() on the raw paragraph text (adult +
     child), using the sim's own dictionary seed/lexicon. This measures
     what the CURRENT sim does with its word-level lexicon — the honest
     baseline the edge thesis has to beat.

Run:  cd ~/workspace/pmca && python3 build_test_graphs.py
Writes: data/edge_harm_results.json
"""

import json
import re
import sys

sys.path.insert(0, ".")
from pmca import Engine
from pmca.model import Entry, Store, Subcoord

CORPUS = "data/edge_harm_corpus.json"
RESULTS = "data/edge_harm_results.json"
WORD = re.compile(r"[a-z']+")

CATEGORIES = ["Physical", "Emotional", "Psychological", "Exposure"]

# Placeholder verdict thresholds (blueprint: tunable, NOT training).
# Documented here so the mapping is auditable, not hidden.
TH_REJECT_TOTAL = 5      # deterministic-reaction placeholder: total >= 5


def distribute(par):
    """Build one Store per paragraph; harmful words ride the edges."""
    store = Store()
    cat_coord = {}
    for name in CATEGORIES:
        e = Entry(content="harm_category:%s" % name, truth_flags={"category"})
        cat_coord[name] = store.commit(e)

    word_coord = {}
    ann = {a["word"]: a for a in par.get("edge_annotations", [])}
    intrinsic_words = set()

    def word_entry(w):
        if w in word_coord:
            return word_coord[w]
        a = ann.get(w)
        e = Entry(content=w, intrinsic_harm=bool(a and a.get("intrinsic")))
        if e.intrinsic_harm:
            intrinsic_words.add(w)
        word_coord[w] = store.commit(e)
        return word_coord[w]

    total_harm, max_harm, n_edges = 0, 0, 0
    by_cat = {c: 0 for c in CATEGORIES}
    for i, sent in enumerate(par["sentences"]):
        se = Entry(content="sentence:%s:%d" % (par["id"], i))
        sc = store.commit(se)
        words = WORD.findall(sent.lower())
        prev = None
        for w in words:
            wc = word_entry(w)
            se.sigma.append(Subcoord(target=wc, direction="out",
                                     role="meaning", harm=0))
            n_edges += 1
            if prev is not None:
                pe = store.lookup(prev)
                pe.sigma.append(Subcoord(target=wc, direction="out",
                                         role="descriptor", harm=0))
                n_edges += 1
            prev = wc
            a = ann.get(w)
            if a and a.get("category"):
                h = a["harm"]
                we = store.lookup(wc)
                we.sigma.append(Subcoord(target=cat_coord[a["category"]],
                                         direction="out", role="meaning",
                                         harm=h))
                n_edges += 1
                total_harm += h
                max_harm = max(max_harm, h)
                by_cat[a["category"]] += h

    if intrinsic_words:
        edge_verdict = "REJECTED"
        reason = "IntrinsicHarm token(s): %s" % sorted(intrinsic_words)
    elif total_harm >= TH_REJECT_TOTAL:
        edge_verdict = "REJECTED"
        reason = "total edge harm %d >= %d" % (total_harm, TH_REJECT_TOTAL)
    elif total_harm >= 1:
        edge_verdict = "REDACTED"
        reason = "total edge harm %d in [1,%d)" % (total_harm, TH_REJECT_TOTAL)
    else:
        edge_verdict = "APPROVED"
        reason = "zero edge harm"

    return {
        "id": par["id"],
        "group": par["group"],
        "intent": par["intent"],
        "n_sentences": len(par["sentences"]),
        "n_words": sum(len(WORD.findall(s.lower())) for s in par["sentences"]),
        "n_edges": n_edges,
        "total_edge_harm": total_harm,
        "max_edge_harm": max_harm,
        "edge_harm_by_category": by_cat,
        "intrinsic_words": sorted(intrinsic_words),
        "edge_verdict": edge_verdict,
        "edge_reason": reason,
    }


def main():
    corpus = json.load(open(CORPUS))
    pars = corpus["paragraphs"]
    assert len(pars) == 40, "expected 40 paragraphs, got %d" % len(pars)

    rows = []
    for par in pars:
        m = distribute(par)
        text = " ".join(par["sentences"])
        # Fresh engine per paragraph AND per audience: isolated verdicts, no
        # cross-paragraph chaos-pool contamination and no adult->child
        # contamination within a paragraph (the sequential effect is measured
        # separately below as its own experiment).
        eng_a = Engine(dictionary_path="data/words_alpha.txt")
        va = eng_a.admit(text, audience="adult")
        eng_c = Engine(dictionary_path="data/words_alpha.txt")
        vc = eng_c.admit(text, audience="child")
        m["sim_adult"] = va.outcome
        m["sim_adult_reasons"] = va.reasons[:2]
        m["sim_child"] = vc.outcome
        rows.append(m)

    # ---- differential report ----
    def grp(g):
        return [r for r in rows if r["group"] == g]

    print("=" * 78)
    print("EDGE-HARM DIFFERENTIAL TEST — 40 paragraphs")
    print("=" * 78)
    print("%-5s %-7s %4s %5s %6s | %-9s | %-9s %-9s" %
          ("id", "group", "edge", "simA", "simC", "edge-ttl",
           "adult", "child"))
    for r in rows:
        print("%-5s %-7s %4d %5d %6d | %-9s | %-9s %-9s" % (
            r["id"], r["group"], r["total_edge_harm"], r["n_words"],
            r["n_edges"], r["edge_verdict"], r["sim_adult"], r["sim_child"]))

    print("\n--- group aggregates (edge mechanism) ---")
    for g in ["A", "B", "C", "D", "E", "clean"]:
        rs = grp(g)
        mean = sum(r["total_edge_harm"] for r in rs) / len(rs)
        ev = {}
        for r in rs:
            ev[r["edge_verdict"]] = ev.get(r["edge_verdict"], 0) + 1
        print("group %-5s n=%2d  mean edge-harm %6.1f  verdicts %s" %
              (g, len(rs), mean, ev))

    print("\n--- A vs B pairs: same profanity vocabulary, different relation ---")
    for a, b in [("H01", "H05"), ("H02", "H06"), ("H03", "H07"), ("H04", "H08")]:
        ra = next(r for r in rows if r["id"] == a)
        rb = next(r for r in rows if r["id"] == b)
        print("%s (emphasis): edge=%d/%s  sim=%s | %s (attack): edge=%d/%s  sim=%s" % (
            a, ra["total_edge_harm"], ra["edge_verdict"], ra["sim_adult"],
            b, rb["total_edge_harm"], rb["edge_verdict"], rb["sim_adult"]))

    print("\n--- sim cross-check: what the current lexicon does ---")
    for g in ["A", "B", "C", "D", "E", "clean"]:
        rs = grp(g)
        adult = {}
        for r in rs:
            adult[r["sim_adult"]] = adult.get(r["sim_adult"], 0) + 1
        print("group %-5s sim adult verdicts: %s" % (g, adult))
    clean_ok = sum(1 for r in grp("clean") if r["sim_adult"] == "APPROVED")
    emph_fp = sum(1 for r in grp("A") if r["sim_adult"] != "APPROVED")
    print("clean approval rate: %d/20 | emphasis-group flagged by sim: %d/4 "
          "(word-level lexicon cannot see emphasis)" % (clean_ok, emph_fp))

    # ---- sequential experiment: does the chaos-pool tripwire over-generalize?
    # One engine, harmful paragraphs first (they dismantle into the pool),
    # then the clean controls. Measures tripwire learning vs false trips.
    print("\n--- sequential run: tripwire learning / over-generalization ---")
    eng2 = Engine(dictionary_path="data/words_alpha.txt")
    seq = {}
    for par in pars:
        if par["group"] == "clean":
            continue
        v = eng2.admit(" ".join(par["sentences"]), audience="adult")
        seq[par["id"]] = v.outcome
    trips = 0
    for par in pars:
        if par["group"] != "clean":
            continue
        v = eng2.admit(" ".join(par["sentences"]), audience="adult")
        seq[par["id"]] = v.outcome
        if v.outcome == "SANDBOX_TRIPWIRE":
            trips += 1
            print("  clean %s tripped the wire after harmful dismantling" %
                  par["id"])
    print("clean paragraphs tripping the wire after harmful exposure: %d/20" %
          trips)

    json.dump({"rows": rows,
               "sequential_tripwire_hits": trips,
               "thresholds": {"reject_total": TH_REJECT_TOTAL},
               "note": "edge_verdict uses harness-annotated edge values "
                       "(design intent); sim_* uses the sim's own lexicon."},
              open(RESULTS, "w"), indent=1)
    print("\nresults ->", RESULTS)


if __name__ == "__main__":
    main()
