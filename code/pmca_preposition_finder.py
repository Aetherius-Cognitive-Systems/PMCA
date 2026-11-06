"""pmca_preposition_finder.py — the PREPOSITION MICROSCRIPT.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: one of Jonathan's grammar microscripts, built by
copies and adjustments of pmca_noun_finder.py (his method,
2026-10-21). Each microscript stands alone — stdlib +
pmca_dictionary only, no other microscript, no shared helper.

The PREPOSITION variable, in Jonathan's relational definition:
a preposition is A WORD PLACING A NOUN IN RELATION — it stands
before a noun phrase and relates that phrase's noun (its
OBJECT) to the rest of the sentence. Prepositions are a CLOSED
class: the variable's own words are finite rules-of-language
words, and the seed legitimately holds them as its set (stated
here as such): {of, in, on, at, by, for, with, to, from, over,
under, after, before, between, among, through, against, into,
upon, near, about}.

CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24): a set word is a preposition USE only when a noun
phrase follows it as its object. Two dual lives are settled by
what follows, and HOLD out of this module's scope:
  - "to" followed by a verb-capable word is the infinitive
    marker, not a preposition -> HOLD
    infinitive_marker_out_of_scope:to;
  - "before" / "after" / "since" (the set's dual-class words,
    preposition and conjunction both) followed by a noun
    phrase AND THEN a verb-capable word introduce a clause,
    a conjunction use -> HOLD conjunction_use_out_of_scope:<w>.
    Followed by a noun phrase alone, they are prepositions
    and parse ("The man eats before the wolf").
The phrase itself may stand after the clause ("sleeps in the
forest") or inside the subject ("The wolf of the forest
sleeps"): the module removes the preposition phrase span,
parses what remains by the template rules, and requires exactly
one (span, parse) success.

find(sentence) -> {"sentence", "verdict", "hold_reason",
"uses", "subject", "verb", "target",
"places": [{"word", "object"}]} with keys in this fixed
order. On HOLD, uses is empty and the other fields are None.

Scope v1: the noun microscript's simple declarative patterns
plus at most one prepositional phrase. Auxiliary be-forms
anywhere -> HOLD passive_or_auxiliary_out_of_scope (this is
also what keeps passive "by" out of this module); unknown
token -> HOLD unknown_word:<w>.

Deterministic. Stdlib + pmca_dictionary only.
"""

import pmca_dictionary
import pmca_verb_capable as _vc
import pmca_parse_guards as _guards

ARTICLES = {"the", "a", "an"}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
AUXILIARIES = {"is", "are", "was", "were", "be", "been", "am"}
PREPOSITIONS = {"of", "in", "on", "at", "by", "for", "with",
                "to", "from", "over", "under", "after",
                "before", "between", "among", "through",
                "against", "into", "upon", "near", "about"}
DUAL_CLASS = {"before", "after", "since"}
CLOSED_CLASS = ARTICLES | PRONOUNS | AUXILIARIES | PREPOSITIONS


def _entry(word):
    return pmca_dictionary.lookup(word)


def noun_capable(word):
    """The shared implementation (pmca_verb_capable, Candidacy
    Rule Set C-4): the word carries a noun candidate — a stored
    sense that is not a verb-form sense under the one cleaned
    decoding, at CANDIDATE tier. This module's former RAW
    "To " copy is RETIRED as a mechanism (DICT-1 Part 1)."""
    return _vc.noun_capable(word)


def verb_capable(word):
    """The shared implementation (pmca_verb_capable, Candidacy
    Rule Set C-4): the word carries a verb candidate under the
    one cleaned decoding, at CANDIDATE tier. This module's
    former RAW "To " copy is RETIRED as a mechanism (DICT-1
    Part 1)."""
    return _vc.verb_capable(word)


def _known(word):
    if word in CLOSED_CLASS:
        return True
    return _entry(word) is not None


def _base_verb(word):
    entry = _entry(word)
    if entry is not None:
        return entry["headword"]
    return word


def _check_phrase(tokens):
    if len(tokens) == 1 and tokens[0] in PRONOUNS:
        return (True, tokens[0], None)
    toks = list(tokens)
    if toks and toks[0] in ARTICLES:
        toks = toks[1:]
    if not toks:
        return (False, None, ("head", None))
    for tok in toks:
        if tok in ARTICLES or tok in PRONOUNS or tok in AUXILIARIES:
            return (False, None, ("head", tok))
    head = toks[-1]
    if not noun_capable(head):
        return (False, None, ("head", head))
    for tok in toks[:-1]:
        if verb_capable(tok):
            return (False, None, ("adjective", tok))
    return (True, head, None)


def _phrase_uses(tokens, head):
    uses = []
    for tok in tokens:
        if tok in ARTICLES:
            uses.append({"word": tok, "use": "article",
                         "subcoordinate": None})
        elif tok in PRONOUNS:
            uses.append({"word": tok, "use": "pronoun",
                         "subcoordinate": None})
        elif tok == head and tok is tokens[-1]:
            uses.append({"word": tok, "use": "noun",
                         "subcoordinate": "noun"})
        else:
            uses.append({"word": tok, "use": "adjective",
                         "subcoordinate": None})
    return uses


def _parse(tokens):
    """All valid template parses of a token list:
    (verb_index, subj_tokens, head_s, obj_tokens, head_o,
    uses) with uses in token order."""
    valid = []
    for i, tok in enumerate(tokens):
        if not verb_capable(tok):
            continue
        subj_tokens = tokens[:i]
        obj_tokens = tokens[i + 1:]
        if tokens and tokens[0] in PRONOUNS and i != 1:
            continue
        ok_s, head_s, _ = _check_phrase(subj_tokens)
        if not ok_s:
            continue
        if obj_tokens:
            ok_o, head_o, _ = _check_phrase(obj_tokens)
            if not ok_o:
                continue
        else:
            head_o = None
        uses = _phrase_uses(subj_tokens, head_s)
        uses.append({"word": tok, "use": "verb",
                     "subcoordinate": "verb"})
        if obj_tokens:
            uses.extend(_phrase_uses(obj_tokens, head_o))
        valid.append((i, subj_tokens, head_s, obj_tokens,
                      head_o, uses))
    return valid


def _result(sentence, verdict, hold_reason, uses, subject, verb,
            target, places):
    return {"sentence": sentence, "verdict": verdict,
            "hold_reason": hold_reason, "uses": uses,
            "subject": subject, "verb": verb, "target": target,
            "places": places}


def _hold(sentence, reason):
    return _result(sentence, "HOLD", reason, [], None, None,
                   None, None)


def find(sentence):
    """Find the preposition use in one simple declarative
    sentence and name the noun it places. See the module
    docstring for scope and HOLD reasons."""
    text = sentence.strip().lower().rstrip(".!?")
    tokens = text.split()
    if not tokens:
        return _hold(sentence, "empty_sentence")

    for tok in tokens:
        if tok in AUXILIARIES:
            return _hold(sentence,
                         "passive_or_auxiliary_out_of_scope")

    # P0 shared screen (pmca_parse_guards): guards + genitive scope.
    scr = _guards.screen(tokens, noun_capable, verb_capable,
                         guards=("genitive", "g5", "grammar", "adverb", "adjunct"))
    if scr["hold"] is not None:
        return _hold(sentence, scr["hold"])
    if scr.get("expanded_aux_be"):
        # Pack A (Argon F4): a contraction expanded inside the
        # shared normalization inserted an AUX_BE token ("it's"
        # -> "it is") after this finder's raw-token auxiliary
        # scan had already run. The auxiliary scope rule applies
        # to the expanded stream exactly as to a spelled-out one.
        return _hold(sentence, "passive_or_auxiliary_out_of_scope")
    if scr["genitives"]:
        return _hold(sentence, "genitive_out_of_scope")

    for tok in tokens:
        if not _known(tok):
            return _hold(sentence, "unknown_word:" + tok)

    # Dual-class words followed by [noun phrase][verb...]
    # introduce a clause: a conjunction use, out of scope.
    for p, tok in enumerate(tokens):
        if tok in DUAL_CLASS:
            if not any(verb_capable(t) for t in tokens[:p]):
                continue
            for ln in range(1, len(tokens) - p):
                ok, _, _ = _check_phrase(tokens[p + 1:p + 1 + ln])
                if ok:
                    rest = tokens[p + 1 + ln:]
                    if any(verb_capable(t) for t in rest):
                        return _hold(
                            sentence,
                            "conjunction_use_out_of_scope:" + tok)
                    break

    # "to" before a verb-capable word is the infinitive
    # marker, not a preposition.
    for p, tok in enumerate(tokens):
        if tok == "to" and p + 1 < len(tokens) \
                and verb_capable(tokens[p + 1]):
            return _hold(sentence,
                         "infinitive_marker_out_of_scope:to")

    prep_indices = [i for i, t in enumerate(tokens)
                    if t in PREPOSITIONS]

    if not prep_indices:
        plain = _parse(tokens)
        if len(plain) > 1:
            return _hold(sentence, "ambiguous_verb_candidates")
        if len(plain) == 1:
            i, _, head_s, _, head_o, uses = plain[0]
            return _guards.demote_adverb_target(
                _result(sentence, "OK", None, uses, head_s,
                        _base_verb(tokens[i]), head_o, []))
        return _hold(sentence, "no_valid_parse")

    successes = []
    for p in prep_indices:
        for ln in range(1, len(tokens) - p):
            span = tokens[p + 1:p + 1 + ln]
            ok, head, _ = _check_phrase(span)
            if not ok:
                continue
            rest = tokens[:p] + tokens[p + 1 + ln:]
            for parse in _parse(rest):
                successes.append((p, ln, head, rest, parse))

    distinct = {(p, head) for p, _, head, _, _ in successes}
    if len(distinct) > 1:
        return _hold(sentence, "ambiguous_preposition_phrase")
    if not successes:
        return _hold(sentence, "no_valid_parse")

    p, ln, head, rest, parse = successes[0]
    i, _, head_s, _, head_o, rest_uses = parse
    span = tokens[p + 1:p + 1 + ln]
    pp_uses = [{"word": tokens[p], "use": "preposition",
                "subcoordinate": "preposition"}]
    pp_uses.extend(_phrase_uses(span, head))
    uses = rest_uses[:p] + pp_uses + rest_uses[p:]
    places = [{"word": tokens[p], "object": head}]
    return _guards.demote_adverb_target(
        _result(sentence, "OK", None, uses, head_s,
                _base_verb(rest[i]), head_o, places))


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_preposition_finder.py
# ---------------------------------------------------------------------

def _use_of(result, word):
    for u in result["uses"]:
        if u["word"] == word:
            return u
    return None


def run_checks():
    import json
    import os

    results = []

    def check(name, cond, detail=""):
        results.append(cond)
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    pmca_dictionary.configure(db)

    # 1. Headline: in places forest.
    r = find("The wolf sleeps in the forest.")
    check("case1_in_places_forest",
          r["verdict"] == "OK" and r["verb"] == "sleep"
          and r["places"] == [{"word": "in", "object": "forest"}]
          and _use_of(r, "in")["subcoordinate"] == "preposition",
          str((r["verdict"], r["places"])))

    # 2. Headline: with places wolf.
    r = find("The man eats with the wolf.")
    check("case2_with_places_wolf",
          r["verdict"] == "OK"
          and r["places"] == [{"word": "with", "object": "wolf"}],
          str((r["verdict"], r["places"])))

    # 3. The phrase inside the subject: of places forest, and
    # the clause still parses around it.
    r = find("The wolf of the forest sleeps.")
    check("case3_of_inside_subject",
          r["verdict"] == "OK" and r["subject"] == "wolf"
          and r["places"] == [{"word": "of", "object": "forest"}],
          str((r["verdict"], r["subject"], r["places"])))

    # 4. A dual-class word with a noun phrase only: the
    # preposition reading stands.
    r = find("The man eats before the wolf.")
    check("case4_before_as_preposition",
          r["verdict"] == "OK"
          and r["places"] == [{"word": "before",
                               "object": "wolf"}],
          str((r["verdict"], r["places"])))

    # 5. No preposition present: OK with an empty list.
    r = find("The cat jumps.")
    check("case5_no_preposition",
          r["verdict"] == "OK" and r["places"] == [],
          str((r["verdict"], r["places"])))

    # 6. The same dual-class word before a clause: conjunction
    # use, out of this module's scope. HOLD.
    r = find("The man eats before the wolf hunts.")
    check("case6_before_conjunction_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "conjunction_use_out_of_scope:before",
          str((r["verdict"], r["hold_reason"])))

    # 7. "to" as infinitive marker: not a preposition use.
    r = find("The wolf wants to run.")
    check("case7_infinitive_to_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "infinitive_marker_out_of_scope:to",
          str((r["verdict"], r["hold_reason"])))

    # 8. Passive "by": the auxiliary rule fires before the
    # preposition logic ever sees it.
    r = find("The cup was broken by the man.")
    check("case8_passive_by_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope",
          str((r["verdict"], r["hold_reason"])))

    # 9. Unknown word inside the phrase.
    r = find("The wolf sleeps in the zorblat.")
    check("case9_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat",
          str((r["verdict"], r["hold_reason"])))

    battery = ["The wolf sleeps in the forest.",
               "The man eats with the wolf.",
               "The wolf of the forest sleeps.",
               "The man eats before the wolf.",
               "The cat jumps.",
               "The man eats before the wolf hunts.",
               "The wolf wants to run.",
               "The cup was broken by the man.",
               "The wolf sleeps in the zorblat."]
    dump1 = json.dumps([find(s) for s in battery], sort_keys=True)
    dump2 = json.dumps([find(s) for s in battery], sort_keys=True)
    check("determinism_two_runs_identical", dump1 == dump2,
          dump1[:60])

    passed = sum(1 for x in results if x)
    print("%d/%d checks passed" % (passed, len(results)))
    print("Determinism (two runs identical): %s" % (dump1 == dump2))
    return passed == len(results)


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_checks() else 1)
