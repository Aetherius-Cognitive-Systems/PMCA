"""pmca_pronoun_finder.py — the PRONOUN MICROSCRIPT for PMCA.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: one of Jonathan's grammar microscripts, built by
copies and adjustments of pmca_noun_finder.py (his method,
2026-10-21). Each microscript stands alone — stdlib +
pmca_dictionary only, no other microscript, no shared helper.

The PRONOUN variable, in Jonathan's relational definition: a
pronoun is A WORD STANDING IN PLACE OF A NOUN. Pronouns are a
CLOSED class: the variable's own words are finite
rules-of-language words, and the seed legitimately holds them
as its set (stated here as such, per the template rules):
subject forms {i, you, he, she, it, we, they} and object forms
{me, him, her, us, them} — "you" and "it" serve both roles.
Set membership does not require a dictionary entry: a measured
gap, asserted in the battery, is that "me" has NO entry in
this Webster 1913 build at all (lookup returns None); it is
admitted purely as a seed word, which is exactly what a closed
class is for.

CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24): a pronoun's ROLE is decided by position — before the
verb, standing in the subject phrase, it is the subject; after
the verb, standing as the object phrase, it is the object.
Dual-role words (you, it, her) are settled by the slot they
stand in, never by the spelling. One word is genuinely
ambiguous in this scope: "her" heading the object zone
followed by a noun phrase ("The man sees her sheep") admits an
object-pronoun reading, which strands the following phrase,
and a possessive reading ("her sheep"), which is a different
variable and out of this module's scope -> HOLD
ambiguous_pronoun:her. "Her" standing alone as the object
("The man sees her") is unambiguous and parses.

find(sentence) -> {"sentence", "verdict", "hold_reason",
"uses", "subject", "verb", "target",
"stands_for": [{"word", "role"}]} with keys in this fixed
order; role is "subject" or "object". On HOLD, uses is empty
and the other fields are None.

Scope v1: the noun microscript's simple declarative patterns.
Auxiliary be-forms anywhere -> HOLD
passive_or_auxiliary_out_of_scope; unknown token -> HOLD
unknown_word:<w>. Possessive pronouns (my, his, her-as-owner,
their) as phrase modifiers are out of scope.

Deterministic. Stdlib + pmca_dictionary only.
"""

import pmca_dictionary
import pmca_verb_capable as _vc
import pmca_parse_guards as _guards

ARTICLES = {"the", "a", "an"}
SUBJECT_PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
OBJECT_PRONOUNS = {"me", "you", "him", "her", "it", "us", "them"}
PRONOUNS = SUBJECT_PRONOUNS | OBJECT_PRONOUNS
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
    """As the noun microscript, with the pronoun set widened to
    this module's full closed class: a single pronoun token is
    a valid phrase in either slot."""
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
                         "subcoordinate": "pronoun"})
        elif tok == head and tok is tokens[-1]:
            uses.append({"word": tok, "use": "noun",
                         "subcoordinate": "noun"})
        else:
            uses.append({"word": tok, "use": "adjective",
                         "subcoordinate": None})
    return uses


def _result(sentence, verdict, hold_reason, uses, subject, verb,
            target, stands_for):
    return {"sentence": sentence, "verdict": verdict,
            "hold_reason": hold_reason, "uses": uses,
            "subject": subject, "verb": verb, "target": target,
            "stands_for": stands_for}


def _hold(sentence, reason):
    return _result(sentence, "HOLD", reason, [], None, None,
                   None, None)


def _her_ambiguity(tokens):
    """True when "her" begins the object zone of some verb
    candidate and is followed by a token that could continue a
    noun phrase (an article or a noun-capable word): the
    object-pronoun reading and the possessive reading both
    open, and position alone cannot close them."""
    for i, tok in enumerate(tokens):
        if not verb_capable(tok):
            continue
        if i + 2 < len(tokens) and tokens[i + 1] == "her":
            nxt = tokens[i + 2]
            if nxt in ARTICLES or noun_capable(nxt):
                return True
    return False


def find(sentence):
    """Find the pronoun uses in one simple declarative
    sentence and name the role each stands in. See the module
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
        if _her_ambiguity(tokens):
            return _hold(sentence, "ambiguous_pronoun:her")
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

    stands_for = []
    if len(subj_tokens) == 1 and subj_tokens[0] in PRONOUNS:
        stands_for.append({"word": subj_tokens[0],
                           "role": "subject"})
    if len(obj_tokens) == 1 and obj_tokens[0] in PRONOUNS:
        stands_for.append({"word": obj_tokens[0],
                           "role": "object"})
    return _guards.demote_adverb_target(
        _result(sentence, "OK", None, uses, head_s,
                _base_verb(tokens[i]), head_o, stands_for))


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_pronoun_finder.py
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

    # 1. Headline: they, standing in the subject.
    r = find("They hunt the sheep.")
    check("case1_they_subject",
          r["verdict"] == "OK" and r["subject"] == "they"
          and r["stands_for"] == [{"word": "they",
                                   "role": "subject"}]
          and _use_of(r, "they")["subcoordinate"] == "pronoun",
          str((r["verdict"], r["stands_for"])))

    # 2. Headline: them, standing in the object.
    r = find("The man sees them.")
    check("case2_them_object",
          r["verdict"] == "OK" and r["target"] == "them"
          and r["stands_for"] == [{"word": "them",
                                   "role": "object"}],
          str((r["verdict"], r["stands_for"])))

    # 3. She as subject of a transitive clause.
    r = find("She eats the meat.")
    check("case3_she_subject",
          r["verdict"] == "OK" and r["subject"] == "she"
          and r["stands_for"] == [{"word": "she",
                                   "role": "subject"}],
          str((r["verdict"], r["stands_for"])))

    # 4. Dual-role "it" settled by position: object here.
    r = find("The man sees it.")
    check("case4_it_object_by_position",
          r["verdict"] == "OK"
          and r["stands_for"] == [{"word": "it",
                                   "role": "object"}],
          str((r["verdict"], r["stands_for"])))

    # 5. MEASURED DICTIONARY GAP, asserted in the open: "me"
    # has no entry in this Webster 1913 build (lookup returns
    # None). The closed class admits it as a seed word, and
    # the object role is read from position alone.
    check("case5_me_absent_from_dictionary",
          pmca_dictionary.lookup("me") is None,
          "lookup('me') is None in this build")
    r = find("The wolf sees me.")
    check("case5b_me_object_as_seed_word",
          r["verdict"] == "OK"
          and r["stands_for"] == [{"word": "me",
                                   "role": "object"}],
          str((r["verdict"], r["stands_for"])))

    # 6. Genuine ambiguity: "her" before a noun phrase.
    # Object reading strands "sheep"; possessive reading is
    # another variable. Position cannot settle it here.
    r = find("The man sees her sheep.")
    check("case6_her_ambiguity_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "ambiguous_pronoun:her",
          str((r["verdict"], r["hold_reason"])))

    # 7. A pronoun subject does not rescue a passive: the
    # auxiliary rule fires first.
    r = find("It was broken.")
    check("case7_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope",
          str((r["verdict"], r["hold_reason"])))

    # 8. Unknown word elsewhere in the sentence.
    r = find("The zorblat sees them.")
    check("case8_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat",
          str((r["verdict"], r["hold_reason"])))

    battery = ["They hunt the sheep.", "The man sees them.",
               "She eats the meat.", "The man sees it.",
               "The wolf sees me.", "The man sees her sheep.",
               "It was broken.", "The zorblat sees them."]
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
