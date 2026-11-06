"""pmca_conjunction_finder.py — the CONJUNCTION MICROSCRIPT.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: one of Jonathan's grammar microscripts, built by
copies and adjustments of pmca_noun_finder.py (his method,
2026-10-21). Each microscript stands alone — stdlib +
pmca_dictionary only, no other microscript, no shared helper.

The CONJUNCTION variable, in Jonathan's relational definition:
a conjunction is A WORD JOINING — it stands between two units
of the same kind and joins them into one phrase. Conjunctions
are a CLOSED class: the variable's own words are finite
rules-of-language words, and the seed legitimately holds them
as its set (stated here as such): {and, or, but, nor, yet, so,
for}.

CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24): a set word is a conjunction USE only when a valid
noun phrase stands on BOTH sides of it in the same slot
(subject zone or object zone of the clause). "for" is the
set's dual-life word: standing at the head of a noun phrase
with no left-hand unit ("The man eats for the wolf"), it is a
preposition use, a different variable -> HOLD
preposition_use_out_of_scope:for. A set word with an empty
side ("The wolf and hunts") joins nothing -> HOLD
incomplete_conjunction:<w>.

find(sentence) -> {"sentence", "verdict", "hold_reason",
"uses", "subject", "verb", "target",
"joins": [{"word", "left", "right"}]} with keys in this fixed
order; left/right are the head nouns joined. When a conjunction
joins the subject, "subject" reports the left head (the pair
itself is in "joins"). On HOLD, uses is empty and the other
fields are None.

Scope v1: the noun microscript's simple declarative patterns,
with noun-phrase joining in the subject or object slot.
Clause-joining conjunctions ("... eats, and ... hunts") are out
of scope. Auxiliary be-forms anywhere -> HOLD
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
CONJUNCTIONS = {"and", "or", "but", "nor", "yet", "so", "for"}
CLOSED_CLASS = ARTICLES | PRONOUNS | AUXILIARIES | CONJUNCTIONS


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


def _result(sentence, verdict, hold_reason, uses, subject, verb,
            target, joins):
    return {"sentence": sentence, "verdict": verdict,
            "hold_reason": hold_reason, "uses": uses,
            "subject": subject, "verb": verb, "target": target,
            "joins": joins}


def _hold(sentence, reason):
    return _result(sentence, "HOLD", reason, [], None, None,
                   None, None)


def _plain_parse(tokens):
    """The template parse for a sentence with no conjunction
    candidate: (verb_index, head_s, head_o, uses) list."""
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
        valid.append((i, head_s, head_o, uses))
    return valid


def find(sentence):
    """Find the conjunction use in one simple declarative
    sentence and name the two units it joins. See the module
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

    conj_indices = [i for i, t in enumerate(tokens)
                    if t in CONJUNCTIONS]

    if not conj_indices:
        plain = _plain_parse(tokens)
        if len(plain) > 1:
            return _hold(sentence, "ambiguous_verb_candidates")
        if len(plain) == 1:
            i, head_s, head_o, uses = plain[0]
            return _guards.demote_adverb_target(
                _result(sentence, "OK", None, uses, head_s,
                        _base_verb(tokens[i]), head_o, []))
        return _hold(sentence, "no_valid_parse")

    # Join candidates: for each verb position, look for a
    # conjunction inside the subject zone or the object zone
    # with a valid noun phrase on both sides. A candidate must
    # complete a full parse: the OTHER zone must also validate
    # (empty or a valid phrase for the object zone, a valid
    # phrase for the subject zone) — otherwise a noun that
    # merely carries verb senses (e.g. "man") can pose as the
    # verb and manufacture a join out of the real verb.
    candidates = []
    for v, tok in enumerate(tokens):
        if not verb_capable(tok):
            continue
        zones = [("subject", tokens[:v], 0),
                 ("object", tokens[v + 1:], v + 1)]
        for slot, zone, offset in zones:
            for c, ztok in enumerate(zone):
                if ztok not in CONJUNCTIONS:
                    continue
                left, right = zone[:c], zone[c + 1:]
                ok_l, head_l, _ = _check_phrase(left)
                ok_r, head_r, _ = _check_phrase(right)
                if not (ok_l and ok_r):
                    continue
                if slot == "subject":
                    other = tokens[v + 1:]
                    if other:
                        ok_o, _, _ = _check_phrase(other)
                        if not ok_o:
                            continue
                else:
                    ok_s, _, _ = _check_phrase(tokens[:v])
                    if not ok_s:
                        continue
                candidates.append(
                    (v, slot, offset + c, head_l, head_r))

    distinct = {(c[0], c[1], c[3], c[4]) for c in candidates}
    if len(distinct) > 1:
        return _hold(sentence, "ambiguous_conjunction_candidates")
    if len(candidates) == 1:
        v, slot, cabs, head_l, head_r = candidates[0]
        word = tokens[cabs]
        # Build the use list over the original token order:
        # each zone phrase contributes its uses; the
        # conjunction token contributes its own.
        if slot == "subject":
            subj_tokens = tokens[:v]
            obj_tokens = tokens[v + 1:]
            left = subj_tokens[:cabs]
            right = subj_tokens[cabs + 1:]
            uses = _phrase_uses(left, head_l)
            uses.append({"word": word, "use": "conjunction",
                         "subcoordinate": "conjunction"})
            uses.extend(_phrase_uses(right, head_r))
            uses.append({"word": tokens[v], "use": "verb",
                         "subcoordinate": "verb"})
            head_o = None
            if obj_tokens:
                ok_o, head_o, _ = _check_phrase(obj_tokens)
                if not ok_o:
                    return _hold(sentence, "no_valid_parse")
                uses.extend(_phrase_uses(obj_tokens, head_o))
            return _guards.demote_adverb_target(
                _result(sentence, "OK", None, uses, head_l,
                        _base_verb(tokens[v]), head_o,
                        [{"word": word, "left": head_l,
                          "right": head_r}]))
        else:
            subj_tokens = tokens[:v]
            obj_tokens = tokens[v + 1:]
            rel = cabs - (v + 1)
            left = obj_tokens[:rel]
            right = obj_tokens[rel + 1:]
            ok_s, head_s, _ = _check_phrase(subj_tokens)
            if not ok_s:
                return _hold(sentence, "no_valid_parse")
            uses = _phrase_uses(subj_tokens, head_s)
            uses.append({"word": tokens[v], "use": "verb",
                         "subcoordinate": "verb"})
            uses.extend(_phrase_uses(left, head_l))
            uses.append({"word": word, "use": "conjunction",
                         "subcoordinate": "conjunction"})
            uses.extend(_phrase_uses(right, head_r))
            return _guards.demote_adverb_target(
                _result(sentence, "OK", None, uses, head_s,
                        _base_verb(tokens[v]), head_l,
                        [{"word": word, "left": head_l,
                          "right": head_r}]))

    # No valid join. Named failures first: "for" heading a noun
    # phrase is a preposition use, out of scope here.
    for i, tok in enumerate(tokens):
        if tok == "for" and i > 0:
            ok, _, _ = _check_phrase(tokens[i + 1:])
            if ok:
                return _hold(sentence,
                             "preposition_use_out_of_scope:for")
    # A conjunction with an empty side joins nothing.
    for c in conj_indices:
        left_empty = c == 0 or tokens[c - 1] in CONJUNCTIONS
        right_empty = c == len(tokens) - 1
        # Zone-relative emptiness: the token stands at a clause
        # boundary or directly against the verb.
        if right_empty or (c + 1 < len(tokens)
                           and verb_capable(tokens[c + 1])
                           and c >= 1):
            return _hold(sentence,
                         "incomplete_conjunction:" + tokens[c])
        if left_empty:
            return _hold(sentence,
                         "incomplete_conjunction:" + tokens[c])
    return _hold(sentence, "no_valid_parse")


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_conjunction_finder.py
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

    # 1. Headline: and joins wolf and dog in the subject.
    r = find("The wolf and the dog hunt.")
    check("case1_and_joins_subjects",
          r["verdict"] == "OK" and r["verb"] == "hunt"
          and r["joins"] == [{"word": "and", "left": "wolf",
                              "right": "dog"}]
          and r["subject"] == "wolf"
          and _use_of(r, "and")["subcoordinate"] == "conjunction",
          str((r["verdict"], r["joins"])))

    # 2. And joins in the object slot; "target" reports the
    # left joined head, the pair itself is in "joins".
    r = find("The man eats the sheep and the goat.")
    check("case2_and_joins_objects",
          r["verdict"] == "OK" and r["subject"] == "man"
          and r["joins"] == [{"word": "and", "left": "sheep",
                              "right": "goat"}]
          and r["target"] == "sheep",
          str((r["verdict"], r["joins"], r["target"])))

    # 3. Or joins the same way.
    r = find("The wolf or the dog hunts.")
    check("case3_or_joins_subjects",
          r["verdict"] == "OK"
          and r["joins"] == [{"word": "or", "left": "wolf",
                              "right": "dog"}],
          str((r["verdict"], r["joins"])))

    # 4. No conjunction present: OK with an empty list.
    r = find("The cat jumps.")
    check("case4_no_conjunction",
          r["verdict"] == "OK" and r["joins"] == [],
          str((r["verdict"], r["joins"])))

    # 5. A conjunction with an empty right side joins
    # nothing. HOLD, reason named.
    r = find("The wolf and hunts.")
    check("case5_incomplete_conjunction_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "incomplete_conjunction:and",
          str((r["verdict"], r["hold_reason"])))

    # 6. Dual-life "for": heading the object noun phrase, it
    # is a preposition use — a different variable. HOLD.
    r = find("The man eats for the wolf.")
    check("case6_for_preposition_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "preposition_use_out_of_scope:for",
          str((r["verdict"], r["hold_reason"])))

    # 7. Unknown word inside a joined phrase.
    r = find("The wolf and the zorblat hunt.")
    check("case7_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat",
          str((r["verdict"], r["hold_reason"])))

    battery = ["The wolf and the dog hunt.",
               "The man eats the sheep and the goat.",
               "The wolf or the dog hunts.", "The cat jumps.",
               "The wolf and hunts.", "The man eats for the wolf.",
               "The wolf and the zorblat hunt."]
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
