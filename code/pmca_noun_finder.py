"""pmca_noun_finder.py — the NOUN MICROSCRIPT for PMCA.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: the first of Jonathan's grammar microscripts
(2026-10-20/21). Each microscript is one understood grammatical
variable, tiny, RAM-resident in the full system, verifiable and
replaceable alone. This one holds the NOUN variable's working rule:

(1) NOUN-FINDING BY DESCRIPTION (Jonathan, 2026-10-20): a noun is
found by its description. A word's dictionary definitions are its
subcoordinates (pmca_dictionary.lookup, Webster 1913 tier); the
word has a NOUN subcoordinate when the dictionary holds senses for
it that are not verb-form, and a VERB subcoordinate when it holds
verb-form senses. Verb-form test: the ONE shared
implementation (pmca_verb_capable, Candidacy Rule Set C-4;
WG2 2026-11-01) — the cleaned Webster-convention decoding at
CANDIDATE tier. SUPERSEDED in this paragraph: the former
"a definition beginning "To "" RAW test and the analyzer's
own table resolution OR'd into this module's verb_capable
are both RETIRED as mechanisms (DICT-1 Part 1); seed-table
membership still names KNOWN words (_known) and the stored
VERB_FORMS table still resolves a found verb's base form
(_base_verb) as attested form data, but neither extends the
dictionary oracle any more.

(2) CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24, ruled 2026-10-14): the microscript classifies USES, not
spellings. A word that is both noun-capable and verb-capable is
decided purely by its position among the other words; the spelling
alone decides nothing. Once the use is determined, this module
reports the selected subcoordinate ("noun" / "verb"); the
identifiers inside that subcoordinate are what later processing
would use — selecting and reporting them is all this module does.

Scope v1: simple declarative sentences of the pattern
[subject noun phrase] [verb] [optional object noun phrase].
Noun phrase = optional article (the/a/an) + zero or more adjectives
+ one head noun; or a single pronoun (subject only). Pragmatic
phrase rules: the phrase's LAST token is the head and must be
noun-capable (else HOLD noun_phrase_head_not_noun:<w>); preceding
phrase tokens are adjectives and must NOT be verb-capable.
The verb is the single token between the phrases that is
verb-capable and yields exactly one fully valid split; zero verb
candidates -> HOLD no_verb_candidate; more than one valid split ->
HOLD ambiguous_verb_candidates. Auxiliary be-forms
(is/are/was/were/be/been/am) anywhere -> HOLD
passive_or_auxiliary_out_of_scope. A token with no dictionary
entry and no closed-class membership -> HOLD unknown_word:<w>.

find(sentence) -> {"sentence", "verdict": "OK"|"HOLD",
"hold_reason": str|None, "uses": [{"word", "use", "subcoordinate"}],
"subject", "verb", "target"} with keys in this fixed order.
On HOLD, uses is empty and subject/verb/target are None: the
module reports the failure, it does not guess a partial parse.

Deterministic. Stdlib + duplicate modules only. Lookup results
depend only on the dictionary bytes pmca_dictionary is configured
with (the battery configures this tree's data/webster1913.db).
P0 shared screen (pmca_parse_guards): find() screens tokens via
_guards.screen after the AUX_BE scan (guards + genitive), and
applies genitive labels + the adverb lock to the OK result.
"""

import pmca_dictionary
import pmca_verb_capable as _vc
import pmca_parse_guards as _guards
import pmca_sentence_analyzer as _analyzer

ARTICLES = {"the", "a", "an"}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
AUXILIARIES = {"is", "are", "was", "were", "be", "been", "am"}
CLOSED_CLASS = ARTICLES | PRONOUNS | AUXILIARIES

_VERB_FORMS = _analyzer.VERB_FORMS
_VERBS = _analyzer.VERBS


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
    one cleaned decoding, at CANDIDATE tier. RETIRED here
    (DICT-1 Part 1): this module's former RAW "To " copy AND
    its seed-table oracle OR (the analyzer's VERBS/VERB_FORMS
    consulted inside a dictionary oracle — Atlas T-1; C-5
    leaves the seed tables to their own consumers, and form
    identity to pmca_forms_resolver under Part 2)."""
    return _vc.verb_capable(word)


def _known(word):
    """Known token: closed-class, or the dictionary resolves it,
    or the analyzer's seed tables name it."""
    if word in CLOSED_CLASS:
        return True
    if _entry(word) is not None:
        return True
    return word in _VERBS or word in _VERB_FORMS


def _base_verb(word):
    """The verb's base form: VERB_FORMS resolution when it
    applies, else the dictionary headword the word resolved to,
    else the word itself."""
    if word in _VERB_FORMS and _VERB_FORMS[word] in _VERBS:
        return _VERB_FORMS[word]
    entry = _entry(word)
    if entry is not None:
        return entry["headword"]
    return word


def _check_phrase(tokens, possessor_positions=None):
    """Validate one noun phrase. Return (ok, head, failure) where
    failure is None, ("head", word) when the last token is not
    noun-capable, or ("adjective", word) when a preceding token is
    verb-capable. A single pronoun token is a valid subject
    phrase (head = the pronoun, kind "pronoun").
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
    """Classify the uses in one simple declarative sentence.
    See the module docstring for scope and HOLD reasons."""
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
# Check battery. Run: python3 pmca_noun_finder.py
# Every battery case from the build brief is asserted against the
# real dictionary this tree carries. Where the real dictionary
# cannot support a case as specified, the check asserts the
# MEASURED outcome and its detail says so in the open; the
# substitute mechanism proof is a separate, labeled check.
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

    # The dictionary tier: this tree's real Webster 1913 DB (the
    # path pmca_dictionary resolves by default), configured
    # through the analyzer's own configure entry point.
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    _analyzer.configure_dictionary(db)

    # 1. The live-demo HOLD sentence.
    r = find("The cat jumps.")
    check("case1_cat_jumps_ok",
          r["verdict"] == "OK" and r["subject"] == "cat"
          and r["verb"] == "jump" and r["target"] is None
          and _use_of(r, "cat")["subcoordinate"] == "noun"
          and _use_of(r, "jumps")["subcoordinate"] == "verb",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    # 2. Seed-vocabulary transitive.
    r = find("The man breaks the cup.")
    check("case2_man_breaks_cup_ok",
          r["verdict"] == "OK" and r["subject"] == "man"
          and r["verb"] == "break" and r["target"] == "cup",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    # 3. Wolf as noun (mechanism pair, first half).
    r = find("The wolf howls.")
    check("case3_wolf_noun_use",
          r["verdict"] == "OK" and r["subject"] == "wolf"
          and _use_of(r, "wolf")["use"] == "noun"
          and _use_of(r, "wolf")["subcoordinate"] == "noun"
          and r["verb"] == "howl",
          str((r["verdict"], r["subject"], r["verb"])))

    # 4. Wolf as verb, AS SPECIFIED in the brief ("They wolf the
    # pizza."): the brief expects OK with wolf in the verb
    # subcoordinate. The real dictionary cannot support it, for
    # two measured reasons: "pizza" has no entry in this Webster
    # 1913 build at all, and the stored "wolf" entry carries
    # five senses, ALL noun senses — the verb sense ("to eat
    # greedily") is not among the stored definitions, so wolf is
    # not verb-capable here. This check asserts the measured
    # outcome; the brief's expectation is recorded as unmet
    # against this dictionary, not papered over.
    r = find("They wolf the pizza.")
    check("case4_wolf_verb_AS_SPECIFIED_unmet_by_dictionary",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:pizza",
          "brief expects OK/wolf-as-verb; measured: "
          + str((r["verdict"], r["hold_reason"])))
    # 4b. Same sentence with an in-dictionary object (disclosed
    # diagnostic): isolates the second gap — with pizza gone,
    # the parse HOLDs no_verb_candidate because the stored wolf
    # entry holds no verb-form sense.
    r = find("They wolf the meat.")
    check("case4b_wolf_verb_gap_isolated",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "no_verb_candidate",
          str((r["verdict"], r["hold_reason"])))
    # 4c/4d. DISCLOSED SUBSTITUTE mechanism pair: "duck" is
    # noun-capable and verb-capable in the stored text, so the
    # position-decides mechanism the wolf pair was meant to prove
    # is proven with duck instead.
    r = find("The duck swims.")
    check("case4c_substitute_duck_noun_use",
          r["verdict"] == "OK" and r["subject"] == "duck"
          and _use_of(r, "duck")["subcoordinate"] == "noun",
          str((r["verdict"], r["subject"], r["verb"])))
    r = find("They duck the question.")
    check("case4d_substitute_duck_verb_use",
          r["verdict"] == "OK" and r["subject"] == "they"
          and _use_of(r, "duck")["use"] == "verb"
          and _use_of(r, "duck")["subcoordinate"] == "verb"
          and r["target"] == "question",
          str((r["verdict"], r["verb"], r["target"])))

    # 5. Adjective inside the subject phrase.
    r = find("The big wolf eats the sheep.")
    check("case5_big_wolf_eats_sheep_ok",
          r["verdict"] == "OK" and r["subject"] == "wolf"
          and _use_of(r, "big")["use"] == "adjective"
          and r["verb"] == "eat" and r["target"] == "sheep",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    # 6. Dog subject (data-gap word from the deep verification,
    # present in the dictionary as a noun).
    r = find("The dog barks.")
    check("case6_dog_barks_ok",
          r["verdict"] == "OK" and r["subject"] == "dog"
          and r["verb"] == "bark" and r["target"] is None,
          str((r["verdict"], r["subject"], r["verb"])))

    # 7. Plural subject: "wolves" is its own stored headword in
    # this build and resolves as a noun.
    r = find("The wolves hunt the sheep.")
    check("case7_wolves_hunt_sheep_ok",
          r["verdict"] == "OK" and r["subject"] == "wolves"
          and _use_of(r, "wolves")["subcoordinate"] == "noun"
          and r["verb"] == "hunt" and r["target"] == "sheep",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    # 8. Passive: out of scope, reason named.
    r = find("The cup was broken by the man.")
    check("case8_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope",
          str((r["verdict"], r["hold_reason"])))

    # 9. Unknown word.
    r = find("The zorblat jumps.")
    check("case9_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat",
          str((r["verdict"], r["hold_reason"])))

    # 10. "The sheep hunt duck." AMENDED EXPECTATION (WG2,
    # 2026-11-01, under DICT-1 Part 1 / Candidacy Rule Set
    # C-4 — the B-2 amendment pattern: named, recorded, not
    # silent). The old expectation (HOLD
    # ambiguous_verb_candidates) rested on the retired RAW
    # "To " test reading "hunt" as noun-capable: its only
    # non-"To " openings are apparatus-prefixed verb senses
    # ("(Mach.) To be in a state of instability...",
    # "(Change Ringing) To shift up and down..."), which the
    # RAW test counted as noun senses because they do not
    # literally begin "To ". Under the one shared
    # verb_capable (cleaned decoding, CANDIDATE evidence),
    # hunt is verb-capable ONLY, the second parse (verb
    # "duck") has no noun-capable head "hunt" to stand on,
    # and the sentence parses uniquely: sheep / hunt / duck.
    # "hunt" is in the 1,538-word adjudication population
    # for exactly this disagreement.
    r = find("The sheep hunt duck.")
    check("case10_hunt_verb_only_shared_rule",
          r["verdict"] == "OK"
          and r["subject"] == "sheep" and r["verb"] == "hunt"
          and r["target"] == "duck",
          str((r["verdict"], r["hold_reason"], r["verb"])))

    # Cross-checks against pmca_sentence_analyzer.analyze() with
    # the same dictionary configured: compare the words each
    # module identifies (the analyzer's parameters name cause /
    # action / target; its action is the surface table form,
    # bridged to the finder's base form through the dictionary
    # headword the action resolves to).
    def analyzer_words(sentence):
        a = _analyzer.analyze(sentence)
        p = a["parameters"]
        action = p["action"]
        if action is not None:
            entry = pmca_dictionary.lookup(action)
            if entry is not None:
                action = entry["headword"]
        return a, p["cause"], action, p["target"]

    a, cause, action, target = analyzer_words("The cat jumps.")
    f = find("The cat jumps.")
    check("xcheck1_cat_jumps_agrees",
          cause == f["subject"] == "cat"
          and action == f["verb"] == "jump"
          and target is None and f["target"] is None,
          "analyzer " + str((a["verdict"], cause, action, target))
          + " vs finder " + str((f["subject"], f["verb"],
                                 f["target"])))

    a, cause, action, target = analyzer_words("The man breaks the cup.")
    f = find("The man breaks the cup.")
    check("xcheck2_man_breaks_cup_agrees",
          cause == f["subject"] == "man"
          and action == f["verb"] == "break"
          and target == f["target"] == "cup",
          "analyzer " + str((a["verdict"], cause, action, target))
          + " vs finder " + str((f["subject"], f["verb"],
                                 f["target"])))

    # Case 5 cross-surface, as corrected by Pack A (2026-10-26;
    # synthesis divergence D2, Argon item 10 adjudicated): the
    # analyzer's clause parse originally named cause "big" —
    # its subject scan took the first dictionary-resolvable
    # token before the verb and had no phrase-head concept, and
    # this check asserted that divergence as the expected
    # state. The D2 fix gives parse_clause the phrase-head rule
    # (_np_head: a seed word inside the NP wins; among
    # dictionary resolutions the last candidate is the head),
    # so the analyzer now names the head "wolf", agreeing with
    # the finder. The analyzer still HOLDs
    # target_structure_missing:sheep (a seed-structure question,
    # outside this microscript's scope). Action and target
    # agree (eat / sheep).
    a, cause, action, target = analyzer_words(
        "The big wolf eats the sheep.")
    f = find("The big wolf eats the sheep.")
    check("xcheck3_case5_head_agreement_after_packA",
          a["verdict"] == "HOLD"
          and a["hold_reason"] == "target_structure_missing:sheep"
          and cause == "wolf" and f["subject"] == "wolf"
          and action == f["verb"] == "eat"
          and target == f["target"] == "sheep",
          "analyzer " + str((a["verdict"], a["hold_reason"], cause,
                             action, target))
          + " vs finder " + str((f["subject"], f["verb"],
                                 f["target"])))

    # Determinism inside the process: the whole battery re-run
    # must produce identical result objects.
    battery = ["The cat jumps.", "The man breaks the cup.",
               "The wolf howls.", "They wolf the pizza.",
               "They wolf the meat.", "The duck swims.",
               "They duck the question.",
               "The big wolf eats the sheep.", "The dog barks.",
               "The wolves hunt the sheep.",
               "The cup was broken by the man.",
               "The zorblat jumps.", "The sheep hunt duck."]
    dump1 = json.dumps([find(s) for s in battery], sort_keys=True)
    dump2 = json.dumps([find(s) for s in battery], sort_keys=True)
    check("determinism_two_runs_identical", dump1 == dump2, dump1[:60])

    passed = sum(1 for x in results if x)
    print("%d/%d checks passed" % (passed, len(results)))
    print("Determinism (two runs identical): %s" % (dump1 == dump2))
    return passed == len(results)


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_checks() else 1)
