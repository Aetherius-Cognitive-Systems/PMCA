"""pmca_adverb_finder.py — the ADVERB MICROSCRIPT for PMCA.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: one of Jonathan's grammar microscripts, built by
copies and adjustments of pmca_noun_finder.py (his method,
2026-10-21). Each microscript stands alone — stdlib +
pmca_dictionary only, no other microscript, no shared helper.

The ADVERB variable, in Jonathan's relational definition: an
adverb is A DESCRIPTION OF AN ACTION. The seed holds the word
"adverb" and what an adverb is; this microscript holds the
variable's working rule:

ENTRY EVIDENCE, MEASURED on this Webster 1913 build. Unlike the
adjective variable, adverbs DO leave markers in the stored text,
three of them, each demonstrated on real entries:
  (M1) the entry-header tag: some entries store their own
       dictionary header as the first definition, e.g.
       quickly: 'Quickly \\Quick"ly\\, adv. Speedily; ...' —
       the ", adv." there names THIS entry (the same string
       inside other entries, e.g. "fierce" pointing at
       "{Fiercely}, adv.", is a cross-reference and is NOT
       used: the tag must follow the word's own headword at
       the start of its first definition);
  (M2) the manner phrase: a definition containing "In a ...
       manner" (slowly, swiftly, gently, softly, loudly,
       bravely — and "fast", whose defs 2-3 are genuine adverb
       senses: "In a fast or rapid manner; ... as, to run
       fast");
  (M3) -ly derivation: the word ends in "ly" and the stem
       resolves to a stored entry (gladly->glad, bravely->
       brave, fiercely->fierce). The weakest marker — morphology,
       not text evidence: a noun like "belly" (stem "bell")
       would pass it, and it is only ever consulted inside an
       adverb slot.
Measured union on a sample of 16 true adverbs: the markers
catch 10 (all nine -ly forms sampled, plus "fast"). The six
missed (often, now, then, well, daily, early) are non-derived
adverbs whose stored text carries no marker — "well" stores
five noun senses and no adverb sense at all in this build.
Where a word has no marker, this module does not call it an
adverb; position alone is not evidence here.

CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24): an adverb USE is a marker-evidenced word standing in
an adverb slot — sentence-final after a complete clause, or
immediately before the main verb. The clause is parsed by the
template rules with the adverb removed; the adverb then
modifies the verb. One competition is judged explicitly: if
the same word ALSO parses as the clause's object (or subject
head) with genuine noun evidence — a stored definition that is
neither verb-form, nor the adverb header, nor a manner phrase
— the two readings stand together and the module HOLDs
ambiguous_postverbal:<w>. That is "fast": adverb by manner
defs 2-3, noun by def 4 ("Abstinence from food..."), and no
rule in scope ranks them. When the competing noun reading
rests only on manner/header definitions themselves (quickly,
slowly), it is an artifact of the crude any-non-"To " noun
test and does not compete.

find(sentence) -> {"sentence", "verdict", "hold_reason",
"uses", "subject", "verb", "target",
"modifies": [{"word", "verb"}]} with keys in this fixed
order. On HOLD, uses is empty and the other fields are None.

Scope v1: the noun microscript's simple declarative patterns
plus at most one adverb. Auxiliary be-forms anywhere -> HOLD
passive_or_auxiliary_out_of_scope; unknown token -> HOLD
unknown_word:<w>. Post-verbal bare nouns are objects in this
scope (no microscript in the set carries transitivity).

Deterministic. Stdlib + pmca_dictionary only.
"""

import re

import pmca_dictionary
import pmca_verb_capable as _vc
import pmca_parse_guards as _guards

ARTICLES = {"the", "a", "an"}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
AUXILIARIES = {"is", "are", "was", "were", "be", "been", "am"}
CLOSED_CLASS = ARTICLES | PRONOUNS | AUXILIARIES

_MANNER = re.compile(r"\bIn an? [\w\s,'-]{0,40}? manner\b")


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


def _adv_header(word):
    """M1: the word's own entry header, stored as its first
    definition, carries the ", adv." tag for THIS headword."""
    entry = _entry(word)
    if entry is None or not entry["definitions"]:
        return False
    head = entry["headword"]
    pattern = re.escape(head) + r"\s+\\.*\\,\s*adv\."
    return re.match(pattern, entry["definitions"][0],
                    re.IGNORECASE) is not None


def adverb_capable(word):
    """Measured entry evidence for the adverb variable: M1
    header tag, M2 manner phrase, or M3 -ly derivation. See
    the docstring for the measured coverage and misses."""
    entry = _entry(word)
    if entry is None:
        return False
    if _adv_header(word):
        return True
    if any(_MANNER.search(d) for d in entry["definitions"]):
        return True
    if word.endswith("ly") and len(word) > 3:
        if _entry(word[:-2]) is not None:
            return True
    return False


def _strict_noun_evidence(word):
    """A genuine noun sense: a stored definition that is not
    verb-form, not the word's adverb header, and not a manner
    phrase. Used only to judge whether an object/subject
    reading of an adverb candidate truly competes."""
    entry = _entry(word)
    if entry is None:
        return False
    for idx, d in enumerate(entry["definitions"]):
        if _vc.is_verb_sense(d):
            continue
        if idx == 0 and _adv_header(word):
            continue
        if _MANNER.search(d):
            continue
        return True
    return False


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
    (verb_index, subj_tokens, head_s, obj_tokens, head_o)."""
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
        valid.append((i, subj_tokens, head_s, obj_tokens, head_o))
    return valid


def _first_failure(tokens):
    """The template's named phrase failure for the first verb
    candidate that produced one, else None."""
    for i, tok in enumerate(tokens):
        if not verb_capable(tok):
            continue
        ok_s, _, fail_s = _check_phrase(tokens[:i])
        if not ok_s:
            if fail_s[1] is not None:
                return fail_s
            continue
        if tokens[i + 1:]:
            ok_o, _, fail_o = _check_phrase(tokens[i + 1:])
            if not ok_o and fail_o[1] is not None:
                return fail_o
    return None


def _result(sentence, verdict, hold_reason, uses, subject, verb,
            target, modifies):
    return {"sentence": sentence, "verdict": verdict,
            "hold_reason": hold_reason, "uses": uses,
            "subject": subject, "verb": verb, "target": target,
            "modifies": modifies}


def _hold(sentence, reason):
    return _result(sentence, "HOLD", reason, [], None, None,
                   None, None)


def _build(sentence, tokens, parse, adverb):
    """Assemble an OK result from a parse of `tokens` (the
    adverb already removed, if any). `adverb` is None or
    (original_index, word): the adverb use is inserted at its
    original position in the use list."""
    i, subj_tokens, head_s, obj_tokens, head_o = parse
    uses = _phrase_uses(subj_tokens, head_s)
    uses.append({"word": tokens[i], "use": "verb",
                 "subcoordinate": "verb"})
    if obj_tokens:
        uses.extend(_phrase_uses(obj_tokens, head_o))
    modifies = []
    if adverb is not None:
        idx, word = adverb
        modifies = [{"word": word,
                     "verb": _base_verb(tokens[i])}]
        uses.insert(idx, {"word": word, "use": "adverb",
                          "subcoordinate": "adverb"})
    return _result(sentence, "OK", None, uses, head_s,
                   _base_verb(tokens[i]), head_o, modifies)


def find(sentence):
    """Find the adverb use in one simple declarative sentence
    and name the verb it modifies. See the module docstring
    for scope, markers, and HOLD reasons."""
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

    plain = _parse(tokens)

    # Adverb-slot removals: marker-evidenced words standing
    # sentence-finally or immediately before a verb-capable
    # token.
    removals = []
    for idx, tok in enumerate(tokens):
        if not adverb_capable(tok):
            continue
        slot_final = idx == len(tokens) - 1
        slot_preverb = (idx + 1 < len(tokens)
                        and verb_capable(tokens[idx + 1]))
        if slot_final or slot_preverb:
            rest = tokens[:idx] + tokens[idx + 1:]
            for parse in _parse(rest):
                removals.append((idx, tok, rest, parse))

    # Competition: a plain parse whose subject head or object
    # head IS a removed adverb word, backed by genuine noun
    # evidence, stands against the adverb reading.
    competing = []
    for idx, tok, rest, parse in removals:
        for p in plain:
            _, _, head_s, _, head_o = p
            if tok in (head_s, head_o) \
                    and _strict_noun_evidence(tok):
                competing.append(tok)
    if competing:
        return _hold(sentence,
                     "ambiguous_postverbal:" + competing[0])

    # Drop plain parses that merely re-read a marker-evidenced
    # adverb word as a phrase head on artifact evidence.
    removed_words = {tok for _, tok, _, _ in removals}
    plain = [p for p in plain
             if p[2] not in removed_words
             and p[4] not in removed_words]

    if len(removals) > 1:
        return _hold(sentence, "ambiguous_adverb_candidates")
    if len(removals) == 1:
        idx, tok, rest, parse = removals[0]
        return _guards.demote_adverb_target(
            _build(sentence, rest, parse, (idx, tok)))
    if len(plain) > 1:
        return _hold(sentence, "ambiguous_verb_candidates")
    if len(plain) == 1:
        return _guards.demote_adverb_target(
            _build(sentence, tokens, plain[0], None))
    failure = _first_failure(tokens)
    if failure is not None and failure[0] == "head":
        return _hold(sentence,
                     "noun_phrase_head_not_noun:" + failure[1])
    return _hold(sentence, "no_valid_parse")


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_adverb_finder.py
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

    # The measured markers themselves.
    check("markers_as_measured",
          adverb_capable("quickly") and adverb_capable("slowly")
          and adverb_capable("gladly") and adverb_capable("bravely")
          and adverb_capable("fiercely") and adverb_capable("fast")
          and not adverb_capable("well")
          and not adverb_capable("now")
          and not adverb_capable("often")
          and not adverb_capable("wolf"),
          "M1/M2/M3 catch 10/16 sampled adverbs; often/now/"
          "well carry no marker in this build")

    # 1. Headline: quickly modifies runs.
    r = find("The wolf runs quickly.")
    check("case1_quickly_modifies_runs",
          r["verdict"] == "OK" and r["verb"] == "run"
          and r["modifies"] == [{"word": "quickly", "verb": "run"}]
          and _use_of(r, "quickly")["subcoordinate"] == "adverb",
          str((r["verdict"], r["modifies"])))

    # 2. Headline: slowly modifies eats.
    r = find("The man eats slowly.")
    check("case2_slowly_modifies_eats",
          r["verdict"] == "OK" and r["verb"] == "eat"
          and r["target"] is None
          and r["modifies"] == [{"word": "slowly", "verb": "eat"}],
          str((r["verdict"], r["modifies"])))

    # 3. Pre-verb slot, as corrected by Pack A (2026-10-26;
    # claims-03 W1, synthesis D1): this case originally expected
    # OK — the adverb read off the slot while the subject was
    # taken positionally. But "quickly" carries a Webster noun
    # sense, so in the subject span its slot is genuinely
    # ambiguous (the same geometry in which "never" bound as
    # subject at every finder and promoted to VERIFIED in the
    # store, claims-03 W2). The shared adverb-slot guard HOLDs
    # it; the pre-verb adverb use is determined only when the
    # token carries no noun sense, which this one does.
    r = find("The wolf quickly runs.")
    check("case3_preverb_quickly",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "adverb_in_subject_slot:quickly",
          str((r["verdict"], r["hold_reason"])))

    # 4. No adverb present: OK with an empty list.
    r = find("The wolf runs.")
    check("case4_no_adverb",
          r["verdict"] == "OK" and r["modifies"] == []
          and r["verb"] == "run",
          str((r["verdict"], r["modifies"])))

    # 5. Genuine ambiguity, both readings from the module's own
    # measured evidence: "fast" is adverb-capable by its manner
    # definitions ("In a fast or rapid manner; ... as, to run
    # fast") AND carries a genuine noun sense (def 4:
    # "Abstinence from food; omission to take nourishment"),
    # so the object reading of the same slot truly competes
    # and no rule in scope ranks them.
    r = find("The wolf runs fast.")
    check("case5_fast_ambiguity_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "ambiguous_postverbal:fast"
          and adverb_capable("fast")
          and _strict_noun_evidence("fast"),
          str((r["verdict"], r["hold_reason"])))

    # 6. Unknown word in the adverb slot.
    r = find("The wolf runs zippily.")
    check("case6_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zippily",
          str((r["verdict"], r["hold_reason"])))

    # 7. Passive with an adverb present: the auxiliary rule
    # fires first.
    r = find("The cup was broken quickly.")
    check("case7_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope",
          str((r["verdict"], r["hold_reason"])))

    battery = ["The wolf runs quickly.", "The man eats slowly.",
               "The wolf quickly runs.", "The wolf runs.",
               "The wolf runs fast.", "The wolf runs zippily.",
               "The cup was broken quickly."]
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
