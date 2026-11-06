"""wg2_conformance — C-9 conformance battery (DICT-1 build WG2).

C-9 (Candidacy Rule Set, docket:279): "Conformance:
identical-input cross-implementation battery over the
1,538-word adjudication population plus combined-sentence
vectors; per-locus batteries remain necessary but are not
sufficient alone."

The adjudication population is RECOMPUTED here from the
stored dictionary, not taken on trust: every headword whose
verb_capable or noun_capable value differs between the
retired RAW test (unstripped "To " prefix) and the cleaned
decoding. The recomputation must yield exactly 1,538 words
(the measurement of record) or this battery fails at the
population step.

For every population word, EVERY verb_capable / noun_capable
consumer in the tree is exercised on the identical input:
  shared pmca_verb_capable, guards dict pair, all nine
  finders' pairs, the analyzer's guard wrapper, the
  dictionary's own lookup form, morphology's sense mix,
  dictionary_loop's and population's per-sense aggregates,
  and category_derive v5's noun test + verb-form-only test
  (checked against their shared-decoding complements).
Pass condition: zero disagreements with the shared module,
per case, published in wg2_conformance_cases.jsonl (one
JSON record per word, in this working directory).

Combined-sentence vectors: the wg2_combined_battery
sentences are run through the analyzer, the noun finder,
and the verb finder; the agreement rule is that every verb
a surface SELECTS (analyzer action's surface token; a
finder's verb use) is one the shared module attests as
verb-capable or the Part 2 resolver attests as a seed-verb
form — no surface may select a verb the shared evidence
positively excludes. Per-sentence records are appended to
the same cases file.

Run: python3 wg2_conformance.py  (exit 0 iff zero
disagreements and the population is exactly 1,538)
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(_HERE, "data", "webster1913.db")
CASES_PATH = os.path.join(_HERE, "wg2_conformance_cases.jsonl")

import pmca_dictionary

pmca_dictionary.configure(DB)


def adjudication_population():
    """The 1,538 words, recomputed: RAW vs CLEANED
    disagreement on verb_capable OR noun_capable."""
    import pmca_verb_capable as vc
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
    out = []
    for word, raw in con.execute("SELECT word, defs FROM defs"):
        defs = json.loads(raw)
        raw_v = any(d.startswith("To ") for d in defs)
        raw_n = any(not d.startswith("To ") for d in defs)
        cln_v = any(vc.is_verb_sense(d) for d in defs)
        cln_n = any(not vc.is_verb_sense(d) for d in defs)
        if raw_v != cln_v or raw_n != cln_n:
            out.append(word)
    con.close()
    return sorted(out)


def main():
    import pmca_adjective_finder as adj_f
    import pmca_adverb_finder as adv_f
    import pmca_article_finder as art_f
    import pmca_category_derive as derive
    import pmca_conjunction_finder as conj_f
    import pmca_dictionary_loop as dloop
    import pmca_interjection_finder as int_f
    import pmca_morphology_finder as morph
    import pmca_noun_finder as noun_f
    import pmca_parse_guards as guards
    import pmca_population as population
    import pmca_preposition_finder as prep_f
    import pmca_pronoun_finder as pron_f
    import pmca_sentence_analyzer as analyzer
    import pmca_verb_capable as vc
    import pmca_verb_finder as verb_f

    population_words = adjudication_population()
    print("adjudication population recomputed: %d words"
          % len(population_words))
    if len(population_words) != 1538:
        print("FAIL: population is not the 1,538 of record")
        return 1

    verb_consumers = {
        "guards.dict_verb_capable": guards.dict_verb_capable,
        "noun_finder.verb_capable": noun_f.verb_capable,
        "verb_finder.verb_capable": verb_f.verb_capable,
        "adjective_finder.verb_capable": adj_f.verb_capable,
        "adverb_finder.verb_capable": adv_f.verb_capable,
        "pronoun_finder.verb_capable": pron_f.verb_capable,
        "preposition_finder.verb_capable": prep_f.verb_capable,
        "conjunction_finder.verb_capable": conj_f.verb_capable,
        "article_finder.verb_capable": art_f.verb_capable,
        "interjection_finder.verb_capable": int_f.verb_capable,
        "analyzer._guard_verb_capable":
            analyzer._guard_verb_capable,
    }
    noun_consumers = {
        "guards.dict_noun_capable": guards.dict_noun_capable,
        "noun_finder.noun_capable": noun_f.noun_capable,
        "verb_finder.noun_capable": verb_f.noun_capable,
        "adjective_finder.noun_capable": adj_f.noun_capable,
        "adverb_finder.noun_capable": adv_f.noun_capable,
        "pronoun_finder.noun_capable": pron_f.noun_capable,
        "preposition_finder.noun_capable": prep_f.noun_capable,
        "conjunction_finder.noun_capable": conj_f.noun_capable,
        "article_finder.noun_capable": art_f.noun_capable,
        "interjection_finder.noun_capable": int_f.noun_capable,
    }

    disagreements = 0
    records = []
    for word in population_words:
        shared_v = vc.verb_capable(word)
        shared_n = vc.noun_capable(word)
        entry = pmca_dictionary.lookup(word)
        exact = entry if entry and entry["headword"] == word \
            else None
        got_v = {name: bool(fn(word))
                 for name, fn in verb_consumers.items()}
        got_n = {name: bool(fn(word))
                 for name, fn in noun_consumers.items()}
        got_v["dictionary.lookup_form"] = \
            (entry["form"] == "verb") if entry else False
        if exact is not None:
            has_v, has_n = morph._sense_mix(exact)
            got_v["morphology._sense_mix"] = bool(has_v)
            got_n["morphology._sense_mix"] = bool(has_n)
            got_v["dictionary_loop.aggregate"] = bool(any(
                dloop._is_verb_sense(d)
                for d in exact["definitions"]))
            got_n["dictionary_loop.aggregate"] = bool(any(
                not dloop._is_verb_sense(d)
                for d in exact["definitions"]))
            got_v["population.aggregate"] = bool(any(
                population._is_verb_sense(d)
                for d in exact["definitions"]))
            got_n["population.aggregate"] = bool(any(
                not population._is_verb_sense(d)
                for d in exact["definitions"]))
            # category_derive v5: its noun test is a noun
            # consumer; verb-form-only must equal "entry with
            # definitions and NO noun capability".
            got_n["category_derive._noun_capable"] = \
                bool(derive._noun_capable(word))
            got_v["category_derive.verb_form_only_complement"] = \
                bool(derive._verb_form_only(word)) == \
                bool(exact["definitions"] and not shared_n)
        bad_v = [k for k, v in got_v.items() if v != shared_v]
        bad_n = [k for k, v in got_n.items() if v != shared_n]
        agree = not bad_v and not bad_n
        if not agree:
            disagreements += 1
        records.append({
            "word": word, "shared": {"verb_capable": shared_v,
                                     "noun_capable": shared_n},
            "verb_consumers": got_v, "noun_consumers": got_n,
            "disagreeing_verb_consumers": bad_v,
            "disagreeing_noun_consumers": bad_n,
            "agree": agree})

    # --- Combined-sentence vectors --------------------------
    import wg2_combined_battery as combined
    sentence_records = []
    sent_disagreements = 0
    for sentence, status, _v, _h, _l, _a in combined.CASES:
        r = analyzer.analyze(sentence)
        action = r["parameters"]["action"]
        tokens = analyzer.clean(sentence).split()
        selected = []
        if action is not None:
            for tok in tokens:
                looked = pmca_dictionary.lookup(tok) or {}
                if analyzer._seed_verb_of(tok) == action \
                        or looked.get("headword") == action:
                    selected.append(tok)
                    break
            if not selected:
                selected.append(action)
        surfaces = {"analyzer": {"verdict": r["verdict"],
                                 "selected_verb": selected}}
        for name, mod in (("noun_finder", noun_f),
                          ("verb_finder", verb_f)):
            fr = mod.find(sentence)
            surfaces[name] = {
                "verdict": fr["verdict"],
                "hold_reason": fr["hold_reason"],
                "selected_verb": fr["verb"]}
        ok = True
        for tok in selected:
            if not (vc.verb_capable(tok)
                    or analyzer._seed_verb_of(tok) is not None):
                ok = False
        for name in ("noun_finder", "verb_finder"):
            fv = surfaces[name]["selected_verb"]
            if fv is not None and not (
                    vc.verb_capable(fv)
                    or analyzer._seed_verb_of(fv) is not None):
                ok = False
        if not ok:
            sent_disagreements += 1
        sentence_records.append({
            "sentence": sentence, "status_label": status,
            "surfaces": surfaces, "agree": ok})

    with open(CASES_PATH, "w") as f:
        for rec in records:
            f.write(json.dumps({"kind": "word", **rec},
                               sort_keys=True) + "\n")
        for rec in sentence_records:
            f.write(json.dumps({"kind": "sentence", **rec},
                               sort_keys=True) + "\n")

    print("word cases: %d, disagreements: %d"
          % (len(records), disagreements))
    print("sentence vectors: %d, disagreements: %d"
          % (len(sentence_records), sent_disagreements))
    print("per-case file: " + CASES_PATH)
    ok = disagreements == 0 and sent_disagreements == 0
    print("CONFORMANCE: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
