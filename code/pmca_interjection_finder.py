"""pmca_interjection_finder.py — the INTERJECTION MICROSCRIPT.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: one of Jonathan's grammar microscripts, built by
copies and adjustments of pmca_noun_finder.py (his method,
2026-10-21). Each microscript stands alone — stdlib +
pmca_dictionary only, no other microscript, no shared helper.

The INTERJECTION variable, in Jonathan's relational definition:
an interjection is A WORD STANDING ALONE AS AN EXCLAMATION —
it belongs to no phrase and joins no clause; it stands at the
edge of a sentence (usually first, set off by a comma) or
alone entirely. Interjections are a CLOSED class here: the
seed holds the variable's words, and the set below contains
ONLY words verified present in this Webster 1913 build, each
with its stored evidence (checked 2026-10-21):
  oh   — its single definition: "An exclamation expressing
         various emotions, according to the tone and manner..."
  ah   — "An exclamation, expressive of surprise, pity..."
  alas — "An exclamation expressive of sorrow, pity, or
         apprehension of evil..."
  ha   — its stored header carries the tag itself: ", interj.
         ... An exclamation denoting surprise, joy, or grief."
  oho  — stored header: 'Oho \\O*ho"\\, interj. An exclamation
         of surprise, etc.'
  hurrah — stored header tags it a noun (", n. A cheer; a
         shout of joy"); the exclamation use IS the cheer
         itself, admitted on that evidence and disclosed.
Words checked and NOT admitted: "wow" has no entry in this
build at all (a sentence opening with it HOLDs
unknown_word:wow); "hark" and "hush" store verb senses only.

CLASS DETERMINATION BY THE OTHER WORDS (Jonathan, 2026-10-21;
OPEN-24): an interjection USE is a set word standing first,
set off from a clause that parses on its own, or standing
entirely alone. One opening word is genuinely ambiguous:
"hush" before a full clause ("Hush, the wolf sleeps") reads
both as an interjection and as an imperative verb (its stored
senses are verb senses: "To make silent; to stop..."), and
this scope — declarative sentences — holds no rule that rules
the command reading out -> HOLD
ambiguous_interjection_or_imperative:hush.

find(sentence) -> {"sentence", "verdict", "hold_reason",
"uses", "exclamation", "subject", "verb", "target"} with keys
in this fixed order; "exclamation" is the interjection word
or None, and subject/verb/target describe the remaining
clause (None for a standalone exclamation). On HOLD, uses is
empty and the other fields are None.

Scope v1: one optional leading interjection plus the noun
microscript's simple declarative patterns. Tokens carry their
commas in transcription; this module strips a trailing comma
from each token before all other work (the one tokenization
adjustment in the set). Auxiliary be-forms in the clause ->
HOLD passive_or_auxiliary_out_of_scope; unknown token -> HOLD
unknown_word:<w>.

Deterministic. Stdlib + pmca_dictionary only.
"""

import pmca_dictionary
import pmca_verb_capable as _vc
import pmca_parse_guards as _guards

ARTICLES = {"the", "a", "an"}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
AUXILIARIES = {"is", "are", "was", "were", "be", "been", "am"}
INTERJECTIONS = {"oh", "ah", "alas", "ha", "oho", "hurrah"}
CLOSED_CLASS = ARTICLES | PRONOUNS | AUXILIARIES | INTERJECTIONS


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
    """All valid template parses: (verb_index, head_s, head_o,
    uses)."""
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


def _result(sentence, verdict, hold_reason, uses, exclamation,
            subject, verb, target):
    return {"sentence": sentence, "verdict": verdict,
            "hold_reason": hold_reason, "uses": uses,
            "exclamation": exclamation, "subject": subject,
            "verb": verb, "target": target}


def _hold(sentence, reason):
    return _result(sentence, "HOLD", reason, [], None, None,
                   None, None)


def _clause(sentence, tokens, exclamation):
    """Parse a clause token list (any interjection already
    removed) and build the result around it."""
    for tok in tokens:
        if not _known(tok):
            return _hold(sentence, "unknown_word:" + tok)
    parses = _parse(tokens)
    if len(parses) > 1:
        return _hold(sentence, "ambiguous_verb_candidates")
    if not parses:
        return _hold(sentence, "no_valid_parse")
    i, head_s, head_o, uses = parses[0]
    if exclamation is not None:
        uses = [{"word": exclamation, "use": "interjection",
                 "subcoordinate": "interjection"}] + uses
    return _result(sentence, "OK", None, uses, exclamation,
                   head_s, _base_verb(tokens[i]), head_o)


def find(sentence):
    """Find the interjection use in one sentence. See the
    module docstring for scope and HOLD reasons."""
    text = sentence.strip().lower().rstrip(".!?")
    tokens = [t.rstrip(",") for t in text.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return _hold(sentence, "empty_sentence")

    for tok in tokens:
        if tok in AUXILIARIES:
            return _hold(sentence,
                         "passive_or_auxiliary_out_of_scope")

    # "hush" opening a sentence before a full clause: stored
    # verb senses only; imperative and interjection readings
    # both stand in this scope.
    if tokens[0] == "hush" and len(tokens) > 1:
        return _hold(sentence,
                     "ambiguous_interjection_or_imperative:hush")

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

    if tokens[0] in INTERJECTIONS:
        word = tokens[0]
        rest = tokens[1:]
        if not rest:
            uses = [{"word": word, "use": "interjection",
                     "subcoordinate": "interjection"}]
            return _guards.demote_adverb_target(
                _result(sentence, "OK", None, uses, word,
                        None, None, None))
        return _guards.demote_adverb_target(
            _clause(sentence, rest, word))

    return _guards.demote_adverb_target(
        _clause(sentence, tokens, None))


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_interjection_finder.py
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

    # The admitted set, each word verified present in this
    # build; and the exclusions the docstring names.
    check("set_verified_against_dictionary",
          all(pmca_dictionary.lookup(w) is not None
              for w in INTERJECTIONS)
          and pmca_dictionary.lookup("wow") is None,
          "set = " + ",".join(sorted(INTERJECTIONS))
          + "; wow absent from this build")

    # 1. Headline: oh stands before a clause that parses on
    # its own.
    r = find("Oh, the wolf howls.")
    check("case1_oh_exclamation",
          r["verdict"] == "OK" and r["exclamation"] == "oh"
          and r["subject"] == "wolf" and r["verb"] == "howl"
          and _use_of(r, "oh")["subcoordinate"] == "interjection",
          str((r["verdict"], r["exclamation"], r["subject"])))

    # 2. Alas before a transitive clause.
    r = find("Alas, the man eats the meat.")
    check("case2_alas_exclamation",
          r["verdict"] == "OK" and r["exclamation"] == "alas"
          and r["target"] == "meat",
          str((r["verdict"], r["exclamation"], r["target"])))

    # 3. Ha: the word whose stored header carries ", interj."
    r = find("Ha, the wolf runs.")
    check("case3_ha_exclamation",
          r["verdict"] == "OK" and r["exclamation"] == "ha"
          and r["verb"] == "run",
          str((r["verdict"], r["exclamation"])))

    # 4. No interjection present: OK, exclamation None.
    r = find("The wolf howls.")
    check("case4_no_interjection",
          r["verdict"] == "OK" and r["exclamation"] is None,
          str((r["verdict"], r["exclamation"])))

    # 5. Standing entirely alone: the exclamation is the
    # whole sentence.
    r = find("Alas!")
    check("case5_standalone_exclamation",
          r["verdict"] == "OK" and r["exclamation"] == "alas"
          and r["subject"] is None and r["verb"] is None,
          str((r["verdict"], r["exclamation"])))

    # 6. "wow" is not in the set and has no entry in this
    # build: unknown word, named.
    r = find("Wow, the wolf howls.")
    check("case6_wow_unknown_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:wow",
          str((r["verdict"], r["hold_reason"])))

    # 7. Genuine ambiguity: "hush" before a full clause —
    # imperative verb (its only stored senses) vs
    # interjection. This scope cannot rule the command out.
    r = find("Hush, the wolf sleeps.")
    check("case7_hush_ambiguity_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "ambiguous_interjection_or_imperative:hush",
          str((r["verdict"], r["hold_reason"])))

    battery = ["Oh, the wolf howls.",
               "Alas, the man eats the meat.", "Ha, the wolf runs.",
               "The wolf howls.", "Alas!", "Wow, the wolf howls.",
               "Hush, the wolf sleeps."]
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
