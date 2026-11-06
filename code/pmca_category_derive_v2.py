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
  "one"}. Scan at most the next 6 tokens, up to the first
  boundary token in {"of","with","which","who","that","in",
  "on","from","by","for","resembling","like"} or a "(" in the
  CLEANED text. The genus is the LAST NOUN-CAPABLE token
  before that boundary: a token is noun-capable iff it
  resolves in the dictionary AND at least one of its
  definitions, after the same label cleaning, does not open
  with the token "to". If no boundary appears within the 6
  tokens, or no window token is noun-capable, -> None.

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


def genus_of_definition(definition):
    """Extract the genus word from ONE definition string by
    the v2 rule (see module docstring), or None. The text rule
    is unchanged from v1 except the two v2 changes: leading
    field labels are cleaned first, and the genus pick is the
    last noun-capable token before the boundary."""
    chunk = _clean_definition(definition).split(";", 1)[0]
    spans = [(m.group(0).lower(), m.start())
             for m in _TOKEN_RE.finditer(chunk)]
    if not spans:
        return None
    tokens = [t for t, _ in spans]
    if tokens[0] == "to":
        return None
    start = 1 if tokens[0] in _ARTICLES else 0
    window = spans[start:start + 6]
    if not window:
        return None
    paren = chunk.find("(")
    before = None  # number of window tokens sitting before the cut
    for i, (tok, off) in enumerate(window):
        if tok in _BOUNDARY:
            before = i
            break
        if paren != -1 and off > paren:
            before = i
            break
    if before is None:
        return None  # no boundary within the scanned tokens
    if before == 0:
        return None  # boundary is the first scanned token
    for tok, _off in reversed(window[:before]):
        if _noun_capable(tok):
            return tok
    return None


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
