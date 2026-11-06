"""pmca_verb_finder.py — the VERB MICROSCRIPT for PMCA.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: one of Jonathan's grammar microscripts, built by
copies and adjustments of pmca_noun_finder.py (his method,
2026-10-21: they are all the same; copy the template, adjust for
the individual variable). Each microscript is one understood
grammatical variable, tiny, RAM-resident in the full system,
verifiable and replaceable ALONE — so this module imports stdlib
+ pmca_dictionary only, no other microscript, no shared helper.

The VERB variable, in Jonathan's relational definition: a verb
is AN ACTION. The seed holds the word "verb" and what a verb is;
this microscript holds the variable's working rule:

(1) VERB-FINDING BY DESCRIPTION: a word has a VERB subcoordinate
when its dictionary entry (pmca_dictionary.lookup, Webster 1913
tier) holds verb-form senses under the ONE shared
implementation (pmca_verb_capable, Candidacy Rule Set C-4;
WG2 2026-11-01 — the cleaned Webster-convention decoding at
CANDIDATE tier; the former RAW "a definition beginning "To ""
test is RETIRED as a mechanism, DICT-1 Part 1). A word has a noun
subcoordinate when the entry holds senses that are not verb-form.
RESOLVED DIVERGENCE from the noun microscript (Atlas T-1):
that module's seed-table oracle OR inside its verb_capable
is retired with the same build; both microscripts now read
the one shared oracle, and the divergence this paragraph
used to disclose no longer exists. A form this
dictionary cannot resolve (an irregular inflection stored under
no reachable headword) HOLDs unknown_word:<w> here rather than
consulting another module's tables.

(2) CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24, ruled 2026-10-14): the microscript classifies USES, not
spellings. A word both noun-capable and verb-capable is decided
purely by position: the verb is the single token between the
subject phrase and the object phrase that is verb-capable and
yields exactly one fully valid split. Zero verb candidates ->
HOLD no_verb_candidate; more than one valid split -> HOLD
ambiguous_verb_candidates. Once the use is determined, this
module reports the selected subcoordinate ("verb" / "noun");
selecting and reporting it is all this module does.

Scope v1: simple declarative sentences of the pattern
[subject noun phrase] [verb] [optional object noun phrase],
exactly as the noun microscript. Auxiliary be-forms
(is/are/was/were/be/been/am) anywhere -> HOLD
passive_or_auxiliary_out_of_scope. A token with no dictionary
entry and no closed-class membership -> HOLD unknown_word:<w>.

find(sentence) -> {"sentence", "verdict": "OK"|"HOLD",
"hold_reason": str|None, "uses": [{"word", "use",
"subcoordinate"}], "subject", "verb", "target"} with keys in
this fixed order; "verb" is the dictionary headword the verb
resolved to. On HOLD, uses is empty and subject/verb/target are
None: the module reports the failure, it does not guess.

Deterministic. Stdlib + pmca_dictionary only.
P0 shared screen (pmca_parse_guards): find() screens tokens via
_guards.screen after the AUX_BE scan (guards + genitive), and
applies genitive labels + the adverb lock to the OK result.
"""

import pmca_dictionary
import pmca_verb_capable as _vc
import pmca_parse_guards as _guards

ARTICLES = {"the", "a", "an"}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
AUXILIARIES = {"is", "are", "was", "were", "be", "been", "am"}
CLOSED_CLASS = ARTICLES | PRONOUNS | AUXILIARIES


def _entry(word):
    """The dictionary entry for a word (pmca_dictionary.lookup,
    which resolves suffix variants to a stored headword), or
    None."""
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
    """Known token: closed-class, or the dictionary resolves
    it."""
    if word in CLOSED_CLASS:
        return True
    return _entry(word) is not None


def _base_verb(word):
    """The verb's base form: the dictionary headword the word
    resolved to, else the word itself."""
    entry = _entry(word)
    if entry is not None:
        return entry["headword"]
    return word


def _check_phrase(tokens, possessor_positions=None):
    """Validate one noun phrase. Return (ok, head, failure) where
    failure is None, ("head", word) when the last token is not
    noun-capable, or ("adjective", word) when a preceding token
    is verb-capable. A single pronoun token is a valid subject
    phrase (head = the pronoun).
    possessor_positions: positions in the ORIGINAL phrase slice
    of possessor stems; a possessor skips the verb-capable test
    (its use is determined: possessor). Positions are translated
    below by the article-strip amount, against the stripped toks
    list this function actually validates."""
    if possessor_positions is None:
        possessor_positions = set()
    if len(tokens) == 1 and tokens[0] in PRONOUNS:
        return (True, tokens[0], None)
    toks = list(tokens)
    strip = 0
    if toks and toks[0] in ARTICLES:
        toks = toks[1:]
        strip = 1
    possessor_local = {p - strip for p in possessor_positions}
    if not toks:
        return (False, None, ("head", None))
    for tok in toks:
        if tok in ARTICLES or tok in PRONOUNS or tok in AUXILIARIES:
            return (False, None, ("head", tok))
    head = toks[-1]
    if not noun_capable(head):
        return (False, None, ("head", head))
    for idx, tok in enumerate(toks[:-1]):
        if idx in possessor_local:
            continue
        if verb_capable(tok):
            return (False, None, ("adjective", tok))
    return (True, head, None)


def _phrase_uses(tokens, head):
    """Use records for a validated phrase, in token order."""
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
            target):
    return {"sentence": sentence, "verdict": verdict,
            "hold_reason": hold_reason, "uses": uses,
            "subject": subject, "verb": verb, "target": target}


def _hold(sentence, reason):
    return _result(sentence, "HOLD", reason, [], None, None, None)


def find(sentence):
    """Classify the uses in one simple declarative sentence,
    reporting the verb and its target. See the module docstring
    for scope and HOLD reasons."""
    text = sentence.strip().lower().rstrip(".!?")
    tokens = text.split()
    if not tokens:
        return _hold(sentence, "empty_sentence")

    # Auxiliary be-forms are out of scope wherever they appear;
    # this fires before the unknown scan so a passive HOLDs as a
    # passive even when a later token is also unresolved.
    for tok in tokens:
        if tok in AUXILIARIES:
            return _hold(sentence, "passive_or_auxiliary_out_of_scope")

    scr = _guards.screen(tokens, noun_capable, verb_capable)
    if scr["hold"] is not None:
        return _hold(sentence, scr["hold"])
    if scr.get("expanded_aux_be"):
        # Pack A (Argon F4): a contraction expanded inside the
        # shared normalization inserted an AUX_BE token ("it's"
        # -> "it is") after this finder's raw-token auxiliary
        # scan had already run. The auxiliary scope rule applies
        # to the expanded stream exactly as to a spelled-out one.
        return _hold(sentence, "passive_or_auxiliary_out_of_scope")
    tokens = scr["tokens"]

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
        subj_poss = {g for g in scr["possessor_indices"] if g < i}
        ok_s, head_s, fail_s = _check_phrase(subj_tokens, subj_poss)
        if not ok_s:
            failures.append(fail_s)
            continue
        if obj_tokens:
            obj_poss = {g - (i + 1) for g in scr["possessor_indices"]
                        if g > i}
            ok_o, head_o, fail_o = _check_phrase(obj_tokens, obj_poss)
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
        for kind, word in failures:
            if kind == "adjective" and word is not None:
                return _hold(sentence,
                             "adjective_is_verb_capable:" + word)
        return _hold(sentence, "no_valid_parse")

    i, subj_tokens, head_s, obj_tokens, head_o = valid[0]
    uses = _phrase_uses(subj_tokens, head_s)
    uses.append({"word": tokens[i], "use": "verb",
                 "subcoordinate": "verb"})
    if obj_tokens:
        uses.extend(_phrase_uses(obj_tokens, head_o))
    result = _result(sentence, "OK", None, uses, head_s,
                     _base_verb(tokens[i]), head_o)
    result = _guards.apply_genitive_labels(result, scr["genitives"])
    result = _guards.demote_adverb_target(result)
    return result


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_verb_finder.py
# Every case is asserted against the real dictionary this tree
# carries; failures or dictionary gaps are asserted as the
# MEASURED outcome and labeled in the open.
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

    # 1. Headline: the verb and its target.
    r = find("The man eats the meat.")
    check("case1_man_eats_meat_ok",
          r["verdict"] == "OK" and r["subject"] == "man"
          and r["verb"] == "eat" and r["target"] == "meat"
          and _use_of(r, "eats")["subcoordinate"] == "verb",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    # 2. Headline HOLD, produced NATURALLY by the entry test:
    # the stored "wolf" entry carries five senses, all noun
    # senses — wolf has no verb subcoordinate in this data, so
    # no word in the sentence is verb-capable. No special-casing
    # of wolf anywhere in this module.
    r = find("They wolf the meat.")
    check("case2_wolf_verb_holds_naturally",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "no_verb_candidate"
          and not verb_capable("wolf"),
          str((r["verdict"], r["hold_reason"])))

    # 3. Intransitive headline verb from the noun build.
    r = find("The cat jumps.")
    check("case3_cat_jumps_ok",
          r["verdict"] == "OK" and r["verb"] == "jump"
          and r["target"] is None,
          str((r["verdict"], r["verb"], r["target"])))

    # 4/5. Mechanism pair with "duck", which carries both
    # subcoordinates in the stored text: position decides.
    r = find("The duck swims.")
    check("case4_duck_subject_swim_verb",
          r["verdict"] == "OK" and r["verb"] == "swim"
          and _use_of(r, "duck")["use"] == "noun",
          str((r["verdict"], r["subject"], r["verb"])))
    r = find("They duck the question.")
    check("case5_duck_verb_use",
          r["verdict"] == "OK" and r["verb"] == "duck"
          and _use_of(r, "duck")["subcoordinate"] == "verb"
          and r["target"] == "question",
          str((r["verdict"], r["verb"], r["target"])))

    # 6. Dog subject, bark verb.
    r = find("The dog barks.")
    check("case6_dog_barks_ok",
          r["verdict"] == "OK" and r["subject"] == "dog"
          and r["verb"] == "bark" and r["target"] is None,
          str((r["verdict"], r["subject"], r["verb"])))

    # 7. Passive: out of scope, reason named.
    r = find("The cup was broken by the man.")
    check("case7_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope",
          str((r["verdict"], r["hold_reason"])))

    # 8. Unknown word.
    r = find("The zorblat jumps.")
    check("case8_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat",
          str((r["verdict"], r["hold_reason"])))

    # 9. "The sheep hunt duck." AMENDED EXPECTATION (WG2,
    # 2026-11-01, under DICT-1 Part 1 / Candidacy Rule Set
    # C-4 — named, recorded, not silent; see the noun
    # finder's case 10 for the full record). The old HOLD
    # rested on the retired RAW test counting hunt's
    # apparatus-prefixed verb senses ("(Mach.) To...",
    # "(Change Ringing) To...") as noun senses. Under the
    # one shared verb_capable hunt is verb-capable only and
    # the sentence parses uniquely: sheep / hunt / duck.
    r = find("The sheep hunt duck.")
    check("case9_hunt_verb_only_shared_rule",
          r["verdict"] == "OK"
          and r["subject"] == "sheep" and r["verb"] == "hunt"
          and r["target"] == "duck",
          str((r["verdict"], r["hold_reason"], r["verb"])))

    battery = ["The man eats the meat.", "They wolf the meat.",
               "The cat jumps.", "The duck swims.",
               "They duck the question.", "The dog barks.",
               "The cup was broken by the man.",
               "The zorblat jumps.", "The sheep hunt duck."]
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
