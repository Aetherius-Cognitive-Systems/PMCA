"""pmca_parse_guards.py — the SHARED parse-truthfulness layer for PMCA (P0).

Design: Jonathan Wayne Fleuren (the builder). Mechanism: the nine
structural fractures and the P0 guard/genitive specifications were
found and drafted by Gemini 4 Argon (2026-10-25), adjudicated by
Alder, ruled by Jonathan. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only.

Why this module exists (Jonathan's system-wide fix rule,
2026-10-25): a solved gap must work across the whole system, not
only in the zone where it was found. The five pre-binding guards
and the genitive rule therefore live HERE, in one shared layer,
and every parsing surface consumes them: the finder microscripts,
the sentence analyzer's parse path, the population token stream,
and through the primary finders the write-in encounter path. No
consumer carries a private copy of a guard.

Non-LLM purity (standing gate, Jonathan 2026-10-25): every
mechanism in this module is a deterministic rule over closed
grammar lists, dictionary attestation (pmca_dictionary, Webster
1913 tier), and morphology attestation (pmca_morphology_finder).
No probability, no similarity, no fitted parameters, no
statistical tagging, no generated completion. "POS" here means
the grammar microscripts' own determinations. A guard failure is
a HOLD with a named diagnostic tag, never a guessed role.

The guards (spec: p0-build-spec-2026-10-25.md):

1. POS ambiguity is enforced by the consumers' own ambiguity
   machinery (multiple valid splits HOLD in the microscripts);
   this module adds the geometry screens below and never lets a
   span token's use be asserted by elimination.
2. Multi-verb / unsplit clause: more than one attested finite
   verb in a segment the closed splitter list does not divide
   into single-verb segments -> HOLD multi_verb_unsegmented.
   Auxiliaries and modals are grammar tokens, not finite verbs
   for this count; two verb candidates ADJACENT in one span are
   competing parses of one clause (the consumers' ambiguity
   HOLD), not two clauses.
3. Passive geometry: AUX_BE + a verb-form token -> HOLD
   passive_geometry_unhandled_in_p0 (analyzer form; the finder
   microscripts carry the pre-existing equivalent tag
   passive_or_auxiliary_out_of_scope in their own scope code).
   No role inversion is attempted anywhere in P0.
4. Prepositional barrier: a preposition as the first
   non-article token after the verb, with no direct-object NP
   before it -> HOLD prepositional_barrier:<prep>. The
   prepositional object is never the verb's direct target.
5. Double NP / relative geometry: a relative pronoun token
   (who, whom, whose, which, that) anywhere -> HOLD
   non_svo_np_geometry. In P0 relative clauses HOLD wholesale;
   internal binding is a later capability build.

Standing locks: grammar words (modals, do/does/did auxiliaries,
negation) never fill content slots — in the finder microscripts
their presence HOLDs with the scope tag
passive_or_auxiliary_out_of_scope (grammar_lock_tag). A slot is
filled only by a use determined in this sequence; in particular
a sentence-final token whose morphology attestation is
adverbial_ly is an adverb, never a target (demote_adverb_target).

Genitive (spec section B): contraction exclusion by exact lookup
in a closed table; X's splits to possessor X after dictionary
attestation that X is a noun; Xs' splits after attested plural
verification; the phrase head after the clitic must be an
attested noun, else HOLD headless_or_non_noun_genitive:<X>'s.
Morphology owns the possessive FORM attestation
(pmca_morphology_finder.resolve_form already resolves "child's"
to entry child, form possessive); this module owns the clitic
geometry and the validation order.

Pack A additions (fix campaign 2026-10-26; evidence claims-03 /
claims-07, synthesis D1 / D2 / D22; Argon items F2 / F3 / F4):

6. Adverb slot guard (adverb_slot_check): an adverb-attested
   token (morphology adverbial_ly, or the closed unmarked-adverb
   lexeme list) as a noun-phrase head candidate -> HOLD
   adverb_in_subject_slot:<w> / adverb_in_target_slot:<w>. The
   P0 demote covered the final-target geometry only; "The wolf
   never eats the sheep." bound never as subject at every
   finder and the false binding promoted to VERIFIED.
7. Noun-adjunct guard (adjunct_check): a modifier without the
   measured adjective attestation -> HOLD
   ambiguous_modifier_noun_adjunct:<w> — the surfaces aligned
   on the adjective microscript's HOLD behaviour (D22).
Also in Pack A: _find_head returns the phrase head (the LAST
nominal of the genitive's phrase, not an adjective inside it);
IRREGULAR_PARTICIPLES extends the contraction participle test
beyond -ed / -en spellings; normalize() reports
"expanded_aux_be" so consumers meet an expanded auxiliary
("it's" -> "is") with their scope HOLD instead of a downstream
no-verb state. The analyzer-side Pack A fixes (phrase-head
binding, per-clause gate scoping, the "never" HOLD) live in
pmca_sentence_analyzer.py.
"""

import hashlib
import json
import os
import re
import sqlite3
import threading

import pmca_dictionary
import pmca_morphology_finder as _morph
import pmca_verb_capable as _vc

# Closed grammar lists. These are the grammar microscripts' own
# closed classes, stated once here for the shared layer.
CONTRACTIONS = {"it's", "that's", "what's", "who's", "there's",
                "where's", "when's", "why's", "how's", "he's",
                "she's", "let's"}
RELATIVE_PRONOUNS = {"who", "whom", "whose", "which", "that"}
MODALS = {"can", "could", "may", "might", "must", "shall",
          "should", "will", "would",
          # FINDER FIX WAVE (2026-11-01), W-4: "ought" added on
          # stored attestation only — Webster 1913's entry for
          # "ought" states in its own note: "Ought is now
          # chiefly employed as an auxiliary verb, expressing
          # fitness, expediency, propriety, moral obligation,
          # or the like, in the action or state indicated by
          # the principal verb." The modal class is this tier's
          # auxiliary subclass, so the attested auxiliary use
          # is recorded here, in the one list.
          "ought"}
AUX_BE = {"am", "is", "are", "was", "were", "be", "been", "being"}
AUX_HAVE = {"have", "has", "had"}
AUX_DO = {"do", "does", "did"}
NEGATION = {"not"}
# The negation markers the sentence analyzer's per-clause
# negation scan adjudicates (NEW-02, 2026-10-27): "not" via
# NEGATION / negated_target_check; "never" via the scan's
# clause arm. Grammar-tier closed-class markers, never content
# words: no surface may adjudicate them against the content
# dictionary or record them as unknown words, in either
# dictionary tier. Stated ONCE here; the analyzer's negation
# scan and its unknown-word scan both consult this set.
NEGATION_MARKERS = NEGATION | {"never"}
AUX_ALL = AUX_BE | AUX_HAVE | AUX_DO | MODALS
GRAMMAR_LOCK = MODALS | AUX_DO | NEGATION
# GRAM-1 (RULED 2026-10-28): the closed auxiliary class is
# grammar-tier in full. Auxiliaries belong to the rules of
# language, never to the content dictionary: no surface may
# adjudicate one against the dictionary or record it as an
# unknown word, in either dictionary tier — the jurisdiction
# NEW-02 established for the negation markers, extended to the
# whole class (the dictionary-off "does" unknown-word wart was
# the same category error). The fused negative forms are
# members in their own right: "doesn't" is one token to the
# tokenizer, and it is grammar, not content. Stated ONCE here;
# the analyzer's jurisdiction screens and its unknown-word
# scan consult these sets.
AUX_NT = {"don't", "doesn't", "didn't",
          "isn't", "aren't", "wasn't", "weren't",
          "haven't", "hasn't", "hadn't",
          "can't", "couldn't", "won't", "wouldn't", "shan't",
          "shouldn't", "mustn't", "mayn't", "mightn't",
          # A-1 / GRAM-1: "cannot" is the apostrophe-less fused
          # form of can+not — the one standard fused modal
          # written without an apostrophe, missing from every
          # closed list until this fix.
          "cannot"}
# The modal members of AUX_NT: a fused modal still marks its
# predication with a modal, so the (open, unruled) modal
# question applies to it exactly as to the plain form.
# "cannot" is a member on the same ground as "can't" (A-1).
MODAL_NT = {"can't", "couldn't", "won't", "wouldn't", "shan't",
            "shouldn't", "mustn't", "mayn't", "mightn't",
            "cannot"}
# The do-family members of AUX_NT: a fused do-form in a verb
# group carries the negation with it (see the analyzer's
# verb-group negation arm).
AUX_NT_DO = {"don't", "doesn't", "didn't"}
# Every token the grammar tier recognizes as an auxiliary,
# plain or fused: AUX_ALL plus the negative forms.
AUX_RECOGNIZED = AUX_ALL | AUX_NT
COORDINATORS = {"and", "or", "but"}
# FINDER BUILD (2026-11-01; spec-check fix F4): the wider closed
# sets the legacy finder microscripts carried as private copies
# move HERE, to the grammar tier's one home, copied verbatim
# from the legacy modules named in the finder spec's inventory
# (section 3.2). No consumer carries a private copy; the legacy
# modules keep their own literals as disclosed history, and the
# ONE FINDER (pmca_finder) reads every closed-class list from
# this module.
CONJUNCTIONS = {"and", "or", "but", "nor", "yet", "so", "for"}
SUBJECT_PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
OBJECT_PRONOUNS = {"me", "you", "him", "her", "it", "us", "them"}
PRONOUNS = SUBJECT_PRONOUNS | OBJECT_PRONOUNS
# FINDER FIX WAVE (2026-11-01), W-4: the independent possessive
# pronouns, added ONLY on stored attestation in the tree, one
# source named per item (no item is invented):
#   his   — Webster 1913, entry "his": "used as a pronominal
#           adjective or adjective pronoun"; also the analyzer
#           seed's POSSESSIVES {"his", "her"} and the proof
#           module's closed-class STOPWORDS.
#   hers  — pmca_proof STOPWORDS (closed-class pronoun block);
#           Webster 1913, entry "our" (note): "ours is used in
#           the same way as hers for her" — the possessive-form
#           use. (Its own entry in this DB build is polluted
#           with the she/he senses; the attestation above is
#           the clean one.)
#   mine  — Webster 1913, entry "my": "mine is used in the
#           predicate; as, the book is mine"; entry "mine"'s
#           own note ("mine in the navy ... it is mine");
#           pmca_proof STOPWORDS.
#   yours — Webster 1913, entry "yours" header: "pron. [Poss.
#           {Your} or {Yours}]"; pmca_proof STOPWORDS.
#   ours  — Webster 1913, entry "ours" header: "pron.; pl. of
#           I. [Poss. {Our} or {Ours}]"; pmca_proof STOPWORDS.
#   theirs — pmca_proof STOPWORDS (closed-class pronoun
#           block). (Its own Webster entry in this build is
#           polluted like hers; no possessive attestation
#           stands in the entry itself.)
# NOT added: "per" as a preposition — no stored source in the
# tree attests its class (its Webster entry in this build
# carries a single definition with no class marker, and no
# list in the tree contains it). Recorded as a gap item in
# the finder fix wave's build paper, not invented here.
POSSESSIVE_PRONOUNS = {"his", "hers", "mine", "yours", "ours",
                       "theirs"}
DUAL_CLASS = {"before", "after", "since"}
# The legacy interjection finder's dictionary-verified set,
# stored under a DISTINCT name beside INTERJECTION_TOKENS
# (below). Storing both names decides nothing about which
# governs: the Helm determination for the finder build
# (2026-11-01) names INTERJECTION_TOKENS the governing list;
# this set stands as the retired private copy, with the
# membership conflict on record (it holds "oho", which the
# governing list omits, and omits "wow" and kin, which the
# governing list holds).
INTERJECTIONS_VERIFIED = {"oh", "ah", "alas", "ha", "oho",
                          "hurrah"}
SUBORDINATORS = {"because", "if", "when", "while", "before",
                 "after", "since", "although", "though", "unless",
                 "until"}
SPLITTERS = COORDINATORS | SUBORDINATORS
# The preposition microscript's closed list (pmca_preposition_finder).
PREPOSITIONS = {"of", "in", "on", "at", "by", "for", "with",
                "to", "from", "over", "under", "after", "before",
                "between", "among", "through", "against", "into",
                "upon", "near", "about"}
ARTICLES = {"the", "a", "an"}

# Pack A (2026-10-26; claims-03 W1/W2, claims-07 section 2.1): the
# closed class of UNMARKED adverb lexemes — frequency / time /
# manner adverbs that carry noun senses in Webster 1913 and no
# morphology attestation as adverbs, so a phrase parse that takes
# one as a noun-phrase head is reading an ambiguous token as a
# noun. A slot filled from this list is never determined; the
# slot guards below HOLD. (-ly adverbs need no list entry: the
# morphology attestation adverbial_ly determines their use.)
ADVERB_SLOT_LEXEMES = {"never", "always", "often", "rarely",
                       "seldom", "now", "well", "hard", "fast",
                       "ever", "sometimes", "usually",
                       "frequently", "occasionally", "again",
                       "still", "already", "just", "also", "too",
                       "then", "here", "there", "today",
                       "yesterday", "tomorrow"}

# Pack A (claims-07 2.2 fallout): interjection tokens are clause
# furniture, never attributive modifiers. A closed list for the
# adjunct guard's skip rule ("Oh, the wolf howls." — "oh" opens
# the clause; it does not modify "wolf").
INTERJECTION_TOKENS = {"oh", "alas", "ha", "wow", "hey", "ah",
                       "ouch", "hurrah", "bravo", "eh", "psst",
                       "shh", "hmm", "lo", "behold"}

# Pack A (Argon F4, adjudicated 2026-10-26): irregular past
# participles whose spelling carries no -ed / -en signal, as a
# closed grammar list. A contraction followed by one of these is
# ambiguous between "is" and "has" exactly as with a regular
# participle; see _participle_candidate.
IRREGULAR_PARTICIPLES = {"gone", "done", "hurt", "cut", "run",
                         "built", "made", "sent", "lost", "held",
                         "told", "taken", "given", "seen",
                         "known", "written", "eaten", "become",
                         "brought", "caught", "taught", "thought",
                         "found", "stood", "won", "met", "left",
                         "felt", "kept", "meant", "sold", "paid",
                         "said", "read", "put", "set", "hit",
                         "shut", "let", "sung", "drunk", "begun",
                         "swum", "borne", "born", "chosen",
                         "driven", "forgotten", "hidden", "risen",
                         "shaken", "spoken", "stolen", "worn",
                         "torn", "frozen", "bitten", "beaten",
                         "blown", "drawn", "flown", "grown",
                         "thrown"}

# First-definition openings that mark a genus phrase (a noun
# opening) in the stored GCIDE text — the adjective microscript's
# measured entry evidence, stated here once for the shared layer
# (see modifier_capable). WG2 2026-11-01: the former "To "
# member is REMOVED — it was Variant D, a "To "-test wearing an
# adjective's clothes (DICT-1 Part 1 retires it as a mechanism);
# the verb-form exclusion in modifier_capable now goes through
# the one shared decoding (pmca_verb_capable.is_verb_sense).
_NOMINAL_OPENINGS = ("A ", "An ", "The ", "Any ")

SCOPE_TAG = "passive_or_auxiliary_out_of_scope"


def dict_noun_capable(word):
    """Default oracle: the word has a noun subcoordinate in the
    stored dictionary. The ONE shared implementation
    (pmca_verb_capable.noun_capable, Candidacy Rule Set C-4;
    WG2 2026-11-01 — this copy's former RAW "To " test is
    RETIRED as a mechanism, DICT-1 Part 1)."""
    return _vc.noun_capable(word)


def dict_verb_capable(word):
    """Default oracle: the word has a verb subcoordinate in the
    stored dictionary. The ONE shared implementation
    (pmca_verb_capable.verb_capable, Candidacy Rule Set C-4;
    WG2 2026-11-01 — this copy's former RAW "To " test is
    RETIRED as a mechanism, DICT-1 Part 1)."""
    return _vc.verb_capable(word)


def _first_def_opening(word):
    """The first definition's opening text, with one leading
    parenthesized field label ("(Zoöl.)" and kin) stripped, or
    None when the word has no entry. (The adjective
    microscript's measurement primitive, hoisted in Pack A.)"""
    entry = pmca_dictionary.lookup(word)
    if entry is None or not entry["definitions"]:
        return None
    text = entry["definitions"][0].lstrip()
    if text.startswith("(") and ")" in text:
        text = text[text.index(")") + 1:].lstrip()
    return text


def modifier_capable(word):
    """Modifier CAPABILITY evidence — NOT adjective attestation.

    The adjective microscript's adjective_capable rule, stated
    once here so every surface judges a modifier by the same
    evidence: an entry exists whose first definition opens as
    a quality, not as a genus phrase and not as a verb form.
    This is a NEGATIVE test — it reads the ABSENCE of a nominal
    or verb opening — and absence is capability, not
    attestation. Measured 13/14 adjectives, 8/10 nouns on the
    microscript's labeled sample (the mass nouns "stone" and
    "meat" are its named false positives); over a deterministic
    headword sample of the whole store it returns True for
    ~60.5% of headwords (Wreck, R-1 scour 2026-11-02),
    including the verb "accumulate" and the preposition
    "across". Its consumers ask the capability question only:
    may this token serve as an attributive modifier rather
    than a noun adjunct (adjunct_check), may it fail to serve
    as a phrase head (the adverb-slot guards), does the
    adjective microscript admit it as a modifier candidate.
    No class ATTESTATION may rest on this test; attestation is
    adjective_attested below — a different claim, positive
    evidence only. (Fix wave "negative-evidence settles",
    2026-11-02: this test carried the name adjective_attested
    until that wave separated the two claims it was answering
    for.)"""
    opening = _first_def_opening(word)
    if opening is None:
        return False
    if _vc.is_verb_sense(opening):
        # The first definition is a verb-form sense under the
        # one shared decoding — not capability evidence. (The
        # retired Variant D reached this exclusion through the
        # "To " member of _NOMINAL_OPENINGS; WG2 2026-11-01.)
        return False
    return not opening.startswith(_NOMINAL_OPENINGS)


# The GCIDE adjective class marker as it survives in a stored
# entry: a header fragment the store's build left at the start
# of a definition string (the "accumulate" entry's fragment
# "'t), a. [L. accumulatus, ..." is the type specimen). The
# header zone is the first 60 characters of a definition
# string — the in-window convention of the R-1 scour's marker
# census (2026-11-02).
_ADJECTIVE_MARKER = ", a."
_MARKER_WINDOW = 60


def _seed_adjective(word):
    """Seed attestation for the adjective class: the seed's
    own entry for the word carries ADJ among its pos values
    (pmca_seed.DEMO_DICTIONARY). The seed is a stored attesting
    source; this reads its table and nothing else. (pmca_seed
    is imported lazily, the tree's convention where an import
    cycle is possible.)"""
    import pmca_seed
    w = (word or "").strip().lower()
    for entry in pmca_seed.DEMO_DICTIONARY:
        if entry.get("word") == w:
            return "ADJ" in (entry.get("pos") or [])
    return False


def _apparatus_owner(text, pos):
    """The headword named by the last GCIDE apparatus header
    (Headword \\pronunciation\\) that starts before position
    pos in a stored definition string, lowercased — or None
    when no apparatus header precedes pos. An apparatus
    header is a headword (letters, apostrophes, hyphens)
    followed by spaces and a backslash opening the
    pronunciation; the pronunciation's own closing backslash
    is preceded by a quote, never by spaces and a headword,
    so it cannot masquerade as a header.

    Fix wave 3 (B0, 2026-11-03; Gauge's wave-2 residual): a
    suffix headword's header LEADS with its hyphen
    ("-diecious \\-di*e\\"cious\\") — the hyphen is part of
    the headword, so a token opening with "-" followed by a
    letter is a header too. Before this, such headers were
    invisible here and a sibling's marker behind one
    (androdioecism / -diecism reading the -diecious string)
    still borrowed."""
    owner = None
    j = 0
    while True:
        j = text.find("\\", j, pos)
        if j < 0:
            return owner
        k = j
        spaces = 0
        while k > 0 and text[k - 1] == " ":
            k -= 1
            spaces += 1
        if spaces:
            e = k
            while k > 0 and (text[k - 1].isalpha()
                             or text[k - 1] in "'-"):
                k -= 1
            if k < e and (text[k].isalpha()
                          or (text[k] == "-" and k + 1 < e
                              and text[k + 1].isalpha())):
                owner = text[k:e].lower()
        j += 1


def _webster_adjective_marker(word):
    """Webster attestation for the adjective class: the GCIDE
    adjective class marker survives in the word's stored
    entry, IN A STRING WHOSE MARKER THIS WORD OWNS — ", a."
    inside the header zone of a stored definition string,
    where the marker is not claimed by a different headword's
    apparatus.

    Ownership (fix wave 2, 2026-11-02; Wreck WRECK2-2): the
    store's rows merge sibling headwords — GCIDE derivative
    and variant forms share one stored entry, their strings
    side by side — and the first version of this test read
    any string's marker as the row's own, so aimlessness
    attested on Aimless's string, abaca on Manila's (over
    abaca's own ", n." string), alder on Aller's apparatus
    merged mid-string. The mechanical ownership rule: a
    marker belongs to the headword named by the nearest
    apparatus header preceding it in its string; where that
    headword is not this word, the marker casts no vote for
    this word. A string with no apparatus header before the
    marker (a bare paraphrase, or a truncated fragment of
    the word's own header — accumulate's "'t), a. ..." is
    the type specimen) carries no foreign claim, and its
    marker is the row's own. Where the apparatus names a
    form the stored record cannot mechanically equate with
    this word (a variant spelling, one word of a multiword
    headword), ownership is undetermined and the marker
    does not attest."""
    entry = pmca_dictionary.lookup(word)
    if entry is None:
        return False
    own = (entry["headword"] or "").strip().lower()
    for text in entry["definitions"]:
        zone = text[:_MARKER_WINDOW]
        start = 0
        while True:
            p = zone.find(_ADJECTIVE_MARKER, start)
            if p < 0:
                break
            owner = _apparatus_owner(text, p)
            if owner is None or owner == own:
                return True
            start = p + 1
    return False


def adjective_attestation(word):
    """The stored positive evidence attesting the word's
    adjective class, as a short provenance label, or None when
    no stored source attests it. Exactly two sources attest,
    and the label names which one fired: the seed's ADJ entry,
    or the Webster class marker surviving in the stored entry.
    Positive evidence only — the absence of a nominal or verb
    opening is modifier CAPABILITY (modifier_capable above),
    never attestation."""
    if _seed_adjective(word):
        return ("seed attestation (pmca_seed.DEMO_DICTIONARY: "
                "pos ADJ)")
    if _webster_adjective_marker(word):
        return ("Webster 1913 class marker in the stored "
                "entry ('" + _ADJECTIVE_MARKER + "' in the "
                "header zone of a definition string this "
                "headword owns)")
    return None


def adjective_attested(word):
    """Positive-evidence adjective ATTESTATION: a stored
    source attests the word's adjective class (see
    adjective_attestation for the two sources and their
    labels). This name carried the negative opening test
    until the "negative-evidence settles" fix wave
    (2026-11-02) separated the claims; that test is
    modifier_capable now, and nothing in this attestation
    answer is an absence."""
    return adjective_attestation(word) is not None


def adverb_attested(word):
    """Determined-or-ambiguous adverb evidence for the slot
    guards: the word's morphology attestation is adverbial_ly
    (its use as an adverb is DETERMINED), or it is a member of
    the closed unmarked-adverb lexeme list (its adverb use is
    attested by the grammar list while its noun use is attested
    by the dictionary — an ambiguous token). Either way the
    word cannot be taken as a noun-phrase head; see
    adverb_slot_check for how the two cases differ."""
    res = _morph.resolve_form(word)
    if res["verdict"] == "OK" and res["form"] == "adverbial_ly":
        return True
    return word in ADVERB_SLOT_LEXEMES


def _adverbial_ly(word):
    """Only the determined case: morphology attests the word's
    form as adverbial_ly."""
    res = _morph.resolve_form(word)
    return res["verdict"] == "OK" and res["form"] == "adverbial_ly"


# ---------------------------------------------------------------------
# FINDER R-1 — paraphrase attestation (ruled by Jonathan
# 2026-11-02, rulings sitting; built 2026-11-03).
#
# The rule: a stored definition made wholly of one class's
# words counts as attestation of that class (uniform
# paraphrase only). The sitting's six points, as built:
#
# F1 GRAIN — the counting units are single words AND whole
# phrases. The definition's own segmentation (';' / '. ')
# presents the units; each unit is looked up WHOLE in the
# stored sources. A stored phrase is one unit of its class.
# A multiword unit no stored source holds is NOT decomposed
# into computed spans — it is an unclassified unit: it
# abstains, and it is recorded on the result (no silent
# gaps).
# F2 DEFINITION FIRST — only the gloss pieces vote. Pieces
# that introduce examples (first token "as"), pieces that
# name the headword (examples/apparatus about the word),
# and meta pieces (prose about the word: "<headword> is
# ...") cast no vote in the meaning test. If no proper
# definition can be found (no stored entry, no voting
# pieces, or no classified unit among them), the test
# falls back to the HOW: the example and headword-naming
# pieces, read as use evidence.
# F3 WIKTIONARY IDENTIFIES — for THIS TEST ONLY, a unit's
# class set is read from three stored sources: the seed's
# pos table, the unit's own surviving Webster class marker
# (ownership-ruled, _r1_marker_classes), and Wiktionary's
# stored pos rows (wiktionary.db). No other consumer reads
# wiktionary.db under this admission.
# F4 CONFLICTS — when the word's own surviving Webster tag
# and its paraphrase attestation disagree, NEITHER wins:
# the attestation is withheld and the conflict is recorded
# on the result. The record is a work order, not a shelf:
# the use-fallback, teaching, and later encounters remain
# open to the word (Jonathan's correction, same sitting).
# FALLBACK GRADE — a word settled by the use-fallback is
# UPDATED, NOT MINTED: CANDIDATE tier with the gap
# paraphrase_use_fallback:<word> on the record, never full
# attestation from the second door.
# MARKER CASE — marker reading is case-sensitive: ", N."
# is the proper-noun marker (a name), ", n." the
# common-noun marker (a thing-word); the two are different
# classes, and Wiktionary's "name" pos likewise classifies
# as "proper noun", never collapsed into "noun".
#
# Qualification (the scour operationalization the ruling
# was measured and presented under, Wreck 2026-11-02 §1.1,
# carried into the build as its stated core): a definition
# attests class C iff at least 2 of its voting units are
# classified and the intersection of all classified units'
# class sets is exactly {C}. An unclassified unit
# abstains — a gap in the evidence, recorded, never a
# verdict. An intersection of two or more classes attests
# neither (ambiguous).
#
# The Wiktionary store's path resolves the dictionary
# tier's way, minus a default of its own: the
# PMCA_WIKTIONARY_DB environment variable, else the file
# wiktionary.db sitting beside the configured Webster DB,
# else the identifier is unavailable and the test runs on
# seed + marker evidence alone (the result says which).
# Deterministic; stdlib only; read-only.
#
# FIX WAVE 3 — evidence hygiene (2026-11-03; Wreck's R-1
# attack families B1/B5/B2/B3/B6 + Gauge's wave-2 residual
# B0). The ruled logic above did not change; what it is FED
# did. (1) B5: Wiktionary evidence is admitted only for
# word-like units — numeral / letter / symbol rows are not
# word-class witnesses (the numeral 4's "preposition" row
# testified); and a "prep_phrase" row no longer testifies
# to the preposition class: it attests a phrase's FORM (a
# phrase built on a preposition), not the class it
# functions as — phrases that function as prepositions
# carry the store's "prep" pos (by means of) and keep
# testifying. (2) B1: citation and apparatus fragments
# never reach the vote — segmentation no longer shatters
# abbreviations ("esp.", "p. p.", "v. 4"), citation and
# derivative tails ("--Shak.", "-- {X}, adv.") and
# shattered author names are apparatus pieces, etymology
# elements are apparatus, quotation-open pieces are
# examples. (3) B2: the floor counts DISTINCT units — one
# unit in two pieces is one witness, not two. (4) B3/B6:
# definition OWNERSHIP is read — a headword attests only
# on its OWN definition text; text served from a parent /
# component / cross-referenced entry is recorded as
# borrowed (its pieces neither vote nor feed the
# use-fallback). (5) The fallback route now runs the same
# F4 comparison as the main route (Wreck §4.1: a fallback
# settlement that contradicts the word's own tag records
# the conflict instead of standing silently). B4
# (participial intersections) is recorded as FORK GAP
# FG-W3-1 in the wave-3 report: no core change — the one
# specimen whose collapse depended on a citation fragment
# (energical) is closed by B1; the clean specimen
# (nascent) follows from the ruled intersection core on
# stored verb classes, and any carve-out is unruled.
#
# Wave-3 completion (same wave, found by the wave's own
# after-sweep verification specimens): three more feeds
# cleaned under the same families. (a) B1: pieces inside
# an etymology/usage bracket span ("[From F. dragoman,
# ... Ar. ...]") are apparatus — the bracket is stored
# apparatus wherever it sits in the element, not only
# when the element opens with it; and a piece whose whole
# unit is one apparatus token (see / cf / pref / ar / ...)
# is apparatus — the truncated tail of a cross-reference
# ("...pref. a- + web. See") paraphrases nothing. (b) B3:
# the ownership read is completed store-wide — a whole
# element standing verbatim in at least two OTHER stored
# rows is shared text, not this headword's own (the AT /
# BY / ON articles and the store's shifted entry blocks
# stand under whole families of headwords), UNLESS this
# headword is the source those rows borrowed from (it is
# a nominated parent of a holder: per stands in per
# annum / per cent / per centum, and per's own text stays
# per's). A borrower is NAMED only on a directional
# witness: a nominated parent holding the element, a
# holder that is a token of the headword (on't -> on),
# or the token the headword shares with at least two
# holders (day by day -> by). Where no witness shows
# the direction (the shifted blocks: one entry's text
# standing under dozens of headwords), the piece is
# recorded "shared" — not this headword's own, source
# not mechanically shown, no name claimed.

_R1_WIKTIONARY_POS = {
    "noun": "noun", "verb": "verb", "adj": "adjective",
    "adv": "adverb", "prep": "preposition",
    "adv_phrase": "adverb",
    "conj": "conjunction", "pron": "pronoun",
    "intj": "interjection", "name": "proper noun",
}
# Wiktionary pos values outside this map (det, particle,
# num, phrase, proverb, prefix, suffix, contraction, ...)
# cast no vote: they name no class in the marker universe.
# "prep_phrase" is outside the map since fix wave 3 (B5):
# it names a phrase's form, not a class it functions as.

_R1_SEED_POS = {"NOUN": "noun", "VERB": "verb",
                "ADJ": "adjective", "ADV": "adverb",
                "PREP": "preposition", "CONJ": "conjunction",
                "PRON": "pronoun"}
# Seed pos values outside this map (DET, WH, AUX, QUAL,
# REL) cast no vote — the scour's recorded mapping.

# Webster class markers, matched as CASE-SENSITIVE literals
# (marker-case point): ", N." is not ", n.".
_R1_MARKERS = ((", N.", "proper noun"), (", n.", "noun"),
               (", a.", "adjective"), (", adv.", "adverb"),
               (", prep.", "preposition"),
               (", pron.", "pronoun"),
               (", interj.", "interjection"),
               (", conj.", "conjunction"),
               (", v. t.", "verb"), (", v. i.", "verb"),
               (", v.", "verb"))

_r1_wikt_conn = None
_r1_wikt_path = None
_r1_wikt_lock = threading.Lock()
_r1_unit_cache = {}
_r1_marker_cache = {}
_r1_web_conn = None
_r1_web_path = None
_r1_webster_cache = {}
_r1_family_cache = {}
_r1_census_cache = {}
_r1_census_path = None


def _r1_wiktionary_path():
    """The Wiktionary store's path, or None when it cannot
    be found: the PMCA_WIKTIONARY_DB environment variable,
    else wiktionary.db beside the configured Webster DB
    (the stores live together), else None."""
    env = os.environ.get("PMCA_WIKTIONARY_DB")
    if env:
        return env if os.path.isfile(env) else None
    dbp = pmca_dictionary.db_path()
    if dbp:
        sib = os.path.join(os.path.dirname(dbp),
                           "wiktionary.db")
        if os.path.isfile(sib):
            return sib
    return None


def _r1_wiktionary_pos(unit):
    """The pos values Wiktionary stores for the exact unit
    (a word or a whole phrase — the store holds both), as
    a list; [] when the unit is absent; None when the
    store itself is unavailable to this test."""
    global _r1_wikt_conn, _r1_wikt_path
    path = _r1_wiktionary_path()
    if path is None:
        return None
    with _r1_wikt_lock:
        try:
            if _r1_wikt_conn is None or _r1_wikt_path != path:
                if _r1_wikt_conn is not None:
                    _r1_wikt_conn.close()
                _r1_wikt_conn = sqlite3.connect(
                    "file:" + path + "?mode=ro", uri=True,
                    check_same_thread=False)
                _r1_wikt_path = path
            rows = _r1_wikt_conn.execute(
                "SELECT pos FROM entries WHERE word=?",
                (unit,)).fetchall()
        except sqlite3.Error:
            return None
    return [r[0] for r in rows]


def _r1_marker_classes(word):
    """The word's OWN surviving Webster class markers —
    every marker occurrence inside a stored definition
    string's header zone whose apparatus this word owns
    (the wave-2 ownership rule, _apparatus_owner), read
    case-sensitively across the whole marker table. A
    sibling's marker in a merged string casts no vote."""
    key = (word or "").strip().lower()
    if key in _r1_marker_cache:
        return _r1_marker_cache[key]
    entry = pmca_dictionary.lookup(key)
    out = set()
    if entry is not None:
        own = (entry["headword"] or "").strip().lower()
        for text in entry["definitions"]:
            zone = text[:_MARKER_WINDOW]
            for pat, cls in _R1_MARKERS:
                start = 0
                while True:
                    p = zone.find(pat, start)
                    if p < 0:
                        break
                    owner = _apparatus_owner(text, p)
                    if owner is None or owner == own:
                        out.add(cls)
                    start = p + 1
    _r1_marker_cache[key] = out
    return out


def _r1_unit_evidence(unit):
    """(classes, sources) for one counting unit, from the
    admitted stored sources: the seed's pos table (single
    words only — the seed holds no phrases), the unit's
    own Webster marker, and Wiktionary (F3). For a single
    word absent from Wiktionary, the dictionary tier's own
    inflection candidates are tried in their fixed order
    against the store. Unmapped pos values cast no vote.
    Memoized per process; deterministic.

    Fix wave 3 (B5): the Wiktionary door admits only
    word-like units — a unit with no letter (a numeral or
    symbol) or a single letter is not a word, and its
    store rows (the numeral 4's "preposition" row, the
    letter rows) are not word-class witnesses, so they
    testify to nothing here."""
    u = (unit or "").strip().lower()
    if u in _r1_unit_cache:
        return _r1_unit_cache[u]
    classes = set()
    sources = set()
    if " " not in u:
        import pmca_seed
        for entry in pmca_seed.DEMO_DICTIONARY:
            if entry.get("word") == u:
                for p in entry.get("pos") or []:
                    c = _R1_SEED_POS.get(p)
                    if c:
                        classes.add(c)
                        sources.add("seed")
                break
    mc = _r1_marker_classes(u)
    if mc:
        classes |= mc
        sources.add("webster_marker")
    wordlike = len(u) > 1 and any(ch.isalpha() for ch in u)
    if wordlike:
        poss = _r1_wiktionary_pos(u)
        if poss:
            for p in poss:
                c = _R1_WIKTIONARY_POS.get(p)
                if c:
                    classes.add(c)
                    sources.add("wiktionary")
        elif poss is not None and " " not in u:
            for cand in pmca_dictionary._candidates(u):
                poss = _r1_wiktionary_pos(cand)
                if poss:
                    for p in poss:
                        c = _R1_WIKTIONARY_POS.get(p)
                        if c:
                            classes.add(c)
                            sources.add("wiktionary")
                    break
    out = (tuple(sorted(classes)), tuple(sorted(sources)))
    _r1_unit_cache[u] = out
    return out


def paraphrase_unit_classes(unit):
    """The R-1 class set of one counting unit (a single
    word or a whole phrase), as a sorted list — the F3
    identification surface of the R-1 test."""
    return list(_r1_unit_evidence(unit)[0])


def _r1_unit_text(piece):
    """A gloss piece normalized to its lookup unit:
    lowercased, leading GCIDE field labels ("(Bot.)" and
    the like) stripped — the codebase's apparatus
    convention — surrounding punctuation stripped,
    whitespace collapsed."""
    t = (piece or "").strip().lower()
    while t.startswith("(") and ")" in t:
        t = t[t.index(")") + 1:].strip()
    t = t.strip(" .,;:!?\"'")
    return " ".join(t.split())


def _r1_webster_conn():
    """The section's one read-only connection to the
    configured Webster store (the ownership read needs
    exact rows and phrase families, which lookup's API
    does not enumerate). Caller holds _r1_wikt_lock, the
    section's store lock."""
    global _r1_web_conn, _r1_web_path
    path = pmca_dictionary.db_path()
    if path is None:
        return None
    if _r1_web_conn is None or _r1_web_path != path:
        if _r1_web_conn is not None:
            _r1_web_conn.close()
        _r1_web_conn = sqlite3.connect(
            "file:" + path + "?mode=ro", uri=True,
            check_same_thread=False)
        _r1_web_path = path
    return _r1_web_conn


def _r1_webster_defs(word):
    """The stored Webster definition strings under the
    EXACT headword (no inflection resolution), or None
    when the tier is off or the headword is absent. The
    ownership read needs exact rows: lookup's resolution
    is itself one of the borrowing routes (fix wave 3,
    B3). Read-only; memoized per process."""
    if word in _r1_webster_cache:
        return _r1_webster_cache[word]
    defs = None
    with _r1_wikt_lock:
        row = None
        try:
            con = _r1_webster_conn()
            if con is not None:
                row = con.execute(
                    "SELECT defs FROM defs WHERE word=?",
                    (word,)).fetchone()
        except sqlite3.Error:
            row = None
    if row is not None:
        try:
            val = json.loads(row[0])
        except (ValueError, TypeError):
            val = None
        if isinstance(val, list):
            defs = [d for d in val if isinstance(d, str)]
    _r1_webster_cache[word] = defs
    return defs


def _r1_norm_text(s):
    """An element normalized for ownership comparison:
    lowercased, whitespace collapsed."""
    return " ".join((s or "").lower().split())


def _r1_parent_words(w):
    """The headwords whose served text can be BORROWED
    under w (fix wave 3, B3): a phrase's component words,
    the dictionary tier's inflection candidates, and the
    derivative stems (-ness, -ly) — the relations under
    which the store serves a parent entry's text beneath
    a derivative or phrase headword."""
    out = []

    def add(p):
        if p and p != w and p not in out:
            out.append(p)

    if " " in w:
        for comp in w.split():
            add(comp)
    for cand in pmca_dictionary._candidates(w):
        add(cand)
    if w.endswith("ness") and len(w) > 5:
        add(w[:-4])
    if w.endswith("ly") and len(w) > 3:
        add(w[:-2])
    # Fix wave 4, nomination gaps (Wreck §1.4(3)): the
    # store serves the stem's article under these
    # derivative shapes too, and the stems were never
    # nominated — the -ity/-ibility stem
    # (impersuasibility's row is IMPERSUASIBLE's article;
    # incompossibility's is INCOMPOSSIBLE's), the -ically
    # adverb's -ic stem (athletically's row is ATHLETIC's
    # article; the -ly rule alone nominates the
    # nonexistent athletical), and the -iness stem in -y
    # (elusoriness's row is ELUSORY's article; the -ness
    # rule alone nominates "elusori"). A nominated word
    # with no stored row nominates nothing further —
    # parents_data skips it.
    if w.endswith("ibility") and len(w) > 9:
        add(w[:-7] + "ible")
    elif w.endswith("ability") and len(w) > 9:
        add(w[:-7] + "able")
    if w.endswith("ity") and len(w) > 5:
        add(w[:-3])
        add(w[:-3] + "e")
    if w.endswith("ically") and len(w) > 7:
        add(w[:-4])
    if w.endswith("iness") and len(w) > 6:
        add(w[:-5] + "y")
    # The -able/-ible adverb stems (same family, found
    # on the wave's own residue grading): the -ly rule
    # nominates "definab" / "incessab" — no such rows —
    # while the store serves DEFINABLE's / INCESSABLE's
    # articles under definably / incessably. Nominate
    # the real stems.
    if w.endswith("ibly") and len(w) > 6:
        add(w[:-4] + "ible")
    elif w.endswith("ably") and len(w) > 6:
        add(w[:-4] + "able")
    return out


def _r1_parents_data(w):
    """{parent: (element set, joined text)} over the
    nominated parents' OWN stored rows, both normalized —
    the comparison base for the verbatim ownership test."""
    data = {}
    for p in _r1_parent_words(w):
        defs = _r1_webster_defs(p)
        if defs is None:
            e = pmca_dictionary.lookup(p)
            if e is not None and \
                    (e["headword"] or "").strip().lower() == p:
                defs = e["definitions"]
        if defs:
            data[p] = ({_r1_norm_text(d) for d in defs},
                       _r1_norm_text(" ".join(defs)))
    return data


def _r1_family_rows(first):
    """(word, [normalized element, ...]) for every stored
    headword of the form "<first> ..." — a phrase family.
    The store serves the component entry's text under the
    whole family (AT's senses stand in at all, at home,
    at it, at last alike), so family-shared text is the
    component's text, not any one phrase's. Memoized."""
    if first in _r1_family_cache:
        return _r1_family_cache[first]
    rows = []
    with _r1_wikt_lock:
        raw = []
        try:
            con = _r1_webster_conn()
            if con is not None:
                raw = con.execute(
                    "SELECT word, defs FROM defs "
                    "WHERE word LIKE ?",
                    (first + " %",)).fetchall()
        except sqlite3.Error:
            raw = []
    for word, val in raw:
        try:
            defs = json.loads(val)
        except (ValueError, TypeError):
            continue
        if isinstance(defs, list):
            rows.append((word, [_r1_norm_text(d) for d in defs
                                if isinstance(d, str)]))
    _r1_family_cache[first] = rows
    return rows


def _r1_shared_census():
    """The store-wide element census for the ownership
    read (fix wave 3 completion, B3): {(md5, length):
    (row count, holder words (up to 64), exemplar)} over
    every normalized definition element of at least 20
    characters, counting each element once per row it
    stands in. Built once per configured store,
    memoized; read-only. The store serves whole entry
    texts under families of headwords and its rows are
    shifted in blocks, so an element's holder set is the
    mechanical record of whose text it is."""
    global _r1_census_cache, _r1_census_path
    path = pmca_dictionary.db_path()
    if _r1_census_path == path and _r1_census_cache:
        return _r1_census_cache
    census = {}
    with _r1_wikt_lock:
        con = _r1_webster_conn()
        rows = []
        if con is not None:
            try:
                rows = con.execute(
                    "SELECT word, defs FROM defs").fetchall()
            except sqlite3.Error:
                rows = []
    for word, val in rows:
        try:
            defs = json.loads(val)
        except (ValueError, TypeError):
            continue
        if not isinstance(defs, list):
            continue
        seen = set()
        for d in defs:
            if not isinstance(d, str):
                continue
            n = _r1_norm_text(d)
            if len(n) < 20:
                continue
            key = (hashlib.md5(n.encode("utf-8")).hexdigest(),
                   len(n))
            if key in seen:
                continue
            seen.add(key)
            ent = census.get(key)
            if ent is None:
                census[key] = [1, [word], n]
            else:
                ent[0] += 1
                if len(ent[1]) < 64:
                    ent[1].append(word)
    _r1_census_cache = census
    _r1_census_path = path
    return census


# Sentinel for _r1_element_borrower: the element is
# store-shared text with no directional witness — not
# this headword's own, but no borrower can be named
# truthfully. Pieces carrying it are recorded with the
# disposition "shared".
_R1_SHARED = object()


def _r1_stem_token(t):
    """A headword token with a possessive ending
    normalized away ("adam's" -> "adam"): the store's
    possessive phrases are the same word's family, and
    comparisons that miss the apostrophe miss the word
    (fix wave 4 — adam's holders are "adam's apple" /
    "adam's needle", whose component token is "adam's",
    and the un-normalized source guard never fired)."""
    t = (t or "").strip().lower()
    return t[:-2] if t.endswith("'s") else t


def _r1_nominates(h, w):
    """Whether holder h nominates w as a parent, with
    possessives normalized on both sides (fix wave 4):
    the census source guard in _r1_element_borrower
    reads through this, so a word whose text stands in
    its possessive phrases' rows keeps its own text."""
    return any(_r1_stem_token(p) == _r1_stem_token(w)
               for p in _r1_parent_words(h))


def _r1_chain_source(x, n, w):
    """Walk a census attribution's chain to its source
    (fix wave 4): the named holder x may itself read
    this same element as borrowed from a nominated
    parent holding it verbatim — apothecaries' weight
    named [apothecaries], a fellow borrower, for text
    whose source is APOTHECARY (apothecaries itself
    names [apothecary] for it). Follow the verbatim
    parent links, bounded and cycle-safe, and name the
    chain's end. n is the element's normalized text."""
    seen = {w, x}
    cur = x
    for _ in range(4):
        nxt = None
        for parent, (eset, _join) in \
                _r1_parents_data(cur).items():
            if n in eset:
                nxt = parent
                break
        if nxt is None or nxt in seen:
            break
        seen.add(nxt)
        cur = nxt
    return cur


def _r1_header_source(text):
    """The headword whose apparatus header OPENS this
    element, when the element opens with one (fix wave
    5): a GCIDE article element heads its own article
    with its apparatus header (Kneippism \\Kneipp"ism\\
    ...), so the header is the stored record of which
    row the article belongs to — the source row of any
    alias row serving the same text. The apparatus
    scan (_apparatus_owner) reads the header's final
    token; the element's opening text must be exactly
    that head — a full headword, or a phrase head
    ending in the scanned token. None when the element
    does not open with its article's header."""
    t = text or ""
    j = t.find("\\")
    if j < 0:
        return None
    head = t[:j].strip().lower()
    if not head or not re.fullmatch(
            r"[a-z][a-z' -]*[a-z]|[a-z]", head):
        return None
    owner = _apparatus_owner(t, j + 1)
    if owner is None:
        return None
    if head != owner and not head.endswith(
            " " + owner):
        return None
    return head


_r1_own_read_cache = {}


def _r1_own_read(h, text):
    """Headword h's own ownership read of an element of
    its entry — _r1_element_borrower over h's nominated
    parents and phrase family — memoized per
    (h, element): every alias row of one article asks
    the same question of the same text (fix wave 5)."""
    key = (h, hashlib.md5(
        _r1_norm_text(text).encode("utf-8")).hexdigest())
    if key not in _r1_own_read_cache:
        _r1_own_read_cache[key] = _r1_element_borrower(
            h, text, _r1_parents_data(h),
            _r1_family_rows(h.split()[0])
            if " " in h else None)
    return _r1_own_read_cache[key]


def _r1_entry_chain_borrower(w, h, text):
    """Fix wave 5: the borrower named for one element
    of a word whose stored text is another headword's
    entry (borrowed_all in _r1_build_pieces: the
    dictionary tier resolved w to h's entry, or a
    p. p. cross-reference named h). Waves 3/4 named h
    for every element outright; Gauge's uncapped
    naming audit showed h can itself be a fellow
    borrower or a fellow holder of the element, one
    link short of the source. The resolved headword's
    OWN read of the element decides:
    - h reads the element as borrowed from y: the
      text is y's — name y (new year's names NEW
      through NEW YEAR, not the fellow borrower
      NEW YEAR).
    - h reads it as its own (None): name h.
    - h reads it as shared: the element's own article
      header shows the source row when it can — name
      the header's headword when it is not h and the
      census shows it holding the element (kneipp's
      names KNEIPPISM, whose article the element is);
      when the header names h itself, h stands
      (glover's names GLOVER — glover's own article,
      correctly). When no source can be shown, shared
      with no name — honesty over a name.
    A read naming w itself would close a cycle: the
    direction is not shown, so shared, unnamed (a
    word never names itself — fix wave 4)."""
    read = _r1_own_read(h, text)
    if isinstance(read, str):
        return read if read != w else _R1_SHARED
    if read is _R1_SHARED:
        src = _r1_header_source(text)
        if src is not None and src == h:
            return h
        if src is not None and src != w:
            n = _r1_norm_text(text)
            if len(n) >= 20:
                ent = _r1_shared_census().get(
                    (hashlib.md5(
                        n.encode("utf-8")).hexdigest(),
                     len(n)))
                if ent is not None and ent[2] == n \
                        and src in ent[1]:
                    return src
        return _R1_SHARED
    return h


_r1_ghost_cache = {}


def _r1_ghost_borrower(w, text):
    """The single-row ghost witness (fix wave 4, B3):
    a phrase's row serving a sense the store extracted
    ONLY under the phrase — the element stands in no
    other row in any form, so no verbatim witness can
    see it (astral lamp's only element is ASTRAL's
    gloss: "Pertaining to, coming from, or resembling,
    the stars; starry; starlike"). The record still
    shows whose text it is, by stored structure alone:
    the element's own voting units intersect to exactly
    one class; that class is the FIRST component's
    class under the F3 identifier (Wiktionary); the
    element never names the phrase's head component
    (the thing the phrase names — a definition of the
    phrase would be about it); and the element is a
    gloss-fragment chain, not an article-led definition
    of a thing ("A small Brazilian rodent..." defines
    the phrase's referent and is left alone). Returns
    the component headword, else None. Memoized."""
    if " " not in w:
        return None
    key = (w, hashlib.md5(
        _r1_norm_text(text).encode("utf-8")).hexdigest())
    if key in _r1_ghost_cache:
        return _r1_ghost_cache[key]
    out = None
    toks_w = w.split()
    c1, cn = toks_w[0], toks_w[-1]
    if c1 != cn:
        etoks = re.findall(r"[a-z]+", text.lower())
        first = text.strip()
        while first.startswith("(") and ")" in first:
            first = first[first.index(")") + 1:].strip()
        if cn not in etoks and not first.lower().startswith(
                ("a ", "an ", "the ")):
            by_unit = {}
            for p in _r1_element_pieces(w, w, text, None):
                if p["disposition"] != "vote":
                    continue
                u = _r1_unit_text(p["text"])
                classes, _src = _r1_unit_evidence(u)
                if classes:
                    by_unit[u] = set(classes)
            if len(by_unit) >= 2:
                inter = set.intersection(*by_unit.values())
                if len(inter) == 1:
                    cls = next(iter(inter))
                    poss = _r1_wiktionary_pos(c1)
                    if poss:
                        c1_classes = {
                            _R1_WIKTIONARY_POS[p]
                            for p in poss
                            if p in _R1_WIKTIONARY_POS}
                        if cls in c1_classes:
                            out = c1
    _r1_ghost_cache[key] = out
    return out


def _r1_element_borrower(w, text, parents_data, family_rows,
                         _depth=0):
    """The headword whose text this definition element
    is, when the record mechanically shows borrowing
    (fix wave 3, B3) — else None: the element is the
    word's own. Borrowing is shown three ways: the
    element stands verbatim as an element of a nominated
    parent's stored text; or, for a phrase, the element
    is family-shared text — verbatim in at least two
    other rows of the phrase's family (the component
    entry's text served under each of them), in which
    case the component is the borrower named; or the
    element is store-shared text — verbatim in at least
    two other stored rows anywhere (the census) — unless
    this headword is the source those rows borrowed
    from (a nominated parent of a holder: per for per
    annum / per cent / per centum). The store-shared
    return is the borrower headword when a directional
    witness names one, else the _R1_SHARED sentinel.

    Fix wave 4 adds three witnesses and two repairs.
    SIBLING CONSENSUS: an element standing in exactly
    two rows, held by two headwords that share a
    nominated parent whose own row does NOT hold it, is
    the parent's text served to both borrowers
    (trespass offering / trespass on the case share
    TRESPASS's sense-elements while trespass's own
    extraction differs) — shared, abstain; the parent
    check, the family check and the >=3 census are each
    blind to the shape. THE GHOST WITNESS
    (_r1_ghost_borrower) covers the single-row residue.
    Repairs: the source guard reads nominations with
    possessives normalized (_r1_nominates — adam's
    holders are its possessive phrases, and the guard
    never fired); the common-token attribution never
    returns the word itself (a word never names itself
    as its own borrower — adam did); and every census
    attribution is chain-walked to its source
    (_r1_chain_source — apothecaries' weight named the
    fellow borrower apothecaries for APOTHECARY's text),
    with a final check that the named holder does not
    itself read the element as borrowed or shared.

    Fix wave 5 (naming completion): the chain
    resolution (resolve, below) guards EVERY naming
    route, not only the census attributions. Gauge's
    uncapped re-run of the naming audit (945
    candidates, 527 chain-tested) found the
    verbatim-parent and family routes naming fellow
    borrowers in the possessive / "one's" family —
    off one's hands named HANDS for HAND's article,
    because hands' row holds the element verbatim and
    the route returned it unwalked. A name is only as
    good as its chain's end: whatever route proposes
    the holder, resolve walks it — verbatim parent
    links first (_r1_chain_source), then, at depth 0,
    the holder's own read of the element: a borrower
    it names onward, or the _R1_SHARED verdict when
    the holder itself marks the text shared (naming
    it would overclaim a direction the record does
    not show)."""
    n = _r1_norm_text(text)
    if len(n) < 20:
        return None

    def resolve(x):
        y = _r1_chain_source(x, n, w)
        if y != x:
            return y
        if _depth == 0:
            bx = _r1_element_borrower(
                x, text, _r1_parents_data(x),
                _r1_family_rows(x.split()[0])
                if " " in x else None, 1)
            if bx is _R1_SHARED:
                return _R1_SHARED
            if isinstance(bx, str) and bx != x \
                    and bx != w:
                return bx
        return x

    for parent, (eset, join) in parents_data.items():
        if n in eset:
            return resolve(parent)
    if family_rows is not None:
        shared = 0
        for word, elems in family_rows:
            if word != w and n in elems:
                shared += 1
                if shared >= 2:
                    return resolve(w.split()[0])
    key = (hashlib.md5(n.encode("utf-8")).hexdigest(),
           len(n))
    ent = _r1_shared_census().get(key)
    holders = []
    if ent is not None and ent[2] == n:
        holders = [h for h in ent[1] if h != w]
        # The source guard, at every holder count: a
        # headword whose text the holder rows took (they
        # name it as a parent — a component, a stem, an
        # inflection base, a possessive's stem) keeps
        # its own text.
        for h in holders:
            if _r1_nominates(h, w):
                return None
        # Sibling consensus (exactly two rows): two
        # borrowers sharing a nominated parent whose
        # row does not hold the element.
        if ent[0] == 2 and len(holders) == 1:
            h = holders[0]
            if h not in parents_data:
                pw = set(_r1_parent_words(w))
                ph = set(_r1_parent_words(h))
                if any(p != w and p != h
                       for p in pw & ph):
                    return _R1_SHARED
    # The ghost witness: a parent sense extracted only
    # under a phrase's row (any census count the other
    # witnesses left unclaimed).
    ghost = _r1_ghost_borrower(w, text)
    if ghost is not None:
        return ghost
    if ent is not None and ent[0] >= 3 and ent[2] == n \
            and holders:
        # Attribution, only on a directional witness,
        # in order: a nominated parent holding the
        # element; a holder that is a token of this
        # headword (on't -> on); the one token this
        # headword shares with at least two holders
        # (day by day -> by) — never the word itself.
        # Without a witness the direction is not shown
        # (the shifted blocks name no source): the
        # element is shared, unnamed.
        for parent in parents_data:
            if parent in holders:
                return resolve(parent)
        wtoks = re.findall(r"[a-z]+", w)
        for h in holders:
            if h in wtoks:
                return resolve(h)
        common = [t for t in dict.fromkeys(wtoks)
                  if t != w
                  and _r1_stem_token(t) != _r1_stem_token(w)
                  and sum(1 for h in holders
                          if t in re.findall(
                              r"[a-z]+", h)) >= 2]
        if len(common) == 1:
            return resolve(common[0])
        return _R1_SHARED
    return None


# Apparatus abbreviations whose closing period does not
# end a sentence in GCIDE text (esp., cf., fig., ...).
_R1_ABBREV = frozenset({
    "esp", "cf", "viz", "sc", "ibid", "fig", "obs", "fr",
    "al", "etc", "imp", "def", "syn", "prop", "pl",
    "sing", "st"})


def _r1_split_element(text):
    """One definition element split into (piece, offset)
    on ';' and on sentence '. ' — except that a period
    closing a single letter or a stored apparatus
    abbreviation does not split (fix wave 3, B1: the
    shattering of "esp." / "p. p." / "v. 4" is what
    minted citation fragments as voting units).

    Fix wave 4, three more non-splits, all read off the
    stored text's own structure:
    (i) an ELLIPSIS period: one of the spaced dots of
    ". . ." — the next non-space character is another
    period, or the previous non-space character before
    the token is one. GCIDE elides quotation middles
    this way; splitting on the dots shattered the quote
    and its lone first fragment ("He . . . stammered
    ...") voted as a gloss unit. Unsplit, the quote
    stays one piece and meets the ordinary dispositions
    (it names the headword, or its whole text classifies
    nothing).
    (ii) a CONTINUATION period: the word after the
    period starts lowercase or with a digit — the
    clause runs on ("Eng. bursar", "Lat. ferre",
    "Eph. if. 1", "Eccl. v. 4"). A sentence-ending
    period in this
    prose is followed by a capital; an abbreviation's
    period is followed by the rest of its clause, and
    the abbreviation fragments the split used to mint
    ("eng", "lat", the OCR verse numeral "if" for
    "ii.") were citation and label matter, not glosses.
    (iii) a CITATION-CHAIN period: the text from this
    token to the element's end is a chain of capitalized
    abbreviation tokens ("Encyc. Brit.") — the un-dashed
    form of the citation tail. It stays glued to its
    piece, whose whole text classifies nothing, instead
    of shattering into classifying fragments."""
    parts = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == ";":
            parts.append((text[start:i], start))
            i += 1
            start = i
        elif ch == "." and i + 1 < n and text[i + 1] == " ":
            j = i
            while j > start and text[j - 1].isalnum():
                j -= 1
            tok = text[j:i].lower()
            if len(tok) == 1 or tok in _R1_ABBREV:
                i += 1
                continue
            # (i) ellipsis: another period on either side
            if i + 2 < n and text[i + 2] == ".":
                i += 1
                continue
            k = j - 1
            while k >= 0 and text[k] == " ":
                k -= 1
            if k >= 0 and text[k] == ".":
                i += 1
                continue
            # (ii) continuation: lowercase or digit next
            if i + 2 < n and (text[i + 2].islower()
                              or text[i + 2].isdigit()):
                i += 1
                continue
            # (iii) citation chain to the element's end
            if re.fullmatch(
                    r"(?:[A-Z][A-Za-z]{0,5}\.\s*){2,}\)?",
                    text[j:].strip()):
                i += 1
                continue
            parts.append((text[start:i], start))
            i += 2
            start = i
        else:
            i += 1
    parts.append((text[start:], start))
    return [(t, o) for (t, o) in parts if t.strip()]


# Tokens that, standing alone as a whole piece, are
# apparatus — cross-reference and etymology labels, never
# a gloss (fix wave 3 completion, B1): the truncated tail
# of "See {X}" ("...pref. a- + web. See") and the Arabic
# label inside an etymology ("?, Ar.") paraphrase
# nothing, and the classes their store rows carry
# ("see" as interjection/noun/verb) are not witnesses.
_R1_APPARATUS_UNITS = frozenset({
    "see", "cf", "pref", "ar", "viz", "esp", "ibid",
    "fig", "obs", "sc", "etc", "syn", "ant", "fr"})


def _r1_bracket_spans(text):
    """The [start, end) spans of bracketed matter in one
    definition element: etymologies and usage/domain
    labels are bracketed in GCIDE text ("[From F.
    dragoman, ... Ar. tarjum[=a]n ...]", "[Obs.]"). An
    unclosed bracket runs to the element's end (the
    store truncates elements mid-etymology).

    Fix wave 4: brackets are counted by DEPTH. GCIDE
    respelling brackets nest inside the etymology
    bracket ("OE. fe['o]nd", "OS. f[=i]ond"); the flat
    first-close pairing ended the span at the first
    respelling bracket and let the etymology's tail
    vote (fiend's "orig." / "prob." fragments attested
    a noun on an entry whose definition text is not in
    the store at all)."""
    spans = []
    start = None
    depth = 0
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
                start = None
    if start is not None:
        spans.append((start, len(text)))
    return spans


def _r1_tail_split(piece):
    """Split a raw piece at a citation or derivative
    tail: '--' followed by a capital, a digit, or '{' —
    a cited author or book ("--Shak.", "--Eccl. v. 4")
    or a run-on derivative listing ("-- {X}, adv."). A
    lowercase continuation after '--' is gloss text, not
    a tail ("-- used in composition", "-- generally
    followed by ..."). Returns [(text, offset, is_tail),
    ...]."""
    m = re.search(r"\s--\s*(?=[A-Z0-9{])", piece)
    if not m:
        return [(piece, 0, False)]
    return [(piece[:m.start()], 0, False),
            (piece[m.start():].strip(), m.start(), True)]


def _r1_names_headword(low, headword):
    """Whether a piece names the headword (the F2 self
    test): for a single-word headword, one of the piece's
    tokens is the word; for a phrase headword, the
    phrase's own token sequence appears — a lone
    component token does not name the phrase (fix wave
    3, B3: the component test gutted phrase entries —
    every piece of a parent's text mentioning 'at' or
    'of' read as naming 'at all' / 'because of')."""
    toks = re.findall(r"[a-z]+", low)
    hw_toks = re.findall(r"[a-z]+", headword)
    if not hw_toks:
        return False
    if len(hw_toks) == 1:
        return hw_toks[0] in toks
    n = len(hw_toks)
    return any(toks[i:i + n] == hw_toks
               for i in range(len(toks) - n + 1))


def _r1_element_pieces(w, headword, text, el_borrower):
    """One definition element's pieces with dispositions
    — the per-element body of _r1_build_pieces, factored
    out in fix wave 4 so the ghost witness
    (_r1_ghost_borrower) reads an element exactly as the
    attestation read does. el_borrower is the element's
    ownership outcome: a headword (borrowed), the
    _R1_SHARED sentinel, or None (the word's own)."""
    etymology = text.lstrip().startswith("[")
    spans = _r1_bracket_spans(text)
    tail_open = False
    pieces = []
    for raw, off in _r1_split_element(text):
        for sub, sub_off, is_tail in _r1_tail_split(raw):
            t = sub.strip()
            if not t:
                continue
            content_off = off + sub_off \
                + (len(sub) - len(sub.lstrip()))
            low = t.lower()
            toks = re.findall(r"[a-z]+", low)
            unit = _r1_unit_text(t)
            if el_borrower is _R1_SHARED:
                disp, owner = "shared", None
            elif el_borrower is not None:
                disp, owner = "borrowed", el_borrower
            elif etymology:
                disp, owner = "apparatus", None
            elif any(s <= content_off < e
                     for s, e in spans):
                # Inside a bracket span: etymology
                # or a usage/domain label — stored
                # apparatus wherever the bracket sits
                # in the element (fix wave 3
                # completion, B1; the dragoman
                # specimen, whose etymology follows
                # its header in the same element).
                disp, owner = "apparatus", None
            elif unit in _R1_APPARATUS_UNITS:
                # A whole piece that is one apparatus
                # token (see / cf / pref / ar / ...):
                # a cross-reference tail or an
                # etymology label, not a gloss.
                disp, owner = "apparatus", None
            elif re.fullmatch(r"[A-Z]{2,}", t) \
                    or re.fullmatch(r"[A-Za-z]+:", t):
                # Fix wave 4: a language label standing
                # alone ("OE" — shattered out of gospel's
                # unbracketed etymology discussion, where
                # its "noun" row voted) or a section
                # label standing as a whole piece
                # ("Usage:" — calculate's co-witness) is
                # apparatus by shape: a gloss word does
                # not stand as an all-caps token or as a
                # bare colon-headed label. The word
                # 'usage' inside gloss text is untouched
                # (custom / practice keep their gloss).
                disp, owner = "apparatus", None
            elif is_tail or re.match(r"--\s*[A-Z0-9{]", t):
                disp, owner = "apparatus", None
                tail_open = True
            elif tail_open and (
                    re.fullmatch(r"[A-Z][A-Za-z]*[.]?", t)
                    or re.fullmatch(r"[a-z]", unit)
                    or re.fullmatch(r"[0-9]+", unit)
                    or re.fullmatch(r"[ivxlcdm]+", unit)
                    or re.fullmatch(r"[a-z]{2,3}", unit)):
                # A citation-tail fragment: the author
                # name shattered from its "--" tail
                # ("--Jer." + "Taylor", "--J." + "C." +
                # "Shairp"), a verse numeral, a chapter
                # letter. Fragment-shaped pieces only,
                # and only while a citation tail is open
                # in this element — a gloss word that
                # merely starts a sentence ("Abdicating")
                # is not a fragment. Fix wave 4 adds the
                # lone short lowercase token: the OCR
                # verse numeral's stored shape ("if" for
                # "ii." in "--Eph. if. 1") and name
                # particles — gloss text does not resume
                # as a lone short token inside an open
                # citation tail.
                disp, owner = "apparatus", None
            elif "\\" in t:
                # An apparatus-header piece: the header
                # line of the entry it heads — borrowed
                # when it heads another headword's
                # segment, apparatus when it heads
                # this word's own.
                oend = _apparatus_owner(
                    text, content_off + len(t))
                if oend and oend != headword:
                    disp, owner = "borrowed", oend
                else:
                    disp, owner = "apparatus", None
            else:
                tail_open = False
                seg = _apparatus_owner(text, content_off)
                if seg and seg != headword:
                    disp, owner = "borrowed", seg
                elif _r1_unit_text(t).startswith(
                        ("syn:", "ant:", "see {")):
                    disp, owner = "apparatus", None
                elif t.startswith("``"):
                    disp, owner = "example", None
                elif re.search(
                        r"\b" + re.escape(headword)
                        + r"\s+(is|was|are|were|has|have)\b",
                        low):
                    disp, owner = "meta", None
                elif toks and toks[0] == "as":
                    disp, owner = "example", None
                elif _r1_names_headword(low, headword):
                    disp, owner = "self", None
                else:
                    disp, owner = "vote", None
            pieces.append({"text": t,
                           "disposition": disp,
                           "borrowed_from": owner})
    return pieces


def _r1_build_pieces(w, entry):
    """The stored definition's pieces with dispositions,
    as paraphrase_attestation reads them. The F2
    dispositions — "vote" (a gloss), "example"
    (introduces examples: first token "as", or a
    quotation), "self" (names the headword), "meta"
    (prose about the word: "<headword> is ...") —
    plus the fix-wave-3 dispositions: "apparatus"
    (etymology elements, citation and derivative tails,
    shattered author names, apparatus headers — stored
    apparatus, not glosses; B1) and "borrowed" (text the
    record shows belongs to another headword; it neither
    votes nor feeds the use-fallback; B3/B6), and
    "shared" (text standing verbatim under at least two
    other headwords with no directional witness naming
    its source; likewise neither votes nor feeds the
    use-fallback; B3 completion). Every piece carries
    borrowed_from when borrowed. Segmentation
    and the meta/example readings are the scour's stated
    mechanics (§1.1); the "self" exclusion is its Variant
    P, ratified by F2. Fix wave 5: an entry-level
    borrowing (borrowed_all) is resolved per element
    through the resolved headword's own read
    (_r1_entry_chain_borrower) — the entry headword is
    a link in the naming chain, not always its end."""
    headword = (entry["headword"] or "").strip().lower()
    definitions = entry["definitions"]
    # Entry-level borrowing: lookup resolved the word to
    # another headword's entry (its text is that
    # headword's), or the entry is a pure cross-reference
    # ("p. p. of {Shew}") whose senses are the referenced
    # headword's.
    borrowed_all = None
    if headword != w:
        borrowed_all = headword
    else:
        for text in definitions:
            m = re.search(
                r"(?:p\. p\.|imp\.|pret\.)\s+of\s*\{([^}]+)\}",
                text)
            if m and m.group(1).strip().lower() != w:
                borrowed_all = m.group(1).strip().lower()
                break
    parents_data = {}
    family_rows = None
    if borrowed_all is None:
        parents_data = _r1_parents_data(w)
        if " " in w:
            family_rows = _r1_family_rows(w.split()[0])
    pieces = []
    for text in definitions:
        el_borrower = borrowed_all
        if el_borrower is not None:
            # Fix wave 5: an entry-level borrower is a
            # name to be resolved per element, not a
            # conclusion — the resolved headword may
            # itself borrow the element onward
            # (_r1_entry_chain_borrower).
            el_borrower = _r1_entry_chain_borrower(
                w, borrowed_all, text)
        if el_borrower is None:
            el_borrower = _r1_element_borrower(
                w, text, parents_data, family_rows)
        pieces.extend(_r1_element_pieces(
            w, headword, text, el_borrower))
    return pieces


def _r1_pieces(headword, definitions):
    """The stored definition's pieces with their F2
    dispositions — the own-text view of _r1_build_pieces
    (an entry read as its own headword's).
    paraphrase_attestation builds through
    _r1_build_pieces with the queried word, which adds
    the wave-3 ownership dispositions."""
    hw = (headword or "").strip().lower()
    return _r1_build_pieces(
        hw, {"headword": headword,
             "definitions": definitions})


def paraphrase_attestation(word):
    """FINDER R-1: the uniform-paraphrase attestation of
    one word, as a record dict — attested_class and tier
    ("attested" from the glosses; "CANDIDATE" from the
    use-fallback, with its gap), the word's own marker
    classes, any F4 conflict, the headwords any of the
    served text was borrowed from (borrowed_from; fix
    wave 3, B3), and every unit read, with its classes
    and sources, voting or abstaining, on the record.
    Nothing about the read is silent."""
    w = (word or "").strip().lower()
    result = {"word": w, "headword": None,
              "attested_class": None, "tier": None,
              "provenance": None, "marker_classes": [],
              "conflict": None, "units": [], "gap": None,
              "borrowed_from": [],
              "wiktionary_live":
                  _r1_wiktionary_path() is not None}
    entry = pmca_dictionary.lookup(w)
    marker_classes = _r1_marker_classes(w)
    result["marker_classes"] = sorted(marker_classes)
    pieces = []
    if entry is not None:
        result["headword"] = entry["headword"]
        pieces = _r1_build_pieces(w, entry)
    result["borrowed_from"] = sorted(
        {p["borrowed_from"] for p in pieces
         if p.get("borrowed_from")})

    def run(selected):
        recs = []
        for p in selected:
            u = _r1_unit_text(p["text"])
            classes, sources = _r1_unit_evidence(u)
            rec = {"unit": u,
                   "classes": list(classes),
                   "sources": list(sources),
                   "disposition": p["disposition"]}
            if p.get("borrowed_from"):
                rec["borrowed_from"] = p["borrowed_from"]
            recs.append(rec)
        return recs

    def verdict(selected):
        # The floor counts DISTINCT units (fix wave 3,
        # B2): the same unit text in two pieces is one
        # witness, not two — a second copy is not a
        # second witness (a cited author quoted in two
        # senses attested ascertain as a noun on the one
        # name, twice).
        by_unit = {}
        for p in selected:
            u = _r1_unit_text(p["text"])
            classes, _ = _r1_unit_evidence(u)
            if classes:
                by_unit[u] = set(classes)
        if len(by_unit) >= 2:
            inter = set.intersection(*by_unit.values())
            if len(inter) == 1:
                return next(iter(inter)), len(by_unit)
        return None, len(by_unit)

    # Every piece read is on the record, voting or not.
    result["units"] = run(pieces)
    voting = [p for p in pieces
              if p["disposition"] == "vote"]
    cls, n_classified = verdict(voting)
    if cls is not None:
        if marker_classes and cls not in marker_classes:
            # F4: neither wins. The conflict is the
            # record; the word is not settled by
            # this test.
            result["conflict"] = {
                "marker_classes": sorted(marker_classes),
                "paraphrase_class": cls}
            result["provenance"] = (
                "FINDER R-1: paraphrase attests "
                + cls + " but the word's own Webster tag "
                "attests "
                + "/".join(sorted(marker_classes))
                + " — F4 conflict recorded, neither wins; "
                "the word is not settled by this test")
            return result
        result["attested_class"] = cls
        result["tier"] = "attested"
        result["provenance"] = (
            "FINDER R-1 uniform-paraphrase attestation: "
            "the stored definition's voting units are "
            "wholly " + cls + " (pieces identified per F3)")
        return result

    # F2, second door: no proper definition could be
    # found (no stored entry, no voting pieces, or no
    # classified unit among them) — read the HOW: the
    # example and headword-naming pieces, as use
    # evidence.
    if entry is None or not voting or n_classified == 0:
        fb = [p for p in pieces
              if p["disposition"] in ("example", "self")]
        if fb:
            fcls, fn = verdict(fb)
            if fcls is not None:
                if marker_classes and fcls not in marker_classes:
                    # F4 on the fallback route too (fix
                    # wave 3; Wreck §4.1): a use-settlement
                    # that contradicts the word's own tag
                    # records the conflict instead of
                    # standing silently — neither wins.
                    result["conflict"] = {
                        "marker_classes":
                            sorted(marker_classes),
                        "paraphrase_class": fcls}
                    result["provenance"] = (
                        "FINDER R-1 use-fallback: the "
                        "how-to-use evidence attests "
                        + fcls + " but the word's own "
                        "Webster tag attests "
                        + "/".join(sorted(marker_classes))
                        + " — F4 conflict recorded, neither "
                        "wins; the word is not settled by "
                        "this test")
                    return result
                result["attested_class"] = fcls
                result["tier"] = "CANDIDATE"
                result["gap"] = ("paraphrase_use_fallback:"
                                 + w)
                result["provenance"] = (
                    "FINDER R-1 use-fallback (F2): no "
                    "proper definition found; settled by "
                    "the how-to-use evidence at CANDIDATE "
                    "tier — updated, not minted")
                return result
    if entry is not None and pieces and all(
            p["disposition"] == "apparatus" for p in pieces):
        # Fix wave 4: the stored row carries no
        # definition text at all — every piece read is
        # apparatus (an entry header and a truncated
        # etymology: fiend). A row with no definition
        # text cannot attest; the gap names the shape,
        # distinct from a definition that merely fails
        # the uniformity test.
        result["gap"] = "paraphrase_no_definition_text:" + w
        result["provenance"] = (
            "FINDER R-1: the stored row carries no "
            "definition text — every piece read is "
            "apparatus (etymology, labels, citation "
            "matter); no attestation is possible and "
            "the gap is recorded")
        return result
    result["gap"] = "paraphrase_no_attestation:" + w
    result["provenance"] = (
        "FINDER R-1: no uniform paraphrase attestation "
        "and no use-fallback settlement; the units read "
        "are on the record")
    return result


def _participle_candidate(word, verb_capable):
    """A following token that makes a contraction's expansion
    ambiguous (is vs has): an attested verb form spelled as a
    participle — regular (-ed / -en, entry form "verb") or a
    member of the closed irregular-participle list (Pack A;
    Argon F4: gone / hurt / cut / run carry no suffix signal).
    The list is itself the participle attestation (a closed
    grammar list, like CONTRACTIONS): this dictionary build
    stores some of these words' verb senses without a "To "
    opening ("cut" resolves with form "other"), so a
    verb-capability gate would silently exempt exactly the
    words the list exists for; a stored entry is required."""
    if word in IRREGULAR_PARTICIPLES:
        return pmca_dictionary.lookup(word) is not None
    if not verb_capable(word):
        return False
    entry = pmca_dictionary.lookup(word)
    if entry is None or entry["form"] != "verb":
        return False
    return word.endswith("ed") or word.endswith("en")


def _verified_plural(word, noun_capable):
    """Xs' stem verification: the full form is an attested plural
    noun — it resolves as a plural form through morphology, or
    it is itself a stored noun headword (the wolves case), and
    it is noun-capable in the dictionary."""
    if not noun_capable(word):
        return False
    res = _morph.resolve_form(word)
    if res["verdict"] == "OK" and res["form"] == "plural":
        return True
    entry = pmca_dictionary.lookup(word)
    return entry is not None and entry["headword"] == word


def _find_head(tokens, start, noun_capable):
    """The head noun after a possessive clitic: the phrase head
    is the LAST noun-capable token of the noun-phrase span that
    follows the clitic — the span runs to the first verb-capable
    token, article, preposition, splitter, auxiliary, pronoun,
    or further clitic. Leading tokens that are neither noun- nor
    verb-capable are skipped (the adjective-slot tokens of the
    original rule); once a noun-capable token is seen, a token
    that is neither ends the span. Return (head, index) or
    (None, None).

    Pack A (Argon F2, adjudicated 2026-10-26): the original
    returned the FIRST noun-capable token, but in this
    dictionary adjectives are noun-capable (their definitions do
    not open with "To "), so "the child's big cup" recorded the
    head as "big" and the possessor binding landed on the
    adjective. The phrase template everywhere else in the system
    takes the phrase's LAST token as its head; the genitive rule
    now agrees."""
    last = (None, None)
    j = start
    while j < len(tokens):
        tok = tokens[j]
        if tok in ARTICLES or tok in PREPOSITIONS \
                or tok in SPLITTERS or tok in AUX_ALL \
                or tok.endswith("'s") or tok.endswith("s'"):
            break
        res = _morph.resolve_form(tok)
        if res["verdict"] == "OK" \
                and res["form"] in ("third_person", "past"):
            # An inflected verb form is the clause's verb, not
            # part of the possessed phrase ("the wolf's hunger
            # drives the hunt": "drives" is noun-capable through
            # its base entry but its FORM here is third_person).
            break
        if last[0] is not None and dict_verb_capable(tok) \
                and (j + 1 >= len(tokens)
                     or tokens[j + 1] in ARTICLES
                     or tokens[j + 1] in PREPOSITIONS
                     or tokens[j + 1] in SPLITTERS):
            # A verb-capable token after a recorded candidate,
            # itself followed by a new phrase or the end of the
            # stream, is the clause's verb ("hunger drives the
            # hunt", "cup broke") — the span ended at the
            # candidate. (Morphology cannot always attest the
            # form: "drives" HOLDs ambiguous_form because "drive"
            # carries non-verb senses too.)
            break
        if noun_capable(tok):
            last = (tok, j)
            j += 1
            continue
        if dict_verb_capable(tok):
            break
        if last[0] is not None:
            break
        j += 1
    return last


def normalize(tokens, noun_capable, verb_capable):
    """Genitive + contraction normalization of a token list.

    Returns {"hold": tag|None, "tokens": [...], "genitives":
    [{"possessor", "head", "index"}], "possessor_indices": set}.
    Indices refer to the RETURNED token list. Possessor stems
    stay in the stream at their positions (consumers that drop
    them, like the analyzer, remove the indices themselves).
    On a HOLD the returned tokens/genitives are empty: a failed
    normalization asserts nothing."""
    toks = [t.replace("’", "'") for t in tokens]
    out = []
    genitives = []
    possessor_indices = set()
    expanded_aux_be = False

    def hold(tag):
        return {"hold": tag, "tokens": [], "genitives": [],
                "possessor_indices": set(), "expanded_aux_be": False}

    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok in CONTRACTIONS:
            if tok == "let's":
                out.extend(["let", "us"])
                i += 1
                continue
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None and _participle_candidate(nxt,
                                                         verb_capable):
                return hold("ambiguous_contraction:" + tok)
            out.append(tok[:-2])
            out.append("is")
            # Pack A (Argon F4): the expansion INSERTS an AUX_BE
            # token that the consumers' raw-token auxiliary scan
            # (run before this normalization) cannot see. The
            # flag lets each consumer apply its own auxiliary
            # scope rule to the expanded stream; screen() itself
            # does not hold on it (sealed probe:
            # probe_its_red_expands).
            expanded_aux_be = True
            i += 1
            continue
        if tok.endswith("'s") and len(tok) > 2:
            stem = tok[:-2]
            res = _morph.resolve_form(tok)
            if res["verdict"] == "OK" and res["form"] == "possessive":
                stem = res["entry"]
            if not noun_capable(stem):
                return hold("unverified_possessor_stem:" + stem)
            head, _hj = _find_head(toks, i + 1, noun_capable)
            if head is None:
                return hold("headless_or_non_noun_genitive:"
                            + stem + "'s")
            possessor_indices.add(len(out))
            genitives.append({"possessor": stem, "head": head,
                              "index": len(out)})
            out.append(stem)
            i += 1
            continue
        if tok.endswith("s'") and len(tok) > 2:
            stem_full = tok[:-1]
            if not _verified_plural(stem_full, noun_capable):
                return hold("unverified_plural_possessor:"
                            + stem_full)
            head, _hj = _find_head(toks, i + 1, noun_capable)
            if head is None:
                return hold("headless_or_non_noun_genitive:"
                            + stem_full + "'")
            possessor_indices.add(len(out))
            genitives.append({"possessor": stem_full, "head": head,
                              "index": len(out)})
            out.append(stem_full)
            i += 1
            continue
        out.append(tok)
        i += 1
    return {"hold": None, "tokens": out, "genitives": genitives,
            "possessor_indices": possessor_indices,
            "expanded_aux_be": expanded_aux_be}


def _finite_verbs(tokens, verb_capable):
    """Indices of attested finite-verb tokens: verb-capable and
    not an auxiliary/modal grammar token."""
    return [i for i, tok in enumerate(tokens)
            if tok not in AUX_ALL and verb_capable(tok)]


# Public alias: consumers with their own clause counting (the
# sentence analyzer) use the same attested-finite-verb notion.
finite_verbs = _finite_verbs


def _segments(tokens):
    """Split token indices into segments at splitter tokens."""
    segs = []
    cur = []
    for i, tok in enumerate(tokens):
        if tok in SPLITTERS:
            segs.append(cur)
            cur = []
        else:
            cur.append(i)
    segs.append(cur)
    return segs


def _counted_verbs(tokens, verb_capable,
                    possessor_indices=frozenset()):
    """Finite verbs as Guard 2 counts them: attested finite-verb
    tokens minus tokens in determined noun position (immediately
    preceded by an article, or a genitive phrase head immediately
    preceded by its possessor stem) and minus possessor stems
    themselves."""
    return [i for i in _finite_verbs(tokens, verb_capable)
            if i not in possessor_indices
            and not (i > 0 and tokens[i - 1] in ARTICLES)
            and not (i > 0 and (i - 1) in possessor_indices)]


def _g2_g4(tokens, verb_capable, possessor_indices=frozenset(),
           use_g2=True, use_g4=True):
    """Guards 2 and 4 over a token list. Returns a HOLD tag or
    None.

    Finite-verb counting excludes tokens in determined noun
    position: a verb-capable token immediately preceded by an
    article ("the cup" — the word's use there is noun, by the
    article) or immediately preceded by a possessor stem ("the
    child's cup" — the token is that phrase's head) is not a
    clause verb. Residual limitation, recorded: a bare subject
    with no article that is itself verb-capable (e.g. "Men
    break cups.") can still be miscounted; the error direction
    is a HOLD, never a false binding."""
    verbs = _counted_verbs(tokens, verb_capable, possessor_indices)
    if use_g2 and len(verbs) > 1:
        seg_of = {}
        for si, seg in enumerate(_segments(tokens)):
            for idx in seg:
                seg_of[idx] = si
        by_seg = {}
        for v in verbs:
            by_seg.setdefault(seg_of[v], []).append(v)
        for _si, vs in by_seg.items():
            if len(vs) > 1:
                for a, b in zip(vs, vs[1:]):
                    if b - a > 1:
                        return "multi_verb_unsegmented"
    # Guard 4: exactly one finite verb; the first non-article
    # token after it is a preposition -> no direct-object NP
    # precedes the preposition.
    if use_g4 and len(verbs) == 1:
        v = verbs[0]
        j = v + 1
        while j < len(tokens) and tokens[j] in ARTICLES:
            j += 1
        if j < len(tokens) and tokens[j] in PREPOSITIONS:
            return "prepositional_barrier:" + tokens[j]
    return None


def _span_head_candidate(tokens, span, noun_capable,
                         possessor_indices):
    """The phrase-head candidate of a span under the finder
    template (the head is the phrase's last nominal token):
    the rightmost noun-capable token, skipping articles and
    possessor stems. Returns (token, index) or (None, None)."""
    cand = (None, None)
    for i in span:
        if i in possessor_indices or tokens[i] in ARTICLES:
            continue
        if noun_capable(tokens[i]):
            cand = (tokens[i], i)
    return cand


def _np_prefix_idx(tokens, idxs):
    """The index-span truncated at its first preposition token:
    the noun phrase proper. A prepositional phrase inside a
    subject or object span ("the wolf of the forest", "eats
    with the wolf") belongs to the preposition layer; the slot
    guards judge only the NP before it."""
    out = []
    for i in idxs:
        if tokens[i] in PREPOSITIONS:
            break
        out.append(i)
    return out


def _single_verb_segments(tokens, noun_capable, verb_capable,
                          possessor_indices):
    """Yield (segment_indices, verb_index) for each segment
    (splitter-delimited run) whose verb geometry is determined
    for the slot guards: exactly one counted finite verb — or
    exactly two ADJACENT counted verbs where the first is
    noun-capable and a non-empty object span follows the
    second (the first is then a noun-phrase-internal token, as
    in "the wolf pack hunts the sheep": the counted-verb list
    sees "pack", the phrase parse sees a subject NP ending in
    "pack" and the verb "hunts"). Segments of any other shape
    are owned by the other guards and the consumers' ambiguity
    machinery (e.g. "The sheep hunt duck." keeps its
    ambiguous_verb_candidates HOLD: there the second verb has
    no object span, and two full parses compete)."""
    verbs = set(_counted_verbs(tokens, verb_capable,
                                possessor_indices))
    for seg in _segments(tokens):
        vs = [i for i in seg if i in verbs]
        if len(vs) == 1:
            yield seg, vs[0]
        elif len(vs) == 2 and vs[1] - vs[0] == 1:
            if tokens[vs[1]] in PREPOSITIONS:
                # Verb + preposition ("eats with the wolf"): the
                # clause verb is the first; the preposition opens
                # a PP the slot guards truncate away.
                yield seg, vs[0]
            elif noun_capable(tokens[vs[0]]) \
                    and any(i > vs[1] for i in seg):
                yield seg, vs[1]


def adverb_slot_check(tokens, noun_capable, verb_capable,
                      possessor_indices=frozenset()):
    """Guard 6 (Pack A; claims-03 W1/W2/W5, claims-07 2.1): an
    adverb-attested token as a noun-phrase head. Returns a HOLD
    tag or None.

    Subject span: the head candidate is the rightmost
    noun-capable token before the segment's verb. If it carries
    adverb evidence — morphology adverbial_ly, or membership in
    the closed unmarked-adverb lexeme list — its noun reading
    is not determined in this sequence (for the -ly forms the
    adverb use is the determined one, and the subject template
    has no adverb slot; for the lexemes both uses are attested
    and nothing ranks them) -> HOLD adverb_in_subject_slot:<w>.
    This is the lock the P0 demote applied to targets only;
    "The wolf never eats the sheep." bound never as subject at
    every finder and the false binding promoted to VERIFIED in
    the store (claims-03 W2).

    Object span: the same rule for the head candidate after the
    verb, with one determined exception — a sentence-final
    adverbial_ly token is a determined adverb use, placed by
    demote_adverb_target, when it is the span's only content
    token (the sealed L5a geometry) or when a token that can
    serve as the phrase head precedes it (noun-capable and not
    modifier-capable: "the sheep" in "eats the sheep
    quickly"). A lexeme adverb as head ("hunts well", "eats the
    sheep well") and an adverbial_ly token with no servable
    head before it ("runs big quickly" — "big" is a modifier,
    not a head) -> HOLD adverb_in_target_slot:<w>."""
    for seg, v in _single_verb_segments(tokens, noun_capable, verb_capable,
                                         possessor_indices):
        subj_span = _np_prefix_idx(tokens,
                                   [i for i in seg if i < v])
        cand = (None, None)
        if not any(tokens[i] in AUX_ALL for i in subj_span):
            cand = _span_head_candidate(tokens, subj_span,
                                        noun_capable,
                                        possessor_indices)
        if cand[0] is not None and adverb_attested(cand[0]):
            return "adverb_in_subject_slot:" + cand[0]
        obj_span = _np_prefix_idx(tokens,
                                  [i for i in seg if i > v])
        if any(tokens[i] in AUX_ALL for i in obj_span):
            continue
        cand, ci = _span_head_candidate(tokens, obj_span,
                                        noun_capable,
                                        possessor_indices)
        if cand is None or not adverb_attested(cand):
            continue
        if cand in ADVERB_SLOT_LEXEMES and not _adverbial_ly(cand):
            return "adverb_in_target_slot:" + cand
        # cand is a determined adverbial_ly form.
        content = [i for i in obj_span
                   if tokens[i] not in ARTICLES
                   and i not in possessor_indices]
        if len(content) <= 1 and not any(
                tokens[i] in ARTICLES for i in obj_span):
            # The bare post-verbal adverb (sealed L5a): no noun
            # phrase is projected, demote places the adverb.
            # An article changes the geometry — "the quickly"
            # projects a noun phrase whose head would be the
            # adverb (Argon F3) — and falls through to HOLD.
            continue
        servable = any(noun_capable(tokens[i])
                       and not modifier_capable(tokens[i])
                       for i in obj_span if i < ci)
        if not servable:
            return "adverb_in_target_slot:" + cand
    return None


def adjunct_check(tokens, noun_capable, verb_capable,
                  possessor_indices=frozenset()):
    """Guard 7 (Pack A; claims-07 2.2, divergence D22): a
    modifier whose adjective use is not attested. Returns a
    HOLD tag or None.

    Behaviour aligned on HOLD, in the shared layer, with the
    adjective microscript's own tag: for each one-verb segment's
    subject and object spans, every modifier (span token before
    the phrase head, articles and possessor stems excluded)
    must carry the measured modifier-capability evidence
    (modifier_capable). A modifier without it is a noun
    adjunct candidate — English places bare nouns in attributive
    position ("sheep" in "a sheep dog") and this dictionary
    carries no marker separating the readings — undecidable in
    this scope -> HOLD ambiguous_modifier_noun_adjunct:<w>.
    Before Pack A the noun, verb and adverb finders asserted
    such a modifier with use "adjective" while the adjective
    finder HOLDed the identical sentence; the surfaces now
    agree. An object span whose final token is a determined
    adverbial_ly form with a servable head before it is judged
    on the span up to that head (the demote geometry), not on
    the adverb."""
    for seg, v in _single_verb_segments(tokens, noun_capable, verb_capable,
                                         possessor_indices):
        spans = [(_np_prefix_idx(tokens, [i for i in seg if i < v]),
                  False),
                 (_np_prefix_idx(tokens, [i for i in seg if i > v]),
                  True)]
        for span, is_object in spans:
            if any(tokens[i] in AUX_ALL for i in span):
                # Auxiliary geometry inside a span is the scope
                # rules' territory, not a phrase to judge.
                continue
            idxs = list(span)
            if idxs and tokens[idxs[0]] in ARTICLES:
                idxs = idxs[1:]
            if not idxs:
                continue
            if any(tokens[i] in ARTICLES for i in idxs):
                # A second article means a second noun phrase
                # has begun inside the span (reduced relatives,
                # apposition: "the man the dog bit ran") — not
                # one NP, so no adjunct judgment is made here;
                # the consumers' own geometry rules own it.
                continue
            head_i = idxs[-1]
            if is_object and _adverbial_ly(tokens[head_i]):
                servable = [i for i in idxs[:-1]
                            if noun_capable(tokens[i])
                            and not modifier_capable(tokens[i])]
                if servable:
                    head_i = servable[-1]
                    idxs = [i for i in idxs if i <= head_i]
            if not noun_capable(tokens[head_i]):
                continue
            for i in idxs:
                if i == head_i or i in possessor_indices:
                    continue
                tok = tokens[i]
                if tok in ARTICLES or tok in INTERJECTION_TOKENS:
                    # Clause furniture, not an attributive
                    # modifier: an interjection before the clause
                    # ("Oh, the wolf howls.") is judged by the
                    # interjection layer, never as an adjunct.
                    continue
                if pmca_dictionary.lookup(tok) is None:
                    # Unattested token: the consumers' unknown
                    # scan owns it; no adjunct judgment is made
                    # on no evidence.
                    continue
                if not modifier_capable(tok):
                    return ("ambiguous_modifier_noun_adjunct:"
                            + tok)
    return None


def guard_geometry(tokens, verb_capable):
    """Guards 5, 2, 4 over a normalized token list, in binding
    order (Guard 3 is surface-specific: passive_signature
    below; Guard 1 lives in the consumers' ambiguity
    machinery). Returns a HOLD tag or None."""
    for tok in tokens:
        if tok in RELATIVE_PRONOUNS:
            return "non_svo_np_geometry"
    return _g2_g4(tokens, verb_capable)


def passive_signature(tokens, verb_capable):
    """Guard 3 geometry: an AUX_BE token immediately followed
    by a verb-form token, or an AUX_BE token with a later "by"
    phrase. Returns True when the passive signature is present."""
    for i, tok in enumerate(tokens):
        if tok in AUX_BE:
            if i + 1 < len(tokens) and verb_capable(tokens[i + 1]) \
                    and tokens[i + 1] not in AUX_ALL:
                return True
            if "by" in tokens[i + 1:]:
                return True
    return False


def grammar_lock(tokens):
    """The finder microscripts' grammar-word lock: a modal, a
    do-auxiliary, or negation anywhere in the sentence HOLDs
    with the scope tag — those words' uses are grammatical, and
    v1 scope cannot place them, so they must never be read as
    content. Returns the scope tag or None."""
    for tok in tokens:
        if tok in GRAMMAR_LOCK:
            return SCOPE_TAG
    return None


def negated_target_check(span, head_capable):
    """ST-20 guard (2026-10-27): "not" inside a clause's target
    span, before the noun-phrase head, negates the target
    itself. The analyzer's head scan breaks a span at "not" —
    correct for the verb group in a SUBJECT span ("The wolf
    does not eat ...") — but in a TARGET span the break drops
    the target with no candidate seen, and before this guard
    the clause was skipped silently and the sentence approved
    below the gate ("He deliberately poisons not the child."
    -> APPROVE (0, 1); the control REFUSEs). Negation geometry
    is out of v1 scope exactly as it is for "never"
    (negation_out_of_scope:never); the grammar lock above
    covers the finder surfaces sentence-wide, but it does not
    run on the analyzer surface (the analyzer parses the verb
    group itself), so this guard covers the one position the
    lock cannot see: the span after the clause's verb. The scan
    mirrors the head scan's: a preposition before any head
    candidate does not end the span (its object may still be
    the head), one after the head does; an auxiliary ends it
    (a following verb group is not this clause's target).
    "not" AFTER the head is a contrastive tail ("breaks the
    cup not the window") — the head was bound and evaluated,
    not dropped — and returns None. Returns the HOLD tag or
    None."""
    head_seen = False
    for tok in span:
        if tok in NEGATION:
            return None if head_seen else "negation_out_of_scope:not"
        if tok in AUX_ALL:
            break
        if tok in PREPOSITIONS:
            if head_seen:
                break
            continue
        if head_capable(tok):
            head_seen = True
    return None


def screen(tokens, noun_capable, verb_capable,
           guards=("genitive", "g5", "grammar", "g2", "g4",
                   "adverb", "adjunct")):
    """The full shared screen, in binding order: genitive
    normalization first (tokenization layer), then Guard 5,
    the grammar lock, Guard 2, Guard 4, and (Pack A) the
    adverb slot guard and the noun-adjunct guard. Returns the
    normalize() dict plus "hold" possibly set by a later guard
    (on a guard HOLD, tokens/genitives from normalization are
    preserved for the caller's record but MUST NOT be bound
    from)."""
    if "genitive" in guards:
        res = normalize(tokens, noun_capable, verb_capable)
    else:
        res = {"hold": None, "tokens": list(tokens),
               "genitives": [], "possessor_indices": set(),
               "expanded_aux_be": False}
    if res["hold"] is not None:
        return res
    toks = res["tokens"]
    if "g5" in guards:
        for tok in toks:
            if tok in RELATIVE_PRONOUNS:
                res["hold"] = "non_svo_np_geometry"
                return res
    if "grammar" in guards:
        tag = grammar_lock(toks)
        if tag is not None:
            res["hold"] = tag
            return res
    if "g2" in guards or "g4" in guards:
        tag = _g2_g4(toks, verb_capable,
                     possessor_indices=res["possessor_indices"],
                     use_g2="g2" in guards, use_g4="g4" in guards)
        if tag is not None:
            res["hold"] = tag
            return res
    if "adverb" in guards:
        tag = adverb_slot_check(
            toks, noun_capable, verb_capable,
            possessor_indices=res["possessor_indices"])
        if tag is not None:
            res["hold"] = tag
            return res
    if "adjunct" in guards:
        tag = adjunct_check(
            toks, noun_capable, verb_capable,
            possessor_indices=res["possessor_indices"])
        if tag is not None:
            res["hold"] = tag
            return res
    return res


def demote_adverb_target(result):
    """The adverb lock on an OK finder result: if the bound
    target is the sentence-final token and its morphology
    attestation is adverbial_ly, its use in this sequence is
    adverb — rebuild the result with target None and the token
    relabeled as an adverb use. Shared by every finder surface
    (their result dicts share the sentence/verdict/hold_reason/
    uses/subject/verb/target core). Returns the result,
    corrected in a copy; non-OK results pass through."""
    if result.get("verdict") != "OK" or not result.get("target"):
        return result
    target = result["target"]
    uses = result.get("uses") or []
    entry = None
    for u in uses:
        if u["word"] == target:
            entry = u
    if entry is None:
        return result
    # The target must be the final token of the sentence.
    text = result["sentence"].strip().lower().rstrip(".!?")
    toks = text.split()
    if not toks or toks[-1] != target:
        return result
    res = _morph.resolve_form(target)
    if not (res["verdict"] == "OK" and res["form"] == "adverbial_ly"):
        return result
    new_uses = []
    for u in uses:
        if u is entry and u["use"] != "adverb":
            # Relabel only a wrongly-asserted use; a surface
            # that already determined the adverb use keeps its
            # own subcoordinate record untouched.
            new_uses.append({"word": target, "use": "adverb",
                             "subcoordinate": None})
        else:
            new_uses.append(u)
    out = dict(result)
    out["uses"] = new_uses
    out["target"] = None
    return out


def apply_genitive_labels(result, genitives):
    """Possessor labeling on an OK primary-finder result: a
    possessor stem the phrase template labeled "adjective" is
    relabeled to its determined use — noun, subcoordinate
    "possessor" — and the result gains a "possessors" list of
    {"head", "possessor"} records. Returns the result,
    corrected in a copy."""
    if result.get("verdict") != "OK" or not genitives:
        return result
    uses = [dict(u) for u in (result.get("uses") or [])]
    records = []
    for g in genitives:
        stem = g["possessor"]
        for u in uses:
            if u["word"] == stem and u["use"] == "adjective":
                u["use"] = "noun"
                u["subcoordinate"] = "possessor"
                break
        records.append({"head": g["head"], "possessor": stem})
    out = dict(result)
    out["uses"] = uses
    out["possessors"] = records
    return out
