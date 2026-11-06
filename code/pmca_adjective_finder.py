"""pmca_adjective_finder.py — the ADJECTIVE MICROSCRIPT for PMCA.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: one of Jonathan's grammar microscripts, built by
copies and adjustments of pmca_noun_finder.py (his method,
2026-10-21). Each microscript stands alone — stdlib +
pmca_dictionary only, no other microscript, no shared helper.

The ADJECTIVE variable, in Jonathan's relational definition: an
adjective is A DESCRIPTION OF A NOUN. The seed holds the word
"adjective" and what an adjective is; this microscript holds the
variable's working rule:

ENTRY EVIDENCE, MEASURED on this Webster 1913 build (and stated
plainly because it is weak): GCIDE stores NO adjective tag for
its own entry in the text this build carries — the ", a." strings
that appear (e.g. inside "red": "See under Red, a.") are
cross-references to other entries, not markers of the entry they
sit in; relying on them would be faking a marker. The one signal
the stored text does carry is the OPENING of the first
definition: adjective entries open with the quality itself
("Having largeness of size...", "Of or pertaining to the
forest...", "Furious; violent..."), while noun entries open with
a genus phrase beginning "A " / "An " / "The " / "Any " (after a
leading "(Zoöl.)"-style field label is stripped). Measured on a
labeled sample of 24 words: 13 of 14 adjectives open as qualities
(the miss is "red", whose stored entry is noun-first: "The color
of blood..."), and 8 of 10 nouns open with a genus phrase (the
two false positives are the mass nouns "stone" and "meat",
whose definitions open with the substance name and no article —
in attributive position this build cannot tell them from
adjectives, and says so). adjective_capable(word) is therefore:
an entry exists, and its first definition opens as a quality.
Capability is evidence, not the decision —

CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24): an adjective USE is a word standing in attributive
position — inside a noun phrase, before the phrase's head noun.
Position decides the use; the entry evidence decides whether the
word can be confirmed as an adjective at all. A modifier in
attributive position that is NOT adjective-capable under the
measured opening test is genuinely undecidable in this scope:
English also places bare nouns there (noun adjuncts: "sheep" in
"a sheep dog"), and this dictionary carries no marker that
separates the two readings -> HOLD
ambiguous_modifier_noun_adjunct:<w>. The "red" HOLD in the
battery is this same rule landing on a word that IS an adjective
in the language — a measured miss of the marker, asserted in
the open, not papered over.

find(sentence) -> {"sentence", "verdict", "hold_reason",
"uses", "subject", "verb", "target",
"describes": [{"word", "noun"}]} with keys in this fixed
order; "describes" lists each adjective use and the head noun
it describes. On HOLD, uses is empty and the other fields are
None.

Scope v1: the noun microscript's simple declarative patterns.
Auxiliary be-forms anywhere -> HOLD
passive_or_auxiliary_out_of_scope; unknown token -> HOLD
unknown_word:<w>. Predicative adjectives ("the wolf is big")
are out of scope and HOLD through the auxiliary rule.

Deterministic. Stdlib + pmca_dictionary only.
"""

import pmca_dictionary
import pmca_verb_capable as _vc
import pmca_parse_guards as _guards

ARTICLES = {"the", "a", "an"}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
AUXILIARIES = {"is", "are", "was", "were", "be", "been", "am"}
CLOSED_CLASS = ARTICLES | PRONOUNS | AUXILIARIES

# First-definition openings that mark a genus phrase, i.e. a
# noun opening, in the stored GCIDE text. (Dead copy: no code
# in this module reads it — adjective_capable delegates to
# pmca_parse_guards.modifier_capable. Its former "To "
# member was Variant D, RETIRED as a mechanism by DICT-1
# Part 1 (WG2 2026-11-01); the tuple is aligned to the shared
# layer's so the dead copy cannot mislead a reader.)
_NOMINAL_OPENINGS = ("A ", "An ", "The ", "Any ")


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


def _first_def_opening(word):
    """The first definition's opening text, with one leading
    parenthesized field label ("(Zoöl.)" and kin) stripped, or
    None when the word has no entry. (Pack A: the primitive
    lives in the shared layer, pmca_parse_guards; this is the
    microscript's own name for it.)"""
    return _guards._first_def_opening(word)


def adjective_capable(word):
    """Measured entry evidence for the adjective variable: an
    entry exists whose first definition opens as a quality,
    not as a genus phrase and not as a verb form. See the
    docstring for the measured reliability (13/14 adjectives,
    8/10 nouns on the labeled sample) and its named misses.
    (Pack A: the rule itself is the shared layer's, so every
    surface judges a modifier by the same evidence; this name
    is the microscript's. Fix wave "negative-evidence
    settles", 2026-11-02: the shared rule is the CAPABILITY
    claim, pmca_parse_guards.modifier_capable — it carried
    the name adjective_attested until that wave separated
    the claims. Adjective ATTESTATION is a different claim
    (pmca_parse_guards.adjective_attested, positive evidence
    only), and this microscript does not make it.)"""
    return _guards.modifier_capable(word)


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
    """Validate one noun phrase: optional article + modifiers +
    one head noun. Adjustment from the noun microscript:
    modifiers here may be verb-capable — in attributive
    position the slot, not the word's other subcoordinates,
    decides (their adjective evidence is judged after the
    parse, by adjective_capable)."""
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
    return (True, head, None)


def _modifiers(tokens, head):
    """The modifier tokens of a validated phrase (everything
    that is not the article and not the head)."""
    out = []
    for tok in tokens:
        if tok in ARTICLES or tok in PRONOUNS:
            continue
        if tok == head and tok is tokens[-1]:
            continue
        out.append(tok)
    return out


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
                         "subcoordinate": "adjective"})
    return uses


def _result(sentence, verdict, hold_reason, uses, subject, verb,
            target, describes):
    return {"sentence": sentence, "verdict": verdict,
            "hold_reason": hold_reason, "uses": uses,
            "subject": subject, "verb": verb, "target": target,
            "describes": describes}


def _hold(sentence, reason):
    return _result(sentence, "HOLD", reason, [], None, None,
                   None, None)


def find(sentence):
    """Find the adjective uses in one simple declarative
    sentence and name the noun each describes. See the module
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
        for kind, word in failures:
            if kind == "head" and word is not None:
                return _hold(sentence,
                             "noun_phrase_head_not_noun:" + word)
        return _hold(sentence, "no_valid_parse")

    i, subj_tokens, head_s, obj_tokens, head_o = valid[0]

    # Pack A alignment (claims-07 2.2 coda): a sentence-final
    # adverbial_ly token in the object span is a determined
    # adverb use — the demote geometry — not the phrase head.
    # When the token before it can serve as the head
    # (noun-capable, not adjective-capable), judge the phrase
    # on the span before the adverb and record the adverb use;
    # the shared screen's adverb guard has already HOLDed the
    # shapes where no token can serve ("runs big quickly").
    # Before this, the judgment below took the adverb as head
    # and HOLDed "eats the sheep quickly" on the "modifier"
    # sheep, contradicting every other finder.
    trailing_adverb = None
    if obj_tokens and len(obj_tokens) >= 2 \
            and _guards._adverbial_ly(obj_tokens[-1]) \
            and noun_capable(obj_tokens[-2]) \
            and not adjective_capable(obj_tokens[-2]):
        trailing_adverb = obj_tokens[-1]
        obj_tokens = obj_tokens[:-1]
        head_o = obj_tokens[-1]

    # The adjective variable's own judgment: every modifier in
    # attributive position must carry the measured entry
    # evidence, else its reading (adjective vs noun adjunct)
    # is undecidable in this scope.
    phrases = [(subj_tokens, head_s)]
    if obj_tokens:
        phrases.append((obj_tokens, head_o))
    describes = []
    for ptoks, head in phrases:
        for mod in _modifiers(ptoks, head):
            if not adjective_capable(mod):
                return _hold(
                    sentence,
                    "ambiguous_modifier_noun_adjunct:" + mod)
            describes.append({"word": mod, "noun": head})

    uses = _phrase_uses(subj_tokens, head_s)
    uses.append({"word": tokens[i], "use": "verb",
                 "subcoordinate": "verb"})
    if obj_tokens:
        uses.extend(_phrase_uses(obj_tokens, head_o))
    if trailing_adverb is not None:
        uses.append({"word": trailing_adverb, "use": "adverb",
                     "subcoordinate": None})
    return _guards.demote_adverb_target(
        _result(sentence, "OK", None, uses, head_s,
                _base_verb(tokens[i]), head_o, describes))


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_adjective_finder.py
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

    # The measured marker itself, on the docstring's sample.
    sample_adj = ["big", "savage", "good", "wild", "fierce",
                  "strong", "young", "old", "hungry", "gentle",
                  "common", "quick", "slow"]
    sample_noun = ["cat", "wolf", "dog", "cup", "sheep",
                   "forest", "goat", "man"]
    check("marker_sample_as_measured",
          all(adjective_capable(w) for w in sample_adj)
          and not any(adjective_capable(w) for w in sample_noun)
          and not adjective_capable("red")
          and adjective_capable("stone")
          and adjective_capable("meat"),
          "13/14 adj (red missed), 8/10 nouns (stone, meat "
          "false-positive)")

    # 1. Headline: big describes wolf.
    r = find("The big wolf runs.")
    check("case1_big_describes_wolf",
          r["verdict"] == "OK" and r["subject"] == "wolf"
          and r["verb"] == "run"
          and r["describes"] == [{"word": "big", "noun": "wolf"}]
          and _use_of(r, "big")["subcoordinate"] == "adjective",
          str((r["verdict"], r["describes"])))

    # 2. Headline: savage describes wolf (savage also carries
    # noun senses later in its entry; its first definition
    # opens as a quality, and position puts it in attributive
    # position — description of the subject, target untouched).
    r = find("The savage wolf eats the sheep.")
    check("case2_savage_describes_wolf",
          r["verdict"] == "OK"
          and r["describes"] == [{"word": "savage", "noun": "wolf"}]
          and r["target"] == "sheep",
          str((r["verdict"], r["describes"], r["target"])))

    # 3. Two phrases, two descriptions, same adjective word.
    r = find("The big wolf eats the big sheep.")
    check("case3_big_describes_both",
          r["verdict"] == "OK"
          and r["describes"] == [{"word": "big", "noun": "wolf"},
                                 {"word": "big", "noun": "sheep"}],
          str((r["verdict"], r["describes"])))

    # 4. No adjectives present: OK with an empty list — the
    # module does not invent descriptions.
    r = find("The cat jumps.")
    check("case4_no_adjectives",
          r["verdict"] == "OK" and r["describes"] == [],
          str((r["verdict"], r["describes"])))

    # 5. Genuine ambiguity: "sheep" in attributive position.
    # Its entry opens with a genus phrase ("Any one of several
    # species of ruminants...") — no adjective evidence — and
    # English licenses the noun-adjunct reading ("a sheep
    # dog"). No marker in this dictionary separates the two.
    r = find("The sheep goat barks.")
    check("case5_noun_adjunct_ambiguity_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "ambiguous_modifier_noun_adjunct:sheep",
          str((r["verdict"], r["hold_reason"])))

    # 6. MEASURED MARKER MISS, asserted in the open: "red" IS
    # an adjective in the language, but its stored entry is
    # noun-first ("The color of blood..."), so under the
    # measured entry test this module cannot confirm it and
    # HOLDs with the same reason as case 5. The miss is the
    # dictionary's ordering, not a rule invented for the case.
    r = find("The red wolf runs.")
    check("case6_red_measured_marker_miss_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "ambiguous_modifier_noun_adjunct:red"
          and not adjective_capable("red"),
          str((r["verdict"], r["hold_reason"])))

    # 7. Passive with an adjective present: the auxiliary rule
    # fires first — predicative/passive scope is not this
    # module's.
    r = find("The big cup was broken.")
    check("case7_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope",
          str((r["verdict"], r["hold_reason"])))

    # 8. Unknown word in modifier position.
    r = find("The zorblat wolf runs.")
    check("case8_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat",
          str((r["verdict"], r["hold_reason"])))

    battery = ["The big wolf runs.",
               "The savage wolf eats the sheep.",
               "The big wolf eats the big sheep.",
               "The cat jumps.", "The sheep goat barks.",
               "The red wolf runs.", "The big cup was broken.",
               "The zorblat wolf runs."]
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
