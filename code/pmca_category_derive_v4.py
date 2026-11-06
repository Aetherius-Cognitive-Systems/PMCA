"""pmca_category_derive.py — SHA-256 category keys with membership
derived from THE DICTIONARY (Webster 1913), not from the seed's
category field.

Direction: Jonathan Wayne Fleuren, 2026-10-20, correcting the
pmca_category_keys proof — category membership comes from the
dictionary. A word's definition names its kind (the genus); that
kind's definition names a wider kind; the chain decides membership.
"This key holds all animals" = every word whose definition chain
reaches "animal". Built by Alder's hive in the DUPLICATE tree
(pmca_argon_test) only, per Jonathan's build method.

Derivation (deterministic; no heuristics beyond the stated rule —
whatever the rule yields is the data, good or bad):

  genus(definition): from ONE definition string, first CLEAN
  it: repeatedly strip a leading "(...)" group plus following
  whitespace while the text starts with "(" (field labels like
  "(Zoöl.)", sequences like "(Naut.) (a)"), then take the text
  up to the first ";" (first sense chunk). Lowercase-tokenize
  on non-letters. A first token of "to" is a verb form -> None.
  Skip ONE leading article/quantifier in {"a","an","the","any",
  "one"}; then the v3 quantifier-gloss skip: if the next two
  tokens are "one","of" (plus an optional following "the"),
  skip them as a unit, and if the first remaining token is
  then "of", skip it too, plus an optional following "the".
  Collect the noun-phrase window: at most the next 6 tokens,
  stopping before the first boundary token in {"of","with",
  "which","who","that","in","on","from","by","for",
  "resembling","like"} or before a "(" in the CLEANED text;
  the phrase ALSO closes at the end of the window or of the
  chunk (v3 change 2). V4 change (4) adds one more cut,
  here and in the scaffold of-phrase collection alike: the
  collection stops BEFORE the first verb-form-only token —
  a token that resolves in the dictionary and whose
  definitions, every one, open with the token "to" after
  the same label cleaning (the exact complement of the
  noun test's per-definition read). A noun phrase ends
  where verb structure begins. V4 change (5) sits before
  all of this: a definition whose RAW text carries a
  backslash within its first 30 characters is
  pronunciation apparatus, not a definition, and yields
  None, so the walker's first-resolvable preference moves
  to the next definition. The genus is the LAST NOUN-CAPABLE
  token in the window: a token is noun-capable iff it
  resolves in the dictionary AND at least one of its
  definitions, after the same label cleaning, does not open
  with the token "to". An empty window, or no noun-capable
  window token, -> None. V3 scaffold re-pick (change 3): when
  the picked genus is a scaffold word {"species","kind",
  "sort","type","class","order","family","genus","group",
  "variety","tribe"} AND the token immediately after the
  window is "of", re-pick from the of-phrase: the tokens
  following that "of" (one leading "the"/"a"/"an" skipped)
  up to the next boundary token, "(", or 6 tokens; the genus
  is the last noun-capable token there, falling back to the
  scaffold word itself when the of-phrase yields none. At
  most one re-pick per definition. Scaffold words are
  language-side taxonomy markers, a closed class like the
  function words: the true genus of "species of arachnids"
  is arachnids.

  chain(word): resolve the word through pmca_dictionary; start at
  its headword. At each step, take the current word's definitions
  IN ORDER and use the FIRST whose genus is not None AND resolves
  to a headword; record the step; move to the genus headword.
  Stop conditions: the current word yields no resolvable genus
  (natural end: DEAD, or VERB_FORM when that word's dictionary
  form is "verb"); a headword repeats (CYCLE); 8 steps taken
  without a natural end (DEPTH); the start word does not resolve
  at all (UNRESOLVED).

  A word belongs to EVERY probed kind its chain passes through
  (reaching "mammal" and continuing to "animal" puts the word in
  both). The per-kind payload chain for a word is its walk path
  truncated at that kind.

Payload per kind: {"category", "source": "webster1913",
"dictionary_sha256", "members": sorted, "chains": {word: path}}.
Canonical bytes and TamperError are REUSED from
pmca_category_keys (its canonical_bytes is payload-shape
agnostic). Its CategoryStore is NOT reused: CategoryStore.put
reconstructs the seed-shaped payload {"category","members",
"edges"} and cannot carry this payload shape, so the store below
re-implements the same pattern (put recomputes and refuses a
mismatch; pull verifies by recomputation) for this shape. That
duplication is structural, reported as a finding.

RULE V2 (2026-10-20): genus extraction changed in exactly two
ways from v1; everything else in this module is unchanged.
  (1) Leading parenthesized field labels are cleaned away
      before tokenizing. V1 let the "(" boundary fire at
      token zero and discarded the kind-carrying first
      definitions (cat def 0 names "animal", elephant
      "mammal", spider "arachnids" — all skipped under v1).
  (2) The genus pick is noun-tested against the dictionary
      itself: the LAST noun-capable window token, where v1
      took the last token outright and so picked participles
      and adjectives (animal -> "endowed").
V1 (md5 869c818e16aa6f51160aee1d3ae9b7dd) is preserved
alongside as pmca_category_derive_v1.py. V1's measured result
on the seed-110: ZERO memberships in all six kinds; failure
census VERB_FORM 68, DEAD 30, CYCLE 8, UNRESOLVED 4.

RULE V3 (2026-10-20): genus extraction changed in exactly
three ways from v2; everything else in this module is
unchanged, and the noun test, label cleaning, and verb-form
handling are v2's.
  (1) Quantifier gloss skip: in "Any one of numerous species
      ...", "One of the Mammalia", "One of the Insecta", the
      "one of" / "of the" gloss is skipped as a unit before
      the window is collected. Under v2 the window closed on
      "one" itself, or on the "of" behind it, so the true
      genus never became a candidate (spider and wolf both
      walked to "one"; mammal's own definition yielded None).
  (2) End-of-window close: a noun phrase with no boundary
      token and no "(" inside the 6 scanned tokens now yields
      its last noun-capable token instead of None.
  (3) Scaffold of-phrase re-pick (see the derivation above):
      when the picked genus is a scaffold word and the next
      token is "of", the genus is re-picked from the
      of-phrase — "species of arachnids" names arachnids.
V2 (md5 b5411a3a02ebd3e1450a6bce530af4fc) is preserved
alongside as pmca_category_derive_v2.py. V2's measured
result on the seed-110: 1/110 words reaching a kind (cat);
failure census VERB_FORM 60, DEAD 34, CYCLE 11,
UNRESOLVED 4; 4 memberships across the seed and probe
universes, all correct (cat->animal, frog->animal,
ant->insect, bee->insect); elephant and lion reached
mammal at the genus step but propagated no further.

RULE V4 (2026-10-20): genus extraction changed in exactly
two ways from v3; everything else in this module is
unchanged, and the v3 changes (gloss skip, end-of-window
close, scaffold re-pick) all stand.
  (4) Verb-form truncation: candidate collection — in
      the main window scan and in the scaffold of-phrase
      collection alike — stops BEFORE the first
      verb-form-only token (defined above), because a
      noun phrase ends where verb structure begins. Under
      v3, cook's definition "One whose occupation is to
      prepare food for the table" crossed the verb
      structure "is to prepare" and picked "food", putting
      a cook in the food key; and wolf's of-phrase ran
      past "belonging" and picked "to".
  (5) Apparatus skip: a definition whose raw text carries
      a backslash within its first 30 characters yields
      None inside genus extraction. Pronunciation
      apparatus is metadata, not a definition. Under v3,
      mammalia's stored def 0 (the headword followed by
      its backslash pronunciation apparatus and "n. pl.
      [NL., from L. mammalis...]") yielded the genus
      "n". A census before this change was relied on:
      exactly 3 of the 449 definitions stored for the
      seed-110 words carry an early backslash (the first
      definitions of shrapnel, prepared, and us), so the
      skip is a scalpel, not a sieve.
V3 (md5 7b1ebd5560181d8d362414f5557095c5) is preserved
alongside as pmca_category_derive_v3.py. V3's measured
result: 7 memberships across the seed and probe
universes, 6 correct (cat, frog, goat, sheep -> animal;
ant, bee -> insect) and 1 wrong on its face (cook ->
food, inside the keyed payload); seed-110 census
VERB_FORM 40, DEAD 10, CYCLE 17, UNRESOLVED 4, DEPTH 38
across all statuses, reached 2/110; spider still failed
(its def 0 of-phrase ran through "comprising" past the
sentence end and picked "spiders", a self-cycle, while
def 1 yields arachnids -> arachnid correctly); the
mammal -> mammalia bridge worked but mammalia's own
walk wandered from its pronunciation entry.

Rules: Python 3 stdlib only. Deterministic. No floats in a keyed
payload (enforced by pmca_category_keys.canonical_bytes).
"""

import hashlib
import re

import pmca_category_keys
import pmca_dictionary

TamperError = pmca_category_keys.TamperError

KINDS = ("animal", "mammal", "insect", "arachnid", "plant", "food")
MAX_DEPTH = 8

_ARTICLES = frozenset(("a", "an", "the", "any", "one"))
_BOUNDARY = frozenset(("of", "with", "which", "who", "that", "in",
                       "on", "from", "by", "for", "resembling",
                       "like"))
_TOKEN_RE = re.compile(r"[A-Za-z]+")


_LABEL_RE = re.compile(r"^\([^)]*\)\s*")


def _clean_definition(definition):
    """V2 cleaning (step a), shared by genus extraction and
    the noun test: repeatedly strip a leading "(...)" group
    plus following whitespace while the text starts with "(".
    Strips GCIDE field labels ("(Zoöl.)") and sequences like
    "(Naut.) (a)"."""
    text = definition.lstrip()
    while text.startswith("("):
        m = _LABEL_RE.match(text)
        if m is None:
            break
        text = text[m.end():]
    return text


def _first_token(text):
    """First lowercase letter-token of a string, or None."""
    m = _TOKEN_RE.search(text)
    return m.group(0).lower() if m else None


def _noun_capable(candidate):
    """V2 noun test (step e): a candidate is noun-capable iff
    it resolves in the dictionary AND at least one of its
    definitions, after the same label cleaning as the genus
    rule and left-stripping, does not open with the token
    "to" (i.e. is not a verb-form definition)."""
    resolved = pmca_dictionary.lookup(candidate)
    if resolved is None:
        return False
    for d in resolved["definitions"]:
        if _first_token(_clean_definition(d)) != "to":
            return True
    return False


def _verb_form_only(token):
    """V4 change (4) test: a token is verb-form-only iff it
    resolves in the dictionary AND every one of its
    definitions, after the v2 label cleaning and
    left-stripping, opens with the token "to" — the exact
    complement of the noun test's per-definition read, so
    a verb-form-only token is never noun-capable. An
    unresolved token, or one with no definitions, is not
    verb-form-only."""
    resolved = pmca_dictionary.lookup(token)
    if resolved is None or not resolved["definitions"]:
        return False
    for d in resolved["definitions"]:
        if _first_token(_clean_definition(d)) != "to":
            return False
    return True


_SCAFFOLD = frozenset(("species", "kind", "sort", "type",
                       "class", "order", "family", "genus",
                       "group", "variety", "tribe"))
_OF_ARTICLES = frozenset(("the", "a", "an"))


def _collect(spans, start, paren):
    """Collect a noun-phrase window: the (token, offset) spans
    from index start, at most 6, stopping before the first
    boundary token or the first token starting after the "("
    at offset paren (-1 when the chunk has none). Under v3
    the phrase also closes at the end of the scanned window
    or of the chunk, so the collected tokens are the window
    whether or not a boundary was met. V4 change (4): the
    collection ALSO stops before the first verb-form-only
    token — it and everything after it in this collection
    are excluded, because a noun phrase ends where verb
    structure begins. This one collection serves the main
    window scan and the scaffold of-phrase re-pick alike,
    so the cut applies to both. May be empty."""
    window = []
    for tok, off in spans[start:start + 6]:
        if tok in _BOUNDARY:
            break
        if paren != -1 and off > paren:
            break
        if _verb_form_only(tok):
            break
        window.append((tok, off))
    return window


def _last_noun_capable(window):
    """The last noun-capable token in a collected window of
    (token, offset) spans, or None."""
    for tok, _off in reversed(window):
        if _noun_capable(tok):
            return tok
    return None


def _of_phrase_genus(spans, tokens, of_index, paren):
    """V3 scaffold re-pick source: the of-phrase following
    tokens[of_index] == "of" — skip ONE leading
    "the"/"a"/"an", collect up to 6 spans until the next
    boundary token or "(", and return the last noun-capable
    token there, or None."""
    j = of_index + 1
    if j < len(tokens) and tokens[j] in _OF_ARTICLES:
        j += 1
    return _last_noun_capable(_collect(spans, j, paren))


def genus_of_definition(definition):
    """Extract the genus word from ONE definition string by
    the v4 rule (see module docstring), or None. The v2
    label cleaning and noun test, and the three v3 changes
    (quantifier gloss skip, end-of-window close, scaffold
    of-phrase re-pick), all stand; v4 adds the verb-form
    truncation inside the shared collection (change 4) and
    the apparatus skip below (change 5)."""
    # V4 change (5): apparatus skip. Pronunciation
    # apparatus in the raw text — a backslash within the
    # first 30 characters, as in "Mammalia \Mam*ma"li*a\,
    # ..." — is metadata, not a definition: yield None so
    # the walker's first-resolvable preference moves to
    # the next definition.
    if "\\" in definition[:30]:
        return None
    chunk = _clean_definition(definition).split(";", 1)[0]
    spans = [(m.group(0).lower(), m.start())
             for m in _TOKEN_RE.finditer(chunk)]
    if not spans:
        return None
    tokens = [t for t, _ in spans]
    if tokens[0] == "to":
        return None
    start = 1 if tokens[0] in _ARTICLES else 0
    # V3 change (1): quantifier gloss skip. "Any one of ..."
    # leaves ["one", "of"] after the article skip; "One of
    # the X" leaves the "of" continuation after "one" itself
    # was the skipped article.
    if tokens[start:start + 2] == ["one", "of"]:
        start += 2
        if start < len(tokens) and tokens[start] == "the":
            start += 1
    if start < len(tokens) and tokens[start] == "of":
        start += 1
        if start < len(tokens) and tokens[start] == "the":
            start += 1
    paren = chunk.find("(")
    # V3 change (2): _collect closes at the window/chunk end
    # too; an empty window is still None.
    window = _collect(spans, start, paren)
    if not window:
        return None
    genus = _last_noun_capable(window)
    if genus is None:
        return None
    # V3 change (3): scaffold of-phrase re-pick, at most once.
    if genus in _SCAFFOLD:
        after = start + len(window)
        if after < len(tokens) and tokens[after] == "of":
            repick = _of_phrase_genus(spans, tokens, after,
                                      paren)
            if repick is not None:
                return repick
    return genus


def _resolve(word):
    """pmca_dictionary lookup result dict, or None."""
    return pmca_dictionary.lookup(word)


def walk(word, kinds=KINDS, max_depth=MAX_DEPTH):
    """Walk word's definition chain. Returns a dict:
    {"word", "headword" (or None), "path": [headword, ...],
     "steps": [(word, definition_index, genus_token,
                genus_headword), ...],
     "kinds": [probed kinds on the path, in path order],
     "status": "CYCLE" | "DEAD" | "VERB_FORM" | "DEPTH" |
               "UNRESOLVED"}.
    A natural end (current word offers no resolvable genus) is
    reported as DEAD, or VERB_FORM when that word's dictionary
    form is "verb". Kinds collected before a failure still count."""
    start = _resolve(word)
    if start is None:
        return {"word": word, "headword": None, "path": [],
                "steps": [], "kinds": [], "status": "UNRESOLVED"}
    current = start
    path = [current["headword"]]
    steps = []
    status = None
    while True:
        if len(steps) >= max_depth:
            status = "DEPTH"
            break
        here = current["headword"]
        nxt = None
        for idx, definition in enumerate(current["definitions"]):
            g = genus_of_definition(definition)
            if g is None:
                continue
            resolved = _resolve(g)
            if resolved is not None:
                nxt = (idx, g, resolved)
                break
        if nxt is None:
            status = ("VERB_FORM" if current["form"] == "verb"
                      else "DEAD")
            break
        idx, g, resolved = nxt
        steps.append((here, idx, g, resolved["headword"]))
        if resolved["headword"] in path:
            status = "CYCLE"
            break
        path.append(resolved["headword"])
        current = resolved
    reached = [w for w in path if w in kinds]
    return {"word": word, "headword": start["headword"],
            "path": path, "steps": steps, "kinds": reached,
            "status": status}


def derive_universe(words, kinds=KINDS):
    """Walk every word in the universe once. Returns
    {input_word: walk_result}."""
    return {w: walk(w, kinds=kinds) for w in words}


def chain_to(walk_result, kind):
    """The word's path truncated at (and including) kind, or
    None when the walk never reached kind."""
    path = walk_result["path"]
    if kind not in path:
        return None
    return path[:path.index(kind) + 1]


def build_kind_payload(kind, walks):
    """Build the keyed payload for one kind from precomputed
    walks {word: walk_result}. Members are the input words whose
    chain reached kind; each carries its truncated chain."""
    members = []
    chains = {}
    for word in sorted(walks):
        c = chain_to(walks[word], kind)
        if c is not None:
            members.append(word)
            chains[word] = c
    payload = {"category": kind,
               "source": "webster1913",
               "dictionary_sha256": pmca_dictionary.file_hash(),
               "members": members,
               "chains": chains}
    key = hashlib.sha256(
        pmca_category_keys.canonical_bytes(payload)).hexdigest()
    return {"payload": payload, "key": key,
            "member_count": len(members)}


class DerivedCategoryStore:
    """Key-addressed store of frozen derived-category payloads.
    Same pattern as pmca_category_keys.CategoryStore (blobs kept
    per version; live pointer per category; an address locates,
    a key proves), re-implemented because that store's put() is
    bound to the seed payload shape. Verification reuses
    pmca_category_keys.canonical_bytes + TamperError."""

    def __init__(self):
        self.blobs = {}
        self.live = {}

    def put(self, built):
        """Store a build_kind_payload result under its key. The
        payload is re-canonicalized and re-hashed here: a stated
        key that does not match the content is refused."""
        blob = pmca_category_keys.canonical_bytes(built["payload"])
        if hashlib.sha256(blob).hexdigest() != built["key"]:
            raise TamperError("record key does not match content")
        self.blobs[built["key"]] = blob
        return built["key"]

    def pull(self, key):
        """Return the payload under key ONLY if the stored bytes
        still hash to that key; else TamperError."""
        import json
        blob = self.blobs[key]  # KeyError: no such key
        if hashlib.sha256(blob).hexdigest() != key:
            raise TamperError(
                "stored bytes do not hash to key %s" % key)
        return json.loads(blob.decode("utf-8"))

    def set_live(self, category, key):
        if key not in self.blobs:
            raise KeyError("cannot point live at unstored key %s"
                           % key)
        self.live[category] = key

    def pull_live(self, category):
        return self.pull(self.live[category])


if __name__ == "__main__":
    import pmca_seed

    seed_words = [e["word"] for e in pmca_seed.DEMO_DICTIONARY]
    walks = derive_universe(seed_words)
    for kind in KINDS:
        built = build_kind_payload(kind, walks)
        print("%s: %d seed members, key %s"
              % (kind, built["member_count"], built["key"][:16]))
    reached = sum(1 for r in walks.values() if r["kinds"])
    print("seed words reaching >=1 kind: %d/%d"
          % (reached, len(seed_words)))
