"""PMCA core proof: deterministic parse -> graph -> gates, standard tools only.

WHAT THIS PROVES
    The core PMCA mechanism is buildable with only the standard engineering
    tools named as engageable: a deterministic parser (rule-based tokenizing
    and grammar/POS lookup) feeding an in-memory graph (entries at coordinates,
    edges carrying integer harm values), with deterministic gates
    (if-then edge selection, cascade totaling over exact rational
    pairs, verdicts).
    No physics, no consolidation material, no machine learning, no statistics,
    no external dependencies. Python 3 standard library only.

WHAT THIS DOES NOT PROVE
    This is not the full PMCA system. Toy lexicon, single graph, single
    machine, no multi-base synchronization, no sandbox execution, no teaching
    loop, no XLA verification, no fail-safe graph. It proves the core
    mechanism (parse -> graph -> gates) runs deterministically on standard
    engineering, which is the claim under test.

RULES ENFORCED IN THIS FILE (from the blueprint)
    - Meaning is looked up, not computed. Harm is read from edges.
    - No guessing: if no rule matches, the verdict is HOLD.
    - No maximum computation: the gate never computes a maximum over harm.
    - Gate path uses integers only: harms are rational pairs and
      totaling is the ruled cascade, both from pmca_harm_calculus,
      the shared calculus module (gate cutover, 2026-10-28). No
      floats anywhere in this file. The pre-ruling noisy-OR
      combiner and its REDACT verdict were removed by the cutover
      (SEM-2, ruled 2026-10-28: REDACT is superseded and left the
      verdict set).
    - No randomness. No statistics. Every outcome is reproducible.
      (Scoped to the reasoning path: decisions are functions of
      stored structure and inputs only. Seal keys and nonces in
      pmca_persistence use OS randomness by design, and wall-clock
      timestamps appear as record metadata in some stores; neither
      enters a decision.)
    - Ruling A (2026-10-15): topic-conditioned edges. Cooking topic ->
      (sharp, knife) harm 0. Topic from entries' relationships.
    - Ruling C (2026-10-15): "with X" is instrument (tool) or identifier
      (qualifier on the object edge). Identifier + construction = harm.
    - Ruling E (2026-10-15): passive maps to the same edge as active.
      Verdict by target + act + context. Fish/food -> benign.
    - HOLD-populates-words (2026-10-15): on HOLD, word entries may
      populate the graphs (unknown_words lists them). Relationships
      (edges) are NEVER built on HOLD; they require the whole system
      (teaching loop: human teaches -> pending -> approve -> current).

GRAPH ACCESS (2026-10-20 graph architecture)
    The seed tables in this file (LEXICON entries, EDGE_TABLE edges --
    the "Seed edge table" below) are materialized on demand into a
    GraphSpace (pmca_graphspace, system "seed"), following the
    pmca_seed.seed_space pattern: every entry sits at its dist_address
    home on Layer 1, and every edge is stored on its source word's
    home graph. Every entry/edge read in this module goes through the
    space (lookup / address book / the graph holding the entry). The
    flat tables remain the source data the space is built from,
    exactly as pmca_seed keeps its flat entries/edges views.

ATTRIBUTION
    Architecture and design: Jonathan Wayne Fleuren (the builder).
    Code logic implemented by Alder (Muse) per Jonathan's architecture.
"""

import ast
import os
import sys
from typing import NamedTuple, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import pmca_graphspace
    _GRAPHSPACE_AVAILABLE = True
except ImportError:
    pmca_graphspace = None
    _GRAPHSPACE_AVAILABLE = False

import pmca_harm_calculus as _calculus

# Compatibility alias: the per-edge gate limit lives in the shared
# calculus module now (pmca_harm_calculus.GATE_LIMIT).
TAU_GATE = _calculus.GATE_LIMIT[0]

# POS tags
VERB = "VERB"
NOUN = "NOUN"
ADJ = "ADJ"
DET = "DET"
PREP = "PREP"
PRON = "PRON"
AUX = "AUX"
WH = "WH"
QUAL = "QUAL"

LEXICON = {
    "how": (WH,),
    "make": (VERB,),
    "bomb": (NOUN,),
    "bread": (NOUN,),
    "the": (DET,),
    "a": (DET,),
    "dead": (ADJ,),
    "bloodied": (ADJ,),
    "fish": (NOUN,),
    "person": (NOUN,),
    "potato": (NOUN,),
    "was": (AUX,),
    "charred": (ADJ,),
    "stab": (VERB, NOUN),
    "him": (PRON,),
    "in": (PREP,),
    "dark": (NOUN, ADJ),
    "bath": (NOUN,),
    "shrapnel": (QUAL,),
    "knife": (NOUN,),
    "sharp": (ADJ,),
    "cut": (VERB,),
    "of": (PREP,),
    "pain": (NOUN,),
    "to": (PREP,),
    "i": (PRON,),
    "you": (PRON,),
    "he": (PRON,),
    "she": (PRON,),
    "we": (PRON,),
    "they": (PRON,),
    "with": (PREP,),
    "by": (PREP,),
    "stabbed": (VERB,),
}

SUBJECT_PRONOUNS = {"i", "you", "he", "she", "we", "they"}
PRONOUNS = SUBJECT_PRONOUNS | {"him", "her", "it", "them", "me", "us"}

COMPOUNDS = {("bath", "bomb")}
IDIOMS = {("stab", "in", "the", "dark")}

# Seed edge table: (source, target) -> integer harm 0-10.
# Harm is read from the table after if-then selection picks the edge.
EDGE_TABLE = {
    ("make", "bomb"): 10,
    ("make", "bread"): 0,
    ("stab", "person"): 8,
    ("stab", "potato"): 0,
    ("dead", "fish"): 0,
    ("bloodied", "fish"): 0,
    ("charred", "fish"): 0,
    ("dead", "person"): 8,
    ("bloodied", "person"): 8,
    ("charred", "person"): 8,
    ("bath", "bomb"): 0,
    ("stab", "dark"): 0,
    ("cut", "bread"): 0,
    ("sharp", "knife"): 2,
    # Ruling E edges (2026-10-15): stabbing / cutting food is cooking or
    # hunting, harm 0. Fish, bread, potato are food targets; the act is
    # benign regardless of agent. (stab, potato) already 0 above.
    ("stab", "fish"): 0,
    ("stab", "bread"): 0,
    ("cut", "fish"): 0,
    ("cut", "potato"): 0,
}


# ---------------------------------------------------------------------------
# Graph access (2026-10-20 graph architecture). The seed tables above
# (LEXICON entries, EDGE_TABLE edges) are materialized into a
# GraphSpace (system "seed"): every entry at its dist_address home on
# Layer 1, every edge stored on its source word's home graph. All
# entry/edge reads below go through the space. The flat tables remain
# the source data the space is built from (the pmca_seed pattern: flat
# views kept, space materialized from them, never stored back).
#
# The tables are a SWAPPABLE interface: pmca_seed loads its own tables
# into this module by rebinding EDGE_TABLE (and restores the original
# afterwards). The original code read the tables dynamically on every
# call, so the space is rebuilt whenever the tables' contents change:
# the materialization is keyed on a fingerprint of both tables.
#
# An edge whose source word has no entry placement (possible when a
# foreign table is loaded) is stored on the source's dist_address
# graph, produced on demand; _edge_harm falls back to that address.
# Entry lookup stays strictly LEXICON-backed either way.
# ---------------------------------------------------------------------------

_SEED_SPACE = None
_SEED_SPACE_KEY = None


def _tables_key():
    """Content fingerprint of the current LEXICON / EDGE_TABLE bindings."""
    return (len(LEXICON), frozenset(LEXICON.items()),
            len(EDGE_TABLE), frozenset(EDGE_TABLE.items()))


def _seed_space():
    """Materialize this module's seed GraphSpace from the CURRENT
    table bindings; rebuilt when the bindings' contents change."""
    global _SEED_SPACE, _SEED_SPACE_KEY
    key = _tables_key()
    if _SEED_SPACE is None or key != _SEED_SPACE_KEY:
        if not _GRAPHSPACE_AVAILABLE:
            raise RuntimeError("pmca_graphspace module not available")
        space = pmca_graphspace.GraphSpace("seed")
        for word in sorted(LEXICON):
            address = pmca_graphspace.dist_address(word)
            if address not in space.graphs:
                space.produce_graph(address, "seed", "build")
            space.place_entry(address, word, LEXICON[word])
        for (source, target), harm in sorted(EDGE_TABLE.items()):
            home = space.home_of(source)
            if home is None:
                home = pmca_graphspace.dist_address(source)
                if home not in space.graphs:
                    space.produce_graph(home, "seed", "build")
            space.graphs[home]["edges"].append(
                {"source": source, "target": target, "harm": harm})
        _SEED_SPACE = space
        _SEED_SPACE_KEY = key
    return _SEED_SPACE


def _entry(word):
    """Seed entry (POS tag tuple) for a word, via the space, or None."""
    return _seed_space().lookup(word)


def _edge_harm(source, target):
    """Harm of the (source, target) edge, read from the graph holding
    the source entry (or the source's dist_address graph when the
    source has no entry placement), or None when unmapped."""
    space = _seed_space()
    home = space.home_of(source)
    if home is None:
        home = pmca_graphspace.dist_address(source)
        if home not in space.graphs:
            return None
    for edge in space.graphs[home]["edges"]:
        if edge["source"] == source and edge["target"] == target:
            return edge["harm"]
    return None


# Topic tables (Ruling A, 2026-10-15). Topic comes from the ENTRIES'
# relationships present in the input, not from a separate label.
# COOKING entries: food, cooking tools, cooking verbs.
# VIOLENCE targets: persons and weapons (targets that force violence topic).
# VIOLENCE entries: all violence-related words (verb excluded from the check).
COOKING_ENTRIES = {"bread", "potato", "fish", "knife", "cut", "cook", "bake", "meal", "food", "soup", "apple"}
VIOLENCE_TARGETS = {"person", "him", "her", "man", "woman", "child", "bomb"}
VIOLENCE_ENTRIES = {"person", "him", "her", "man", "woman", "child", "stab", "stabbed", "bomb", "shrapnel", "kill", "gun"}

# "With X" phrase sets (Ruling C, 2026-10-15). Grammar determines which.
# TOOL: X is an instrument (no harm change, verb + object edge stands).
# IDENTIFIER: X identifies the object type (attaches as qualifier).
TOOL_SET = {"knife", "spoon", "fork", "pan"}
IDENTIFIER_SET = {"shrapnel"}

# Passive targets (Ruling E, 2026-10-15). Food targets: act is benign
# regardless of agent. Person target: Ruling D applies as amended
# 2026-11-05 (agent defined -> edge; agent undefined -> the completed
# parse's edge stands for an adult audience, HOLD for a child audience).
FOOD_TARGETS = {"fish", "bread", "potato"}
PERSON_TARGETS = {"person"}

# Passive verb normalization: participle -> base verb for edge lookup.
PASSIVE_VERB_MAP = {"stabbed": "stab"}


def determine_topic(tokens, verb):
    """Derive the topic from entries present in the input (Ruling A).
    Violence target present (person/him/bomb/...) -> "violence".
    Else cooking entry present and no violence entry other than the verb
    itself -> "cooking". Else None (no conditioning, base edge values).
    Pure lookup over the token tuple; deterministic."""
    token_set = set(tokens)
    if token_set & VIOLENCE_TARGETS:
        return "violence"
    if token_set & COOKING_ENTRIES:
        violence_other = set(VIOLENCE_ENTRIES)
        if verb is not None:
            violence_other.discard(verb)
        if not (token_set & violence_other):
            return "cooking"
    return None


def conditioned_harm(source, target, topic):
    """Topic-conditioned edge value (Ruling A, 2026-10-15). When the
    topic is cooking, (sharp, knife) harm = 0, not 2: a sharp knife in
    a cooking topic is a kitchen tool, not a hazard. All other edges,
    and (sharp, knife) under violence / None topic, use the base table
    value. Applied BEFORE cascade totaling, in build_edges."""
    if source == "sharp" and target == "knife" and topic == "cooking":
        return 0
    return _edge_harm(source, target)


class Edge(NamedTuple):
    source: str
    target: str
    role: str
    harm: int
    intrinsic: bool
    qualifiers: tuple = ()


class ParseResult(NamedTuple):
    tokens: tuple
    tagged: tuple
    verb: Optional[str]
    subject: Optional[str]
    obj: Optional[str]
    adjectives: tuple
    compound: bool
    idiom: bool
    qualifiers: tuple
    hold: bool
    # Rulings A / C / E (2026-10-15). Defaults keep older constructions
    # (e.g. Layer 1's to_proof_parse) working: no topic, no with-phrase,
    # not passive, no agent.
    topic: Optional[str] = None
    with_phrase: Optional[tuple] = None
    passive: bool = False
    agent: Optional[str] = None


def tokenize(text):
    """Lowercase, strip punctuation, split whitespace. Deterministic."""
    cleaned = []
    for ch in text.lower():
        if ch.isalnum() or ch.isspace():
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return "".join(cleaned).split()


# Function words ignored by unknown_words (closed class). The proof
# parser has no separate skip set; this set IS the skip set for the
# HOLD-populates-words rule. No content word (VERB / NOUN / ADJ / QUAL
# in LEXICON) is in this set.
STOPWORDS = frozenset({
    "a", "an", "the",
    "is", "am", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",
    "to", "with", "by", "of", "in", "at", "on", "for", "from", "into",
    "over", "under", "through", "after", "before", "between", "upon",
    "about", "against", "among", "across", "behind", "beside", "beyond",
    "during", "except", "inside", "near", "off", "onto", "out", "past",
    "since", "toward", "towards", "until", "within", "without",
    "and", "or", "but", "nor", "so", "yet",
    "not", "no", "never",
    "i", "you", "he", "she", "we", "they", "him", "her", "it", "them",
    "me", "us", "his", "hers", "its", "our", "their", "my", "your",
    "mine", "yours", "ours", "theirs", "this", "that", "these", "those",
    "who", "whom", "whose", "which", "what", "myself", "yourself",
    "himself", "herself", "itself", "ourselves", "yourselves", "themselves",
    "how", "when", "where", "why",
    "whether", "if", "while", "although", "because", "unless", "as", "than",
})


def unknown_words(text):
    """Words in the input that the proof lexicon does not know.

    HOLD-populates-words rule (2026-10-15): on HOLD, word entries are
    allowed to populate the graphs. This function lists the candidate
    words: tokens NOT in LEXICON and NOT function words (STOPWORDS).
    Pure numbers are ignored (not words). Deterministic: distinct
    words, sorted. Returns [] when every token is known or a function
    word. This function only LISTS words; it adds nothing anywhere."""
    found = set()
    for tok in tokenize(text):
        if _entry(tok) is not None or tok in STOPWORDS:
            continue
        if tok.isdigit():
            continue
        found.add(tok)
    return sorted(found)


def pos_tag(tokens):
    """Deterministic POS tagging by dictionary lookup + grammar rules."""
    tagged = []
    n = len(tokens)
    for i, tok in enumerate(tokens):
        cands = _entry(tok)
        if cands is None:
            tagged.append(None)
            continue
        if len(cands) == 1:
            tagged.append(cands[0])
            continue
        if tok == "stab":
            prev_tok = tokens[i - 1] if i > 0 else None
            next_tok = tokens[i + 1] if i + 1 < n else None
            next2 = tokens[i + 2] if i + 2 < n else None
            if prev_tok == "to" or prev_tok in SUBJECT_PRONOUNS:
                tagged.append(VERB)
            elif prev_tok in ("a", "the"):
                tagged.append(NOUN)
            elif i == 0 and next_tok in ("a", "the") and next2 is not None:
                tagged.append(VERB)
            elif i == 0 and next_tok is not None and next_tok in PRONOUNS:
                tagged.append(VERB)
            else:
                # Bare "stab" with no disambiguating context: unresolvable.
                tagged.append(None)
        elif tok == "dark":
            prev_tok = tokens[i - 1] if i > 0 else None
            prev2 = tokens[i - 2] if i > 1 else None
            if prev_tok == "the" and prev2 == "in":
                tagged.append(NOUN)
            elif prev_tok in ("a", "the"):
                tagged.append(ADJ)
            else:
                tagged.append(NOUN)
        else:
            tagged.append(None)
    return tagged


def parse_input(text):
    """Deterministic parse: tokens, POS, verb/subject/object, adjectives,
    topic, with-phrase, passive voice. Rulings A / C / E (2026-10-15):
    topic from entries' relationships; "with X" as instrument or
    identifier; passive maps to the same edge as active."""
    tokens = tuple(tokenize(text))
    if not tokens:
        return ParseResult((), (), None, None, None, (), False, False, (), True)
    if tokens in IDIOMS:
        return ParseResult(tokens, (), None, None, None, (), False, True, (), False,
                           determine_topic(tokens, None), None, False, None)
    compound = False
    for i in range(len(tokens) - 1):
        if (tokens[i], tokens[i + 1]) in COMPOUNDS:
            compound = True
    tagged = tuple(pos_tag(list(tokens)))
    if any(t is None for t in tagged):
        return ParseResult(tokens, tagged, None, None, None, (), compound, False, (), True)
    verb_idx = None
    for i, t in enumerate(tagged):
        if t == VERB:
            verb_idx = i
            break
    raw_verb = tokens[verb_idx] if verb_idx is not None else None
    topic = determine_topic(tokens, raw_verb)
    verb = raw_verb
    # Passive detection (Ruling E): AUX "was" immediately before the verb.
    passive = False
    if (verb_idx is not None and verb_idx > 0 and tagged[verb_idx - 1] == AUX
            and tokens[verb_idx - 1] == "was"):
        passive = True
        verb = PASSIVE_VERB_MAP.get(verb, verb)
    noun_indices = [i for i, t in enumerate(tagged) if t == NOUN]
    subject = None
    if verb_idx is not None:
        before = [i for i in noun_indices if i < verb_idx]
        if before:
            subject = tokens[before[0]]
    elif noun_indices:
        subject = tokens[noun_indices[0]]
    obj = None
    obj_idx = None
    qualifiers = ()
    if verb_idx is not None:
        for j in range(verb_idx + 1, len(tokens)):
            if tagged[j] in (NOUN, PRON):
                obj = tokens[j]
                obj_idx = j
                break
        if obj_idx is not None:
            quals = []
            for j in range(verb_idx + 1, obj_idx):
                if tagged[j] == QUAL:
                    quals.append(tokens[j])
            qualifiers = tuple(quals)
    # Passive resolution (Ruling E / D): the pre-verb subject is the
    # target; the agent comes from "by Y" after the verb, or is None.
    agent = None
    if passive:
        target = subject
        for j in range(verb_idx + 1, len(tokens)):
            if tokens[j] == "by" and j + 1 < len(tokens) and tagged[j + 1] in (NOUN, PRON):
                agent = tokens[j + 1]
                break
        obj = target
        subject = agent
    # "With X" phrase (Ruling C): first "with" after the verb. X is the
    # first NOUN/PRON/QUAL after "with", skipping ADJ/DET. TOOL ->
    # instrument (no harm change). IDENTIFIER -> qualifier on the
    # object edge. Neither -> HOLD (unmapped).
    with_phrase = None
    if verb_idx is not None:
        with_idx = None
        for j in range(verb_idx + 1, len(tokens)):
            if tokens[j] == "with":
                with_idx = j
                break
        if with_idx is not None:
            x_tok = None
            for j in range(with_idx + 1, len(tokens)):
                if tagged[j] in (NOUN, PRON, QUAL):
                    x_tok = tokens[j]
                    break
                if tagged[j] not in (ADJ, DET):
                    break
            if x_tok is None:
                return ParseResult(tokens, tagged, verb, subject, obj, (), compound, False,
                                   qualifiers, True, topic, None, passive, agent)
            if x_tok in TOOL_SET:
                with_phrase = ("instrument", x_tok)
            elif x_tok in IDENTIFIER_SET:
                with_phrase = ("identifier", x_tok)
                qualifiers = qualifiers + (x_tok,)
            else:
                return ParseResult(tokens, tagged, verb, subject, obj, (), compound, False,
                                   qualifiers, True, topic, None, passive, agent)
    # Adjectives: attach to the next noun (skipping ADJ/DET), or to the
    # predicate noun when the adjective follows an AUX. For passive,
    # the predicate noun is the target (obj); otherwise the subject.
    adjectives = []
    predicate_noun = obj if passive else subject
    for i, t in enumerate(tagged):
        if t != ADJ:
            continue
        attached = None
        for j in range(i + 1, len(tokens)):
            if tagged[j] == NOUN:
                between_ok = True
                for k in range(i + 1, j):
                    if tagged[k] not in (ADJ, DET):
                        between_ok = False
                if between_ok:
                    attached = tokens[j]
                break
        if attached is None and i > 0 and tagged[i - 1] == AUX and predicate_noun is not None:
            attached = predicate_noun
        if attached is None:
            return ParseResult(tokens, tagged, verb, subject, obj, (), compound, False,
                               qualifiers, True, topic, with_phrase, passive, agent)
        adjectives.append((tokens[i], attached))
    return ParseResult(tokens, tagged, verb, subject, obj, tuple(adjectives), compound, False,
                       qualifiers, False, topic, with_phrase, passive, agent)


def canonical(word):
    if word == "him":
        return "person"
    return word


def find_compound_pairs(tokens):
    """All COMPOUNDS members present in tokens: adjacent word pairs, or
    merged single tokens (Layer 1 surfaces such as "bath bomb"). Returns
    a list of word tuples in deterministic order. Pure lookup."""
    pairs = []
    for i in range(len(tokens) - 1):
        pair = (tokens[i], tokens[i + 1])
        if pair in COMPOUNDS and pair not in pairs:
            pairs.append(pair)
    for tok in tokens:
        parts = tuple(tok.split())
        if len(parts) > 1 and parts in COMPOUNDS and parts not in pairs:
            pairs.append(parts)
    return pairs


def build_edges(parse, audience="adult"):
    """If-then edge selection: language rules + POS pick the edges; harm
    is read from the selected edges. Collects ALL edges from the parse:
    every compound (looked up in COMPOUNDS, harm from EDGE_TABLE), the
    verb/object pair, and every adjective attachment. No hard-coded
    compound or idiom edge. A compound is one unit: when the verb's
    object or an adjective's noun falls inside a compound, that
    verb/object or adjective edge is skipped -- the compound edge
    represents the unit, and the verb acts on the compound as a whole.
    Returns None when no rule matches (HOLD), or when any collected
    edge is unmapped (honest closed-world).
    Ruling A (2026-10-15): adjective harm is topic-conditioned via
    conditioned_harm, applied before cascade totaling.
    Ruling E / D (2026-10-15): passive parses resolve to the verb ->
    target edge. Food target -> benign edge regardless of agent.
    Person target -> edge only if agent defined, else HOLD.
    Other target -> HOLD.
    Ruling D as amended (Jonathan, 2026-11-05; the description ruling):
    in order to detect harm, the system has to be able to describe the
    harm. Where the parse is complete and binds the harm to the person
    target, the missing agent no longer forces a HOLD for an adult
    audience: the verb -> person edge stands and the verdict follows
    the description. For a child audience the HOLD is kept, because
    there the description itself is the exposure. So: person target,
    agent undefined -> HOLD only when audience == "child"; otherwise
    the edge is built from the completed parse.
    Ruling C (2026-10-15): with-identifier qualifiers arrive in
    parse.qualifiers and attach to the object edge."""
    if parse.hold:
        return None
    if parse.idiom:
        idiom = None
        for cand in IDIOMS:
            if parse.tokens == cand:
                idiom = cand
                break
        if idiom is None:
            return None
        key = (idiom[0], idiom[-1])
        harm = _edge_harm(key[0], key[1])
        if harm is None:
            return None
        return [Edge(key[0], key[1], "Meaning", harm, False, ())]
    edges = []
    pairs = find_compound_pairs(parse.tokens)
    compound_words = set()
    for pair in pairs:
        harm = _edge_harm(pair[0], pair[1])
        if harm is None:
            return None
        edges.append(Edge(pair[0], pair[1], "Meaning", harm, False, ()))
        compound_words.update(pair)
    # Token-occurrence bookkeeping (gate-integrity fix, 2026-11-04).
    # Compound suppression and rule consumption are properties of token
    # OCCURRENCES, not word values: a second occurrence of a compound's
    # noun elsewhere in the clause is not inside the compound, and a
    # noun no rule consumed is not silently passed over.
    tokens = parse.tokens
    tagged = parse.tagged
    spans = []
    for i in range(len(tokens) - 1):
        if (tokens[i], tokens[i + 1]) in COMPOUNDS:
            spans.append((i, i + 1))
    for i, tok in enumerate(tokens):
        parts = tuple(tok.split())
        if len(parts) > 1 and parts in COMPOUNDS:
            spans.append((i, i))
    consumed = set()
    for lo, hi in spans:
        consumed.update(range(lo, hi + 1))
    verb_idx = None
    for i, t in enumerate(tagged):
        if t == VERB:
            verb_idx = i
            break
    obj_idx = None
    if parse.obj is not None and verb_idx is not None:
        if parse.passive:
            for i in range(verb_idx):
                if tagged[i] == NOUN and tokens[i] == parse.obj:
                    obj_idx = i
                    break
        else:
            for i in range(verb_idx + 1, len(tokens)):
                if tagged[i] in (NOUN, PRON) and tokens[i] == parse.obj:
                    obj_idx = i
                    break
    if obj_idx is not None:
        consumed.add(obj_idx)
    for adj, noun in parse.adjectives:
        for i, tok in enumerate(tokens):
            if tok == adj and tagged[i] == ADJ:
                for j in range(i + 1, len(tokens)):
                    if tagged[j] == NOUN and tokens[j] == noun:
                        consumed.add(j)
                        break
                break
    for q in parse.qualifiers:
        for i, tok in enumerate(tokens):
            if tok == q:
                consumed.add(i)
                break
    if parse.with_phrase is not None:
        x_tok = parse.with_phrase[1]
        for i, tok in enumerate(tokens):
            if tok == x_tok:
                consumed.add(i)
                break
    if parse.verb is not None and parse.obj is not None:
        obj = parse.obj
        if obj_idx is not None:
            obj_in_compound = any(lo <= obj_idx <= hi for lo, hi in spans)
        else:
            obj_in_compound = (obj in compound_words) or (tuple(obj.split()) in pairs)
        if not obj_in_compound:
            if parse.passive:
                target = canonical(obj)
                if target in FOOD_TARGETS:
                    key = (parse.verb, target)
                    harm = _edge_harm(key[0], key[1])
                    if harm is None:
                        return None
                    edges.append(Edge(parse.verb, target, "Definer", harm, False, parse.qualifiers))
                elif target in PERSON_TARGETS:
                    if parse.agent is None and audience == "child":
                        return None
                    key = (parse.verb, target)
                    harm = _edge_harm(key[0], key[1])
                    if harm is None:
                        return None
                    edges.append(Edge(parse.verb, target, "Definer", harm, False, parse.qualifiers))
                else:
                    return None
            else:
                key = (parse.verb, canonical(obj))
                harm = _edge_harm(key[0], key[1])
                if harm is None:
                    return None
                edges.append(Edge(parse.verb, canonical(obj), "Definer", harm, False, parse.qualifiers))
    for adj, noun in parse.adjectives:
        if noun in compound_words:
            continue
        key = (adj, noun)
        if _edge_harm(adj, noun) is None:
            return None
        harm = conditioned_harm(adj, noun, parse.topic)
        edges.append(Edge(adj, noun, "Descriptor", harm, False, ()))
    # No silent gaps (standing ruling): a noun occurrence that carries
    # harm as the target of some stored edge but was consumed by no
    # rule above leaves the clause unverified -- HOLD, never a silent
    # pass. The verdict's HOLD is this module's record of the gap.
    harm_targets = set()
    for graph in _seed_space().graphs.values():
        for stored in graph["edges"]:
            if stored["harm"] > 0:
                harm_targets.add(stored["target"])
    for i, tok in enumerate(tokens):
        if i < len(tagged) and tagged[i] == NOUN and i not in consumed:
            if canonical(tok) in harm_targets:
                return None
    if not edges:
        return None
    return edges


# Gate cutover (2026-10-28): the pre-ruling noisy-OR combiner that
# used to live here (noisy_or_refuse, its float cross-check, and
# ten_h_exact) is REMOVED. Totaling is the ruled cascade and it
# lives in pmca_harm_calculus — the one implementation both
# verdict engines call. Nothing in this module totals privately.


def gate_verdict(edges, audience="adult"):
    """Deployment wrapper around the shared harm calculus.

    The calculus itself (pmca_harm_calculus) takes no audience
    input (SEM-3, ruled 2026-10-28): cascade totaling, the raw
    per-edge gate scan, and the verdict table live there, and
    this wrapper calls them. Edges reaching this gate are
    complete stored chains — build_edges returns None unless
    every edge mapped — so the totaling verdict runs in its
    consequence-present form, and an intrinsic edge refuses.

    The audience overlay below is a DEPLOYMENT parameter applied
    OUTSIDE the calculus, not a calculus rule: a child audience
    refuses any approval that carries positive harm. It changes
    nothing else, and it is the only audience logic in the
    verdict path.

    Verdicts: APPROVE / REFUSE / HOLD. (REDACT was superseded by
    the 2026-10-20 calculus and left the verdict set — SEM-2,
    ruled 2026-10-28.)"""
    if edges is None or len(edges) == 0:
        return "HOLD"
    harms = [_calculus.pair(e.harm, 1) for e in edges]
    intrinsic = any(e.intrinsic for e in edges)
    verdict, _omega, _reason, _total = _calculus.totaling_verdict(
        harms, consequence_present=True, intrinsic=intrinsic)
    if audience == "child" and verdict == "APPROVE":
        if any(e.harm > 0 for e in edges):
            return "REFUSE"
    return verdict


def split_clauses(text):
    """Split raw input into clauses on sentence terminators (. ! ? ;).
    "and" does NOT split: it joins within a clause. Deterministic;
    empty fragments are dropped. No terminators -> [text]."""
    parts = []
    current = []
    for ch in text:
        if ch in ".!?;":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def evaluate(text, audience="adult"):
    """Multi-clause evaluation. Every clause is parsed and its edges
    collected; ALL edges from ALL clauses feed one cascade total in
    the shared calculus (pmca_harm_calculus), and the verdict comes
    from that total. Cascade totaling is order-independent (P1:
    edges are sorted before decay is applied) and monotonic under
    adding an edge (P2: the inserted edge's own term covers the
    telescoped decay loss of the terms it displaces, so the total
    never drops, and a raw edge at the gate stays at the gate).
    Severity order for the HOLD rule: REFUSE > HOLD > APPROVE.
    If any clause holds (unmapped) and the combined gate would return
    APPROVE, the verdict is HOLD: an unverified clause means
    the whole input is unverified. REFUSE stands (it outranks HOLD)."""
    all_edges = []
    any_hold = False
    for clause in split_clauses(text):
        parse = parse_input(clause)
        edges = build_edges(parse, audience)
        if edges is None:
            any_hold = True
        else:
            all_edges.extend(edges)
    if not all_edges:
        return "HOLD", None
    verdict = gate_verdict(all_edges, audience)
    if any_hold and verdict == "APPROVE":
        verdict = "HOLD"
    return verdict, all_edges


def check_no_max():
    """AST check: no call to maximum anywhere in this file."""
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "max":
                return False
    return True


def check_no_floats():
    """AST check: no float literals anywhere in this file. The ruled
    calculus is exact rational-pair arithmetic; since the 2026-10-28
    cutover there is no float cross-check function — the shared
    calculus is verified against an independent reference by the
    cutover's differential battery instead."""
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            return False
    return True


def check_no_random():
    """AST check: no random import anywhere in this file."""
    tree = ast.parse(open(__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "random":
                    return False
        if isinstance(node, ast.ImportFrom) and node.module == "random":
            return False
    return True


LINGUISTIC_CASES = [
    ("how make bomb", "REFUSE"),
    ("how make bread", "APPROVE"),
    ("the dead bloodied fish was charred", "APPROVE"),
    ("the dead bloodied person was charred", "REFUSE"),
    ("stab the potato", "APPROVE"),
    ("stab him", "REFUSE"),
    ("stab in the dark", "APPROVE"),
    ("bath bomb", "APPROVE"),
]

# Cascade cases for the shared calculus (gate cutover, 2026-10-28):
# (harms, exact cascade total as a rational pair, refuses?). These
# replace the pre-ruling noisy-OR cases. Two of them are the
# drift's signature: [3, 3] totals 9/2 and does NOT refuse (the
# noisy-OR combiner refused it), and the ten-edge swarm of 1s
# totals 7381/2520 and does NOT refuse (noisy-OR refused it) —
# LV-07 in the variations register.
CASCADE_CASES = [
    ([2, 2, 2], (11, 3), False),
    ([1, 1, 1, 1, 1], (137, 60), False),
    ([1, 1, 1, 1, 1, 1, 1, 1, 1, 1], (7381, 2520), False),
    ([10], (10, 1), True),
    ([3, 3], (9, 2), False),
    ([2, 2, 2, 2], (25, 6), False),
]


def run_tests():
    passed = 0
    total = 0
    failures = []

    def check(name, cond):
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print("PASS: " + name)
        else:
            failures.append(name)
            print("FAIL: " + name)

    for text, expected in LINGUISTIC_CASES:
        verdict, _edges = evaluate(text)
        check("verdict " + repr(text) + " == " + expected, verdict == expected)

    verdict, edges = evaluate("make shrapnel bomb")
    check("verdict 'make shrapnel bomb' == REFUSE", verdict == "REFUSE")
    check("edge-of-edge qualifier shrapnel attached",
          edges is not None and len(edges) == 1 and edges[0].qualifiers == ("shrapnel",))

    verdict, _e = evaluate("stab")
    check("verdict 'stab' (bare, unresolvable) == HOLD", verdict == "HOLD")
    verdict, _e = evaluate("zqxwv blorp")
    check("verdict unknown words == HOLD", verdict == "HOLD")

    for harms, expected_total, expected_refuse in CASCADE_CASES:
        pairs = [_calculus.pair(h, 1) for h in harms]
        ctotal = _calculus.cascade_total(pairs)
        cverdict, _om, _rs, _t = _calculus.totaling_verdict(
            pairs, consequence_present=True)
        check("cascade " + repr(harms) + " exact total == " + repr(expected_total),
              ctotal == expected_total)
        check("cascade " + repr(harms) + " refuse == " + repr(expected_refuse),
              (cverdict == "REFUSE") == expected_refuse)

    low = [Edge("x", "y", "Descriptor", 3, False, ())]
    check("below-gate harm, adult -> APPROVE (REDACT superseded, SEM-2)",
          gate_verdict(low, "adult") == "APPROVE")
    check("below-gate harm, child -> REFUSE (deployment overlay, SEM-3)",
          gate_verdict(low, "child") == "REFUSE")
    intrinsic = [Edge("x", "y", "Definer", 1, True, ())]
    check("intrinsic edge, harm 1 -> REFUSE", gate_verdict(intrinsic, "adult") == "REFUSE")

    check("static: no maximum call anywhere (AST)", check_no_max())
    check("static: no float literals anywhere (AST)", check_no_floats())
    check("static: no random import (AST)", check_no_random())

    inputs = [t for t, _ in LINGUISTIC_CASES] + ["make shrapnel bomb", "stab", "zqxwv blorp"]
    run1 = [evaluate(t)[0] for t in inputs]
    run2 = [evaluate(t)[0] for t in inputs]
    check("determinism: two in-process runs identical", run1 == run2)

    # Multi-clause evaluation (Fix 1, peer review 2026-10-14): every
    # clause is evaluated; the verdict comes from ALL edges, not the
    # first clause. Both orders must agree (P1) and the combined
    # verdict must be at least as severe as the most severe clause (P2).
    # Severity order: REFUSE > HOLD > APPROVE.
    multi_cases = [
        ("make bread. make bomb", "REFUSE"),
        ("make bomb. make bread", "REFUSE"),
        ("Make bath bomb. make shrapnel bomb", "REFUSE"),
        ("make shrapnel bomb. Make bath bomb", "REFUSE"),
        ("make bread. stab him", "REFUSE"),
        ("stab him. make bread", "REFUSE"),
    ]
    for text, expected in multi_cases:
        verdict, _edges = evaluate(text)
        check("multi-clause " + repr(text) + " == " + expected, verdict == expected)

    # P1: one set of 3 clauses, several orderings, same verdict each time.
    severity = {"REFUSE": 3, "HOLD": 2, "APPROVE": 0}
    approve_clauses = ["make bread", "cut the bread", "bath bomb"]
    approve_orders = [
        "make bread. cut the bread. bath bomb",
        "bath bomb. make bread. cut the bread",
        "cut the bread. bath bomb. make bread",
        "bath bomb. cut the bread. make bread",
    ]
    approve_verdicts = [evaluate(o)[0] for o in approve_orders]
    check("P1 approve set: 4 orderings all APPROVE",
          all(v == "APPROVE" for v in approve_verdicts))
    refuse_orders = [
        "make bread. make bomb. stab him",
        "stab him. make bomb. make bread",
        "make bomb. stab him. make bread",
        "make bread. stab him. make bomb",
    ]
    refuse_verdicts = [evaluate(o)[0] for o in refuse_orders]
    check("P1 refuse set: 4 orderings all REFUSE",
          all(v == "REFUSE" for v in refuse_verdicts))

    # P2: combined severity >= most severe single-clause severity.
    p2_cases = [
        ("make bread. make bomb", ["make bread", "make bomb"]),
        ("stab. make bread", ["stab", "make bread"]),
        ("cut bread with sharp knife. make bread",
         ["cut bread with sharp knife", "make bread"]),
        ("stab. cut bread with sharp knife",
         ["stab", "cut bread with sharp knife"]),
    ]
    for combined_text, singles in p2_cases:
        combined_verdict, _e = evaluate(combined_text)
        highest_single = 0
        for single in singles:
            single_verdict, _e2 = evaluate(single)
            if severity[single_verdict] > highest_single:
                highest_single = severity[single_verdict]
        check("P2 " + repr(combined_text) + " severity >= most severe clause",
              severity[combined_verdict] >= highest_single)

    # split_clauses: terminators split, "and" does not.
    check("split_clauses splits on . ! ? ;",
          split_clauses("a. b! c? d; e") == ["a", "b", "c", "d", "e"])
    check("split_clauses does not split on and",
          split_clauses("make bread and make bomb") == ["make bread and make bomb"])
    check("split_clauses drops empty fragments",
          split_clauses("make bread.. make bomb") == ["make bread", "make bomb"])

    # Rulings A, C, E (coupled-edge benchmark, 2026-10-15). The 8 ruled
    # verdicts. Three changed from prior behavior (cited per case);
    # the other five are unchanged and re-asserted here.
    ruling_cases = [
        ("the sharp knife cut the bread", "APPROVE"),  # Ruling A (was REDACT)
        ("the charred fish was stabbed", "APPROVE"),   # Ruling E (was HOLD)
        ("make bomb with shrapnel", "REFUSE"),         # Ruling C (was HOLD)
        ("cut bread with sharp knife", "APPROVE"),      # Ruling C instrument + Ruling A cooking (was HOLD)
        ("the sharp knife cut the person", "HOLD"),    # unchanged (cut->person unmapped)
        ("make shrapnel bomb", "REFUSE"),              # unchanged
        ("stab him", "REFUSE"),                        # unchanged
        ("the dead bloodied person was charred", "REFUSE"),  # unchanged, Ruling B
    ]
    for text, expected in ruling_cases:
        verdict, _e = evaluate(text)
        check("ruling " + repr(text) + " == " + expected, verdict == expected)

    # Topic determination (Ruling A): topic from entries present.
    check("topic cooking for bread input",
          parse_input("the sharp knife cut the bread").topic == "cooking")
    check("topic violence for person input",
          parse_input("the sharp knife cut the person").topic == "violence")
    check("topic violence for bomb input",
          parse_input("make bomb with shrapnel").topic == "violence")
    check("topic cooking for fish passive input",
          parse_input("the charred fish was stabbed").topic == "cooking")
    check("topic None for neither input",
          parse_input("stab in the dark").topic is None)

    # With-instrument vs with-identifier distinction (Ruling C).
    p = parse_input("cut bread with sharp knife")
    check("with-instrument: knife is instrument",
          p.with_phrase == ("instrument", "knife"))
    check("with-instrument: no qualifier added", p.qualifiers == ())
    p = parse_input("make bomb with shrapnel")
    check("with-identifier: shrapnel is identifier",
          p.with_phrase == ("identifier", "shrapnel"))
    check("with-identifier: qualifier attached", p.qualifiers == ("shrapnel",))
    verdict, _e = evaluate("make bomb with potato")
    check("with-neither: 'make bomb with potato' == HOLD", verdict == "HOLD")
    verdict, _e = evaluate("cut bread with")
    check("with-no-X: 'cut bread with' == HOLD", verdict == "HOLD")

    # Passive mapping (Ruling E) + agent rule (Ruling D).
    p = parse_input("the charred fish was stabbed")
    check("passive: fish stabbed is passive", p.passive is True)
    check("passive: fish stabbed verb normalized to stab", p.verb == "stab")
    check("passive: fish stabbed obj is fish", p.obj == "fish")
    check("passive: fish stabbed agent is None", p.agent is None)
    p = parse_input("the person was stabbed")
    check("passive: person stabbed is passive", p.passive is True)
    check("passive: person stabbed agent is None", p.agent is None)
    verdict, _e = evaluate("the person was stabbed")
    check("passive: person stabbed, no agent == REFUSE (Ruling D as amended 2026-11-05: completed parse describes the harm)", verdict == "REFUSE")
    verdict, _e = evaluate("the person was stabbed", audience="child")
    check("passive: person stabbed, no agent, child audience == HOLD (Ruling D as amended 2026-11-05)", verdict == "HOLD")
    p = parse_input("the person was stabbed by him")
    check("passive: person stabbed by him agent is him", p.agent == "him")
    verdict, _e = evaluate("the person was stabbed by him")
    check("passive: person stabbed by him == REFUSE", verdict == "REFUSE")
    verdict, _e = evaluate("the fish was cut")
    check("passive: fish cut == APPROVE (Ruling E)", verdict == "APPROVE")
    verdict, _e = evaluate("the bread was stabbed")
    check("passive: bread stabbed == APPROVE (Ruling E)", verdict == "APPROVE")
    verdict, _e = evaluate("the person was cut by him")
    check("passive: person cut by him == HOLD (cut->person unmapped)", verdict == "HOLD")

    # unknown_words (HOLD-populates-words rule, 2026-10-15).
    check("unknown_words: unknown words listed, sorted, deduped",
          unknown_words("florp the blorp florp") == ["blorp", "florp"])
    check("unknown_words: known words excluded",
          unknown_words("how make bread") == [])
    check("unknown_words: function words excluded",
          unknown_words("an is were do not") == [])
    check("unknown_words: numbers excluded",
          unknown_words("florp 123 456") == ["florp"])
    check("unknown_words: empty input -> []",
          unknown_words("") == [])
    check("unknown_words: punctuation-only -> []",
          unknown_words("... !!!") == [])

    print(str(passed) + "/" + str(total) + " tests passed")
    if failures:
        print("FAILED:")
        for f in failures:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
