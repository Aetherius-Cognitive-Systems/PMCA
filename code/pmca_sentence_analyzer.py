"""PMCA Sentence Analyzer - v1 prototype.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's architecture.

Rulings used here (Jonathan 2026-10-20):
- One sentence in. Out: the 7 parameters, a verdict, a trace.
  Parameters: Cause (Source Node), Action (Verb Edge), Target
  (Terminal Object), Consequence (Terminal Condition),
  Directness (Trace Length), Conscious Choice Gate (boolean),
  Infinity State Trigger (omega, boolean lock).
- Harm = target dependency weight x verb irreversibility
  coefficient. The coefficient is RESOLVED: the verb entry
  carries a table per replaceability class; the target entry
  carries its class; the cell where they meet is the
  coefficient. A missing cell HOLDs. Nothing is invented.
- Harm values are rational pairs (numerator, denominator),
  integers only, normalized by gcd, denominator positive.
  Comparisons by cross-multiplication.
- Totaling: per-edge boolean gate first (any edge at or above
  the gate limit refuses on its own), then cascade addition
  with decay. The gate verdict comes from boolean scans only;
  sorting is bookkeeping for the cascade, never the verdict.
- Conscious Choice Gate: true iff a foreknowledge marker is in
  the sentence. Markers: "in order to", "knowingly",
  "deliberately", "intentionally", "on purpose", "knowing".
- Omega locks, checked per clause after the target is known
  and before the coefficient lookup: (1) child target and the
  gate true (child_gate); (2) the chain's stored terminal
  consequence is the death of a person (death_of_person) —
  read from stored data only: the target's stored person flag
  and the verb entry's stored death_consequence field, never
  the verb's name (P3-3, ruled 2026-10-28). Both trigger
  checks live in pmca_harm_calculus, the shared calculus
  module (gate cutover, 2026-10-28).
- Missing Consequence never HOLDs by itself. Missing
  Consequence with high harm: closure False HOLDs (the waiting
  state, consequence_missing_high_harm); closure True locks
  to omega (the closing state, closure_unknown_high_harm).
  Low harm with a missing Consequence still approves.
- Missing lookups HOLD: unknown word, missing coefficient
  cell, no parseable clause. Nothing is filled in.

Seed values below are SEED STARTING VALUES, Jonathan's to adjust.

Deterministic. Stdlib only. Integers only. No statistics.
Same input + same state gives same output, always.

Graph architecture wiring (2026-10-20 rewrite, stage 4): the
analyzer carries an own GraphSpace (pmca_graphspace, system
"own"). Every analysis records its parameter summary there, at
the dist_address home of the sentence's first content word.
Own logic registered through register_logic (the stand-in for
the gated promotion path) can resolve a coefficient cell the
seed tables lack; the seed tables win first, a resolution from
the own space is traced with provenance 'own', and the
analyzer never registers logic from its own analyses. With an
empty own space, behavior is identical to the pre-wiring
module.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import weakref

import pmca_atomic_entries
import pmca_dictionary
import pmca_finder as _finder
import pmca_graphspace
import pmca_harm_calculus as _calculus
import pmca_forms_resolver as _resolver
import pmca_parse_guards as _guards
import pmca_persistence
import pmca_pools


# ---------------------------------------------------------------------
# Rational pairs and the harm calculus. A harm value is a tuple
# (numerator, denominator), denominator positive, divided down by
# the greatest common divisor; ordering is by cross-multiplication,
# integers only. The calculus itself — pair arithmetic, cascade
# totaling, the per-edge gate, the omega triggers, and the
# totaling verdict — lives in pmca_harm_calculus, the ONE shared
# implementation both verdict engines call (gate cutover,
# 2026-10-28). The names below are bound from that module so this
# module's own code and any importer of them are unchanged.
# ---------------------------------------------------------------------

GATE_LIMIT = _calculus.GATE_LIMIT
pair = _calculus.pair
pair_cmp = _calculus.pair_cmp
pair_ge = _calculus.pair_ge
pair_add = _calculus.pair_add
cascade_total = _calculus.cascade_total


# ---------------------------------------------------------------------
# Seed lexicon. SEED STARTING VALUES, Jonathan's to adjust.
# Nouns carry: dependency weight, replaceability class, flags.
# Verbs carry: a coefficient table per replaceability class, the
# result state left on the target (or None), whether the verb
# changes the target's state, and — where it holds — the stored
# fact that the verb's terminal consequence on a person is death
# (death_consequence, read by Trigger 2).
# ---------------------------------------------------------------------

NOUNS = {
    "cup": {"weight": 1, "replaceability": "replaceable", "flags": ()},
    "window": {"weight": 2, "replaceability": "repairable", "flags": ()},
    "spine": {"weight": 10, "replaceability": "non_replaceable", "flags": ()},
    "man": {"weight": 10, "replaceability": "non_replaceable",
            "flags": ("person",)},
    "person": {"weight": 10, "replaceability": "non_replaceable",
               "flags": ("person",)},
    "child": {"weight": 10, "replaceability": "non_replaceable",
              "flags": ("person", "child")},
    "hill": {"weight": 1, "replaceability": "replaceable", "flags": ()},
}

# Subject words that need no seed entry (part of speech only).
SUBJECT_ONLY = {"boulder"}

VERBS = {
    "breaks": {"coefficients": {"replaceable": 1, "non_replaceable": 10},
               "result_state": "broken", "change_of_state": True},
    "cracks": {"coefficients": {"repairable": 1},
               "result_state": "cracked", "change_of_state": True},
    # death_consequence is stored data, not a rule: this verb's
    # stored terminal consequence on a person is death, which is
    # what Trigger 2 reads (P3-3, ruled 2026-10-28 — the lock
    # fires on the stored consequence, never on the verb's name).
    "crushes": {"coefficients": {"non_replaceable": 10},
                "result_state": "crushed", "change_of_state": True,
                "death_consequence": True},
    "poisons": {"coefficients": {"non_replaceable": 8},
                "result_state": "poisoned", "change_of_state": True},
    "rolled": {"coefficients": {"replaceable": 0, "non_replaceable": 6},
               "result_state": None, "change_of_state": False},
    "took": {"coefficients": {"replaceable": 0},
             "result_state": None, "change_of_state": False},
    # Empty table on purpose: the omega lock is checked before
    # this table can HOLD anything (see test 4).
    "holds": {"coefficients": {},
              "result_state": None, "change_of_state": False},
}

# Surface forms that resolve to a table verb.
VERB_FORMS = {"crushed": "crushes"}

PRONOUNS = {"he", "she", "it", "they"}
ARTICLES = {"the", "a", "an"}
POSSESSIVES = {"his", "her"}
PREPOSITIONS = {"down", "toward", "at"}
CONJUNCTIONS = {"and"}

# Conscious Choice Gate markers (foreknowledge).
MARKER_PHRASES = ("in order to", "on purpose")
MARKER_WORDS = {"knowingly", "deliberately", "intentionally", "knowing"}
MARKER_TOKENS = {"in", "order", "to", "on", "purpose"}


def _known_tokens():
    words = set()
    words.update(NOUNS)
    words.update(SUBJECT_ONLY)
    words.update(VERBS)
    words.update(VERB_FORMS)
    words.update(PRONOUNS)
    words.update(ARTICLES)
    words.update(POSSESSIVES)
    words.update(PREPOSITIONS)
    words.update(CONJUNCTIONS)
    words.update(MARKER_WORDS)
    words.update(MARKER_TOKENS)
    return words


KNOWN = _known_tokens()
SUBJECT_WORDS = set(PRONOUNS) | set(NOUNS) | set(SUBJECT_ONLY)


# ---------------------------------------------------------------------
# Parsing, v1. Lowercase, strip sentence punctuation, split on
# whitespace. Clauses split on " and ". Per clause: the verb is
# the first token found in VERBS; the subject is the first
# pronoun or noun token before the verb, else the subject
# carried from the previous clause; the target is the first
# token after the verb found in NOUNS.
# ---------------------------------------------------------------------

def clean(sentence):
    """Lowercase, drop sentence punctuation, single-space."""
    text = sentence.lower()
    for ch in ".,!?":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _gate_in_prefix(tokens):
    """True iff a foreknowledge marker appears in the token
    list: a marker word, or a marker phrase as CONSECUTIVE
    tokens. Pack A: phrase matching is token-boundary — the
    old substring test armed the gate on "on purposeful"
    (claims-03 section 5; Argon's find)."""
    for tok in tokens:
        if tok in MARKER_WORDS:
            return True
    for a, b in zip(tokens, tokens[1:]):
        if (a, b) == ("on", "purpose"):
            return True
    for a, b, c in zip(tokens, tokens[1:], tokens[2:]):
        if (a, b, c) == ("in", "order", "to"):
            return True
    return False


def detect_gate(cleaned):
    """True iff a foreknowledge marker appears in the sentence."""
    return _gate_in_prefix(cleaned.split())


def _clause_gate(clause_tokens, lexicon):
    """Pack A (claims-03 W6): the gate is armed PER CLAUSE. A
    marker arms the gate only for the clause whose verb it
    precedes — BEFORE, the ruled axiom's own order. A marker in
    another clause, or after this clause's verb, does not arm
    it: before this fix the sentence-global scan fired the
    child_gate lock on a spine clause because "knowingly"
    modified a dog's barking in the clause before it. This is
    clause-scoping hardening of the marker test, NOT the ruled
    recursive intent loop (a P3-depth build, still open)."""
    verb_index = None
    for i, tok in enumerate(clause_tokens):
        if _seed_verb_of(tok) is not None:
            verb_index = i
            break
    if verb_index is None and lexicon:
        for i, tok in enumerate(clause_tokens):
            res = lexicon.get(tok)
            if res is not None and res["form"] == "verb":
                verb_index = i
                break
    if verb_index is None:
        return False
    return _gate_in_prefix(clause_tokens[:verb_index])


_NON_NOMINAL = (ARTICLES | PRONOUNS | POSSESSIVES | PREPOSITIONS
                | CONJUNCTIONS | set(_guards.PREPOSITIONS)
                | MARKER_WORDS | MARKER_TOKENS)


def _np_head(span, seed_words, lexicon):
    """The head of a noun-phrase span (Pack A, divergence D2).

    A seed-table word inside the NP wins over dictionary
    resolutions: the old first-hit scan bound the MODIFIER in
    "poisons the young child" — "young" resolves in the
    dictionary with form "other" and precedes the seed noun, so
    the target became "young", the analysis HOLDed on its
    missing structure, and the child gate was never evaluated
    ("crushes the old man" likewise defused the death trigger).
    Among dictionary resolutions alone, the LAST candidate is
    the head (modifiers precede the head: "the big wolf" ->
    wolf at both surfaces now). Dictionary candidates exclude
    non-nominal tokens — prepositions above all: a preposition
    never binds as subject or target — and adverb-attested
    tokens. The scan leaves the NP at a preposition once any
    candidate has been seen (a following PP is a different
    phrase); before any candidate, a preposition's own object
    may still be the head ("rolled toward the child")."""
    best = None
    dict_hits = []
    for tok in span:
        if tok in _guards.AUX_ALL or tok == "not":
            # The verb group begins: auxiliaries and negation
            # trapped before the verb the scan selected ("The
            # wolf does not eat..." — the seed verb is "eat")
            # are not noun-phrase material.
            break
        if tok in _guards.PREPOSITIONS or tok in PREPOSITIONS:
            if best is not None or dict_hits:
                break
            continue
        if tok in seed_words:
            if best is None:
                best = tok
            continue
        if tok in _NON_NOMINAL:
            continue
        res = lexicon.get(tok) if lexicon else None
        if res is not None and res["form"] == "other" \
                and not _guards.adverb_attested(tok):
            dict_hits.append(tok)
    if best is not None:
        return best
    return dict_hits[-1] if dict_hits else None


def _clause_verb_index(tokens, lexicon):
    """The verb position parse_clause selects: the first token
    whose canonical form is a seed verb; failing that, the
    first token the dictionary resolved with verb form. None
    when the clause holds no known verb. Shared by
    parse_clause and the ST-20 negated-target guard so both
    always agree on which span is the target span."""
    for i, tok in enumerate(tokens):
        if _seed_verb_of(tok) is not None:
            return i
    if lexicon:
        for i, tok in enumerate(tokens):
            res = lexicon.get(tok)
            if res is not None and res["form"] == "verb":
                return i
    return None


def parse_clause(tokens, carried_subject, lexicon=None):
    """Parse one clause. Return (subject, verb, target), or
    None when the clause holds no known verb. Seed tables win:
    the verb is the first token found in VERBS, as before; only
    when no seed verb exists does a dictionary resolution with
    form "verb" serve (canonical = its headword). Subject and
    target are the noun-phrase HEADS of their spans (see
    _np_head); a dictionary head is returned as its token and
    handled by the caller (it has no seed structure)."""
    verb_index = _clause_verb_index(tokens, lexicon)
    if verb_index is None:
        return None
    _vtok = tokens[verb_index]
    _sv = _seed_verb_of(_vtok)
    if _sv is not None:
        verb = _sv
    else:
        verb = lexicon[_vtok]["headword"]
    subject = _np_head(tokens[:verb_index], SUBJECT_WORDS, lexicon)
    if subject is None:
        subject = carried_subject
    target = _np_head(tokens[verb_index + 1:], NOUNS, lexicon)
    return (subject, verb, target)


def _do_company(clause_tokens, i):
    """True when another verb stands in the clause after the
    do-form at index i: a later do-form, or a later token the
    surface attests as verb-capable outside the auxiliary
    class (a seed verb form, or a dictionary resolution with
    verb form, when the tier is on). This is the company by
    which the SENTENCE decides a do-form's class (GRAM-1,
    RULED 2026-10-28 — his class-by-company doctrine): the
    form itself carries no class."""
    for u in clause_tokens[i + 1:]:
        if u in _guards.AUX_DO:
            return True
        if u not in _guards.AUX_RECOGNIZED \
                and _guard_verb_capable(u):
            return True
    return False


def _main_verb_do_forms(clause_tokens):
    """The indices of the do-forms standing as their clause's
    MAIN verb: exactly those with no company after them —
    "he does his work" (the doing is the action) against
    "the dog does bite the man" (the bite is; "does" is
    do-support and contributes nothing)."""
    return [i for i, tok in enumerate(clause_tokens)
            if tok in _guards.AUX_DO
            and not _do_company(clause_tokens, i)]


def _generic_frame(tokens):
    """GRAM-1 frame guard: the one generic/hypothetical shape
    deterministic from stored structure — a demonstrative
    subject ("that" / "this") whose do-form has no company,
    "that does that". The subject is unattested and the
    clause's only verb material is the auxiliary itself, so
    no statement geometry exists from which a current state
    could be read; the frame is generic and the analysis
    HOLDs naming it, never silently reading the sentence as
    a current statement. Anything richer (a gerund subject —
    "doing this does that") has no such signature in stored
    structure and is beyond this guard's reach by design."""
    if not tokens or tokens[0] not in ("that", "this"):
        return False
    return any(tok in _guards.AUX_DO
               and not _do_company(tokens, i)
               for i, tok in enumerate(tokens))


def _guard_verb_capable(w):
    """The guard layer's verb oracle: the ONE shared
    implementation (pmca_verb_capable via
    pmca_parse_guards.dict_verb_capable, Candidacy Rule Set
    C-4). RETIRED here (DICT-1 Part 1, WG2 2026-11-01): the
    seed-table oracle OR that used to extend this wrapper —
    seed identity is decided by the seed tables' own checks
    (and, for forms, by _seed_verb_of under Part 2), never by
    a dictionary oracle (Atlas T-1; C-5)."""
    return _guards.dict_verb_capable(w)


_LEMMA_MAP = None
_LEMMA_MAP_KEY = None


def _dictionary_fingerprint():
    """The dictionary-configuration fingerprint the lemma map
    is a function of (WG2c F-A fix, 2026-11-01): the resolved
    DB path plus its content hash (pmca_dictionary.file_hash,
    itself cached per path), or (None, None) when the tier is
    off. The resolver's lemma derivation reads the dictionary
    through _exact_headword, so the map is dictionary-
    dependent; keying the cache on this fingerprint makes the
    cached map a pure function of (seed tables, dictionary
    configuration) — any configuration change, through
    configure_dictionary OR directly through
    pmca_dictionary.configure / the environment, yields a
    different key and a rebuilt map."""
    path = pmca_dictionary.db_path()
    if path is None:
        return (None, None)
    return (path, pmca_dictionary.file_hash())


def _lemma_map():
    """The seed lemma map for the CURRENT dictionary
    configuration, built lazily and cached under its
    fingerprint (see _dictionary_fingerprint). Before the
    WG2c fix the first-built map was cached for the process
    lifetime: an analyze() before configure_dictionary()
    poisoned the map (the OFF map is the identity map), the
    ruled DICT-1 Part 2 crush lock then silently HOLDed for
    the rest of the process, and no gap was recorded."""
    global _LEMMA_MAP, _LEMMA_MAP_KEY
    key = _dictionary_fingerprint()
    if _LEMMA_MAP is None or _LEMMA_MAP_KEY != key:
        _LEMMA_MAP = _resolver.seed_lemma_map(VERBS, VERB_FORMS)
        _LEMMA_MAP_KEY = key
    return _LEMMA_MAP


def _seed_verb_of(tok):
    """The seed verb a token IS, under DICT-1 Part 2 (RULED
    2026-10-31): the token itself when it is a seed key, the
    stored form table's target, or — through
    pmca_forms_resolver, under its armour (per-form
    attestation, verb-slot sentence-context arbitration) —
    the seed key whose lemma the token is an attested form
    of. None when the resolver attests no seed identity
    (verdict NONE) or HOLDs (ambiguous_form: resolver HOLDs
    remain HOLDs — no identity is forced here)."""
    res = _resolver.resolve(tok, VERBS, VERB_FORMS,
                            context="verb_slot", lemmas=_lemma_map())
    return res["canonical"] if res["verdict"] == "OK" else None


def _part_of_targets(space, word):
    if space is None:
        return []
    targets = []
    for graph in space.graphs.values():
        for edge in graph.get("edges", []):
            if edge.get("source") == word and edge.get("identifier") == "part_of":
                targets.append(edge.get("target"))
    return targets


def _blank_params():
    return {"cause": None, "action": None, "target": None,
            "consequence": None, "directness": None}


def _result(sentence, verdict, omega, harm, gate, params, edges,
            trace, hold_reason, lock_reason):
    return {
        "sentence": sentence,
        "verdict": verdict,
        "omega": omega,
        "harm": harm,
        "gate": gate,
        "parameters": params,
        "edges": edges,
        "trace": trace,
        "hold_reason": hold_reason,
        "lock_reason": lock_reason,
        # P0 genitive contract: every result carries the typed
        # gap list and the co-target (None unless a part_of
        # co-target was bound). _done overwrites these defaults
        # on paths where gaps were recorded.
        "gaps": [],
        "co_target": None,
    }


def _analyze_core(sentence, closure, space):
    """The pre-wiring analysis, unchanged except that a
    coefficient cell missing from the seed tables falls back to
    the given own space (see _own_coefficient), and, when the
    dictionary tier is on (see configure_dictionary), a word
    the seed does not know may resolve through the stored
    dictionary. Verdicts, harm values, and trace lines are the
    pre-wiring ones; the additions are one trace note when the
    own space resolves a cell and one per dictionary resolution.
    GRAM-1 (2026-10-28) adds the grammar tier's auxiliary
    jurisdiction inside this flow — the frame guard, the two
    jurisdiction screens, the unknown-word scan's skip, the
    main-verb do-forms, and the verb-group negation arm; the
    blocks are marked GRAM-1 where they stand."""
    trace = []
    cleaned = clean(sentence)
    gate = detect_gate(cleaned)
    trace.append("gate (conscious choice): " + str(gate))

    tokens = cleaned.split()
    # FINDER BUILD (2026-11-01; spec section 5.2 as amended by
    # spec-check fixes F1/F2): the ONE FINDER (pmca_finder)
    # classifies EVERY token of the cleaned sequence here —
    # immediately after the tokens are computed and the gate
    # trace line is appended, BEFORE the generic-frame check
    # and every other return path in this function — and its
    # lines are appended to the trace at this point, so every
    # return path (pre-scan, scan, post-scan) carries them.
    # Invariant: len(finder trace lines) ==
    # len(clean(sentence).split()), on OK and on every HOLD
    # path, dictionary on or off. The records are observational
    # (spec section 5.2.4): no verdict, harm value, edge, or
    # clause binding reads them, except the one named flip —
    # the unknown-word scan below skips a token the finder
    # classified as an interjection (Helm determination on O1,
    # 2026-11-01: the governing interjection list is
    # pmca_parse_guards.INTERJECTION_TOKENS; the
    # unknown_word:wow death is retired for classified
    # interjections — flip register item in the finder build
    # paper).
    _finder_records = _finder.classify_tokens(
        tokens, seed_verbs=VERBS, verb_forms=VERB_FORMS,
        sentence=sentence)["occurrences"]
    trace.extend(_finder.trace_lines(_finder_records))
    # FINDER FIX WAVE (2026-11-01), W-1 + W-8: the interjection
    # skip is keyed on the OCCURRENCE (position), never on the
    # word string. The pre-fix string-keyed set let a later
    # UNKNOWN occurrence of the same spelling escape the
    # unknown-word scan on the strength of an earlier
    # occurrence's record ("Wow wow, he breaks the cup." ->
    # APPROVE over an UNKNOWN wow@1). The map carries, per
    # position, the grade the skip will consume — (tier, gap
    # reason) — so the skip can record what it consumed (W-8);
    # a record at UNKNOWN tier is never entered here, and an
    # occurrence whose own record is not an interjection is
    # never skipped.
    _finder_interjections = {}
    for rec in _finder_records:
        if rec["class"] == "interjection" \
                and rec["tier"] != "UNKNOWN":
            _finder_interjections[rec["position"]] = (
                rec["tier"],
                rec["gap"]["reason"] if rec["gap"] else None)
    # Occurrence positions per word string in the ORIGINAL
    # token sequence (the records' own indexing). The scan
    # below walks a normalized string whose tokens may have
    # been rewritten (genitive stems, contraction expansions),
    # but normalization never creates, removes, or reorders an
    # interjection string, so the scan's j-th occurrence of a
    # string is the original sequence's j-th occurrence of it.
    _finder_positions_by_word = {}
    for _pos, _tok in enumerate(tokens):
        _finder_positions_by_word.setdefault(_tok, []).append(_pos)
    # GRAM-1 frame guard: the generic "that does that" shape
    # (see _generic_frame) is a non-statement frame — it HOLDs
    # naming the frame here, before the geometry guards below
    # can name only its shape.
    if _generic_frame(tokens):
        trace.append("hold: generic_frame_unresolved")
        return _result(sentence, "HOLD", False, None, gate, _blank_params(), [], trace, "generic_frame_unresolved", None)
    scr = _guards.screen(tokens, _guards.dict_noun_capable, _guard_verb_capable, guards=("genitive", "g5"))
    if scr["hold"] is not None:
        trace.append("hold: " + scr["hold"])
        return _result(sentence, "HOLD", False, None, gate, _blank_params(), [], trace, scr["hold"], None)
    if _guards.passive_signature(scr["tokens"], _guard_verb_capable):
        trace.append("hold: passive_geometry_unhandled_in_p0")
        return _result(sentence, "HOLD", False, None, gate, _blank_params(), [], trace, "passive_geometry_unhandled_in_p0", None)
    # GRAM-1 (RULED 2026-10-28): the grammar tier's jurisdiction
    # over the auxiliary class, exercised before any content
    # adjudication. The analyzer parses verb groups itself, so
    # unlike the finder surfaces it cannot take the grammar
    # lock wholesale; it takes here the two dispositions the
    # ruling assigns this surface, and the rest of the class's
    # jurisdiction in the scans below. (1) A sentence-initial
    # auxiliary is subject-auxiliary inversion: a question,
    # not a statement — a non-statement frame HOLDs naming its
    # frame, never silently read as a (subjectless) statement.
    # (2) A modal anywhere in the sentence marks its
    # predication with the modal question — whether a modal
    # contributes the potential itself or selects a stored
    # capability — which stands OPEN and unruled; the
    # disposition is a HOLD naming that question, never a
    # silent state assignment in either direction (before
    # this screen the dictionary tier read "will" away and
    # evaluated the future predication as a present one).
    if scr["tokens"] and scr["tokens"][0] in _guards.AUX_RECOGNIZED:
        trace.append("hold: frame_not_a_statement:interrogative")
        return _result(sentence, "HOLD", False, None, gate, _blank_params(), [], trace, "frame_not_a_statement:interrogative", None)
    if any(tok in _guards.MODALS or tok in _guards.MODAL_NT
           for tok in scr["tokens"]):
        trace.append("hold: modal_state_unresolved")
        return _result(sentence, "HOLD", False, None, gate, _blank_params(), [], trace, "modal_state_unresolved", None)
    # Guard 2/4 verb counting uses this surface's own verb
    # attestation — a seed verb identity (DICT-1 Part 2: the
    # resolver's attested forms count as that verb, docket
    # consequence (2)) or a non-seed token the dictionary
    # resolves with verb form — the same rule parse_clause
    # builds its lexicon by. A generic capability count would
    # count seed nouns that carry dictionary verb senses
    # ("man", "spine") as second finite verbs. Part 2
    # consequence (2) in the other direction: the counter
    # reads RESOLVED forms — an -s token whose dictionary
    # resolution is a seed NOUN's headword (cups -> cup,
    # windows -> window) is that noun's plural in this scan,
    # not a second verb, and no longer counts.
    verbs = []
    for i, t in enumerate(scr["tokens"]):
        if _seed_verb_of(t) is not None:
            verbs.append((i, t))
        elif t not in KNOWN:
            e = pmca_dictionary.lookup(t)
            if e is not None and e["form"] == "verb":
                if not (t.endswith("s") and e["headword"] in NOUNS):
                    verbs.append((i, t))
    n_clauses = len(cleaned.split(" and "))
    if len(verbs) > n_clauses:
        trace.append("hold: multi_verb_unsegmented")
        return _result(sentence, "HOLD", False, None, gate, _blank_params(), [], trace, "multi_verb_unsegmented", None)
    if len(verbs) == 1:
        j = verbs[0][0] + 1
        while j < len(scr["tokens"]) and scr["tokens"][j] in ARTICLES:
            j += 1
        if j < len(scr["tokens"]) and scr["tokens"][j] in _guards.PREPOSITIONS and scr["tokens"][j] not in PREPOSITIONS:
            trace.append("hold: prepositional_barrier:" + scr["tokens"][j])
            return _result(sentence, "HOLD", False, None, gate, _blank_params(), [], trace, "prepositional_barrier:" + scr["tokens"][j], None)
    for g in scr["genitives"]:
        trace.append("genitive: " + g["head"] + " possessor " + g["possessor"])
    cleaned = " ".join(t for idx, t in enumerate(scr["tokens"]) if idx not in scr["possessor_indices"])

    # Every word must be known. An unknown word HOLDs, unless
    # the dictionary tier is on and the stored dictionary
    # resolves it: then the resolution joins this analysis's
    # lexicon and is noted in the trace, at scan position. A
    # token in neither tier HOLDs exactly as before. The
    # grammar-tier negation markers are outside this scan's
    # jurisdiction in both tiers (NEW-02): when no resolution
    # exists for one, the scan does not claim it — the
    # per-clause negation scan below adjudicates it and
    # records the true reason. (When the tier is on, a marker
    # resolves above and joins the lexicon exactly as before;
    # the skip is reached only when no resolution exists.)
    # GRAM-1 extends the same jurisdiction to the whole closed
    # auxiliary class (_guards.AUX_RECOGNIZED): an auxiliary is
    # a rule of language, never content, so the scan does not
    # claim one in either tier — the grammar dispositions
    # (do-support composition, the main-verb determination,
    # the negation arms) adjudicate it. A content word the
    # tiers cannot resolve is still claimed, exactly as before.
    lexicon = {}
    dict_on = _dictionary_on()
    _scan_seen = {}
    for tok in cleaned.split():
        _scan_ordinal = _scan_seen.get(tok, 0)
        _scan_seen[tok] = _scan_ordinal + 1
        if tok not in KNOWN:
            res = pmca_dictionary.lookup(tok) if dict_on else None
            if res is None:
                if tok in _guards.NEGATION_MARKERS \
                        or tok in _guards.AUX_RECOGNIZED:
                    continue
                # FINDER BUILD flip (Helm determination on O1,
                # 2026-11-01): a token the ONE FINDER classified
                # as an interjection (governing list:
                # pmca_parse_guards.INTERJECTION_TOKENS) is
                # clause furniture, not an unknown content word
                # — the scan does not claim it, exactly as it
                # does not claim the grammar-tier markers above.
                # The classification record stands in the trace
                # (the finder lines at the head of _analyze_core).
                # FINDER FIX WAVE (2026-11-01), W-1 + W-8: the
                # skip consumes ONLY the exact occurrence whose
                # own record is the interjection — this scan
                # token's occurrence ordinal selects its original
                # position, and only that position's record can
                # skip it. An UNKNOWN occurrence is never in the
                # map, so it is never skipped. When the skip
                # fires, the skip and the grade it consumed
                # (tier + the record's own gap) are recorded on
                # that occurrence's own finder trace line — no
                # silent consumption of a gap, and the finder
                # surface keeps exactly one line per token.
                _orig_positions = _finder_positions_by_word.get(
                    tok, [])
                _orig_pos = _orig_positions[_scan_ordinal] \
                    if _scan_ordinal < len(_orig_positions) else None
                if _orig_pos is not None \
                        and _orig_pos in _finder_interjections:
                    _skip_tier, _skip_gap = \
                        _finder_interjections[_orig_pos]
                    _skip_note = (
                        " skip=unknown_word_scan tier=" + _skip_tier
                        + (" gap=" + _skip_gap if _skip_gap else ""))
                    _line_prefix = ("finder: " + tok + "@"
                                    + str(_orig_pos) + " ")
                    for _li, _line in enumerate(trace):
                        if _line.startswith(_line_prefix):
                            trace[_li] = _line + _skip_note
                            break
                    else:
                        trace.append("finder: " + tok + "@"
                                     + str(_orig_pos) + _skip_note)
                    continue
                reason = "unknown_word:" + tok
                trace.append("hold: " + reason)
                return _result(sentence, "HOLD", False, None, gate,
                               _blank_params(), [], trace, reason, None)
            lexicon[tok] = res
            if res["headword"] == tok:
                trace.append("dictionary: " + tok
                             + " (webster1913, form: "
                             + res["form"] + ")")
            else:
                trace.append("dictionary: " + tok + " -> "
                             + res["headword"]
                             + " (webster1913, form: "
                             + res["form"] + ")")

    # GRAM-1: a do-form standing as its clause's main verb (the
    # company test, _main_verb_do_forms) joins the lexicon as
    # the verb "do" on the grammar tier's own authority — the
    # same virtual standing a dictionary verb receives in the
    # clause loop (no seed entry, no coefficient table), so the
    # clause evaluates exactly as it would for any lexical verb
    # in that slot, coverage HOLDs included. A do-form WITH
    # company is do-support: it contributes nothing to the
    # event and is never entered.
    for clause_text in cleaned.split(" and "):
        ctoks = clause_text.split()
        for i in _main_verb_do_forms(ctoks):
            tok = ctoks[i]
            if lexicon.get(tok, {}).get("form") == "verb":
                continue
            lexicon[tok] = {"headword": "do", "definitions": [],
                            "form": "verb"}
            trace.append("grammar: " + tok + " -> do"
                         + " (auxiliary class; main-verb position"
                         + " in this sentence)")

    clauses = []
    clause_gates = []
    carried = None
    for clause_text in cleaned.split(" and "):
        ctoks = clause_text.split()
        parsed = parse_clause(ctoks, carried, lexicon)
        if parsed is None:
            continue
        subject, verb, target = parsed
        if subject is not None:
            carried = subject
        clauses.append(parsed)
        clause_gates.append(_clause_gate(ctoks, lexicon))
    if len(clauses) == 0:
        trace.append("hold: unparsed")
        return _result(sentence, "HOLD", False, None, gate,
                       _blank_params(), [], trace, "unparsed", None)

    # Pack A (claims-03 W5): a negation adverb in a clause is
    # negation geometry, out of scope exactly as the "does not"
    # construction is — before this, "never" was silently
    # dropped by the parse and the negated action was evaluated
    # as occurring (APPROVE on "The man never breaks the cup.",
    # REFUSE (100, 1) on the spine variant).
    for clause_text in cleaned.split(" and "):
        ctoks = clause_text.split()
        # The "never" arm: jurisdiction is the shared
        # grammar-tier marker set (_guards.NEGATION_MARKERS —
        # the single definition the unknown-word scan also
        # consults, NEW-02); this arm is that member's
        # disposition.
        if any(tok in _guards.NEGATION_MARKERS and tok == "never"
               for tok in ctoks):
            trace.append("hold: negation_out_of_scope:never")
            return _result(sentence, "HOLD", False, None, gate,
                           _blank_params(), [], trace,
                           "negation_out_of_scope:never", None)
        # GRAM-1 (2026-10-28): "not" in the clause's VERB GROUP
        # — before the verb, where do-support puts it ("The
        # dog does not bite the man.") — is the same negation
        # geometry from the other side of the verb, and takes
        # the same disposition as the "never" arm above and
        # the target-span guard below. Before this arm, the
        # verb-group "not" was silently dropped by the parse
        # and the negated action was evaluated as occurring.
        # A fused do-form ("doesn't") carries the negation in
        # its own token and is named by it, as ":never" names
        # its token.
        vi = _clause_verb_index(ctoks, lexicon)
        if vi is not None:
            for tok in ctoks[:vi]:
                if tok in _guards.NEGATION:
                    trace.append("hold: negation_out_of_scope:not")
                    return _result(sentence, "HOLD", False, None,
                                   gate, _blank_params(), [], trace,
                                   "negation_out_of_scope:not", None)
                if tok in _guards.AUX_NT_DO:
                    tag = "negation_out_of_scope:" + tok
                    trace.append("hold: " + tag)
                    return _result(sentence, "HOLD", False, None,
                                   gate, _blank_params(), [], trace,
                                   tag, None)
        # ST-20 (2026-10-27): "not" inside the clause's target
        # span, before the head, drops the target in _np_head
        # and the clause was skipped silently — the sentence
        # approved below the gate with a negated harm target
        # never evaluated. The shared guard names it instead:
        # negation geometry, out of scope, HOLD — the same
        # disposition as "never" above. The negated action is
        # neither approved as safe nor refused as harm; the
        # target is unresolved and the reason is recorded.
        vi = _clause_verb_index(ctoks, lexicon)
        if vi is not None:
            tag = _guards.negated_target_check(
                ctoks[vi + 1:],
                lambda w: w in NOUNS
                or (lexicon.get(w) is not None
                    and lexicon[w]["form"] == "other"
                    and not _guards.adverb_attested(w)))
            if tag is not None:
                trace.append("hold: " + tag)
                return _result(sentence, "HOLD", False, None, gate,
                               _blank_params(), [], trace, tag, None)

    # Pack A (claims-03 W6): from here the result's gate field
    # reports whether a marker armed at least one clause; the
    # omega checks in the clause loop use each clause's OWN
    # gate (clause_gates), never the sentence-wide scan.
    if clause_gates:
        gate = any(clause_gates)

    params = _blank_params()
    edges = []
    harms = []
    consequence = None
    gaps = []
    co_target_val = None

    def _done(result):
        result["gaps"] = list(gaps)
        result["co_target"] = co_target_val
        return result

    if clauses:
        params["cause"], params["action"], params["target"] = clauses[0]

    for ci, (subject, verb, target) in enumerate(clauses):
        cgate = clause_gates[ci]
        trace.append("clause: cause=" + str(subject) + " action=" + verb
                     + " target=" + str(target))
        if target is None:
            continue
        if target not in NOUNS:
            # Dictionary target: definitions exist, but no seed
            # structure (no weight, no replaceability class, no
            # flags). Never omega (flags are seed structure,
            # never text) and never an invented weight: HOLD
            # before any coefficient work, naming what is
            # missing.
            trace.append("dictionary target: " + target
                         + " has definitions but no seed structure "
                         "(weight, replaceability unresolved)")
            return _done(_result(sentence, "HOLD", False, None, gate,
                           params, edges, trace,
                           "target_structure_missing:" + target, None))
        entry = NOUNS[target]
        flags = entry["flags"]

        # Omega locks. Checked after the target is known, before
        # any coefficient lookup. The child gate is armed by
        # THIS clause's gate only (Pack A, claims-03 W6). The
        # trigger checks live in the shared calculus module:
        # Trigger 2 reads stored data only — the target's flags
        # and the verb entry's stored death_consequence field —
        # and never compares the verb's name (P3-3).
        if _calculus.child_gate_lock(flags, cgate):
            trace.append("omega lock: child_gate")
            return _done(_result(sentence, "REFUSE", True, None, gate, params,
                           edges, trace, None, "child_gate"))
        if _calculus.terminal_death_lock(flags, VERBS.get(verb)):
            trace.append("omega lock: death_of_person")
            return _done(_result(sentence, "REFUSE", True, None, gate, params,
                           edges, trace, None, "death_of_person"))

        for g in scr["genitives"]:
            if g["head"] != target:
                continue
            possessor = g["possessor"]
            pt = _part_of_targets(space, target)
            kinds = (set(NOUNS[possessor]["flags"]) | {possessor}) if possessor in NOUNS else {possessor}
            attested = [t for t in pt if t == possessor or t in kinds]
            if attested:
                co_target_val = possessor
                trace.append("co-target: " + possessor + " bound via part_of " + target + " -> " + str(attested[0]))
                if possessor in NOUNS and _calculus.child_gate_lock(NOUNS[possessor]["flags"], cgate):
                    trace.append("omega lock: child_gate (co-target)")
                    return _done(_result(sentence, "REFUSE", True, None, gate, params, edges, trace, None, "child_gate"))
            else:
                if possessor in NOUNS and (set(NOUNS[possessor]["flags"]) & {"person", "child"}) and target in NOUNS and NOUNS[target]["replaceability"] != "replaceable":
                    gap = "slot::" + target + "::part_of"
                    if gap not in gaps:
                        gaps.append(gap)
                    trace.append("gap: " + gap + " (co-target unattested; omega not evaluated)")
                else:
                    trace.append("genitive: " + target + " possessor " + possessor + " (no part_of attested; possessor relational only)")

        ventry = VERBS.get(verb)
        if ventry is None:
            # Dictionary verb: the virtual entry. Its coefficient
            # table is empty, so with a seed target the cell
            # misses here and in the own space, and the clause
            # HOLDs coefficient_missing exactly as a seed verb
            # with an empty table does.
            ventry = _DICTIONARY_VERB_ENTRY
        table = ventry["coefficients"]
        cls = entry["replaceability"]
        if cls in table:
            coefficient = table[cls]
        else:
            # Seed table miss: the own space may hold registered
            # logic for this (verb, class) pair. A resolution
            # from there is traced with provenance 'own'. Still
            # missing everywhere: HOLD, exactly as before.
            coefficient = _own_coefficient(space, verb, cls)
            if coefficient is None:
                trace.append("hold: coefficient_missing")
                return _done(_result(sentence, "HOLD", False, None, gate,
                               params, edges, trace,
                               "coefficient_missing", None))
            trace.append("own logic: identifier for " + verb + ":"
                         + cls + " resolved from own space "
                         "(provenance: own)")
        edge_harm = pair(entry["weight"] * coefficient, 1)
        edges.append({"cause": subject, "action": verb,
                      "target": target, "harm": edge_harm})
        harms.append(edge_harm)
        trace.append("edge harm: " + str(edge_harm))
        if ventry["change_of_state"]:
            consequence = target + " " + ventry["result_state"]

    if consequence is not None:
        params["consequence"] = consequence
        params["directness"] = len(edges) - 1

    # The totaling verdict comes from the shared calculus module
    # (gate cutover, 2026-10-28): the cascade total, the raw
    # per-edge gate scan, and this decision table live there
    # exactly once, and pmca_proof calls the same functions.
    verdict, omega, reason, total = _calculus.totaling_verdict(
        harms, consequence is not None, closure=closure)

    if consequence is not None:
        trace.append("verdict: " + verdict)
        return _done(_result(sentence, verdict, False, total, gate, params,
                       edges, trace, None, None))

    # Missing Consequence. Present parameters still decide.
    if omega:
        trace.append("omega lock: closure_unknown_high_harm")
        return _done(_result(sentence, "REFUSE", True, total, gate, params,
                       edges, trace, None, "closure_unknown_high_harm"))
    if verdict == "HOLD":
        # Thread the calculus's own reason (B-2 named consequence:
        # the analyzer must not hardcode one reason for every HOLD —
        # an empty harm shape HOLDs as no_edges_no_event, a missing
        # consequence over high harm as consequence_missing_high_harm).
        hold_reason = reason if reason is not None else \
            "consequence_missing_high_harm"
        trace.append("hold: " + hold_reason)
        return _done(_result(sentence, "HOLD", False, total, gate, params,
                       edges, trace, hold_reason, None))
    trace.append("verdict: APPROVE (no consequence, harm below gate)")
    return _done(_result(sentence, "APPROVE", False, total, gate, params,
                   edges, trace, None, None))


# ---------------------------------------------------------------------
# Graph architecture wiring (stage 4). The analyzer carries an
# own GraphSpace, materialized empty and held at module level
# (the pmca_teaching held-space pattern). Analyses record their
# parameter summary there; logic registered through
# register_logic can resolve coefficient cells the seed tables
# lack. The space is never consulted for anything else, and
# recording never feeds back into a verdict.
# ---------------------------------------------------------------------

_OWN_SPACE = pmca_graphspace.GraphSpace("own")

# Analysis-record sequence (persistence design Decision 4). Records
# are scratch, never persisted; these counters are in-memory only.
# They replace the old count-the-"analysis:"-keys derivation, which
# would reuse a sequence number -- and raise a glue conflict inside
# analyze() -- if records were ever removed mid-process. One counter
# per space, held in this module-level registry: in any flow where
# no records are removed, a space's counter always equals the count
# the old derivation produced, so key sequences are identical to
# the counting ones (including a fresh space starting at 0); if
# records ever are removed, the counter still never goes back.
_RECORD_COUNTERS = weakref.WeakKeyDictionary()


def current_own_space():
    """The module's default own space: every analysis made
    without an explicit space records here, and register_logic
    writes here. Empty until the first analysis or
    registration."""
    return _OWN_SPACE


def restore_own_space(space):
    """Rebind the module's default own space to the given space
    (the persistence module's load path). Subsequent analyze() and
    register_logic() calls made without an explicit space use the
    given space."""
    global _OWN_SPACE
    _OWN_SPACE = space
    return _OWN_SPACE


def _first_content_word(cleaned):
    """First content word of a cleaned sentence: the first token
    that is a noun, a subject-only word, or a verb (canonical
    form). With the dictionary tier on, a token in neither the
    seed tables nor KNOWN that the dictionary resolves also
    counts (form "other": the token; form "verb": its
    headword). Falls back to the first token, then to ""."""
    tokens = cleaned.split()
    for tok in tokens:
        if tok in NOUNS or tok in SUBJECT_ONLY:
            return tok
        canon = _seed_verb_of(tok)
        if canon is not None:
            return canon
        if tok not in KNOWN and _dictionary_on():
            res = pmca_dictionary.lookup(tok)
            if res is not None:
                if res["form"] == "verb":
                    return res["headword"]
                return tok
    if tokens:
        return tokens[0]
    return ""


def _record_analysis(space, sentence, closure, result):
    """Record one analysis's parameter summary in the own
    space, at the dist_address home of the sentence's first
    content word (graph produced on demand, created_by
    "system"). The record key carries a sequence number from
    the module record counter, so a later analysis of the same
    sentence (before and after a registration, say) records
    alongside the earlier one, never against it. Returns the
    record's home address."""
    cleaned = clean(sentence)
    word = _first_content_word(cleaned)
    home = pmca_graphspace.dist_address(word)
    if home not in space.graphs:
        space.produce_graph(home, "own", "system")
    seq = _RECORD_COUNTERS.get(space, 0)
    _RECORD_COUNTERS[space] = seq + 1
    key = "analysis:" + str(seq) + ":" + cleaned
    entry = {"kind": "analysis_record",
             "sentence": result["sentence"],
             "cleaned": cleaned,
             "closure": closure,
             "verdict": result["verdict"],
             "omega": result["omega"],
             "harm": result["harm"],
             "gate": result["gate"],
             "parameters": dict(result["parameters"]),
             "hold_reason": result["hold_reason"],
             "lock_reason": result["lock_reason"],
             "provenance": "analyzer"}
    space.place_entry(home, key, entry)
    return home


def _own_coefficient(space, verb, cls):
    """Coefficient for a (verb, replaceability class) pair from
    the own space, or None. Only entries written by
    register_logic count; analysis records never resolve."""
    entry = space.lookup(verb + ":" + cls)
    if isinstance(entry, dict) and entry.get("kind") == "own_logic":
        return entry["identifier"]
    return None


def register_logic(key, identifier, space=None):
    """Register one piece of own logic: the coefficient for a
    (verb, replaceability class) pair, keyed "verb:class" (a
    surface verb form is canonicalized). identifier is the
    integer coefficient. This API stands in for the gated
    promotion path: it is the ONLY way logic entries enter the
    own space, and analyze() never calls it (no
    self-ratification). Re-registering the same key with the
    same identifier is a no-op; a different identifier raises
    GlueConflict, the graphspace glue discipline. Returns the
    key's home address."""
    if space is None:
        space = _OWN_SPACE
    if not isinstance(key, str) or key.count(":") != 1:
        raise ValueError("logic key must be 'verb:class'")
    verb, cls = key.split(":")
    _canon = _seed_verb_of(verb)
    if _canon is not None:
        verb = _canon
    if verb not in VERBS:
        raise ValueError("unknown verb in logic key: " + verb)
    classes = set()
    for noun_entry in NOUNS.values():
        classes.add(noun_entry["replaceability"])
    if cls not in classes:
        raise ValueError("unknown replaceability class: " + cls)
    if isinstance(identifier, bool) or not isinstance(identifier, int):
        raise ValueError("identifier must be an integer coefficient")
    if identifier < 0:
        raise ValueError("identifier must be non-negative")
    canonical_key = verb + ":" + cls
    entry = {"kind": "own_logic", "key": canonical_key, "verb": verb,
             "replaceability": cls, "identifier": identifier,
             "provenance": "own"}
    home = pmca_graphspace.dist_address(verb)
    if home not in space.graphs:
        space.produce_graph(home, "own", "system")
    space.place_entry(home, canonical_key, entry)
    return home


def analyze(sentence, closure=False, space=None):
    """Analyze one sentence. Return the 7 parameters, a verdict,
    and a trace. closure=True at engagement or document end.
    With space given, the analysis is recorded in that own
    space and own logic registered there may resolve
    seed-missing coefficient cells; with no space, the module's
    default own space is used."""
    if space is None:
        space = _OWN_SPACE
    result = _analyze_core(sentence, closure, space)
    # FINDER BUILD (2026-11-01; spec section 5.2.3 + fix F11):
    # the occurrence records ride the result under "finder" as
    # an in-memory carriage only. They are stored exactly once —
    # as the finder lines in the trace, which the seal machinery
    # journals as steps; neither _engagement_payload's output
    # nor _record_analysis copies this key, and the records are
    # never merged into result["gaps"] or written as graph
    # edges. The recomputation here is the same deterministic
    # classify_tokens call _analyze_core made for the trace
    # lines, over the same cleaned token sequence.
    result["finder"] = _finder.classify_tokens(
        clean(sentence).split(), seed_verbs=VERBS,
        verb_forms=VERB_FORMS, sentence=sentence)["occurrences"]
    _record_analysis(space, sentence, closure, result)
    return result


# ---------------------------------------------------------------------
# Dictionary wiring (2026-10-20 build, stage 1). The analyzer's
# lookup gains the stored dictionary (pmca_dictionary: Webster
# 1913) as a second tier under the seed tables, per Jonathan's
# ruling: "We have to [point] the system t[o] the dictionary
# for definitions." OFF by default: until configure_dictionary
# is called (or PMCA_DICTIONARY_DB is set), every output and
# trace is byte-identical to the pre-wiring module. With it on,
# a word the seed does not know but the dictionary resolves is
# present as MEANING (its definitions, and a form read off the
# stored text); it carries no seed structure. A dictionary verb
# acts through the virtual entry below (empty coefficient
# table, no result state, no change of state); a dictionary
# target HOLDs target_structure_missing. Nothing is invented,
# and flags (hence omega) never come from text.
# ---------------------------------------------------------------------

_DICTIONARY_VERB_ENTRY = {"coefficients": {}, "result_state": None,
                          "change_of_state": False}


def configure_dictionary(path):
    """Point the analyzer at a Webster 1913 DB (see
    pmca_dictionary.configure). path None turns the tier off.
    Either way the cached code manifest is dropped: the
    manifest names the dictionary exactly when one is in use,
    so a configuration change must recompute it. The cached
    lemma map is likewise dropped (WG2c F-A fix): it is
    dictionary-dependent, so a configuration change
    invalidates it here, and _lemma_map's fingerprint key
    rebuilds it under the new configuration on next use
    (the key also covers configuration routes that bypass
    this function)."""
    global _CODE_MANIFEST, _LEMMA_MAP, _LEMMA_MAP_KEY
    pmca_dictionary.configure(path)
    _CODE_MANIFEST = None
    _LEMMA_MAP = None
    _LEMMA_MAP_KEY = None


def _dictionary_on():
    """True exactly when the dictionary tier can resolve in
    this process: pmca_dictionary has a DB (configured through
    configure_dictionary or directly, or PMCA_DICTIONARY_DB is
    set) and the named file exists. Derived from
    pmca_dictionary's own state (Pack G, 2026-10-26), so every
    consultation path — the lexicon scan in _analyze_core, the
    verb scan, and the parse guards, which consult
    pmca_dictionary directly — sees the same tier this
    function reports, and the code manifest, which names the
    dictionary exactly in this state, agrees with behaviour
    by construction. (Before Pack G this tracked only the
    analyzer's own setting while pmca_dictionary silently
    resolved a default-path DB: the guards consulted a
    dictionary the manifest omitted — claims audit 06,
    synthesis D12.)"""
    return pmca_dictionary.db_path() is not None


# ---------------------------------------------------------------------
# Falsifiability: engagement logging (2026-10-20 build, stage 1).
# analyze() above is UNTOUCHED. What follows only observes it.
#
# Design (Jonathan, ruled 2026-10-20): every analysis is an
# engagement, recorded in the pools "engagement" chain (a separate
# audit artifact per the Decision 4 scope ruling: this log never
# lives in a GraphSpace and never feeds a verdict). A record binds
# the byte-exact input, the before/after fingerprints of the own
# space (persistence.analyzer_space_checksum, record-filtered, so
# for a pure analysis before == after: analysis does not mutate
# reasoning state), the code manifest hash, the verbatim trace,
# and the full output. Records accumulate in an open segment on
# the potential side; a segment completes at 100 engagements, at
# a teaching event, or at session end (hybrid trigger, Alder's
# proposal, adopted by Jonathan), and closes with a key computed
# over its closing fingerprint and chain head. A discarded
# segment is marked by a record, never vaporized (D5). Wall-clock
# time never enters any computation here; the pool envelope adds
# its own host-asserted timestamp.
# ---------------------------------------------------------------------

_SEGMENT_LIMIT = 100

# Per-store segment state, recovered by scanning the engagement
# chain on first use in a process and cached afterwards. Same
# weakref pattern as _RECORD_COUNTERS, keyed by store.
_SEGMENT_STATES = weakref.WeakKeyDictionary()

_CODE_MANIFEST = None
_MANIFEST_MODULES = ("pmca_sentence_analyzer", "pmca_seed",
                     "pmca_graphspace", "pmca_persistence")


def _canonical_json(obj):
    """Canonical JSON: sorted keys, tight separators. The one
    serialization every hash in this section is computed over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _code_manifest():
    """{module_name: sha256 of file bytes} for the modules whose
    code determines a verdict, located beside this module.
    Computed lazily, once per process: determinism is a property
    of code + state, so the code is pinned by content hash."""
    global _CODE_MANIFEST
    if _CODE_MANIFEST is None:
        here = os.path.dirname(os.path.abspath(__file__))
        manifest = {}
        for name in _MANIFEST_MODULES:
            with open(os.path.join(here, name + ".py"), "rb") as f:
                manifest[name] = hashlib.sha256(f.read()).hexdigest()
        if _dictionary_on():
            dictionary_hash = pmca_dictionary.file_hash()
            if dictionary_hash is not None:
                manifest["dictionary"] = dictionary_hash
        _CODE_MANIFEST = manifest
    return _CODE_MANIFEST


def _code_manifest_hash():
    return hashlib.sha256(
        _canonical_json(_code_manifest()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------
# Atomic-entry wiring (atomic-entry pack, build step 3, 2026-10-27;
# spec sections 4.4, 4.5, 5, 8). Adoption is a host act: until
# configure_entry_store is called, every surface in this module
# behaves exactly as it did before the pack -- pool engagement
# records, current returns, current exceptions. With a store
# configured, analysis processes and segment lifecycle acts write
# sealed entries to the entry store instead of pool records: a
# segment_open entry embeds the segment's serialized starting
# state (the checkpoint, inside the entry, written by the same
# append that opens the segment), so the state_root / _checkpoint
# external-file path leaves the segment lifecycle on this path --
# there is no way to open a segment without its state.
#
# Boundary discipline: at most one segment is open across both
# record sources at any time, and a segment's records live in
# exactly one of them. A segment left open in the pool chain when
# the first entry-mode open arrives is closed in the pool chain
# (reason "adoption") before the first entry segment opens.
# ---------------------------------------------------------------------

_ENTRY_STORE = None
_ENTRY_STORE_OWNED = False
_ENTRY_KEYRING_PATH = None


def configure_entry_store(root, keyring_path=None):
    """Point the analyzer's logging surfaces at an atomic entry
    store. root is a state root (the entry files live under
    <root>/atomic/, spec 4.1) or an already-open
    pmca_atomic_entries.EntryStore to share with another surface
    (a store held by this module is owned and closed on
    reconfigure; a shared store is left open). root None turns
    entry writing off: the surfaces return to pool records,
    exactly as before the pack.

    Hosts configure once, before logging, in the same spirit as
    configure_dictionary. Reconfiguring drops the cached segment
    states, so the next segment scan rebuilds from both record
    sources under the new configuration."""
    global _ENTRY_STORE, _ENTRY_STORE_OWNED, _ENTRY_KEYRING_PATH
    if _ENTRY_STORE is not None and _ENTRY_STORE_OWNED:
        _ENTRY_STORE.close()
    _ENTRY_STORE = None
    _ENTRY_STORE_OWNED = False
    _ENTRY_KEYRING_PATH = None
    for cached in list(_SEGMENT_STATES.keys()):
        _SEGMENT_STATES.pop(cached, None)
    if root is None:
        return None
    if isinstance(root, pmca_atomic_entries.EntryStore):
        _ENTRY_STORE = root
        _ENTRY_KEYRING_PATH = keyring_path
        return _ENTRY_STORE
    _ENTRY_STORE = pmca_atomic_entries.EntryStore.open(
        root, keyring_path=keyring_path)
    _ENTRY_STORE_OWNED = True
    _ENTRY_KEYRING_PATH = keyring_path
    return _ENTRY_STORE


def _dictionary_block():
    """The environment's dictionary block (spec 4.5): ALWAYS
    present on every entry, from every surface. Off names
    configured False, db_hash None, source "off" (the ST-71
    fix: a dict-off record names its environment instead of
    naming no dictionary at all). On names the DB hash and how
    the tier resolved: "explicit" (a configure call), "env"
    (PMCA_DICTIONARY_DB), or "default" (any other resolution;
    Pack G removed the default path, so that value is defensive
    only)."""
    if not _dictionary_on():
        return {"configured": False, "db_hash": None,
                "source": "off"}
    if pmca_dictionary._configured is not pmca_dictionary._UNSET:
        source = "explicit"
    elif os.environ.get("PMCA_DICTIONARY_DB"):
        source = "env"
    else:
        source = "default"
    return {"configured": True,
            "db_hash": pmca_dictionary.file_hash(),
            "source": source}


def _entry_environment():
    """The entry_v 1 environment (spec 4.5): the analyzer's
    _code_manifest() map and its hash, plus the dictionary
    block, always named. This is the ONE manifest helper: the
    other wired surfaces (teaching, write-in) build their
    entries' environments through it, so every entry in the
    store names its code and dictionary environment the same
    way."""
    return {"code_manifest_hash": _code_manifest_hash(),
            "code_manifest": dict(_code_manifest()),
            "dictionary": _dictionary_block()}


def _starting_state_blob(space, generation):
    """The analyzer space serialized EXACTLY as
    pmca_persistence.save_analyzer_space serializes it (typed
    encoding, its own checksum): the save runs against a
    throwaway directory with the keyring state this module's
    entry store was configured with, and the envelope it
    writes is read back and embedded in the segment_open
    entry. The save's own read-back verification runs as part
    of the save, so a state that could not be loaded back
    raises here, at segment open, before the segment exists.
    The generation follows the _checkpoint allocation
    convention (last recorded + 1), so blob generations stay
    continuous with the pre-adoption checkpoint generations in
    the same segment history."""
    tmp = tempfile.mkdtemp(prefix="pmca_segment_blob_")
    try:
        path = pmca_persistence.save_analyzer_space(
            tmp, space, generation,
            keyring_path=_ENTRY_KEYRING_PATH)
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _effective_space(space):
    return _OWN_SPACE if space is None else space


def _fingerprint(space):
    return pmca_persistence.analyzer_space_checksum(space)


def _scan_segments(store):
    """Rebuild segment state by scanning BOTH record sources
    once: the pool engagement chain (pre-adoption segments) and,
    when an entry store is configured, the entry store
    (post-adoption segments; spec 4.4 / build step 3). If the
    last segment_open in either source has no matching
    segment_close or segment_discard, that segment is still
    open. Segment ids are allocated across both sources, so
    "opens" counts both and ids never repeat across the
    adoption boundary. state["open_source"] names which source
    the open segment's records live in ("pool" or "entry");
    state["open_checkpoint_entry_seq"] is, for an entry segment,
    the seq of the segment_open entry carrying its starting
    state blob. Returns the state dict (also cached per store).
    """
    state = {"opens": 0, "open_segment": None, "count": 0,
             "first_seq": None, "last_seq": None,
             "segment_head": None, "closed": 0, "discarded": 0,
             "last_key": None, "last_checkpoint_generation": None,
             "open_source": None, "open_checkpoint_entry_seq": None}
    for rec in store.by_namespace("engagement"):
        payload = rec["payload"]
        if not isinstance(payload, dict):
            continue
        kind = payload.get("record_kind")
        if kind == "segment_open":
            state["opens"] += 1
            state["open_segment"] = payload["segment_id"]
            state["open_source"] = "pool"
            state["open_checkpoint_entry_seq"] = None
            state["count"] = 0
            state["first_seq"] = None
            state["last_seq"] = None
            state["segment_head"] = None
            gen = payload.get("checkpoint_generation")
            if gen is not None:
                state["last_checkpoint_generation"] = gen
        elif kind == "engagement":
            if state["open_segment"] is not None \
                    and state["open_source"] == "pool":
                if state["count"] == 0:
                    state["first_seq"] = rec["seq"]
                state["count"] += 1
                state["last_seq"] = rec["seq"]
                state["segment_head"] = rec["hash"]
        elif kind == "segment_close":
            state["closed"] += 1
            state["last_key"] = payload.get("key")
            gen = payload.get("checkpoint_generation")
            if gen is not None:
                state["last_checkpoint_generation"] = gen
            if payload.get("segment_id") == state["open_segment"] \
                    and state["open_source"] == "pool":
                state["open_segment"] = None
                state["open_source"] = None
                state["count"] = 0
                state["first_seq"] = None
                state["last_seq"] = None
                state["segment_head"] = None
        elif kind == "segment_discard":
            state["discarded"] += 1
            if payload.get("segment_id") == state["open_segment"] \
                    and state["open_source"] == "pool":
                state["open_segment"] = None
                state["open_source"] = None
                state["count"] = 0
                state["first_seq"] = None
                state["last_seq"] = None
                state["segment_head"] = None
    if _ENTRY_STORE is not None:
        for entry in _ENTRY_STORE.replay():
            kind = entry.get("kind")
            if kind == "segment_open":
                starting = entry.get("starting_state") or {}
                state["opens"] += 1
                state["open_segment"] = starting.get("segment_id")
                state["open_source"] = "entry"
                state["open_checkpoint_entry_seq"] = entry["seq"]
                state["count"] = 0
                state["first_seq"] = None
                state["last_seq"] = None
                state["segment_head"] = None
                blob = entry.get("starting_state_blob") or {}
                gen = blob.get("generation")
                if isinstance(gen, int) \
                        and not isinstance(gen, bool):
                    state["last_checkpoint_generation"] = gen
            elif kind == "process" \
                    and entry.get("surface") == "analysis":
                starting = entry.get("starting_state") or {}
                if state["open_segment"] is not None \
                        and state["open_source"] == "entry" \
                        and starting.get("segment_id") \
                        == state["open_segment"]:
                    if state["count"] == 0:
                        state["first_seq"] = entry["seq"]
                    state["count"] += 1
                    state["last_seq"] = entry["seq"]
                    state["segment_head"] = entry["seal"]
            elif kind == "segment_close":
                state["closed"] += 1
                state["last_key"] = entry.get("key")
                if entry.get("segment_id") == state["open_segment"] \
                        and state["open_source"] == "entry":
                    state["open_segment"] = None
                    state["open_source"] = None
                    state["open_checkpoint_entry_seq"] = None
                    state["count"] = 0
                    state["first_seq"] = None
                    state["last_seq"] = None
                    state["segment_head"] = None
            elif kind == "segment_discard":
                state["discarded"] += 1
                if entry.get("segment_id") == state["open_segment"] \
                        and state["open_source"] == "entry":
                    state["open_segment"] = None
                    state["open_source"] = None
                    state["open_checkpoint_entry_seq"] = None
                    state["count"] = 0
                    state["first_seq"] = None
                    state["last_seq"] = None
                    state["segment_head"] = None
    _SEGMENT_STATES[store] = state
    return state


def _segment_state(store):
    state = _SEGMENT_STATES.get(store)
    if state is None:
        state = _scan_segments(store)
    return state


def _checkpoint(store, space, state_root):
    """Save a checkpoint of the effective space when state_root
    is given; return the generation used, else None.
    persistence.save_analyzer_space takes (root, space,
    generation) and returns the file path, so generations are
    allocated here: the first checkpoint is generation 1, then
    last recorded + 1 (the demo app's convention; the chain is
    the record of generations)."""
    if state_root is None:
        return None
    state = _segment_state(store)
    generation = (state["last_checkpoint_generation"] or 0) + 1
    pmca_persistence.save_analyzer_space(state_root, space, generation)
    state["last_checkpoint_generation"] = generation
    return generation


def _open_entry_segment(store, space, state):
    """Append a segment_open ENTRY embedding the serialized
    starting state (the blob is the CURRENT space serialization)
    and swing the segment state to the new segment, in place.
    The inner half of _engagement_open_entry, split out so the
    seal path (_seal_analysis_entry) can open the next segment
    inside its own hold of the entry store's lock when a count
    close fires there: the caller holds the lock, passes the
    state, and has already decided a new segment is wanted."""
    segment_id = state["opens"] + 1
    opening_fingerprint = _fingerprint(space)
    generation = (state["last_checkpoint_generation"] or 0) + 1
    blob = _starting_state_blob(space, generation)
    entry = _ENTRY_STORE.append_segment_open(
        opening_fingerprint, blob, _entry_environment(),
        starting_state={
            "segment_id": segment_id,
            "state_before_checksum": opening_fingerprint})
    state["opens"] += 1
    state["open_segment"] = segment_id
    state["open_source"] = "entry"
    state["open_checkpoint_entry_seq"] = entry["seq"]
    state["last_checkpoint_generation"] = generation
    state["count"] = 0
    state["first_seq"] = None
    state["last_seq"] = None
    state["segment_head"] = None
    return segment_id


def _engagement_open_entry(store, space):
    """engagement_open on the entry path (atomic-entry pack):
    append a segment_open ENTRY embedding the serialized
    starting state, and return the new segment id. If an entry
    segment is already open, return its id. If a POOL segment is
    still open (the adoption boundary), close it in the pool
    chain first, reason "adoption", so exactly one segment is
    open across both sources and its records live in one of
    them. state_root / _checkpoint play no part here: the
    entry carries the state, so there is no way to open a
    segment without it. One critical section under the pool
    store's lock, as on the pool path (the entry store's own
    append takes its lock inside this hold)."""
    with store.lock:
        state = _segment_state(store)
        if state["open_segment"] is not None:
            if state["open_source"] == "entry":
                return state["open_segment"]
            engagement_close(store, space=space, reason="adoption")
            state = _segment_state(store)
        return _open_entry_segment(store, space, state)


def engagement_open(store, space=None, state_root=None):
    """Open a segment in the store, or return the open one's id.
    Appends a segment_open record {segment_id,
    opening_fingerprint, checkpoint_generation}. When state_root
    is given, a checkpoint of the effective space is saved first
    and its generation recorded.

    With an entry store configured (configure_entry_store), the
    segment opens as an entry instead -- see
    _engagement_open_entry; state_root is then not used, because
    the entry embeds the starting state itself.

    The check-and-open is one critical section under the
    store's lock (Pack D, 2026-10-26): two threads racing to
    open cannot both append a segment_open. The lock is the
    store's own (pmca_pools.PoolStore.lock, reentrant), so the
    append inside proceeds under the same hold."""
    space = _effective_space(space)
    if _ENTRY_STORE is not None:
        return _engagement_open_entry(store, space)
    with store.lock:
        state = _segment_state(store)
        if state["open_segment"] is not None:
            return state["open_segment"]
        segment_id = state["opens"] + 1
        opening_fingerprint = _fingerprint(space)
        generation = _checkpoint(store, space, state_root)
        pmca_pools.engagement_record(store, {
            "record_kind": "segment_open",
            "segment_id": segment_id,
            "opening_fingerprint": opening_fingerprint,
            "checkpoint_generation": generation})
        state["opens"] += 1
        state["open_segment"] = segment_id
        state["count"] = 0
        state["first_seq"] = None
        state["last_seq"] = None
        state["segment_head"] = None
        return segment_id


def _close_entry_segment(store, space, state, reason):
    """Append a segment_close ENTRY for the open entry segment
    the passed state describes, carrying the close fields
    exactly as the pool segment_close payload computes them
    (segment_id, reason, engagements, first_seq, last_seq,
    segment_head, closing_fingerprint, and the segment key by
    the same formula -- computed over ENTRY seqs, with
    segment_head the seal of the segment's last engagement
    entry, or "GENESIS" if the segment holds none), then clear
    the open-segment fields in place. The inner half of
    _engagement_close_entry, split out for the seal path (see
    _open_entry_segment): the caller holds the lock and has
    checked the open segment is entry-backed."""
    closing_fingerprint = _fingerprint(space)
    segment_head = state["segment_head"] or "GENESIS"
    key = hashlib.sha256(_canonical_json({
        "closing_fingerprint": closing_fingerprint,
        "segment_head": segment_head,
        "first_seq": state["first_seq"],
        "last_seq": state["last_seq"]}).encode("utf-8")).hexdigest()
    entry = _ENTRY_STORE.append_segment_close({
        "segment_id": state["open_segment"],
        "reason": reason,
        "engagements": state["count"],
        "first_seq": state["first_seq"],
        "last_seq": state["last_seq"],
        "segment_head": segment_head,
        "closing_fingerprint": closing_fingerprint,
        "key": key}, _entry_environment())
    state["closed"] += 1
    state["last_key"] = key
    state["open_segment"] = None
    state["open_source"] = None
    state["open_checkpoint_entry_seq"] = None
    state["count"] = 0
    state["first_seq"] = None
    state["last_seq"] = None
    state["segment_head"] = None
    return entry


def _engagement_close_entry(store, space, reason):
    """engagement_close on the entry path (atomic-entry pack):
    append a segment_close ENTRY carrying the close fields
    exactly as the pool segment_close payload computes them
    (see _close_entry_segment). Returns the sealed entry, or
    None when the open segment is not entry-backed, in which
    case the pool path applies. One critical section under the
    pool store's lock, as on the pool path."""
    with store.lock:
        state = _segment_state(store)
        if state["open_segment"] is None \
                or state["open_source"] != "entry":
            return None
        return _close_entry_segment(store, space, state, reason)


def engagement_close(store, space=None, reason="manual",
                     state_root=None):
    """Close the open segment with a key, or return None if no
    segment is open. When state_root is given, a checkpoint is
    saved first and its generation recorded. The key is the
    sha256 of the canonical JSON of {closing_fingerprint,
    segment_head, first_seq, last_seq}, where segment_head is
    the hash of the segment's last engagement record, or
    "GENESIS" if the segment holds none. Returns the stored
    close record. One critical section under the store's lock:
    the key is computed over the segment state and the close
    record appended atomically with the state transition.

    With an entry store configured and the open segment
    entry-backed, the close is a segment_close entry instead
    (see _engagement_close_entry) and the sealed entry is
    returned; a pool-backed open segment still closes on the
    pool path."""
    space = _effective_space(space)
    if _ENTRY_STORE is not None:
        entry = _engagement_close_entry(store, space, reason)
        if entry is not None:
            return entry
    with store.lock:
        state = _segment_state(store)
        if state["open_segment"] is None:
            return None
        generation = _checkpoint(store, space, state_root)
        closing_fingerprint = _fingerprint(space)
        segment_head = state["segment_head"] or "GENESIS"
        key = hashlib.sha256(_canonical_json({
            "closing_fingerprint": closing_fingerprint,
            "segment_head": segment_head,
            "first_seq": state["first_seq"],
            "last_seq": state["last_seq"]}).encode("utf-8")).hexdigest()
        rec = pmca_pools.engagement_record(store, {
            "record_kind": "segment_close",
            "segment_id": state["open_segment"],
            "reason": reason,
            "engagements": state["count"],
            "first_seq": state["first_seq"],
            "last_seq": state["last_seq"],
            "segment_head": segment_head,
            "closing_fingerprint": closing_fingerprint,
            "checkpoint_generation": generation,
            "key": key})
        state["closed"] += 1
        state["last_key"] = key
        state["open_segment"] = None
        state["count"] = 0
        state["first_seq"] = None
        state["last_seq"] = None
        state["segment_head"] = None
        return rec


def engagement_discard(store, reason="discarded"):
    """Close the open segment WITHOUT a key, marking it
    discarded by a record (D5: discards are marked, never
    vaporized). Returns the stored discard record, or None if
    no segment is open. One critical section under the store's
    lock, like engagement_open and engagement_close.

    With an entry store configured and the open segment
    entry-backed, the discard is a segment_discard entry
    (segment_id + reason, per the entry format) and the sealed
    entry is returned; a pool-backed open segment still
    discards on the pool path."""
    if _ENTRY_STORE is not None:
        with store.lock:
            state = _segment_state(store)
            if state["open_segment"] is not None \
                    and state["open_source"] == "entry":
                entry = _ENTRY_STORE.append_segment_discard(
                    state["open_segment"], _entry_environment(),
                    reason=reason)
                state["discarded"] += 1
                state["open_segment"] = None
                state["open_source"] = None
                state["open_checkpoint_entry_seq"] = None
                state["count"] = 0
                state["first_seq"] = None
                state["last_seq"] = None
                state["segment_head"] = None
                return entry
    with store.lock:
        state = _segment_state(store)
        if state["open_segment"] is None:
            return None
        rec = pmca_pools.engagement_record(store, {
            "record_kind": "segment_discard",
            "segment_id": state["open_segment"],
            "reason": reason,
            "engagements": state["count"]})
        state["discarded"] += 1
        state["open_segment"] = None
        state["count"] = 0
        state["first_seq"] = None
        state["last_seq"] = None
        state["segment_head"] = None
        return rec


def segment_status(store):
    """{open_segment, engagements_in_open_segment,
    closed_segments, discarded_segments, last_key} for a store,
    from the recovery cache (scanning the chain on first use).
    Read under the store's lock so the snapshot cannot straddle
    another thread's segment transition."""
    with store.lock:
        state = _segment_state(store)
        return {"open_segment": state["open_segment"],
                "engagements_in_open_segment": state["count"],
                "closed_segments": state["closed"],
                "discarded_segments": state["discarded"],
                "last_key": state["last_key"]}


def _harm_list(harm):
    """Rational pair as a JSON list [num, den]; None stays None.
    Floats never appear: pairs are integers by construction."""
    if harm is None:
        return None
    return [harm[0], harm[1]]


def _engagement_payload(sentence, closure, result,
                        space_before_checksum, space_after_checksum,
                        segment_id):
    """Build the engagement record payload (build spec:
    Engagement record payload). The single builder shared by
    analyze_logged and log_result, so both paths record
    identically. claim_id is left None here and set by
    _append_engagement, the only caller, from the seq the
    record is about to receive."""
    input_obj = {"sentence": sentence, "closure": closure}
    edges = []
    for e in result["edges"]:
        edges.append({"cause": e["cause"], "action": e["action"],
                      "target": e["target"],
                      "harm": _harm_list(e["harm"])})
    return {
        "record_kind": "engagement",
        "claim_id": None,
        "segment_id": segment_id,
        "input": input_obj,
        "input_hash": hashlib.sha256(
            _canonical_json(input_obj).encode("utf-8")).hexdigest(),
        "state_before": {"space_checksum": space_before_checksum},
        "state_after": {"space_checksum": space_after_checksum},
        "code_manifest_hash": _code_manifest_hash(),
        "code_manifest": dict(_code_manifest()),
        "trajectory": list(result["trace"]),
        "output": {
            "verdict": result["verdict"],
            "omega": result["omega"],
            "harm": _harm_list(result["harm"]),
            "parameters": dict(result["parameters"]),
            "edges": edges,
            "hold_reason": result["hold_reason"],
            "lock_reason": result["lock_reason"],
            "gate": result["gate"]}}


def _append_engagement(store, space, sentence, closure, result,
                       before_checksum, after_checksum):
    """Append one engagement record for an already-computed
    result: ensure a segment is open, build the payload, append,
    apply the auto-close rule (the append that brings the open
    segment to 100 engagements closes it, reason "count",
    immediately after). Returns the stored record. Append
    exceptions propagate (loud, per design); the result is
    already computed and is never altered here.

    claim_id: pools assigns seq = the engagement chain's current
    length, which is readable before the append through the
    public by_namespace API, so the id is derived deterministically
    as ENG-<that seq, zero-padded 6> without mutating the stored
    record afterwards.

    The whole operation — ensure-open, claim-id derivation from
    the chain length, the append, the segment-state update, and
    the auto-close — is one critical section under the store's
    lock (Pack D, 2026-10-26), so concurrent callers cannot
    derive the same claim id, double-open a segment, or straddle
    a count transition."""
    with store.lock:
        segment_id = engagement_open(store, space=space)
        state = _segment_state(store)
        payload = _engagement_payload(sentence, closure, result,
                                      before_checksum, after_checksum,
                                      segment_id)
        payload["claim_id"] = "ENG-%06d" % len(
            store.by_namespace("engagement"))
        rec = pmca_pools.engagement_record(store, payload)
        if state["count"] == 0:
            state["first_seq"] = rec["seq"]
        state["count"] += 1
        state["last_seq"] = rec["seq"]
        state["segment_head"] = rec["hash"]
        if state["count"] >= _SEGMENT_LIMIT:
            engagement_close(store, space=space, reason="count")
        return rec


def _seal_analysis_entry(handle, store, space, sentence, closure,
                         result, before_checksum, after_checksum,
                         segment_id):
    """The one shared entry builder for analysis processes
    (atomic-entry pack; the _engagement_payload sharing
    precedent): journal the result's trace entries as steps, in
    emission order, and seal the entry with outcome = the
    output object _engagement_payload builds, verbatim.

    Segment membership is bound AT SEAL, inside ONE hold of the
    entry store's reentrant lock (spec 6.1: composite callers
    may hold the store lock across several appends as one
    atomic step -- the discipline the pool path always had,
    where _append_engagement binds membership at append under
    one lock). Inside the hold the builder resolves the CURRENT
    open segment from the segment state, stamps the sealed
    entry's starting_state segment_id and checkpoint_entry_seq
    from that resolution (the journal's entry_open keeps the
    begun state as its record; state_before_checksum stays the
    process's actual begin state), appends the entry, applies
    the segment bookkeeping (count, first/last seq -- ENTRY
    seqs on this path -- head = the entry's seal), and when the
    append brings the segment to the limit appends its
    segment_close (engagements = the actual count of process
    entries in the segment) and the next segment_open, all
    before the lock is released. A process therefore never
    seals into a segment that has already closed, and no close
    can under- or over-count. (The pre-fix shape bound
    membership at begin and fired the count in the seal's
    bookkeeping under the pool lock, a different critical
    section from the entry append: in-flight processes sealed
    into already-closed segments -- sealed battery case B5.)
    If no segment is open at seal time (a manual close landed
    while the process was in flight), one is opened inside the
    hold, so a sealing entry always lands in a live segment.
    Uncontended, the resolved segment is the begun segment and
    the appended entry stream is exactly the pre-fix stream.
    Returns the sealed entry."""
    payload = _engagement_payload(sentence, closure, result,
                                  before_checksum, after_checksum,
                                  segment_id)
    for trace_line in result["trace"]:
        handle.step("trace", trace_line)
    with _ENTRY_STORE.lock:
        state = _segment_state(store)
        if state["open_segment"] is not None \
                and state["open_source"] == "entry":
            seal_starting = {
                "segment_id": state["open_segment"],
                "checkpoint_entry_seq":
                    state["open_checkpoint_entry_seq"],
                "state_before_checksum": before_checksum}
        elif state["open_segment"] is None:
            _open_entry_segment(store, space, state)
            seal_starting = {
                "segment_id": state["open_segment"],
                "checkpoint_entry_seq":
                    state["open_checkpoint_entry_seq"],
                "state_before_checksum": before_checksum}
        else:
            # A pool segment is open (the adoption boundary).
            # The entry-path callers adopt -- closing the pool
            # segment and opening an entry segment -- before
            # beginning, so this is unreachable from them; stamp
            # nothing and let the entry carry the journal's
            # begun state, exactly as before the seal-time
            # binding.
            seal_starting = None
        entry = handle.seal(
            outcome=payload["output"],
            ending_state={"state_after_checksum": after_checksum},
            starting_state=seal_starting)
        if seal_starting is not None \
                and state["open_source"] == "entry" \
                and state["open_segment"] \
                == seal_starting["segment_id"]:
            if state["count"] == 0:
                state["first_seq"] = entry["seq"]
            state["count"] += 1
            state["last_seq"] = entry["seq"]
            state["segment_head"] = entry["seal"]
            if state["count"] >= _SEGMENT_LIMIT:
                _close_entry_segment(store, space, state, "count")
                _open_entry_segment(store, space, state)
    return entry


def _analyze_logged_entry(sentence, closure, space, store):
    """analyze_logged on the entry path: the analysis runs
    INSIDE the entry lifecycle (spec 4.4). The entry is begun
    (input {sentence, closure}, durable) before analyze() runs;
    an exception from the analysis files the process incomplete
    immediately and propagates unchanged. The caller is
    acknowledged only after the entry is durable. Returns
    (result, sealed_entry)."""
    space = _effective_space(space)
    segment_id = engagement_open(store, space=space)
    state = _segment_state(store)
    before = _fingerprint(space)
    starting_state = {
        "segment_id": segment_id,
        "checkpoint_entry_seq": state["open_checkpoint_entry_seq"],
        "state_before_checksum": before}
    with _ENTRY_STORE.process(
            "analysis", {"sentence": sentence, "closure": closure},
            _entry_environment(), starting_state) as handle:
        result = analyze(sentence, closure=closure, space=space)
        after = _fingerprint(space)
        entry = _seal_analysis_entry(
            handle, store, space, sentence, closure, result,
            before, after, segment_id)
    return result, entry


def _log_result_entry(result, sentence, closure, space, store):
    """log_result on the entry path: same lifecycle as
    _analyze_logged_entry, for a result computed elsewhere.
    Both fingerprints are taken at call time, as on the pool
    path. Returns the sealed entry."""
    space = _effective_space(space)
    segment_id = engagement_open(store, space=space)
    state = _segment_state(store)
    before = _fingerprint(space)
    after = _fingerprint(space)
    starting_state = {
        "segment_id": segment_id,
        "checkpoint_entry_seq": state["open_checkpoint_entry_seq"],
        "state_before_checksum": before}
    with _ENTRY_STORE.process(
            "analysis", {"sentence": sentence, "closure": closure},
            _entry_environment(), starting_state) as handle:
        entry = _seal_analysis_entry(
            handle, store, space, sentence, closure, result,
            before, after, segment_id)
    return entry


def analyze_logged(sentence, closure=False, space=None, store=None):
    """analyze() with its engagement recorded. With store None,
    identical to analyze() and the record is None. Else: ensure
    a segment is open, fingerprint the effective space before,
    call analyze(), fingerprint after, append the engagement
    record, return (result, stored_record). The result is fully
    computed before any logging, so a logging failure (which
    propagates, loudly) can never alter it.

    With an entry store configured, the engagement is a sealed
    atomic entry instead of a pool record (see
    _analyze_logged_entry) and the returned record is the
    entry."""
    if store is None:
        return analyze(sentence, closure=closure, space=space), None
    if _ENTRY_STORE is not None:
        return _analyze_logged_entry(sentence, closure, space,
                                     store)
    space = _effective_space(space)
    engagement_open(store, space=space)
    before = _fingerprint(space)
    result = analyze(sentence, closure=closure, space=space)
    after = _fingerprint(space)
    rec = _append_engagement(store, space, sentence, closure,
                             result, before, after)
    return result, rec


def log_result(result, sentence, closure=False, space=None,
               store=None):
    """Record an engagement for a result computed elsewhere (the
    app-facing path: the Space's GPU call returns a result, and
    logging happens on the CPU side from that result, never by
    re-running analyze()). The before/after fingerprints are both
    taken at call time; for a pure analysis they are equal under
    the record filter, which is the design's invariant. Returns
    the stored record, or None when store is None.

    With an entry store configured, the engagement is a sealed
    atomic entry instead (see _log_result_entry) and the entry
    is returned."""
    if store is None:
        return None
    if _ENTRY_STORE is not None:
        return _log_result_entry(result, sentence, closure, space,
                                 store)
    space = _effective_space(space)
    before = _fingerprint(space)
    after = _fingerprint(space)
    return _append_engagement(store, space, sentence, closure,
                              result, before, after)


# ---------------------------------------------------------------------
# Test suite. Cases per the analyzer design record, 2026-10-20.
# ---------------------------------------------------------------------

def run_suite():
    """Run all test cases. Return list of result tuples."""
    results = []

    # Case 1: breaks + cup. Replaceable, coefficient 1. Harm 1.
    r = analyze("He breaks the cup.")
    results.append(("1_breaks_cup",
                    (r["verdict"], r["omega"], r["harm"], r["gate"],
                     r["parameters"]["consequence"])))

    # Case 2: breaks + spine. Non-replaceable, coefficient 10,
    # weight 10. Harm 100. Single edge at the gate: REFUSE.
    r = analyze("He breaks her spine.")
    results.append(("2_breaks_spine",
                    (r["verdict"], r["omega"], r["harm"])))

    # Case 3: child target, gate true. Omega lock, child_gate.
    r = analyze("He deliberately poisons the child.")
    results.append(("3_poisons_child",
                    (r["verdict"], r["omega"], r["lock_reason"], r["gate"])))

    # Case 4: holds has an empty coefficient table. The omega
    # lock must fire before that table can HOLD.
    r = analyze("He deliberately holds the child.")
    results.append(("4_holds_child",
                    (r["verdict"], r["omega"], r["lock_reason"])))

    # Case 5: crushes on a person target. Omega lock,
    # death_of_person. First clause (rolled, hill) is harmless.
    r = analyze("The boulder rolled down the hill and crushed a man.")
    results.append(("5_boulder_crushes_man",
                    (r["verdict"], r["omega"], r["lock_reason"])))

    # Case 6a: no change of state, high harm, still open.
    # HOLD, the waiting state, harm recorded.
    r = analyze("The boulder rolled toward the man.")
    results.append(("6a_rolled_toward_man_open",
                    (r["verdict"], r["hold_reason"], r["harm"])))

    # Case 6b: same sentence at closure. Omega lock, the
    # closing state.
    r = analyze("The boulder rolled toward the man.", closure=True)
    results.append(("6b_rolled_toward_man_closure",
                    (r["verdict"], r["omega"], r["lock_reason"])))

    # Case 7: rolled + hill. Coefficient 0. No consequence,
    # harm below the gate. APPROVE, consequence None.
    r = analyze("The boulder rolled down the hill.")
    results.append(("7_rolled_down_hill",
                    (r["verdict"], r["harm"],
                     r["parameters"]["consequence"])))

    # Case 8: two edges, cascade. 2 in full, then 1 divided
    # by 2. Total 5/2, below the gate. APPROVE.
    r = analyze("He breaks the cup and cracks the window.")
    results.append(("8_breaks_cup_cracks_window",
                    (r["verdict"], r["harm"])))

    # Case 9: unknown word. HOLD, naming the word.
    r = analyze("He breaks the zorblat.")
    results.append(("9_breaks_zorblat",
                    (r["verdict"], r["hold_reason"])))

    # Case 10: breaks has no repairable cell, window is
    # repairable. HOLD, coefficient_missing.
    r = analyze("He breaks the window.")
    results.append(("10_breaks_window",
                    (r["verdict"], r["hold_reason"])))

    # Case 11: knowingly sets the gate. Took + cup is 0 harm,
    # no consequence, below the gate. APPROVE.
    r = analyze("She knowingly took the cup.")
    results.append(("11_knowingly_took_cup",
                    (r["verdict"], r["harm"], r["gate"])))

    return results


def check_suite(results):
    """Check results against expected values. Return list of
    (name, passed, actual, want) tuples."""
    expected = {
        "1_breaks_cup": ("APPROVE", False, (1, 1), False, "cup broken"),
        "2_breaks_spine": ("REFUSE", False, (100, 1)),
        "3_poisons_child": ("REFUSE", True, "child_gate", True),
        "4_holds_child": ("REFUSE", True, "child_gate"),
        "5_boulder_crushes_man": ("REFUSE", True, "death_of_person"),
        "6a_rolled_toward_man_open": ("HOLD",
                                      "consequence_missing_high_harm",
                                      (60, 1)),
        "6b_rolled_toward_man_closure": ("REFUSE", True,
                                         "closure_unknown_high_harm"),
        "7_rolled_down_hill": ("APPROVE", (0, 1), None),
        "8_breaks_cup_cracks_window": ("APPROVE", (5, 2)),
        "9_breaks_zorblat": ("HOLD", "unknown_word:zorblat"),
        "10_breaks_window": ("HOLD", "coefficient_missing"),
        "11_knowingly_took_cup": ("APPROVE", (0, 1), True),
    }
    checks = []
    for name, actual in results:
        want = expected[name]
        passed = (actual == want)
        checks.append((name, passed, actual, want))
    return checks


def main():
    """Run suite twice, verify determinism, print results."""
    run1 = run_suite()
    run2 = run_suite()
    deterministic = (run1 == run2)
    checks = check_suite(run1)
    passed_count = 0
    for name, passed, actual, want in checks:
        if passed:
            passed_count = passed_count + 1
            print("PASS " + name + " -> " + str(actual))
        else:
            print("FAIL " + name + " -> got " + str(actual)
                  + " want " + str(want))
    print("Passed: " + str(passed_count) + " of " + str(len(checks)))
    print("Determinism (two runs identical): " + str(deterministic))
    if passed_count == len(checks) and deterministic:
        print("ALL CHECKS PASS")
    else:
        print("CHECKS FAILED")


# ---------------------------------------------------------------------
# Wiring checks (stage 4). Separate from the original suite:
# the 12 cases above are untouched. Each check uses a fresh own
# space. Run with: python3 pmca_sentence_analyzer.py --wiring
# ---------------------------------------------------------------------

def run_wiring_checks():
    """Run the graphspace wiring checks. Return list of
    (name, passed, detail) tuples."""
    checks = []

    def fresh():
        return pmca_graphspace.GraphSpace("own")

    def own_noted(result):
        for line in result["trace"]:
            if "provenance: own" in line:
                return True
        return False

    # w1: a recorded analysis lands at the expected dist_address
    # home of the sentence's first content word ("breaks"), as
    # an analysis_record entry with provenance recorded.
    sp = fresh()
    r = analyze("He breaks the cup.", space=sp)
    rec_keys = [k for k in sp.address_book
                if k.startswith("analysis:")]
    home = None
    entry = None
    if len(rec_keys) == 1:
        home = sp.home_of(rec_keys[0])
        entry = sp.lookup(rec_keys[0])
    ok = (len(rec_keys) == 1
          and home == pmca_graphspace.dist_address("breaks")
          and entry is not None
          and entry["kind"] == "analysis_record"
          and entry["verdict"] == "APPROVE"
          and entry["parameters"]["consequence"] == "cup broken"
          and entry["provenance"] == "analyzer"
          and sp.graphs[home]["meta"]["system"] == "own"
          and sp.graphs[home]["meta"]["created_by"] == "system")
    checks.append(("w1_record_home", ok, str(home)))

    # w2: an identifier absent from the seed tables HOLDs before
    # register_logic and resolves from the own space after,
    # with provenance 'own' in the trace. (Case 10's pair:
    # breaks has no repairable cell.)
    sp = fresh()
    before = analyze("He breaks the window.", space=sp)
    register_logic("breaks:repairable", 1, space=sp)
    after = analyze("He breaks the window.", space=sp)
    ok = (before["verdict"] == "HOLD"
          and before["hold_reason"] == "coefficient_missing"
          and not own_noted(before)
          and after["verdict"] == "APPROVE"
          and after["harm"] == (2, 1)
          and after["parameters"]["consequence"] == "window broken"
          and own_noted(after))
    checks.append(("w2_own_logic_resolves", ok,
                   str((before["verdict"], after["verdict"],
                        after["harm"], own_noted(after)))))

    # w3: seed precedence. A key present in both the seed tables
    # and the own space resolves from the seed: the registered
    # 99 must not move case 1's harm, and no 'own' note appears.
    sp = fresh()
    register_logic("breaks:replaceable", 99, space=sp)
    r = analyze("He breaks the cup.", space=sp)
    ok = (r["verdict"] == "APPROVE" and r["harm"] == (1, 1)
          and not own_noted(r))
    checks.append(("w3_seed_precedence", ok,
                   str((r["verdict"], r["harm"], own_noted(r)))))

    # w4: the analyzer never registers logic from its own
    # analyses. After analyses (including a seed-missing HOLD),
    # the own space holds records only, no logic entries.
    sp = fresh()
    analyze("He breaks the window.", space=sp)
    analyze("He breaks the cup.", space=sp)
    logic_entries = 0
    for graph in sp.graphs.values():
        for e in graph["entries"].values():
            if isinstance(e, dict) and e.get("kind") == "own_logic":
                logic_entries = logic_entries + 1
    ok = (logic_entries == 0
          and sp.lookup("breaks:repairable") is None)
    checks.append(("w4_no_self_registration", ok,
                   str(logic_entries)))

    # w5: omega locks are unaffected by registered logic. The
    # child gate fires before any coefficient lookup, so a
    # registered cell for "holds" cannot unlock case 4.
    sp = fresh()
    register_logic("holds:non_replaceable", 1, space=sp)
    r = analyze("He deliberately holds the child.", space=sp)
    ok = (r["verdict"] == "REFUSE" and r["omega"]
          and r["lock_reason"] == "child_gate")
    checks.append(("w5_omega_unaffected", ok,
                   str((r["verdict"], r["lock_reason"]))))

    # w6: an empty own space behaves identically: full result
    # dicts equal the default-space path for sample sentences.
    sp = fresh()
    same = True
    for s in ("He breaks the cup.",
              "The boulder rolled down the hill.",
              "He breaks the window."):
        if analyze(s, space=sp) != analyze(s):
            same = False
    checks.append(("w6_empty_space_identity", same, str(same)))

    # w7: determinism. One fixed wiring scenario, run twice on
    # fresh spaces: outcomes and the spaces' canonical bytes
    # must be identical.
    def scenario():
        sp = fresh()
        out = []
        out.append(analyze("He breaks the window.", space=sp)["verdict"])
        register_logic("breaks:repairable", 1, space=sp)
        r = analyze("He breaks the window.", space=sp)
        out.append((r["verdict"], r["harm"]))
        out.append(analyze("He breaks the cup.", space=sp)["harm"])
        return out, sp.canonical_bytes()

    out1, bytes1 = scenario()
    out2, bytes2 = scenario()
    ok = (out1 == out2 and bytes1 == bytes2)
    checks.append(("w7_wiring_determinism", ok, str(out1)))

    return checks


def main_wiring():
    """Run wiring checks twice, verify determinism, print."""
    run1 = run_wiring_checks()
    run2 = run_wiring_checks()
    deterministic = (run1 == run2)
    passed_count = 0
    for name, passed, detail in run1:
        if passed:
            passed_count = passed_count + 1
            print("PASS " + name + " -> " + detail)
        else:
            print("FAIL " + name + " -> " + detail)
    print("Passed: " + str(passed_count) + " of " + str(len(run1)))
    print("Determinism (two runs identical): " + str(deterministic))
    if passed_count == len(run1) and deterministic:
        print("ALL WIRING CHECKS PASS")
    else:
        print("WIRING CHECKS FAILED")


# ---------------------------------------------------------------------
# Dictionary checks (2026-10-20 build, stage 1). Separate from
# the original suite and the wiring checks: both are untouched.
# A fixture dictionary (a tiny sqlite DB with the production
# schema and GCIDE-style entries) stands in for the real 50 MB
# Webster DB, which tests never use.
# Run with: python3 pmca_sentence_analyzer.py --dictionary
# ---------------------------------------------------------------------

def _build_fixture_db(dirpath):
    """Build the fixture dictionary in dirpath and return its
    path. Schema is the production one (defs: word -> JSON
    list of definition strings). Verb entries carry definitions
    beginning "To " (the GCIDE infinitive convention); the cat
    entry's definitions do not."""
    import sqlite3
    path = os.path.join(dirpath, "webster1913.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE defs (word TEXT PRIMARY KEY, defs TEXT)")
    entries = {
        "cat": ["A well-known domesticated carnivorous mammal, "
                "kept as a pet and for catching mice.",
                "Any animal of the feline kind."],
        "jump": ["To spring free from the ground by the muscular "
                 "act of pushing the legs; to leap.",
                 "To pass by a spring or leap; to overleap."],
        "bite": ["To seize with the teeth, so that they enter "
                 "and nip the thing seized.",
                 "To cause sharp pain, or smarting, to."],
    }
    for word, defs in entries.items():
        con.execute("INSERT INTO defs (word, defs) VALUES (?, ?)",
                    (word, json.dumps(defs)))
    con.commit()
    con.close()
    return path


def run_dictionary_checks():
    """Run the dictionary wiring checks. Return list of
    (name, passed, detail) tuples."""
    import tempfile

    checks = []
    tmp = tempfile.mkdtemp(prefix="pmca_dict_fixture_")
    fixture = _build_fixture_db(tmp)

    suite_sentences = [
        "He breaks the cup.",
        "He breaks her spine.",
        "He deliberately poisons the child.",
        "He deliberately holds the child.",
        "The boulder rolled down the hill and crushed a man.",
        "The boulder rolled toward the man.",
        "The boulder rolled down the hill.",
        "He breaks the cup and cracks the window.",
        "He breaks the zorblat.",
        "He breaks the window.",
        "She knowingly took the cup.",
    ]

    # d1: dictionary off, the existing suite passes untouched.
    configure_dictionary(None)
    suite_checks = check_suite(run_suite())
    ok = all(passed for _, passed, _, _ in suite_checks)
    checks.append(("dict_off_suite_passes", ok,
                   str(sum(1 for _, p, _, _ in suite_checks if p))
                   + " of " + str(len(suite_checks))))

    # d2: dictionary off, full results for the suite sentences.
    off = {}
    for s in suite_sentences:
        off[s] = analyze(s)

    # d3: fixture configured, the same sentences give identical
    # full results (the fixture's words appear in none of them;
    # "zorblat" resolves in neither tier and HOLDs as before).
    # AMENDED (FINDER BUILD, 2026-11-01): the identity is read
    # over the pre-finder result surface — the result minus the
    # "finder" carriage key and the trace minus its finder:
    # lines. The finder's records are dictionary-configuration
    # dependent BY DESIGN (spec section 5.2.5: with the tier
    # off, dictionary-sourced words record UNKNOWN with gap
    # dictionary_tier_off; with a fixture on, they classify
    # from the fixture's entries), so full-dict identity across
    # configurations cannot hold for the finder surface itself.
    # Everything the check was written to protect — verdicts,
    # harm, parameters, edges, and every pre-finder trace line —
    # is still compared byte-for-byte.
    configure_dictionary(fixture)

    def _pre_finder_surface(result):
        surface = {k: v for k, v in result.items()
                   if k != "finder"}
        surface["trace"] = [line for line in result["trace"]
                            if not line.startswith("finder: ")]
        return surface

    same = True
    for s in suite_sentences:
        if _pre_finder_surface(analyze(s)) \
                != _pre_finder_surface(off[s]):
            same = False
    checks.append(("dict_on_seed_identity", same, str(same)))

    # d4: "the cat jumps". Dictionary subject + dictionary verb,
    # no target: the clause stands with no edge, harm (0,1).
    # B-2 (ruled 2026-10-30) re-rules this case: no edges is not
    # an approval — the verdict is HOLD, hold_reason
    # no_edges_no_event. Expectation amended under the ruling's
    # named consequences (the old APPROVE expectation is
    # superseded by the ruling, recorded here, not silently
    # dropped). Both resolutions are still traced, cat exact,
    # jumps resolved to its headword jump.
    r = analyze("the cat jumps")
    ok = (r["verdict"] == "HOLD" and r["harm"] == (0, 1)
          and r["hold_reason"] == "no_edges_no_event"
          and r["parameters"]["cause"] == "cat"
          and r["parameters"]["action"] == "jump"
          and r["parameters"]["target"] is None
          and "dictionary: cat (webster1913, form: other)"
          in r["trace"]
          and "dictionary: jumps -> jump (webster1913, form: verb)"
          in r["trace"])
    checks.append(("dict_cat_jumps", ok,
                   str((r["verdict"], r["harm"], r["parameters"]))))

    # d5: a dictionary target has definitions but no seed
    # structure: HOLD target_structure_missing, no omega.
    r = analyze("the man breaks the cat")
    ok = (r["verdict"] == "HOLD"
          and r["hold_reason"] == "target_structure_missing:cat"
          and not r["omega"]
          and "dictionary target: cat has definitions but no seed "
              "structure (weight, replaceability unresolved)"
          in r["trace"])
    checks.append(("dict_target_structure_missing", ok,
                   str((r["verdict"], r["hold_reason"]))))

    # d6: a dictionary verb against a seed target: omega checks
    # run on the target's (empty) flags, the virtual entry's
    # empty table misses, the own space cannot hold it: HOLD
    # coefficient_missing.
    r = analyze("the man bites the cup")
    ok = (r["verdict"] == "HOLD"
          and r["hold_reason"] == "coefficient_missing"
          and "dictionary: bites -> bite (webster1913, form: verb)"
          in r["trace"])
    checks.append(("dict_coefficient_missing", ok,
                   str((r["verdict"], r["hold_reason"]))))

    # d7: a word in neither tier HOLDs unknown_word, exactly as
    # with the dictionary off (the scan reaches zorblat before
    # jumps, so no resolution is recorded first).
    r = analyze("the zorblat jumps")
    ok = (r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat")
    checks.append(("dict_unknown_word", ok,
                   str((r["verdict"], r["hold_reason"]))))

    # d8: the analysis of a dictionary sentence records at the
    # dist_address home of its real first content word ("cat"),
    # not at the fallback first token ("the").
    sp = pmca_graphspace.GraphSpace("own")
    analyze("the cat jumps", space=sp)
    rec_keys = [k for k in sp.address_book
                if k.startswith("analysis:")]
    home = None
    if len(rec_keys) == 1:
        home = sp.home_of(rec_keys[0])
    ok = (home == pmca_graphspace.dist_address("cat"))
    checks.append(("dict_record_home", ok, str(home)))

    # d9: with the fixture configured, the code manifest names
    # the dictionary, valued by the fixture file's sha256; the
    # four module keys are still present.
    with open(fixture, "rb") as f:
        fixture_hash = hashlib.sha256(f.read()).hexdigest()
    manifest = _code_manifest()
    ok = (manifest.get("dictionary") == fixture_hash
          and all(name in manifest for name in _MANIFEST_MODULES))
    checks.append(("dict_manifest_present", ok,
                   str("dictionary" in manifest)))

    # d10: tier off again, the manifest omits the key entirely
    # (pre-wiring records and manifests are untouched).
    configure_dictionary(None)
    manifest = _code_manifest()
    ok = ("dictionary" not in manifest
          and all(name in manifest for name in _MANIFEST_MODULES))
    checks.append(("dict_manifest_absent", ok,
                   str("dictionary" in manifest)))

    return checks


def main_dictionary():
    """Run dictionary checks twice, verify determinism, print."""
    run1 = run_dictionary_checks()
    run2 = run_dictionary_checks()
    deterministic = (run1 == run2)
    passed_count = 0
    for name, passed, detail in run1:
        if passed:
            passed_count = passed_count + 1
            print("PASS " + name + " -> " + detail)
        else:
            print("FAIL " + name + " -> " + detail)
    print("Passed: " + str(passed_count) + " of " + str(len(run1)))
    print("Determinism (two runs identical): " + str(deterministic))
    if passed_count == len(run1) and deterministic:
        print("ALL DICTIONARY CHECKS PASS")
    else:
        print("DICTIONARY CHECKS FAILED")


if __name__ == "__main__":
    if "--dictionary" in sys.argv:
        main_dictionary()
    elif "--wiring" in sys.argv:
        main_wiring()
    else:
        main()


# ---------------------------------------------------------------------------
# ISA wiring (2026-10-28, is_a build): an OPTIONAL secondary
# recognition lookup over the is_a genus store (pmca_isa). Jonathan's
# design: a word is recognized once on its own entry and again
# through the genus it sits under; only VERIFIED referrals confer
# recognition. analyze() never calls this — verdict paths are
# untouched; the lookup is offered to callers that want it.


def recognize_via_genus(word):
    """Secondary recognition via the word's genus (pmca_isa).

    Returns pmca_isa.recognize(word)'s record, or None when the
    is_a module or its store is unavailable. Never raises into a
    caller, never affects analyze()."""
    try:
        import pmca_isa
        return pmca_isa.recognize(word)
    except Exception:
        return None
