"""pmca_verb_capable — the ONE shared verb_capable / noun_capable
implementation under the Candidacy Rule Set (C-1..C-10).

Authority: DICT-1 Part 1, RULED 2026-10-31 BY ALDER UNDER
DELEGATION, by reference under the Candidacy Rule Set (docket
line 280): "One shared verb_capable, implemented under the set
(C-4 POS tiering, C-5 seed exemption). Both "To "-test families
(RAW and CLEANED), Variant C (category-derive token test),
Variant D (nominal-openings test), and the seed-table oracle
OR'd into _guard_verb_capable are RETIRED as mechanisms; their
stored outputs stand as facts with their provenance until
re-derived under the set."

Built 2026-11-01, WG2 (04 Forge). Every consumer in the tree
— pmca_dictionary._form, pmca_parse_guards.dict_verb_capable /
dict_noun_capable, all nine finders, pmca_morphology_finder's
sense mix, pmca_dictionary_loop, pmca_population,
pmca_category_derive (v5), the analyzer's guard wrapper — reads
THIS module. No other in-tree verdict path may test a stored
definition for verb-form itself.

What the rule set requires of the answer (C-1): every
candidacy answer is (tier, value, provenance classes, gap
record); tier in {attested, CANDIDATE, UNKNOWN}; UNKNOWN or
conflicted answers always carry a gap record (no silent gaps).

POS tiering (C-4): "structural POS governs where attested by
any lineage; the cleaned Webster-convention decoding is
CANDIDATE evidence only, never promoted by the mechanism
itself." In this tree no structural-POS store is wired into
the runtime for dictionary words (OEWN is reached only by the
offline hold-checker / is_a builder, which are not verdict
paths), so a dictionary entry's POS answer is CANDIDATE tier,
its evidence the cleaned decoding below, its provenance the
Webster lineage read through the governed table (C-3, via
pmca_lineage_table). A word with no entry is UNKNOWN, value
None, with a gap record. The mechanism never promotes its own
CANDIDATE evidence: promotion to attested requires >= 2
distinct provenance CLASSES (C-2) and, for POS/forms, is
BLOCKED while lineage independence is UNMEASURED (C-6 /
DICT-1 Part 3) — see lineage_independence() /
promotion_blocked() below, which answer UNMEASURED / True on
today's stores by design.

The cleaned decoding (the single surviving copy): a stored
definition is a verb sense iff, after stripping leading
"(...)" apparatus labels and "(a)" sense markers, it begins
"To " (the GCIDE infinitive convention). This decoding is
EVIDENCE at CANDIDATE tier; the retired RAW family tested the
unstripped string, and the retired CLEANED family carried
three independent copies of the stripped test. Both are
retired AS MECHANISMS (independent verdict-grade oracles);
the decoding survives once, here, at the tier C-4 assigns it.

Boolean projection: verb_capable(word) / noun_capable(word)
return the candidacy VALUE at attested or CANDIDATE tier
(False at UNKNOWN). Finder/analyzer callers select candidate
subcoordinates with it; any caller that needs the tier reads
pos_answer(). Seed tables are EXEMPT (C-5): this module never
consults them, and the retired seed-oracle OR is not
reinstated here — seed identity is decided by the seed
tables' own consumers, and verb FORM identity by
pmca_forms_resolver under Part 2's armour.

Per-field attestation (C-4, WG2 item 3): an attestation
record carries a "field" key ("POS", "forms", "genus", ...).
classes_for_field() counts provenance classes over records
of ONE field only: POS evidence never counts for genus and
genus evidence never counts for POS, by construction.

Deterministic. Stdlib only. pmca_dictionary is imported
lazily inside the word-level functions (pmca_dictionary
imports this module for _form; no import cycle).
"""

import re

import pmca_lineage_table

_LABEL_RE = re.compile(r"^\s*(\([^)]*\)\s*)+")
_SENSE_MARK_RE = re.compile(r"^\s*\([a-z]\)\s*")

#: The one provenance the dictionary tier can offer today.
_DICTIONARY_PROVENANCE = {"source": "Webster 1913",
                          "lineage": "Webster",
                          "evidence": "cleaned_webster_convention"}


# ---------------------------------------------------------------------
# The cleaned decoding — single copy (CANDIDATE evidence, C-4).
# ---------------------------------------------------------------------

def clean_sense_text(defn):
    """A definition with its leading GCIDE apparatus stripped:
    "(...)" field labels and "(a)" sense markers are apparatus,
    not sense text."""
    text = _LABEL_RE.sub("", defn)
    text = _SENSE_MARK_RE.sub("", text)
    return text


def is_verb_sense(defn):
    """True iff this stored definition is a verb sense under
    the cleaned Webster-convention decoding: its cleaned text
    begins "To "."""
    return clean_sense_text(defn).startswith("To ")


def sense_mix(definitions):
    """(has_verb_senses, has_nonverb_senses) over stored
    definitions, by the cleaned decoding."""
    has_v = any(is_verb_sense(d) for d in definitions)
    has_n = any(not is_verb_sense(d) for d in definitions)
    return has_v, has_n


def form_of(definitions):
    """"verb" iff any definition is a verb sense under the
    cleaned decoding, else "other". This is the value
    pmca_dictionary.lookup reports as "form"; its tier is
    CANDIDATE (see pos_answer), never attested."""
    return "verb" if any(is_verb_sense(d) for d in definitions) \
        else "other"


# ---------------------------------------------------------------------
# Per-field attestation shape (C-4) and the Part 3 block (C-6).
# ---------------------------------------------------------------------

def attestation(field, value, provenance, tier):
    """One attestation record in the shared shape. `field`
    names the field the evidence is FOR; class counting is
    per-field (classes_for_field), so evidence recorded for
    one field can never promote another."""
    return {"field": field, "value": value,
            "provenance": dict(provenance), "tier": tier}


def classes_for_field(records, field):
    """The distinct provenance CLASSES over attestation
    records of exactly `field`, read through the governed
    source->lineage table (C-3). Records of any other field
    contribute nothing — POS evidence never counts for genus
    and genus evidence never counts for POS."""
    provs = [r.get("provenance") for r in records or []
             if isinstance(r, dict) and r.get("field") == field
             and isinstance(r.get("provenance"), dict)]
    return pmca_lineage_table.provenance_classes(
        provs, context="pmca_verb_capable.classes_for_field")


def lineage_independence(field="POS"):
    """DICT-1 Part 3 (RULED 2026-10-31 BY ALDER UNDER
    DELEGATION): the sealed lineage-independence measurement
    for POS/forms is permitted — and on current stores
    expected — to BE the negative result. No witness with real
    cross-lineage coverage exists in today's stores (POS
    cross-lineage overlap n=3 in the verification store; ECDICT
    POS unmeasurable in principle; OEWN forms never recorded),
    so the mechanism answers UNMEASURED for POS and forms, and
    the honest answer for any other field it has no measurement
    for. It never invents a measured value."""
    return "UNMEASURED"


def promotion_blocked(field="POS"):
    """C-6: while POS/forms lineage independence is UNMEASURED,
    lineage-counted promotion in POS/forms is disabled. True
    for POS and forms on today's stores."""
    if field in ("POS", "forms"):
        return lineage_independence(field) == "UNMEASURED"
    return False


# ---------------------------------------------------------------------
# The candidacy answer (C-1) and its boolean projection.
# ---------------------------------------------------------------------

def pos_answer(word):
    """The C-1 candidacy answer for one word's POS capability:
    {"word", "tier", "value", "provenance_classes",
     "attestations", "gap"} where value is
    {"verb_capable": bool, "noun_capable": bool} at CANDIDATE
    tier, or None at UNKNOWN tier (no dictionary entry — the
    gap record names why). Tier "attested" is never returned
    by this mechanism for POS: structural POS governs where
    attested by any lineage (none is wired here), the cleaned
    decoding is CANDIDATE only (C-4), and lineage-counted
    promotion is blocked while independence is UNMEASURED
    (C-6)."""
    import pmca_dictionary
    w = (word or "").strip().lower()
    entry = pmca_dictionary.lookup(w) if w else None
    if entry is None:
        reason = ("dictionary_tier_off"
                  if pmca_dictionary.db_path() is None
                  else "no_headword")
        return {"word": w, "tier": "UNKNOWN", "value": None,
                "provenance_classes": [],
                "attestations": [],
                "gap": {"word": w, "field": "POS",
                        "reason": reason}}
    has_v, has_n = sense_mix(entry["definitions"])
    value = {"verb_capable": has_v, "noun_capable": has_n}
    atts = [attestation("POS", value, _DICTIONARY_PROVENANCE,
                        "CANDIDATE")]
    classes = sorted(classes_for_field(atts, "POS"))
    return {"word": w, "tier": "CANDIDATE", "value": value,
            "provenance_classes": classes,
            "attestations": atts, "gap": None}


def verb_capable(word):
    """Boolean projection of pos_answer: the word carries a
    verb candidate (attested or CANDIDATE tier). This is the
    one verb_capable every consumer in the tree calls."""
    ans = pos_answer(word)
    return bool(ans["value"] and ans["value"]["verb_capable"])


def noun_capable(word):
    """Boolean projection of pos_answer: the word carries a
    noun candidate (a sense that is not a verb-form sense)."""
    ans = pos_answer(word)
    return bool(ans["value"] and ans["value"]["noun_capable"])
