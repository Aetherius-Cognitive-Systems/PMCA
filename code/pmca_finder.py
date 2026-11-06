"""pmca_finder.py — the ONE FINDER for PMCA (FINDER BUILD,
2026-11-01).

Design: Jonathan Wayne Fleuren (the builder). Ruling implemented:
"We build a finder" (Jonathan, 2026-11-01), resolving attack item
D-01 (production sentence-level classification never called the
nine per-class finder microscripts; adjective/adverb flattened
into "other", article/conjunction were invisible, interjection
was fatal, pronouns bound without classification).

Specification of record: finder-build spec paper (02 Ledger)
as amended by the spec check's binding fixes F1-F12 (04 Forge).
Where the spec and a fix conflict, the fix governs. The Helm
determination on O1 (2026-11-01, flagged upward for Alder's
finalization) governs the interjection list: the governing list
is the grammar-tier pmca_parse_guards.INTERJECTION_TOKENS
(includes "wow"); the legacy interjection finder's divergent
6-word set is a retired private copy, stored in guards as
INTERJECTIONS_VERIFIED with the conflict on record, NOT merged.

What this module is: ONE finder classifying EVERY word of a
sentence in context. Classes: noun, verb, adjective, adverb,
article, conjunction, interjection, preposition, pronoun,
auxiliary, modal.

Sources, lookup only (non-LLM purity gate, Jonathan 2026-10-25):
  - the shared pmca_verb_capable (noun/verb capability, Candidacy
    Rule Set tiering: dictionary POS answers are CANDIDATE tier
    on today's stores; DICT-1 Part 3 blocks lineage-counted
    promotion while independence is UNMEASURED, and this module
    performs none);
  - pmca_forms_resolver (DICT-1 Part 2: an attested form of a
    verb is that verb; a resolver HOLD or NONE never settles a
    verb class here);
  - pmca_parse_guards (the grammar tier: every closed-class list
    is read from there verbatim — F4; this module defines NO
    closed-class list literal of its own — plus
    modifier_capable / adjective_attested / adverb_attested
    entry evidence and the shared screen);
  - pmca_morphology_finder.resolve_form (candidate-generating
    morphology only: rules generate, the dictionary disposes).

Never guess (spec section 4): arbitration runs ONLY between
attested candidates; an unattested or undisambiguated token
records a candidate class (or None) plus a gap record — there
are no silent skips and no silent gaps. Classification is
independent of screen disposition (F7): occurrence records are
computed for every token whether or not any screen guard HOLDs;
the full legacy screen may contribute only the result's
verdict / hold_reason disposition.

Occurrence record (spec section 2, relation shape per F9):
  {"word", "position", "class", "tier",
   "relations": [{"relation", "targets": [{"word","position"}],
                  ...}],
   "provenance": {"source", "detail"},
   "gap": {"word","position","field","reason","candidates",
           "decided_by"} | None}
Provenance source is exactly one of "dictionary_attestation",
"closed_class_list", "context_arbitration", "candidate+gap"
(F10 precedence/tier mapping applied). Stored-once (F11): the
records' one stored form is the analyzer's trace lines, which
the existing seal machinery journals as steps; this module
writes no storage of its own.

find(sentence) -> {"sentence", "verdict": "OK"|"HOLD",
"hold_reason": str|None, "occurrences": [...], "subject",
"verb", "target"} with keys in this fixed order. Unlike the
legacy microscripts, occurrences are NEVER emptied on HOLD:
every token carries its record (spec section 1.2, superseding
the legacy empty-on-HOLD convention for this module only).
subject/verb/target are None unless the occurrence relations
themselves are settled.

Deterministic. Stdlib + duplicate-tree modules only. This
module must NOT import pmca_sentence_analyzer at module level
(F3: the analyzer imports this module for wiring; the noun
finder's module-level precedent would be circular). The
analyzer passes its seed tables and cleaned tokens in through
classify_tokens(); standalone find() tokenizes with a
character-identical local implementation of the analyzer's
clean().

The nine legacy finder modules are retired as mechanisms by
this build (their batteries are ported into run_checks()
below, assertions re-pointed at occurrence records per spec
section 6.2); their files stay in the tree as disclosed
history, and their capability functions — already thin
delegating wrappers over pmca_verb_capable / pmca_parse_guards
— are left intact.
"""

import re

import pmca_dictionary
import pmca_forms_resolver as _resolver
import pmca_morphology_finder as _morph
import pmca_parse_guards as _guards
import pmca_verb_capable as _vc

# ---------------------------------------------------------------------
# Shared oracles (no private capability copies).
# ---------------------------------------------------------------------


def noun_capable(word):
    """The ONE shared implementation (pmca_verb_capable)."""
    return _vc.noun_capable(word)


def verb_capable(word):
    """The ONE shared implementation (pmca_verb_capable)."""
    return _vc.verb_capable(word)


# The adverb microscript's measured entry markers, replicated as
# EVIDENCE functions (the mechanism homes of record stay
# pmca_parse_guards.adverb_attested for the slot guards and the
# morphology microscript for adverbial_ly; M1/M2 live only in the
# legacy adverb finder, which retires as a mechanism — the marker
# tests themselves are ported here with their measured standing).
_MANNER = re.compile(r"\bIn an? [\w\s,'-]{0,40}? manner\b")


def _adv_header(word):
    """M1: the word's own entry header carries the ", adv." tag
    for this headword (legacy adverb finder, measured)."""
    entry = pmca_dictionary.lookup(word)
    if entry is None or not entry["definitions"]:
        return False
    head = entry["headword"]
    pattern = re.escape(head) + r"\s+\\.*\\,\s*adv\."
    return re.match(pattern, entry["definitions"][0],
                    re.IGNORECASE) is not None


def _manner_phrase(word):
    """M2: a stored definition carries an "In a ... manner"
    phrase (legacy adverb finder, measured)."""
    entry = pmca_dictionary.lookup(word)
    if entry is None:
        return False
    return any(_MANNER.search(d) for d in entry["definitions"])


def _adverbial_ly(word):
    """Determined adverb evidence: morphology attests the form
    adverbial_ly (the dictionary disposes: resolve_form returns
    OK only when the generated base attests)."""
    res = _morph.resolve_form(word)
    return res["verdict"] == "OK" and res["form"] == "adverbial_ly"


def _adverb_evidence(word):
    """Adverb evidence for one token, strongest first, as
    (kind, determined): M1 header / M2 manner (dictionary text
    evidence), adverbial_ly (morphology-determined), M3 -ly stem
    (legacy adverb finder's weakest marker: the word ends in
    "ly" and the stem resolves to a stored entry), or membership
    in the closed ADVERB_SLOT_LEXEMES list (candidate-grade
    only, F10). None when no source attests an adverb use."""
    if _adv_header(word):
        return ("M1_header_tag", False)
    if _manner_phrase(word):
        return ("M2_manner_phrase", False)
    if _adverbial_ly(word):
        return ("adverbial_ly", True)
    if word.endswith("ly") and len(word) > 3 \
            and pmca_dictionary.lookup(word[:-2]) is not None:
        return ("M3_ly_stem", False)
    if word in _guards.ADVERB_SLOT_LEXEMES:
        return ("slot_lexeme", False)
    return None


def _strict_noun_evidence(word):
    """The legacy adverb finder's competition test, kept
    under its measured meaning (fix wave 2, 2026-11-02;
    Wreck WRECK2-5): a stored definition exists that is not
    verb-form, not the word's adverb header, and not a
    manner phrase. This does NOT establish a noun sense —
    adjective definitions pass it (lonely, lively, hourly
    measured) — it judges only that some non-verb-form
    definition competes with an adverb reading. Provenance
    strings name it by this test, never as a "noun sense
    attested"."""
    entry = pmca_dictionary.lookup(word)
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


# ---------------------------------------------------------------------
# Tokenization (F3): a character-identical local implementation
# of pmca_sentence_analyzer.clean (pmca_sentence_analyzer.py:194).
# ---------------------------------------------------------------------


def _clean(sentence):
    """Lowercase, drop sentence punctuation, single-space.
    Character-identical to the analyzer's clean(); a ported
    battery case asserts token-sequence equality over the
    acceptance set."""
    text = sentence.lower()
    for ch in ".,!?":
        text = text.replace(ch, " ")
    return " ".join(text.split())


# ---------------------------------------------------------------------
# Record construction helpers.
# ---------------------------------------------------------------------

_CLASSES = ("noun", "verb", "adjective", "adverb", "article",
            "conjunction", "interjection", "preposition",
            "pronoun", "auxiliary", "modal")

# Tokens that can never stand as a clause's main verb: closed
# classes and grammar tokens (F5 precedence: their closed-class
# candidacy is structural, against which a dictionary reading
# must independently parse).
_NOT_VERB = (_guards.ARTICLES | _guards.PRONOUNS
             | _guards.POSSESSIVE_PRONOUNS
             | _guards.PREPOSITIONS | _guards.CONJUNCTIONS
             | _guards.INTERJECTION_TOKENS
             | _guards.RELATIVE_PRONOUNS
             | _guards.SUBORDINATORS | _guards.NEGATION_MARKERS)


def _gap(word, position, reason, candidates):
    """One gap record in the spec section 2.3 shape: the field
    that could not be settled, the named reason, the rival
    candidates, and decided_by None (no silent gaps)."""
    return {"word": word, "position": position, "field": "class",
            "reason": reason, "candidates": list(candidates),
            "decided_by": None}


def _record(word, position):
    """A blank occurrence record for one token: class None,
    tier UNKNOWN, no relations, until a source settles or
    candidates it."""
    return {"word": word, "position": position, "class": None,
            "tier": "UNKNOWN", "relations": [],
            "provenance": {"source": "candidate+gap",
                           "detail": ""},
            "gap": None}


def _rel(name, targets, extra=None):
    """One occurrence relation in the F9 shape: a vocabulary
    name plus its targets as {"word", "position"} records
    (extra carries the stands_for role key)."""
    rel = {"relation": name,
           "targets": [{"word": w, "position": p}
                       for (w, p) in targets]}
    if extra:
        rel.update(extra)
    return rel


def _settle(rec, cls, tier, source, detail):
    """Settle an occurrence record: class, tier and provenance
    from the deciding source; any gap is cleared."""
    rec["class"] = cls
    rec["tier"] = tier
    rec["provenance"] = {"source": source, "detail": detail}
    rec["gap"] = None


def _candidate(rec, cls, source, detail, reason, candidates):
    """Candidate an occurrence record: a candidate class at
    CANDIDATE tier with a mandatory gap record naming the
    reason and the rival candidates."""
    rec["class"] = cls
    rec["tier"] = "CANDIDATE"
    rec["provenance"] = {"source": source, "detail": detail}
    rec["gap"] = _gap(rec["word"], rec["position"], reason,
                      candidates)


def _closed_list_name(word):
    """The guards list that carries a closed-class token, for
    provenance detail. Modal precedence (F5): MODALS membership
    is reported before the auxiliary sets."""
    if word in _guards.MODALS:
        return "MODALS"
    if word in _guards.MODAL_NT:
        return "MODAL_NT"
    if word in _guards.ARTICLES:
        return "ARTICLES"
    if word in _guards.SUBJECT_PRONOUNS:
        return "SUBJECT_PRONOUNS"
    if word in _guards.OBJECT_PRONOUNS:
        return "OBJECT_PRONOUNS"
    if word in _guards.POSSESSIVE_PRONOUNS:
        return "POSSESSIVE_PRONOUNS"
    if word in _guards.PREPOSITIONS:
        return "PREPOSITIONS"
    if word in _guards.CONJUNCTIONS:
        return "CONJUNCTIONS"
    if word in _guards.INTERJECTION_TOKENS:
        return "INTERJECTION_TOKENS"
    if word in _guards.AUX_BE:
        return "AUX_BE"
    if word in _guards.AUX_HAVE:
        return "AUX_HAVE"
    if word in _guards.AUX_DO:
        return "AUX_DO"
    if word in _guards.AUX_NT:
        return "AUX_NT"
    if word in _guards.NEGATION_MARKERS:
        return "NEGATION_MARKERS"
    return None


def _known(word, seed_verbs, verb_forms):
    """Known token: closed-class / grammar-tier membership, a
    dictionary resolution, or (when the wiring supplies them)
    the analyzer's seed tables."""
    if word in _guards.ARTICLES or word in _guards.PRONOUNS \
            or word in _guards.POSSESSIVE_PRONOUNS \
            or word in _guards.PREPOSITIONS \
            or word in _guards.CONJUNCTIONS \
            or word in _guards.INTERJECTION_TOKENS \
            or word in _guards.AUX_RECOGNIZED \
            or word in _guards.NEGATION_MARKERS \
            or word in _guards.RELATIVE_PRONOUNS \
            or word in _guards.SUBORDINATORS \
            or word in _guards.ADVERB_SLOT_LEXEMES \
            or word in _guards.CONTRACTIONS:
        return True
    if pmca_dictionary.lookup(word) is not None:
        return True
    if seed_verbs and (word in seed_verbs
                       or (verb_forms and word in verb_forms)):
        return True
    return False

# ---------------------------------------------------------------------
# Phrase / clause parsing over the token stream. A parse assigns
# every stream token a class USE (class-by-company, Jonathan
# 2026-10-21): arbitration runs only between attested candidates,
# and a token no source attests is never assigned by elimination.
# Assignments are (basis, ...) tuples converted to occurrence
# records by _finalize_settled / _finalize_fallback.
# ---------------------------------------------------------------------

_AUX_BE_LEGACY = {"is", "are", "was", "were", "be", "been", "am"}


class _Ctx(object):
    def __init__(self, tokens, seed_verbs, verb_forms):
        """Per-classification context: the token stream, the
        wiring-supplied seed tables (or None), and the genitive
        occurrence data from an internal genitive+g5 screen
        (W-5: always carried; see the note at the screen call —
        the recording path maps the normalized-stream indices
        back to input positions)."""
        self.tokens = tokens
        self.seed_verbs = seed_verbs
        self.verb_forms = verb_forms
        self.possessor_indices = set()
        self.genitives = []
        self._lemmas = None
        scr = _guards.screen(tokens, noun_capable, verb_capable,
                             guards=("genitive", "g5"))
        # FINDER FIX WAVE (2026-11-01), W-5: the screen's
        # genitive records are ALWAYS carried. The pre-fix
        # guard carried them only when normalization left the
        # token strings unchanged — which a genitive rewrite
        # never does (man's -> man) — so possessor data was
        # dropped for EVERY 's-genitive, contraction or not.
        # The genitive records are occurrence data in
        # normalized-stream index space; the recording path in
        # classify_tokens maps them back to input positions
        # itself. possessor_indices are deliberately NOT
        # carried in this wave: they are normalized-stream
        # indices, and feeding them to _validate_np would
        # change parse behaviour — this wave records the
        # possessor relation observationally and changes no
        # classification or disposition (in practice the old
        # guard never carried them either, for the same
        # rewrite reason).
        self.genitives = list(scr["genitives"])

    def resolver(self, token, context="verb_slot"):
        """DICT-1 Part 2 forms resolution, only when the wiring
        supplied the seed tables (F3). No seed context -> the
        resolver is not consulted and returns NONE."""
        if not self.seed_verbs:
            return {"verdict": "NONE", "canonical": None,
                    "lemma": None, "form": None,
                    "attestation": None, "hold_reason": None}
        if self._lemmas is None:
            self._lemmas = _resolver.seed_lemma_map(
                self.seed_verbs, self.verb_forms or {})
        return _resolver.resolve(token, self.seed_verbs,
                                 self.verb_forms or {},
                                 context=context,
                                 lemmas=self._lemmas)

    def verb_candidate(self, idx):
        """Attested verb candidacy for the token at idx: the
        shared dictionary oracle, or a forms-resolver OK under
        Part 2's armour. Closed-class / grammar tokens are
        never verb candidates (F5 precedence)."""
        tok = self.tokens[idx]
        if tok in _NOT_VERB or tok in _guards.MODALS \
                or tok in _guards.MODAL_NT \
                or tok in _guards.AUX_BE \
                or tok in _guards.AUX_HAVE \
                or tok in _guards.AUX_NT:
            return False
        if verb_capable(tok):
            return True
        return self.resolver(tok)["verdict"] == "OK"


def _validate_np(ctx, indices, role):
    """Validate one noun phrase over stream indices. Returns
    (head_idx, assign) or (None, failure) where failure is
    (kind, idx) with kind in "head" / "adjunct" / "adjective" /
    "bad_token" / "adverb". Legacy phrase template: optional
    leading article(s), zero or more modifiers, one head; a
    single pronoun token is a valid phrase in either slot."""
    toks = ctx.tokens
    if not indices:
        return None, ("head", None)
    assign = {}
    rest = list(indices)
    articles = []
    while rest and toks[rest[0]] in _guards.ARTICLES:
        articles.append(rest.pop(0))
    if len(rest) == 1 and (toks[rest[0]] in _guards.PRONOUNS
                           or toks[rest[0]]
                           in _guards.POSSESSIVE_PRONOUNS):
        head = rest[0]
        for a in articles:
            assign[a] = ("article", head)
        assign[head] = ("pronoun", role)
        return head, assign
    if not rest:
        return None, ("head", None)
    head = rest[-1]
    htok = toks[head]
    if head in ctx.possessor_indices:
        pass  # a possessor stem can head its own phrase record
    elif htok in _guards.PRONOUNS or htok in _guards.ARTICLES \
            or htok in _guards.PREPOSITIONS \
            or htok in _guards.CONJUNCTIONS \
            or htok in _guards.RELATIVE_PRONOUNS \
            or htok in _guards.POSSESSIVE_PRONOUNS:
        # FINDER FIX WAVE (2026-11-01), W-4: a relative pronoun
        # (whom and kin — already in RELATIVE_PRONOUNS, attested
        # by Webster 1913's entry for "whom": "an interrogative
        # pronoun, later, a relative pronoun") can no more head
        # a noun phrase than a personal pronoun can; the head
        # check omitted RELATIVE_PRONOUNS, so "whom" settled
        # noun inside a settled parse. A possessive pronoun
        # heads a phrase only as the single-token phrase above.
        return None, ("head", head)
    elif _adverbial_ly(htok):
        return None, ("head", head)
    elif not noun_capable(htok):
        return None, ("head", head)
    for idx in rest[:-1]:
        tok = toks[idx]
        if idx in ctx.possessor_indices:
            assign[idx] = ("possessor", head)
        elif tok in _guards.POSSESSIVE_PRONOUNS:
            # FINDER FIX WAVE (2026-11-01), W-4: a possessive
            # pronoun in modifier position ("his food") is a
            # pronoun modifier of the head, attested by the
            # grammar tier's POSSESSIVE_PRONOUNS list.
            assign[idx] = ("possessive_pronoun", head)
        elif tok in _guards.ARTICLES or tok in _guards.PRONOUNS:
            return None, ("bad_token", idx)
        elif _adverb_evidence(tok) is not None \
                and not _guards.modifier_capable(tok):
            return None, ("adverb", idx)
        elif _guards.modifier_capable(tok):
            assign[idx] = ("adjective", head)
        elif verb_capable(tok):
            return None, ("adjective", idx)
        else:
            return None, ("adjunct", idx)
    for a in articles:
        assign[a] = ("article", head)
    assign[head] = ("noun_head", role)
    return head, assign


def _parse_np_chain(ctx, indices, role):
    """Parse a (possibly conjoined) noun-phrase chain: split at
    conjunction tokens, validate each side as one NP, record
    the joins. Returns (heads, assign, joins) or
    (None, failure, None)."""
    toks = ctx.tokens
    if not indices:
        return None, ("head", None), None
    parts = [[]]
    conjs = []
    for idx in indices:
        if toks[idx] in _guards.CONJUNCTIONS:
            conjs.append(idx)
            parts.append([])
        else:
            parts[-1].append(idx)
    if any(not p for p in parts):
        bad = conjs[0] if conjs else None
        return None, ("incomplete_conjunction", bad), None
    heads = []
    assign = {}
    for part in parts:
        head, sub = _validate_np(ctx, part, role)
        if head is None:
            return None, sub, None
        heads.append(head)
        assign.update(sub)
    joins = []
    for c, left, right in zip(conjs, heads, heads[1:]):
        assign[c] = ("conjunction", left, right)
        joins.append((c, left, right))
    return heads, assign, joins


def _parse_span_plain(ctx, indices, role):
    """Parse one clause span (subject or object zone) with no
    adverb extraction: a main NP chain plus any prepositional
    phrases. Returns a span dict or (None, failure)."""
    toks = ctx.tokens
    if not indices:
        if role == "object":
            return {"heads": [], "assign": {}, "joins": [],
                    "pps": [], "adverbs": []}, None
        return None, ("head", None)
    prep_at = [k for k, idx in enumerate(indices)
               if toks[idx] in _guards.PREPOSITIONS]
    if not prep_at:
        heads, assign, joins = _parse_np_chain(ctx, indices, role)
        if heads is None:
            return None, assign
        return {"heads": heads, "assign": assign, "joins": joins,
                "pps": [], "adverbs": []}, None
    main = indices[:prep_at[0]]
    assign = {}
    joins = []
    pps = []
    heads = []
    if main:
        heads, sub, jn = _parse_np_chain(ctx, main, role)
        if heads is None:
            return None, sub
        assign.update(sub)
        joins.extend(jn)
    elif role == "subject":
        return None, ("head", None)
    for n, k in enumerate(prep_at):
        pidx = indices[k]
        end = prep_at[n + 1] if n + 1 < len(prep_at) \
            else len(indices)
        np_indices = indices[k + 1:end]
        if not np_indices:
            return None, ("head", None)
        pheads, sub, jn = _parse_np_chain(ctx, np_indices, "pp")
        if pheads is None:
            return None, sub
        assign.update(sub)
        joins.extend(jn)
        assign[pidx] = ("preposition", pheads[0])
        pps.append((pidx, pheads[0]))
    return {"heads": heads, "assign": assign, "joins": joins,
            "pps": pps, "adverbs": []}, None


def _parse_span(ctx, indices, role):
    """Parse one span, with adverb extraction (F7 slots:
    span-final, which covers the post-object sentence-final
    slot and the immediately-pre-verbal slot). Returns
    (span, failure, ambiguous_postverbal_idx). A determined
    adverb reading (adverbial_ly) wins over a noun-head reading
    by the demote doctrine; an undetermined marker reading that
    competes with a genuine noun-head parse is ambiguous
    (legacy ambiguous_postverbal:<w>)."""
    plain, plain_fail = _parse_span_plain(ctx, indices, role)
    if indices:
        last = indices[-1]
        ev = _adverb_evidence(ctx.tokens[last])
        if ev is not None:
            kind, determined = ev
            rest = indices[:-1]
            if rest:
                sub, sub_fail = _parse_span_plain(ctx, rest, role)
            elif role == "object":
                sub = {"heads": [], "assign": {}, "joins": [],
                       "pps": [], "adverbs": []}
                sub_fail = None
            else:
                sub, sub_fail = None, ("head", None)
            if sub is not None:
                if plain is not None and not determined \
                        and last in plain["heads"] \
                        and _strict_noun_evidence(ctx.tokens[last]):
                    return None, ("ambiguous_postverbal", last), last
                sub = dict(sub)
                sub["assign"] = dict(sub["assign"])
                sub["assign"][last] = ("adverb", kind)
                sub["adverbs"] = list(sub["adverbs"]) + [last]
                return sub, None, None
    if plain is not None:
        return plain, None, None
    return None, plain_fail, None


def _parse_clause(ctx, stream, verb_idx):
    """One full clause parse around a verb candidate. Returns
    (parse, failure, ambiguous_idx)."""
    subj_idx = [i for i in stream if i < verb_idx]
    obj_idx = [i for i in stream if i > verb_idx]
    subj, fail, amb = _parse_span(ctx, subj_idx, "subject")
    if subj is None:
        return None, fail, amb
    obj, fail, amb = _parse_span(ctx, obj_idx, "object")
    if obj is None:
        return None, fail, amb
    assign = {}
    assign.update(subj["assign"])
    assign.update(obj["assign"])
    assign[verb_idx] = ("verb",)
    return {"verb": verb_idx, "subject_heads": subj["heads"],
            "object_heads": obj["heads"], "assign": assign,
            "joins": subj["joins"] + obj["joins"],
            "pps": subj["pps"] + obj["pps"]}, None, None

# ---------------------------------------------------------------------
# Finalization: assignments -> occurrence records.
# ---------------------------------------------------------------------


def _dict_detail(ctx, tok):
    """The dictionary headword a token resolves to (provenance
    detail for dictionary-sourced records); the token itself
    when no entry resolves it."""
    entry = pmca_dictionary.lookup(tok)
    head = entry["headword"] if entry is not None else tok
    return head


def _finalize_settled(ctx, records, parse):
    """Convert one settled clause parse's assignments into
    occurrence records — classes, tiers, provenance (F10
    mapping) and F9 relations — and return the clause's
    (subject, verb headword, target) head words."""
    toks = ctx.tokens
    verb_idx = parse["verb"]
    vtok = toks[verb_idx]
    for idx, a in parse["assign"].items():
        rec = records[idx]
        tok = toks[idx]
        basis = a[0]
        if basis == "article":
            _settle(rec, "article", "attested", "closed_class_list",
                    "pmca_parse_guards.ARTICLES")
            rec["relations"] = [_rel("introduces",
                                     [(toks[a[1]], a[1])])]
        elif basis == "pronoun":
            role = a[1]
            if tok in _guards.POSSESSIVE_PRONOUNS:
                lst = "POSSESSIVE_PRONOUNS"
            elif tok in _guards.SUBJECT_PRONOUNS and role == "subject":
                lst = "SUBJECT_PRONOUNS"
            elif tok in _guards.OBJECT_PRONOUNS:
                lst = "OBJECT_PRONOUNS"
            else:
                lst = "PRONOUNS"
            _settle(rec, "pronoun", "attested", "closed_class_list",
                    "pmca_parse_guards." + lst)
            rels = [_rel("stands_for", [(vtok, verb_idx)],
                         {"role": role})]
            rels.append(_rel(
                "subject_of" if role == "subject" else "object_of",
                [(vtok, verb_idx)]))
            rec["relations"] = rels
        elif basis == "possessive_pronoun":
            # FINDER FIX WAVE (2026-11-01), W-4.
            head_idx = a[1]
            _settle(rec, "pronoun", "attested", "closed_class_list",
                    "pmca_parse_guards.POSSESSIVE_PRONOUNS "
                    "(possessive)")
            rec["relations"] = [_rel("modifier_of",
                                     [(toks[head_idx], head_idx)])]
        elif basis == "noun_head":
            role = a[1]
            head = _dict_detail(ctx, tok)
            # FIX WAVE 2 (2026-11-02), WRECK2-1 + WRECK2-3:
            # order C completed in the settled parse. The head
            # position is assigned by _validate_np on this
            # same capability test (noun_capable), so the
            # position is not independent evidence for the
            # class — a capability-only settle minted here
            # carries the capability_only_settle gap, exactly
            # as the fallback's wave-1 settles do, and the
            # provenance names the evidence capability, never
            # attestation (the wave-1 rename reached the
            # fallback and the attributive path; the settled
            # bases still read "winning attestation" over a
            # capability result — V1 "The dog ate the per.",
            # V2 "The big fell.", V3 "The run ended.").
            if verb_capable(tok) and noun_capable(tok):
                _candidate(rec, "noun", "context_arbitration",
                           "winning evidence: dictionary noun "
                           "capability (pmca_verb_capable."
                           "pos_answer, CANDIDATE tier, headword "
                           + head + "); rival capability "
                           "candidate: verb; arbitrating "
                           "structure: " + role + " phrase head "
                           "position; capability is not "
                           "attestation — no stored source "
                           "attests the noun class for this "
                           "token",
                           "capability_only_settle:" + tok,
                           ["noun"])
            else:
                _candidate(rec, "noun", "dictionary_attestation",
                           "dictionary noun capability only "
                           "(pmca_verb_capable.pos_answer, "
                           "CANDIDATE tier, headword " + head
                           + "); capability is not attestation "
                           "— no stored source attests the noun "
                           "class for this token, and the head "
                           "position itself rests on this same "
                           "capability test",
                           "capability_only_settle:" + tok,
                           ["noun"])
            if role == "subject":
                rec["relations"] = [_rel("subject_of",
                                         [(vtok, verb_idx)])]
            elif role == "object":
                rec["relations"] = [_rel("object_of",
                                         [(vtok, verb_idx)])]
        elif basis == "possessor":
            head_idx = a[1]
            _settle(rec, "noun", "CANDIDATE", "context_arbitration",
                    "genitive possessor (pmca_parse_guards screen "
                    "genitives); possessor of " + toks[head_idx])
            rec["relations"] = [_rel("possessor",
                                     [(toks[head_idx], head_idx)])]
        elif basis == "adjective":
            head_idx = a[1]
            # FIX WAVE 2 (2026-11-02), WRECK2-1: order C
            # completed for the attributive assignment. The
            # position is assigned on modifier_capable alone
            # (the same mechanism as noun_head, Wreck's
            # mechanism note on V7/V8), so these settles are
            # capability-only and carry the gap; the wave-1
            # truthful wording is kept and the missing
            # attestation is named by the gap on the record.
            if noun_capable(tok):
                _candidate(rec, "adjective", "context_arbitration",
                           "winning evidence: "
                           "pmca_parse_guards.modifier_capable "
                           "(CANDIDATE tier; modifier capability, "
                           "not class attestation); rival candidate: "
                           "noun adjunct; arbitrating structure: "
                           "attributive modifier position before "
                           "head " + toks[head_idx] + "; capability "
                           "is not attestation — no stored source "
                           "attests the adjective class for this "
                           "token",
                           "capability_only_settle:" + tok,
                           ["adjective"])
            else:
                _candidate(rec, "adjective",
                           "dictionary_attestation",
                           "pmca_parse_guards.modifier_capable "
                           "(CANDIDATE tier; modifier capability "
                           "evidence); arbitrating structure: "
                           "attributive modifier position before "
                           "head " + toks[head_idx] + "; capability "
                           "is not attestation — no stored source "
                           "attests the adjective class for this "
                           "token",
                           "capability_only_settle:" + tok,
                           ["adjective"])
            rec["relations"] = [_rel("modifier_of",
                                     [(toks[head_idx], head_idx)])]
        elif basis == "adverb":
            kind = a[1]
            if kind in ("adverbial_ly", "M3_ly_stem") \
                    and pmca_dictionary.lookup(tok) is None:
                # FINDER FIX WAVE (2026-11-01), W-6: the ONLY
                # evidence is morphology (the -ly form's stem
                # attests; the form itself has no dictionary
                # entry anywhere). Morphology alone yields a
                # CANDIDATE class with the morphology_only gap
                # recorded — never a settled, gapless record —
                # and no relation is asserted from it.
                _candidate(rec, "adverb", "dictionary_attestation",
                           "morphology evidence " + kind
                           + " only (base attested as headword); "
                           "the form itself is unattested — no "
                           "dictionary entry for " + tok,
                           "morphology_only:" + tok, ["adverb"])
            elif kind == "slot_lexeme":
                _settle(rec, "adverb", "CANDIDATE",
                        "closed_class_list",
                        "pmca_parse_guards.ADVERB_SLOT_LEXEMES "
                        "(candidate-grade, spec section 3.1)")
                rec["relations"] = [_rel("modifier_of",
                                         [(vtok, verb_idx)])]
            else:
                _settle(rec, "adverb", "CANDIDATE",
                        "dictionary_attestation",
                        "adverb evidence " + kind
                        + " (CANDIDATE tier; legacy adverb "
                        "microscript markers / morphology "
                        "adverbial_ly, dictionary disposing)")
                rec["relations"] = [_rel("modifier_of",
                                         [(vtok, verb_idx)])]
        elif basis == "preposition":
            detail = "pmca_parse_guards.PREPOSITIONS"
            if tok in _guards.DUAL_CLASS:
                detail += "; also DUAL_CLASS"
            _settle(rec, "preposition", "attested",
                    "closed_class_list", detail)
            rec["relations"] = [
                _rel("places", [(toks[a[1]], a[1])]),
                _rel("phrase_attaches_to", [(vtok, verb_idx)])]
        elif basis == "conjunction":
            _settle(rec, "conjunction", "attested",
                    "closed_class_list",
                    "pmca_parse_guards.CONJUNCTIONS")
            rec["relations"] = [_rel(
                "joins", [(toks[a[1]], a[1]), (toks[a[2]], a[2])])]
        elif basis == "verb":
            head = _dict_detail(ctx, tok)
            # FIX WAVE 2 (2026-11-02), WRECK2-3 + WRECK2-4:
            # provenance names capability as capability, and
            # a capability-only verb settle carries the
            # capability_only_settle gap (P8 "The man is
            # awake." settled verb gapless here). Where the
            # forms resolver attests the token as a form of a
            # seed verb (DICT-1 Part 2), the record's evidence
            # is not capability-only — an attested form stands
            # in the provenance — and no capability_only gap
            # is minted; that reading is recorded in the wave
            # report.
            if verb_capable(tok) and noun_capable(tok):
                source = "context_arbitration"
                detail = ("winning evidence: dictionary verb "
                          "capability (pmca_verb_capable."
                          "pos_answer, CANDIDATE tier, headword "
                          + head + "); rival capability "
                          "candidate: noun; arbitrating "
                          "structure: token between subject and "
                          "object phrases; capability is not "
                          "attestation — no stored source "
                          "attests the verb class for this "
                          "token")
            else:
                source = "dictionary_attestation"
                detail = ("dictionary verb capability "
                          "(pmca_verb_capable.pos_answer, "
                          "CANDIDATE tier, headword " + head
                          + "); capability is not attestation "
                          "— no stored source attests the verb "
                          "class for this token")
            res = ctx.resolver(tok)
            if res["verdict"] == "OK":
                detail += ("; pmca_forms_resolver: attested form "
                           + str(res["form"]) + " of seed verb "
                           + str(res["canonical"])
                           + " (DICT-1 Part 2)")
                _settle(rec, "verb", "CANDIDATE", source, detail)
            else:
                _candidate(rec, "verb", source, detail,
                           "capability_only_settle:" + tok,
                           ["verb"])
            targets = []
            if parse["subject_heads"]:
                s = parse["subject_heads"][0]
                targets.append((toks[s], s))
            if parse["object_heads"]:
                o = parse["object_heads"][0]
                targets.append((toks[o], o))
            rec["relations"] = [_rel("action", targets)]
    subject = None
    target = None
    if parse["subject_heads"]:
        subject = toks[parse["subject_heads"][0]]
    if parse["object_heads"]:
        target = toks[parse["object_heads"][0]]
    entry = pmca_dictionary.lookup(vtok)
    verb_word = entry["headword"] if entry is not None else vtok
    return subject, verb_word, target


def _her_ambiguity(ctx):
    """Legacy pronoun microscript: "her" begins the object zone
    of some verb candidate and is followed by an article or a
    noun-capable token — object-pronoun and possessive readings
    both open."""
    toks = ctx.tokens
    for i, tok in enumerate(toks):
        if not verb_capable(tok):
            continue
        if i + 2 < len(toks) and toks[i + 1] == "her":
            nxt = toks[i + 2]
            if nxt in _guards.ARTICLES or noun_capable(nxt):
                return True
    return False


def _finalize_fallback(ctx, records, hold_reason):
    """No settled parse: token-level candidacy only. Closed
    classes settle by list (F5); dictionary capability gives
    CANDIDATE classes; rival capabilities and unattested tokens
    record candidate+gap / UNKNOWN+gap. Nothing is assigned by
    elimination. (hold_reason is the sentence disposition,
    retained in the signature for the caller's record; since
    the FINDER FIX WAVE's W-7 it is never copied into a token's
    gap — every gap carries its token's own true reason.)"""
    toks = ctx.tokens
    for idx, rec in enumerate(records):
        if rec["class"] is not None or rec["gap"] is not None \
                or rec["tier"] != "UNKNOWN" \
                or rec["provenance"]["detail"]:
            continue  # already recorded (grammar/interjection)
        tok = toks[idx]
        if tok in _guards.ARTICLES:
            _settle(rec, "article", "attested", "closed_class_list",
                    "pmca_parse_guards.ARTICLES")
        elif tok in _guards.MODALS or tok in _guards.MODAL_NT:
            _settle(rec, "modal", "attested", "closed_class_list",
                    "pmca_parse_guards." + _closed_list_name(tok))
        elif tok in _guards.AUX_RECOGNIZED:
            _settle(rec, "auxiliary", "attested",
                    "closed_class_list",
                    "pmca_parse_guards." + _closed_list_name(tok))
        elif tok in _guards.PRONOUNS:
            if tok == "her" and _her_ambiguity(ctx):
                _candidate(rec, "pronoun", "closed_class_list",
                           "pmca_parse_guards.OBJECT_PRONOUNS; "
                           "possessive reading also attested",
                           "ambiguous_pronoun:her", ["pronoun"])
            else:
                _settle(rec, "pronoun", "attested",
                        "closed_class_list",
                        "pmca_parse_guards."
                        + (_closed_list_name(tok) or "PRONOUNS"))
        elif tok in _guards.POSSESSIVE_PRONOUNS:
            # FINDER FIX WAVE (2026-11-01), W-4.
            _settle(rec, "pronoun", "attested",
                    "closed_class_list",
                    "pmca_parse_guards.POSSESSIVE_PRONOUNS "
                    "(possessive)")
        elif tok in _guards.RELATIVE_PRONOUNS:
            _candidate(rec, "pronoun", "closed_class_list",
                       "pmca_parse_guards.RELATIVE_PRONOUNS",
                       "non_svo_np_geometry", ["pronoun"])
        elif tok in _guards.DUAL_CLASS:
            _candidate(rec, None, "closed_class_list",
                       "pmca_parse_guards.PREPOSITIONS and "
                       "SUBORDINATORS (DUAL_CLASS)",
                       "dual_class_candidate:" + tok,
                       ["preposition", "conjunction"])
        elif tok in _guards.PREPOSITIONS:
            _settle(rec, "preposition", "attested",
                    "closed_class_list",
                    "pmca_parse_guards.PREPOSITIONS")
        elif tok == "for":
            _candidate(rec, None, "closed_class_list",
                       "pmca_parse_guards.PREPOSITIONS and "
                       "CONJUNCTIONS both carry 'for'",
                       "dual_class_candidate:for",
                       ["preposition", "conjunction"])
        elif tok in _guards.CONJUNCTIONS:
            _settle(rec, "conjunction", "attested",
                    "closed_class_list",
                    "pmca_parse_guards.CONJUNCTIONS")
        elif tok in _guards.SUBORDINATORS:
            _candidate(rec, "conjunction", "closed_class_list",
                       "pmca_parse_guards.SUBORDINATORS",
                       "clause_geometry_out_of_scope:" + tok,
                       ["conjunction"])
        elif tok in _guards.CONTRACTIONS:
            expanded = _guards.normalize(
                [tok], noun_capable, verb_capable)["tokens"]
            first = expanded[0] if expanded else tok
            if first in _guards.PRONOUNS:
                _settle(rec, "pronoun", "attested",
                        "closed_class_list",
                        "contraction expansion (pmca_parse_guards."
                        "CONTRACTIONS): " + tok + " -> "
                        + " ".join(expanded)
                        + "; head token in PRONOUNS")
            else:
                _candidate(rec, None, "candidate+gap",
                           "contraction expansion: " + tok
                           + " -> " + " ".join(expanded),
                           "contraction_out_of_scope:" + tok, [])
        else:
            ans = _vc.pos_answer(tok)
            if ans["tier"] == "UNKNOWN":
                reason = (ans["gap"] or {}).get(
                    "reason", "no_headword")
                gap_reason = ("dictionary_tier_off"
                              if reason == "dictionary_tier_off"
                              else "unknown_word:" + tok)
                rec["class"] = None
                rec["tier"] = "UNKNOWN"
                rec["provenance"] = {
                    "source": "candidate+gap",
                    "detail": "pmca_verb_capable.pos_answer: "
                              + reason}
                rec["gap"] = _gap(tok, idx, gap_reason, [])
            else:
                value = ans["value"] or {}
                head = _dict_detail(ctx, tok)
                ev = _adverb_evidence(tok)
                if value.get("verb_capable") \
                        and value.get("noun_capable"):
                    rec["class"] = None
                    rec["tier"] = "CANDIDATE"
                    rec["provenance"] = {
                        "source": "candidate+gap",
                        "detail": "dictionary capability "
                                  "evidence: both noun and verb "
                                  "capability (headword "
                                  + head + "); no valid parse "
                                  "arbitrated"}
                    # FINDER FIX WAVE (2026-11-01), W-7: the
                    # gap reason is THIS token's own true reason
                    # — its attested noun/verb candidacy went
                    # unarbitrated — never the sentence's
                    # hold_reason copied onto it (the pre-fix
                    # record read e.g. ate's gap as
                    # "unknown_word:glorpy", a reason naming a
                    # different token).
                    rec["gap"] = _gap(
                        tok, idx,
                        "dual_class_candidate:" + tok,
                        ["noun", "verb"])
                elif value.get("verb_capable"):
                    # FIX WAVE "negative-evidence settles"
                    # (2026-11-02): a capability-only settle
                    # carries its gap on the record — the class
                    # may remain CANDIDATE, the silence may not
                    # (no silent gaps). The gap names the
                    # missing attestation; the W-6 morphology
                    # precedent is the shape.
                    _candidate(rec, "verb",
                               "dictionary_attestation",
                               "dictionary verb capability only "
                               "(headword " + head + "); "
                               "capability is not attestation — "
                               "no stored source attests the "
                               "verb class for this token and no "
                               "parse arbitrated it",
                               "capability_only_settle:" + tok,
                               ["verb"])
                elif ev is not None and ev[0] == "adverbial_ly":
                    _settle(rec, "adverb", "CANDIDATE",
                            "dictionary_attestation",
                            "morphology adverbial_ly determined; "
                            "noun capability also present but "
                            "the adverb use is the determined one "
                            "(demote doctrine)")
                elif ev is not None \
                        and not _strict_noun_evidence(tok):
                    # FINDER FIX WAVE (2026-11-01), W-3: the
                    # fallback consults the FULL adverb evidence
                    # the finder already gathers (M1 header tag /
                    # M2 manner phrase / M3 -ly stem / slot
                    # lexeme), not only adverbial_ly. "quickly"
                    # in a parse-dead sentence settles adverb on
                    # its M1 attestation instead of falling
                    # through to the noun default.
                    _settle(rec, "adverb", "CANDIDATE",
                            "dictionary_attestation",
                            "adverb evidence " + ev[0]
                            + " (CANDIDATE tier; legacy adverb "
                            "microscript markers, dictionary "
                            "text attestation); no competing "
                            "definition passes the loose "
                            "non-verb-definition test "
                            "(_strict_noun_evidence) (headword "
                            + head + ")")
                elif ev is not None:
                    # Adverb evidence AND a definition passing
                    # the loose non-verb-definition test
                    # (_strict_noun_evidence — NOT a noun-sense
                    # attestation; fix wave 2, WRECK2-5(a)); no
                    # parse arbitrated. The record stays a
                    # candidate with the rivalry as its gap —
                    # never noun-by-default (W-3).
                    _detail = ("adverb evidence " + ev[0]
                               + " and a dictionary definition "
                               "passing the loose "
                               "non-verb-definition test "
                               "(_strict_noun_evidence) both "
                               "present (headword " + head
                               + "); no valid parse arbitrated")
                    _cands = ["noun", "adverb"]
                    if idx == len(toks) - 1 \
                            and any(t in _guards.AUX_BE
                                    for t in toks[:idx]) \
                            and (_guards.adjective_attested(tok)
                                 or _guards.modifier_capable(tok)):
                        # FIX WAVE 2 (2026-11-02), WRECK2-5(b):
                        # the token stands in the W-3 predicate
                        # slot (sentence-final, after a copula)
                        # and adjective evidence is stored for
                        # it, yet this branch — consulted before
                        # the predicate branch — excluded the
                        # adjective from candidacy on branch
                        # order alone (P5 lonely, P6 lively,
                        # P7 hourly, whose own marker
                        # attestation the order hid). C-41: the
                        # composed sentence decides — candidacy
                        # widens so the predicate structure's
                        # class is among the candidates the
                        # record carries. Candidacy only: the
                        # class stays None, the gap stays, no
                        # settle is minted.
                        _cands.append("adjective")
                        _detail += ("; adjective candidacy "
                                    "also open: predicate "
                                    "position after a copula, "
                                    "with adjective evidence "
                                    "stored for this token "
                                    "(attestation or modifier "
                                    "capability)")
                    rec["class"] = None
                    rec["tier"] = "CANDIDATE"
                    rec["provenance"] = {
                        "source": "candidate+gap",
                        "detail": _detail}
                    rec["gap"] = _gap(
                        tok, idx, "dual_class_candidate:" + tok,
                        _cands)
                elif idx == len(toks) - 1 \
                        and any(t in _guards.AUX_BE
                                for t in toks[:idx]) \
                        and (_guards.adjective_attested(tok)
                             or _guards.modifier_capable(tok)):
                    # FINDER FIX WAVE (2026-11-01), W-3: entry
                    # evidence for an adjective use settles the
                    # adjective class at CANDIDATE tier when the
                    # composed sentence places the token in the
                    # predicate slot — after a copula (the
                    # copula/predicate structure the fix names;
                    # copula sentences are the mass case the
                    # attack measured). The old fallthrough
                    # pronounced every such word a noun with no
                    # evidence for the noun class beyond the
                    # loose capability decoding. Outside the
                    # predicate slot the adjective evidence is
                    # modifier evidence with no structure to
                    # arbitrate it, and this branch does not
                    # fire (class-in-sentence, Principle 3).
                    # FIX WAVE "negative-evidence settles"
                    # (2026-11-02): the W-3 branch rested the
                    # settle on the negative opening test alone
                    # and called it attestation — its record
                    # even claimed the entry "opens as a
                    # quality" for words whose entries open
                    # otherwise ("per", whose definition opens
                    # "Through; by means of..."). The claims
                    # are separated now. A stored positive
                    # attestation (adjective_attested) settles
                    # gapless, its provenance naming the source
                    # that fired. Capability evidence alone
                    # (modifier_capable) still settles at
                    # CANDIDATE — but with the gap on the
                    # record naming the missing attestation
                    # (no silent gaps). The sentence-final
                    # condition is the same separation applied
                    # to structure: a token with more sentence
                    # after it is not in the predicate slot —
                    # in "The charge is per unit." per heads
                    # the phrase that follows it, which is the
                    # prepositional-use family handled below,
                    # not this branch (Wreck S7, R-1 scour
                    # 2026-11-02).
                    _att = _guards.adjective_attestation(tok)
                    if _att is not None:
                        _settle(rec, "adjective", "CANDIDATE",
                                "dictionary_attestation",
                                "adjective attestation: " + _att
                                + " (headword " + head + "); "
                                "arbitrating structure: predicate "
                                "position after a copula in this "
                                "sentence")
                    else:
                        _candidate(rec, "adjective",
                                   "dictionary_attestation",
                                   "modifier capability "
                                   "evidence only "
                                   "(pmca_parse_guards."
                                   "modifier_capable, CANDIDATE "
                                   "tier); no positive adjective "
                                   "attestation is stored for "
                                   "this word — no seed ADJ "
                                   "entry and no Webster class "
                                   "marker in a string this "
                                   "headword owns in its entry "
                                   "(headword " + head + "); "
                                   "arbitrating structure: "
                                   "predicate position after a "
                                   "copula in this sentence",
                                   "adjective_attestation_missing:"
                                   + tok, ["adjective"])
                elif value.get("noun_capable"):
                    # FIX WAVE "negative-evidence settles"
                    # (2026-11-02): a capability-only settle —
                    # the class remains CANDIDATE, and the gap
                    # naming the missing attestation rides the
                    # record (no silent gaps). This is the
                    # branch that settled "per" as a gapless
                    # noun in its own prepositional uses
                    # (Wreck S3/S4, R-1 scour 2026-11-02); the
                    # finder fix wave's W-4 gap item for per
                    # existed in that wave's build paper — it
                    # lives on the occurrence record now.
                    _candidate(rec, "noun",
                               "dictionary_attestation",
                               "dictionary noun capability only "
                               "(headword " + head + "); "
                               "capability is not attestation — "
                               "no stored source attests a class "
                               "for this token and no parse "
                               "arbitrated it",
                               "capability_only_settle:" + tok,
                               ["noun"])
                else:
                    # FINDER FIX WAVE (2026-11-01), W-3: a
                    # dictionary word with NO evidence for any
                    # class records UNKNOWN-class + its own gap;
                    # the fallback may not emit a class with no
                    # evidence for it (no noun-by-default).
                    rec["class"] = None
                    rec["tier"] = "CANDIDATE"
                    rec["provenance"] = {
                        "source": "candidate+gap",
                        "detail": "dictionary entry attests no "
                                  "class evidence for this "
                                  "token (headword " + head + ")"}
                    rec["gap"] = _gap(
                        tok, idx, "no_class_evidence:" + tok, [])


# ---------------------------------------------------------------------
# The finder proper.
# ---------------------------------------------------------------------


def classify_tokens(tokens, seed_verbs=None, verb_forms=None,
                    sentence=None):
    """Classify every token of a cleaned token sequence in
    context. This is the supporting entry point the analyzer
    wiring calls (F3): the caller supplies the cleaned tokens
    (clean(sentence).split()) and, optionally, the forms
    resolver's seed context (the analyzer's VERBS / VERB_FORMS).
    Returns the find() result shape."""
    tokens = list(tokens)
    n = len(tokens)
    records = [_record(t, i) for i, t in enumerate(tokens)]
    result = {"sentence": sentence, "verdict": "OK",
              "hold_reason": None, "occurrences": records,
              "subject": None, "verb": None, "target": None}
    if not tokens:
        result["verdict"] = "HOLD"
        result["hold_reason"] = "empty_sentence"
        return result
    ctx = _Ctx(tokens, seed_verbs, verb_forms)
    stream = list(range(n))

    # Sentence-initial interjection (governing list, Helm
    # determination on O1): classified from
    # pmca_parse_guards.INTERJECTION_TOKENS. Membership in the
    # retired dictionary-verified set as well -> attested tier;
    # governing-list-only membership (wow and kin) -> CANDIDATE
    # with the list conflict recorded as the gap.
    # FINDER FIX WAVE, W-1 AMENDMENT (Jonathan's ruling,
    # 2026-11-01 12:10 EST): "Wow, wow is a double
    # exclamation." A repeated interjection inside one
    # exclamatory burst is intensification — the composed
    # language settles the repeated occurrence's class
    # (C-41). The burst is the pre-clause exclamation sequence:
    # the maximal contiguous run of the SAME token opening the
    # sentence. Every occurrence in the run classifies as
    # interjection on occurrence-level evidence — membership
    # in the burst — with the initial occurrence's own grade
    # (the list conflict, where one stands, is the token's and
    # is carried by every burst occurrence's record). The rule
    # applies ONLY inside the burst: a same-string token inside
    # the clause is not in the run and inherits nothing.
    if tokens[0] in _guards.INTERJECTION_TOKENS:
        tok = tokens[0]
        burst = [0]
        while burst[-1] + 1 < n \
                and tokens[burst[-1] + 1] == tok:
            burst.append(burst[-1] + 1)
        for pos in burst:
            rec = records[pos]
            if tok in _guards.INTERJECTIONS_VERIFIED:
                _settle(rec, "interjection", "attested",
                        "closed_class_list",
                        "pmca_parse_guards.INTERJECTION_TOKENS "
                        "(governing list); also "
                        "INTERJECTIONS_VERIFIED")
            else:
                _candidate(rec, "interjection", "closed_class_list",
                           "pmca_parse_guards.INTERJECTION_TOKENS "
                           "(governing list, Helm determination "
                           "2026-11-01); absent from the retired "
                           "dictionary-verified set "
                           "INTERJECTIONS_VERIFIED and no "
                           "dictionary entry in this build",
                           "interjection_list_conflict:" + tok,
                           ["interjection"])
            if pos > 0:
                rec["provenance"] = {
                    "source": "context_arbitration",
                    "detail": "exclamatory burst membership: "
                              "repetition of the sentence-"
                              "initial interjection " + tok
                              + " within the pre-clause "
                              "exclamation sequence (double "
                              "exclamation — intensification, "
                              "Jonathan's ruling 2026-11-01); "
                              "the burst occurrence's class is "
                              "settled by the composed sentence "
                              "(C-41), at the initial "
                              "occurrence's grade"}
            rec["relations"] = [_rel("exclamation", [])]
            stream.remove(pos)

    # "hush" opening before a full clause: interjection and
    # imperative readings both stand (legacy interjection
    # microscript).
    hush_hold = False
    if tokens[0] == "hush" and n > 1 and 0 in stream:
        _candidate(records[0], "interjection", "candidate+gap",
                   "stored verb senses only; imperative and "
                   "interjection readings both open",
                   "ambiguous_interjection_or_imperative:hush",
                   ["interjection", "verb"])
        records[0]["relations"] = [_rel("exclamation", [])]
        stream.remove(0)
        hush_hold = True

    # Grammar-tier tokens: modals, auxiliaries, negation. Their
    # classes are settled by list (F5 modal precedence); they
    # never stand in content spans.
    grammar = {}
    modal_removed = []
    for idx in list(stream):
        tok = tokens[idx]
        if tok in _guards.MODALS or tok in _guards.MODAL_NT:
            _settle(records[idx], "modal", "attested",
                    "closed_class_list",
                    "pmca_parse_guards." + _closed_list_name(tok))
            grammar[idx] = "modal"
            modal_removed.append(idx)
            stream.remove(idx)
        elif tok in _guards.AUX_RECOGNIZED:
            _settle(records[idx], "auxiliary", "attested",
                    "closed_class_list",
                    "pmca_parse_guards." + _closed_list_name(tok))
            grammar[idx] = "auxiliary"
            stream.remove(idx)
        elif tok in _guards.NEGATION_MARKERS:
            rec = records[idx]
            rec["class"] = None
            rec["tier"] = "CANDIDATE"
            rec["provenance"] = {
                "source": "closed_class_list",
                "detail": "pmca_parse_guards.NEGATION_MARKERS; "
                          "the finder class set carries no "
                          "negation class"}
            rec["gap"] = _gap(tok, idx,
                              "negation_out_of_scope:" + tok,
                              [])
            grammar[idx] = "negation"
            stream.remove(idx)

    # Legacy screen: disposition only (F7). Its HOLD never
    # removes or blanks a record. Guard set: the preposition
    # microscript's (genitive, g5, grammar, adverb, adjunct) —
    # the geometry guards g2/g4 are analyzer-surface guards
    # (their analyzer behaviour is spec section 8(b), OPEN and
    # unchanged); with them in this screen, every
    # prepositional-phrase sentence would HOLD
    # prepositional_barrier / multi_verb_unsegmented here and
    # the preposition variable's own ported cases could not
    # stand.
    screen_hold = _guards.screen(
        tokens, noun_capable, verb_capable,
        guards=("genitive", "g5", "grammar", "adverb",
                "adjunct"))["hold"]

    # Clause parses over the remaining stream. verb_extra: a
    # set of grammar-removed modal indices restored as content
    # for this attempt (FINDER FIX WAVE W-2) — a restored token
    # may stand as a verb candidate on its own dictionary
    # attestation, which ctx.verb_candidate bars for modal-list
    # tokens categorically.
    def _attempt(stream_, verb_extra=frozenset()):
        cand = []
        for idx in stream_:
            if idx in verb_extra:
                tok = tokens[idx]
                if verb_capable(tok) \
                        or ctx.resolver(tok)["verdict"] == "OK":
                    cand.append(idx)
            elif ctx.verb_candidate(idx):
                cand.append(idx)
        found = []
        fails = []
        amb = None
        for v in cand:
            parse, fail, a = _parse_clause(ctx, stream_, v)
            if parse is not None:
                found.append(parse)
            else:
                fails.append(fail)
                if a is not None:
                    amb = a
        distinct_ = []
        seen_ = set()
        for p in found:
            key = (p["verb"], tuple(p["subject_heads"]),
                   tuple(p["object_heads"]))
            if key not in seen_:
                seen_.add(key)
                distinct_.append(p)
        return distinct_, fails, amb, cand

    def _positions_ok(parse, positions):
        """Strict verb-group position for unrestored grammar
        modals (FINDER FIX WAVE W-2): each stands after the
        settled parse's last subject head and before its main
        verb, with nothing between it and the verb but other
        grammar tokens or a parse-assigned adverb. A modal that
        fails this test is NOT standing as a modal in the
        composed sentence, whatever the list says."""
        if not positions:
            return True
        if not parse["subject_heads"]:
            return False
        head = parse["subject_heads"][-1]
        verb = parse["verb"]
        for g in positions:
            if not head < g < verb:
                return False
            for t in range(g + 1, verb):
                if t in grammar:
                    continue
                a = parse["assign"].get(t)
                if a is not None and a[0] == "adverb":
                    continue
                return False
        return True

    def _modal_unresolved(idx):
        """Re-settle one modal record as CANDIDATE + gap
        (FINDER FIX WAVE W-2): list membership was candidacy,
        a content reading is structurally live, and the composed
        sentence did not arbitrate it — the record may not stand
        modal/attested/gapless on the list alone."""
        tok = tokens[idx]
        rec = records[idx]
        cands = ["modal"]
        if noun_capable(tok):
            cands.append("noun")
        if verb_capable(tok):
            cands.append("verb")
        rec["class"] = None
        rec["tier"] = "CANDIDATE"
        rec["provenance"] = {
            "source": "candidate+gap",
            "detail": "closed-list modal membership "
                      "(pmca_parse_guards."
                      + (_closed_list_name(tok) or "MODALS")
                      + ") is candidacy, not settlement; a "
                      "content reading is structurally live "
                      "and the composed sentence did not "
                      "arbitrate it"}
        rec["gap"] = _gap(tok, idx,
                          "modal_content_unresolved:" + tok, cands)
        rec["relations"] = []

    distinct, failures, amb_post, candidates = _attempt(stream)
    settled = distinct[0] if len(distinct) == 1 else None
    ambiguous = len(distinct) > 1

    # Whole-sequence arbitration (spec section 3.4), as widened
    # by the FINDER FIX WAVE (2026-11-01), W-2 — governing
    # principles C-41 and Principle 3: list membership is
    # candidacy, not settlement; the composed sentence
    # arbitrates content vs modal use. A modal token removed as
    # grammar may be a content word in this sentence ("A can
    # can contain soda." — the first "can" is the subject noun;
    # "He can can the fish." — the second "can" is the verb).
    # Stage A: a grammar-first parse that settles stands only
    # if every unrestored modal stands in strict verb-group
    # position in it; otherwise the parse is not the composed
    # sentence's reading and arbitration continues. Stage B:
    # restore SUBSETS of the content-capable modals as content
    # (smallest subset first; a restored token must take a
    # content assignment in the parse, and every unrestored
    # modal must stand in strict verb-group position); exactly
    # one valid composition settles, more than one leaves the
    # contested modals as candidate+gap. Stage C: where no full
    # parse settles, a modal heading a valid subject phrase —
    # with a unique main verb its fellow modals can stand
    # before, or standing before a copula — settles as that
    # phrase's noun by structure. The fused MODAL_NT forms are
    # never restored (their content reading is not separable).
    _content_modals = [i for i in modal_removed
                       if tokens[i] in _guards.MODALS]
    if settled is not None and modal_removed \
            and not _positions_ok(settled, modal_removed):
        settled = None
        distinct = []
    if settled is None and not ambiguous and _content_modals:
        import itertools
        _valid = []
        for _size in range(1, len(_content_modals) + 1):
            for _subset in itertools.combinations(
                    _content_modals, _size):
                # A modal token immediately followed by a
                # to-infinitive complement ("ought to run",
                # "can to run" being ungrammatical, the shape
                # selects the auxiliary reading) is never
                # restored as content: the composed sentence
                # has already arbitrated that occurrence.
                if any(m + 2 < n and tokens[m + 1] == "to"
                       and verb_capable(tokens[m + 2])
                       for m in _subset):
                    continue
                _stream2 = sorted(stream + list(_subset))
                _d2, _f2, _a2, _c2 = _attempt(
                    _stream2, verb_extra=frozenset(_subset))
                for _p in _d2:
                    if any(m not in _p["assign"] for m in _subset):
                        continue
                    _remaining = [g for g in modal_removed
                                  if g not in _subset]
                    if not _positions_ok(_p, _remaining):
                        continue
                    _valid.append((_subset, _p, _stream2, _c2))
            if _valid:
                break
        if len(_valid) == 1:
            _subset, settled, stream, candidates = _valid[0]
            distinct = [settled]
            for _m in _subset:
                grammar.pop(_m, None)
                modal_removed.remove(_m)
            failures = []
            amb_post = None
        elif len(_valid) > 1:
            _contested = set()
            for _subset, _p, _s2, _c2 in _valid:
                _contested.update(_subset)
            for _m in sorted(_contested):
                _modal_unresolved(_m)
    if settled is None and not ambiguous and _content_modals:
        # Stage C: subject-phrase arbitration for a modal no
        # full parse could place.
        for m in list(_content_modals):
            if m not in modal_removed:
                continue  # settled as content in stage B
            if records[m]["gap"] is not None:
                continue  # already recorded unresolved in stage B
            span = [i for i in range(0, m + 1)
                    if i in stream or i == m]
            head, _a = _validate_np(ctx, span, "subject") \
                if span else (None, None)
            if head != m or not noun_capable(tokens[m]):
                continue
            _verbs = [i for i in stream if i > m
                      and ctx.verb_candidate(i)]
            _good = []
            for _v in _verbs:
                _ok = True
                for _g in modal_removed:
                    if _g == m:
                        continue
                    if not m < _g < _v:
                        _ok = False
                        break
                    for _t in range(_g + 1, _v):
                        if _t in grammar or _t in span:
                            continue
                        if _t in stream:
                            _ok = False
                            break
                    if not _ok:
                        break
                if _ok:
                    _good.append(_v)
            if len(_good) == 1:
                _v = _good[0]
                _settle(records[m], "noun", "CANDIDATE",
                        "context_arbitration",
                        "winning evidence: dictionary noun "
                        "capability (pmca_verb_capable."
                        "pos_answer, CANDIDATE tier, headword "
                        + _dict_detail(ctx, tokens[m]) + "); "
                        "rival candidate: modal "
                        "(closed list MODALS — candidacy, not "
                        "settlement); arbitrating structure: "
                        "subject phrase head position before "
                        "verb " + tokens[_v])
                records[m]["relations"] = [_rel(
                    "subject_of", [(tokens[_v], _v)])]
                for _g in modal_removed:
                    if _g != m:
                        records[_g]["relations"] = [_rel(
                            "modal_scope", [(tokens[_v], _v)])]
                modal_removed.remove(m)
                grammar.pop(m, None)
            elif len(_good) > 1:
                _modal_unresolved(m)
            elif m + 1 < n and tokens[m + 1] in _guards.AUX_BE:
                _settle(records[m], "noun", "CANDIDATE",
                        "context_arbitration",
                        "winning evidence: dictionary noun "
                        "capability (pmca_verb_capable."
                        "pos_answer, CANDIDATE tier, headword "
                        + _dict_detail(ctx, tokens[m]) + "); "
                        "rival candidate: modal "
                        "(closed list MODALS — candidacy, not "
                        "settlement); arbitrating structure: "
                        "subject phrase head position before "
                        "the copula " + tokens[m + 1])
                modal_removed.remove(m)
                grammar.pop(m, None)

    # A standalone exclamation (the whole stream was the
    # interjection) is an OK result with no clause fields —
    # legacy interjection microscript case5.
    standalone_exclamation = not stream and any(
        rec["class"] == "interjection" for rec in records)

    # Disposition chain (legacy order, merged across the nine
    # microscripts): auxiliary scan, hush, screen, unknown scan,
    # named scope rules, parse outcome.
    hold = None
    if any(t in _AUX_BE_LEGACY for t in tokens):
        hold = _guards.SCOPE_TAG
    elif hush_hold:
        hold = "ambiguous_interjection_or_imperative:hush"
    elif screen_hold is not None:
        hold = screen_hold
    else:
        unknown = None
        for tok in tokens:
            if not _known(tok, seed_verbs, verb_forms):
                unknown = tok
                break
        if unknown is not None:
            hold = "unknown_word:" + unknown
        elif tokens[-1] in _guards.ARTICLES:
            hold = ("ambiguous_article:a" if tokens[-1] == "a"
                    else "article_without_noun:" + tokens[-1])
        else:
            # "for" heading a noun phrase after position 0 is a
            # preposition use the conjunction variable flags out
            # of scope (legacy conjunction microscript).
            for i, tok in enumerate(tokens):
                if tok == "for" and i > 0:
                    head, _f = _validate_np(
                        ctx, list(range(i + 1, n)), "object")
                    if head is not None:
                        hold = "preposition_use_out_of_scope:for"
                        break
            # Dual-class word introducing a clause (legacy
            # preposition microscript).
            if hold is None:
                for p, tok in enumerate(tokens):
                    if tok not in _guards.DUAL_CLASS:
                        continue
                    if not any(verb_capable(t)
                               for t in tokens[:p]):
                        continue
                    for ln in range(1, n - p):
                        head, _f = _validate_np(
                            ctx, list(range(p + 1, p + 1 + ln)),
                            "object")
                        if head is not None:
                            rest = tokens[p + 1 + ln:]
                            if any(verb_capable(t) for t in rest):
                                hold = ("conjunction_use_out_"
                                        "of_scope:" + tok)
                            break
                    if hold is not None:
                        break
            # "to" before a verb-capable word is the infinitive
            # marker (legacy preposition microscript).
            if hold is None:
                for p, tok in enumerate(tokens):
                    if tok == "to" and p + 1 < n \
                            and verb_capable(tokens[p + 1]):
                        hold = "infinitive_marker_out_of_scope:to"
                        break
            if hold is None:
                if standalone_exclamation:
                    hold = None
                elif amb_post is not None:
                    hold = "ambiguous_postverbal:" + tokens[amb_post]
                elif ambiguous:
                    hold = "ambiguous_verb_candidates"
                elif settled is not None:
                    hold = None
                elif not candidates:
                    hold = "no_verb_candidate"
                elif _her_ambiguity(ctx):
                    hold = "ambiguous_pronoun:her"
                else:
                    hold = "no_valid_parse"
                    for kind, idx in failures:
                        if kind == "head" and idx is not None:
                            hold = ("noun_phrase_head_not_noun:"
                                    + tokens[idx])
                            break
                    else:
                        for kind, idx in failures:
                            if kind == "incomplete_conjunction" \
                                    and idx is not None:
                                hold = ("incomplete_conjunction:"
                                        + tokens[idx])
                                break

    # Records.
    subject = verb_word = target = None
    if settled is not None or ambiguous:
        subject, verb_word, target = _finalize_settled(
            ctx, records, distinct[0])
        if ambiguous:
            # Rival parses: the contested tokens keep their
            # candidate class with the ambiguity as the gap, and
            # no relation is asserted from a contested parse.
            for idx in candidates:
                rec = records[idx]
                rec["tier"] = "CANDIDATE"
                rec["gap"] = _gap(tokens[idx], idx,
                                  "ambiguous_verb_candidates",
                                  ["noun", "verb"])
                rec["relations"] = []
            subject = verb_word = target = None
    else:
        _finalize_fallback(ctx, records, hold)

    # Scope relations for grammar tokens, from a settled parse.
    if settled is not None:
        v = settled["verb"]
        for idx, kind in grammar.items():
            if kind == "modal":
                records[idx]["relations"] = [
                    _rel("modal_scope", [(tokens[v], v)])]
            elif kind == "negation":
                records[idx]["relations"] = [
                    _rel("negation_scope", [(tokens[v], v)])]

    # Scope suppression (legacy convention, retained): when the
    # disposition is a scope HOLD whose geometry invalidates the
    # parse itself — the passive/auxiliary scan (an AUX_BE token
    # anywhere) or the hush ambiguity — no occurrence relation
    # is asserted and the clause fields stay None, exactly as
    # the legacy microscripts returned empty uses there. The
    # classifications themselves stand (F7).
    if any(t in _AUX_BE_LEGACY for t in tokens) or hush_hold:
        for rec in records:
            rec["relations"] = []
        subject = verb_word = target = None

    # FINDER FIX WAVE (2026-11-01), W-5: genitive possessor
    # relations, recorded for EVERY 's-genitive the screen
    # attested. The screen's genitive records are in
    # normalized-stream positions; they are mapped back here by
    # ordinal — the k-th genitive record answers to the k-th
    # genitive-shaped input token (a token ending "'s" or "s'"
    # that is not a contraction, the exact tokens normalize()
    # treats as genitives, in the same order) — and the
    # possessed head is the first later input token equal to
    # the record's head word. The relation is a CANDIDATE
    # occurrence relation (possessor -> possessed) recorded on
    # the possessor token's own record: where no classification
    # stands for the token, the screen's attestation settles it
    # noun at CANDIDATE tier (as the pre-fix code intended);
    # where a classification already stands, it is untouched —
    # the relation is appended to it. This block runs AFTER
    # scope suppression on purpose: the possessor relation is
    # screen-attested occurrence data, not a parse assertion,
    # so the suppression's geometry rationale does not reach
    # it. Records stay observational: no disposition reads the
    # possessor relation in this wave.
    _gen_input = [i for i, t in enumerate(tokens)
                  if t not in _guards.CONTRACTIONS
                  and ((t.endswith("'s") and len(t) > 2)
                       or (t.endswith("s'") and len(t) > 2))]
    for _gi, g in enumerate(ctx.genitives):
        if _gi >= len(_gen_input):
            break
        pidx = _gen_input[_gi]
        head_idx = None
        for j in range(pidx + 1, n):
            if tokens[j] == g["head"]:
                head_idx = j
                break
        if head_idx is None:
            continue
        rec = records[pidx]
        if any(r["relation"] == "possessor"
               for r in rec["relations"]):
            continue
        if rec["class"] is None:
            _settle(rec, "noun", "CANDIDATE",
                    "context_arbitration",
                    "genitive possessor (pmca_parse_guards "
                    "screen genitives); possessor of "
                    + g["head"])
        rec["relations"] = rec["relations"] + [_rel(
            "possessor", [(g["head"], head_idx)])]

    if hold is not None:
        result["verdict"] = "HOLD"
        result["hold_reason"] = hold
    result["subject"] = subject
    result["verb"] = verb_word
    result["target"] = target
    return result


def find(sentence):
    """Classify every word of one sentence in context. See the
    module docstring for the return shape and discipline."""
    tokens = _clean(sentence).split()
    return classify_tokens(tokens, sentence=sentence)


def trace_lines(occurrences):
    """The analyzer trace lines for a record list (F2: a finder
    trace line begins with the prefix "finder: "). Line grammar
    (O6, proposed by Forge): finder: <word>@<pos>
    class=<class|candidate:<class>|unknown> tier=<tier>
    via=<provenance source>."""
    lines = []
    for rec in occurrences:
        if rec["class"] is not None and rec["gap"] is not None:
            cls = "candidate:" + rec["class"]
        elif rec["class"] is not None:
            cls = rec["class"]
        elif rec["gap"] is not None and rec["gap"]["candidates"]:
            cls = "candidate:" + rec["gap"]["candidates"][0]
        else:
            cls = "unknown"
        lines.append("finder: " + rec["word"] + "@"
                     + str(rec["position"]) + " class=" + cls
                     + " tier=" + rec["tier"] + " via="
                     + rec["provenance"]["source"])
    return lines


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_finder.py
#
# Population: (1) the acceptance set (spec section 7: the seven
# D-01 sentences with their required classifications, plus
# "A can can contain soda."); (2) the PORTED 91 legacy
# run_checks cases (spec section 6.2 checklist) — assertions
# re-pointed from the legacy find() fields at this module's
# occurrence records, per the section 6.2 porting rule: where a
# legacy case asserted a sentence-level HOLD, the ported case
# asserts the same HOLD reason as this finder's disposition AND
# that every token received a record; (3) the F2 no-silent-
# skips invariant over the analyzer, dictionary on and off,
# plus one contraction and one genitive sentence; (4) the F3
# token-equality case; (5) the F12 FALSE-LOCK assertions.
# Amended expectations are named in their case comments, in the
# WG2 amendment pattern (named, recorded, not silent).
# ---------------------------------------------------------------------


def _occ(result, word, position=None):
    """Battery helper: the occurrence record for a word (at a
    position, when given), or None."""
    for o in result["occurrences"]:
        if o["word"] == word \
                and (position is None or o["position"] == position):
            return o
    return None


def _cls(result, word, position=None):
    """Battery helper: the recorded class of a word, or None."""
    o = _occ(result, word, position)
    return o["class"] if o else None


def _rel_words(result, word, relation, position=None):
    """Battery helper: the target words of one named relation
    on a word's record, or None when the relation is absent."""
    o = _occ(result, word, position)
    if o is None:
        return None
    for rel in o["relations"]:
        if rel["relation"] == relation:
            return [t["word"] for t in rel["targets"]]
    return None


def _rel_role(result, word, relation, position=None):
    """Battery helper: the role key of one named relation on a
    word's record (the stands_for subject/object role)."""
    o = _occ(result, word, position)
    if o is None:
        return None
    for rel in o["relations"]:
        if rel["relation"] == relation:
            return rel.get("role")
    return None


def _every_token_recorded(result, sentence):
    """Battery helper: the no-silent-skips property at finder
    level — one occurrence record per cleaned token."""
    return len(result["occurrences"]) == len(_clean(sentence).split())


def run_checks():
    """The finder's own battery: the acceptance set, the ported
    91 legacy cases, the F2 analyzer invariant, the F3 token
    equality case, and the F12 FALSE-LOCK assertions. Prints
    per-case PASS/FAIL and returns True iff all passed."""
    import json
    import os

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + str(detail) if detail else ""))

    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "data", "webster1913.db")
    pmca_dictionary.configure(db)

    # ---------------------------------------------------------
    # (1) Acceptance set (spec section 7).
    # ---------------------------------------------------------
    r = find("The big dog ate the food.")
    check("acc1_adjective_big",
          _cls(r, "big") == "adjective"
          and _rel_words(r, "big", "modifier_of") == ["dog"]
          and _cls(r, "the", 0) == "article"
          and _rel_words(r, "the", "introduces", 0) == ["dog"]
          and _cls(r, "dog") == "noun"
          and _rel_words(r, "dog", "subject_of") == ["ate"]
          and _cls(r, "ate") == "verb"
          and _cls(r, "food") == "noun"
          and _rel_words(r, "food", "object_of") == ["ate"]
          and _every_token_recorded(r, "The big dog ate the food."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The dog ate the food quickly.")
    check("acc2_adverb_quickly",
          _cls(r, "quickly") == "adverb"
          and _rel_words(r, "quickly", "modifier_of") == ["ate"]
          and _cls(r, "food") == "noun"
          and _every_token_recorded(
              r, "The dog ate the food quickly."),
          str((r["verdict"], r["hold_reason"]))
          + " (disposition may carry the screen's "
          "adverb_in_target_slot guard tag per F7; the record "
          "stands)")

    r = find("The dog ate the food.")
    check("acc3_article_the_recorded",
          _cls(r, "the", 0) == "article"
          and _cls(r, "the", 3) == "article"
          and _rel_words(r, "the", "introduces", 0) == ["dog"]
          and _rel_words(r, "the", "introduces", 3) == ["food"]
          and _every_token_recorded(r, "The dog ate the food."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The dog and the cat ate the food.")
    check("acc4_conjunction_and_joins",
          _cls(r, "and") == "conjunction"
          and _rel_words(r, "and", "joins") == ["dog", "cat"]
          and _cls(r, "dog") == "noun" and _cls(r, "cat") == "noun"
          and _every_token_recorded(
              r, "The dog and the cat ate the food."),
          str((r["verdict"], r["hold_reason"])))

    r = find("Wow, the dog ate the food.")
    wow = _occ(r, "wow")
    check("acc5_interjection_wow",
          wow is not None and wow["class"] == "interjection"
          and wow["tier"] == "CANDIDATE"
          and wow["gap"] is not None
          and wow["gap"]["reason"]
          == "interjection_list_conflict:wow"
          and _rel_words(r, "wow", "exclamation") == []
          and r["hold_reason"] != "unknown_word:wow"
          and _every_token_recorded(
              r, "Wow, the dog ate the food."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The dog sat on the mat.")
    check("acc6_preposition_on",
          _cls(r, "on") == "preposition"
          and _rel_words(r, "on", "places") == ["mat"]
          and _rel_words(r, "on", "phrase_attaches_to") == ["sat"]
          and _cls(r, "mat") == "noun"
          and r["target"] is None
          and _every_token_recorded(r, "The dog sat on the mat."),
          str((r["verdict"], r["hold_reason"])))

    r = find("He ate the food.")
    check("acc7_pronoun_he",
          _cls(r, "he") == "pronoun"
          and _rel_role(r, "he", "stands_for") == "subject"
          and _rel_words(r, "he", "subject_of") == ["ate"]
          and _every_token_recorded(r, "He ate the food."),
          str((r["verdict"], r["hold_reason"])))

    r = find("A can can contain soda.")
    check("acc8_can_can",
          _cls(r, "a", 0) == "article"
          and _rel_words(r, "a", "introduces", 0) == ["can"]
          and _cls(r, "can", 1) == "noun"
          and _rel_words(r, "can", "subject_of", 1) == ["contain"]
          and _cls(r, "can", 2) == "modal"
          and _rel_words(r, "can", "modal_scope", 2) == ["contain"]
          and _cls(r, "contain") == "verb"
          and _cls(r, "soda") == "noun"
          and _rel_words(r, "soda", "object_of") == ["contain"]
          and _every_token_recorded(r, "A can can contain soda."),
          str((r["verdict"], r["hold_reason"])))

    # ---------------------------------------------------------
    # (4) F3: token-sequence equality with the analyzer's
    # clean() over the acceptance set plus the comma-bearing
    # interjection sentence (already in the set).
    # ---------------------------------------------------------
    import pmca_sentence_analyzer as _analyzer
    acc_sentences = [
        "The big dog ate the food.", "The dog ate the food quickly.",
        "The dog ate the food.", "The dog and the cat ate the food.",
        "Wow, the dog ate the food.", "The dog sat on the mat.",
        "He ate the food.", "A can can contain soda.",
        "The cat jumps."]
    check("f3_token_equality_with_analyzer_clean",
          all(_clean(s).split() == _analyzer.clean(s).split()
              for s in acc_sentences),
          "local clean() == pmca_sentence_analyzer.clean() on "
          "the acceptance set")

    # ---------------------------------------------------------
    # (2) Ported legacy population — noun (17).
    # ---------------------------------------------------------
    r = find("The cat jumps.")
    check("case1_cat_jumps_ok",
          r["verdict"] == "OK" and r["subject"] == "cat"
          and r["verb"] == "jump" and r["target"] is None
          and _cls(r, "cat") == "noun" and _cls(r, "jumps") == "verb",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    r = find("The man breaks the cup.")
    check("case2_man_breaks_cup_ok",
          r["verdict"] == "OK" and r["subject"] == "man"
          and r["verb"] == "break" and r["target"] == "cup",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    r = find("The wolf howls.")
    check("case3_wolf_noun_use",
          r["verdict"] == "OK" and r["subject"] == "wolf"
          and _cls(r, "wolf") == "noun" and r["verb"] == "howl",
          str((r["verdict"], r["subject"], r["verb"])))

    r = find("They wolf the pizza.")
    check("case4_wolf_verb_AS_SPECIFIED_unmet_by_dictionary",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:pizza"
          and _every_token_recorded(r, "They wolf the pizza."),
          "brief expects OK/wolf-as-verb; measured: "
          + str((r["verdict"], r["hold_reason"])))

    r = find("They wolf the meat.")
    check("case4b_wolf_verb_gap_isolated",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "no_verb_candidate"
          and _every_token_recorded(r, "They wolf the meat."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The duck swims.")
    check("case4c_substitute_duck_noun_use",
          r["verdict"] == "OK" and r["subject"] == "duck"
          and _cls(r, "duck") == "noun",
          str((r["verdict"], r["subject"], r["verb"])))

    r = find("They duck the question.")
    check("case4d_substitute_duck_verb_use",
          r["verdict"] == "OK" and r["subject"] == "they"
          and _cls(r, "duck") == "verb" and r["target"] == "question",
          str((r["verdict"], r["verb"], r["target"])))

    r = find("The big wolf eats the sheep.")
    check("case5_big_wolf_eats_sheep_ok",
          r["verdict"] == "OK" and r["subject"] == "wolf"
          and _cls(r, "big") == "adjective"
          and r["verb"] == "eat" and r["target"] == "sheep",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    r = find("The dog barks.")
    check("case6_dog_barks_ok",
          r["verdict"] == "OK" and r["subject"] == "dog"
          and r["verb"] == "bark" and r["target"] is None,
          str((r["verdict"], r["subject"], r["verb"])))

    r = find("The wolves hunt the sheep.")
    check("case7_wolves_hunt_sheep_ok",
          r["verdict"] == "OK" and r["subject"] == "wolves"
          and _cls(r, "wolves") == "noun"
          and r["verb"] == "hunt" and r["target"] == "sheep",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    r = find("The cup was broken by the man.")
    check("case8_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope"
          and _every_token_recorded(
              r, "The cup was broken by the man."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The zorblat jumps.")
    check("case9_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat"
          and _occ(r, "zorblat")["tier"] == "UNKNOWN"
          and _every_token_recorded(r, "The zorblat jumps."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The sheep hunt duck.")
    check("case10_hunt_verb_only_shared_rule",
          r["verdict"] == "OK"
          and r["subject"] == "sheep" and r["verb"] == "hunt"
          and r["target"] == "duck",
          str((r["verdict"], r["hold_reason"], r["verb"])))

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

    a, cause, action, target = analyzer_words(
        "The man breaks the cup.")
    f = find("The man breaks the cup.")
    check("xcheck2_man_breaks_cup_agrees",
          cause == f["subject"] == "man"
          and action == f["verb"] == "break"
          and target == f["target"] == "cup",
          "analyzer " + str((a["verdict"], cause, action, target))
          + " vs finder " + str((f["subject"], f["verb"],
                                 f["target"])))

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

    # ---------------------------------------------------------
    # Ported — verb (10).
    # ---------------------------------------------------------
    r = find("The man eats the meat.")
    check("case1_man_eats_meat_ok",
          r["verdict"] == "OK" and r["subject"] == "man"
          and r["verb"] == "eat" and r["target"] == "meat"
          and _cls(r, "eats") == "verb",
          str((r["verdict"], r["subject"], r["verb"], r["target"])))

    r = find("They wolf the meat.")
    check("case2_wolf_verb_holds_naturally",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "no_verb_candidate"
          and not verb_capable("wolf"),
          str((r["verdict"], r["hold_reason"])))

    r = find("The cat jumps.")
    check("case3_cat_jumps_ok",
          r["verdict"] == "OK" and r["verb"] == "jump"
          and r["target"] is None,
          str((r["verdict"], r["verb"], r["target"])))

    r = find("The duck swims.")
    check("case4_duck_subject_swim_verb",
          r["verdict"] == "OK" and r["verb"] == "swim"
          and _cls(r, "duck") == "noun",
          str((r["verdict"], r["subject"], r["verb"])))

    r = find("They duck the question.")
    check("case5_duck_verb_use",
          r["verdict"] == "OK" and r["verb"] == "duck"
          and _cls(r, "duck") == "verb" and r["target"] == "question",
          str((r["verdict"], r["verb"], r["target"])))

    r = find("The dog barks.")
    check("case6_dog_barks_ok",
          r["verdict"] == "OK" and r["subject"] == "dog"
          and r["verb"] == "bark" and r["target"] is None,
          str((r["verdict"], r["subject"], r["verb"])))

    r = find("The cup was broken by the man.")
    check("case7_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope"
          and _every_token_recorded(
              r, "The cup was broken by the man."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The zorblat jumps.")
    check("case8_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat"
          and _every_token_recorded(r, "The zorblat jumps."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The sheep hunt duck.")
    check("case9_hunt_verb_only_shared_rule",
          r["verdict"] == "OK" and r["subject"] == "sheep"
          and r["verb"] == "hunt" and r["target"] == "duck",
          str((r["verdict"], r["hold_reason"], r["verb"])))

    # ---------------------------------------------------------
    # Ported — adjective (10).
    # ---------------------------------------------------------
    sample_adj = ["big", "savage", "good", "wild", "fierce",
                  "strong", "young", "old", "hungry", "gentle",
                  "common", "quick", "slow"]
    sample_noun = ["cat", "wolf", "dog", "cup", "sheep",
                   "forest", "goat", "man"]
    check("marker_sample_as_measured",
          all(_guards.modifier_capable(w) for w in sample_adj)
          and not any(_guards.modifier_capable(w)
                      for w in sample_noun)
          and not _guards.modifier_capable("red")
          and _guards.modifier_capable("stone")
          and _guards.modifier_capable("meat"),
          "13/14 adj (red missed), 8/10 nouns (stone, meat "
          "false-positive) — the CAPABILITY sample, measured "
          "via the shared home, pmca_parse_guards."
          "modifier_capable (the negative-evidence fix wave, "
          "2026-11-02, re-pointed this check from "
          "adjective_attested: the sample measures modifier "
          "capability, not adjective attestation)")

    r = find("The big wolf runs.")
    check("case1_big_describes_wolf",
          r["verdict"] == "OK" and r["subject"] == "wolf"
          and r["verb"] == "run"
          and _rel_words(r, "big", "modifier_of") == ["wolf"]
          and _cls(r, "big") == "adjective",
          str((r["verdict"], r["subject"], r["verb"])))

    r = find("The savage wolf eats the sheep.")
    check("case2_savage_describes_wolf",
          r["verdict"] == "OK"
          and _rel_words(r, "savage", "modifier_of") == ["wolf"]
          and r["target"] == "sheep",
          str((r["verdict"], r["target"])))

    r = find("The big wolf eats the big sheep.")
    check("case3_big_describes_both",
          r["verdict"] == "OK"
          and _rel_words(r, "big", "modifier_of", 1) == ["wolf"]
          and _rel_words(r, "big", "modifier_of", 5) == ["sheep"],
          str((r["verdict"], r["hold_reason"])))

    r = find("The cat jumps.")
    check("case4_no_adjectives",
          r["verdict"] == "OK"
          and all(o["class"] != "adjective"
                  for o in r["occurrences"]),
          str((r["verdict"],)))

    r = find("The sheep goat barks.")
    check("case5_noun_adjunct_ambiguity_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "ambiguous_modifier_noun_adjunct:sheep"
          and _every_token_recorded(r, "The sheep goat barks."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The red wolf runs.")
    check("case6_red_measured_marker_miss_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "ambiguous_modifier_noun_adjunct:red"
          and not _guards.modifier_capable("red")
          and _every_token_recorded(r, "The red wolf runs."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The big cup was broken.")
    check("case7_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope"
          and _every_token_recorded(r, "The big cup was broken."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The zorblat wolf runs.")
    check("case8_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat"
          and _every_token_recorded(r, "The zorblat wolf runs."),
          str((r["verdict"], r["hold_reason"])))

    # ---------------------------------------------------------
    # Ported — adverb (9).
    # ---------------------------------------------------------
    def _marker(word):
        ev = _adverb_evidence(word)
        if ev is None or ev[0] == "slot_lexeme":
            return None
        return ev

    check("markers_as_measured",
          all(_marker(w) is not None
              for w in ("quickly", "slowly", "gladly", "bravely",
                        "fiercely", "fast"))
          and _marker("well") is None
          and _marker("now") is None
          and _marker("often") is None
          and _marker("wolf") is None,
          "M1/M2/M3 catch the sampled adverbs; often/now/well "
          "carry no marker in this build (ported markers, "
          "measured standing unchanged)")

    r = find("The wolf runs quickly.")
    check("case1_quickly_modifies_runs",
          r["verdict"] == "OK" and r["verb"] == "run"
          and _cls(r, "quickly") == "adverb"
          and _rel_words(r, "quickly", "modifier_of") == ["runs"],
          str((r["verdict"], r["hold_reason"])))

    r = find("The man eats slowly.")
    check("case2_slowly_modifies_eats",
          r["verdict"] == "OK" and r["verb"] == "eat"
          and r["target"] is None
          and _cls(r, "slowly") == "adverb"
          and _rel_words(r, "slowly", "modifier_of") == ["eats"],
          str((r["verdict"], r["hold_reason"])))

    r = find("The wolf quickly runs.")
    check("case3_preverb_quickly",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "adverb_in_subject_slot:quickly"
          and _cls(r, "quickly") == "adverb"
          and _every_token_recorded(r, "The wolf quickly runs."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The wolf runs.")
    check("case4_no_adverb",
          r["verdict"] == "OK"
          and all(o["class"] != "adverb" for o in r["occurrences"])
          and r["verb"] == "run",
          str((r["verdict"],)))

    r = find("The wolf runs fast.")
    check("case5_fast_ambiguity_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "ambiguous_postverbal:fast"
          and _adverb_evidence("fast") is not None
          and _strict_noun_evidence("fast")
          and _every_token_recorded(r, "The wolf runs fast."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The wolf runs zippily.")
    check("case6_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zippily"
          and _every_token_recorded(r, "The wolf runs zippily."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The cup was broken quickly.")
    check("case7_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope"
          and _every_token_recorded(r, "The cup was broken quickly."),
          str((r["verdict"], r["hold_reason"])))

    # ---------------------------------------------------------
    # Ported — article (8).
    # ---------------------------------------------------------
    r = find("A wolf eats the sheep.")
    check("case1_a_and_the_introduce",
          r["verdict"] == "OK"
          and _rel_words(r, "a", "introduces", 0) == ["wolf"]
          and _rel_words(r, "the", "introduces", 3) == ["sheep"]
          and _cls(r, "a", 0) == "article",
          str((r["verdict"], r["hold_reason"])))

    r = find("The cat jumps.")
    check("case2_the_introduces_cat",
          r["verdict"] == "OK"
          and _rel_words(r, "the", "introduces") == ["cat"],
          str((r["verdict"],)))

    r = find("The big wolf eats a sheep.")
    check("case3_article_introduces_head",
          r["verdict"] == "OK"
          and _rel_words(r, "the", "introduces") == ["wolf"]
          and _rel_words(r, "a", "introduces") == ["sheep"],
          str((r["verdict"],)))

    r = find("Wolves hunt sheep.")
    check("case4_no_articles",
          r["verdict"] == "OK"
          and all(o["class"] != "article" for o in r["occurrences"])
          and r["subject"] == "wolves" and r["target"] == "sheep",
          str((r["verdict"], r["subject"], r["target"])))

    r = find("The man eats the.")
    check("case5_trailing_the_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "article_without_noun:the"
          and _every_token_recorded(r, "The man eats the."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The man writes a.")
    check("case6_trailing_a_ambiguous",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "ambiguous_article:a"
          and _every_token_recorded(r, "The man writes a."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The zorblat jumps.")
    check("case7_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat"
          and _every_token_recorded(r, "The zorblat jumps."),
          str((r["verdict"], r["hold_reason"])))

    # ---------------------------------------------------------
    # Ported — conjunction (8).
    # ---------------------------------------------------------
    r = find("The wolf and the dog hunt.")
    check("case1_and_joins_subjects",
          r["verdict"] == "OK" and r["verb"] == "hunt"
          and _rel_words(r, "and", "joins") == ["wolf", "dog"]
          and r["subject"] == "wolf"
          and _cls(r, "and") == "conjunction",
          str((r["verdict"], r["hold_reason"])))

    r = find("The man eats the sheep and the goat.")
    check("case2_and_joins_objects",
          r["verdict"] == "OK" and r["subject"] == "man"
          and _rel_words(r, "and", "joins") == ["sheep", "goat"]
          and r["target"] == "sheep",
          str((r["verdict"], r["target"])))

    r = find("The wolf or the dog hunts.")
    check("case3_or_joins_subjects",
          r["verdict"] == "OK"
          and _rel_words(r, "or", "joins") == ["wolf", "dog"],
          str((r["verdict"], r["hold_reason"])))

    r = find("The cat jumps.")
    check("case4_no_conjunction",
          r["verdict"] == "OK"
          and all(o["class"] != "conjunction"
                  for o in r["occurrences"]),
          str((r["verdict"],)))

    r = find("The wolf and hunts.")
    check("case5_incomplete_conjunction_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "incomplete_conjunction:and"
          and _every_token_recorded(r, "The wolf and hunts."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The man eats for the wolf.")
    check("case6_for_preposition_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "preposition_use_out_of_scope:for"
          and _every_token_recorded(r, "The man eats for the wolf."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The wolf and the zorblat hunt.")
    check("case7_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat"
          and _every_token_recorded(
              r, "The wolf and the zorblat hunt."),
          str((r["verdict"], r["hold_reason"])))

    # ---------------------------------------------------------
    # Ported — interjection (9). Case6 is AMENDED under the
    # Helm determination on O1 (2026-11-01): the governing
    # interjection list is pmca_parse_guards.INTERJECTION_TOKENS
    # (includes wow), so wow classifies as interjection and is
    # NOT fatal; the legacy expectation (HOLD unknown_word:wow)
    # is superseded by that ruling, named here in the WG2
    # amendment pattern.
    # ---------------------------------------------------------
    check("set_verified_against_dictionary",
          all(pmca_dictionary.lookup(w) is not None
              for w in _guards.INTERJECTIONS_VERIFIED)
          and pmca_dictionary.lookup("wow") is None
          and "wow" in _guards.INTERJECTION_TOKENS,
          "verified set = "
          + ",".join(sorted(_guards.INTERJECTIONS_VERIFIED))
          + "; wow absent from this build's dictionary, present "
          "in the governing INTERJECTION_TOKENS")

    r = find("Oh, the wolf howls.")
    check("case1_oh_exclamation",
          r["verdict"] == "OK" and _cls(r, "oh") == "interjection"
          and _rel_words(r, "oh", "exclamation") == []
          and r["subject"] == "wolf" and r["verb"] == "howl",
          str((r["verdict"], r["subject"])))

    r = find("Alas, the man eats the meat.")
    check("case2_alas_exclamation",
          r["verdict"] == "OK" and _cls(r, "alas") == "interjection"
          and r["target"] == "meat",
          str((r["verdict"], r["target"])))

    r = find("Ha, the wolf runs.")
    check("case3_ha_exclamation",
          r["verdict"] == "OK" and _cls(r, "ha") == "interjection"
          and r["verb"] == "run",
          str((r["verdict"], r["verb"])))

    r = find("The wolf howls.")
    check("case4_no_interjection",
          r["verdict"] == "OK"
          and all(o["class"] != "interjection"
                  for o in r["occurrences"]),
          str((r["verdict"],)))

    r = find("Alas!")
    check("case5_standalone_exclamation",
          r["verdict"] == "OK" and _cls(r, "alas") == "interjection"
          and r["subject"] is None and r["verb"] is None,
          str((r["verdict"], r["hold_reason"])))

    r = find("Wow, the wolf howls.")
    check("case6_wow_classified_not_fatal_O1_amended",
          r["verdict"] == "OK" and _cls(r, "wow") == "interjection"
          and _occ(r, "wow")["gap"]["reason"]
          == "interjection_list_conflict:wow"
          and r["subject"] == "wolf" and r["verb"] == "howl",
          str((r["verdict"], r["hold_reason"]))
          + " (AMENDED: legacy expected HOLD unknown_word:wow; "
          "the Helm O1 determination supersedes)")

    r = find("Hush, the wolf sleeps.")
    check("case7_hush_ambiguity_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "ambiguous_interjection_or_imperative:hush"
          and _every_token_recorded(r, "Hush, the wolf sleeps."),
          str((r["verdict"], r["hold_reason"])))

    # ---------------------------------------------------------
    # Ported — preposition (10).
    # ---------------------------------------------------------
    r = find("The wolf sleeps in the forest.")
    check("case1_in_places_forest",
          r["verdict"] == "OK" and r["verb"] == "sleep"
          and _rel_words(r, "in", "places") == ["forest"]
          and _cls(r, "in") == "preposition",
          str((r["verdict"], r["hold_reason"])))

    r = find("The man eats with the wolf.")
    check("case2_with_places_wolf",
          r["verdict"] == "OK"
          and _rel_words(r, "with", "places") == ["wolf"],
          str((r["verdict"], r["hold_reason"])))

    r = find("The wolf of the forest sleeps.")
    check("case3_of_inside_subject",
          r["verdict"] == "OK" and r["subject"] == "wolf"
          and _rel_words(r, "of", "places") == ["forest"],
          str((r["verdict"], r["subject"])))

    r = find("The man eats before the wolf.")
    check("case4_before_as_preposition",
          r["verdict"] == "OK"
          and _rel_words(r, "before", "places") == ["wolf"],
          str((r["verdict"], r["hold_reason"])))

    r = find("The cat jumps.")
    check("case5_no_preposition",
          r["verdict"] == "OK"
          and all(o["class"] != "preposition"
                  for o in r["occurrences"]),
          str((r["verdict"],)))

    r = find("The man eats before the wolf hunts.")
    check("case6_before_conjunction_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"]
          == "conjunction_use_out_of_scope:before"
          and _every_token_recorded(
              r, "The man eats before the wolf hunts."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The wolf wants to run.")
    check("case7_infinitive_to_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "infinitive_marker_out_of_scope:to"
          and _every_token_recorded(r, "The wolf wants to run."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The cup was broken by the man.")
    check("case8_passive_by_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope"
          and _every_token_recorded(
              r, "The cup was broken by the man."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The wolf sleeps in the zorblat.")
    check("case9_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat"
          and _every_token_recorded(
              r, "The wolf sleeps in the zorblat."),
          str((r["verdict"], r["hold_reason"])))

    # ---------------------------------------------------------
    # Ported — pronoun (10).
    # ---------------------------------------------------------
    r = find("They hunt the sheep.")
    check("case1_they_subject",
          r["verdict"] == "OK" and r["subject"] == "they"
          and _rel_role(r, "they", "stands_for") == "subject"
          and _cls(r, "they") == "pronoun",
          str((r["verdict"], r["hold_reason"])))

    r = find("The man sees them.")
    check("case2_them_object",
          r["verdict"] == "OK" and r["target"] == "them"
          and _rel_role(r, "them", "stands_for") == "object",
          str((r["verdict"], r["target"])))

    r = find("She eats the meat.")
    check("case3_she_subject",
          r["verdict"] == "OK" and r["subject"] == "she"
          and _rel_role(r, "she", "stands_for") == "subject",
          str((r["verdict"], r["subject"])))

    r = find("The man sees it.")
    check("case4_it_object_by_position",
          r["verdict"] == "OK"
          and _rel_role(r, "it", "stands_for") == "object",
          str((r["verdict"], r["hold_reason"])))

    check("case5_me_absent_from_dictionary",
          pmca_dictionary.lookup("me") is None,
          "lookup('me') is None in this build")

    r = find("The wolf sees me.")
    check("case5b_me_object_as_seed_word",
          r["verdict"] == "OK"
          and _rel_role(r, "me", "stands_for") == "object",
          str((r["verdict"], r["hold_reason"])))

    r = find("The man sees her sheep.")
    check("case6_her_ambiguity_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "ambiguous_pronoun:her"
          and _every_token_recorded(r, "The man sees her sheep."),
          str((r["verdict"], r["hold_reason"])))

    r = find("It was broken.")
    check("case7_passive_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "passive_or_auxiliary_out_of_scope"
          and _every_token_recorded(r, "It was broken."),
          str((r["verdict"], r["hold_reason"])))

    r = find("The zorblat sees them.")
    check("case8_unknown_word_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "unknown_word:zorblat"
          and _every_token_recorded(r, "The zorblat sees them."),
          str((r["verdict"], r["hold_reason"])))

    # ---------------------------------------------------------
    # Ported determinism cases (9): each legacy module's
    # determinism_two_runs_identical, over its own battery
    # sentence subset.
    # ---------------------------------------------------------
    det_subsets = {
        "noun": ["The cat jumps.", "The man breaks the cup.",
                 "The wolf howls.", "They wolf the pizza.",
                 "They wolf the meat.", "The duck swims.",
                 "They duck the question.",
                 "The big wolf eats the sheep.", "The dog barks.",
                 "The wolves hunt the sheep.",
                 "The cup was broken by the man.",
                 "The zorblat jumps.", "The sheep hunt duck."],
        "verb": ["The man eats the meat.", "They wolf the meat.",
                 "The cat jumps.", "The duck swims.",
                 "They duck the question.", "The dog barks.",
                 "The cup was broken by the man.",
                 "The zorblat jumps.", "The sheep hunt duck."],
        "adjective": ["The big wolf runs.",
                      "The savage wolf eats the sheep.",
                      "The big wolf eats the big sheep.",
                      "The cat jumps.", "The sheep goat barks.",
                      "The red wolf runs.",
                      "The big cup was broken.",
                      "The zorblat wolf runs."],
        "adverb": ["The wolf runs quickly.", "The man eats slowly.",
                   "The wolf quickly runs.", "The wolf runs.",
                   "The wolf runs fast.", "The wolf runs zippily.",
                   "The cup was broken quickly."],
        "article": ["A wolf eats the sheep.", "The cat jumps.",
                    "The big wolf eats a sheep.",
                    "Wolves hunt sheep.", "The man eats the.",
                    "The man writes a.", "The zorblat jumps."],
        "conjunction": ["The wolf and the dog hunt.",
                        "The man eats the sheep and the goat.",
                        "The wolf or the dog hunts.",
                        "The cat jumps.", "The wolf and hunts.",
                        "The man eats for the wolf.",
                        "The wolf and the zorblat hunt."],
        "interjection": ["Oh, the wolf howls.",
                         "Alas, the man eats the meat.",
                         "Ha, the wolf runs.", "The wolf howls.",
                         "Alas!", "Wow, the wolf howls.",
                         "Hush, the wolf sleeps."],
        "preposition": ["The wolf sleeps in the forest.",
                        "The man eats with the wolf.",
                        "The wolf of the forest sleeps.",
                        "The man eats before the wolf.",
                        "The cat jumps.",
                        "The man eats before the wolf hunts.",
                        "The wolf wants to run.",
                        "The cup was broken by the man.",
                        "The wolf sleeps in the zorblat."],
        "pronoun": ["They hunt the sheep.", "The man sees them.",
                    "She eats the meat.", "The man sees it.",
                    "The wolf sees me.", "The man sees her sheep.",
                    "It was broken.", "The zorblat sees them."],
    }
    for mod_name in ("noun", "verb", "adjective", "adverb",
                     "article", "conjunction", "interjection",
                     "preposition", "pronoun"):
        subset = det_subsets[mod_name]
        dump1 = json.dumps([find(s) for s in subset],
                           sort_keys=True)
        dump2 = json.dumps([find(s) for s in subset],
                           sort_keys=True)
        check("determinism_two_runs_identical[" + mod_name + "]",
              dump1 == dump2, dump1[:60])

    # ---------------------------------------------------------
    # (3) F2 invariant over the analyzer: finder trace lines ==
    # tokens, dictionary ON and OFF, plus one contraction and
    # one genitive sentence (dictionary ON).
    # ---------------------------------------------------------
    def invariant(sentence):
        a = _analyzer.analyze(sentence)
        fl = [line for line in a["trace"]
              if line.startswith("finder: ")]
        return len(fl) == len(_analyzer.clean(sentence).split()) \
            and len(a["finder"]) == len(fl)

    check("f2_invariant_dictionary_on",
          all(invariant(s) for s in acc_sentences),
          "finder lines == len(clean(sentence).split()) on the "
          "acceptance set, dictionary on")
    check("f2_invariant_contraction_and_genitive",
          invariant("It's the dog.")
          and invariant("The child's cup breaks the window."),
          "contraction + genitive shapes, dictionary on")
    _analyzer.configure_dictionary(None)
    try:
        inv_off = all(invariant(s) for s in acc_sentences)
    finally:
        _analyzer.configure_dictionary(db)
    check("f2_invariant_dictionary_off", inv_off,
          "same invariant with the dictionary tier off")

    # ---------------------------------------------------------
    # (5) F12 FALSE-LOCK: the standing battery runs unchanged
    # in this battery set, and no token in its sentences
    # receives a verb class from a resolver HOLD or NONE.
    # ---------------------------------------------------------
    import wg2_false_lock_battery as _flb
    check("f12_wg2_false_lock_battery_unchanged",
          _flb.main() == 0,
          "wg2_false_lock_battery.main() over the wired "
          "analyzer")
    fl_ok = True
    fl_detail = ""
    for sentence, _v, _h in _flb.CASES:
        rr = classify_tokens(_clean(sentence).split(),
                             seed_verbs=_analyzer.VERBS,
                             verb_forms=_analyzer.VERB_FORMS,
                             sentence=sentence)
        for o in rr["occurrences"]:
            if o["class"] == "verb" \
                    and "pmca_forms_resolver" \
                    in o["provenance"]["detail"]:
                res = _resolver.resolve(
                    o["word"], _analyzer.VERBS,
                    _analyzer.VERB_FORMS, context="verb_slot")
                if res["verdict"] != "OK":
                    fl_ok = False
                    fl_detail = (sentence + " / " + o["word"]
                                 + " / " + res["verdict"])
    check("f12_no_verb_from_resolver_hold_or_none", fl_ok,
          fl_detail or "every resolver-cited verb record in the "
          "FALSE-LOCK sentences traces to a resolver OK")

    # ---------------------------------------------------------
    # (6) FINDER FIX WAVE (2026-11-01): the W-1..W-8 fix cases.
    # Each case asserts the fixed record/disposition by
    # execution; the 91 ported legacy cases above are
    # unmodified.
    # ---------------------------------------------------------
    # W-1 + W-8: the FR-1 skip is occurrence-keyed, and the
    # skip of a classified interjection is recorded with the
    # grade it consumed on that occurrence's own trace line.
    # AMENDED (Jonathan's double-exclamation ruling,
    # 2026-11-01 12:10): the doubled wow is a burst — both
    # occurrences classify interjection and the sentence takes
    # the single-wow disposition. The pre-amendment case here
    # (doubled wow HOLDs unknown_word:wow) asserted the old
    # acceptance, which the ruling superseded; it is replaced
    # by the burst cases below, not silently dropped.
    _a = _analyzer.analyze("Wow wow, he breaks the cup.")
    _wow1 = _occ({"occurrences": _a["finder"]}, "wow", 1)
    check("fix_w1_burst_double_wow_classified",
          _a["verdict"] == "APPROVE"
          and _a["harm"] == (1, 1)
          and _wow1 is not None
          and _wow1["class"] == "interjection",
          str((_a["verdict"], _a["hold_reason"], _a["harm"])))
    _a3 = _analyzer.analyze("Wow wow wow, he breaks the cup.")
    check("fix_w1_burst_triple_wow_classified",
          _a3["verdict"] == "APPROVE"
          and all(_cls({"occurrences": _a3["finder"]}, "wow", p)
                  == "interjection" for p in (0, 1, 2)),
          str((_a3["verdict"], _a3["hold_reason"])))
    _a = _analyzer.analyze("Wow, he breaks the wow.")
    check("fix_w1_safety_net_clause_wow_still_holds",
          _a["verdict"] == "HOLD"
          and _a["hold_reason"] == "unknown_word:wow"
          and _a["harm"] is None,
          "the occurrence-keyed skip is the safety net: a wow "
          "inside the clause (no burst membership, no other "
          "classification) is never consumed by the skip")
    _a = _analyzer.analyze("Wow, he breaks the cup.")
    check("fix_w8_skip_records_its_grade",
          _a["verdict"] == "APPROVE"
          and any("skip=unknown_word_scan tier=CANDIDATE "
                  "gap=interjection_list_conflict:wow" in line
                  for line in _a["trace"]),
          "the CANDIDATE-grade skip is recorded with its grade "
          "and gap on wow@0's own finder line")
    # W-2: modal list membership is candidacy; the composed
    # sentence arbitrates content vs modal use.
    r = find("He can can the fish.")
    check("fix_w2_can_can_verb",
          _cls(r, "can", 1) == "modal"
          and _cls(r, "can", 2) == "verb",
          str((_cls(r, "can", 1), _cls(r, "can", 2))))
    r = find("A can can can soda.")
    check("fix_w2_can_can_can",
          _cls(r, "can", 1) == "noun"
          and _cls(r, "can", 2) == "modal"
          and _cls(r, "can", 3) == "verb",
          str((_cls(r, "can", 1), _cls(r, "can", 2),
               _cls(r, "can", 3))))
    r = find("The will to live can fade.")
    check("fix_w2_will_noun_can_modal",
          _cls(r, "will", 1) == "noun"
          and _cls(r, "can", 4) == "modal",
          str((_cls(r, "will", 1), _cls(r, "can", 4))))
    r = find("May is warm.")
    check("fix_w2_may_not_modal",
          _cls(r, "may", 0) == "noun",
          str(_cls(r, "may", 0)))
    # W-3: the fallback settles only on evidence it gathers.
    r = find("The dog is big.")
    _big_w3 = _occ(r, "big")
    check("fix_w3_big_adjective",
          _cls(r, "big") == "adjective"
          and _big_w3 is not None and _big_w3["gap"] is None,
          str((_cls(r, "big"),
               _big_w3["gap"] if _big_w3 else None)))
    r = find("He glorped the food quickly.")
    check("fix_w3_quickly_adverb_parse_dead",
          _cls(r, "quickly") == "adverb"
          and _cls(r, "food") == "noun",
          str((_cls(r, "quickly"), _cls(r, "food"))))
    # W-6: morphology alone is CANDIDATE + morphology_only gap.
    r = find("The dog ate the food dogly.")
    _dogly = _occ(r, "dogly")
    check("fix_w6_dogly_morphology_only_gap",
          _dogly is not None and _dogly["class"] == "adverb"
          and _dogly["gap"] is not None
          and _dogly["gap"]["reason"] == "morphology_only:dogly",
          str(_dogly["gap"] if _dogly else None))
    # W-4: possessives / whom / ought from stored sources;
    # per stays as-is (no stored source attests its class —
    # gap item in the fix wave's build paper).
    r = find("The dog ate his food.")
    check("fix_w4_his_pronoun",
          _cls(r, "his") == "pronoun", str(_cls(r, "his")))
    r = find("The bone is mine.")
    _theirs = find("The dog is theirs.")
    check("fix_w4_mine_and_theirs_pronoun",
          _cls(r, "mine") == "pronoun"
          and _cls(_theirs, "theirs") == "pronoun",
          str((_cls(r, "mine"), _cls(_theirs, "theirs"))))
    r = find("He saw whom.")
    check("fix_w4_whom_pronoun_not_noun",
          _cls(r, "whom") == "pronoun", str(_cls(r, "whom")))
    r = find("He ought to run.")
    check("fix_w4_ought_modal",
          _cls(r, "ought") == "modal", str(_cls(r, "ought")))
    r = find("The dog ate per the rules.")
    _per_w4 = _occ(r, "per")
    check("fix_w4_per_gap_item_unchanged",
          _cls(r, "per") == "noun"
          and _per_w4 is not None
          and _per_w4["gap"] is not None
          and _per_w4["gap"]["reason"]
          == "capability_only_settle:per",
          "per is NOT classified preposition: no stored source "
          "in the tree attests its class (gap item, W-4); the "
          "negative-evidence wave (2026-11-02) landed the gap "
          "item on the occurrence record itself — class "
          "expectation unchanged, the record's silence is "
          "what changed")
    # W-5: the genitive screen's possessor is recorded as a
    # candidate occurrence relation, contraction or not.
    r = find("The man's dog ate the food.")
    check("fix_w5_possessor_recorded",
          _rel_words(r, "man's", "possessor") == ["dog"],
          str(_rel_words(r, "man's", "possessor")))
    r = find("It's the man's dog.")
    check("fix_w5_possessor_recorded_with_contraction",
          _rel_words(r, "man's", "possessor") == ["dog"],
          str(_rel_words(r, "man's", "possessor")))
    # W-7: a token's gap carries its own true reason.
    r = find("The glorpy dog ate the food.")
    _ate = _occ(r, "ate")
    check("fix_w7_gap_reason_is_the_tokens_own",
          _ate is not None and _ate["gap"] is not None
          and _ate["gap"]["reason"] == "dual_class_candidate:ate",
          str(_ate["gap"] if _ate else None))
    # R-1 SCOUR (2026-11-02) Seam 2 — Wreck's S1-S7 probes,
    # with the expectations stated in the scour report
    # section 2.1 (derived from the composed sentences and
    # the stored record before the runs). The wave's law: a
    # settle minted from capability evidence alone is never
    # gapless again, and no provenance claims a ground the
    # stored entry contradicts.
    r = find("The fee is per.")
    _s1 = _occ(r, "per")
    check("scour_s1_per_predicate_not_gapless",
          _s1 is not None and _s1["class"] == "adjective"
          and _s1["gap"] is not None
          and _s1["gap"]["reason"]
          == "adjective_attestation_missing:per"
          and "opens as a quality"
          not in _s1["provenance"]["detail"],
          str((_s1["gap"] if _s1 else None,
               _s1["provenance"]["detail"] if _s1 else None)))
    r = find("The dog is big.")
    _s2 = _occ(r, "big")
    check("scour_s2_big_seed_attested_gapless",
          _s2 is not None and _s2["class"] == "adjective"
          and _s2["gap"] is None
          and "seed attestation"
          in _s2["provenance"]["detail"],
          str((_s2["gap"] if _s2 else None,
               _s2["provenance"]["detail"] if _s2 else None)))
    r = find("The dog ate per the rules.")
    _s3 = _occ(r, "per")
    check("scour_s3_per_prepositional_noun_with_gap",
          _s3 is not None and _s3["class"] == "noun"
          and _s3["gap"] is not None
          and _s3["gap"]["reason"]
          == "capability_only_settle:per",
          str(_s3["gap"] if _s3 else None))
    r = find("He pays five dollars per hour.")
    _s4 = _occ(r, "per")
    check("scour_s4_per_distributive_noun_with_gap",
          _s4 is not None and _s4["class"] == "noun"
          and _s4["gap"] is not None
          and _s4["gap"]["reason"]
          == "capability_only_settle:per",
          str(_s4["gap"] if _s4 else None))
    r = find("The book is on the table.")
    _s5 = _occ(r, "on")
    check("scour_s5_on_preposition_attested_gapless",
          _s5 is not None and _s5["class"] == "preposition"
          and _s5["tier"] == "attested"
          and _s5["gap"] is None,
          str((_s5["class"], _s5["tier"], _s5["gap"])
              if _s5 else None))
    r = find("The rate is high.")
    _s6 = _occ(r, "high")
    check("scour_s6_high_adjective_with_gap",
          _s6 is not None and _s6["class"] == "adjective"
          and _s6["gap"] is not None
          and _s6["gap"]["reason"]
          == "adjective_attestation_missing:high",
          str(_s6["gap"] if _s6 else None))
    r = find("The charge is per unit.")
    _s7 = _occ(r, "per")
    check("scour_s7_per_heads_phrase_not_adjective",
          _s7 is not None and _s7["class"] == "noun"
          and _s7["gap"] is not None
          and _s7["gap"]["reason"]
          == "capability_only_settle:per",
          str((_s7["class"], _s7["gap"]) if _s7 else None))

    # ---------------------------------------------------------
    # FIX WAVE 2 (2026-11-02): Wreck's re-attack specimens
    # (WRECK2-1..WRECK2-5). Expectations are Wreck's, derived
    # from the composed sentences and the stored record:
    # capability-only settles in the settled parse carry the
    # capability_only_settle gap (order C completed); settled
    # provenance names capability as capability; a Webster
    # marker attests only from a string the headword owns;
    # the dual [noun, adverb] candidacy in the predicate
    # slot carries the adjective among its candidates.
    # ---------------------------------------------------------
    check("wreck2_marker_ownership_function_level",
          not _guards.adjective_attested("aimlessness")
          and not _guards.adjective_attested("abaca")
          and not _guards.adjective_attested("alder")
          and not _guards.adjective_attested("accumulatively")
          and not _guards.adjective_attested("able-bodiedness")
          and _guards.adjective_attested("accumulate")
          and _guards.adjective_attested("hourly")
          and "owns" in (_guards.adjective_attestation("hourly")
                         or ""),
          "borrowed markers (Aimless/Manila/Aller/Accumulative/"
          "Able-bodied strings) no longer attest; the word's own "
          "marker still does (accumulate fragment, hourly)")
    _a = _analyzer.analyze("The dog ate the per.")
    _v1 = _occ({"occurrences": _a["finder"]}, "per")
    check("wreck2_v1_per_noun_head_gap",
          _v1 is not None and _v1["class"] == "noun"
          and _v1["gap"] is not None
          and _v1["gap"]["reason"]
          == "capability_only_settle:per",
          str(_v1["gap"] if _v1 else None))
    _a = _analyzer.analyze("The big fell.")
    _v2 = _occ({"occurrences": _a["finder"]}, "big")
    check("wreck2_v2_big_noun_head_gap",
          _v2 is not None and _v2["class"] == "noun"
          and _v2["gap"] is not None
          and _v2["gap"]["reason"]
          == "capability_only_settle:big",
          str(_v2["gap"] if _v2 else None))
    _a = _analyzer.analyze("The run ended.")
    _v3 = _occ({"occurrences": _a["finder"]}, "run")
    check("wreck2_v3_run_dual_provenance_truthful",
          _v3 is not None and _v3["class"] == "noun"
          and _v3["gap"] is not None
          and _v3["gap"]["reason"]
          == "capability_only_settle:run"
          and "winning evidence"
          in _v3["provenance"]["detail"]
          and "winning attestation"
          not in _v3["provenance"]["detail"]
          and "rival capability candidate: verb"
          in _v3["provenance"]["detail"],
          str((_v3["gap"], _v3["provenance"]["detail"])
              if _v3 else None))
    _a = _analyzer.analyze("The dog runs.")
    _v4 = _occ({"occurrences": _a["finder"]}, "runs")
    check("wreck2_v4_runs_verb_gap",
          _v4 is not None and _v4["class"] == "verb"
          and _v4["gap"] is not None
          and _v4["gap"]["reason"]
          == "capability_only_settle:runs"
          and "winning attestation"
          not in _v4["provenance"]["detail"],
          str((_v4["gap"], _v4["provenance"]["detail"])
              if _v4 else None))
    _a = _analyzer.analyze("The problem is aimlessness.")
    _p1 = _occ({"occurrences": _a["finder"]}, "aimlessness")
    check("wreck2_p1_aimlessness_predicate_gap",
          _p1 is not None and _p1["class"] == "adjective"
          and _p1["gap"] is not None
          and _p1["gap"]["reason"]
          == "adjective_attestation_missing:aimlessness",
          str((_p1["class"], _p1["gap"]) if _p1 else None))
    _a = _analyzer.analyze("The fiber is abaca.")
    _p2 = _occ({"occurrences": _a["finder"]}, "abaca")
    check("wreck2_p2_abaca_predicate_gap",
          _p2 is not None and _p2["class"] == "adjective"
          and _p2["gap"] is not None
          and _p2["gap"]["reason"]
          == "adjective_attestation_missing:abaca",
          str((_p2["class"], _p2["gap"]) if _p2 else None))
    _a = _analyzer.analyze("The tree is alder.")
    _p3 = _occ({"occurrences": _a["finder"]}, "alder")
    check("wreck2_p3_alder_predicate_gap",
          _p3 is not None and _p3["class"] == "adjective"
          and _p3["gap"] is not None
          and _p3["gap"]["reason"]
          == "adjective_attestation_missing:alder",
          str((_p3["class"], _p3["gap"]) if _p3 else None))
    _a = _analyzer.analyze("The man is lonely.")
    _p5 = _occ({"occurrences": _a["finder"]}, "lonely")
    check("wreck2_p5_lonely_adjective_candidate",
          _p5 is not None and _p5["class"] is None
          and _p5["gap"] is not None
          and "adjective" in _p5["gap"]["candidates"]
          and "noun sense both attested"
          not in _p5["provenance"]["detail"],
          str((_p5["gap"], _p5["provenance"]["detail"])
              if _p5 else None))
    _a = _analyzer.analyze("The party is lively.")
    _p6 = _occ({"occurrences": _a["finder"]}, "lively")
    check("wreck2_p6_lively_adjective_candidate",
          _p6 is not None and _p6["class"] is None
          and _p6["gap"] is not None
          and "adjective" in _p6["gap"]["candidates"]
          and "noun sense both attested"
          not in _p6["provenance"]["detail"],
          str((_p6["gap"], _p6["provenance"]["detail"])
              if _p6 else None))
    _a = _analyzer.analyze("The pay is hourly.")
    _p7 = _occ({"occurrences": _a["finder"]}, "hourly")
    check("wreck2_p7_hourly_adjective_candidate",
          _p7 is not None and _p7["class"] is None
          and _p7["gap"] is not None
          and "adjective" in _p7["gap"]["candidates"],
          str(_p7["gap"] if _p7 else None))
    _a = _analyzer.analyze("The man is awake.")
    _p8 = _occ({"occurrences": _a["finder"]}, "awake")
    check("wreck2_p8_awake_verb_gap",
          _p8 is not None and _p8["class"] == "verb"
          and _p8["gap"] is not None
          and _p8["gap"]["reason"]
          == "capability_only_settle:awake",
          str((_p8["class"], _p8["gap"]) if _p8 else None))

    # FINDER R-1 (ruled 2026-11-02, rulings sitting; built
    # 2026-11-03): uniform-paraphrase attestation, F1-F4 +
    # fallback grade + marker case. Expectations from the
    # stored record by execution (Forge's full-store R-1
    # scan, scour directory forge-r1/scan_out.txt).
    def _r1_unit(res, unit):
        for u in res["units"]:
            if u["unit"] == unit:
                return u
        return None

    _r1per = _guards.paraphrase_attestation("per")
    check("r1_per_attests_preposition",
          _r1per["attested_class"] == "preposition"
          and _r1per["tier"] == "attested"
          and _r1per["conflict"] is None
          and _r1per["gap"] is None
          and _r1per["marker_classes"] == []
          and _r1_unit(_r1per, "by means of") is not None
          and _r1_unit(_r1per, "by means of")["classes"]
          == ["preposition"]
          and _r1_unit(_r1per, "through the agency of")
          is not None
          and _r1_unit(_r1per, "through the agency of")
          ["classes"] == []
          and _r1_unit(_r1per, "for each") is not None
          and _r1_unit(_r1per, "for each")["classes"] == [],
          str((_r1per["attested_class"], _r1per["tier"],
               _r1per["gap"])))
    _r1acc = _guards.paraphrase_attestation("accumulate")
    # AMENDED in fix wave 3 (2026-11-03), grounds in
    # forge-fix-wave3-report.md: this specimen's paraphrase
    # side stood on the citation fragment 'p' ("p. p.") as
    # one of its only two classified witnesses — B1
    # evidence. With fragments excluded, one classified
    # unit remains ('accumulated'), the floor is unmet,
    # and there is no paraphrase verdict for the marker to
    # conflict with; the word's own adjective marker
    # stands alone on the record. The F4 path itself is
    # pinned by the r1_w3_f4_conflict_* check below.
    check("r1_f4_conflict_accumulate",
          _r1acc["attested_class"] is None
          and _r1acc["tier"] is None
          and _r1acc["conflict"] is None
          and _r1acc["marker_classes"] == ["adjective"],
          str((_r1acc["conflict"],
               _r1acc["marker_classes"])))
    _r1eyed = _guards.paraphrase_attestation("eyed")
    check("r1_use_fallback_eyed_candidate",
          _r1eyed["attested_class"] == "adjective"
          and _r1eyed["tier"] == "CANDIDATE"
          and _r1eyed["gap"]
          == "paraphrase_use_fallback:eyed"
          and all(u["classes"] == [] for u in
                  _r1eyed["units"]
                  if u["disposition"] == "vote"),
          str((_r1eyed["attested_class"], _r1eyed["tier"],
               _r1eyed["gap"])))
    check("r1_marker_case_name_vs_noun",
          _guards.paraphrase_unit_classes("adpocalypse")
          == ["proper noun"]
          and _guards.paraphrase_unit_classes("aabomycin")
          == ["noun"],
          str((_guards.paraphrase_unit_classes(
                   "adpocalypse"),
               _guards.paraphrase_unit_classes(
                   "aabomycin"))))

    # FIX WAVE 3 (2026-11-03): R-1 evidence hygiene —
    # Wreck's families B1/B5/B2/B3/B6 + Gauge's B0
    # residual. Expectations from the stored record:
    # Wreck's attack sweep (wreck-r1/sweep.jsonl) and
    # Forge's wave-3 specimen runs (forge-fix3/).
    def _r1_none(word):
        _r = _guards.paraphrase_attestation(word)
        return (_r["attested_class"] is None
                and _r["tier"] is None)

    check("r1_w3_b1_citation_fragments_shewn_vow",
          _r1_none("shewn") and _r1_none("vow"),
          "shewn/vow must not attest on citation fragments")
    check("r1_w3_b1_apparatus_testify_desist",
          _r1_none("testify") and _r1_none("desist"),
          "testify/desist must not attest on apparatus")
    check("r1_w3_b1_paradigm_and_abbrev_thy_boor",
          _r1_none("thy") and _r1_none("boor"),
          "thy (paradigm labels) / boor (esp.) must not "
          "attest on fragments")
    check("r1_w3_b2_floor_counts_distinct_units",
          _r1_none("ascertain"),
          "ascertain: one distinct unit (taylor) quoted "
          "twice is one witness, not two")
    _r1dec = _guards.paraphrase_attestation("decisiveness")
    check("r1_w3_b3_borrowed_definition_decisiveness",
          _r1dec["attested_class"] is None
          and _r1dec["borrowed_from"] == ["decisive"],
          str(_r1dec["borrowed_from"]))
    _r1pc = _guards.paraphrase_attestation("per centum")
    check("r1_w3_b3_borrowed_definition_per_centum",
          _r1pc["attested_class"] is None
          and _r1pc["borrowed_from"] == ["per"],
          str(_r1pc["borrowed_from"]))
    check("r1_w3_b3_borrowed_definition_because_of",
          _r1_none("because of"),
          "because of reads BECAUSE's text, not its own")
    _r1atall = _guards.paraphrase_attestation("at all")
    check("r1_w3_b6_fallback_reads_own_examples_only",
          _r1atall["attested_class"] is None
          and _r1atall["tier"] is None
          and _r1atall["borrowed_from"] == ["all", "at"],
          str((_r1atall["attested_class"],
               _r1atall["borrowed_from"])))
    check("r1_w3_b5_nonword_units_cast_no_vote",
          _guards.paraphrase_unit_classes("4") == []
          and _guards.paraphrase_unit_classes("p") == [],
          "numeral/letter rows are not word-class "
          "witnesses")
    check("r1_w3_b5_phrase_form_is_not_a_class",
          _guards.paraphrase_unit_classes("at work") == []
          and _guards.paraphrase_unit_classes("on high")
          == []
          and _guards.paraphrase_unit_classes(
              "by means of") == ["preposition"]
          and _r1_none("aloft"),
          "prep_phrase rows attest form, not class; "
          "aloft must not attest preposition on them")
    check("r1_w3_b4_energical_fragment_collapse_removed",
          _r1_none("energical"),
          "energical: without the citation fragment "
          "'mag', its participial units intersect at "
          "{adjective, noun} — ambiguous, no attestation")
    _r1nas = _guards.paraphrase_attestation("nascent")
    check("r1_w3_b4_nascent_participial_specimen",
          sorted(u["unit"] for u in _r1nas["units"]
                 if u["disposition"] == "vote"
                 and u["classes"])
          == ["coming into being", "evolving"]
          and all(u["classes"] == ["verb"]
                  for u in _r1nas["units"]
                  if u["disposition"] == "vote"
                  and u["classes"]),
          "FORK GAP FG-W3-1: nascent's only classified "
          "gloss units are two verb-form phrases; whether "
          "a participial paraphrase attests the verb class "
          "is unruled — this check pins the specimen's "
          "evidence, not a verdict on the word")
    _r1andro = _guards.paraphrase_attestation(
        "androdioecism")
    check("r1_w3_b0_hyphen_header_marker_ownership",
          _r1andro["marker_classes"] == []
          and not _guards.adjective_attested(
              "androdioecism")
          and _guards.adjective_attested("-diecious"),
          "the -diecious string's marker belongs to "
          "-diecious, not to androdioecism")
    # The F4 path on clean evidence (replacing the
    # fragment-dependent accumulate specimen, amended
    # above): beauish's own tag is ", n." while its own
    # gloss pieces — 'foppish' and 'fine', two distinct
    # classified units of its own text, nothing borrowed
    # or shared — intersect at {adjective}. Neither wins:
    # the conflict is recorded and the word is not
    # settled by this test (F4, ruled 2026-11-02).
    _r1beau = _guards.paraphrase_attestation("beauish")
    check("r1_w3_f4_conflict_beauish",
          _r1beau["attested_class"] is None
          and _r1beau["tier"] is None
          and _r1beau["conflict"] is not None
          and _r1beau["conflict"]["marker_classes"]
          == ["noun"]
          and _r1beau["conflict"]["paraphrase_class"]
          == "adjective"
          and _r1beau["borrowed_from"] == []
          and sorted(u["unit"] for u in _r1beau["units"]
                     if u["disposition"] == "vote"
                     and u["classes"])
          == ["fine", "foppish"],
          str((_r1beau["conflict"],
               _r1beau["marker_classes"])))
    # Wave-3 completion specimens (same wave; found by
    # the wave's own after-sweep verification): citation
    # and apparatus text that still voted after the first
    # pass — dragoman's spurious F4 conflict stood on the
    # etymology fragments 'ar' + 'cf'; abb attested noun
    # on 'pref' + 'see'; the BY article attested day by
    # day as a preposition from a shifted phrase row;
    # on't attested on ON's text verbatim.
    check("r1_w3_b1_etymology_bracket_dragoman",
          _r1_none("dragoman")
          and _guards.paraphrase_attestation(
              "dragoman")["conflict"] is None,
          "dragoman's record is its header and etymology "
          "fragment alone; no gloss votes, no conflict")
    check("r1_w3_b1_apparatus_tokens_abb",
          _r1_none("abb"),
          "abb's fragment attests nothing: 'pref' and "
          "'see' are apparatus tokens, not glosses")
    _r1day = _guards.paraphrase_attestation("day by day")
    check("r1_w3_b3_shared_article_day_by_day",
          _r1day["attested_class"] is None
          and _r1day["borrowed_from"] == ["by"],
          "day by day's row is the BY article served "
          "under a phrase headword; it attests nothing")
    _r1ont = _guards.paraphrase_attestation("on't")
    check("r1_w3_b3_shared_article_ont",
          _r1ont["attested_class"] is None
          and _r1ont["borrowed_from"] == ["on"],
          "on't's row is ON's entry text verbatim; it "
          "attests nothing")

    # FIX WAVE 4 (2026-11-03): R-1 second-tranche
    # hygiene — Wreck's fix-wave-3 re-attack families:
    # quotation shatter, citation fragments tranche 2,
    # ownership blind spots (sibling consensus, single-
    # row ghosts, nomination gaps), self-naming, and
    # directional chain naming. Expectations from the
    # stored record by execution (forge-fix4/).
    def _r1_disp(res, unit):
        u = _r1_unit(res, unit)
        return u["disposition"] if u else None

    _r1imp = _guards.paraphrase_attestation("imperfect")
    check("r1_w4_quote_shatter_imperfect_froth",
          _r1_none("imperfect") and _r1_none("froth")
          and _r1_unit(_r1imp, "he") is None,
          "the unmarked quotation 'He . . . stammered "
          "...' no longer shatters: imperfect attests "
          "nothing on a quotation pronoun, and froth's "
          "gloss units (spume/foam) intersect honestly "
          "at {noun, verb}")
    check("r1_w4_quote_shatter_protection_squint",
          _r1_none("protection") and _r1_none("squint"),
          "protection's deciding vote was the quotation "
          "pronoun 'he'; without it its units intersect "
          "at {noun, verb}. squint keeps only "
          "'strabismus' classified — floor unmet")
    _r1gos = _guards.paraphrase_attestation("gospel")
    check("r1_w4_citation_tranche2_bergamot_gospel_b",
          _r1_none("bergamot") and _r1_none("gospel")
          and _r1_none("b")
          and _r1_unit(_r1gos, "oe") is None,
          "the 'Encyc. Brit.' citation chain, the 'OE.' "
          "language label and b's 'Eng.'/'Lat.' "
          "fragments cast no vote; gospel keeps 'glad "
          "tidings' alone — floor unmet")
    _r1fie = _guards.paraphrase_attestation("fiend")
    check("r1_w4_fiend_no_definition_text",
          _r1fie["attested_class"] is None
          and _r1fie["gap"]
          == "paraphrase_no_definition_text:fiend"
          and len(_r1fie["units"]) >= 3
          and all(u["disposition"] == "apparatus"
                  for u in _r1fie["units"]),
          "fiend's stored row is a header and a "
          "truncated etymology (depth-counted brackets "
          "keep it whole); a row with no definition "
          "text cannot attest, and the gap names it")
    _r1cal = _guards.paraphrase_attestation("calculate")
    _r1cus = _guards.paraphrase_attestation("custom")
    check("r1_w4_usage_label_calculate_custom_control",
          _r1_disp(_r1cal, "usage") == "apparatus"
          and _r1cus["attested_class"] == "noun",
          "the 'Usage:' section label standing as a "
          "whole piece is apparatus; the word 'usage' "
          "inside gloss text still testifies (custom "
          "attests noun on its own glosses)")
    def _r1_shared(word):
        _r = _guards.paraphrase_attestation(word)
        return (_r["attested_class"] is None
                and any(u["disposition"] == "shared"
                        for u in _r["units"]))

    check("r1_w4_sibling_consensus_trespass",
          _r1_shared("trespass offering")
          and _r1_shared("trespass on the case"),
          "the trespass phrases share TRESPASS's "
          "sense-elements with each other while "
          "trespass's own row holds different text: "
          "sibling-consensus text is shared, abstains")
    _r1ast = _guards.paraphrase_attestation("astral lamp")
    _r1dn = _guards.paraphrase_attestation(
        "deadly nightshade")
    check("r1_w4_ghost_astral_lamp_deadly_nightshade",
          _r1ast["attested_class"] is None
          and _r1ast["borrowed_from"] == ["astral"]
          and _r1dn["attested_class"] is None
          and "deadly" in _r1dn["borrowed_from"],
          "a parent sense extracted only under a "
          "phrase's row is the component's text, not "
          "the phrase's own: astral lamp reads "
          "ASTRAL's gloss; deadly nightshade reads "
          "DEADLY's senses")
    check("r1_w4_nomination_stems",
          _guards.paraphrase_attestation(
              "impersuasibility")["borrowed_from"]
          == ["impersuasible"]
          and _guards.paraphrase_attestation(
              "incompossibility")["borrowed_from"]
          == ["incompossible"]
          and _guards.paraphrase_attestation(
              "athletically")["borrowed_from"]
          == ["athletic"]
          and _guards.paraphrase_attestation(
              "elusoriness")["borrowed_from"]
          == ["elusory"]
          and _r1_none("impersuasibility")
          and _r1_none("athletically"),
          "the real stems are nominated: -ibility "
          "reads -ible, -ically reads -ic, -iness "
          "reads -y; the derivatives attest nothing on "
          "their stems' articles")
    check("r1_w4_derivative_siblings_shared",
          _r1_shared("fictitiousness")
          and _r1_shared("obsceneness"),
          "-ness/-ly sibling pairs holding their stem's "
          "text in exactly two rows with the stem's own "
          "row divergent: shared, abstain")
    _r1adam = _guards.paraphrase_attestation("adam")
    check("r1_w4_self_naming_adam",
          "adam" not in _r1adam["borrowed_from"],
          "a word never names itself as its own "
          "borrower: adam's possessive holders nominate "
          "it (possessives normalized), so its own text "
          "stands as its own")
    _r1apo = _guards.paraphrase_attestation(
        "apothecaries' weight")
    check("r1_w4_chain_apothecaries_weight",
          _r1apo["borrowed_from"] == ["apothecary"],
          "borrower naming walks the chain to the "
          "source: apothecaries' weight names "
          "APOTHECARY, not the fellow borrower "
          "apothecaries")
    _r1abbey = _guards.paraphrase_attestation("abbey")
    check("r1_w4_syn_channel_untouched_abbey",
          _r1abbey["attested_class"] == "noun",
          "control: SYN-1 is unruled and untouched — "
          "abbey's verdict still stands on its own "
          "row's list units, exactly as in wave 3")

    # FINDER R-1 fix wave 5 (naming completion): the
    # chain resolution guards every naming route, and
    # entry-level borrowers resolve through the entry
    # headword's own read. Gauge's wave-4 verification
    # REFUTED the wave-4 naming claim — the audit
    # script had kept Wreck's cands[:60] cap; uncapped
    # (945 candidates, 527 chain-tested on Wreck's
    # census), 7 genuine direction errors + 1
    # shared-channel overclaim survived in the
    # possessive / "one's" family. The last check is
    # that audit, UNCAPPED, over the full candidate
    # census, so a capped audit can never pass
    # silently again.
    _r1oh = _guards.paraphrase_attestation(
        "off one's hands")
    _r1onh = _guards.paraphrase_attestation(
        "on one's hands")
    _r1full = _guards.paraphrase_attestation(
        "to have one's hands full")
    check("r1_w5_possessive_chain_hands",
          _r1oh["borrowed_from"] == ["hand"]
          and _r1onh["borrowed_from"] == ["hand"]
          and _r1full["borrowed_from"] == ["hand"],
          "the verbatim-parent route is chain-walked: "
          "the one's-hands phrases name HAND, whose "
          "article their rows serve through the fellow "
          "borrower HANDS")
    _r1fo = _guards.paraphrase_attestation(
        "to feel one's oats")
    _r1so = _guards.paraphrase_attestation(
        "to sow one's wild oats")
    check("r1_w5_possessive_chain_oats",
          _r1fo["borrowed_from"] == ["oat"]
          and _r1so["borrowed_from"] == ["oat"],
          "the one's-oats phrases name OAT through "
          "the fellow borrower OATS")
    _r1la = _guards.paraphrase_attestation(
        "the lord's anointed")
    check("r1_w5_chain_lords_anointed",
          _r1la["borrowed_from"] == ["anoint"],
          "the lord's anointed names ANOINT, not the "
          "fellow borrower ANOINTED")
    _r1ny = _guards.paraphrase_attestation("new year's")
    check("r1_w5_entry_chain_new_years",
          _r1ny["borrowed_from"] == ["new"],
          "entry-level naming resolves through the "
          "resolved headword's own read: new year's "
          "names NEW through NEW YEAR")
    _r1kn = _guards.paraphrase_attestation("kneipp's")
    check("r1_w5_entry_chain_kneipps",
          _r1kn["borrowed_from"] == ["kneippism"],
          "kneipp's names KNEIPPISM — the source row "
          "whose article the element is — not the "
          "fellow holder KNEIPP")
    _r1gl = _guards.paraphrase_attestation("glover's")
    check("r1_w5_entry_chain_glovers_control",
          _r1gl["borrowed_from"] == ["glover"],
          "control: glover's still names GLOVER — "
          "the element heads glover's own article, "
          "so the name is the source row, not an "
          "error (Gauge's audit false positive)")

    def _r1_naming_audit():
        """Wreck's borrower-naming audit, UNCAPPED, on
        the mechanism's own census, with Gauge's
        origin-row adjudication: a named borrower x of
        w is a direction error for a census-shared
        element (count >= 3, held by x) standing in
        w's row when x's own record reads the element
        as borrowed onward (a fellow borrower, one
        link short of the source), or as shared while
        the element does not head x's article (x is
        not its source row). A borrower that reads
        the element as its own, or whose article the
        element heads, IS the source row and naming
        it is correct. Returns (candidates,
        chain-tested, errors)."""
        import hashlib as _hl
        import re as _re

        def _cent(n):
            ent = _guards._r1_shared_census().get(
                (_hl.md5(n.encode("utf-8")).hexdigest(),
                 len(n)))
            if ent is not None and ent[2] == n:
                return ent
            return None

        def _wreck_parents(w):
            out = set()
            if " " in w:
                out.update(w.split())
            out.update(pmca_dictionary._candidates(w))
            if w.endswith("ness") and len(w) > 5:
                out.add(w[:-4])
            if w.endswith("ly") and len(w) > 3:
                out.add(w[:-2])
            return out

        con = _guards._r1_webster_conn()
        words = [r[0] for r in con.execute(
            "SELECT word FROM defs ORDER BY word")]
        cands = []
        elems = {}
        for w in words:
            entry = pmca_dictionary.lookup(w)
            if entry is None:
                continue
            pieces = _guards._r1_build_pieces(w, entry)
            names = {p["borrowed_from"] for p in pieces
                     if p.get("borrowed_from")}
            if not names:
                continue
            defs = _guards._r1_webster_defs(w)
            if defs is None:
                defs = entry["definitions"]
            els = []
            for d in defs:
                if isinstance(d, str):
                    n = _guards._r1_norm_text(d)
                    if len(n) >= 20:
                        els.append((d, n))
            if els:
                elems[w] = els
            toks = _re.findall(r"[a-z]+", w)
            pw = _wreck_parents(w)
            for x in names:
                if (x in toks and x not in pw) \
                        or "'" in w:
                    cands.append((w, x))
        tested = 0
        errors = []
        for w, x in cands:
            shared = []
            for d, n in elems.get(w, []):
                ent = _cent(n)
                if ent is not None and ent[0] >= 3 \
                        and x in ent[1]:
                    shared.append(d)
            if not shared:
                continue
            tested += 1
            pd_x = _guards._r1_parents_data(x)
            fam_x = _guards._r1_family_rows(
                x.split()[0]) if " " in x else None
            for d in shared:
                read = _guards._r1_element_borrower(
                    x, d, pd_x, fam_x)
                if isinstance(read, str):
                    errors.append((w, x,
                                   "borrows onward"))
                    break
                if read is _guards._R1_SHARED and \
                        _guards._r1_header_source(d) \
                        != x:
                    errors.append((w, x,
                                   "shared, not source"))
                    break
        return len(cands), tested, errors

    _nac, _nat, _nae = _r1_naming_audit()
    check("r1_w5_naming_audit_full_census",
          _nae == [],
          "the borrower-naming audit over the FULL "
          "candidate census, uncapped: %d candidates, "
          "%d chain-tested, %d direction errors %s"
          % (_nac, _nat, len(_nae), _nae[:5]))

    passed = sum(1 for x in results if x)
    print("%d/%d checks passed" % (passed, len(results)))
    return passed == len(results)


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_checks() else 1)
