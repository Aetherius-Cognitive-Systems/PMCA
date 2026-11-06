"""pmca_forms_resolver — the DICT-1 Part 2 forms resolver.

Authority: DICT-1 Part 2, RULED 2026-10-31 (Jonathan: "B it
is"; docket line 273): Option (B) — the forms resolver FEEDS
seed-verb identity, ruled openly as a verdict-identity
doctrine: "an attested form of a verb is that verb, for
verdicts." Built 2026-11-01, WG2 (04 Forge).

ARMOUR carried in the ruling, implemented here verbatim in
structure:
  (1) Per-form attestation required before a form carries
      verdict weight: a token resolves to a seed verb only
      when its form-of relation to that verb's lemma is
      ATTESTED — by the seed table itself (the token IS a
      seed key), by the stored form table (VERB_FORMS), or
      by the morphology rule table generating the lemma as a
      candidate base AND the lemma attesting as an exact
      dictionary headword (the morphology microscript's own
      generation discipline: rules GENERATE candidate bases
      only; the dictionary DISPOSES). Every resolution
      returns its attestation record naming the form, the
      lemma, and the evidence.
  (2) Arbitration of ambiguous forms is by sentence context
      (class-by-company), never by force: the caller states
      the token's context ("verb_slot", "noun_slot", or
      "standalone"). A -s token whose one lemma offers both
      the plural (noun) and third_person (verb) readings is
      arbitrated by that context; standalone it HOLDs
      ambiguous_form, exactly as the morphology microscript
      holds it standalone.
  (3) Resolver HOLDs remain HOLDs: a token whose attested
      readings name MORE THAN ONE lemma (leaves -> leaf /
      leave) HOLDs in every context the readings cannot
      separate, and no caller may force it. Callers in the
      verdict path treat a HOLD resolution as "no seed-verb
      identity from the resolver" and record/keep their own
      HOLD — the resolver never manufactures an identity.

Seed identity shape: the seed verb table's keys are surface
forms (breaks, crushes, rolled, took, ...). The resolver's
canonical output is the seed KEY whose lemma the token is an
attested form of. Lemma derivation is deterministic from the
same morphology rule table: the lemma of a seed key is the
single rule-generated base attested as a headword (crushes ->
crush, rolled -> roll), the VERB_FORMS target's lemma for a
table form, or the key itself when no rule attests a distinct
base (took -> took; no stored text in this build attests the
suppletive took -> take link, so none is asserted — the same
honesty the morphology microscript records for went -> go).

Deterministic. Stdlib + pmca_dictionary +
pmca_morphology_finder's candidate RULE TABLE only (its
_candidates generation; the resolver does not call
resolve_form, whose standalone verdict is a different
question from the in-sentence one the armour assigns here).
"""

import pmca_dictionary
import pmca_morphology_finder as _morph

_VERB_FORMS_OF = ("base", "third_person", "past",
                  "participle_ing")


def _exact_headword(word):
    entry = pmca_dictionary.lookup(word)
    return entry is not None and entry["headword"] == word


def light_readings(token):
    """All attested (lemma, form) readings of a token, cheaply:
    the token itself as base when it is an exact headword, plus
    every morphology-rule candidate base that attests as an
    exact headword. Mirrors pmca_morphology_finder._readings'
    generation set without its standalone arbitration."""
    t = (token or "").strip().lower()
    out = []
    if not t:
        return out
    if _exact_headword(t):
        out.append((t, "base"))
    for base, forms in _morph._candidates(t).items():
        if not _exact_headword(base):
            continue
        for f in sorted(forms):
            if (base, f) not in out:
                out.append((base, f))
    return out


def lemma_of(seed_key, verb_forms):
    """The lemma a seed key stands for: the VERB_FORMS target's
    rule base when the key is itself a table form's target is
    not needed — derivation is direct: a rule-generated base
    attested as a headword, preferring a verb-form reading;
    the key itself when no distinct base attests."""
    readings = [(b, f) for (b, f) in light_readings(seed_key)
                if b != seed_key]
    verb_bases = sorted({b for (b, f) in readings
                         if f in _VERB_FORMS_OF})
    if len(verb_bases) == 1:
        return verb_bases[0]
    if len(verb_bases) > 1:
        return seed_key
    bases = sorted({b for (b, f) in readings})
    return bases[0] if len(bases) == 1 else seed_key


def seed_lemma_map(seed_verbs, verb_forms):
    """{lemma: seed_key} over the seed verb table."""
    out = {}
    for key in seed_verbs:
        out.setdefault(lemma_of(key, verb_forms), key)
    return out


def resolve(token, seed_verbs, verb_forms, context="verb_slot",
            lemmas=None):
    """Resolve one token to a seed-verb identity under the
    armour. Returns {"token", "verdict", "canonical", "lemma",
    "form", "attestation", "hold_reason"} with verdict in
    {"OK", "HOLD", "NONE", "NOUN"} ("NOUN" only from the
    noun_slot arbitration: the plural reading is selected and
    the token carries no verb identity in that context):
      OK   — attested form of exactly one seed verb;
             `canonical` is that verb's seed key.
      HOLD — attested readings exist but arbitration cannot
             settle them (multi-lemma, or standalone -s
             ambiguity); canonical is None and MUST stay None
             in the verdict path.
      NONE — no attested reading ties the token to any seed
             verb (not a resolver matter; callers proceed with
             their other tiers exactly as before).
    """
    t = (token or "").strip().lower()

    def hold(reason):
        return {"token": t, "verdict": "HOLD", "canonical": None,
                "lemma": None, "form": None, "attestation": None,
                "hold_reason": reason}

    def none():
        return {"token": t, "verdict": "NONE", "canonical": None,
                "lemma": None, "form": None, "attestation": None,
                "hold_reason": None}

    def ok(canonical, lemma, form, evidence):
        return {"token": t, "verdict": "OK",
                "canonical": canonical, "lemma": lemma,
                "form": form,
                "attestation": {
                    "field": "forms", "token": t,
                    "lemma": lemma, "form": form,
                    "canonical": canonical,
                    "provenance": {"source": "morphology",
                                   "evidence": evidence},
                    "tier": "attested"},
                "hold_reason": None}

    if not t:
        return none()
    # The seed table itself attests its own keys (C-5 exempt).
    if t in seed_verbs:
        return ok(t, lemma_of(t, verb_forms), "seed_key",
                  "token is a seed verb key (seed table, C-5 "
                  "exempt)")
    # The stored form table attests its entries.
    if t in verb_forms and verb_forms[t] in seed_verbs:
        canon = verb_forms[t]
        return ok(canon, lemma_of(canon, verb_forms),
                  "stored_form_table",
                  "stored form table: " + t + " -> " + canon)
    readings = light_readings(t)
    if lemmas is None:
        lemmas = seed_lemma_map(seed_verbs, verb_forms)
    # Attested readings tied to a seed lemma.
    tied = [(b, f) for (b, f) in readings if b in lemmas]
    if t in lemmas:
        # The token IS a seed verb's lemma (its base form).
        tied.append((t, "base"))
    if not tied:
        return none()
    tied_bases = sorted({b for (b, _f) in tied})
    if len(tied_bases) > 1:
        return hold("ambiguous_form:bases both attested: "
                    + ", ".join(tied_bases))
    base = tied_bases[0]
    forms = sorted({f for (b, f) in tied if b == base})
    verb_readings = [f for f in forms if f in _VERB_FORMS_OF]
    if set(forms) == {"plural", "third_person"} or \
            set(forms) == {"plural"} and "third_person" not in forms:
        # The -s two-way shape (or a plural-only reading).
        if context == "verb_slot" and "third_person" in forms:
            return ok(lemmas[base], base, "third_person",
                      "sentence-context arbitration (verb slot) "
                      "selects the third_person reading of "
                      "attested lemma " + base)
        if context == "noun_slot":
            return {"token": t, "verdict": "NOUN",
                    "canonical": None, "lemma": base,
                    "form": "plural", "attestation": None,
                    "hold_reason": None}
        return hold("ambiguous_form:plural/third_person "
                    "undecidable standalone")
    if len(verb_readings) == 1:
        form = verb_readings[0]
        return ok(lemmas[base], base, form,
                  "morphology rule form " + form + "; lemma '"
                  + base + "' attested as exact headword")
    if not verb_readings:
        return none()
    return hold("ambiguous_form:" + ", ".join(forms))
