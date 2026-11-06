"""pmca_article_finder.py — the ARTICLE MICROSCRIPT for PMCA.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: one of Jonathan's grammar microscripts, built by
copies and adjustments of pmca_noun_finder.py (his method,
2026-10-21). Each microscript stands alone — stdlib +
pmca_dictionary only, no other microscript, no shared helper.

The ARTICLE variable, in Jonathan's relational definition: an
article is A WORD MARKING A NOUN AS KNOWN OR NEW — "the" marks
the noun as the known one, "a"/"an" as a new one. Articles are
a CLOSED class of three rules-of-language words, {the, a, an},
and the seed legitimately holds them as its set (stated here
as such).

CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24): an article USE is a set word standing at the head of
a noun phrase; it introduces that phrase's head noun. A noun
phrase with no article is simply a bare phrase — the module
reports the articles that are present and invents none. An
article with no noun after it is a failure with a named
reason, and one case is genuinely ambiguous: "a" standing
last ("The man writes a") — the storage itself gives "a"
nominal uses (its entry names it as a word: "(Mus.) The name
of the sixth tone in the model major scale..."), so the last
token reads both as an article missing its noun and as the
noun "a" itself standing as the object -> HOLD
ambiguous_article:a. "The" has no nominal use in storage, so
a trailing "the" is unambiguous: HOLD article_without_noun:the.

find(sentence) -> {"sentence", "verdict", "hold_reason",
"uses", "subject", "verb", "target",
"introduces": [{"word", "noun"}]} with keys in this fixed
order. On HOLD, uses is empty and the other fields are None.

Scope v1: the noun microscript's simple declarative patterns.
Auxiliary be-forms anywhere -> HOLD
passive_or_auxiliary_out_of_scope; unknown token -> HOLD
unknown_word:<w>.

Deterministic. Stdlib + pmca_dictionary only.
"""

import pmca_dictionary
import pmca_verb_capable as _vc
import pmca_parse_guards as _guards

ARTICLES = {"the", "a", "an"}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
AUXILIARIES = {"is", "are", "was", "were", "be", "been", "am"}
CLOSED_CLASS = ARTICLES | PRONOUNS | AUXILIARIES


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
                         "subcoordinate": "article"})
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


def _result(sentence, verdict, hold_reason, uses, subject, verb,
            target, introduces):
    return {"sentence": sentence, "verdict": verdict,
            "hold_reason": hold_reason, "uses": uses,
            "subject": subject, "verb": verb, "target": target,
            "introduces": introduces}


def _hold(sentence, reason):
    return _result(sentence, "HOLD", reason, [], None, None,
                   None, None)


def find(sentence):
    """Find the article uses in one simple declarative
    sentence and name the noun each introduces. See the
    module docstring for scope and HOLD reasons."""
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
                         guards=("genitive", "g5", "grammar", "g2", "adverb", "adjunct"))
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

    # A trailing article introduces nothing. "a" is genuinely
    # ambiguous there (see docstring); "the"/"an" are not.
    if tokens[-1] in ARTICLES:
        if tokens[-1] == "a":
            return _hold(sentence, "ambiguous_article:a")
        return _hold(sentence,
                     "article_without_noun:" + tokens[-1])

    verb_indices = [i for i, tok in enumerate(tokens)
                    if verb_capable(tok)]
    if not verb_indices:
        return _hold(sentence, "no_verb_candidate")

    valid = []
    failures = []
    for i in verb_indices:
        subj_tokens = tokens[:i]
        obj_tokens = tokens[i + 1:]
        if tokens[0] in PRONOUNS and i != 1:
            continue
        ok_s, head_s, fail_s = _check_phrase(subj_tokens)
        if not ok_s:
            failures.append(fail_s)
            continue
        if obj_tokens:
            ok_o, head_o, fail_o = _check_phrase(obj_tokens)
            if not ok_o:
                failures.append(fail_o)
                continue
        else:
            head_o = None
        valid.append((i, subj_tokens, head_s, obj_tokens, head_o))

    if len(valid) > 1:
        return _hold(sentence, "ambiguous_verb_candidates")
    if not valid:
        for kind, word in failures:
            if kind == "head" and word is not None:
                return _hold(sentence,
                             "noun_phrase_head_not_noun:" + word)
        return _hold(sentence, "no_valid_parse")

    i, subj_tokens, head_s, obj_tokens, head_o = valid[0]
    uses = _phrase_uses(subj_tokens, head_s)
    uses.append({"word": tokens[i], "use": "verb",
                 "subcoordinate": "verb"})
    if obj_tokens:
        uses.extend(_phrase_uses(obj_tokens, head_o))

    introduces = []
    for ptoks, head in [(subj_tokens, head_s),
                        (obj_tokens, head_o)]:
        if ptoks and ptoks[0] in ARTICLES:
            introduces.append({"word": ptoks[0], "noun": head})
    return _guards.demote_adverb_target(
        _result(sentence, "OK", None, uses, head_s,
                _base_verb(tokens[i]), head_o, introduces))


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_article_finder.py
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

    # 1. Headline: a introduces wolf (new), the introduces
    # sheep (known).
    r = find("A wolf eats the sheep.")
    check("case1_a_and_the_introduce",
          r["verdict"] == "OK"
          and r["introduces"] == [{"word": "a", "noun": "wolf"},
                                  {"word": "the",
                                   "noun": "sheep"}]
          and _use_of(r, "a")["subcoordinate"] == "article",
          str((r["verdict"], r["introduces"])))

    # 2. The ordinary case.
    r = find("The cat jumps.")
    check("case2_the_introduces_cat",
          r["verdict"] == "OK"
          and r["introduces"] == [{"word": "the", "noun": "cat"}],
          str((r["verdict"], r["introduces"])))

    # 3. An article before a phrase with a modifier introduces
    # the phrase's HEAD, not the modifier.
    r = find("The big wolf eats a sheep.")
    check("case3_article_introduces_head",
          r["verdict"] == "OK"
          and r["introduces"] == [{"word": "the", "noun": "wolf"},
                                  {"word": "a", "noun": "sheep"}],
          str((r["verdict"], r["introduces"])))

    # 4. Bare phrases: no articles present, none invented.
    r = find("Wolves hunt sheep.")
    check("case4_no_articles",
          r["verdict"] == "OK" and r["introduces"] == []
          and r["subject"] == "wolves" and r["target"] == "sheep",
          str((r["verdict"], r["introduces"])))

    # 5. A trailing "the" introduces nothing, and "the" has
    # no nominal reading in storage to compete with that.
    r = find("The man eats the.")
    check("case5_trailing_the_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "article_without_noun:the",
          str((r["verdict"], r["hold_reason"])))

    # 6. Genuine ambiguity: a trailing "a". Its own entry
    # gives it nominal uses (it names the word itself, e.g.
    # the musical tone), so the token reads both as an
    # article missing its noun and as the noun "a" standing
    # as the object of "writes".
    r = find("The man writes a.")
    check("case6_trailing_a_ambiguous",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "ambiguous_article:a",
          str((r["verdict"], r["hold_reason"])))

    # 7. Unknown word.
    r = find("The zorblat jumps.")
    check("case7_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat",
          str((r["verdict"], r["hold_reason"])))

    battery = ["A wolf eats the sheep.", "The cat jumps.",
               "The big wolf eats a sheep.", "Wolves hunt sheep.",
               "The man eats the.", "The man writes a.",
               "The zorblat jumps."]
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
