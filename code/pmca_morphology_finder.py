"""pmca_morphology_finder.py — the MORPHOLOGY MICROSCRIPT for PMCA.

Design: Jonathan Wayne Fleuren (the builder). Mechanism from his
rulings, as noted inline. Code: Alder (Muse). Built in the
DUPLICATE tree (pmca_argon_test) only, per Jonathan's build method:
the running system is never modified in place.

What this is: the tenth of Jonathan's grammar microscripts
(2026-10-21). Each microscript is one understood grammatical
variable, tiny, RAM-resident in the full system, verifiable and
replaceable alone. This one holds the MORPHOLOGY variable:
morphology is the rules of how a word's forms relate — a form is
the shape a word takes for its place in the sequence (wolf /
wolves; crush / crushed / crushing; they wolf / he wolfs).

JONATHAN'S CONDITION, the spine of this build: morphology as
STORED STRUCTURE, never as a runtime guessing machine. The old
suffix-stemmer's inventions ("quadrup", "quadrupe", a genus named
"n") are the documented failure mode this module replaces. So:
rules GENERATE candidate bases only; the dictionary DISPOSES.
No form is ever returned that the stored text cannot attest.

resolve_form(token) -> {"token", "verdict": "OK"|"HOLD",
"hold_reason", "entry", "form", "evidence"} with keys in this
fixed order. form is one of: base, plural, third_person, past,
participle_ing, comparative, superlative, adverbial_ly,
possessive, base_or_plural. Evidence discipline, strongest
first:

(a) TEXT ATTESTATION. If the token's own stored definitions name
    it as a form of another entry (GCIDE cross-reference
    patterns "pl. of X", "imp. of X", "p. p. of X"), the entry
    and form come FROM THE TEXT and the attesting fragment is
    quoted in evidence. A definition list that is a pure
    cross-reference stub ("See {Big}") resolves through to its
    target as a base. MEASURED LIMIT OF THIS BUILD: the Webster
    extraction stored here replaced cross-reference entries
    with copies of the base entry's definitions, so pattern (a)
    fires zero times over this module's probe vocabulary
    (wolves, went, children, geese, feet, men, oxen, ran,
    eaten, better, mice, teeth). The pattern is implemented for
    extractions that preserve the text; here (c) does the work.
(c) RULE-GENERATED, DICTIONARY-ATTESTED. A closed table of
    inflection rules (plural / third-person -s/-es with -ies
    and -ves variants; past -ed with e-drop, doubling, -ied;
    -ing with e-drop and doubling; comparative -er / superlative
    -est with -ier/-iest and doubling; adverbial -ly) generates
    candidate bases ONLY. A candidate is accepted iff the base
    is an exact headword in pmca_dictionary. (Candidate bases
    that are themselves cross-reference stubs are followed
    through to their targets — "bigg" is a stub for "big", not
    an independent base.) If two different bases both resolve,
    HOLD ambiguous_form with both named. If the token is itself
    a headword, the rule reading wins only when the store shows
    the headword is the extraction's copy of the base entry
    (stored definitions identical to the base's) or, for -ly,
    when the token's own entry tags itself "adv."; otherwise
    the headword stands as its own base (news, butter).
    The -s family is two-way (plural noun / third-person verb):
    a standalone -s token is read by its base entry's attested
    senses — verb senses only -> third_person; no verb senses
    -> plural; both -> HOLD ambiguous_form, both readings named.
    (A verb sense = a definition beginning "To " after stripping
    leading "(...)" apparatus labels, the GCIDE convention; the
    extraction also stores illustrative quotations as definition
    strings, and a quotation counts as non-verb text, so -s
    forms of quotation-polluted verb entries HOLD ambiguous
    standalone. Agreement, below, is unaffected: it reads form
    shape, not senses.)
(b) HEADWORD. The token is itself an exact headword and owns at
    least one definition found in no other entry: entry =
    token, form = base — or base_or_plural when the entry
    itself carries a "pl."-marked sense and no token+"s"
    headword exists (sheep; the classic "sing. & pl." header
    marker is not present in this build's stored text, and the
    both-numbers reading rests on the store facts named in the
    evidence, disclosed there).
    A headword owning NO unique definition — every stored
    definition found verbatim inside other entries' lists — is
    not a base lemma in this store; it is a form entry whose
    attestation the extraction destroyed. (Direction is
    checked: an entry does not count as "covering" when the
    token is itself a rule-generated base of it — crush is the
    source of crushed's copied definitions, not a stub of
    crushed.) With no rule and no
    text naming its base: HOLD form_entry_without_attested_base,
    the covering entries named in evidence. (went: its five
    definitions are wend's two and go's three; the suppletive
    link went -> go is real English and is NOT attested by this
    text, so this module does not assert it. children likewise:
    definitions identical to child's, no rule or text attests
    the plural relation in this build.)
(d) Anything else -> HOLD unknown_word (no headword, no
    attested base: quadrupe, quadruped) or
    unattested_form.

form_family(words) -> {"families": {entry: {form: token}},
"held": [{"word", "hold_reason"}], "counts": {...}}: resolves
each word of a caller-supplied list ONCE and groups the results
by entry — traverse once, store. The returned dict is plain
data: the stored structure the write-in slice will later
persist as subcoordinates. This module writes nothing anywhere.

check_agreement(subject, verb_token) -> {"subject", "verb",
"verdict", "hold_reason"}. Subject person/number comes from
closed-class pronoun data (i/you/we/they take the base form;
he/she/it take the third-person form) or from the subject
noun's own resolve_form (plural noun -> base form; singular
noun -> third-person form; a base_or_plural subject accepts
either). The verb token is judged by its FORM SHAPE: a token
with an attested third_person reading (rule + resolved base)
carries the third-person form in the verb slot, a headword base
carries the base form — agreement reads the token's verb
reading; the plural reading of the same -s shape is a noun
reading and does not enter the verb slot. Mismatch -> HOLD
agreement_violation. (they, wolf) OK; (he, wolfs) OK;
(he, wolf) HOLD; (they, wolfs) HOLD.

Deterministic. Stdlib + pmca_dictionary only. The stub-coverage
search reads the same DB file read-only through
pmca_dictionary.db_path(). This module does not consult the
sentence analyzer's hand-built form table anywhere; the battery
proves it by scanning this file's own source.
"""

import re
import sqlite3

import pmca_dictionary
import pmca_verb_capable as _vc

# Closed-class pronoun agreement data (rules of language).
BASE_FORM_SUBJECTS = {"i", "you", "we", "they"}
THIRD_FORM_SUBJECTS = {"he", "she", "it"}

_ATTEST_RE = re.compile(
    r"\b(pl|imp|pret|p\. p|pres\. p)\.\s+of\s+\{?([A-Za-z]+)\}?")
_ATTEST_FORM = {"pl": "plural", "imp": "past", "pret": "past",
                "p. p": "past", "pres. p": "participle_ing"}
_SEE_RE = re.compile(r"See \{([A-Za-z]+)\}")
_LABEL_RE = re.compile(r"^\s*(\([^)]*\)\s*)+")
_SENSE_MARK_RE = re.compile(r"^\s*\([a-z]\)\s*")


def _exact(word):
    """The exact-headword entry for a word, or None.
    pmca_dictionary.lookup resolves suffix variants itself; the
    entry counts as exact only when the headword it reports IS
    the word asked for."""
    entry = pmca_dictionary.lookup(word)
    if entry is not None and entry["headword"] == word:
        return entry
    return None


def _sense_text(defn):
    """A definition with its leading GCIDE apparatus stripped:
    "(Zoöl.)" field labels and "(a)" sense markers are
    apparatus, not sense text. The ONE shared implementation
    (pmca_verb_capable.clean_sense_text, Candidacy Rule Set;
    WG2 2026-11-01 — this module's CLEANED copy is RETIRED as
    a mechanism and survives only as this delegation)."""
    return _vc.clean_sense_text(defn)


def _is_verb_sense(defn):
    """GCIDE convention: a verb sense is a definition beginning
    "To ", read after apparatus labels — the one shared
    decoding (pmca_verb_capable.is_verb_sense, CANDIDATE
    evidence under C-4)."""
    return _vc.is_verb_sense(defn)


def _sense_mix(entry):
    """(has_verb_senses, has_nonverb_senses) over stored defs."""
    has_v = any(_is_verb_sense(d) for d in entry["definitions"])
    has_n = any(not _is_verb_sense(d) for d in entry["definitions"])
    return has_v, has_n


def _see_stub_target(entry):
    """If the entry is a pure cross-reference stub (a single
    short definition whose content is "See {X}"), return X
    lowercased; else None."""
    defs = entry["definitions"]
    if len(defs) != 1:
        return None
    m = _SEE_RE.search(defs[0])
    if m and len(defs[0]) < 120:
        return m.group(1).lower()
    return None


def _candidates(token):
    """Rule-generated candidate bases: {base: {forms}}.
    Generation only — nothing here is accepted until the base
    attests as an exact headword. -s endings yield both the
    plural and third_person readings of the same base."""
    out = {}

    def add(base, form):
        if base and len(base) >= 2:
            out.setdefault(base, set()).add(form)

    t = token
    # Plural / third-person -s family.
    if t.endswith("ies") and len(t) > 3:
        add(t[:-3] + "y", "plural")
        add(t[:-3] + "y", "third_person")
    if t.endswith("ves") and len(t) > 3:
        add(t[:-3] + "f", "plural")
        add(t[:-3] + "fe", "plural")
    if t.endswith("es") and len(t) > 2:
        add(t[:-2], "plural")
        add(t[:-2], "third_person")
    if t.endswith("s") and not t.endswith("ss") and len(t) > 1:
        add(t[:-1], "plural")
        add(t[:-1], "third_person")
    # Past -ed family.
    if t.endswith("ied") and len(t) > 3:
        add(t[:-3] + "y", "past")
    if t.endswith("ed") and len(t) > 2:
        stem = t[:-2]
        add(stem, "past")
        add(stem + "e", "past")
        if len(stem) >= 2 and stem[-1] == stem[-2]:
            add(stem[:-1], "past")
    # Participle -ing family.
    if t.endswith("ing") and len(t) > 3:
        stem = t[:-3]
        add(stem, "participle_ing")
        add(stem + "e", "participle_ing")
        if len(stem) >= 2 and stem[-1] == stem[-2]:
            add(stem[:-1], "participle_ing")
    # Comparative -er / superlative -est families.
    if t.endswith("ier") and len(t) > 3:
        add(t[:-3] + "y", "comparative")
    if t.endswith("er") and len(t) > 2:
        stem = t[:-2]
        add(stem, "comparative")
        add(stem + "e", "comparative")
        if len(stem) >= 2 and stem[-1] == stem[-2]:
            add(stem[:-1], "comparative")
    if t.endswith("iest") and len(t) > 4:
        add(t[:-4] + "y", "superlative")
    if t.endswith("est") and len(t) > 3:
        stem = t[:-3]
        add(stem, "superlative")
        add(stem + "e", "superlative")
        if len(stem) >= 2 and stem[-1] == stem[-2]:
            add(stem[:-1], "superlative")
    # Adverbial -ly family.
    if t.endswith("ily") and len(t) > 3:
        add(t[:-3] + "y", "adverbial_ly")
    if t.endswith("ly") and len(t) > 2:
        add(t[:-2], "adverbial_ly")
    return out


def _covers(d, o):
    """Stored definition d is covered by another entry's
    definition o: equal, or contained either way with the
    contained text substantial (>= 30 chars) — short fragments
    shared across entries are not coverage."""
    if d == o:
        return True
    if len(d) >= 30 and d in o:
        return True
    if len(o) >= 30 and o in d:
        return True
    return False


def _covering_entries(token, defs):
    """For the stub check: the sorted names of OTHER entries
    whose stored definitions, taken together, contain every one
    of the token's definitions (verbatim or by containment).
    Bounded fragment search over the same DB file, read-only.
    Returns None when the check cannot run (tier off)."""
    path = pmca_dictionary.db_path()
    if path is None:
        return None
    try:
        con = sqlite3.connect("file:" + path + "?mode=ro",
                              uri=True)
    except sqlite3.Error:
        return None
    try:
        uncovered = list(defs)
        covering = []
        guard = 0
        while uncovered and guard < 8:
            guard += 1
            frag = uncovered[0][:40]
            cut = len(frag)
            for bad in ('"', "\\"):
                idx = frag.find(bad)
                if idx != -1:
                    cut = min(cut, idx)
            frag = frag[:cut]
            if len(frag) < 15:
                break
            rows = con.execute(
                "SELECT word, defs FROM defs WHERE instr(defs, ?) > 0"
                " ORDER BY word", (frag,)).fetchall()
            progressed = False
            import json as _json
            for word, raw in rows:
                if word == token:
                    continue
                try:
                    other = _json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(other, list):
                    continue
                hit = [d for d in uncovered
                       if any(_covers(d, o) for o in other)]
                if hit:
                    covering.append(word)
                    uncovered = [d for d in uncovered
                                 if d not in hit]
                    progressed = True
            if not progressed:
                break
        if uncovered:
            return []
        return sorted(set(covering))
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _result(token, verdict, hold_reason, entry, form, evidence):
    return {"token": token, "verdict": verdict,
            "hold_reason": hold_reason, "entry": entry,
            "form": form, "evidence": evidence}


def _hold(token, reason, evidence=""):
    return _result(token, "HOLD", reason, None, None, evidence)


def resolve_form(token):
    """Resolve one token to its entry and form. See the module
    docstring for the evidence discipline."""
    t = token.strip().lower()
    if not t:
        return _hold(token, "unknown_word")

    # Possessive: the shape the dictionary's own normalization
    # would silently eat, so it is read here, first. The base
    # must attest as an exact headword.
    if t.endswith("'s"):
        base = t[:-2]
        if _exact(base) is not None:
            return _result(t, "OK", None, base, "possessive",
                           "possessive 's on attested headword "
                           + base)
        return _hold(t, "unknown_word",
                     "possessive base unattested: " + base)
    if t.endswith("s'") and len(t) > 2:
        base = t[:-1]
        if _exact(base) is not None:
            return _result(t, "OK", None, base, "possessive",
                           "plural possessive on attested "
                           "headword " + base)
        return _hold(t, "unknown_word",
                     "possessive base unattested: " + base)

    own = _exact(t)

    # (a) Text attestation from the token's own stored text.
    if own is not None:
        stub = _see_stub_target(own)
        if stub is not None and _exact(stub) is not None:
            return _result(t, "OK", None, stub, "base",
                           "cross-reference stub in stored text: "
                           + own["definitions"][0][:80])
        for d in own["definitions"]:
            m = _ATTEST_RE.search(d)
            if m:
                base = m.group(2).lower()
                if base != t and _exact(base) is not None:
                    return _result(
                        t, "OK", None, base, _ATTEST_FORM[m.group(1)],
                        "text attestation in stored definition: "
                        + d[max(0, m.start() - 10):m.end() + 10])

    # (c) Rule-generated candidates, dictionary-attested.
    cands = {}
    for base, forms in _candidates(t).items():
        entry = _exact(base)
        if entry is None:
            continue
        # Follow cross-reference stubs through to their targets:
        # a stub is not an independent base.
        target = _see_stub_target(entry)
        if target is not None and _exact(target) is not None:
            base = target
        cands.setdefault(base, set()).update(forms)

    if own is None:
        # The token is not a headword: the rule reading is the
        # only possible one.
        if len(cands) > 1:
            return _hold(t, "ambiguous_form",
                         "bases both attested: "
                         + ", ".join(sorted(cands)))
        if len(cands) == 1:
            base, forms = next(iter(cands.items()))
            return _accept_rule(t, base, forms, _exact(base),
                                False)
        return _hold(t, "unknown_word")

    # The token IS a headword. Arbitrate between the rule
    # reading and the headword reading on store facts only.
    single = len(cands) == 1
    base1 = next(iter(cands)) if single else None
    identical = (single and own["definitions"]
                 == _exact(base1)["definitions"])
    adv_self_tag = any(", adv." in d for d in own["definitions"])
    if single and cands[base1] == {"adverbial_ly"} and adv_self_tag:
        # The entry declares itself an adverb (quickly): the
        # derived reading wins over the headword reading.
        return _accept_rule(t, base1, cands[base1],
                            _exact(base1), False)
    if single and identical:
        # The headword is this build's copy of the base entry
        # (wolves, crushed, bigger): the form reading wins.
        return _accept_rule(t, base1, cands[base1],
                            _exact(base1), True)
    # Does the headword own definitions of its own? A covering
    # entry does not count when the token is itself a
    # rule-generated base OF that entry — direction matters:
    # crush is the source of crushed's copied definitions, not
    # a stub of crushed.
    covering = _covering_entries(t, own["definitions"])
    if covering:
        covering = [w for w in covering
                    if t not in _candidates(w)]
        # Re-verify: the surviving entries must jointly cover
        # every definition, else the headword owns unique
        # definitions after all (butter: its past-form copy
        # entry is direction-filtered out, and what remains
        # covers only a fragment).
        if covering:
            pool = []
            for w in covering:
                e = _exact(w)
                if e is not None:
                    pool.extend(e["definitions"])
            if not all(any(_covers(d, o) for o in pool)
                       for d in own["definitions"]):
                covering = []
    if covering is not None and not covering:
        # Owns unique definitions: the headword stands as its
        # own base (news, butter, friendly).
        has_pl_sense = any(_sense_text(d).startswith("pl.")
                           for d in own["definitions"])
        if has_pl_sense and _exact(t + "s") is None:
            frag = next(d for d in own["definitions"]
                        if _sense_text(d).startswith("pl."))
            return _result(
                t, "OK", None, t, "base_or_plural",
                "headword serves both numbers: stored entry "
                "carries a plural-marked sense (" + frag[:40]
                + "...) and no " + t + "s headword exists")
        return _result(t, "OK", None, t, "base",
                       "exact headword with its own definitions")
    if len(cands) > 1:
        return _hold(t, "ambiguous_form",
                     "bases both attested: "
                     + ", ".join(sorted(cands)))
    if covering:
        return _hold(
            t, "form_entry_without_attested_base",
            "headword owns no unique definition; every stored "
            "definition is found in: " + ", ".join(covering)
            + "; no rule or text attests its base")

    # (d) A headword fully covered by other entries, with a
    # single rule candidate that is not its covering entry:
    # nothing attests the relation.
    return _hold(t, "form_entry_without_attested_base",
                 "headword owns no unique definition; no rule "
                 "or text attests its base")


def _accept_rule(token, base, forms, base_entry, identical):
    """Accept the single rule-generated base. The -s family is
    two-way: the standalone reading follows the base entry's
    attested senses."""
    if forms == {"plural", "third_person"}:
        has_v, has_n = _sense_mix(base_entry)
        if has_v and not has_n:
            form = "third_person"
        elif has_n and not has_v:
            form = "plural"
        else:
            return _hold(token, "ambiguous_form",
                         "base '" + base + "' attests both verb "
                         "and non-verb senses; the -s reading "
                         "(plural / third_person) is undecidable "
                         "standalone")
        ev = ("rule -s; base '" + base + "' attested as headword"
              " with " + ("verb senses only" if form ==
                          "third_person" else "no verb senses"))
    else:
        form = sorted(forms)[0]
        ev = ("rule form " + form + "; base '" + base
              + "' attested as headword")
    if identical:
        ev += ("; stored definitions identical to the base "
               "entry's (this build's extraction copy)")
    return _result(token, "OK", None, base, form, ev)


def _readings(token):
    """All attested (entry, form) readings of a token, for
    agreement: the resolve_form result when it resolves, plus
    BOTH -s readings when a base attests (the verb slot reads
    the third_person shape; the plural is its noun reading)."""
    out = []
    r = resolve_form(token)
    if r["verdict"] == "OK":
        out.append((r["entry"], r["form"]))
    t = token.strip().lower()
    for base, forms in _candidates(t).items():
        if _exact(base) is None:
            continue
        for f in sorted(forms):
            if (base, f) not in out:
                out.append((base, f))
    if _exact(t) is not None and (t, "base") not in out:
        out.append((t, "base"))
    return out


def check_agreement(subject, verb_token):
    """Person/number agreement between a subject and a verb
    token. See the module docstring."""
    s = subject.strip().lower()
    v = verb_token.strip().lower()

    if s in BASE_FORM_SUBJECTS:
        need = "base"
    elif s in THIRD_FORM_SUBJECTS:
        need = "third_person"
    else:
        r = resolve_form(s)
        if r["verdict"] != "OK":
            return {"subject": subject, "verb": verb_token,
                    "verdict": "HOLD",
                    "hold_reason": "unknown_subject:" + s}
        if r["form"] == "plural":
            need = "base"
        elif r["form"] == "base_or_plural":
            need = "either"
        else:
            need = "third_person"

    forms = {f for (_e, f) in _readings(v)}
    present = forms & {"base", "third_person"}
    if not present:
        return {"subject": subject, "verb": verb_token,
                "verdict": "HOLD",
                "hold_reason": "verb_form_out_of_scope:" + v}
    if need == "either" or need in present:
        return {"subject": subject, "verb": verb_token,
                "verdict": "OK", "hold_reason": None}
    return {"subject": subject, "verb": verb_token,
            "verdict": "HOLD", "hold_reason": "agreement_violation"}


def form_family(words):
    """Resolve each word once and group by entry. Returns plain
    data: {"families": {entry: {form: token}}, "held":
    [{"word", "hold_reason"}], "counts": {...}}."""
    families = {}
    held = []
    for w in words:
        r = resolve_form(w)
        if r["verdict"] == "OK":
            fam = families.setdefault(r["entry"], {})
            fam[r["form"]] = r["token"]
        else:
            held.append({"word": r["token"],
                         "hold_reason": r["hold_reason"]})
    counts = {"resolved": sum(len(f) for f in families.values()),
              "held": len(held), "entries": len(families)}
    return {"families": families, "held": held, "counts": counts}


# ---------------------------------------------------------------------
# Check battery. Run: python3 pmca_morphology_finder.py
# Every battery case from the build brief is asserted against the
# real dictionary this tree carries. Where the real dictionary
# cannot support a case as specified, the check asserts the
# MEASURED outcome and its detail says so in the open.
# ---------------------------------------------------------------------

def run_checks():
    import json
    import os

    results = []

    def check(name, cond, detail=""):
        results.append(cond)
        print(("PASS" if cond else "FAIL") + ": " + name
              + (" -> " + detail if detail else ""))

    # The dictionary tier is off until configured (Pack G,
    # 2026-10-26); this battery runs against the tree's stored
    # Webster 1913 DB, configured explicitly.
    pmca_dictionary.configure(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "webster1913.db"))

    # No analyzer consultation anywhere in this module: the
    # hand-built form table this build replaces is absent from
    # this file's own source, and so is the analyzer import.
    src = open(os.path.abspath(__file__)).read()
    check("no_analyzer_form_table_anywhere",
          "VERB" + "_FORMS" not in src
          and "pmca_sentence" + "_analyzer" not in src,
          "source scan clean")

    def rf(tok):
        r = resolve_form(tok)
        return (r["verdict"], r["entry"], r["form"],
                r["hold_reason"])

    # 1. wolves -> wolf, plural. wolves is its own headword in
    # this build; the -ves rule proposes wolf, the dictionary
    # attests it, and the stored definitions are identical to
    # wolf's (the extraction's copy), so the form reading wins.
    r = resolve_form("wolves")
    check("wolves_plural_of_wolf",
          rf("wolves") == ("OK", "wolf", "plural", None),
          str(rf("wolves")) + " | " + r["evidence"])

    # 2. crushed -> crush, past, with NO analyzer form table
    # consulted (proven by the source scan above): rule -ed,
    # base crush attested, stored definitions identical.
    check("crushed_past_of_crush",
          rf("crushed") == ("OK", "crush", "past", None),
          str(rf("crushed")))

    # 3. Third-person -s on verb-only bases.
    check("jumps_third_person",
          rf("jumps") == ("OK", "jump", "third_person", None),
          str(rf("jumps")))
    check("breaks_third_person",
          rf("breaks") == ("OK", "break", "third_person", None),
          str(rf("breaks")))

    # 4. Comparative / superlative with consonant doubling.
    check("bigger_comparative",
          rf("bigger") == ("OK", "big", "comparative", None),
          str(rf("bigger")))
    check("biggest_superlative",
          rf("biggest") == ("OK", "big", "superlative", None),
          str(rf("biggest")))

    # 5. Adverbial -ly; quickly's own entry tags itself "adv."
    check("quickly_adverbial_ly",
          rf("quickly") == ("OK", "quick", "adverbial_ly", None),
          str(rf("quickly")))

    # 6. sheep -> sheep, base_or_plural, on store facts: the
    # entry carries a plural-marked sense ("pl. Fig.: ...") and
    # no "sheeps" headword exists. The classic "sing. & pl."
    # header marker is NOT in this build's stored text; the
    # both-numbers reading rests on those two facts, disclosed
    # here and in the result's evidence.
    r = resolve_form("sheep")
    check("sheep_base_or_plural",
          rf("sheep") == ("OK", "sheep", "base_or_plural", None),
          str(rf("sheep")) + " | " + r["evidence"])

    # 7. quadrupedal is its own headword and resolves as a base;
    # its family link to quadruped is UNATTESTED in this build:
    # quadruped has no entry, and this module does not bridge
    # the gap (the old stemmer's failure, inverted).
    check("quadrupedal_own_headword",
          rf("quadrupedal") == ("OK", "quadrupedal", "base", None),
          str(rf("quadrupedal")))
    check("quadruped_absent_holds",
          rf("quadruped")[0] == "HOLD"
          and rf("quadruped")[3] == "unknown_word",
          str(rf("quadruped")))

    # 8. quadrupe -> HOLD: the stemmer's invention, attested
    # nowhere. The anti-guessing proof case.
    check("quadrupe_holds_unattested",
          rf("quadrupe")[0] == "HOLD"
          and rf("quadrupe")[3] == "unknown_word",
          str(rf("quadrupe")))

    # 9. went -> go: NOT attested by this text. The stored went
    # entry owns no unique definition (its five definitions are
    # wend's two and go's three, verbatim) and no rule generates
    # go from went, so the module HOLDs and names the covering
    # entries instead of asserting the suppletive link.
    r = resolve_form("went")
    check("went_suppletion_honest_hold",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "form_entry_without_attested_base"
          and "go" in r["evidence"] and "wend" in r["evidence"],
          str((r["verdict"], r["hold_reason"])) + " | "
          + r["evidence"])

    # 10. children: same species — definitions identical to
    # child's, but no rule and no text in this build attests the
    # plural relation, so it HOLDs rather than asserting it.
    r = resolve_form("children")
    check("children_form_entry_holds",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "form_entry_without_attested_base",
          str((r["verdict"], r["hold_reason"])) + " | "
          + r["evidence"])

    # 11. axes -> HOLD ambiguous_form: two different bases (ax,
    # axe) both resolve. Standalone morphology cannot choose.
    r = resolve_form("axes")
    check("axes_ambiguous_form",
          r["verdict"] == "HOLD"
          and r["hold_reason"] == "ambiguous_form"
          and "ax" in r["evidence"] and "axe" in r["evidence"],
          str((r["verdict"], r["hold_reason"])) + " | "
          + r["evidence"])

    # 12. Possessive and participle forms.
    check("wolfs_possessive",
          rf("wolf's") == ("OK", "wolf", "possessive", None),
          str(rf("wolf's")))
    check("crushing_participle_ing",
          rf("crushing") == ("OK", "crush", "participle_ing", None),
          str(rf("crushing")))

    # 13. Headword arbitration guards: -s/-er candidates exist
    # for these too, but their stored definitions are their own,
    # so they stand as bases (news is not new+s; butter is not
    # butt+er).
    check("news_stands_as_base",
          rf("news") == ("OK", "news", "base", None),
          str(rf("news")))
    check("butter_stands_as_base",
          rf("butter") == ("OK", "butter", "base", None),
          str(rf("butter")))

    # 14. Agreement battery (the brief's four pairs).
    def ag(s, v):
        r = check_agreement(s, v)
        return (r["verdict"], r["hold_reason"])

    check("agree_they_wolf_ok",
          ag("they", "wolf") == ("OK", None), str(ag("they", "wolf")))
    check("agree_he_wolfs_ok",
          ag("he", "wolfs") == ("OK", None), str(ag("he", "wolfs")))
    check("agree_he_wolf_violation",
          ag("he", "wolf") == ("HOLD", "agreement_violation"),
          str(ag("he", "wolf")))
    check("agree_they_wolfs_violation",
          ag("they", "wolfs") == ("HOLD", "agreement_violation"),
          str(ag("they", "wolfs")))
    # Noun subjects: number comes from the subject's own form.
    check("agree_wolves_hunt_ok",
          ag("wolves", "hunt") == ("OK", None),
          str(ag("wolves", "hunt")))
    check("agree_wolf_hunts_ok",
          ag("wolf", "hunts") == ("OK", None),
          str(ag("wolf", "hunts")))
    check("agree_wolf_hunt_violation",
          ag("wolf", "hunt") == ("HOLD", "agreement_violation"),
          str(ag("wolf", "hunt")))
    check("agree_sheep_hunts_ok_either_number",
          ag("sheep", "hunts") == ("OK", None),
          str(ag("sheep", "hunts")))

    # 15. form_family over the battery vocabulary: traverse
    # once, group by entry; HOLDs excluded and counted.
    vocab = ["wolf", "wolves", "crush", "crushed", "crushing",
             "jumps", "breaks", "bigger", "biggest", "quickly",
             "sheep", "quadrupedal", "quadruped", "went"]
    fam = form_family(vocab)
    check("family_wolf_has_wolves",
          fam["families"].get("wolf", {}).get("plural") == "wolves",
          json.dumps(fam["families"].get("wolf"), sort_keys=True))
    check("family_crush_has_crushed",
          fam["families"].get("crush", {}).get("past") == "crushed"
          and fam["families"].get("crush", {}).get(
              "participle_ing") == "crushing",
          json.dumps(fam["families"].get("crush"), sort_keys=True))
    check("family_held_counted",
          fam["counts"]["held"] == 2
          and {h["word"] for h in fam["held"]}
          == {"quadruped", "went"}
          and "quadruped" not in fam["families"],
          json.dumps(fam["counts"], sort_keys=True) + " held="
          + json.dumps(fam["held"], sort_keys=True))

    # Determinism inside the process: the whole battery re-run
    # must produce identical result objects.
    toks = ["wolves", "crushed", "jumps", "breaks", "bigger",
            "biggest", "quickly", "sheep", "quadrupedal",
            "quadruped", "quadrupe", "went", "children", "axes",
            "wolf's", "crushing", "news", "butter"]
    dump1 = json.dumps([resolve_form(t) for t in toks],
                       sort_keys=True)
    dump2 = json.dumps([resolve_form(t) for t in toks],
                       sort_keys=True)
    fam1 = json.dumps(form_family(vocab), sort_keys=True)
    fam2 = json.dumps(form_family(vocab), sort_keys=True)
    check("determinism_two_runs_identical",
          dump1 == dump2 and fam1 == fam2, dump1[:60])

    passed = sum(1 for x in results if x)
    print("%d/%d checks passed" % (passed, len(results)))
    print("Determinism (two runs identical): %s"
          % (dump1 == dump2 and fam1 == fam2))
    return passed == len(results)


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_checks() else 1)
